from machine import Pin, TouchPad, RTC
import network, ntptime, time

pir = Pin(23, Pin.IN)
led = Pin(22, Pin.OUT)
touch = TouchPad(Pin(13))
rtc = RTC()

WIFI_SSID = "fish"
WIFI_PASS = "00000000"
UTC_OFFSET = 8  # Taiwan UTC+8

LED_ENABLED = True
TOUCH_THRESHOLD = 400
last_touch = False

def connect_wifi():
    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)
    wlan.connect(WIFI_SSID, WIFI_PASS)
    print("Connecting to WiFi", end="")
    for _ in range(20):
        if wlan.isconnected():
            print(f" Connected! IP: {wlan.ifconfig()[0]}")
            return True
        print(".", end="")
        time.sleep(0.5)
    print(" Failed")
    return False

def sync_ntp():
    try:
        ntptime.settime()  # Sync UTC time to RTC
        print("NTP sync successful")
    except Exception as e:
        print(f"NTP sync failed: {e}")

def now():
    # RTC stores UTC, apply timezone offset
    t = time.localtime(time.time() + UTC_OFFSET * 3600)
    return f"{t[0]:04d}-{t[1]:02d}-{t[2]:02d} {t[3]:02d}:{t[4]:02d}:{t[5]:02d}"

# Sync time on startup
if connect_wifi():
    sync_ntp()

print(f"[{now()}] Starting, LED: ON")

while True:
    # Touch detection (edge trigger)
    is_touching = touch.read() < TOUCH_THRESHOLD
    if is_touching and not last_touch:
        LED_ENABLED = not LED_ENABLED
        print(f"[{now()}] [Touch] LED: {'ON' if LED_ENABLED else 'OFF'}")
    last_touch = is_touching

    # PIR detection
    if pir.value() == 1:
        print(f"[{now()}] Action Move! LED={'ON' if LED_ENABLED else 'OFF'}")
        if LED_ENABLED:
            led.on()
        time.sleep(3)
        led.off()

    time.sleep(0.05)