"""Bounded, correlated Mega command bridge, with no automatic action retries."""

from link_protocol import action_line
from telemetry import UART_ERROR_REASONS, ticks_diff

MAX_PENDING = 8
MEGA_TIMEOUT_MS = 6000
COMMAND_TIMEOUT_MS = 3000
PING_INTERVAL_MS = 2000
PING_TIMEOUT_MS = 1500
ACTION_ERROR_REASONS = UART_ERROR_REASONS + ("mega_unavailable", "unsupported", "ack_timeout",
                                           "uart_write_failed", "too_many_pending", "bad_frame")


def error_packet(ident, reason):
    return {"v": 1, "type": "action_error", "id": ident, "reason": reason}


class CommandBridge:
    def __init__(self, write, set_tx, enabled=False):
        if type(enabled) is not bool:
            raise ValueError("bad_uart_commands_flag")
        self.write = write
        self.set_tx = set_tx
        self.enabled = enabled
        self.connected = False
        self.capable = False
        self.last_live = None
        self.last_uptime = None
        self.tx_enabled = False
        self.owned = False
        self.controller_error = "controller_required"
        self.pending = {}
        self.local_pending = None
        self.last_ping = None
        self.cooldown = None
        # Server IDs and local UART IDs are independent. Reserved upper-half
        # IDs identify local keepalive/stop; neither sequence resets on WSS
        # reconnect, so a late previous-session ACK cannot match a new action.
        self.next_action = 1
        self.next_local = 32768

    def _tx(self, enabled):
        if enabled != self.tx_enabled:
            self.set_tx(enabled)
            self.tx_enabled = enabled

    def _available(self, now):
        age = None if self.last_live is None else ticks_diff(now, self.last_live)
        cooling = self.cooldown is not None and ticks_diff(now, self.cooldown) < MEGA_TIMEOUT_MS
        return (self.enabled and self.connected and self.capable and age is not None
                and 0 <= age < MEGA_TIMEOUT_MS and not cooling)

    def _reason(self):
        if self.enabled and self.connected and not self.capable:
            return "unsupported"
        return "mega_unavailable"

    def command_ready(self, now):
        return bool(self._available(now) and self.owned and self.tx_enabled)

    def _fail_all(self, reason):
        result = [error_packet(entry["server_id"], reason) for entry in self.pending.values()]
        self.pending = {}
        return result

    def _local_id(self):
        ident = self.next_local
        self.next_local = 32768 + (ident - 32768 + 1) % 32768
        return ident

    def _action_id(self):
        for _ in range(MAX_PENDING + 1):
            ident = self.next_action
            self.next_action = ident % 32767 + 1
            if ident not in self.pending:
                return ident
        raise ValueError("uart_id_exhausted")

    def _write(self, line, now):
        try:
            count = self.write(line)
            if type(count) is not int or count != len(line):
                raise OSError("uart_partial_write")
        except OSError:
            self.cooldown = now
            self.owned = False
            self.local_pending = None
            self._tx(False)
            return False
        return True

    def connect(self):
        self.connected = True
        self.pending = {}
        self.local_pending = None
        self.owned = False
        self.last_ping = None
        self.controller_error = "controller_required"

    def disconnect(self, now):
        # This one best-effort STOP is permitted only on an already enabled
        # link. No action is replayed, and no acceptance is invented for STOP.
        if self.tx_enabled:
            self._write(("V1 %d STOP\n" % self._local_id()).encode("ascii"), now)
        self.connected = False
        self.pending = {}
        self.local_pending = None
        self.owned = False
        self._tx(False)

    def observe(self, event, now):
        kind = event["type"]
        result = []
        if kind in ("ready", "heartbeat"):
            restarted = kind == "ready" or (self.last_uptime is not None and kind == "heartbeat"
                         and ((event["uptime_ms"] - self.last_uptime) & 0xFFFFFFFF) >= 0x80000000)
            if restarted:
                result = self._fail_all("mega_unavailable")
                self.owned = False
                self.local_pending = None
                self.last_ping = None
                self.last_uptime = None
                self._tx(False)
            self.capable = event.get("commands") is True
            self.last_live = now
            if kind == "heartbeat":
                self.last_uptime = event["uptime_ms"]
            if not self.capable:
                result.extend(self._fail_all("unsupported"))
                self.owned = False
                self.local_pending = None
                self._tx(False)
            return result
        if kind not in ("ack", "error"):
            return result
        ident = event["id"]
        if kind == "error" and ident == 0 and event["reason"] == "host_timeout":
            self.owned = False
            self.local_pending = None
            self.controller_error = "controller_required"
            return self._fail_all("host_timeout")
        if self.local_pending is not None and ident == self.local_pending[0]:
            if kind == "ack" and event["command"] == "PING":
                self.owned = True
                self.controller_error = "controller_required"
                self.last_live = now
            else:
                self.owned = False
                self.controller_error = event.get("reason", "bad_frame")
                if kind == "error":
                    self.last_live = now
            self.local_pending = None
            return result
        entry = self.pending.pop(ident, None)
        if entry is None:
            return result
        if kind == "error":
            if event["reason"] in ("controller_busy", "controller_required"):
                self.owned = False
                self.controller_error = event["reason"]
            self.last_live = now
            return [error_packet(entry["server_id"], event["reason"])]
        if event["command"] != entry["command"]:
            return [error_packet(entry["server_id"], "bad_frame")]
        self.last_live = now
        if event["command"] == "PING":
            self.owned = True
        return [{"v": 1, "type": "ack", "id": entry["server_id"], "stage": "mega_accepted",
                 "uart_line": entry["original_line"]}]

    def submit(self, action, now):
        original_line = action_line(action).decode("ascii")
        ident = action["id"]
        if any(entry["server_id"] == ident for entry in self.pending.values()):
            raise ValueError("duplicate_server_id")
        if not self._available(now) or not self.tx_enabled:
            return error_packet(ident, self._reason())
        limit = MAX_PENDING if action["command"] == "STOP" else MAX_PENDING - 1
        if len(self.pending) >= limit:
            return error_packet(ident, "too_many_pending")
        if action["command"] not in ("PING", "STOP") and not self.owned:
            reason = "controller_busy" if self.controller_error == "controller_busy" else "controller_required"
            return error_packet(ident, reason)
        uart_id = self._action_id()
        mapped = dict(action)
        mapped["id"] = uart_id
        self.pending[uart_id] = {"server_id": ident, "command": action["command"],
                                "original_line": original_line, "at": now}
        if not self._write(action_line(mapped), now):
            self.pending.pop(uart_id, None)
            return error_packet(ident, "uart_write_failed")
        return {"v": 1, "type": "ack", "id": ident, "stage": "forwarded", "uart_line": original_line}

    def service(self, now):
        result = []
        if not self._available(now):
            self._tx(False)
            self.local_pending = None
            self.owned = False
            return self._fail_all(self._reason())
        try:
            self._tx(True)
        except OSError:
            self.cooldown = now
            return self._fail_all("uart_write_failed")
        for ident in list(self.pending):
            entry = self.pending[ident]
            if ticks_diff(now, entry["at"]) >= COMMAND_TIMEOUT_MS:
                self.pending.pop(ident)
                result.append(error_packet(entry["server_id"], "ack_timeout"))
        if self.local_pending is not None and ticks_diff(now, self.local_pending[1]) >= PING_TIMEOUT_MS:
            self.local_pending = None
            self.owned = False
            self.controller_error = "ack_timeout"
        if self.local_pending is None and (self.last_ping is None or ticks_diff(now, self.last_ping) >= PING_INTERVAL_MS):
            ident = self._local_id()
            self.local_pending = (ident, now)
            self.last_ping = now
            if not self._write(("V1 %d PING\n" % ident).encode("ascii"), now):
                result.extend(self._fail_all("uart_write_failed"))
        return result
