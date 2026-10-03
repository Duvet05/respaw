#!/usr/bin/env python3
"""Local operator for robot_link_server (run on the Raspberry or via SSH tunnel)."""

import argparse
import asyncio
import json
from pathlib import Path
from urllib.parse import urlparse


async def request(url, token, packet):
    from websockets.asyncio.client import connect

    async with connect(url, additional_headers={"Authorization": "Bearer " + token},
                       proxy=None, compression=None, max_size=512, open_timeout=10,
                       close_timeout=3) as ws:
        await ws.send(json.dumps(packet, separators=(",", ":")))
        return json.loads(await asyncio.wait_for(ws.recv(), 15))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="ws://127.0.0.1:8767/operator")
    parser.add_argument("--token-file", required=True, type=Path)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("status")
    action = commands.add_parser("action")
    action.add_argument("action", choices=("FACE", "PING", "STOP", "MEASURE"))
    action.add_argument("argument", nargs="?", choices=("neutral", "warm", "listening", "thinking", "sleeping"))
    args = parser.parse_args()
    address = urlparse(args.url)
    if address.scheme != "ws" or address.hostname not in ("localhost", "127.0.0.1", "::1") or address.path != "/operator":
        parser.error("El operador requiere ws://localhost:puerto/operator mediante conexión local o túnel SSH")
    packet = {"v": 1, "type": "status"}
    if args.command == "action":
        if (args.action == "FACE") != (args.argument is not None):
            parser.error("FACE requiere una expresión; los demás comandos no reciben argumentos")
        packet = {"v": 1, "type": "command", "command": args.action}
        if args.argument is not None:
            packet["argument"] = args.argument
    result = asyncio.run(request(args.url, args.token_file.read_text().strip(), packet))
    print(json.dumps(result, indent=2, ensure_ascii=False))
    if result.get("stage") in ("error", "unconfirmed", "disconnected"):
        raise SystemExit(2)


if __name__ == "__main__":
    main()
