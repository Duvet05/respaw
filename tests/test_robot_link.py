import asyncio
import importlib.util
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from robot_link_server import LinkServer, Session, action_line, decode_packet, encode

HAS_WEBSOCKETS = importlib.util.find_spec("websockets") is not None


class ActionTests(unittest.TestCase):
    def test_existing_mega_commands_and_bounds(self):
        for command in ("PING", "STOP", "MEASURE"):
            for ident in (1, 65535):
                packet = {"v": 1, "type": "action", "id": ident, "command": command}
                self.assertEqual(action_line(packet), ("V1 %d %s\n" % (ident, command)).encode())
        packet = {"v": 1, "type": "action", "id": 2, "command": "FACE", "argument": "warm"}
        self.assertEqual(action_line(packet), b"V1 2 FACE warm\n")
        for field, bad in (("v", True), ("id", True), ("id", 0), ("id", 65536),
                           ("argument", "happy"), ("argument", "warm\nV1 3 STOP"), ("command", "EXEC")):
            with self.subTest(field=field, bad=bad), self.assertRaises(ValueError):
                action_line({**packet, field: bad})
        with self.assertRaises(ValueError):
            action_line({**packet, "extra": "ignored?"})

    def test_packet_limits_and_version(self):
        for raw in (b"{}", "[]", '{"v":true}', '{"v":2}', 'x' * 513, ""):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                decode_packet(raw)


class SendRaceTests(unittest.IsolatedAsyncioTestCase):
    async def test_send_disconnect_race_preserves_original_exception(self):
        server = LinkServer("t" * 32, "test_pico", emit=lambda event: None)

        class DisconnectWhileSending:
            async def send(self, raw):
                await asyncio.sleep(0)
                server._finish(server.session, json.loads(raw)["id"], "disconnected")
                raise ConnectionResetError("peer disconnected during send")

        now = asyncio.get_running_loop().time()
        server.session = Session(DisconnectWhileSending(), "test_pico", "wifi", now, now)
        with self.assertRaisesRegex(ConnectionResetError, "peer disconnected"):
            await server.send_action("PING")
        self.assertEqual(server.session.pending, {})
        self.assertEqual(server.session.last_action["stage"], "disconnected")


@unittest.skipUnless(HAS_WEBSOCKETS, "Install requirements-link.txt for WebSocket integration tests")
class LinkTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.events = []
        self.token = "t" * 32
        self.probe = LinkServer(self.token, "test_pico", face="warm", emit=self.events.append)
        self.server = await self.probe.start(port=0)
        self.url = "ws://127.0.0.1:%d/robot" % self.server.sockets[0].getsockname()[1]
        self.operator_server = await self.probe.start_operator(port=0)
        self.operator_url = "ws://127.0.0.1:%d/operator" % self.operator_server.sockets[0].getsockname()[1]

    async def asyncTearDown(self):
        self.server.close()
        self.operator_server.close()
        await self.server.wait_closed()
        await self.operator_server.wait_closed()

    def connect(self, token=None, path=None):
        from websockets.asyncio.client import connect
        return connect(path or self.url, additional_headers={"Authorization": "Bearer " + (token or self.token)},
                       proxy=None, compression=None, close_timeout=1)

    async def hello(self, ws, transport="usb_diagnostic"):
        await ws.send(encode({"v": 1, "type": "hello", "robot_id": "test_pico", "transport": transport}))
        welcome = json.loads(await asyncio.wait_for(ws.recv(), 2))
        self.assertEqual(welcome["type"], "welcome")
        return json.loads(await asyncio.wait_for(ws.recv(), 2))

    async def test_two_connections_ack_event_and_heartbeat(self):
        for _ in range(2):
            async with self.connect() as ws:
                action = await self.hello(ws)
                await ws.send(encode({"v": 1, "type": "ack", "id": action["id"], "stage": "validated",
                                      "uart_line": action_line(action).decode()}))
                self.assertEqual(json.loads(await ws.recv())["type"], "ack_received")
                await ws.send(encode({"v": 1, "type": "heartbeat", "id": 7}))
                self.assertEqual(json.loads(await ws.recv()), {"v": 1, "type": "heartbeat_ack", "id": 7})
                await ws.send(encode({"v": 1, "type": "event", "source": "synthetic", "event": {
                    "v": 1, "type": "ready", "board": "mega2560", "sensor": False, "audio": False}}))
                self.assertEqual(json.loads(await ws.recv())["source"], "synthetic")
        self.assertEqual(sum(e["type"] == "action_validated" for e in self.events), 2)

    async def test_auth_path_and_browser_origin(self):
        from websockets.asyncio.client import connect
        from websockets.exceptions import InvalidStatus
        for token, path, origin, status in (("bad", self.url, None, 401),
                                          (self.token, self.url + "/wrong", None, 404),
                                          (self.token, self.url, "https://foreign.example", 403)):
            with self.subTest(status=status), self.assertRaises(InvalidStatus) as error:
                async with connect(path, additional_headers={"Authorization": "Bearer " + token},
                                   origin=origin, proxy=None):
                    pass
            self.assertEqual(error.exception.response.status_code, status)

    async def test_invalid_hello_bad_ack_and_oversized_packet(self):
        from websockets.exceptions import ConnectionClosed
        for mode, code in (("hello", 1008), ("ack", 1008), ("size", 1009)):
            async with self.connect() as ws:
                if mode == "hello":
                    await ws.send('{"v":true,"type":"hello"}')
                else:
                    action = await self.hello(ws)
                    if mode == "size":
                        await ws.send("x" * 513)
                    else:
                        await ws.send(encode({"v": 1, "type": "ack", "id": action["id"], "stage": "mega_accepted",
                                              "uart_line": action_line(action).decode()}))
                with self.assertRaises(ConnectionClosed) as error:
                    await asyncio.wait_for(ws.recv(), 2)
                self.assertEqual(error.exception.rcvd.code, code)

    async def test_native_uart_event_and_truthful_action_error(self):
        async with self.connect() as ws:
            action = await self.hello(ws, transport="wifi")
            await ws.send(encode({"v": 1, "type": "action_error", "id": action["id"], "reason": "mega_unavailable"}))
            self.assertEqual(json.loads(await ws.recv()), {"v": 1, "type": "action_error_received", "id": action["id"],
                                                          "reason": "mega_unavailable"})
            await ws.send(encode({"v": 1, "type": "event", "source": "uart", "event": {
                "v": 1, "type": "ready", "board": "mega2560", "sensor": False, "audio": False}}))
            self.assertEqual(json.loads(await ws.recv())["source"], "uart")
            status = self.probe.status()
            self.assertTrue(status["connected"])
            self.assertEqual(status["transport"], "wifi")
            self.assertEqual(status["last_action"]["reason"], "mega_unavailable")
            self.assertEqual(status["last_event"]["source"], "uart")
        self.assertEqual(sum(e["type"] == "action_error" for e in self.events), 1)
        self.assertFalse(any("accepted" in e["type"] or "executed" in e["type"] for e in self.events))

    async def test_pairing_duplicate_connection_and_packet_types(self):
        from websockets.exceptions import ConnectionClosed
        for raw in (encode({"v": 1, "type": "hello", "robot_id": "different_pico", "transport": "wifi"}),
                    b'{"v":1,"type":"hello","robot_id":"test_pico","transport":"wifi"}'):
            async with self.connect() as ws:
                await ws.send(raw)
                with self.assertRaises(ConnectionClosed) as error:
                    await ws.recv()
                self.assertEqual(error.exception.rcvd.code, 1008)
        async with self.connect() as first:
            await self.hello(first)
            async with self.connect() as second:
                await second.send(encode({"v": 1, "type": "hello", "robot_id": "test_pico", "transport": "wifi"}))
                with self.assertRaises(ConnectionClosed) as error:
                    await second.recv()
                self.assertEqual(error.exception.rcvd.reason, "robot_already_connected")
            self.assertTrue(self.probe.status()["connected"])

    async def test_duplicate_ack_stage_forgery_and_invalid_heartbeat(self):
        from websockets.exceptions import ConnectionClosed
        for mode in ("duplicate", "stage", "heartbeat", "synthetic_wifi", "unknown_id"):
            async with self.connect() as ws:
                action = await self.hello(ws, transport="wifi")
                ack = {"v": 1, "type": "ack", "id": action["id"], "stage": "validated",
                       "uart_line": action_line(action).decode()}
                if mode == "duplicate":
                    await ws.send(encode(ack))
                    await ws.recv()
                    bad = ack
                elif mode == "stage":
                    bad = {**ack, "stage": "mega_accepted"}
                elif mode == "heartbeat":
                    bad = {"v": 1, "type": "heartbeat", "id": True}
                elif mode == "unknown_id":
                    bad = {**ack, "id": 0}
                else:
                    bad = {"v": 1, "type": "event", "source": "synthetic", "event": {
                        "v": 1, "type": "ready", "board": "mega2560", "sensor": False, "audio": False}}
                await ws.send(encode(bad))
                with self.subTest(mode=mode), self.assertRaises(ConnectionClosed) as error:
                    await ws.recv()
                self.assertEqual(error.exception.rcvd.code, 1008)

    async def test_operator_is_separate_and_waits_for_native_result(self):
        from websockets.exceptions import InvalidStatus
        with self.assertRaises(InvalidStatus) as error:
            async with self.connect(path=self.url.replace("/robot", "/operator")):
                pass
        self.assertEqual(error.exception.response.status_code, 404)
        async with self.connect(path=self.operator_url) as operator:
            await operator.send(encode({"v": 1, "type": "status"}))
            self.assertFalse(json.loads(await operator.recv())["connected"])
            await operator.send(encode({"v": 1, "type": "command", "command": "PING"}))
            self.assertEqual(json.loads(await operator.recv())["reason"], "robot_offline")
            async with self.connect() as robot:
                action = await self.hello(robot, transport="wifi")
                await robot.send(encode({"v": 1, "type": "action_error", "id": action["id"], "reason": "mega_unavailable"}))
                await robot.recv()
                await operator.send(encode({"v": 1, "type": "command", "command": "MEASURE"}))
                command = json.loads(await robot.recv())
                self.assertEqual(command["command"], "MEASURE")
                self.assertNotEqual(command["id"], action["id"])
                await robot.send(encode({"v": 1, "type": "action_error", "id": command["id"], "reason": "unsupported"}))
                await robot.recv()
                self.assertEqual(json.loads(await operator.recv()), {"v": 1, "type": "command_result", "id": command["id"],
                                                                    "stage": "error", "reason": "unsupported"})

    async def test_hello_heartbeat_and_action_timeouts_without_replay(self):
        from websockets.exceptions import ConnectionClosed
        self.probe.hello_timeout = 0.1
        async with self.connect() as ws:
            with self.assertRaises(ConnectionClosed) as error:
                await ws.recv()
            self.assertEqual(error.exception.rcvd.reason, "hello_timeout")
        self.probe.heartbeat_timeout = 0.15
        self.probe.action_timeout = 0.05
        async with self.connect() as ws:
            first = await self.hello(ws)
            future = self.probe.session.pending[first["id"]][1]
            result = await asyncio.wait_for(asyncio.shield(future), 1)
            self.assertEqual(result["stage"], "unconfirmed")
            self.assertEqual(result["reason"], "ack_timeout")
            with self.assertRaises(ConnectionClosed) as error:
                await ws.recv()
            self.assertEqual(error.exception.rcvd.reason, "heartbeat_timeout")
        self.probe.face = None
        async with self.connect() as ws:
            await ws.send(encode({"v": 1, "type": "hello", "robot_id": "test_pico", "transport": "wifi"}))
            self.assertEqual(json.loads(await ws.recv())["type"], "welcome")
            await ws.send(encode({"v": 1, "type": "heartbeat", "id": 1}))
            self.assertEqual(json.loads(await ws.recv())["type"], "heartbeat_ack")
            with self.assertRaises(asyncio.TimeoutError):
                await asyncio.wait_for(ws.recv(), 0.04)
            self.assertIsNone(self.probe.status()["last_action"])
