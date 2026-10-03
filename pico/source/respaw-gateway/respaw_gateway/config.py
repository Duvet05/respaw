"""Validate and atomically persist configuration; never log its secrets."""

import json
import os
import re

CONFIG_PATH = "respaw-gateway-config.json"
MAX_CONFIG = 2048
URL_RE = re.compile(r"^wss://([A-Za-z0-9.-]+)(:([0-9]+))?/robot$")
TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]+$")


def validate(value):
    if not isinstance(value, dict) or type(value.get("v")) is not int or value["v"] != 1:
        raise ValueError("bad_config")
    if set(value) != {"v", "ssid", "password", "server_url", "token", "ap_password"}:
        raise ValueError("bad_fields")
    if any(not isinstance(value[key], str) for key in value if key != "v"):
        raise ValueError("bad_text")
    if len(value["ssid"].encode("utf-8")) > 32 or len(value["password"].encode("utf-8")) > 63:
        raise ValueError("bad_wifi")
    if "\x00" in value["ssid"] or "\x00" in value["password"]:
        raise ValueError("bad_wifi")
    match = URL_RE.match(value["server_url"])
    if not match or match.group(0) != value["server_url"] or len(value["server_url"]) > 200:
        raise ValueError("bad_server_url")
    host = match.group(1)
    if not host or host.startswith(".") or host.endswith(".") or ".." in host:
        raise ValueError("bad_hostname")
    port = int(match.group(3) or 443)
    if not 1 <= port <= 65535:
        raise ValueError("bad_port")
    if not 32 <= len(value["token"]) <= 128 or not all(c in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_-" for c in value["token"]):
        raise ValueError("bad_token")
    if not 12 <= len(value["ap_password"]) <= 63 or not all(c in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_-" for c in value["ap_password"]):
        raise ValueError("bad_ap_password")
    return value


def load():
    with open(CONFIG_PATH, "r") as stream:
        data = stream.read(MAX_CONFIG + 1)
    if len(data.encode("utf-8")) > MAX_CONFIG:
        raise ValueError("config_too_large")
    return validate(json.loads(data))


def save(value):
    validate(value)
    data = json.dumps(value)
    if len(data.encode("utf-8")) > MAX_CONFIG:
        raise ValueError("config_too_large")
    temporary = CONFIG_PATH + ".new"
    with open(temporary, "w") as stream:
        stream.write(data)
    os.sync()
    os.rename(temporary, CONFIG_PATH)
    os.sync()
