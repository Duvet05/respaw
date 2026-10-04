"""Native verified-TLS robot link, with explicitly enabled correlated UART."""

import asyncio
import binascii
import gc
import json
import machine
import network
import ssl
import time

from link_protocol import MAX_PACKET, action_line, decode_packet
from receiver import open_uart
from telemetry import Receiver
from . import config
from .portal import Portal
from .bridge import ACTION_ERROR_REASONS, CommandBridge
from .vendor import ntptime
from .vendor.aiohttp_ws import WebSocketClient, urlparse

CA_PATH = "respaw_gateway/ca.der"
WIFI_TIMEOUT_MS = 20000
HEARTBEAT_SECONDS = 5
ACK_TIMEOUT_MS = 20000
MAX_EVENTS = 4
MAX_ACTION_RESULTS = 16


def emit(kind, **values):
    packet = {"v": 1, "type": kind}
    packet.update(values)
    print(json.dumps(packet))


def tls_context():
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.verify_mode = ssl.CERT_REQUIRED
    with open(CA_PATH, "rb") as stream:
        ca = stream.read(4097)
    if not 100 <= len(ca) <= 4096:
        raise ValueError("bad_ca_file")
    context.load_verify_locations(cadata=ca)
    if context.verify_mode != ssl.CERT_REQUIRED:
        raise ValueError("certificate_verification_unavailable")
    return context


def clock_valid():
    return 2026 <= time.gmtime()[0] <= 2100


def synchronize_clock():
    ntptime.timeout = 2
    for host in ("time.cloudflare.com", "pool.ntp.org"):
        try:
            ntptime.host = host
            ntptime.settime()
            if clock_valid():
                emit("gateway_clock", source="ntp")
                return
        except (OSError, ValueError):
            pass
    if not clock_valid():
        raise OSError("clock_unavailable")
    emit("gateway_clock", source="rtc")


class Gateway:
    def __init__(self, value):
        self.value = value
        self.robot_id = binascii.hexlify(machine.unique_id()).decode()
        self.portal = Portal(value)
        self.sta = network.WLAN(network.WLAN.IF_STA)
        self.uart = open_uart()
        self.receiver = Receiver()
        self.led = machine.Pin("LED", machine.Pin.OUT, value=0)
        self.events = []
        self.connected = False
        self.last_ack = 0
        self.sent_id = None
        self.pending_heartbeats = []
        self.was_connected = False
        self.write_lock = asyncio.Lock()
        self.actions_outbox = []
        self.command_link_failed = False
        self.bridge = CommandBridge(self.write_uart, self.set_uart_tx, value.get("uart_commands", False))

    def set_uart_tx(self, enabled):
        if enabled:
            # Enabled only after explicit wiring configuration AND actual
            # commands:true from the Mega. The original receiver stays RX.
            # Change only the TX mux. Reinitializing UART would discard RX
            # bytes, including an initial contact event following ready.
            machine.Pin(0, machine.Pin.ALT, alt=machine.Pin.ALT_UART)
        else:
            machine.Pin(0, machine.Pin.IN)
        emit("gateway_uart_tx", enabled=enabled)

    def write_uart(self, line):
        written = self.uart.write(line)
        if written == len(line):
            self.uart.flush()
        return written

    def queue_action_results(self, results):
        if not self.connected:
            return
        if len(self.actions_outbox) + len(results) > MAX_ACTION_RESULTS:
            self.command_link_failed = True
            self.bridge.disconnect(time.ticks_ms())
            return
        self.actions_outbox.extend(results)

    async def receive_uart(self):
        while True:
            now = time.ticks_ms()
            if self.uart.any():
                data = self.uart.read(min(self.uart.any(), 128))
                if data:
                    for event in self.receiver.feed(data, now):
                        emit("pico_rx", event=event)
                        self.queue_action_results(self.bridge.observe(event, now))
                        # Legacy measurements lack v1 quality evidence and
                        # cannot be represented as native v1 server telemetry.
                        if self.connected and event.get("protocol") != "legacy":
                            if len(self.events) >= MAX_EVENTS:
                                self.events.pop(0)
                            self.events.append((now, event))
            self.queue_action_results(self.bridge.service(now))
            self.led.value(1 if self.connected else int(now % 2000 < 100))
            await asyncio.sleep_ms(20)

    async def wifi(self):
        if self.sta.isconnected():
            return
        self.sta.active(True)
        self.sta.connect(self.value["ssid"], self.value["password"])
        started = time.ticks_ms()
        while not self.sta.isconnected():
            if time.ticks_diff(time.ticks_ms(), started) >= WIFI_TIMEOUT_MS:
                # RP2 otherwise retries forever even with the wrong password.
                self.sta.disconnect()
                self.sta.active(False)
                raise OSError("wifi_timeout")
            await asyncio.sleep_ms(250)
        emit("gateway_wifi", connected=True)

    async def request_handshake(self, method, url, ssl, headers, is_handshake, version):
        uri = urlparse(self.value["server_url"])
        reader = writer = None
        try:
            reader, writer = await asyncio.open_connection(uri.hostname, uri.port,
                                                           ssl=ssl, server_hostname=uri.hostname)
            query = ("GET %s HTTP/1.1\r\n%s\r\n\r\n" % (
                uri.path, "\r\n".join("%s: %s" % (name, value) for name, value in headers.items())))
            writer.write(query.encode("ascii"))
            await writer.drain()
            return reader, writer
        except BaseException:
            if writer is not None:
                writer.close()
                await writer.wait_closed()
            raise

    async def send(self, websocket, packet):
        raw = json.dumps(packet, separators=(",", ":"))
        if not 1 <= len(raw.encode("utf-8")) <= MAX_PACKET:
            raise ValueError("packet_too_large")
        async with self.write_lock:
            await asyncio.wait_for(websocket.send(raw), 10)

    async def receive_network(self, websocket):
        while True:
            opcode, raw = await asyncio.wait_for(websocket.receive(), 30)
            if opcode != websocket.TEXT:
                raise ValueError("non_text_packet")
            packet = decode_packet(raw)
            kind = packet.get("type")
            if kind == "action":
                action_line(packet)
                if len(self.actions_outbox) >= MAX_ACTION_RESULTS // 2 and packet["command"] != "STOP":
                    response = {"v": 1, "type": "action_error", "id": packet["id"],
                                "reason": "too_many_pending"}
                else:
                    response = self.bridge.submit(packet, time.ticks_ms())
                await self.send(websocket, response)
                emit("gateway_action", id=packet["id"], stage=response.get("stage", "error"))
            elif kind == "heartbeat_ack":
                if (set(packet) != {"v", "type", "id"} or type(packet["id"]) is not int
                        or packet["id"] not in self.pending_heartbeats):
                    raise ValueError("bad_heartbeat_ack")
                self.pending_heartbeats = self.pending_heartbeats[self.pending_heartbeats.index(packet["id"]) + 1:]
                self.last_ack = time.ticks_ms()
            elif kind == "event_received":
                if (set(packet) != {"v", "type", "source", "event_type"} or packet["source"] != "uart"
                        or packet["event_type"] not in ("ready", "heartbeat", "measurement", "contact", "ack", "error")):
                    raise ValueError("bad_event_ack")
            elif kind == "action_error_received":
                if (set(packet) != {"v", "type", "id", "reason"}
                        or type(packet["id"]) is not int or not 1 <= packet["id"] <= 65535
                        or packet["reason"] not in ACTION_ERROR_REASONS):
                    raise ValueError("bad_action_error_ack")
            elif kind == "ack_received":
                if (set(packet) != {"v", "type", "id", "stage"}
                        or type(packet["id"]) is not int or not 1 <= packet["id"] <= 65535
                        or packet["stage"] not in ("forwarded", "mega_accepted")):
                    raise ValueError("bad_action_ack")
            else:
                raise ValueError("bad_server_type")

    async def heartbeat(self, websocket):
        ident = 0
        self.last_ack = time.ticks_ms()
        while True:
            if (not self.sta.isconnected() or self.command_link_failed
                    or time.ticks_diff(time.ticks_ms(), self.last_ack) > ACK_TIMEOUT_MS):
                raise OSError("link_timeout")
            ident = ident % 65535 + 1
            self.sent_id = ident
            self.pending_heartbeats.append(ident)
            if len(self.pending_heartbeats) > 4:
                raise OSError("heartbeat_backlog")
            await self.send(websocket, {"v": 1, "type": "heartbeat", "id": ident,
                                        "command_ready": self.bridge.command_ready(time.ticks_ms())})
            await asyncio.sleep(HEARTBEAT_SECONDS)

    async def forward_events(self, websocket):
        while True:
            if self.actions_outbox:
                await self.send(websocket, self.actions_outbox.pop(0))
            elif self.events:
                received_at, event = self.events.pop(0)
                if time.ticks_diff(time.ticks_ms(), received_at) < 10000:
                    await self.send(websocket, {"v": 1, "type": "event", "source": "uart", "event": event})
            await asyncio.sleep_ms(50)

    async def session(self):
        websocket = WebSocketClient({"Authorization": "Bearer " + self.value["token"]})
        tasks = []
        self.was_connected = False
        self.pending_heartbeats = []
        self.command_link_failed = False
        gc.collect()
        try:
            context = tls_context()
            await asyncio.wait_for(websocket.connect(self.value["server_url"], ssl=context,
                                                     handshake_request=self.request_handshake), 25)
            await self.send(websocket, {"v": 1, "type": "hello", "robot_id": self.robot_id, "transport": "wifi"})
            opcode, raw = await asyncio.wait_for(websocket.receive(), 10)
            if opcode != websocket.TEXT or decode_packet(raw) != {"v": 1, "type": "welcome", "robot_id": self.robot_id}:
                raise ValueError("bad_welcome")
            self.connected = True
            self.bridge.connect()
            self.was_connected = True
            self.events = []
            self.actions_outbox = []
            await self.portal.stop()
            emit("gateway_connected", robot_id=self.robot_id, transport="wifi", tls_verified=True,
                 uart_commands_enabled=self.bridge.enabled, mega_commands=self.bridge.owned)
            tasks = [asyncio.create_task(self.receive_network(websocket)),
                     asyncio.create_task(self.heartbeat(websocket)),
                     asyncio.create_task(self.forward_events(websocket))]
            await asyncio.gather(*tasks)
        finally:
            self.connected = False
            self.bridge.disconnect(time.ticks_ms())
            self.events = []
            self.actions_outbox = []
            for task in tasks:
                task.cancel()
            for task in tasks:
                try:
                    await task
                except (asyncio.CancelledError, OSError, ValueError, EOFError, asyncio.TimeoutError):
                    pass
            try:
                await asyncio.wait_for(websocket.close(), 3)
            except (OSError, asyncio.TimeoutError):
                pass
            gc.collect()

    async def run(self):
        uart_task = asyncio.create_task(self.receive_uart())
        failures = 0
        backoff = 2
        try:
            emit("gateway_ready", robot_id=self.robot_id, uart_commands_enabled=self.bridge.enabled,
                 mega_commands=False)
            if not self.value["ssid"]:
                await self.portal.start()
                while True:
                    await asyncio.sleep(1)
            while True:
                try:
                    await self.wifi()
                    synchronize_clock()
                    await self.session()
                except (OSError, ValueError, EOFError, MemoryError, asyncio.TimeoutError) as error:
                    # Error objects can contain secrets in upstream libraries;
                    # publish only their class, never repr/error text.
                    if self.was_connected:
                        failures = 0
                        backoff = 2
                        self.was_connected = False
                    emit("gateway_retry", error=type(error).__name__, delay_seconds=backoff)
                    failures += 1
                    if failures >= 3:
                        await self.portal.start()
                    await asyncio.sleep(backoff)
                    backoff = min(backoff * 2, 60)
        finally:
            uart_task.cancel()
            try:
                await uart_task
            except asyncio.CancelledError:
                pass
            await self.portal.stop()
            self.uart.deinit()
            machine.Pin(0, machine.Pin.IN)
            machine.Pin(1, machine.Pin.IN, machine.Pin.PULL_UP)
            self.led.off()


def run():
    try:
        value = config.load()
    except (OSError, ValueError, KeyError, TypeError):
        emit("gateway_config_unavailable")
        from receiver import run as receive_only
        receive_only()
        return
    asyncio.run(Gateway(value).run())
