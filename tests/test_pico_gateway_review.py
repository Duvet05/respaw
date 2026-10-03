"""Boundary cases for the Pico's small, authenticated WebSocket transport."""

import base64
import asyncio
import hashlib
import importlib.util
import json
from pathlib import Path
import struct
import sys
import unittest
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pico/source/respaw-gateway"))
from respaw_gateway import config, portal
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
                    await client.send(encode({"v": 1, "type": "heartbeat", "id": 1}))
                    self.assertEqual(await receive(client), {"v": 1, "type": "heartbeat_ack", "id": 1})
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
