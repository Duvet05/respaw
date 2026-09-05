#!/usr/bin/env python3
"""Verify duplicate ATmega2560 reads and build recovery artifacts."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil


ROOT = Path("/Users/duvet05/Development/hardware-recovery-2026-09-05/mega2560")
FLASH_SIZE = 262_144
EEPROM_SIZE = 4_096
BOOT_START = 0x3E000


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def ihex_record(address: int, record_type: int, payload: bytes) -> str:
    body = bytes((len(payload), (address >> 8) & 0xFF, address & 0xFF, record_type)) + payload
    checksum = (-sum(body)) & 0xFF
    return ":" + (body + bytes((checksum,))).hex().upper()


def write_ihex(destination: Path, data: bytes, *, skip_erased: bool = False) -> None:
    lines: list[str] = []
    current_upper: int | None = None
    for offset in range(0, len(data), 16):
        chunk = data[offset : offset + 16]
        if skip_erased and chunk == b"\xff" * len(chunk):
            continue
        upper = offset >> 16
        if upper != current_upper:
            lines.append(ihex_record(0, 4, upper.to_bytes(2, "big")))
            current_upper = upper
        lines.append(ihex_record(offset & 0xFFFF, 0, chunk))
    lines.append(ihex_record(0, 1, b""))
    destination.write_text("\n".join(lines) + "\n", encoding="ascii")


def ascii_strings(data: bytes, minimum: int = 5) -> list[dict[str, object]]:
    result: list[dict[str, object]] = []
    start: int | None = None
    for index, byte in enumerate(data + b"\x00"):
        if 32 <= byte <= 126:
            if start is None:
                start = index
        elif start is not None:
            if index - start >= minimum:
                result.append(
                    {
                        "offset": f"0x{start:05X}",
                        "text": data[start:index].decode("ascii"),
                    }
                )
            start = None
    return result


def main() -> int:
    flash1 = (ROOT / "flash-pass1.bin").read_bytes()
    flash2 = (ROOT / "flash-pass2.bin").read_bytes()
    eeprom1 = (ROOT / "eeprom-pass1.bin").read_bytes()
    eeprom2 = (ROOT / "eeprom-pass2.bin").read_bytes()

    if len(flash2) != FLASH_SIZE:
        raise RuntimeError(f"Second flash read has {len(flash2)} bytes, expected {FLASH_SIZE}")
    if len(eeprom1) != EEPROM_SIZE or len(eeprom2) != EEPROM_SIZE:
        raise RuntimeError("Unexpected EEPROM read size")

    flash_prefix_matches = flash1 == flash2[: len(flash1)]
    flash_trimmed_tail_is_erased = flash2[len(flash1) :] == b"\xff" * (len(flash2) - len(flash1))
    eeprom_matches = eeprom1 == eeprom2
    if not (flash_prefix_matches and flash_trimmed_tail_is_erased and eeprom_matches):
        raise RuntimeError("Duplicate read verification failed")

    memory_names = ("lock", "lfuse", "hfuse", "efuse")
    configuration: dict[str, str] = {}
    for name in memory_names:
        first = (ROOT / f"{name}-pass1.bin").read_bytes()
        second = (ROOT / f"{name}-pass2.bin").read_bytes()
        if len(first) != 1 or first != second:
            raise RuntimeError(f"Duplicate {name} verification failed")
        configuration[name] = f"0x{second[0]:02X}"

    canonical_flash = ROOT / "firmware-full.bin"
    canonical_eeprom = ROOT / "eeprom.bin"
    shutil.copyfile(ROOT / "flash-pass2.bin", canonical_flash)
    shutil.copyfile(ROOT / "eeprom-pass2.bin", canonical_eeprom)
    write_ihex(ROOT / "firmware-full.hex", flash2, skip_erased=False)
    write_ihex(ROOT / "firmware-programmed-only.hex", flash2, skip_erased=True)
    write_ihex(ROOT / "application-only.hex", flash2[:BOOT_START], skip_erased=True)
    write_ihex(ROOT / "eeprom.hex", eeprom2, skip_erased=False)

    application = flash2[:BOOT_START]
    bootloader = flash2[BOOT_START:]
    application_non_erased = [i for i, byte in enumerate(application) if byte != 0xFF]
    bootloader_non_erased = [i for i, byte in enumerate(bootloader) if byte != 0xFF]
    strings = ascii_strings(flash2)
    (ROOT / "printable-strings.json").write_text(
        json.dumps(strings, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    report = {
        "device": "Arduino Mega 2560 / ATmega2560",
        "signature": "1E 98 01",
        "port_at_capture": "/dev/cu.usbmodem114301",
        "usb": {"vid": "2341", "pid": "0042", "serial": "55139313435351E0E1C1"},
        "bootloader_protocol": "Wiring / STK500v2",
        "bootloader_reported_version": "2.10",
        "memory": {
            "flash_bytes": len(flash2),
            "flash_sha256": digest(flash2),
            "eeprom_bytes": len(eeprom2),
            "eeprom_sha256": digest(eeprom2),
            "eeprom_non_erased_bytes": sum(byte != 0xFF for byte in eeprom2),
            "application_region": f"0x00000-0x{BOOT_START - 1:05X}",
            "application_last_non_erased": f"0x{max(application_non_erased):05X}",
            "application_non_erased_bytes": len(application_non_erased),
            "bootloader_region": f"0x{BOOT_START:05X}-0x{FLASH_SIZE - 1:05X}",
            "bootloader_last_non_erased": f"0x{BOOT_START + max(bootloader_non_erased):05X}",
            "bootloader_non_erased_bytes": len(bootloader_non_erased),
        },
        "configuration": configuration,
        "verification": {
            "flash_pass1_bytes": len(flash1),
            "flash_pass2_bytes": len(flash2),
            "flash_pass1_matches_pass2_prefix": flash_prefix_matches,
            "flash_pass2_extra_bytes": len(flash2) - len(flash1),
            "flash_pass2_extra_bytes_are_all_FF": flash_trimmed_tail_is_erased,
            "eeprom_passes_match": eeprom_matches,
            "configuration_passes_match": True,
        },
        "artifacts": {
            "restorable_raw_flash": canonical_flash.name,
            "restorable_full_intel_hex": "firmware-full.hex",
            "compact_full_intel_hex": "firmware-programmed-only.hex",
            "application_only_intel_hex": "application-only.hex",
            "restorable_raw_eeprom": canonical_eeprom.name,
            "restorable_eeprom_intel_hex": "eeprom.hex",
            "printable_strings": "printable-strings.json",
        },
        "limitations": [
            "Compiled AVR flash does not contain original comments or most source-level names.",
            "The exact original .ino/.cpp source cannot be reconstructed bit-for-bit from this image.",
        ],
    }
    candidate_source = ROOT / "source-candidate" / "FINAL_FINAL_FINAL_TESIS_AMIR_FLORES.ino"
    comparison_report = ROOT / "source-candidate" / "firmware-string-comparison.json"
    if candidate_source.exists():
        source_bytes = candidate_source.read_bytes()
        candidate_details: dict[str, object] = {
            "artifact": str(candidate_source.relative_to(ROOT)),
            "sha256": digest(source_bytes),
            "bytes": len(source_bytes),
            "status": "high-confidence candidate; exact build identity not proven",
        }
        if comparison_report.exists():
            comparison = json.loads(comparison_report.read_text(encoding="utf-8"))
            candidate_details["string_comparison_artifact"] = str(comparison_report.relative_to(ROOT))
            candidate_details["matched_literals"] = comparison.get("matched_literals")
            candidate_details["distinct_literals_compared"] = comparison.get("distinct_literals_compared")
            candidate_details["all_compared_literals_match"] = comparison.get("all_compared_literals_match")
        report["source_candidate"] = candidate_details
    (ROOT / "recovery-report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
