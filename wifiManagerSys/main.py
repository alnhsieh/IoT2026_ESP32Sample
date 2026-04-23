import socket
import json
import time
import os
import wifi_manager as wm

# Global state
_sta_ip = None   # filled after STA connects
_mode   = "ap"   # "ap" or "sta"

# ── helpers ──────────────────────────────────────────────────────────────────

def read_file(path):
    try:
        with open(path, "r") as f:
            return f.read()
    except:
        return ""

def send_response(conn, status, content_type, body):
    if isinstance(body, str):
        body = body.encode("utf-8")
    header = (
        "HTTP/1.1 {}\r\n"
        "Content-Type: {}; charset=utf-8\r\n"
        "Content-Length: {}\r\n"
        "Connection: close\r\n\r\n"
    ).format(status, content_type, len(body))
    conn.send(header.encode("utf-8"))
    conn.send(body)

def parse_form(body):
    params = {}
    for pair in body.split("&"):
        if "=" in pair:
            k, v = pair.split("=", 1)
            params[url_decode(k)] = url_decode(v)
    return params

def url_decode(s):
    s = s.replace("+", " ")
    result = ""
    i = 0
    while i < len(s):
        if s[i] == "%" and i + 2 < len(s):
            try:
                result += chr(int(s[i+1:i+3], 16))
                i += 3
            except:
                result += s[i]; i += 1
        else:
            result += s[i]; i += 1
    return result

def signal_bars(rssi):
    if rssi >= -55:   return 4
    elif rssi >= -67: return 3
    elif rssi >= -78: return 2
    else:             return 1

# ── request handler ──────────────────────────────────────────────────────────

def handle_request(conn, addr):
    global _sta_ip, _mode
    try:
        request = b""
        conn.settimeout(5)
        while True:
            try:
                chunk = conn.recv(1024)
                if not chunk:
                    break
                request += chunk
                if b"\r\n\r\n" in request:
                    header_end = request.index(b"\r\n\r\n") + 4
                    # check content-length
                    headers_raw = request[:header_end].decode("utf-8", "ignore")
                    content_length = 0
                    for line in headers_raw.split("\r\n"):
                        if line.lower().startswith("content-length:"):
                            content_length = int(line.split(":")[1].strip())
                    if len(request) >= header_end + content_length:
                        break
            except OSError:
                break

        request_str = request.decode("utf-8", "ignore")
        lines = request_str.split("\r\n")
        if not lines:
            return
        first_line = lines[0]
        method = first_line.split(" ")[0] if " " in first_line else "GET"
        path   = first_line.split(" ")[1].split("?")[0] if " " in first_line else "/"

        body = request_str.split("\r\n\r\n", 1)[1] if "\r\n\r\n" in request_str else ""

        # ── routes ──────────────────────────────────────────────────────────

        # GET /  →  AP: WiFi 選擇頁   STA: 首頁
        if method == "GET" and path == "/":
            if _mode == "ap":
                send_response(conn, "200 OK", "text/html", read_file("index.html"))
            else:
                send_response(conn, "200 OK", "text/html", read_file("home.html"))

        # GET /scan
        elif method == "GET" and path == "/scan":
            nets = wm.scan_wifi()
            send_response(conn, "200 OK", "application/json", json.dumps(nets))

        # POST /connect  →  同步連線，直接回傳 new_ip
        elif method == "POST" and path == "/connect":
            params   = parse_form(body)
            ssid     = params.get("ssid", "").strip()
            password = params.get("password", "").strip()
            if not ssid:
                send_response(conn, "400 Bad Request", "text/plain", "Missing SSID")
                return

            ok, ifcfg = wm.connect_wifi(ssid, password)
            if ok:
                _sta_ip = ifcfg[0]
                _mode   = "sta"
                wm.save_config(ssid, password)
                print("Connected! STA IP:", _sta_ip)
                payload = json.dumps({"status": "ok", "ssid": ssid, "new_ip": _sta_ip})
            else:
                payload = json.dumps({"status": "fail", "ssid": ssid})
            send_response(conn, "200 OK", "application/json", payload)

        # GET /status
        elif method == "GET" and path == "/status":
            info   = wm.get_current_connection()
            config = wm.load_config()
            if info["connected"]:
                info["ssid"] = config.get("ssid", "Unknown")
            send_response(conn, "200 OK", "application/json", json.dumps(info))

        # POST /forget  →  清除設定並重啟
        elif method == "POST" and path == "/forget":
            wm.delete_config()
            send_response(conn, "200 OK", "application/json", json.dumps({"status": "ok"}))
            conn.close()
            time.sleep(1)
            import machine
            machine.reset()
            return

        else:
            send_response(conn, "404 Not Found", "text/plain", "Not Found")

    except Exception as e:
        print("Handler error:", e)
    finally:
        try:
            conn.close()
        except:
            pass

# ── startup ───────────────────────────────────────────────────────────────────

def start_server(port=80):
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(("", port))   # bind all interfaces (AP + STA)
    s.listen(5)
    print("Server listening on port", port)
    return s

def run():
    global _sta_ip, _mode

    config = wm.load_config()

    if config.get("ssid"):
        print("Saved config found, connecting to:", config["ssid"])
        ok, ifcfg = wm.connect_wifi(config["ssid"], config.get("password", ""))
        if ok:
            _sta_ip = ifcfg[0]
            _mode   = "sta"
            # 確保 config.json 存在（防止檔案損毀或首次寫入失敗）
            wm.save_config(config["ssid"], config.get("password", ""))
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

    server = start_server()

    while True:
        try:
            conn, addr = server.accept()
            handle_request(conn, addr)
        except Exception as e:
            print("Server error:", e)
            time.sleep(0.1)

run()