#!/usr/bin/env python3
"""Install the Pico receiver, backing up files first and verifying each write."""

import argparse
import base64
import hashlib
import json
from pathlib import Path

from pico_recover import RawRepl, inventory, read_remote_file, remote_eval, safe_destination

SOURCE = Path(__file__).resolve().parents[1] / "pico/source/respaw-v2"


def execute(repl, source):
    stdout, stderr = repl.exec(source)
    if stderr:
        raise RuntimeError(stderr.decode("utf-8", "replace"))
    return stdout


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", required=True)
    parser.add_argument("--backup-dir", required=True, type=Path, help="New directory outside the repository")
    parser.add_argument("--expected-id", help="Expected machine.unique_id in lowercase hex")
    args = parser.parse_args()
    repository = SOURCE.parents[2]
    backup = args.backup_dir.expanduser().resolve()
    if backup.is_relative_to(repository):
        raise ValueError("Keep device backups outside the repository.")
    backup.mkdir(parents=True, exist_ok=False)
    report = {"port": args.port, "before": [], "installed": [], "complete": False}
    try:
        with RawRepl(args.port) as repl:
            repl.enter()
            identity = remote_eval(repl, "__import__('ubinascii').hexlify(__import__('machine').unique_id()).decode()")
            board = remote_eval(repl, "__import__('os').uname().machine")
            if "Pico W" not in board or args.expected_id and identity != args.expected_id:
                raise ValueError("Unexpected board identity: " + str((identity, board)))
            report.update(identity=identity, board=board)
            for entry in inventory(repl):
                if entry["type"] == "error":
                    raise RuntimeError("Cannot back up device inventory")
                if entry["type"] != "file":
                    continue
                name = entry["path"]
                data = read_remote_file(repl, name)
                if data != read_remote_file(repl, name) or len(data) != entry["size"]:
                    raise RuntimeError("Backup mismatch: " + name)
                target = safe_destination(backup / "files", name)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
                report["before"].append({"path": name, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()})
            # Activate main.py last, after the two receiver modules verify.
            for name in ("telemetry.py", "receiver.py", "main.py"):
                data = (SOURCE / name).read_bytes()
                temporary = ".respaw-upload-" + name
                execute(repl, "import ubinascii, os\nf=open(%r, 'wb')" % temporary)
                try:
                    for start in range(0, len(data), 384):
                        chunk = base64.b64encode(data[start:start + 384]).decode("ascii")
                        execute(repl, "f.write(ubinascii.a2b_base64(%r))" % chunk)
                finally:
                    execute(repl, "f.close()\nos.sync()")
                if read_remote_file(repl, temporary) != data:
                    raise RuntimeError("Upload mismatch: " + name)
                execute(repl, "os.rename(%r, %r)\nos.sync()" % (temporary, name))
                if read_remote_file(repl, name) != data:
                    raise RuntimeError("Installed file mismatch: " + name)
                report["installed"].append({"path": name, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()})
            report["complete"] = True
    finally:
        (backup / "install-report.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
