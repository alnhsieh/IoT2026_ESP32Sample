from machine import Pin
from time import ticks_us, sleep_us
import time

# ====== Pin setup ======
TRIG_PIN = 5
ECHO_PIN = 18

trig = Pin(TRIG_PIN, Pin.OUT)
echo = Pin(ECHO_PIN, Pin.IN)

# ====== Low-level measurement (single shot) ======
def _measure_once():
    trig.value(0)
    sleep_us(2)

    trig.value(1)
    sleep_us(10)
    trig.value(0)

    timeout = 30000  # 30ms (≈ 5 meters max)

    # Wait for echo HIGH
    start = ticks_us()
    while echo.value() == 0:
        if ticks_us() - start > timeout:
            return -1

    ts = ticks_us()

    # Wait for echo LOW
    start = ticks_us()
    while echo.value() == 1:
        if ticks_us() - start > timeout:
            return -1

    te = ticks_us()
    tc = te - ts

    # Convert to cm (faster formula)
    distance = tc / 58.0
    return distance


# ====== Retry wrapper ======
def get_distance():
    for _ in range(5):  # retry up to 5 times
        d = _measure_once()
        if d != -1 and 2 <= d <= 400:  # valid range
            return d
        time.sleep(0.01)
    return -1


# ====== Stable averaged reading ======
def get_stable_distance():
    readings = []

    for _ in range(5):
        d = get_distance()
        if d != -1:
            readings.append(d)
        time.sleep(0.05)

    if readings:
        return sum(readings) / len(readings)
    return -1


# ====== Main loop ======
while True:
    d = get_stable_distance()

    if d == -1:
        print("Invalid reading")
    else:
        print("Distance: {:.2f} cm".format(d))

    time.sleep(0.5)