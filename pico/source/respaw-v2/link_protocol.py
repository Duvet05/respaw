"""Small network action envelope, compatible with MicroPython and Mega v2."""

import json

MAX_PACKET = 512
EXPRESSIONS = ("neutral", "warm", "listening", "thinking", "sleeping")


def decode_packet(raw):
    if not isinstance(raw, str) or not 1 <= len(raw.encode("utf-8")) <= MAX_PACKET:
        raise ValueError("bad_length")
    packet = json.loads(raw)
    if not isinstance(packet, dict) or type(packet.get("v")) is not int or packet["v"] != 1:
        raise ValueError("bad_version")
    return packet


def action_line(packet):
    """Validate an action and encode it; this function never writes to UART."""
    if (not isinstance(packet, dict) or type(packet.get("v")) is not int
            or packet["v"] != 1 or packet.get("type") != "action"):
        raise ValueError("bad_action")
    ident = packet.get("id")
    if type(ident) is not int or not 1 <= ident <= 65535:
        raise ValueError("bad_id")
    command = packet.get("command")
    if command == "FACE":
        if set(packet) != {"v", "type", "id", "command", "argument"} or packet["argument"] not in EXPRESSIONS:
            raise ValueError("bad_face")
        return ("V1 %d FACE %s\n" % (ident, packet["argument"])).encode("ascii")
    if command not in ("PING", "STOP", "MEASURE") or set(packet) != {"v", "type", "id", "command"}:
        raise ValueError("bad_command")
    return ("V1 %d %s\n" % (ident, command)).encode("ascii")
