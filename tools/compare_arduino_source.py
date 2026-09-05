#!/usr/bin/env python3
"""Compare C/C++ string literals from a candidate sketch with recovered flash."""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
from pathlib import Path
import re


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def strip_cpp_comments(source: str) -> str:
    output: list[str] = []
    state = "code"
    index = 0
    while index < len(source):
        char = source[index]
        following = source[index + 1] if index + 1 < len(source) else ""
        if state == "code":
            if char == "/" and following == "/":
                state = "line_comment"
                index += 2
                continue
            if char == "/" and following == "*":
                state = "block_comment"
                index += 2
                continue
            output.append(char)
            if char == '"':
                state = "string"
            elif char == "'":
                state = "character"
        elif state == "line_comment":
            if char == "\n":
                output.append(char)
                state = "code"
        elif state == "block_comment":
            if char == "*" and following == "/":
                state = "code"
                index += 2
                continue
            if char == "\n":
                output.append(char)
        elif state in {"string", "character"}:
            output.append(char)
            if char == "\\" and following:
                output.append(following)
                index += 2
                continue
            if state == "string" and char == '"':
                state = "code"
            elif state == "character" and char == "'":
                state = "code"
        index += 1
    return "".join(output)


def string_literals(source: str) -> list[str]:
    without_comments = strip_cpp_comments(source)
    without_includes = re.sub(r"^\s*#\s*include\b.*$", "", without_comments, flags=re.MULTILINE)
    literals: list[str] = []
    for match in re.finditer(r'"(?:\\.|[^"\\])*"', without_includes):
        try:
            value = ast.literal_eval(match.group(0))
        except (SyntaxError, ValueError):
            continue
        if isinstance(value, str) and len(value.encode("utf-8")) >= 4 and value not in literals:
            literals.append(value)
    return literals


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--firmware", required=True, type=Path)
    parser.add_argument("--report", required=True, type=Path)
    args = parser.parse_args()

    source_bytes = args.source.read_bytes()
    firmware = args.firmware.read_bytes()
    source = source_bytes.decode("utf-8")
    literals = string_literals(source)
    comparisons = [
        {
            "text": literal,
            "utf8_hex": literal.encode("utf-8").hex(),
            "found_in_flash": literal.encode("utf-8") in firmware,
            "flash_offset": (
                f"0x{firmware.find(literal.encode('utf-8')):05X}"
                if literal.encode("utf-8") in firmware
                else None
            ),
        }
        for literal in literals
    ]
    matched = sum(item["found_in_flash"] for item in comparisons)
    report = {
        "candidate_source": str(args.source),
        "candidate_source_sha256": sha256(source_bytes),
        "recovered_firmware": str(args.firmware),
        "recovered_firmware_sha256": sha256(firmware),
        "method": "Exact UTF-8 search of distinct C/C++ string literals (>=4 bytes) in recovered flash",
        "distinct_literals_compared": len(comparisons),
        "matched_literals": matched,
        "match_ratio": round(matched / len(comparisons), 4) if comparisons else 0,
        "all_compared_literals_match": matched == len(comparisons),
        "comparisons": comparisons,
        "conclusion": (
            "The candidate is strongly associated with the recovered firmware. "
            "String identity alone cannot prove a bit-for-bit source/compiler/library match."
        ),
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if matched == len(comparisons) else 1


if __name__ == "__main__":
    raise SystemExit(main())
