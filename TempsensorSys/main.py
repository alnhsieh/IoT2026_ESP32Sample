import network
import uasyncio as asyncio
import json
import time
import gc
import os
import wifi_manager as wm

try:
    from machine import Pin, PWM
    import dht
    SENSOR_ENABLED = True
except Exception as e:
    print("Sensor module import failed:", e)
    SENSOR_ENABLED = False

# ====== Global State ======
_mode   = "ap"
_sta_ip = None

config_data = {
    "temp_max": 30,
    "humi_max": 70,
    "device_name": "ESP32-01"
}

sensor_data = {
    "temp": 0,
    "humi": 0,
    "temp_alarm": False,
    "humi_alarm": False,
    "uptime": 0,
    "requests": 0,
    "errors": 0
}

start_time = time.time()

# ====== Hardware Init ======
sensor     = None
yellow_led = None
green_led  = None
red_led    = None

if SENSOR_ENABLED:
    try:
        yellow_led = PWM(Pin(23), freq=1000, duty=0)
        green_led  = PWM(Pin(22), freq=1000, duty=0)
        red_led    = PWM(Pin(21), freq=1000, duty=0)
        print("LED init OK")
    except Exception as e:
        print("LED init warning:", e)

    try:
        # 只建立物件，不在 init 讀取（DHT11 需要上電後等待才能讀取）
        sensor = dht.DHT11(Pin(15))
        print("DHT11 object created, will read in sensor_loop")
    except Exception as e:
        print("DHT11 object FAILED:", e)
        sensor = None
        SENSOR_ENABLED = False

def update_leds():
    if not SENSOR_ENABLED or yellow_led is None:
        return
    try:
        yellow_led.duty(800 if sensor_data["humi_alarm"] else 0)
        if sensor_data["temp_alarm"]:
            red_led.duty(800); green_led.duty(0)
        else:
            red_led.duty(0);   green_led.duty(800)
    except:
        pass

# ====== Helpers ======
def read_file(path):
    try:
        with open(path, "r") as f:
            return f.read()
    except:
        return ""

def url_decode(s):
    s = s.replace("+", " ")
    result = ""
    i = 0
    while i < len(s):
        if s[i] == "%" and i + 2 < len(s):
            try:
                result += chr(int(s[i+1:i+3], 16))
                i += 3
                continue
            except:
                pass
        result += s[i]
        i += 1
    return result

def parse_form(body):
    params = {}
    for pair in body.split("&"):
        if "=" in pair:
            k, v = pair.split("=", 1)
            params[url_decode(k)] = url_decode(v)
    return params

# ====== HTTP Helpers ======
async def send_html(writer, html):
    body = html.encode("utf-8") if isinstance(html, str) else html
    hdr = (
        "HTTP/1.1 200 OK\r\n"
        "Content-Type: text/html; charset=utf-8\r\n"
        "Access-Control-Allow-Origin: *\r\n"
        "Content-Length: {}\r\n"
        "Connection: close\r\n\r\n"
    ).format(len(body))
    writer.write(hdr.encode())
    writer.write(body)
    await writer.drain()

async def send_json(writer, data_dict, status=200):
    body = json.dumps(data_dict).encode("utf-8")
    status_text = "200 OK" if status == 200 else "{} Error".format(status)
    hdr = (
        "HTTP/1.1 {}\r\n"
        "Content-Type: application/json\r\n"
        "Access-Control-Allow-Origin: *\r\n"
        "Content-Length: {}\r\n"
        "Connection: close\r\n\r\n"
    ).format(status_text, len(body))
    writer.write(hdr.encode())
    writer.write(body)
    await writer.drain()

async def send_cors(writer):
    hdr = (
        "HTTP/1.1 200 OK\r\n"
        "Access-Control-Allow-Origin: *\r\n"
        "Access-Control-Allow-Methods: GET, POST, OPTIONS\r\n"
        "Access-Control-Allow-Headers: Content-Type\r\n"
        "Content-Length: 0\r\n"
        "Connection: close\r\n\r\n"
    )
    writer.write(hdr.encode())
    await writer.drain()

# ====== Request Handler ======
async def handle_client(reader, writer):
    global _mode, _sta_ip
    sensor_data["requests"] += 1
    addr = writer.get_extra_info("peername")

    try:
        request_line = await asyncio.wait_for(reader.readline(), timeout=5.0)
        request = request_line.decode("utf-8", "ignore").strip()
        parts  = request.split()
        method = parts[0] if parts else "GET"
        path   = parts[1].split("?")[0] if len(parts) > 1 else "/"

        headers = {}
        while True:
            line = await asyncio.wait_for(reader.readline(), timeout=2.0)
            if line in (b"\r\n", b""):
                break
            if b":" in line:
                k, v = line.decode("utf-8", "ignore").strip().split(":", 1)
                headers[k.strip().lower()] = v.strip()

        print("[{}] {} {}".format(addr, method, path))

        if method == "OPTIONS":
            await send_cors(writer)
            return

        # ── WiFi 管理 ──

        if method == "GET" and path == "/":
            if _mode == "ap":
                await send_html(writer, read_file("index.html"))
            else:
                await send_html(writer, read_file("home.html"))

        elif method == "GET" and path == "/scan":
            nets = wm.scan_wifi()
            await send_json(writer, nets)

        elif method == "POST" and path == "/connect":
            cl   = int(headers.get("content-length", "0"))
            body = (await reader.read(cl)).decode("utf-8", "ignore") if cl > 0 else ""
            params   = parse_form(body)
            ssid     = params.get("ssid", "").strip()
            password = params.get("password", "").strip()
            if not ssid:
                await send_json(writer, {"status": "fail", "message": "Missing SSID"}, 400)
                return
            ok, ifcfg = wm.connect_wifi(ssid, password)
            if ok:
                _sta_ip = ifcfg[0]
                _mode   = "sta"
                wm.save_config(ssid, password)
                print("Connected! STA IP:", _sta_ip)
                await send_json(writer, {"status": "ok", "ssid": ssid, "new_ip": _sta_ip})
            else:
                await send_json(writer, {"status": "fail", "ssid": ssid})

        elif method == "GET" and path == "/status":
            info = wm.get_current_connection()
            cfg  = wm.load_config()
            if info["connected"]:
                info["ssid"] = cfg.get("ssid", "Unknown")
            await send_json(writer, info)

        elif method == "POST" and path == "/forget":
            wm.delete_config()
            await send_json(writer, {"status": "ok"})
            writer.close()
            await asyncio.sleep(1)
            import machine
            machine.reset()
            return

        # ── 溫控面板 ──

        elif method == "GET" and path == "/sensor":
            await send_html(writer, read_file("sensor.html"))

        elif method == "GET" and path == "/api/data":
            sensor_data["uptime"] = time.time() - start_time
            cfg = wm.load_config()
            out = dict(sensor_data)
            out["ip"]             = _sta_ip or "unknown"
            out["device_name"]    = config_data["device_name"]
            out["ssid"]           = cfg.get("ssid", "Unknown")
            out["free_memory"]    = gc.mem_free()
            out["temp_max"]       = config_data["temp_max"]
            out["humi_max"]       = config_data["humi_max"]
            await send_json(writer, out)

        elif method == "GET" and path == "/api/config":
            await send_json(writer, config_data)

        elif method == "POST" and path == "/api/config":
            cl   = int(headers.get("content-length", "0"))
            body = (await reader.read(cl)).decode("utf-8", "ignore") if cl > 0 else "{}"
            try:
                new_cfg = json.loads(body)
                config_data.update(new_cfg)
                sensor_data["temp_alarm"] = sensor_data["temp"] > config_data["temp_max"]
                sensor_data["humi_alarm"] = sensor_data["humi"] > config_data["humi_max"]
                update_leds()
                await send_json(writer, {"status": "success", "config": config_data})
            except Exception as e:
                await send_json(writer, {"error": str(e)}, 400)

        elif method == "GET" and path == "/api/info":
            await send_json(writer, {
                "device_name":    config_data["device_name"],
                "ip":             _sta_ip or "unknown",
                "uptime":         time.time() - start_time,
                "free_memory":    gc.mem_free(),
                "total_requests": sensor_data["requests"],
                "errors":         sensor_data["errors"],
                "sensor_enabled": SENSOR_ENABLED
            })

        elif method == "POST" and path == "/api/restart":
            await send_json(writer, {"status": "restarting"})
            writer.close()
            await asyncio.sleep(2)
            import machine
            machine.reset()
            return

        else:
            await send_json(writer, {"error": "Not Found"}, 404)

    except Exception as e:
        print("Handler error:", e)
        sensor_data["errors"] += 1
    finally:
        try:
            writer.close()
            await writer.wait_closed()
        except:
            pass

# ====== Sensor Loop ======
async def sensor_loop():
    global sensor, SENSOR_ENABLED
    # DHT11 上電後需要足夠暖機時間才能穩定讀取
    print("[Sensor] Waiting 5s for DHT11 warm-up...")
    await asyncio.sleep(5)

    # 若 init 階段因 measure() 失敗而設 SENSOR_ENABLED=False，
    # 但 sensor 物件存在，這裡重新啟用讓 loop 自己重試
    if sensor is not None:
        SENSOR_ENABLED = True
        print("[Sensor] Re-enabled, starting read loop")
    else:
        print("[Sensor] No sensor object, loop idle")

    consecutive_errors = 0

    while True:
        if sensor is not None:
            try:
                sensor.measure()
                await asyncio.sleep_ms(500)   # 等 DHT11 內部計算完成
                t = sensor.temperature()
                h = sensor.humidity()

                if t < -10 or t > 80 or h < 0 or h > 100:
                    raise ValueError("Out of range T={} H={}".format(t, h))

                sensor_data["temp"] = t
                sensor_data["humi"] = h
                sensor_data["temp_alarm"] = t > config_data["temp_max"]
                sensor_data["humi_alarm"] = h > config_data["humi_max"]
                SENSOR_ENABLED = True
                update_leds()
                consecutive_errors = 0
                print("[Sensor] T={}C H={}%".format(t, h))

            except Exception as e:
                consecutive_errors += 1
                sensor_data["errors"] += 1
                print("[Sensor Error] #{} {}".format(consecutive_errors, e))
                if consecutive_errors >= 3:
                    print("[Sensor] Backing off 8s...")
                    await asyncio.sleep(8)
                    consecutive_errors = 0
                    continue
        gc.collect()
        await asyncio.sleep(2)

# ====== Main ======
async def main():
    global _mode, _sta_ip
    print("\n=== ESP32 Starting ===")
    gc.collect()

    cfg = wm.load_config()
    if cfg.get("ssid"):
        print("Saved config found, connecting to:", cfg["ssid"])
        ok, ifcfg = wm.connect_wifi(cfg["ssid"], cfg.get("password", ""))
        if ok:
            _sta_ip = ifcfg[0]
            _mode   = "sta"
            wm.save_config(cfg["ssid"], cfg.get("password", ""))
            print("Auto-connected! STA IP:", _sta_ip)
        else:
            print("Saved config failed, starting AP mode")
            wm.delete_config()
            wm.start_ap_mode()
            _mode = "ap"
    else:
        print("No saved config, starting AP mode")
        wm.start_ap_mode()
        _mode = "ap"

    server = await asyncio.start_server(handle_client, "0.0.0.0", 80)
    print("Server running. Mode={} IP={}".format(_mode, _sta_ip or "192.168.4.1"))
    print("Routes: /  /sensor  /api/data  /api/config  /api/info  /api/restart")

    asyncio.create_task(sensor_loop())

    while True:
        await asyncio.sleep(60)
        print("[Status] uptime={}s req={} mem={}".format(
            int(time.time() - start_time), sensor_data["requests"], gc.mem_free()
        ))

try:
    gc.collect()
    asyncio.run(main())
except KeyboardInterrupt:
    print("Shutting down...")
except Exception as e:
    print("Fatal error:", e)