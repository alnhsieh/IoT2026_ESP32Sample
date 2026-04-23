"""
ESP32 MicroPython - Temperature & Humidity Monitoring System
Features:
  - Connect to WiFi
  - Start built-in Web Server (serving dashboard index.html)
  - Read DHT11 temperature & humidity sensor
  - Support user-configurable device name and thresholds
  - Call FastAPI /broadcast to send LINE broadcast alerts when thresholds exceeded
  - Persistent config & state (config.json)

Hardware Wiring:
  DHT11 DATA pin -> GPIO 4 (configurable in settings)
  DHT11 VCC      -> 3.3V
  DHT11 GND      -> GND

Dependencies (MicroPython built-in or requires upload):
  - dht        (MicroPython built-in)
  - network    (MicroPython built-in)
  - ujson      (MicroPython built-in)
  - usocket    (MicroPython built-in)
  - urequests  (requires uploading urequests.py)
"""

import network
import usocket as socket
import ujson as json
import utime as time
import machine
import dht
import gc
import ntptime

try:
    import urequests as requests
except ImportError:
    print("WARNING: urequests module missing, please upload urequests.py to ESP32")
    requests = None

# -----------------------------------------
#  Default Configuration (used on first boot)
# -----------------------------------------
DEFAULT_CONFIG = {
    "device_name": "ESP32-Sensor",
    "wifi_ssid": "fish",
    "wifi_password": "00000000",
    "fastapi_url": "http://192.168.1.100:8000",  # FastAPI server address
    "dht_pin": 4,                                  # DHT11 pin
    "temp_high": 35.0,                             # Temperature upper limit (C)
    "temp_low": 5.0,                               # Temperature lower limit (C)
    "humi_high": 85.0,                             # Humidity upper limit (%)
    "humi_low": 10.0,                              # Humidity lower limit (%)
    "read_interval": 3,                            # Read interval (seconds)
    "alert_cooldown": 10                           # Alert cooldown (seconds), prevents repeated alerts
}

CONFIG_FILE = "config.json"

# -----------------------------------------
#  Configuration Management
# -----------------------------------------

def sync_time(retry=3):
    for i in range(retry):
        try:
            ntptime.settime()
            print("Time sync successful (UTC)")
            return True
        except Exception as e:
            print(f"Time sync failed ({i+1}/{retry}):", e)
            time.sleep(1)
    return False

def load_config():
    """Load config from file, use defaults if not found"""
    try:
        with open(CONFIG_FILE, "r") as f:
            config = json.load(f)
        # Fill in any missing keys for backward compatibility
        for k, v in DEFAULT_CONFIG.items():
            if k not in config:
                config[k] = v
        return config
    except Exception:
        print("Config file not found, using defaults")
        return dict(DEFAULT_CONFIG)


def save_config(config):
    """Save config to flash"""
    try:
        with open(CONFIG_FILE, "w") as f:
            json.dump(config, f)
        print("Config saved successfully")
        return True
    except Exception as e:
        print(f"Failed to save config: {e}")
        return False


# -----------------------------------------
#  WiFi Connection
# -----------------------------------------
def connect_wifi(ssid, password, timeout=20):
    """Connect to WiFi, return IP address or None"""
    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)

    if wlan.isconnected():
        print(f"Already connected to WiFi, IP: {wlan.ifconfig()[0]}")
        sync_time()
        return wlan.ifconfig()[0]

    print(f"Connecting to WiFi: {ssid}")
    wlan.connect(ssid, password)

    deadline = time.time() + timeout
    while not wlan.isconnected():
        if time.time() > deadline:
            print("WiFi connection timed out")
            return None
        print(".", end="")
        time.sleep(1)

    ip = wlan.ifconfig()[0]
    print(f"\nWiFi connected, IP: {ip}")
    sync_time()
    return ip


# -----------------------------------------
#  DHT Sensor
# -----------------------------------------
sensor = None

def init_sensor(pin_num):
    global sensor
    try:
        sensor = dht.DHT11(machine.Pin(pin_num))
        print(f"DHT11 initialized (GPIO {pin_num})")
    except Exception as e:
        print(f"DHT11 initialization failed: {e}")
        sensor = None


def read_sensor():
    """Read temperature and humidity, return (temperature, humidity) or (None, None)"""
    if sensor is None:
        return None, None
    try:
        sensor.measure()
        time.sleep_ms(100)
        return sensor.temperature(), sensor.humidity()
    except Exception as e:
        print(f"Sensor read failed: {e}")
        return None, None


# -----------------------------------------
#  Alert Sending
# -----------------------------------------
last_alert_time = {}  # key: alert_type, value: timestamp

def should_alert(alert_type, cooldown):
    now = time.time()
    last = last_alert_time.get(alert_type)

    if last is None:
        last_alert_time[alert_type] = now
        return True

    if now - last >= cooldown:
        last_alert_time[alert_type] = now
        return True

    return False


def get_timestamp():
    """Return timestamp string (falls back to uptime seconds if RTC not synced)"""
    try:
        t = machine.RTC().datetime()
        return f"{t[0]}-{t[1]:02d}-{t[2]:02d} {t[4]:02d}:{t[5]:02d}:{t[6]:02d}"
    except Exception:
        secs = time.time()
        return f"Uptime {secs}s"


def send_line_broadcast(fastapi_url, message):
    """Call FastAPI /broadcast to send LINE broadcast"""
    if requests is None:
        print("ERROR: urequests not available, cannot send alert")
        return False
    try:
        url = f"{fastapi_url.rstrip('/')}/broadcast"
        payload = json.dumps({"message": message})
        headers = {"Content-Type": "application/json"}
        resp = requests.post(url, data=payload, headers=headers, timeout=10)
        if resp.status_code == 200:
            print(f"LINE broadcast sent: {message}")
            resp.close()
            return True
        else:
            print(f"LINE broadcast failed HTTP {resp.status_code}: {resp.text}")
            resp.close()
            return False
    except Exception as e:
        print(f"Error sending alert: {e}")
        return False


def check_and_alert(config, temp, humi):
    """Check thresholds and send alerts if needed"""
    now_str = get_timestamp()
    name = config["device_name"]
    cooldown = config.get("alert_cooldown", 300)
    url = config["fastapi_url"]
    alerts = []

    if temp is not None:
        if temp >= config["temp_high"]:
            alert_type = "temp_high"
            if should_alert(alert_type, cooldown):
                alerts.append(
                    f"[HIGH TEMP ALERT]\n"
                    f"Device: {name}\n"
                    f"Threshold: >= {config['temp_high']}C\n"
                    f"Current Temp: {temp:.1f}C\n"
                    f"Time: {now_str}"
                )
        elif temp <= config["temp_low"]:
            alert_type = "temp_low"
            if should_alert(alert_type, cooldown):
                alerts.append(
                    f"[LOW TEMP ALERT]\n"
                    f"Device: {name}\n"
                    f"Threshold: <= {config['temp_low']}C\n"
                    f"Current Temp: {temp:.1f}C\n"
                    f"Time: {now_str}"
                )

    if humi is not None:
        if humi >= config["humi_high"]:
            alert_type = "humi_high"
            if should_alert(alert_type, cooldown):
                alerts.append(
                    f"[HIGH HUMIDITY ALERT]\n"
                    f"Device: {name}\n"
                    f"Threshold: >= {config['humi_high']}%\n"
                    f"Current Humidity: {humi:.1f}%\n"
                    f"Time: {now_str}"
                )
                print(f"[HIGH HUMIDITY ALERT]\n"
                    f"Device: {name}\n"
                    f"Threshold: >= {config['humi_high']}%\n"
                    f"Current Humidity: {humi:.1f}%\n"
                    f"Time: {now_str}")
        elif humi <= config["humi_low"]:
            alert_type = "humi_low"
            if should_alert(alert_type, cooldown):
                alerts.append(
                    f"[LOW HUMIDITY ALERT]\n"
                    f"Device: {name}\n"
                    f"Threshold: <= {config['humi_low']}%\n"
                    f"Current Humidity: {humi:.1f}%\n"
                    f"Time: {now_str}"
                )
    print(len(alerts))
    for msg in alerts:
        print(msg)
        send_line_broadcast(url, msg)

    return len(alerts) > 0


# -----------------------------------------
#  Web Server (serving dashboard and REST API)
# -----------------------------------------
current_data = {
    "temp": None,
    "humi": None,
    "alert": False,
    "alert_messages": [],
    "last_update": "Not yet read"
}


def load_html():
    """Read index.html from flash"""
    try:
        with open("index.html", "r") as f:
            return f.read()
    except Exception:
        return "<h1>index.html not found, please upload to ESP32</h1>"


def handle_request(conn, config):
    """Handle HTTP request"""
    global current_data

    try:
        request_raw = conn.recv(4096).decode("utf-8", "ignore")
        if not request_raw:
            conn.close()
            return config

        lines = request_raw.split("\r\n")
        request_line = lines[0] if lines else ""
        parts = request_line.split(" ")
        method = parts[0] if len(parts) > 0 else "GET"
        path = parts[1] if len(parts) > 1 else "/"

        # Parse path and query string
        if "?" in path:
            path, _ = path.split("?", 1)

        # Get POST body
        body = ""
        if method == "POST":
            if "\r\n\r\n" in request_raw:
                body = request_raw.split("\r\n\r\n", 1)[1]

        # -- Routes --

        # GET / -> return index.html
        if method == "GET" and path == "/":
            html = load_html()
            response = (
                "HTTP/1.1 200 OK\r\n"
                "Content-Type: text/html; charset=utf-8\r\n"
                f"Content-Length: {len(html.encode('utf-8'))}\r\n"
                "Connection: close\r\n\r\n"
            ) + html

        # GET /api/status -> return current sensor data + config
        elif method == "GET" and path == "/api/status":
            payload = json.dumps({
                "device_name": config["device_name"],
                "temp": current_data["temp"],
                "humi": current_data["humi"],
                "alert": current_data["alert"],
                "alert_messages": current_data["alert_messages"],
                "last_update": current_data["last_update"],
                "config": {
                    "temp_high": config["temp_high"],
                    "temp_low": config["temp_low"],
                    "humi_high": config["humi_high"],
                    "humi_low": config["humi_low"],
                    "read_interval": config["read_interval"],
                    "alert_cooldown": config["alert_cooldown"],
                    "fastapi_url": config["fastapi_url"],
                    "dht_pin": config["dht_pin"]
                }
            })
            response = (
                "HTTP/1.1 200 OK\r\n"
                "Content-Type: application/json\r\n"
                "Access-Control-Allow-Origin: *\r\n"
                f"Content-Length: {len(payload.encode('utf-8'))}\r\n"
                "Connection: close\r\n\r\n"
            ) + payload

        # POST /api/config -> update config
        elif method == "POST" and path == "/api/config":
            try:
                new_cfg = json.loads(body)
                updatable = [
                    "device_name", "temp_high", "temp_low",
                    "humi_high", "humi_low", "read_interval",
                    "alert_cooldown", "fastapi_url",
                    "wifi_ssid", "wifi_password", "dht_pin"
                ]
                for key in updatable:
                    if key in new_cfg:
                        if key in ("temp_high", "temp_low", "humi_high", "humi_low"):
                            config[key] = float(new_cfg[key])
                        elif key in ("read_interval", "alert_cooldown", "dht_pin"):
                            config[key] = int(new_cfg[key])
                        else:
                            config[key] = str(new_cfg[key])

                save_config(config)

                # Re-initialize sensor if DHT pin changed
                if "dht_pin" in new_cfg:
                    init_sensor(config["dht_pin"])

                result = json.dumps({"status": "ok", "message": "Config updated"})
                response = (
                    "HTTP/1.1 200 OK\r\n"
                    "Content-Type: application/json\r\n"
                    "Access-Control-Allow-Origin: *\r\n"
                    f"Content-Length: {len(result.encode('utf-8'))}\r\n"
                    "Connection: close\r\n\r\n"
                ) + result

            except Exception as e:
                err = json.dumps({"status": "error", "message": str(e)})
                response = (
                    "HTTP/1.1 400 Bad Request\r\n"
                    "Content-Type: application/json\r\n"
                    f"Content-Length: {len(err.encode('utf-8'))}\r\n"
                    "Connection: close\r\n\r\n"
                ) + err

        # POST /api/test_alert -> manually trigger test alert
        elif method == "POST" and path == "/api/test_alert":
            now_str = get_timestamp()
            msg = (
                f"[TEST ALERT]\n"
                f"Device: {config['device_name']}\n"
                f"This is a test broadcast\n"
                f"Time: {now_str}"
            )
            success = send_line_broadcast(config["fastapi_url"], msg)
            result = json.dumps({
                "status": "ok" if success else "error",
                "message": "Test broadcast sent" if success else "Send failed, please check FastAPI server"
            })
            response = (
                "HTTP/1.1 200 OK\r\n"
                "Content-Type: application/json\r\n"
                "Access-Control-Allow-Origin: *\r\n"
                f"Content-Length: {len(result.encode('utf-8'))}\r\n"
                "Connection: close\r\n\r\n"
            ) + result

        # OPTIONS (CORS preflight)
        elif method == "OPTIONS":
            response = (
                "HTTP/1.1 204 No Content\r\n"
                "Access-Control-Allow-Origin: *\r\n"
                "Access-Control-Allow-Methods: GET, POST, OPTIONS\r\n"
                "Access-Control-Allow-Headers: Content-Type\r\n"
                "Connection: close\r\n\r\n"
            )

        # 404
        else:
            body_404 = '{"status":"not found"}'
            response = (
                "HTTP/1.1 404 Not Found\r\n"
                "Content-Type: application/json\r\n"
                f"Content-Length: {len(body_404)}\r\n"
                "Connection: close\r\n\r\n"
            ) + body_404

        conn.sendall(response.encode("utf-8"))

    except Exception as e:
        print(f"Request handling error: {e}")
    finally:
        conn.close()

    return config


# -----------------------------------------
#  Main Program
# -----------------------------------------
def main():
    global current_data, sensor

    print("\n" + "=" * 40)
    print("  ESP32 Temperature & Humidity Monitor Starting...")
    print("=" * 40)

    # Load config
    config = load_config()
    print(f"Device name: {config['device_name']}")

    # Initialize sensor
    init_sensor(config["dht_pin"])

    # Connect WiFi
    ip = connect_wifi(config["wifi_ssid"], config["wifi_password"])
    if ip is None:
        print("ERROR: Cannot connect to WiFi, system halted")
        return

    # Start Web Server
    addr = socket.getaddrinfo("0.0.0.0", 80)[0][-1]
    srv = socket.socket()
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(addr)
    srv.listen(3)
    srv.settimeout(1)  # Non-blocking wait 1 second
    print(f"Web dashboard: http://{ip}/")
    print("=" * 40)

    last_read = 0

    while True:
        # -- Non-blocking HTTP connection accept --
        try:
            conn, addr_client = srv.accept()
            conn.settimeout(5)
            config = handle_request(conn, config)
        except OSError:
            pass  # timeout, no connection, continue

        # -- Periodic sensor read --
        now = time.time()
        if now - last_read >= config["read_interval"]:
            last_read = now
            temp, humi = read_sensor()

            if temp is not None and humi is not None:
                alerted = check_and_alert(config, temp, humi)
                print(alerted)
                now_str = get_timestamp()
                current_data["temp"] = round(temp, 1)
                current_data["humi"] = round(humi, 1)
                current_data["last_update"] = now_str

                if alerted:
                    current_data["alert"] = True
                    msg = f"T:{temp:.1f}C H:{humi:.1f}% @ {now_str}"
                    current_data["alert_messages"].append(msg)
                    if len(current_data["alert_messages"]) > 10:
                        current_data["alert_messages"] = current_data["alert_messages"][-10:]
                else:
                    # Clear alert state if values return to normal range
                    t_ok = config["temp_low"] < temp < config["temp_high"]
                    h_ok = config["humi_low"] < humi < config["humi_high"]
                    if t_ok and h_ok:
                        current_data["alert"] = False

                print(f"[DATA] {config['device_name']} | Temp:{temp:.1f}C Humi:{humi:.1f}% | Alert:{'YES' if current_data['alert'] else 'NO'}")
            else:
                print("WARNING: Sensor read failed, skipping this cycle")

        gc.collect()


if __name__ == "__main__":
    main()