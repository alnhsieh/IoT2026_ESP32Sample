from machine import Pin
import time

pir = Pin(23, Pin.IN)
last = -1

while True:
    val = pir.value()
    if val != last:
        print(f"change：{last} → {val}")
        last = val
    time.sleep(0.05)