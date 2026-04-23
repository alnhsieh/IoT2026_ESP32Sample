"""
ESP32 MicroPython - 溫溼度感測系統
功能：
  - 連線 WiFi
  - 啟動內建 Web Server（提供儀表板 index.html）
  - 讀取 DHT11 溫溼度感測器
  - 支援使用者設定場域名稱、溫溼度閾值
  - 超過閾值時呼叫 FastAPI /broadcast 發送 LINE 廣播警報
  - 設定與狀態持久化（config.json）

硬體接線：
  DHT11 DATA pin → GPIO 4（可在設定中修改）
  DHT11 VCC      → 3.3V
  DHT11 GND      → GND

依賴函式庫（MicroPython 內建 or 需上傳）：
  - dht        （MicroPython 內建）
  - network    （MicroPython 內建）
  - ujson      （MicroPython 內建）
  - usocket    （MicroPython 內建）
  - urequests  （需上傳 urequests.py）
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
    print("⚠️ 缺少 urequests 模組，請上傳 urequests.py 到 ESP32")
    requests = None

# ─────────────────────────────────────────
#  預設設定（首次啟動時使用）
# ─────────────────────────────────────────
DEFAULT_CONFIG = {
    "device_name": "ESP32-感測器",
    "wifi_ssid": "fish",
    "wifi_password": "00000000",
    "fastapi_url": "http://192.168.1.100:8000",  # FastAPI 伺服器位址
    "dht_pin": 4,                                  # DHT22 接腳
    "temp_high": 35.0,                             # 溫度上限 (°C)
    "temp_low": 5.0,                               # 溫度下限 (°C)
    "humi_high": 85.0,                             # 濕度上限 (%)
    "humi_low": 10.0,                              # 濕度下限 (%)
    "read_interval": 3,                           # 讀取間隔（秒）
    "alert_cooldown": 10                          # 警報冷卻時間（秒），避免重複發送
}

CONFIG_FILE = "config.json"

# ─────────────────────────────────────────
#  設定管理
# ─────────────────────────────────────────

def sync_time(retry=3):
    for i in range(retry):
        try:
            ntptime.settime()
            print("時間同步成功 (UTC)")
            return True
        except Exception as e:
            print(f"時間同步失敗 ({i+1}/{retry}):", e)
            time.sleep(1)
    return False

def load_config():
    """載入設定，若不存在則使用預設值"""
    try:
        with open(CONFIG_FILE, "r") as f:
            config = json.load(f)
        # 補上新增的預設鍵（向下相容）
        for k, v in DEFAULT_CONFIG.items():
            if k not in config:
                config[k] = v
        return config
    except Exception:
        print("📄 找不到設定檔，使用預設值")
        return dict(DEFAULT_CONFIG)


def save_config(config):
    """儲存設定到 flash"""
    try:
        with open(CONFIG_FILE, "w") as f:
            json.dump(config, f)
        print("✅ 設定已儲存")
        return True
    except Exception as e:
        print(f"❌ 儲存設定失敗: {e}")
        return False


# ─────────────────────────────────────────
#  WiFi 連線
# ─────────────────────────────────────────
def connect_wifi(ssid, password, timeout=20):
    """連線 WiFi，回傳 IP 位址或 None"""
    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)

    if wlan.isconnected():
        print(f"✅ 已連線 WiFi，IP: {wlan.ifconfig()[0]}")
        sync_time()
        return wlan.ifconfig()[0]

    print(f"🔗 正在連線 WiFi: {ssid}")
    wlan.connect(ssid, password)

    deadline = time.time() + timeout
    while not wlan.isconnected():
        if time.time() > deadline:
            print("WiFi 連線逾時")
            return None
        print(".", end="")
        time.sleep(1)

    ip = wlan.ifconfig()[0]
    print(f"\n✅ WiFi 連線成功，IP: {ip}")
    sync_time()
    return ip


# ─────────────────────────────────────────
#  DHT 感測器
# ─────────────────────────────────────────
sensor = None

def init_sensor(pin_num):
    global sensor
    try:
        sensor = dht.DHT11(machine.Pin(pin_num))
        print(f"✅ DHT11 初始化完成（GPIO {pin_num}）")
    except Exception as e:
        print(f"❌ DHT11 初始化失敗: {e}")
        sensor = None


def read_sensor():
    """讀取溫溼度，回傳 (temperature, humidity) 或 (None, None)"""
    if sensor is None:
        return None, None
    try:
        sensor.measure()
        time.sleep_ms(100)
        return sensor.temperature(), sensor.humidity()
    except Exception as e:
        print(f"⚠️ 感測器讀取失敗: {e}")
        return None, None


# ─────────────────────────────────────────
#  警報發送
# ─────────────────────────────────────────
last_alert_time = {}  # key: alert_type, value: timestamp

# def should_alert(alert_type, cooldown):
#     """判斷是否應發出警報（冷卻機制）"""
#     now = time.time()
#     last = last_alert_time.get(alert_type, 0)
#     if now - last >= cooldown:
#         last_alert_time[alert_type] = now
#         return True
#     return False

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
    """取得時間戳記字串（RTC 若未同步則顯示開機秒數）"""
    try:
        t = machine.RTC().datetime()
        return f"{t[0]}-{t[1]:02d}-{t[2]:02d} {t[4]:02d}:{t[5]:02d}:{t[6]:02d}"
    except Exception:
        secs = time.time()
        return f"開機後 {secs} 秒"


def send_line_broadcast(fastapi_url, message):
    """呼叫 FastAPI /broadcast 發送 LINE 廣播"""
    if requests is None:
        print("❌ urequests 不可用，無法發送警報")
        return False
    try:
        url = f"{fastapi_url.rstrip('/')}/broadcast"
        payload = json.dumps({"message": message})
        headers = {"Content-Type": "application/json"}
        resp = requests.post(url, data=payload, headers=headers, timeout=10)
        if resp.status_code == 200:
            print(f"📣 LINE 廣播成功: {message}")
            resp.close()
            return True
        else:
            print(f"⚠️ LINE 廣播失敗 HTTP {resp.status_code}: {resp.text}")
            resp.close()
            return False
    except Exception as e:
        print(f"❌ 發送警報時發生錯誤: {e}")
        return False


def check_and_alert(config, temp, humi):
    """檢查閾值並在需要時發送警報"""
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
                    f"🔴【高溫警報】\n"
                    f"設備：{name}\n"
                    f"閾值：≥ {config['temp_high']}°C\n"
                    f"當前溫度：{temp:.1f}°C\n"
                    f"時間：{now_str}"
                )
        elif temp <= config["temp_low"]:
            alert_type = "temp_low"
            if should_alert(alert_type, cooldown):
                alerts.append(
                    f"🔵【低溫警報】\n"
                    f"設備：{name}\n"
                    f"閾值：≤ {config['temp_low']}°C\n"
                    f"當前溫度：{temp:.1f}°C\n"
                    f"時間：{now_str}"
                )

    if humi is not None:
        if humi >= config["humi_high"]:
            alert_type = "humi_high"
            if should_alert(alert_type, cooldown):
                alerts.append(
                    f"💧【高濕度警報】\n"
                    f"設備：{name}\n"
                    f"閾值：≥ {config['humi_high']}%\n"
                    f"當前濕度：{humi:.1f}%\n"
                    f"時間：{now_str}"
                )
                print(f"💧【高濕度警報】\n"
                    f"設備：{name}\n"
                    f"閾值：≥ {config['humi_high']}%\n"
                    f"當前濕度：{humi:.1f}%\n"
                    f"時間：{now_str}")
        elif humi <= config["humi_low"]:
            alert_type = "humi_low"
            if should_alert(alert_type, cooldown):
                alerts.append(
                    f"🏜️【低濕度警報】\n"
                    f"設備：{name}\n"
                    f"閾值：≤ {config['humi_low']}%\n"
                    f"當前濕度：{humi:.1f}%\n"
                    f"時間：{now_str}"
                )
    print(len(alerts))
    for msg in alerts:
        print(msg)
        send_line_broadcast(url, msg)

    return len(alerts) > 0


# ─────────────────────────────────────────
#  Web Server（提供儀表板與 REST API）
# ─────────────────────────────────────────
current_data = {
    "temp": None,
    "humi": None,
    "alert": False,
    "alert_messages": [],
    "last_update": "尚未讀取"
}


def load_html():
    """從 flash 讀取 index.html"""
    try:
        with open("index.html", "r") as f:
            return f.read()
    except Exception:
        return "<h1>找不到 index.html，請上傳到 ESP32</h1>"


def handle_request(conn, config):
    """處理 HTTP 請求"""
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

        # 解析 path 和 query string
        if "?" in path:
            path, _ = path.split("?", 1)

        # 取得 POST body
        body = ""
        if method == "POST":
            # 找到空行後的 body
            if "\r\n\r\n" in request_raw:
                body = request_raw.split("\r\n\r\n", 1)[1]

        # ── 路由 ──────────────────────────────

        # GET / → 回傳 index.html
        if method == "GET" and path == "/":
            html = load_html()
            response = (
                "HTTP/1.1 200 OK\r\n"
                "Content-Type: text/html; charset=utf-8\r\n"
                f"Content-Length: {len(html.encode('utf-8'))}\r\n"
                "Connection: close\r\n\r\n"
            ) + html

        # GET /api/status → 回傳當前感測數據 + 設定
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

        # POST /api/config → 更新設定
        elif method == "POST" and path == "/api/config":
            try:
                new_cfg = json.loads(body)
                # 允許更新的欄位
                updatable = [
                    "device_name", "temp_high", "temp_low",
                    "humi_high", "humi_low", "read_interval",
                    "alert_cooldown", "fastapi_url",
                    "wifi_ssid", "wifi_password", "dht_pin"
                ]
                for key in updatable:
                    if key in new_cfg:
                        # 數值型轉型
                        if key in ("temp_high", "temp_low", "humi_high", "humi_low"):
                            config[key] = float(new_cfg[key])
                        elif key in ("read_interval", "alert_cooldown", "dht_pin"):
                            config[key] = int(new_cfg[key])
                        else:
                            config[key] = str(new_cfg[key])

                save_config(config)

                # 若 DHT pin 改變，重新初始化感測器
                if "dht_pin" in new_cfg:
                    init_sensor(config["dht_pin"])

                result = json.dumps({"status": "ok", "message": "設定已更新"})
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

        # POST /api/test_alert → 手動測試警報
        elif method == "POST" and path == "/api/test_alert":
            now_str = get_timestamp()
            msg = (
                f"🧪【測試警報】\n"
                f"設備：{config['device_name']}\n"
                f"這是一則測試廣播\n"
                f"時間：{now_str}"
            )
            success = send_line_broadcast(config["fastapi_url"], msg)
            result = json.dumps({
                "status": "ok" if success else "error",
                "message": "測試廣播已發送" if success else "發送失敗，請確認 FastAPI 伺服器"
            })
            response = (
                "HTTP/1.1 200 OK\r\n"
                "Content-Type: application/json\r\n"
                "Access-Control-Allow-Origin: *\r\n"
                f"Content-Length: {len(result.encode('utf-8'))}\r\n"
                "Connection: close\r\n\r\n"
            ) + result

        # OPTIONS（CORS preflight）
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
        print(f"⚠️ 請求處理錯誤: {e}")
    finally:
        conn.close()

    return config


# ─────────────────────────────────────────
#  主程式
# ─────────────────────────────────────────
def main():
    global current_data, sensor

    print("\n" + "=" * 40)
    print("  ESP32 溫溼度監控系統 啟動中...")
    print("=" * 40)

    # 載入設定
    config = load_config()
    print(f"📋 設備名稱: {config['device_name']}")

    # 初始化感測器
    init_sensor(config["dht_pin"])

    # 連線 WiFi
    ip = connect_wifi(config["wifi_ssid"], config["wifi_password"])
    if ip is None:
        print("❌ 無法連線 WiFi，系統停止")
        return

    # 啟動 Web Server
    addr = socket.getaddrinfo("0.0.0.0", 80)[0][-1]
    srv = socket.socket()
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(addr)
    srv.listen(3)
    srv.settimeout(1)  # 非阻塞等待 1 秒
    print(f"🌐 Web 儀表板：http://{ip}/")
    print("=" * 40)

    last_read = 0

    while True:
        # ── 非阻塞接受 HTTP 連線 ──────────────
        try:
            conn, addr_client = srv.accept()
            conn.settimeout(5)
            config = handle_request(conn, config)
        except OSError:
            pass  # timeout，無連線，繼續

        # ── 定時讀取感測器 ────────────────────
        now = time.time()
        if now - last_read >= config["read_interval"]:
            last_read = now
            temp, humi = read_sensor()

            if temp is not None and humi is not None:
                # 判斷是否觸發警報
                alerted = check_and_alert(config, temp, humi)
                print(alerted)
                # 更新共用狀態
                now_str = get_timestamp()
                current_data["temp"] = round(temp, 1)
                current_data["humi"] = round(humi, 1)
                current_data["last_update"] = now_str

                if alerted:
                    current_data["alert"] = True
                    msg = f"T:{temp:.1f}°C H:{humi:.1f}% @ {now_str}"
                    current_data["alert_messages"].append(msg)
                    if len(current_data["alert_messages"]) > 10:
                        current_data["alert_messages"] = current_data["alert_messages"][-10:]
                else:
                    # 若溫溼度回到正常範圍，解除警報狀態
                    t_ok = config["temp_low"] < temp < config["temp_high"]
                    h_ok = config["humi_low"] < humi < config["humi_high"]
                    if t_ok and h_ok:
                        current_data["alert"] = False

                print(f"📊 {config['device_name']} | 溫度:{temp:.1f}°C 濕度:{humi:.1f}% | 警報:{'是' if current_data['alert'] else '否'}")
            else:
                print("⚠️ 感測器讀取失敗，跳過本次")

        gc.collect()


if __name__ == "__main__":
    main()