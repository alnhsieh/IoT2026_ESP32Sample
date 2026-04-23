import network
import time
import json
import os

CONFIG_FILE = "config.json"

def load_config():
    try:
        with open(CONFIG_FILE, "r") as f:
            return json.load(f)
    except:
        return {}

def save_config(ssid, password):
    config = {"ssid": ssid, "password": password}
    with open(CONFIG_FILE, "w") as f:
        json.dump(config, f)

def delete_config():
    try:
        os.remove(CONFIG_FILE)
    except:
        pass

def scan_wifi():
    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)
    time.sleep(1)
    nets = wlan.scan()
    seen = set()
    result = []
    for net in nets:
        ssid = net[0].decode("utf-8", "ignore").strip()
        rssi = net[3]
        auth = net[4]
        if ssid and ssid not in seen:
            seen.add(ssid)
            result.append({"ssid": ssid, "rssi": rssi, "auth": auth})
    result.sort(key=lambda x: x["rssi"], reverse=True)
    return result

def connect_wifi(ssid, password, timeout=15):
    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)
    # 已連線到同一個 SSID 就直接回傳，不重複斷線
    if wlan.isconnected():
        try:
            current = wlan.config("essid")
            if current == ssid:
                print("Already connected to", ssid)
                return True, wlan.ifconfig()
        except:
            pass
        wlan.disconnect()
        time.sleep(1)
    wlan.connect(ssid, password)
    for _ in range(timeout):
        if wlan.isconnected():
            return True, wlan.ifconfig()
        time.sleep(1)
    return False, None

def get_current_connection():
    wlan = network.WLAN(network.STA_IF)
    if wlan.isconnected():
        cfg = wlan.ifconfig()
        return {
            "connected": True,
            "ip": cfg[0],
            "subnet": cfg[1],
            "gateway": cfg[2],
            "dns": cfg[3]
        }
    return {"connected": False}

def start_ap_mode():
    ap = network.WLAN(network.AP_IF)
    ap.active(True)
    ap.config(essid="ESP32-Setup", password="12345678", authmode=network.AUTH_WPA_WPA2_PSK)
    time.sleep(1)
    print("AP Mode IP:", ap.ifconfig()[0])
    return ap.ifconfig()[0]