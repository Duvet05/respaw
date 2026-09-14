"""Pico W UART receiver. USB is a diagnostic console, not a second command link."""

import json
from machine import Pin, UART
import time

from telemetry import Receiver


def open_uart():
    Pin(1, Pin.IN, Pin.PULL_UP)
    uart = UART(0, baudrate=115200, bits=8, parity=None, stop=1,
                tx=Pin(0), rx=Pin(1), rxbuf=1024, timeout=0, timeout_char=2)
    # MicroPython initializes both UART pins. Release TX immediately; this
    # receiver never transmits to the Mega, and GP0 is left unconnected.
    Pin(0, Pin.IN)
    return uart


def run():
    uart = open_uart()
    led = Pin("LED", Pin.OUT, value=0)
    receiver = Receiver()
    last_status = time.ticks_ms()
    print(json.dumps({"v": 1, "type": "pico_ready", "board": "pico_w",
                      "uart": 0, "rx_gpio": 1, "baud": 115200}))
    try:
        while True:
            now = time.ticks_ms()
            if uart.any():
                chunk = uart.read(min(uart.any(), 128))
                if chunk:
                    for event in receiver.feed(chunk, now):
                        print(json.dumps({"v": 1, "type": "pico_rx", "event": event}))
            status = receiver.snapshot(now)
            # Solid when receiving, slow pulse while waiting. No fake readings.
            led.value(1 if status["connected"] else int(now % 2000 < 100))
            if time.ticks_diff(now, last_status) >= 2000:
                print(json.dumps({"v": 1, "type": "pico_status", "status": status}))
                last_status = now
            time.sleep_ms(5)
    finally:
        uart.deinit()
        Pin(0, Pin.IN)
        Pin(1, Pin.IN, Pin.PULL_UP)
        led.off()


if __name__ == "__main__":
    run()
