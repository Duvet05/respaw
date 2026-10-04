import asyncio
import importlib.util
import json
from pathlib import Path
import sys
import time
import threading
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "companion"))
from robot_link_server import LinkServer, action_line, encode
from respaw.network_robot import CommandCancelled, NetworkRobot

HAS_WEBSOCKETS = importlib.util.find_spec("websockets") is not None


@unittest.skipUnless(HAS_WEBSOCKETS, "Install requirements-link.txt for network robot tests")
class NetworkRobotTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.token = "n" * 32
        self.link = LinkServer(self.token, "test_pico", emit=lambda event: None)
        self.server = await self.link.start(port=0)
        self.operator = await self.link.start_operator(port=0)
        self.robot_url = "ws://127.0.0.1:%d/robot" % self.server.sockets[0].getsockname()[1]
        self.operator_url = "ws://127.0.0.1:%d/operator" % self.operator.sockets[0].getsockname()[1]
        self.transport = NetworkRobot(self.operator_url, token=self.token, robot_id="test_pico", start_reader=False)

    async def asyncTearDown(self):
        self.transport.close()
        self.server.close()
        self.operator.close()
        await self.server.wait_closed()
        await self.operator.wait_closed()

    async def test_real_operator_updates_body_only_after_mega_acceptance(self):
        from websockets.asyncio.client import connect

        async with connect(self.robot_url, additional_headers={"Authorization": "Bearer " + self.token}, proxy=None) as pico:
            await pico.send(encode({"v": 1, "type": "hello", "robot_id": "test_pico", "transport": "wifi"}))
            await pico.recv()
            for event in ({"v": 1, "type": "ready", "board": "mega2560", "sensor": False, "audio": False, "commands": True},
                          {"v": 1, "type": "contact", "sensor": "fsr_a8", "pressed": True, "uptime_ms": 100}):
                await pico.send(encode({"v": 1, "type": "event", "source": "uart", "event": event}))
                await pico.recv()
            await pico.send(encode({"v": 1, "type": "heartbeat", "id": 1, "command_ready": True}))
            await pico.recv()
            await asyncio.to_thread(self.transport._refresh)
            self.assertTrue(self.transport.snapshot()["ready"])
            self.assertFalse(self.transport.snapshot()["simulated"])
            self.assertTrue(self.transport.snapshot()["contact"]["pressed"])
            pending = asyncio.create_task(asyncio.to_thread(self.transport.command, "FACE", "warm"))
            action = json.loads(await pico.recv())
            await pico.send(encode({"v": 1, "type": "ack", "id": action["id"], "stage": "forwarded",
                                    "uart_line": action_line(action).decode()}))
            await pico.recv()
            self.assertFalse(pending.done())
            self.assertEqual(self.transport.snapshot()["expression"], "neutral")
            await pico.send(encode({"v": 1, "type": "ack", "id": action["id"], "stage": "mega_accepted",
                                    "uart_line": action_line(action).decode()}))
            await pico.recv()
            self.assertEqual((await pending)["stage"], "mega_accepted")
            self.assertEqual(self.transport.snapshot()["expression"], "warm")
            pending = asyncio.create_task(asyncio.to_thread(self.transport.command, "MEASURE"))
            action = json.loads(await pico.recv())
            await pico.send(encode({"v": 1, "type": "action_error", "id": action["id"], "reason": "sensor_unavailable"}))
            await pico.recv()
            with self.assertRaisesRegex(RuntimeError, "sensor_unavailable"):
                await pending
            self.assertIsNone(self.transport.snapshot()["measurement"])

    async def test_offline_and_diagnostic_are_never_ready(self):
        await asyncio.to_thread(self.transport._refresh)
        self.assertFalse(self.transport.snapshot()["ready"])
        with self.assertRaisesRegex(RuntimeError, "robot_offline"):
            await asyncio.to_thread(self.transport.command, "PING")
        self.transport._request = lambda packet: {"v": 1, "type": "status", "connected": True, "robot_id": "test_pico",
                                                 "transport": "usb_diagnostic", "mega_connected": True,
                                                 "capabilities": {"commands": True}}
        self.transport._refresh()
        self.assertFalse(self.transport.snapshot()["ready"])
        self.transport._request = lambda packet: {"v": 1, "type": "command_result", "id": 1, "stage": "validated"}
        with self.assertRaisesRegex(RuntimeError, "mega_unconfirmed"):
            self.transport.command("FACE", "warm")
        self.assertEqual(self.transport.snapshot()["expression"], "neutral")

    async def test_stop_dispatch_does_not_wait_for_face_ack_and_cancellation_sends_nothing(self):
        from websockets.asyncio.client import connect

        async with connect(self.robot_url, additional_headers={"Authorization": "Bearer " + self.token}, proxy=None) as pico:
            await pico.send(encode({"v": 1, "type": "hello", "robot_id": "test_pico", "transport": "wifi"}))
            await pico.recv()
            face_task = asyncio.create_task(asyncio.to_thread(self.transport.command, "FACE", "warm"))
            face = json.loads(await pico.recv())
            stop_task = asyncio.create_task(asyncio.to_thread(self.transport.command, "STOP"))
            stop = json.loads(await asyncio.wait_for(pico.recv(), 1))
            self.assertEqual((face["command"], stop["command"]), ("FACE", "STOP"))
            self.assertFalse(face_task.done())
            for action in (stop, face):
                for stage in ("forwarded", "mega_accepted"):
                    await pico.send(encode({"v": 1, "type": "ack", "id": action["id"], "stage": stage,
                                            "uart_line": action_line(action).decode()}))
                    await pico.recv()
            await stop_task
            await face_task
            self.assertEqual(self.transport.snapshot()["expression"], "neutral")
            with self.assertRaises(CommandCancelled):
                await asyncio.to_thread(self.transport.command, "FACE", "warm", lambda: True)
            with self.assertRaises(asyncio.TimeoutError):
                await asyncio.wait_for(pico.recv(), 0.04)

    async def test_local_only_configuration_and_action_validation(self):
        for url in ("wss://remote.example/operator", "ws://127.0.0.1:8766/robot", "ws://localhost/operator?token=secret"):
            with self.assertRaises(ValueError):
                NetworkRobot(url, token=self.token, start_reader=False)
        for command, argument in (("FACE", "warm\nSTOP"), ("MEASURE", "warm"), ("PLAY", "1")):
            with self.assertRaises(ValueError):
                self.transport.command(command, argument)
        self.transport.close()
        with self.assertRaisesRegex(RuntimeError, "cerrado"):
            self.transport.command("STOP")

    async def test_short_contact_pulse_survives_release_and_clears_on_reconnect(self):
        from websockets.asyncio.client import connect

        async def frame(pico, event):
            await pico.send(encode({"v": 1, "type": "event", "source": "uart", "event": event}))
            await pico.recv()

        async def prepare(pico):
            await pico.send(encode({"v": 1, "type": "hello", "robot_id": "test_pico", "transport": "wifi"}))
            await pico.recv()
            await frame(pico, {"v": 1, "type": "ready", "board": "mega2560", "sensor": False,
                               "audio": False, "commands": True})
            await pico.send(encode({"v": 1, "type": "heartbeat", "id": 1, "command_ready": True}))
            await pico.recv()

        contact = {"v": 1, "type": "contact", "sensor": "fsr_a8", "pressed": False, "uptime_ms": 100}
        async with connect(self.robot_url, additional_headers={"Authorization": "Bearer " + self.token}, proxy=None) as pico:
            await prepare(pico)
            await frame(pico, contact)
            await asyncio.to_thread(self.transport._refresh)
            self.assertTrue(self.transport.supports_contact_response)
            self.assertIsNone(self.transport.snapshot()["touch"])
            await frame(pico, {**contact, "pressed": True, "uptime_ms": 150})
            await frame(pico, {**contact, "pressed": False, "uptime_ms": 200})
            await asyncio.to_thread(self.transport._refresh)
            state = self.transport.snapshot()
            self.assertFalse(state["contact"]["pressed"])
            self.assertIsNotNone(state["touch"])
            self.transport.touch_received_at -= 3
            self.assertIsNone(self.transport.snapshot()["touch"])
        await asyncio.to_thread(self.transport._refresh)
        self.assertIsNone(self.transport.snapshot()["touch"])
        async with connect(self.robot_url, additional_headers={"Authorization": "Bearer " + self.token}, proxy=None) as pico:
            await prepare(pico)
            await frame(pico, {**contact, "pressed": True})
            await asyncio.to_thread(self.transport._refresh)
            self.assertIsNone(self.transport.snapshot()["touch"])

    async def test_invalid_touch_metadata_and_timeout_clear_existing_pulse(self):
        status = {"v": 1, "type": "status", "robot_id": "test_pico", "connected": True, "transport": "wifi",
                  "mega_connected": True, "command_ready": True, "capabilities": {"commands": True}}
        for event in ({"id": "a", "age_ms": True}, {"id": "a", "age_ms": -1}, {"id": "a", "age_ms": 2001},
                      {"id": "a\n", "age_ms": 0}, {"id": "a", "age_ms": 0, "extra": "ignored"}):
            self.transport.state["touch"] = {"id": "prior_pulse", "age_ms": 0}
            self.transport.touch_received_at = time.monotonic()
            self.transport._request = lambda packet: (status if packet["type"] == "status" else
                {"v": 1, "type": "telemetry", "kind": packet["kind"], "event": event if packet["kind"] == "touch" else None})
            with self.subTest(event=event), self.assertRaises(RuntimeError):
                self.transport._refresh()
            self.assertIsNone(self.transport.snapshot()["touch"])
        self.transport.state["touch"] = {"id": "prior_pulse", "age_ms": 0}
        self.transport.touch_received_at = time.monotonic()
        def timeout(packet):
            raise RuntimeError("Local operator timeout")
        self.transport._request = timeout
        with self.assertRaises(RuntimeError):
            self.transport._refresh()
        self.assertIsNone(self.transport.snapshot()["touch"])

    async def test_snapshot_expires_when_reader_stalls_and_slow_refresh_cannot_restore_ready(self):
        status = {"v": 1, "type": "status", "robot_id": "test_pico", "connected": True, "transport": "wifi",
                  "mega_connected": True, "command_ready": True, "capabilities": {"commands": True},
                  "contact": {"sensor": "fsr_a8", "pressed": True}}
        self.transport._request = lambda packet: (status if packet["type"] == "status" else
            {"v": 1, "type": "telemetry", "kind": packet["kind"],
             "event": {"id": "recent_pulse", "age_ms": 10} if packet["kind"] == "touch" else None})
        self.transport._refresh()
        state = self.transport.snapshot()
        self.assertTrue(state["ready"])
        self.assertIsNotNone(state["touch"])
        self.transport.touch_received_at -= 1
        self.assertGreaterEqual(self.transport.snapshot()["touch"]["age_ms"], 1010)
        self.transport.last_refresh_at -= 7
        state = self.transport.snapshot()
        self.assertFalse(state["ready"])
        for key in ("capabilities", "contact", "measurement", "touch"):
            self.assertIsNone(state[key])
        with patch("respaw.network_robot.time.monotonic", side_effect=(100, 100, 100, 107)):
            self.transport._refresh()
        self.assertFalse(self.transport.snapshot()["ready"])
        self.assertIsNone(self.transport.snapshot()["touch"])

    async def test_cancelled_contact_ack_wait_exits_promptly_without_replay(self):
        from websockets.asyncio.client import connect

        cancelled = threading.Event()
        async with connect(self.robot_url, additional_headers={"Authorization": "Bearer " + self.token}, proxy=None) as pico:
            await pico.send(encode({"v": 1, "type": "hello", "robot_id": "test_pico", "transport": "wifi"}))
            await pico.recv()
            command = asyncio.create_task(asyncio.to_thread(self.transport.command, "FACE", "listening", cancelled.is_set))
            self.assertEqual(json.loads(await pico.recv())["argument"], "listening")
            started = time.monotonic()
            cancelled.set()
            with self.assertRaises(CommandCancelled):
                await asyncio.wait_for(command, 1)
            self.assertLess(time.monotonic() - started, 1)
            with self.assertRaises(asyncio.TimeoutError):
                await asyncio.wait_for(pico.recv(), 0.03)
