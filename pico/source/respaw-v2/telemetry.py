"""Bounded telemetry receiver, shared by MicroPython and desktop tests."""

import json
import math
import time

MAX_LINE = 512
LINK_TIMEOUT_MS = 6000
CAPTURE_TIMEOUT_MS = 36000
MEASUREMENT_TTL_MS = 60000
UART_COMMANDS = ("PING", "STOP", "FACE", "MEASURE", "PLAY")
UART_ERROR_REASONS = ("bad_command", "bad_id", "unexpected_argument", "unknown_command",
                      "line_too_long", "bad_face", "bad_track", "controller_required",
                      "controller_busy", "busy", "sensor_unavailable", "no_contact",
                      "audio_unavailable", "host_timeout", "audio_timeout")

try:
    ticks_diff = time.ticks_diff
except AttributeError:
    def ticks_diff(now, before):
        return now - before


def integer(value, low, high):
    return type(value) is int and low <= value <= high


def number(value, low, high):
    return type(value) in (int, float) and math.isfinite(value) and low <= value <= high


def parse_legacy(line):
    fields = {}
    for item in line[1:-1].split(";"):
        key, value = item.split("=", 1)
        if key in fields:
            raise ValueError("duplicate_field")
        fields[key] = value
    if set(fields) != {"BPM", "SDNN", "RMSSD", "ESTADO"}:
        raise ValueError("bad_legacy")
    values = {}
    for key, high in (("BPM", 200), ("SDNN", 1200), ("RMSSD", 1200)):
        value = float(fields[key])
        if not number(value, 0, high):
            raise ValueError("bad_number")
        values[key.lower()] = value
    # The old packet cannot establish signal quality. Never propagate its
    # emotional label or promote these numbers to a validated measurement.
    return {"v": 1, "type": "measurement", "valid": False,
            "reason": "legacy_unvalidated", "protocol": "legacy", "legacy_values": values}


def parse_frame(line):
    if not line or len(line) > MAX_LINE:
        raise ValueError("bad_length")
    if line.startswith("<") and line.endswith(">"):
        return parse_legacy(line)
    event = json.loads(line)
    if not isinstance(event, dict) or type(event.get("v")) is not int or event["v"] != 1:
        raise ValueError("bad_version")
    kind = event.get("type")
    if kind in ("ready", "heartbeat"):
        if event.get("board") != "mega2560":
            raise ValueError("bad_board")
        if any(type(event.get(key)) is not bool for key in ("sensor", "audio")):
            raise ValueError("bad_capabilities")
        result = {"v": 1, "type": kind, "board": "mega2560",
                  "sensor": event["sensor"], "audio": event["audio"]}
        if "commands" in event:
            if type(event["commands"]) is not bool:
                raise ValueError("bad_commands_capability")
            result["commands"] = event["commands"]
        if kind == "heartbeat":
            if not integer(event.get("uptime_ms"), 0, 0xFFFFFFFF) or type(event.get("measuring")) is not bool:
                raise ValueError("bad_heartbeat")
            result.update(uptime_ms=event["uptime_ms"], measuring=event["measuring"])
        return result
    if kind == "contact":
        if (set(event) != {"v", "type", "sensor", "pressed", "uptime_ms"}
                or event["sensor"] != "fsr_a8" or type(event["pressed"]) is not bool
                or not integer(event["uptime_ms"], 0, 0xFFFFFFFF)):
            raise ValueError("bad_contact")
        return event
    if kind == "ack":
        if (set(event) != {"v", "type", "id", "command"}
                or not integer(event["id"], 1, 65535) or event["command"] not in UART_COMMANDS):
            raise ValueError("bad_ack")
        return event
    if kind == "error":
        if (set(event) != {"v", "type", "id", "reason"}
                or not integer(event["id"], 0, 65535) or event["reason"] not in UART_ERROR_REASONS):
            raise ValueError("bad_error")
        return event
    if kind != "measurement" or type(event.get("valid")) is not bool:
        raise ValueError("bad_type")
    for key, high in (("rr_count", 128), ("rmssd_pairs", 127), ("rejected", 65535), ("window_ms", 60000)):
        if not integer(event.get(key), 0, high):
            raise ValueError("bad_measurement")
    if event.get("motion_checked") is not False or event["rmssd_pairs"] > max(0, event["rr_count"] - 1):
        raise ValueError("bad_quality")
    result = {"v": 1, "type": kind, "valid": event["valid"], "protocol": "v1",
              "motion_checked": False}
    for key in ("rr_count", "rmssd_pairs", "rejected", "window_ms"):
        result[key] = event[key]
    if event["valid"]:
        if event["rr_count"] < 10 or event["rmssd_pairs"] < 9 or event["rejected"] > event["rr_count"] // 4 or event["window_ms"] < 30000:
            raise ValueError("insufficient_quality")
        for key, low, high in (("bpm", 40, 200), ("sdnn", 0, 1200), ("rmssd", 0, 1200)):
            if not number(event.get(key), low, high):
                raise ValueError("bad_number")
            result[key] = event[key]
    else:
        reason = event.get("reason")
        if reason not in ("cancelled", "insufficient_signal", "contact_lost", "fifo_gap", "saturated", "sensor_stalled"):
            raise ValueError("bad_reason")
        if any(key in event for key in ("bpm", "sdnn", "rmssd")):
            raise ValueError("invalid_with_values")
        result["reason"] = reason
    return result


class Receiver:
    def __init__(self):
        self.buffer = bytearray()
        self.discard = False
        self.accepted = 0
        self.rejected = 0
        self.last_rx = None
        self.last_measurement = None
        self.measurement_at = None
        self.capabilities = None
        self.measuring = False
        self.uptime = None
        self.contact = None
        self.contact_at = None

    def _accept(self, event, now):
        kind = event["type"]
        if self.last_rx is not None and not self.snapshot(now)["connected"]:
            self.last_measurement = self.measurement_at = None
            self.contact = self.contact_at = None
        if kind == "ready" or (kind == "heartbeat" and self.uptime is not None and
                               ((event["uptime_ms"] - self.uptime) & 0xFFFFFFFF) >= 0x80000000):
            self.last_measurement = self.measurement_at = None
            self.measuring = False
            self.uptime = None
            self.contact = self.contact_at = None
        if kind in ("ready", "heartbeat"):
            self.capabilities = {"sensor": event["sensor"], "audio": event["audio"],
                                 "commands": event.get("commands", False)}
        if kind == "heartbeat":
            self.uptime = event["uptime_ms"]
            self.measuring = event["measuring"]
            if self.measuring:
                self.last_measurement = self.measurement_at = None
        elif kind == "measurement":
            self.last_measurement = event
            self.measurement_at = now
            self.measuring = False
        elif kind == "contact":
            self.contact = {"sensor": event["sensor"], "pressed": event["pressed"],
                            "uptime_ms": event["uptime_ms"]}
            self.contact_at = now
        self.last_rx = now
        self.accepted = min(self.accepted + 1, 0x7FFFFFFF)

    def feed(self, data, now):
        events = []
        for byte in data:
            if byte == 10:
                if self.discard:
                    self.rejected = min(self.rejected + 1, 0x7FFFFFFF)
                elif self.buffer:
                    try:
                        event = parse_frame(self.buffer.decode("ascii"))
                    except (ValueError, TypeError, KeyError, OverflowError, RuntimeError):
                        self.rejected = min(self.rejected + 1, 0x7FFFFFFF)
                    else:
                        self._accept(event, now)
                        events.append(event)
                self.buffer = bytearray()
                self.discard = False
            elif byte == 13:
                continue
            elif not self.discard:
                if byte < 32 or byte > 126 or len(self.buffer) >= MAX_LINE:
                    self.buffer = bytearray()
                    self.discard = True
                else:
                    self.buffer.append(byte)
        return events

    def snapshot(self, now):
        age = None if self.last_rx is None else ticks_diff(now, self.last_rx)
        measurement_age = None if self.measurement_at is None else ticks_diff(now, self.measurement_at)
        timeout = CAPTURE_TIMEOUT_MS if self.measuring else LINK_TIMEOUT_MS
        connected = age is not None and 0 <= age < timeout
        fresh = connected and measurement_age is not None and 0 <= measurement_age < MEASUREMENT_TTL_MS
        return {"connected": connected, "last_frame_age_ms": age,
                "measuring": self.measuring and connected, "capabilities": self.capabilities,
                "measurement": self.last_measurement if fresh else None,
                "measurement_age_ms": measurement_age,
                "contact": self.contact if connected else None,
                "accepted": self.accepted, "rejected": self.rejected}
