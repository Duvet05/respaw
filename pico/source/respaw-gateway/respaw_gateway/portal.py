"""Small WPA2 AP setup form, served only on the Pico's private AP address."""

import asyncio
import binascii
import os
import json

from . import config

AP_ADDRESS = "192.168.4.1"
MAX_HEADERS = 2048
MAX_BODY = 2048


def escape(value):
    return value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def unquote(value):
    result = bytearray()
    offset = 0
    while offset < len(value):
        char = value[offset]
        if char == 37:
            if offset + 2 >= len(value):
                raise ValueError("bad_form_encoding")
            result.append(int(value[offset + 1:offset + 3], 16))
            offset += 3
        else:
            result.append(32 if char == 43 else char)
            offset += 1
    return result.decode("utf-8")


def form_values(body):
    fields = {}
    for item in body.split(b"&"):
        parts = item.split(b"=", 1)
        if len(parts) != 2:
            raise ValueError("bad_form")
        key, value = unquote(parts[0]), unquote(parts[1])
        if key in fields or key not in ("ssid", "password", "server_url", "token", "nonce"):
            raise ValueError("bad_form_field")
        fields[key] = value
    return fields


async def read_request(reader):
    headers = bytearray()
    while len(headers) < MAX_HEADERS:
        char = await reader.readexactly(1)
        if not char:
            raise EOFError
        headers.extend(char)
        if headers.endswith(b"\r\n\r\n"):
            break
    else:
        raise ValueError("headers_too_large")
    lines = bytes(headers).split(b"\r\n")
    first = lines[0].split(b" ")
    if len(first) != 3 or first[2] not in (b"HTTP/1.0", b"HTTP/1.1"):
        raise ValueError("bad_http")
    values = {}
    for line in lines[1:]:
        if not line:
            continue
        parts = line.split(b":", 1)
        if len(parts) != 2:
            raise ValueError("bad_header")
        key, value = parts[0].lower(), parts[1].strip()
        if key in values:
            raise ValueError("duplicate_header")
        values[key] = value
    if values.get(b"host") not in (b"192.168.4.1", b"192.168.4.1:80"):
        raise ValueError("bad_host")
    if values.get(b"origin", b"http://192.168.4.1") != b"http://192.168.4.1":
        raise ValueError("bad_origin")
    if b"transfer-encoding" in values:
        raise ValueError("bad_body")
    length = int(values.get(b"content-length", b"0"))
    if not 0 <= length <= MAX_BODY:
        raise ValueError("body_too_large")
    body = await reader.readexactly(length) if length else b""
    return first[0], first[1], values, body


class Portal:
    def __init__(self, value):
        self.value = value
        self.server = None
        self.ap = None
        self.clients = 0
        self.nonce = binascii.hexlify(os.urandom(16)).decode()
        import machine
        self.ssid = "ResPaw-Setup-" + binascii.hexlify(machine.unique_id()).decode()[-4:].upper()

    def page(self):
        # Persisted WiFi passwords and bearer token are never rendered.
        return ('<!doctype html><html lang="es"><meta charset="utf-8">'
                '<meta name="viewport" content="width=device-width,initial-scale=1">'
                '<title>Conectar ResPaw</title><style>body{font:18px system-ui;max-width:480px;'
                'margin:24px auto;padding:16px;background:#eef4f1;color:#173f30}'
                'input,button{box-sizing:border-box;width:100%%;padding:12px;margin:8px 0 20px;'
                'font:inherit}button{background:#245d45;color:white;border:0;border-radius:8px}'
                'details{font-size:15px}label{display:block}</style>'
                '<h1>Conectar ResPaw</h1><p>Introduce la red WiFi de 2.4 GHz que usará el Pico.</p>'
                '<form method="post" action="/save">'
                '<input type="hidden" name="nonce" value="%s">'
                '<label>Nombre de la red<input name="ssid" maxlength="32" required '
                'autocomplete="off" value="%s"></label>'
                '<label>Contraseña de WiFi<input name="password" type="password" maxlength="63" '
                'autocomplete="new-password"></label>'
                '<details><summary>Configuración avanzada del servidor</summary>'
                '<label>Dirección segura<input name="server_url" maxlength="200" value="%s" '
                'autocomplete="off"></label>'
                '<label>Token nuevo (dejar vacío para conservar)<input name="token" type="password" '
                'maxlength="128" autocomplete="new-password"></label></details>'
                '<button>Guardar y conectar</button></form>'
                '<p>Después de guardar, vuelve a tu red habitual. El Pico se conectará automáticamente.</p>'
                '</html>') % (self.nonce, escape(self.value["ssid"]), escape(self.value["server_url"]))

    async def respond(self, writer, status, body):
        data = body.encode("utf-8")
        writer.write(("HTTP/1.1 %s\r\nContent-Type: text/html; charset=utf-8\r\n"
                      "Cache-Control: no-store\r\nX-Content-Type-Options: nosniff\r\n"
                      "Content-Security-Policy: default-src 'none'; style-src 'unsafe-inline'; form-action 'self'\r\n"
                      "Connection: close\r\nContent-Length: %d\r\n\r\n" % (status, len(data))).encode())
        writer.write(data)
        await writer.drain()

    async def handle(self, reader, writer):
        if self.clients >= 2:
            writer.close()
            await writer.wait_closed()
            return
        self.clients += 1
        restart = False
        try:
            peer = writer.get_extra_info("peername")[0]
            if not peer.startswith("192.168.4."):
                raise ValueError("bad_peer")
            method, path, headers, body = await asyncio.wait_for(read_request(reader), 10)
            if method == b"GET" and path == b"/":
                await asyncio.wait_for(self.respond(writer, "200 OK", self.page()), 5)
            elif method == b"POST" and path == b"/save":
                if headers.get(b"content-type") != b"application/x-www-form-urlencoded":
                    raise ValueError("bad_content_type")
                fields = form_values(body)
                if (fields.get("nonce") != self.nonce or not fields.get("ssid")
                        or set(fields) != {"nonce", "ssid", "password", "server_url", "token"}):
                    raise ValueError("bad_form")
                updated = dict(self.value)
                updated.update(ssid=fields["ssid"], password=fields["password"],
                               server_url=fields["server_url"] or self.value["server_url"],
                               token=fields["token"] or self.value["token"])
                config.save(updated)
                await asyncio.wait_for(self.respond(writer, "200 OK", '<meta charset="utf-8"><p>Guardado. El Pico se reinicia para conectar.</p>'), 5)
                restart = True
            else:
                await asyncio.wait_for(self.respond(writer, "404 Not Found", "Ruta no encontrada."), 5)
        except (ValueError, UnicodeError, OSError, EOFError, asyncio.TimeoutError):
            try:
                await asyncio.wait_for(self.respond(writer, "400 Bad Request", "No se guardó. Revisa los campos y vuelve a intentarlo."), 3)
            except (OSError, asyncio.TimeoutError):
                pass
        finally:
            self.clients -= 1
            writer.close()
            await writer.wait_closed()
        if restart:
            await asyncio.sleep(1)
            import machine
            machine.reset()

    async def start(self):
        if self.server is not None:
            return
        import network
        self.ap = network.WLAN(network.WLAN.IF_AP)
        self.ap.config(ssid=self.ssid, security=3, key=self.value["ap_password"])
        self.ap.active(True)
        self.ap.ifconfig((AP_ADDRESS, "255.255.255.0", AP_ADDRESS, AP_ADDRESS))
        self.server = await asyncio.start_server(self.handle, AP_ADDRESS, 80, backlog=2)
        print(json.dumps({"v": 1, "type": "gateway_setup", "ssid": self.ssid, "url": "http://192.168.4.1"}))

    async def stop(self):
        if self.server is not None:
            self.server.close()
            await self.server.wait_closed()
            self.server = None
        if self.ap is not None:
            self.ap.active(False)
            self.ap = None
