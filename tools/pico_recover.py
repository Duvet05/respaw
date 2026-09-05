#!/usr/bin/env python3
"""Read-only MicroPython filesystem recovery over the raw REPL."""

from __future__ import annotations

import argparse
import ast
import base64
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import select
import termios
import time


class RawRepl:
    def __init__(self, port: str) -> None:
        self.port = port
        self.fd = -1

    def __enter__(self) -> "RawRepl":
        self.fd = os.open(self.port, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
        attrs = termios.tcgetattr(self.fd)
        attrs[0] = 0
        attrs[1] = 0
        attrs[2] = termios.CS8 | termios.CLOCAL | termios.CREAD
        attrs[3] = 0
        attrs[4] = termios.B115200
        attrs[5] = termios.B115200
        attrs[6][termios.VMIN] = 0
        attrs[6][termios.VTIME] = 1
        termios.tcsetattr(self.fd, termios.TCSANOW, attrs)
        termios.tcflush(self.fd, termios.TCIOFLUSH)
        return self

    def __exit__(self, *_: object) -> None:
        if self.fd >= 0:
            try:
                # Return to the friendly REPL and soft-reset so main.py resumes.
                os.write(self.fd, b"\x02\x04")
                time.sleep(0.5)
            finally:
                os.close(self.fd)
                self.fd = -1

    def _read_until(self, marker: bytes, timeout: float) -> bytes:
        deadline = time.monotonic() + timeout
        data = bytearray()
        while marker not in data:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(
                    f"Timeout waiting for {marker!r}; received tail {bytes(data[-200:])!r}"
                )
            readable, _, _ = select.select([self.fd], [], [], min(0.2, remaining))
            if readable:
                chunk = os.read(self.fd, 4096)
                if chunk:
                    data.extend(chunk)
        return bytes(data)

    def enter(self) -> bytes:
        os.write(self.fd, b"\x03\x03")
        time.sleep(0.2)
        os.write(self.fd, b"\x01")
        # Ctrl-C may leave a friendly-REPL ``>>>`` in the input stream.  Wait
        # for the complete raw-REPL banner so that none of it contaminates the
        # first command response.
        return self._read_until(b"raw REPL; CTRL-B to exit\r\n>", 5.0)

    def exec(self, source: str, timeout: float = 10.0) -> tuple[bytes, bytes]:
        os.write(self.fd, source.encode("utf-8") + b"\x04")
        response = self._read_until(b"\x04>", timeout)
        if not response.startswith(b"OK"):
            raise RuntimeError(f"Unexpected raw-REPL response: {response[:200]!r}")
        payload = response[2:-2]
        stdout, separator, stderr = payload.partition(b"\x04")
        if not separator:
            raise RuntimeError("Malformed raw-REPL response")
        return stdout, stderr


def remote_eval(repl: RawRepl, expression: str) -> object:
    stdout, stderr = repl.exec(f"print(repr({expression}))")
    if stderr:
        raise RuntimeError(stderr.decode("utf-8", "replace"))
    representation = stdout.decode("utf-8").strip()
    try:
        return ast.literal_eval(representation)
    except (SyntaxError, ValueError):
        # MicroPython's struct-like values (sys.implementation, os.uname)
        # deliberately use a repr that is descriptive but not a Python literal.
        return representation


def inventory(repl: RawRepl) -> list[dict[str, object]]:
    source = r'''
import os, ubinascii
def walk(p):
    for n in os.listdir(p or '/'):
        q = (p + '/' + n) if p else n
        try:
            s = os.stat(q)
            h = ubinascii.hexlify(q.encode()).decode()
            if s[0] & 0x4000:
                print('D\t%s' % h)
                walk(q)
            else:
                print('F\t%d\t%s' % (s[6], h))
        except Exception as e:
            print('E\t%s\t%s' % (h, repr(e)))
walk('')
'''
    stdout, stderr = repl.exec(source, timeout=30.0)
    if stderr:
        raise RuntimeError(stderr.decode("utf-8", "replace"))
    entries: list[dict[str, object]] = []
    for raw_line in stdout.decode("utf-8", "replace").splitlines():
        fields = raw_line.split("\t")
        if not fields or fields[0] not in {"D", "F", "E"}:
            continue
        if fields[0] == "F":
            entries.append(
                {
                    "type": "file",
                    "size": int(fields[1]),
                    "path": bytes.fromhex(fields[2]).decode("utf-8"),
                }
            )
        elif fields[0] == "D":
            entries.append(
                {
                    "type": "directory",
                    "path": bytes.fromhex(fields[1]).decode("utf-8"),
                }
            )
        else:
            entries.append({"type": "error", "raw": raw_line})
    return entries


def read_remote_file(repl: RawRepl, remote_path: str) -> bytes:
    source = f'''
import ubinascii
f = open({remote_path!r}, 'rb')
while 1:
    b = f.read(384)
    if not b:
        break
    print(ubinascii.b2a_base64(b).decode().strip())
f.close()
'''
    stdout, stderr = repl.exec(source, timeout=60.0)
    if stderr:
        raise RuntimeError(stderr.decode("utf-8", "replace"))
    return b"".join(base64.b64decode(line) for line in stdout.splitlines() if line)


def safe_destination(root: Path, remote_path: str) -> Path:
    parts = PurePosixPath(remote_path).parts
    if not parts or remote_path.startswith("/") or ".." in parts:
        raise ValueError(f"Unsafe remote path: {remote_path!r}")
    return root.joinpath(*parts)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", required=True)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)

    report: dict[str, object] = {
        "port": args.port,
        "started_unix": time.time(),
        "files": [],
        "warnings": [],
    }
    with RawRepl(args.port) as repl:
        banner = repl.enter()
        report["raw_repl_banner"] = banner.decode("utf-8", "replace")
        report["implementation"] = remote_eval(repl, "__import__('sys').implementation")
        try:
            report["uname"] = remote_eval(repl, "__import__('os').uname()")
        except Exception as exc:
            report["warnings"].append(f"uname unavailable: {exc}")

        entries = inventory(repl)
        report["inventory"] = entries
        for entry in entries:
            if entry.get("type") != "file":
                continue
            remote_path = str(entry["path"])
            destination = safe_destination(args.output, remote_path)
            destination.parent.mkdir(parents=True, exist_ok=True)
            first = read_remote_file(repl, remote_path)
            second = read_remote_file(repl, remote_path)
            first_hash = hashlib.sha256(first).hexdigest()
            second_hash = hashlib.sha256(second).hexdigest()
            expected_size = int(entry["size"])
            verified = first == second and len(first) == expected_size
            destination.write_bytes(first)
            file_report = {
                "path": remote_path,
                "size_inventory": expected_size,
                "size_read": len(first),
                "sha256": first_hash,
                "second_read_sha256": second_hash,
                "verified": verified,
            }
            report["files"].append(file_report)
            if not verified:
                report["warnings"].append(f"Verification mismatch: {remote_path}")
            print(f"{'OK' if verified else 'WARN'} {remote_path} {len(first)} {first_hash}")

    report["finished_unix"] = time.time()
    (args.output / "recovery-report.json").write_text(
        json.dumps(report, indent=2, default=repr) + "\n", encoding="utf-8"
    )
    return 0 if not report["warnings"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
