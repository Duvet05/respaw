"""Simulator by default; an explicit --device enables the new Mega v2 protocol."""

import json
import math
import threading
import time

from .model import EXPRESSIONS


def parse_event(line):
    if len(line) > 1024:
        raise ValueError("Oversized robot event")
    event = json.loads(line, parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)))
    if not isinstance(event, dict) or event.get("v") != 1:
        raise ValueError("Unsupported robot protocol")
    if event.get("type") not in ("ready", "ack", "error", "measurement", "animation_done"):
        raise ValueError("Unknown robot event")
    if event["type"] == "measurement":
        if not isinstance(event.get("valid"), bool):
            raise ValueError("Missing measurement validity")
        if event["valid"]:
            for key, low, high in (("bpm", 40, 200), ("rmssd", 0, 1200)):
                value = event.get(key)
                if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value) or not low <= value <= high:
                    raise ValueError("Invalid physiology")
            if not isinstance(event.get("rr_count"), int) or not 10 <= event["rr_count"] <= 128:
                raise ValueError("Insufficient intervals")
    return event


class Robot:
    def __init__(self, device=None):
        self.lock = threading.Lock()
        self.device = device
        self.serial = None
        self.sequence = 0
        self.expression = "neutral"
        self.measurement = None
        self.error = None
        self.ready = device is None
        self.pending = {}
        self.closed = threading.Event()
        if device:
            try:
                import serial
            except ImportError as error:
                raise RuntimeError("Para USB instala pyserial: python3 -m pip install pyserial==3.5") from error
            self.serial = serial.Serial(device, 115200, timeout=0.1, write_timeout=0.5)
            self.reader = threading.Thread(target=self._read, daemon=True)
            self.reader.start()

    def snapshot(self):
        with self.lock:
            return {"simulated": not bool(self.device), "ready": self.ready,
                    "expression": self.expression, "measurement": self.measurement, "error": self.error}

    def command(self, command, argument=""):
        if command not in ("STOP", "FACE", "MEASURE", "PING"):
            raise ValueError("Acción no permitida.")
        if command == "FACE" and argument not in EXPRESSIONS:
            raise ValueError("Expresión no válida.")
        if command != "FACE" and argument:
            raise ValueError("Argumento inesperado.")
        with self.lock:
            if self.serial and not self.ready and command not in ("PING", "STOP"):
                raise RuntimeError("El Mega no ha confirmado el protocolo v2.")
            self.sequence = (self.sequence % 65535) + 1
            if command == "STOP":
                self.expression = "neutral"
            elif command == "FACE":
                self.expression = argument
            elif command == "MEASURE":
                # No invented vital signs in the simulator.
                self.measurement = {"v": 1, "type": "measurement", "valid": False,
                                    "reason": "simulator"} if not self.serial else None
            if self.serial:
                line = f"V1 {self.sequence} {command}" + (f" {argument}" if argument else "") + "\n"
                self.serial.write(line.encode("ascii"))
                self.pending[self.sequence] = time.monotonic()

    def _read(self):
        buffer = bytearray()
        overflow = False
        last_ping = 0.0
        try:
            while not self.closed.is_set():
                if time.monotonic() - last_ping >= 2:
                    self.command("PING")
                    last_ping = time.monotonic()
                chunk = self.serial.read(256)
                for byte in chunk:
                    if byte == 10:
                        if not overflow and buffer:
                            try:
                                event = parse_event(buffer.decode("ascii"))
                            except (ValueError, UnicodeError):
                                event = None
                            if event:
                                with self.lock:
                                    if event["type"] == "ready":
                                        self.ready = True
                                        self.error = None
                                    elif event["type"] == "ack":
                                        if event.get("id") in self.pending:
                                            self.pending.pop(event["id"])
                                            self.ready = True
                                            self.error = None
                                    elif event["type"] == "measurement":
                                        self.measurement = event
                                    elif event["type"] == "error":
                                        self.error = str(event.get("reason", "hardware_error"))
                                        self.pending.pop(event.get("id"), None)
                        buffer.clear()
                        overflow = False
                    elif not overflow:
                        if len(buffer) >= 1024:
                            overflow = True
                            buffer.clear()
                        else:
                            buffer.append(byte)
                with self.lock:
                    if any(time.monotonic() - t > 4 for t in self.pending.values()):
                        self.ready = False
                        self.error = "El Mega no confirma las órdenes; comprueba conexión y firmware v2."
                        self.pending.clear()
        except Exception:
            with self.lock:
                self.ready = False
                self.error = "Se perdió la conexión USB."

    def close(self):
        self.closed.set()
        if self.serial:
            self.reader.join(timeout=1)
            self.serial.close()
