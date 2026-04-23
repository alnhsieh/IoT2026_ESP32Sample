import network
import asyncio
import json
import ubinascii
import hashlib
from machine import Pin

# ===== 設定你的 WiFi =====
SSID = "AECA-U"
PASSWORD = "UC25971684"

# ===== LED 腳位設定 =====
leds = {
    "red":    Pin(23, Pin.OUT),
    "green":  Pin(22, Pin.OUT),
    "yellow": Pin(21, Pin.OUT),
}

led_state = {"red": False, "green": False, "yellow": False}
clients = set()

# ===== 連線 WiFi =====
def connect_wifi():
    import time
    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)
    wlan.connect(SSID, PASSWORD)
    print("連線中...", end="")
    for _ in range(20):
        if wlan.isconnected():
            break
        print(".", end="")
        time.sleep(0.5)
    if not wlan.isconnected():
        print("\n❌ WiFi 連線失敗！")
        return None
    ip = wlan.ifconfig()[0]
    print("\n✅ WiFi 連線成功！IP:", ip)
    return ip

# ===== 套用 LED 狀態 =====
def apply_led_state():
    for color, pin in leds.items():
        pin.value(1 if led_state[color] else 0)

# ===== 製作 WebSocket frame =====
def make_ws_frame(data):
    if isinstance(data, str):
        data = data.encode()
    length = len(data)
    if length < 126:
        header = bytes([0x81, length])
    else:
        header = bytes([0x81, 126, (length >> 8) & 0xFF, length & 0xFF])
    return header + data

# ===== 解析 WebSocket frame =====
def parse_ws_frame(data):
    if len(data) < 6:
        return None
    b2 = data[1]
    masked = (b2 & 0x80) != 0
    payload_len = b2 & 0x7F
    offset = 2
    if payload_len == 126:
        if len(data) < 8:
            return None
        payload_len = (data[2] << 8) | data[3]
        offset = 4
    if masked:
        if len(data) < offset + 4:
            return None
        mask = data[offset:offset + 4]
        offset += 4
    payload = data[offset:offset + payload_len]
    if masked:
        payload = bytes([payload[i] ^ mask[i % 4] for i in range(len(payload))])
    try:
        return payload.decode()
    except:
        return None

# ===== 安全 decode（不用 keyword argument）=====
def safe_decode(b):
    try:
        return b.decode()
    except:
        # 逐 byte 轉，忽略無法解碼的
        result = ""
        for byte in b:
            if byte < 128:
                result += chr(byte)
        return result

# ===== WebSocket 握手 =====
def build_accept_key(key):
    magic = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
    raw = hashlib.sha1((key + magic).encode()).digest()
    # b2a_base64 回傳含換行，需去除
    b64 = ubinascii.b2a_base64(raw)
    if isinstance(b64, (bytes, bytearray)):
        b64 = b64.decode()
    return b64.strip()

# ===== 廣播狀態給所有 clients =====
async def broadcast_state():
    msg = json.dumps({"type": "state", "leds": led_state})
    frame = make_ws_frame(msg)
    dead = set()
    for writer in clients:
        try:
            writer.write(frame)
            await writer.drain()
        except:
            dead.add(writer)
    clients.difference_update(dead)

# ===== 讀取完整 HTTP 請求頭 =====
async def read_http_headers(reader):
    buf = b""
    while b"\r\n\r\n" not in buf:
        chunk = await reader.read(256)
        if not chunk:
            break
        buf += chunk
    return buf

# ===== 主要連線處理器 =====
async def handle_client(reader, writer):
    try:
        request = await read_http_headers(reader)
        request_str = safe_decode(request)

        is_ws = "Upgrade: websocket" in request_str or "upgrade: websocket" in request_str

        if is_ws:
            # 取得握手 key
            key = ""
            for line in request_str.split("\r\n"):
                if "Sec-WebSocket-Key" in line:
                    parts = line.split(": ")
                    if len(parts) >= 2:
                        key = parts[1].strip()
                    break

            if not key:
                writer.close()
                return

            accept = build_accept_key(key)
            response = (
                "HTTP/1.1 101 Switching Protocols\r\n"
                "Upgrade: websocket\r\n"
                "Connection: Upgrade\r\n"
                "Sec-WebSocket-Accept: " + accept + "\r\n\r\n"
            )
            writer.write(response.encode())
            await writer.drain()

            clients.add(writer)
            print("✅ WebSocket client 連線，目前:", len(clients), "個")

            # 傳送目前狀態
            frame = make_ws_frame(json.dumps({"type": "state", "leds": led_state}))
            writer.write(frame)
            await writer.drain()

            # 持續接收訊息
            try:
                while True:
                    data = await reader.read(256)
                    if not data:
                        break
                    msg = parse_ws_frame(data)
                    if msg:
                        try:
                            cmd = json.loads(msg)
                            t = cmd.get("type", "")
                            if t == "toggle":
                                color = cmd.get("color", "")
                                if color in led_state:
                                    led_state[color] = not led_state[color]
                                    apply_led_state()
                                    await broadcast_state()
                                    print("toggle:", color, "->", led_state[color])
                            elif t == "set":
                                color = cmd.get("color", "")
                                val = cmd.get("value", False)
                                if color in led_state:
                                    led_state[color] = bool(val)
                                    apply_led_state()
                                    await broadcast_state()
                            elif t == "set_all":
                                # 一次設定所有燈，交通燈模式用
                                vals = cmd.get("leds", {})
                                for c, v in vals.items():
                                    if c in led_state:
                                        led_state[c] = bool(v)
                                apply_led_state()
                                await broadcast_state()
                                print("set_all:", led_state)
                            elif t == "all_off":
                                for c in led_state:
                                    led_state[c] = False
                                apply_led_state()
                                await broadcast_state()
                                print("all off")
                        except Exception as e:
                            print("指令解析錯誤:", e)
            except Exception as e:
                print("WS 接收錯誤:", e)
            finally:
                clients.discard(writer)
                print("❌ WebSocket client 離線，目前:", len(clients), "個")

        else:
            # 一般 HTTP 回傳 HTML
            try:
                with open("index.html", "r") as f:
                    html = f.read()
                html_bytes = html.encode()
                header = (
                    "HTTP/1.1 200 OK\r\n"
                    "Content-Type: text/html; charset=utf-8\r\n"
                    "Content-Length: " + str(len(html_bytes)) + "\r\n"
                    "Connection: close\r\n\r\n"
                )
                writer.write(header.encode())
                writer.write(html_bytes)
                await writer.drain()
            except Exception as e:
                print("HTML 讀取錯誤:", e)
                err = b"HTTP/1.1 500 Internal Server Error\r\n\r\nError"
                writer.write(err)
                await writer.drain()
            finally:
                writer.close()

    except Exception as e:
        print("handle_client 錯誤:", e)
        try:
            writer.close()
        except:
            pass

# ===== 主程式 =====
async def main():
    ip = connect_wifi()
    if not ip:
        return
    print("🌐 請在瀏覽器開啟 http://" + ip)
    # MicroPython asyncio.start_server 用 positional arguments
    server = await asyncio.start_server(handle_client, "0.0.0.0", 80)
    print("✅ 伺服器啟動於 port 80")
    await server.wait_closed()

asyncio.run(main())