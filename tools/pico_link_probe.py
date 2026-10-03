#!/usr/bin/env python3
"""Raspberry WebSocket <-> Mac USB <-> real Pico, without flash or UART writes."""

import argparse
import asyncio
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys

from pico_recover import RawRepl, read_remote_file, remote_eval

ROOT = Path(__file__).resolve().parents[1]


def execute(repl, source):
    stdout, stderr = repl.exec(source)
    if stderr:
        raise RuntimeError(stderr.decode("utf-8", "replace"))
    return stdout


async def probe(repl, url, token, identity, operator_url=None):
    from websockets.asyncio.client import connect

    async with connect(url, additional_headers={"Authorization": "Bearer " + token},
                       compression=None, proxy=None, max_size=512, open_timeout=10,
                       ping_interval=10, ping_timeout=10, close_timeout=3) as ws:
        async def receive():
            return json.loads(await asyncio.wait_for(ws.recv(), 10))

        await ws.send(json.dumps({"v": 1, "type": "hello", "robot_id": identity, "transport": "usb_diagnostic"}))
        welcome = await receive()
        if welcome != {"v": 1, "type": "welcome", "robot_id": identity}:
            raise RuntimeError("Unexpected welcome")
        operator_task = None
        if operator_url:
            from robot_link_client import request
            operator_task = asyncio.create_task(request(operator_url, token, {
                "v": 1, "type": "command", "command": "FACE", "argument": "warm"}))
        action = await receive()
        # Run the exact repository module in RAM; main.py and GPIOs stay intact.
        raw = json.dumps(action)
        line = remote_eval(repl, "_respaw_link['action_line'](_respaw_link['decode_packet'](%r))" % raw)
        await ws.send(json.dumps({"v": 1, "type": "ack", "id": action["id"],
                                  "stage": "validated", "uart_line": line.decode("ascii")}))
        ack = await receive()
        if ack != {"v": 1, "type": "ack_received", "id": action["id"], "stage": "validated"}:
            raise RuntimeError("Unexpected action acknowledgement")
        if operator_task:
            operator_result = await operator_task
            if operator_result != {"v": 1, "type": "command_result", "id": action["id"], "stage": "validated"}:
                raise RuntimeError("Unexpected operator result")
        # Exercise the already installed receiver with a labeled synthetic
        # heartbeat. This is not a measurement or evidence of Mega wiring.
        heartbeat = {"v": 1, "type": "heartbeat", "board": "mega2560", "sensor": False,
                     "audio": False, "measuring": False, "uptime_ms": 1000}
        execute(repl, "from telemetry import Receiver\n_respaw_events=Receiver().feed(%r, 0)" % (json.dumps(heartbeat) + "\n").encode("ascii"))
        events = remote_eval(repl, "_respaw_events")
        if events != [heartbeat]:
            raise RuntimeError("Pico receiver rejected the synthetic fixture")
        await ws.send(json.dumps({"v": 1, "type": "event", "source": "synthetic", "event": events[0]}))
        event_ack = await receive()
        if event_ack != {"v": 1, "type": "event_received", "source": "synthetic", "event_type": "heartbeat"}:
            raise RuntimeError("Unexpected event acknowledgement")
        await ws.send(json.dumps({"v": 1, "type": "heartbeat", "id": 2}))
        heartbeat_ack = await receive()
        if heartbeat_ack != {"v": 1, "type": "heartbeat_ack", "id": 2}:
            raise RuntimeError("Unexpected heartbeat acknowledgement")
        return {"action": action, "uart_line": line.decode("ascii"), "ack": ack,
                "event": event_ack, "heartbeat": heartbeat_ack}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", required=True)
    parser.add_argument("--expected-id", required=True)
    parser.add_argument("--url", default="ws://127.0.0.1:18766/robot")
    parser.add_argument("--operator-url", help="Optional localhost operator WebSocket through an SSH tunnel")
    parser.add_argument("--token-file", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--connections", type=int, default=2, help="Independent sessions to verify reconnect")
    args = parser.parse_args()
    if not 1 <= args.connections <= 10:
        parser.error("--connections debe estar entre 1 y 10")
    report = {"complete": False, "transport": "usb_diagnostic", "sessions": [],
              "started_at": datetime.now(timezone.utc).isoformat(), "port": args.port,
              "wifi_tested": False, "mega_tested": False, "flash_written": False}
    try:
        with RawRepl(args.port) as repl:
            repl.enter()
            identity = remote_eval(repl, "__import__('ubinascii').hexlify(__import__('machine').unique_id()).decode()")
            board = remote_eval(repl, "__import__('os').uname().machine")
            if identity != args.expected_id or "Pico W" not in board:
                raise ValueError("Unexpected board identity")
            report.update(identity=identity, board=board)
            names = ("main.py", "receiver.py", "telemetry.py")
            before = {name: read_remote_file(repl, name) for name in names}
            report["before_hashes"] = {name: hashlib.sha256(data).hexdigest() for name, data in before.items()}
            source = (ROOT / "pico/source/respaw-v2/link_protocol.py").read_text()
            execute(repl, "_respaw_link={}\nexec(%r, _respaw_link)" % source)
            for _ in range(args.connections):
                report["sessions"].append(asyncio.run(probe(repl, args.url, args.token_file.read_text().strip(), identity, args.operator_url)))
            for name, data in before.items():
                if read_remote_file(repl, name) != data:
                    raise RuntimeError("Device file changed: " + name)
            report["files_unchanged"] = True
            report["complete"] = True
    finally:
        args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
