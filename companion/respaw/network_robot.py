"""Companion transport through the local robot-link operator, without simulation."""

from copy import deepcopy
from contextlib import nullcontext
import json
from pathlib import Path
import re
import secrets
import threading
from urllib.parse import urlparse

from .model import EXPRESSIONS


class CommandCancelled(RuntimeError):
    pass


class NetworkRobot:
    supports_command_cancellation = True

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
                      "connected": False, "capabilities": None, "transport": "wifi"}
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
            raw = websocket.recv(timeout=self.timeout)
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
        status = self._request({"v": 1, "type": "status"})
        if (status.get("type") != "status" or type(status.get("connected")) is not bool
                or (self.robot_id is not None and status.get("robot_id") != self.robot_id)):
            raise RuntimeError("El servidor respondió con una identidad o estado no válido.")
        capabilities = status.get("capabilities")
        ready = (status["connected"] and status.get("transport") == "wifi"
                 and status.get("mega_connected") is True and status.get("command_ready") is True
                 and isinstance(capabilities, dict)
                 and capabilities.get("commands") is True)
        measurement = None
        if ready:
            telemetry = self._request({"v": 1, "type": "telemetry", "kind": "measurement"})
            if telemetry.get("type") != "telemetry" or telemetry.get("kind") != "measurement":
                raise RuntimeError("El servidor devolvió telemetría no válida.")
            measurement = telemetry.get("event")
        with self.lock:
            self.state.update(connected=status["connected"], ready=bool(ready), capabilities=capabilities,
                              contact=status.get("contact") if ready else None,
                              measurement=measurement,
                              error=None if ready else "El Pico o el Mega aún no confirma el enlace.")

    def _read(self):
        while not self.closed.is_set():
            try:
                self._refresh()
            except RuntimeError:
                with self.lock:
                    self.state.update(ready=False, connected=False, contact=None, measurement=None,
                                      error="Se perdió el enlace de red con el robot.")
            self.closed.wait(self.poll_interval)

    def snapshot(self):
        with self.lock:
            return deepcopy(self.state)

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
