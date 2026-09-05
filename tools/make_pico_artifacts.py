#!/usr/bin/env python3
"""Verify Pico flash reads and preserve filesystem/source artifacts."""

from __future__ import annotations

import ast
import hashlib
import json
from pathlib import Path
import shutil


ROOT = Path("/Users/duvet05/Development/hardware-recovery-2026-09-05/pico")
FLASH_SIZE = 2 * 1024 * 1024
FILESYSTEM_START = 0x12C000
FILESYSTEM_END = 0x200000
DELETED_SOURCE_START = 0x1A9000


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def utf8_prefix_before_erased(data: bytes, start: int) -> bytes:
    end = data.find(b"\xff", start)
    if end < 0:
        raise RuntimeError("No erased-byte terminator after candidate source")
    candidate = data[start:end]
    candidate.decode("utf-8")
    return candidate


def main() -> int:
    first = (ROOT / "flash-pass1.bin").read_bytes()
    second = (ROOT / "flash-pass2.bin").read_bytes()
    if len(first) != FLASH_SIZE or len(second) != FLASH_SIZE:
        raise RuntimeError("Unexpected RP2040 flash image size")
    if first != second:
        raise RuntimeError("The two Pico flash reads differ")

    shutil.copyfile(ROOT / "flash-pass2.bin", ROOT / "firmware-full.bin")
    filesystem = second[FILESYSTEM_START:FILESYSTEM_END]
    (ROOT / "filesystem-littlefs.bin").write_bytes(filesystem)

    recovered = utf8_prefix_before_erased(second, DELETED_SOURCE_START)
    parsed = ast.parse(recovered.decode("utf-8"), filename="recovered_source_0x1A9000.py")
    if not parsed.body:
        raise RuntimeError("Recovered source parsed but was empty")
    recovered_dir = ROOT / "recovered-deleted"
    recovered_dir.mkdir(parents=True, exist_ok=True)
    source_path = recovered_dir / "recovered_source_0x1A9000.py"
    source_path.write_bytes(recovered)

    code_markers = (
        b"from machine import",
        b"import machine",
        b"import max30102",
        b"def ",
        b"class ",
        b"while True:",
    )
    page_candidates: list[dict[str, object]] = []
    for address in range(FILESYSTEM_START, FILESYSTEM_END, 4096):
        page = second[address : address + 4096]
        marker_hits = [marker.decode("ascii") for marker in code_markers if marker in page]
        if not marker_hits:
            continue
        printable = sum(byte in b"\t\n\r" or 32 <= byte <= 126 or byte >= 0xC2 for byte in page)
        prefix = page.split(b"\xff", 1)[0][:160]
        page_candidates.append(
            {
                "address": f"0x{address:06X}",
                "sha256": digest(page),
                "marker_hits": marker_hits,
                "printable_ratio": round(printable / len(page), 4),
                "preview": prefix.decode("utf-8", "replace"),
            }
        )

    report = {
        "device": "Raspberry Pi Pico W / RP2040 B2",
        "usb_application": {"vid": "2E8A", "pid": "0005", "serial": "e66368254f3e912e"},
        "usb_bootsel": {"vid": "2E8A", "pid": "0003", "serial": "E0C9125B0D9B"},
        "flash": {
            "bytes": len(second),
            "sha256": digest(second),
            "two_reads_identical": True,
            "picotool_verification_passed_each_read": True,
        },
        "firmware": {
            "name": "MicroPython",
            "version": "1.26.1",
            "build_date": "2025-09-11",
            "sdk_version": "2.1.1",
            "binary_range": "0x10000000-0x100D5998",
        },
        "filesystem": {
            "range": "0x1012C000-0x10200000",
            "flash_file_offset_range": f"0x{FILESYSTEM_START:06X}-0x{FILESYSTEM_END:06X}",
            "bytes": len(filesystem),
            "sha256": digest(filesystem),
            "currently_visible_source_files": [
                "max30102/__init__.py",
                "max30102/circular_buffer.py",
            ],
            "boot_py_present": False,
            "main_py_present": False,
        },
        "deleted_source_recovery": {
            "source_flash_offset": f"0x{DELETED_SOURCE_START:06X}",
            "bytes": len(recovered),
            "sha256": digest(recovered),
            "utf8_valid": True,
            "python_syntax_valid": True,
            "filename_inferred": "main.py-like sensor test; original filename was not recoverable",
            "artifact": str(source_path.relative_to(ROOT)),
        },
        "source_like_flash_pages": page_candidates,
        "artifacts": {
            "restorable_full_flash": "firmware-full.bin",
            "raw_littlefs_partition": "filesystem-littlefs.bin",
            "filesystem_logical_recovery_report": "recovery-report.json",
        },
    }
    (ROOT / "forensic-report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
