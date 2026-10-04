#!/usr/bin/env python3
"""Authenticated Pico link; robot and local operator use separate listeners."""

import argparse
import asyncio
from dataclasses import dataclass, field
from http import HTTPStatus
import json
from pathlib import Path
import re
import secrets
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pico/source/respaw-v2"))
from link_protocol import EXPRESSIONS, MAX_PACKET, action_line, decode_packet
from telemetry import UART_ERROR_REASONS, parse_frame

ACTION_ERROR_REASONS = frozenset(UART_ERROR_REASONS + (
    "mega_unavailable", "unsupported", "ack_timeout", "uart_write_failed", "too_many_pending",
    "bad_frame",
))


def encode(packet):
    return json.dumps(packet, separators=(",", ":"), allow_nan=False)


@dataclass
class Session:
    websocket: object
    robot_id: str
    transport: str
    last_seen: float
    last_heartbeat: float
    pending: dict = field(default_factory=dict)
    last_action: dict | None = None
    last_event: dict | None = None
    capabilities: dict | None = None
    contact: dict | None = None
    measurement: dict | None = None
    measurement_at: float | None = None
    mega_at: float | None = None
    mega_uptime: int | None = None
    measuring: bool = False
    command_ready: bool = False
    command_ready_at: float | None = None
    dispatch_sequences: dict = field(default_factory=dict)
    touch_id: str | None = None
    touch_at: float | None = None


@dataclass
class Pending:
    line: str
    command: str
    future: object
    expiry: object
    stage: str = "pending"


class LinkServer:
    def __init__(self, token, robot_id, face=None, emit=None, hello_timeout=10,
                 heartbeat_timeout=35, action_timeout=10):
        if not re.fullmatch(r"[A-Za-z0-9_-]{32,128}", token):
            raise ValueError("El token debe tener entre 32 y 128 caracteres ASCII seguros.")
        if not isinstance(robot_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,32}", robot_id):
            raise ValueError("Identificador de robot no válido.")
        if face is not None and face not in EXPRESSIONS:
            raise ValueError("Expresión no válida.")
        self.token, self.robot_id, self.face = token, robot_id, face
        self.emit = emit or (lambda event: print(encode(event), flush=True))
        self.hello_timeout, self.heartbeat_timeout = hello_timeout, heartbeat_timeout
        self.action_timeout = action_timeout
        self.session = None
        self.next_id = secrets.randbelow(65535) + 1

    def _authorize(self, connection, request, path):
        if request.path != path:
            return connection.respond(HTTPStatus.NOT_FOUND, "Ruta no encontrada.\n")
        values = request.headers.get_all("Authorization")
        provided = values[0].encode("utf-8") if len(values) == 1 else b""
        if not secrets.compare_digest(provided, ("Bearer " + self.token).encode("ascii")):
            return connection.respond(HTTPStatus.UNAUTHORIZED, "Token requerido.\n")

    def authorize(self, connection, request):
        return self._authorize(connection, request, "/robot")

    def authorize_operator(self, connection, request):
        return self._authorize(connection, request, "/operator")

    def status(self):
        session = self.session
        result = {"v": 1, "type": "status", "robot_id": self.robot_id,
                  "connected": session is not None}
        if session is not None:
            now = asyncio.get_running_loop().time()
            result.update(transport=session.transport,
                          last_seen_age_ms=min(999999999, int(max(0, now - session.last_seen) * 1000)),
                          last_action=session.last_action,
                          mega_connected=self._mega_connected(session, now),
                          command_ready=(session.command_ready and session.command_ready_at is not None
                                         and 0 <= now - session.command_ready_at < min(10, self.heartbeat_timeout)
                                         and self._mega_connected(session, now)),
                          capabilities=session.capabilities if self._mega_connected(session, now) else None,
                          contact=session.contact if self._mega_connected(session, now) else None)
            if session.last_event is not None:
                result["last_event"] = {key: session.last_event[key] for key in ("source", "event_type")}
        return result

    @staticmethod
    def _mega_connected(session, now):
        timeout = 36 if session.measuring else 6
        return session.mega_at is not None and 0 <= now - session.mega_at < timeout

    def telemetry(self, kind):
        session = self.session
        now = asyncio.get_running_loop().time()
        event = None
        if session is not None and self._mega_connected(session, now):
            if kind == "contact":
                event = session.contact
            elif kind == "touch":
                age = None if session.touch_at is None else now - session.touch_at
                if age is not None and 0 <= age < 2:
                    event = {"id": session.touch_id, "age_ms": int(age * 1000)}
            elif session.measurement_at is not None and 0 <= now - session.measurement_at < 60:
                event = session.measurement
        return {"v": 1, "type": "telemetry", "kind": kind, "event": event}

    def _receive_uart(self, session, event, now):
        self._clear_expired_mega(session, now)
        kind = event["type"]
        if kind in ("ready", "heartbeat"):
            reboot = (kind == "ready" or (session.mega_uptime is not None and
                      ((event["uptime_ms"] - session.mega_uptime) & 0xFFFFFFFF) >= 0x80000000))
            if reboot:
                session.contact = session.measurement = session.measurement_at = None
                session.touch_id = session.touch_at = None
                session.mega_uptime = None
            session.capabilities = {key: event.get(key, False) for key in ("sensor", "audio", "commands")}
            session.measuring = event.get("measuring", False)
            if session.measuring:
                session.measurement = session.measurement_at = None
            if kind == "heartbeat":
                session.mega_uptime = event["uptime_ms"]
        elif kind == "contact":
            # A press present at startup is a baseline, not a new interaction.
            reboot = (session.mega_uptime is not None and
                      ((event["uptime_ms"] - session.mega_uptime) & 0xFFFFFFFF) >= 0x80000000)
            if reboot:
                session.contact = session.measurement = session.measurement_at = session.capabilities = None
                session.touch_id = session.touch_at = None
                session.measuring = False
            if session.contact is not None and session.contact["pressed"] is False and event["pressed"] is True:
                session.touch_id, session.touch_at = secrets.token_hex(8), now
            session.contact = event
            session.mega_uptime = event["uptime_ms"]
        elif kind == "measurement":
            session.measurement, session.measurement_at = event, now
            session.measuring = False
        elif kind not in ("ack", "error"):
            return
        session.mega_at = now

    def _clear_expired_mega(self, session, now):
        if session.mega_at is not None and not self._mega_connected(session, now):
            # A frame after an outage cannot recover missed contact edges or
            # establish that earlier readings and capabilities still apply.
            session.contact = session.measurement = session.measurement_at = session.capabilities = None
            session.touch_id = session.touch_at = None
            session.measuring = False

    async def send_action(self, command, argument=None):
        packet = {"v": 1, "type": "action", "id": self.next_id, "command": command}
        if argument is not None:
            packet["argument"] = argument
        line = action_line(packet).decode("ascii")
        session = self.session
        if session is None:
            return {"v": 1, "type": "command_result", "stage": "error", "reason": "robot_offline"}
        if len(session.pending) >= (16 if command == "STOP" else 15):
            return {"v": 1, "type": "command_result", "stage": "error", "reason": "too_many_pending"}
        self.next_id = self.next_id % 65535 + 1
        future = asyncio.get_running_loop().create_future()
        expiry = asyncio.get_running_loop().call_later(self.action_timeout, self._expire, session, packet["id"])
        session.pending[packet["id"]] = Pending(line, command, future, expiry)
        session.last_action = {"id": packet["id"], "command": command, "stage": "pending"}
        try:
            await session.websocket.send(encode(packet))
        except Exception:
            self._finish(session, packet["id"], "disconnected")
            raise
        self.emit({"type": "action_sent", "robot_id": self.robot_id, "id": packet["id"], "command": command})
        return future

    def _finish(self, session, ident, stage, reason=None):
        pending = session.pending.pop(ident, None)
        if pending is None:
            return
        pending.expiry.cancel()
        result = {"v": 1, "type": "command_result", "id": ident, "stage": stage}
        if reason is not None:
            result["reason"] = reason
        if session.last_action is not None and session.last_action["id"] == ident:
            session.last_action.update(stage=stage)
            if reason is not None:
                session.last_action["reason"] = reason
        if not pending.future.done():
            pending.future.set_result(result)

    def _expire(self, session, ident):
        if ident in session.pending:
            self._finish(session, ident, "unconfirmed", "ack_timeout")
            self.emit({"type": "action_unconfirmed", "robot_id": self.robot_id, "id": ident})

    async def handle(self, websocket):
        from websockets.exceptions import ConnectionClosed

        session = None
        try:
            hello = decode_packet(await asyncio.wait_for(websocket.recv(), self.hello_timeout))
            if (set(hello) != {"v", "type", "robot_id", "transport"} or hello["type"] != "hello"
                    or hello["robot_id"] != self.robot_id
                    or hello["transport"] not in ("usb_diagnostic", "wifi")):
                raise ValueError("bad_hello")
            if self.session is not None:
                await websocket.close(1008, "robot_already_connected")
                return
            now = asyncio.get_running_loop().time()
            session = Session(websocket, self.robot_id, hello["transport"], now, now)
            self.session = session
            self.emit({"type": "connected", "robot_id": self.robot_id, "transport": session.transport})
            await websocket.send(encode({"v": 1, "type": "welcome", "robot_id": self.robot_id}))
            if self.face is not None:
                await self.send_action("FACE", self.face)
            while True:
                remaining = self.heartbeat_timeout - (asyncio.get_running_loop().time() - session.last_heartbeat)
                packet = decode_packet(await asyncio.wait_for(websocket.recv(), max(0, remaining)))
                kind = packet.get("type")
                session.last_seen = asyncio.get_running_loop().time()
                if kind == "heartbeat":
                    if (set(packet) not in ({"v", "type", "id"}, {"v", "type", "id", "command_ready"})
                            or type(packet["id"]) is not int or not 1 <= packet["id"] <= 65535
                            or ("command_ready" in packet and type(packet["command_ready"]) is not bool)):
                        raise ValueError("bad_heartbeat")
                    session.last_heartbeat = session.last_seen
                    session.command_ready = packet.get("command_ready", False) and session.transport == "wifi"
                    session.command_ready_at = session.last_seen
                    await websocket.send(encode({"v": 1, "type": "heartbeat_ack", "id": packet["id"]}))
                elif kind in ("ack", "action_error"):
                    ident = packet.get("id")
                    if type(ident) is not int or ident not in session.pending:
                        raise ValueError("bad_ack_id")
                    if kind == "ack":
                        pending = session.pending[ident]
                        stage = packet.get("stage")
                        valid_transition = ((stage == "validated" and session.transport == "usb_diagnostic" and pending.stage == "pending")
                                            or (stage == "forwarded" and session.transport == "wifi" and pending.stage == "pending")
                                            or (stage == "mega_accepted" and session.transport == "wifi" and pending.stage == "forwarded"))
                        if (set(packet) != {"v", "type", "id", "stage", "uart_line"}
                                or not valid_transition or packet["uart_line"] != pending.line):
                            raise ValueError("bad_ack")
                        stage, reason = packet["stage"], None
                    else:
                        if (set(packet) != {"v", "type", "id", "reason"}
                                or packet["reason"] not in ACTION_ERROR_REASONS):
                            raise ValueError("bad_action_error")
                        stage, reason = "error", packet["reason"]
                    if stage == "forwarded":
                        session.pending[ident].stage = stage
                        if session.last_action["id"] == ident:
                            session.last_action["stage"] = stage
                    else:
                        if stage == "mega_accepted":
                            self._clear_expired_mega(session, session.last_seen)
                            pending = session.pending[ident]
                            if pending.command in ("MEASURE", "STOP"):
                                session.measuring = pending.command == "MEASURE"
                                session.measurement = session.measurement_at = None
                        self._finish(session, ident, stage, reason)
                    event = {"type": "action_" + stage, "robot_id": self.robot_id, "id": ident, "stage": stage}
                    if reason is not None:
                        event["reason"] = reason
                    self.emit(event)
                    if kind == "action_error":
                        response = {"v": 1, "type": "action_error_received", "id": ident, "reason": reason}
                    else:
                        response = {"v": 1, "type": "ack_received", "id": ident, "stage": stage}
                    await websocket.send(encode(response))
                elif kind == "event":
                    if (set(packet) != {"v", "type", "source", "event"}
                            or packet["source"] not in ("synthetic", "uart")
                            or (packet["source"] == "synthetic" and session.transport != "usb_diagnostic")):
                        raise ValueError("bad_event")
                    event = parse_frame(encode(packet["event"]))
                    if packet["source"] == "uart":
                        self._receive_uart(session, event, session.last_seen)
                    session.last_event = {"source": packet["source"], "event_type": event["type"], "at": session.last_seen}
                    self.emit({"type": "event_received", "robot_id": self.robot_id,
                               "source": packet["source"], "event_type": event["type"]})
                    await websocket.send(encode({"v": 1, "type": "event_received", "source": packet["source"], "event_type": event["type"]}))
                else:
                    raise ValueError("bad_type")
        except (ValueError, TypeError, KeyError, OverflowError):
            await websocket.close(1008, "invalid_packet")
        except asyncio.TimeoutError:
            await websocket.close(1008, "hello_timeout" if session is None else "heartbeat_timeout")
        except ConnectionClosed:
            pass
        finally:
            if session is not None:
                if self.session is session:
                    self.session = None
                for ident in list(session.pending):
                    self._finish(session, ident, "disconnected")
                self.emit({"type": "disconnected", "robot_id": self.robot_id})

    async def handle_operator(self, websocket):
        from websockets.exceptions import ConnectionClosed

        try:
            async for raw in websocket:
                packet = decode_packet(raw)
                if set(packet) == {"v", "type"} and packet["type"] == "status":
                    result = self.status()
                elif (set(packet) == {"v", "type", "kind"} and packet["type"] == "telemetry"
                      and packet["kind"] in ("contact", "measurement", "touch")):
                    result = self.telemetry(packet["kind"])
                elif packet.get("type") == "command":
                    result = await self.operator_command(packet)
                    if isinstance(result, asyncio.Future):
                        result = await asyncio.shield(result)
                else:
                    raise ValueError("bad_operator_packet")
                encoded = encode(result)
                if len(encoded.encode("utf-8")) > MAX_PACKET:
                    raise ValueError("oversized_response")
                await websocket.send(encoded)
        except (ValueError, TypeError, KeyError):
            await websocket.close(1008, "invalid_packet")
        except ConnectionClosed:
            pass

    async def operator_command(self, packet):
        fields = set(packet)
        ordered = "client_id" in packet or "sequence" in packet
        base = fields - {"client_id", "sequence"}
        if base not in ({"v", "type", "command"}, {"v", "type", "command", "argument"}):
            raise ValueError("bad_operator_command")
        action_line({**{key: packet[key] for key in base}, "type": "action", "id": self.next_id})
        if ordered:
            client_id, sequence = packet.get("client_id"), packet.get("sequence")
            if (not isinstance(client_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{16,64}", client_id)
                    or type(sequence) is not int or not 1 <= sequence <= 0xFFFFFFFF):
                raise ValueError("bad_operator_sequence")
            session = self.session
            if session is not None:
                highest = session.dispatch_sequences.get(client_id, 0)
                if sequence <= highest and packet["command"] != "STOP":
                    return {"v": 1, "type": "command_result", "stage": "error", "reason": "superseded"}
                if client_id not in session.dispatch_sequences and len(session.dispatch_sequences) >= 64:
                    if packet["command"] != "STOP":
                        return {"v": 1, "type": "command_result", "stage": "error", "reason": "too_many_clients"}
                else:
                    session.dispatch_sequences[client_id] = max(highest, sequence)
        return await self.send_action(packet["command"], packet.get("argument"))

    async def start(self, host="127.0.0.1", port=8766):
        from websockets.asyncio.server import serve

        return await serve(self.handle, host, port, process_request=self.authorize,
                           origins=[None], compression=None, max_size=MAX_PACKET,
                           max_queue=4, ping_interval=10, ping_timeout=10, close_timeout=3)

    async def start_operator(self, port=8767):
        from websockets.asyncio.server import serve

        # This listener must never be included in the public Funnel route.
        return await serve(self.handle_operator, "127.0.0.1", port,
                           process_request=self.authorize_operator, origins=[None],
                           compression=None, max_size=MAX_PACKET, max_queue=4,
                           ping_interval=10, ping_timeout=10, close_timeout=3)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8766)
    parser.add_argument("--operator-port", type=int, default=8767, help="Puerto separado, siempre 127.0.0.1")
    parser.add_argument("--token-file", type=Path, required=True)
    parser.add_argument("--robot-id", required=True)
    parser.add_argument("--face-on-connect", choices=EXPRESSIONS, help="Prueba opcional; no implica ejecución física")
    args = parser.parse_args()
    if not 1 <= args.port <= 65535 or not 1 <= args.operator_port <= 65535 or args.port == args.operator_port:
        parser.error("Puertos distintos entre 1 y 65535 requeridos")
    probe = LinkServer(args.token_file.read_text().strip(), args.robot_id, face=args.face_on_connect)

    async def run():
        async with await probe.start(args.host, args.port) as server, await probe.start_operator(args.operator_port):
            print(encode({"type": "listening", "host": args.host, "port": args.port,
                          "operator_host": "127.0.0.1", "operator_port": args.operator_port}), flush=True)
            await server.serve_forever()

    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
