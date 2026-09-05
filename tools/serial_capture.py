#!/usr/bin/env python3
"""Capture a serial boot log using only the Python standard library."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import select
import termios
import time


BAUDS = {
    9600: termios.B9600,
    19200: termios.B19200,
    38400: termios.B38400,
    57600: termios.B57600,
    115200: termios.B115200,
}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", required=True)
    parser.add_argument("--baud", type=int, choices=BAUDS, required=True)
    parser.add_argument("--seconds", type=float, default=15.0)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    fd = os.open(args.port, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
    try:
        attrs = termios.tcgetattr(fd)
        attrs[0] = 0
        attrs[1] = 0
        attrs[2] = termios.CS8 | termios.CLOCAL | termios.CREAD
        attrs[3] = 0
        attrs[4] = BAUDS[args.baud]
        attrs[5] = BAUDS[args.baud]
        attrs[6][termios.VMIN] = 0
        attrs[6][termios.VTIME] = 1
        termios.tcsetattr(fd, termios.TCSANOW, attrs)
        termios.tcflush(fd, termios.TCIFLUSH)
        captured = bytearray()
        deadline = time.monotonic() + args.seconds
        while time.monotonic() < deadline:
            readable, _, _ = select.select([fd], [], [], 0.25)
            if readable:
                chunk = os.read(fd, 4096)
                if chunk:
                    captured.extend(chunk)
    finally:
        os.close(fd)

    args.output.write_bytes(captured)
    print(f"captured={len(captured)} bytes baud={args.baud}")
    print(captured.decode("utf-8", "replace"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
