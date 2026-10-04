"""Companion transport through the local robot-link operator, without simulation."""

from copy import deepcopy
from contextlib import nullcontext
import json
from pathlib import Path
import re
import secrets
import threading
import time
from urllib.parse import urlparse

from .model import EXPRESSIONS


class CommandCancelled(RuntimeError):
    pass


class NetworkRobot:
    supports_command_cancellation = True
    supports_contact_response = True

    def __init__(self, url="ws://127.0.0.1:8767/operator", token_file=None, *, token=None,
                 robot_id=None, poll_interval=0.5, timeout=12, start_reader=True):
        address = urlparse(url)
        if (address.scheme != "ws" or address.hostname not in ("127.0.0.1", "::1", "localhost")
                or address.path != "/operator" or address.username or address.password
                or address.query or address.fragment):
            raise ValueError("El enlace del robot requiere un operador WebSocket local.")
        if (token_file is None) == (token is None):
            raise ValueError("Indica un archivo de token o un token para el enlace.")
        self.token = Path(token_file).read_text().strip() if token_file is not None else token
        if not isinstance(self.token, str) or not re.fullmatch(r"[A-Za-z0-9_-]{32,128}", self.token):
            raise ValueError("Token de enlace no válido.")
        if robot_id is not None and (not isinstance(robot_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,32}", robot_id)):
            raise ValueError("Identificador del robot no válido.")
        try:
            from websockets.sync.client import connect
        except ImportError as error:
            raise RuntimeError("El enlace de red requiere instalar requirements-link.txt.") from error
        self.connect = connect
        self.url, self.robot_id, self.timeout = url, robot_id, timeout
        self.poll_interval = poll_interval
        self.lock = threading.Lock()
        self.dispatch_lock = threading.Lock()
        self.dispatch_sequence = 0
        self.client_id = secrets.token_hex(16)
        self.closed = threading.Event()
        self.state = {"simulated": False, "ready": False, "expression": "neutral", "measurement": None,
                      "error": "El Pico aún no confirma el enlace con el Mega.", "contact": None,
                      "connected": False, "capabilities": None, "transport": "wifi", "touch": None}
        self.touch_received_at = None
        self.last_refresh_at = None
        self.reader = None
        if start_reader:
            self.reader = threading.Thread(target=self._read, daemon=True, name="respaw-network-robot")
            self.reader.start()

    def _request(self, packet, cancelled=None):
        if self.closed.is_set():
            raise RuntimeError("El enlace del robot está cerrado.")
        websocket = None
        order = None
        try:
            dispatch = self.dispatch_lock if packet["type"] == "command" else nullcontext()
            with dispatch:
                if self.closed.is_set():
                    raise CommandCancelled("El enlace del robot está cerrado.")
                if cancelled is not None and cancelled():
                    raise CommandCancelled("La orden del robot se canceló antes de enviarse.")
                websocket = self.connect(self.url, additional_headers={"Authorization": "Bearer " + self.token},
                                         proxy=None, compression=None, max_size=512, open_timeout=3,
                                         close_timeout=1)
                if self.closed.is_set():
                    raise CommandCancelled("El enlace del robot está cerrado.")
                if cancelled is not None and cancelled():
                    raise CommandCancelled("La orden del robot se canceló antes de enviarse.")
                if packet["type"] == "command":
                    if self.dispatch_sequence >= 0xFFFFFFFF:
                        raise RuntimeError("Reinicia el enlace para renovar la secuencia de órdenes.")
                    self.dispatch_sequence += 1
                    order = self.dispatch_sequence
                    packet = {**packet, "client_id": self.client_id, "sequence": order}
                websocket.send(json.dumps(packet, separators=(",", ":")))
            if cancelled is None:
                raw = websocket.recv(timeout=self.timeout)
            else:
                deadline = time.monotonic() + self.timeout
                while True:
                    if self.closed.is_set() or cancelled():
                        raise CommandCancelled("La espera de confirmación del robot se canceló.")
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise TimeoutError("Robot acknowledgement timeout")
                    try:
                        raw = websocket.recv(timeout=min(0.2, remaining))
                        break
                    except TimeoutError:
                        continue
            if not isinstance(raw, str) or len(raw.encode("utf-8")) > 512:
                raise ValueError("Invalid link response")
            result = json.loads(raw)
            if not isinstance(result, dict) or type(result.get("v")) is not int or result["v"] != 1:
                raise ValueError("Invalid link response")
            if order is not None:
                result["_dispatch_order"] = order
            return result
        except CommandCancelled:
            raise
        except Exception as error:
            raise RuntimeError("No se pudo confirmar la respuesta del robot por red.") from error
        finally:
            if websocket is not None:
                try:
                    websocket.close()
                except OSError:
                    pass

    def _refresh(self):
        try:
            self._refresh_state()
        except RuntimeError:
            with self.lock:
                self.state.update(ready=False, connected=False, contact=None, measurement=None,
                                  touch=None, error="Se perdió el enlace de red con el robot.")
                self.touch_received_at = None
                self.last_refresh_at = None
            raise

    def _refresh_state(self):
        started = time.monotonic()
        status = self._request({"v": 1, "type": "status"})
        if (status.get("type") != "status" or type(status.get("connected")) is not bool
                or (self.robot_id is not None and status.get("robot_id") != self.robot_id)):
            raise RuntimeError("El servidor respondió con una identidad o estado no válido.")
        capabilities = status.get("capabilities")
        ready = (status["connected"] and status.get("transport") == "wifi"
                 and status.get("mega_connected") is True and status.get("command_ready") is True
                 and isinstance(capabilities, dict)
                 and capabilities.get("commands") is True and time.monotonic() - started < 6)
        measurement = None
        touch = None
        touch_received_at = None
        if ready:
            telemetry = self._request({"v": 1, "type": "telemetry", "kind": "measurement"})
            if telemetry.get("type") != "telemetry" or telemetry.get("kind") != "measurement":
                raise RuntimeError("El servidor devolvió telemetría no válida.")
            measurement = telemetry.get("event")
            touch_request_at = time.monotonic()
            pulse = self._request({"v": 1, "type": "telemetry", "kind": "touch"})
            if (set(pulse) != {"v", "type", "kind", "event"} or pulse["type"] != "telemetry" or pulse["kind"] != "touch"):
                raise RuntimeError("El servidor devolvió un pulso de contacto no válido.")
            touch = pulse["event"]
            if touch is not None:
                if (not isinstance(touch, dict) or set(touch) != {"id", "age_ms"}
                        or not isinstance(touch["id"], str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", touch["id"])
                        or type(touch["age_ms"]) is not int or not 0 <= touch["age_ms"] <= 2000):
                    raise RuntimeError("El servidor devolvió un pulso de contacto no válido.")
                # Conservatively include the request and close-handshake time.
                touch_received_at = touch_request_at
        fresh = time.monotonic() - started < 6
        if not fresh:
            ready = False
            capabilities = touch = measurement = touch_received_at = None
        with self.lock:
            self.state.update(connected=status["connected"], ready=bool(ready), capabilities=capabilities,
                              contact=status.get("contact") if ready else None,
                              measurement=measurement, touch=touch,
                              error=None if ready else "El Pico o el Mega aún no confirma el enlace.")
            self.touch_received_at = touch_received_at
            self.last_refresh_at = started

    def _read(self):
        while not self.closed.is_set():
            try:
                self._refresh()
            except RuntimeError:
                pass
            self.closed.wait(self.poll_interval)

    def snapshot(self):
        with self.lock:
            result = deepcopy(self.state)
            if self.last_refresh_at is None or time.monotonic() - self.last_refresh_at >= 6:
                result.update(ready=False, capabilities=None, contact=None, measurement=None, touch=None)
                if self.last_refresh_at is not None:
                    result["error"] = "El estado del robot por red dejó de estar actualizado."
            touch = result["touch"]
            if touch is not None:
                elapsed = 0 if self.touch_received_at is None else max(0, time.monotonic() - self.touch_received_at)
                age = touch["age_ms"] + int(elapsed * 1000)
                result["touch"] = {"id": touch["id"], "age_ms": age} if age <= 2000 else None
            return result

    def command(self, command, argument="", cancelled=None):
        if command not in ("STOP", "FACE", "MEASURE", "PING"):
            raise ValueError("Acción no permitida.")
        if (command == "FACE" and argument not in EXPRESSIONS) or (command != "FACE" and argument):
            raise ValueError("Argumento no válido para la acción.")
        packet = {"v": 1, "type": "command", "command": command}
        if command == "FACE":
            packet["argument"] = argument
        result = self._request(packet) if cancelled is None else self._request(packet, cancelled=cancelled)
        order = result.pop("_dispatch_order", self.dispatch_sequence)
        if result.get("type") != "command_result" or result.get("stage") != "mega_accepted":
            reason = result.get("reason", "mega_unconfirmed")
            if not isinstance(reason, str) or not re.fullmatch(r"[a-z_]{1,40}", reason):
                reason = "mega_unconfirmed"
            with self.lock:
                if order == self.dispatch_sequence:
                    self.state["error"] = reason
            raise RuntimeError("El Mega no aceptó la orden: " + reason)
        if type(result.get("id")) is not int or not 1 <= result["id"] <= 65535:
            raise RuntimeError("Confirmación de orden no válida.")
        with self.lock:
            if order == self.dispatch_sequence:
                self.state["error"] = None
                if command == "FACE":
                    self.state["expression"] = argument
                elif command == "STOP":
                    self.state["expression"] = "neutral"
                    self.state["measurement"] = None
                elif command == "MEASURE":
                    self.state["measurement"] = None
        return result

    def close(self):
        self.closed.set()
        if self.reader is not None:
            self.reader.join(timeout=4)
