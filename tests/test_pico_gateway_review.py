"""Boundary cases for the Pico's small, authenticated WebSocket transport."""

import base64
import asyncio
import hashlib
import importlib.util
import json
from pathlib import Path
import struct
import sys
from types import SimpleNamespace
import unittest
from urllib.parse import urlsplit
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pico/source/respaw-gateway"))
sys.path.insert(0, str(ROOT / "pico/source/respaw-v2"))
from respaw_gateway import config, portal
from respaw_gateway.bridge import CommandBridge, MAX_PENDING
from respaw_gateway import bridge as bridge_module
from respaw_gateway.vendor.aiohttp_ws import WebSocketClient

HAS_WEBSOCKETS = importlib.util.find_spec("websockets") is not None


class Reader:
    def __init__(self, data):
        self.data = data
        self.requests = []

    async def readexactly(self, size):
        self.requests.append(size)
        if len(self.data) < size:
            raise EOFError
        result, self.data = self.data[:size], self.data[size:]
        return result

    async def read(self, size):
        return await self.readexactly(min(size, len(self.data)))

    async def readline(self):
        end = self.data.find(b"\n")
        return await self.readexactly(len(self.data) if end < 0 else end + 1)


class Writer:
    def __init__(self):
        self.data = bytearray()
        self.closed = False

    def write(self, data):
        self.data.extend(data)

    async def drain(self):
        pass

    def close(self):
        self.closed = True

    async def wait_closed(self):
        self.closed = True


def frame(opcode, payload, final=True):
    first = opcode | (0x80 if final else 0)
    if len(payload) < 126:
        return struct.pack("!BB", first, len(payload)) + payload
    return struct.pack("!BBH", first, 126, len(payload)) + payload


class ConfigBoundaryTests(unittest.TestCase):
    def config(self):
        return {"v": 1, "ssid": "", "password": "", "server_url": "wss://example.com/robot",
                "token": "t" * 32, "ap_password": "a" * 16}

    def test_version_boolean_and_trailing_newlines_are_rejected(self):
        value = self.config()
        for field, replacement in (("v", True), ("token", value["token"] + "\n"),
                                   ("ap_password", value["ap_password"] + "\n"),
                                   ("server_url", value["server_url"] + "\n")):
            with self.subTest(field=field), self.assertRaises(ValueError):
                config.validate({**value, field: replacement})

    def test_ap_password_minimum_and_explicit_uart_flag(self):
        self.assertTrue(config.validate({**self.config(), "ap_password": "12345678"}))
        for fields in ({"ap_password": "1234567"}, {"ap_password": "a" * 64}, {"uart_commands": 1}):
            with self.subTest(fields=fields), self.assertRaises(ValueError):
                config.validate({**self.config(), **fields})
        self.assertTrue(config.validate({**self.config(), "uart_commands": False}))


class BridgeBoundaryTests(unittest.TestCase):
    def make_bridge(self, enabled=True, commands=True):
        self.writes, self.tx_changes = [], []
        self.short_write = False

        def write(line):
            self.writes.append(line)
            return len(line) - 1 if self.short_write else len(line)

        bridge = CommandBridge(write, self.tx_changes.append, enabled)
        bridge.connect()
        bridge.observe({"v": 1, "type": "ready", "board": "mega2560", "sensor": True,
                        "audio": False, "commands": commands}, 0)
        bridge.service(0)
        return bridge

    def own(self, bridge, now=1):
        bridge.observe({"v": 1, "type": "ack", "id": bridge.local_pending[0], "command": "PING"}, now)
        self.assertTrue(bridge.owned)

    def face(self, ident=500):
        return {"v": 1, "type": "action", "id": ident, "command": "FACE", "argument": "warm"}

    def test_tx_requires_explicit_flag_and_actual_capability(self):
        for enabled, commands in ((False, True), (True, False)):
            with self.subTest(enabled=enabled, commands=commands):
                bridge = self.make_bridge(enabled, commands)
                self.assertEqual(self.writes, [])
                self.assertFalse(bridge.tx_enabled)
                self.assertFalse(bridge.command_ready(1))
                self.assertEqual(bridge.submit(self.face(), 1)["type"], "action_error")

    def test_only_matching_real_mega_ack_is_terminal_and_ids_are_mapped(self):
        bridge = self.make_bridge()
        self.own(bridge)
        forwarded = bridge.submit(self.face(32768), 2)
        self.assertEqual(forwarded["stage"], "forwarded")
        self.assertEqual(forwarded["uart_line"], "V1 32768 FACE warm\n")
        self.assertEqual(self.writes[-1], b"V1 1 FACE warm\n")
        self.assertEqual(bridge.observe({"v": 1, "type": "ack", "id": 32768, "command": "PING"}, 3), [])
        accepted = bridge.observe({"v": 1, "type": "ack", "id": 1, "command": "FACE"}, 4)
        self.assertEqual(accepted, [{**forwarded, "stage": "mega_accepted"}])
        self.assertEqual(bridge.observe({"v": 1, "type": "ack", "id": 1, "command": "FACE"}, 5), [])

    def test_wrong_command_cannot_accept_action(self):
        bridge = self.make_bridge()
        self.own(bridge)
        bridge.submit(self.face(), 2)
        result = bridge.observe({"v": 1, "type": "ack", "id": 1, "command": "STOP"}, 3)
        self.assertEqual(result[0]["reason"], "bad_frame")

    def test_partial_uart_write_never_announces_forwarded_and_never_retries_action(self):
        bridge = self.make_bridge()
        self.own(bridge)
        self.short_write = True
        result = bridge.submit(self.face(), 2)
        self.assertEqual(result["reason"], "uart_write_failed")
        self.assertNotIn("stage", result)
        self.assertFalse(bridge.tx_enabled)
        count = len(self.writes)
        bridge.service(1000)
        self.assertEqual(len(self.writes), count)

    def test_pending_bound_and_timeout_do_not_replay_actions(self):
        bridge = self.make_bridge()
        self.own(bridge)
        for ident in range(100, 100 + MAX_PENDING - 1):
            self.assertEqual(bridge.submit(self.face(ident), 2)["stage"], "forwarded")
        self.assertEqual(bridge.submit(self.face(999), 2)["reason"], "too_many_pending")
        stop = {"v": 1, "type": "action", "id": 999, "command": "STOP"}
        self.assertEqual(bridge.submit(stop, 2)["stage"], "forwarded")
        self.assertEqual(len(bridge.pending), MAX_PENDING)
        results = bridge.service(3002)
        self.assertEqual(len(results), MAX_PENDING)
        self.assertTrue(all(result["reason"] == "ack_timeout" for result in results))
        self.assertEqual(sum(b" FACE " in line for line in self.writes), MAX_PENDING - 1)
        self.assertEqual(sum(b" STOP\n" in line for line in self.writes), 1)
        self.assertEqual(bridge.observe({"v": 1, "type": "ack", "id": 1, "command": "FACE"}, 3003), [])

    def test_disconnect_stops_releases_tx_and_late_ack_cannot_match_reconnect(self):
        bridge = self.make_bridge()
        self.own(bridge)
        bridge.submit(self.face(500), 2)
        bridge.disconnect(3)
        self.assertTrue(self.writes[-1].endswith(b" STOP\n"))
        self.assertFalse(bridge.tx_enabled)
        self.assertEqual(bridge.pending, {})
        bridge.connect()
        bridge.service(4)
        self.own(bridge, 5)
        bridge.submit(self.face(501), 6)
        self.assertEqual(self.writes[-1], b"V1 2 FACE warm\n")
        self.assertEqual(bridge.observe({"v": 1, "type": "ack", "id": 1, "command": "FACE"}, 7), [])
        self.assertEqual(bridge.observe({"v": 1, "type": "ack", "id": 2, "command": "FACE"}, 8)[0]["id"], 501)

    def test_reboot_invalidates_pending_and_controller_busy_is_truthful(self):
        bridge = self.make_bridge()
        local_id = bridge.local_pending[0]
        bridge.observe({"v": 1, "type": "error", "id": local_id, "reason": "controller_busy"}, 1)
        self.assertEqual(bridge.submit(self.face(), 2)["reason"], "controller_busy")
        bridge.service(2000)
        self.own(bridge, 2001)
        bridge.submit(self.face(), 2002)
        result = bridge.observe({"v": 1, "type": "ready", "board": "mega2560", "sensor": True,
                                 "audio": False, "commands": True}, 2003)
        self.assertEqual(result[0]["reason"], "mega_unavailable")
        self.assertFalse(bridge.tx_enabled)
        self.assertFalse(bridge.owned)

    def test_correlated_ping_keeps_capability_live_during_silent_measurement(self):
        bridge = self.make_bridge()
        self.assertFalse(bridge.command_ready(0))
        self.own(bridge)
        self.assertTrue(bridge.command_ready(1))
        for now in range(2000, 32000, 2000):
            bridge.service(now)
            self.own(bridge, now + 1)
        self.assertEqual(bridge.submit(self.face(), 32000)["stage"], "forwarded")
        bridge.service(38002)
        self.assertFalse(bridge.tx_enabled)
        self.assertFalse(bridge.command_ready(38002))

    def test_ticks_wrap_does_not_expire_a_recent_action(self):
        period = 1 << 30

        def wrapped_diff(now, before):
            return ((now - before + period // 2) % period) - period // 2

        with patch.object(bridge_module, "ticks_diff", wrapped_diff):
            writes = []
            bridge = CommandBridge(lambda line: writes.append(line) or len(line), lambda enabled: None, True)
            bridge.connect()
            bridge.observe({"type": "ready", "commands": True}, period - 1000)
            bridge.service(period - 1000)
            bridge.observe({"type": "ack", "id": bridge.local_pending[0], "command": "PING"}, period - 999)
            self.assertEqual(bridge.submit(self.face(), period - 500)["stage"], "forwarded")
            self.assertEqual(bridge.service(500), [])
            self.assertEqual(bridge.observe({"type": "ack", "id": 1, "command": "FACE"}, 1000)[0]["stage"], "mega_accepted")


class GatewayDriverTests(unittest.TestCase):
    def test_tx_mux_changes_preserve_partial_rx_contact(self):
        calls = []

        def pin(*args, **kwargs):
            calls.append((args, kwargs))

        pin.ALT, pin.ALT_UART, pin.IN = 7, 2, 0
        stubs = {"machine": SimpleNamespace(Pin=pin), "network": SimpleNamespace(),
                 "receiver": SimpleNamespace(open_uart=None)}
        source = ROOT / "pico/source/respaw-gateway/respaw_gateway/gateway.py"
        spec = importlib.util.spec_from_file_location("respaw_gateway.gateway_driver_test", source)
        module = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, stubs):
            spec.loader.exec_module(module)
        module.emit = lambda *args, **kwargs: None
        gateway = module.Gateway.__new__(module.Gateway)
        partial_contact = bytearray(b'{"v":1,"type":"contact",')
        gateway.receiver = SimpleNamespace(buffer=partial_contact, discard=False)
        gateway.uart = SimpleNamespace(init=lambda **kwargs: self.fail("TX mux must not reset the RX UART"))
        gateway.set_uart_tx(True)
        gateway.set_uart_tx(False)
        self.assertIs(gateway.receiver.buffer, partial_contact)
        self.assertEqual(calls, [((0, pin.ALT), {"alt": pin.ALT_UART}), ((0, pin.IN), {})])


class FrameBoundaryTests(unittest.IsolatedAsyncioTestCase):
    def client(self, data):
        client = WebSocketClient({})
        client.reader = Reader(data)
        client.writer = Writer()
        return client

    async def test_oversize_extended_lengths_are_rejected_before_payload_read(self):
        for raw in (struct.pack("!BBH", 0x81, 126, 513),
                    struct.pack("!BBQ", 0x81, 127, 2 ** 32)):
            client = self.client(raw)
            with self.subTest(raw=raw), self.assertRaises((ValueError, OSError)):
                await client.receive()
            self.assertLessEqual(max(client.reader.requests), 8)

    async def test_fragmented_masked_reserved_and_oversize_control_frames(self):
        invalid = (frame(1, b"a", final=False), b"\x81\x81", b"\xc1\x00",
                   frame(0, b"continuation"), frame(3, b"unknown"), frame(9, b"x" * 126))
        for raw in invalid:
            client = self.client(raw)
            with self.subTest(raw=raw[:4]), self.assertRaises((ValueError, OSError)):
                await client.receive()

    async def test_ping_is_answered_with_masked_pong_before_next_text(self):
        client = self.client(frame(9, b"alive") + frame(1, b'{"v":1}'))
        opcode, text = await client.receive()
        self.assertEqual((opcode, text), (1, '{"v":1}'))
        outgoing = client.writer.data
        self.assertEqual(outgoing[:2], bytes((0x8A, 0x80 | 5)))
        mask = outgoing[2:6]
        self.assertEqual(bytes(value ^ mask[index % 4] for index, value in enumerate(outgoing[6:])), b"alive")

    async def test_largest_supported_text_and_close_are_processed(self):
        self.assertEqual(await self.client(frame(1, b"x" * 512)).receive(), (1, "x" * 512))
        client = self.client(frame(8, b""))
        self.assertEqual(await client.receive(), (8, b""))
        self.assertTrue(client.closed)


class HandshakeBoundaryTests(unittest.IsolatedAsyncioTestCase):
    async def handshake(self, mutate=None):
        client = WebSocketClient({"Authorization": "Bearer " + "t" * 32})
        observed = {}

        async def request(method, url, **kwargs):
            observed.update(method=method, url=url, **kwargs)
            key = kwargs["headers"]["Sec-WebSocket-Key"]
            accept = base64.b64encode(hashlib.sha1((key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode()).digest())
            response = (b"HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\n"
                        b"Connection: Upgrade\r\nSec-WebSocket-Accept: " + accept + b"\r\n\r\n")
            return Reader(mutate(response) if mutate else response), Writer()

        context = object()
        await client.connect("wss://example.com/robot", ssl=context, handshake_request=request)
        return client, observed, context

    async def test_valid_handshake_keeps_tls_context_and_omits_browser_origin(self):
        client, observed, context = await self.handshake()
        self.assertIs(observed["ssl"], context)
        self.assertNotIn("Origin", observed["headers"])
        self.assertIsNotNone(client.reader)

    async def test_header_case_and_connection_token_lists_are_supported(self):
        await self.handshake(lambda data: data.replace(b"Upgrade: websocket", b"uPgRaDe: WebSocket")
                             .replace(b"Connection: Upgrade", b"connection: keep-alive, UpGrAdE"))

    async def test_wrong_accept_status_or_upgrade_is_rejected(self):
        mutations = (
            lambda data: data.replace(b"Sec-WebSocket-Accept: ", b"Sec-WebSocket-Accept: WRONG"),
            lambda data: data.replace(b"101 Switching Protocols", b"401 Unauthorized"),
            lambda data: data.replace(b"Upgrade: websocket", b"Upgrade: h2c"),
            lambda data: data.replace(b"Connection: Upgrade", b"Connection: close"),
            lambda data: data.replace(b"\r\n\r\n", b"\r\nSec-WebSocket-Accept: duplicate\r\n\r\n"),
            lambda data: data.replace(b"\r\n\r\n", b"\r\nSec-WebSocket-Extensions: permessage-deflate\r\n\r\n"),
        )
        for mutate in mutations:
            with self.subTest(mutate=mutate), self.assertRaises((ValueError, OSError)):
                await self.handshake(mutate)

    async def test_oversize_header_line_is_rejected(self):
        with self.assertRaises((ValueError, OSError)):
            await self.handshake(lambda data: data[:-2] + b"X-Large: " + b"x" * 8192 + b"\r\n\r\n")

    async def test_total_header_size_is_bounded_even_with_short_lines(self):
        extra = b"".join(("X-%d: " % index).encode() + b"x" * 900 + b"\r\n" for index in range(10))
        with self.assertRaises((ValueError, OSError)):
            await self.handshake(lambda data: data[:-2] + extra + b"\r\n")


class PortalBoundaryTests(unittest.IsolatedAsyncioTestCase):
    async def test_ap_accepts_supported_cyw43_security_instead_of_generic_enum(self):
        class WLAN:
            IF_AP = 1
            # Exact public constant in RP2 MicroPython v1.26.1 / CYW43.
            SEC_WPA_WPA2 = 0x00400006
            SEC_OPEN, SEC_WPA3, SEC_WPA2_WPA3 = 0, 0x01000004, 0x01400004

            def __init__(self, interface):
                self.assert_ap = interface == self.IF_AP
                self.enabled = True  # AP can survive a soft reset.

            def active(self, value):
                self.enabled = value

            def config(self, **values):
                if values["security"] not in (self.SEC_OPEN, self.SEC_WPA_WPA2,
                                               self.SEC_WPA3, self.SEC_WPA2_WPA3):
                    raise ValueError("Unsupported CYW43 security")
                self.settings = values

            def ifconfig(self, values):
                self.addresses = values

        with self.assertRaises(ValueError):
            WLAN(WLAN.IF_AP).config(security=3)

        setup = portal.Portal.__new__(portal.Portal)
        setup.server, setup.ap = None, None
        setup.ssid = "ResPaw-Setup-TEST"
        setup.value = {"ap_password": "fixturepass"}
        server = SimpleNamespace()

        async def start_server(callback, address, port, backlog):
            self.assertEqual((address, port, backlog), (portal.AP_ADDRESS, 80, 2))
            self.assertTrue(setup.ap.enabled)
            return server

        with patch.dict(sys.modules, {"network": SimpleNamespace(WLAN=WLAN)}), \
                patch.object(portal.asyncio, "start_server", start_server), patch("builtins.print"):
            await setup.start()
            await setup.start()  # Already running: preserve the existing socket.
        self.assertTrue(setup.ap.assert_ap)
        self.assertIs(setup.server, server)
        self.assertEqual(setup.ap.settings,
                         {"ssid": setup.ssid, "security": WLAN.SEC_WPA_WPA2, "key": "fixturepass"})
        self.assertEqual(setup.ap.addresses[0], portal.AP_ADDRESS)

    async def test_oversize_body_and_foreign_origins_are_rejected_before_reading_body(self):
        for headers in (b"Content-Length: 2049\r\n", b"Content-Length: 65536\r\n",
                        b"Origin: https://foreign.example\r\n", b"Host: duplicate\r\n"):
            reader = Reader(b"POST /save HTTP/1.1\r\nHost: 192.168.4.1\r\n" + headers + b"\r\n")
            with self.subTest(headers=headers), self.assertRaises(ValueError):
                await portal.read_request(reader)
            self.assertEqual(max(reader.requests), 1)

    def test_phone_form_decodes_utf8_without_duplicate_fields(self):
        self.assertEqual(portal.form_values(b"ssid=Ni%C3%B1o+WiFi&password=secret%26%3D"),
                         {"ssid": "Niño WiFi", "password": "secret&="})
        for body in (b"ssid=first&ssid=second", b"ssid=%FF", b"ssid=%", b"unknown=field"):
            with self.subTest(body=body), self.assertRaises((ValueError, UnicodeError)):
                portal.form_values(body)

    def test_setup_page_escapes_ssid_and_keeps_persisted_secrets_hidden(self):
        value = {"v": 1, "ssid": '"><script>bad</script>', "password": "PRIVATE_WIFI_SECRET",
                 "server_url": "wss://example.com/robot", "token": "PRIVATE_ROBOT_TOKEN" * 2,
                 "ap_password": "PRIVATE_AP_SECRET"}
        setup = portal.Portal.__new__(portal.Portal)
        setup.value, setup.nonce = value, "public_nonce"
        page = setup.page()
        self.assertNotIn(value["ssid"], page)
        for key in ("password", "token", "ap_password"):
            self.assertNotIn(value[key], page)


@unittest.skipUnless(HAS_WEBSOCKETS, "Install requirements-link.txt for native-client integration")
class NativeClientIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_native_client_authenticates_exchanges_actions_and_reconnects(self):
        sys.path.insert(0, str(ROOT / "tools"))
        from robot_link_server import LinkServer, encode

        token = "t" * 32
        probe = LinkServer(token, "test_pico", emit=lambda event: None)
        server = await probe.start(port=0)
        uri = "ws://127.0.0.1:%d/robot" % server.sockets[0].getsockname()[1]

        async def request(method, url, **kwargs):
            address = urlsplit(url)
            reader, writer = await asyncio.open_connection(address.hostname, address.port)
            headers = "\r\n".join("%s: %s" % item for item in kwargs["headers"].items())
            writer.write(("GET %s HTTP/1.1\r\n%s\r\n\r\n" % (address.path, headers)).encode())
            await writer.drain()
            return reader, writer

        async def receive(client):
            opcode, raw = await asyncio.wait_for(client.receive(), 2)
            self.assertEqual(opcode, client.TEXT)
            return json.loads(raw)

        action_ids = []
        try:
            for _ in range(2):
                client = WebSocketClient({"Authorization": "Bearer " + token})
                try:
                    await client.connect(uri, handshake_request=request)
                    await client.send(encode({"v": 1, "type": "hello", "robot_id": "test_pico", "transport": "wifi"}))
                    self.assertEqual(await receive(client), {"v": 1, "type": "welcome", "robot_id": "test_pico"})
                    await client.send(encode({"v": 1, "type": "heartbeat", "id": 1, "command_ready": False}))
                    self.assertEqual(await receive(client), {"v": 1, "type": "heartbeat_ack", "id": 1})
                    self.assertFalse(probe.status()["command_ready"])
                    result = await probe.send_action("FACE", "warm")
                    action = await receive(client)
                    action_ids.append(action["id"])
                    await client.send(encode({"v": 1, "type": "action_error", "id": action["id"], "reason": "mega_unavailable"}))
                    self.assertEqual(await receive(client), {"v": 1, "type": "action_error_received", "id": action["id"], "reason": "mega_unavailable"})
                    self.assertEqual((await asyncio.wait_for(result, 2))["stage"], "error")
                finally:
                    await client.close()
                for _ in range(100):
                    if probe.session is None:
                        break
                    await asyncio.sleep(0.01)
                self.assertIsNone(probe.session)
            self.assertEqual(len(set(action_ids)), 2)
        finally:
            server.close()
            await server.wait_closed()


if __name__ == "__main__":
    unittest.main()
