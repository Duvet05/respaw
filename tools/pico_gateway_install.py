#!/usr/bin/env python3
"""Install the opt-in Pico WiFi gateway after a verified full file backup."""

import argparse
import base64
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import secrets
import sys

from pico_recover import RawRepl, inventory, read_remote_file, remote_eval, safe_destination

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "pico/source/respaw-gateway"
sys.path.insert(0, str(SOURCE))
from respaw_gateway.config import CONFIG_PATH, validate


def execute(repl, source):
    stdout, stderr = repl.exec(source, timeout=30)
    if stderr:
        raise RuntimeError(stderr.decode("utf-8", "replace"))
    return stdout


def private_write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb") as stream:
        stream.write(data)


def upload(repl, name, data):
    parts = name.split("/")
    for length in range(1, len(parts)):
        directory = "/".join(parts[:length])
        execute(repl, "import os\ntry:\n os.mkdir(%r)\nexcept OSError as e:\n if e.args[0] != 17: raise" % directory)
    temporary = "/".join(parts[:-1] + [".respaw-upload-" + parts[-1]])
    execute(repl, "import ubinascii, os\nf=open(%r, 'wb')" % temporary)
    try:
        for offset in range(0, len(data), 384):
            chunk = base64.b64encode(data[offset:offset + 384]).decode("ascii")
            execute(repl, "f.write(ubinascii.a2b_base64(%r))" % chunk)
    finally:
        execute(repl, "f.close()\nos.sync()")
    if read_remote_file(repl, temporary) != data:
        raise RuntimeError("Upload verification failed: " + name)
    execute(repl, "os.rename(%r, %r)\nos.sync()" % (temporary, name))
    if read_remote_file(repl, name) != data:
        raise RuntimeError("Installed verification failed: " + name)


def settings(args):
    if args.config_file:
        value = json.loads(args.config_file.read_text())
    else:
        if not args.server_url or not args.token_file:
            raise ValueError("Provide --server-url and --token-file, or --config-file.")
        password = args.ap_password_file.read_text().strip() if args.ap_password_file else secrets.token_urlsafe(12)
        value = {"v": 1, "ssid": "", "password": "", "server_url": args.server_url,
                 "token": args.token_file.read_text().strip(), "ap_password": password}
    return validate(value)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", required=True)
    parser.add_argument("--backup-dir", required=True, type=Path, help="New private directory outside the repository")
    parser.add_argument("--expected-id", required=True, help="Expected lowercase machine.unique_id hex")
    parser.add_argument("--ca-file", required=True, type=Path, help="Trusted root certificate in DER format")
    parser.add_argument("--server-url", help="Public wss:// hostname, optional port, /robot route")
    parser.add_argument("--token-file", type=Path, help="Private Bearer token file; never put the token on the command line")
    parser.add_argument("--ap-password-file", type=Path, help="Optional private WPA2 pairing key file")
    parser.add_argument("--config-file", type=Path, help="Optional private JSON containing all validated fields")
    args = parser.parse_args()
    value = settings(args)
    ca = args.ca_file.read_bytes()
    if not 100 <= len(ca) <= 4096 or ca[0] != 0x30:
        raise ValueError("Expected one DER-encoded root CA certificate of 100–4096 bytes.")
    backup = args.backup_dir.expanduser().resolve()
    if backup.is_relative_to(ROOT):
        raise ValueError("Device backups and pairing secrets must remain outside the repository.")
    backup.mkdir(parents=True, mode=0o700, exist_ok=False)
    os.chmod(backup, 0o700)
    private_write(backup / "pairing.json", (json.dumps(value, indent=2) + "\n").encode())
    report = {"port": args.port, "started_at": datetime.now(timezone.utc).isoformat(),
              "before": [], "installed": [], "complete": False, "tls_preflight": False,
              "mega_commands": False, "secrets_file": str(backup / "pairing.json")}
    before = {}
    try:
        with RawRepl(args.port) as repl:
            repl.enter()
            identity = remote_eval(repl, "__import__('ubinascii').hexlify(__import__('machine').unique_id()).decode()")
            board = remote_eval(repl, "__import__('os').uname().machine")
            if "Pico W" not in board or identity != args.expected_id:
                raise ValueError("Unexpected board identity")
            report.update(identity=identity, board=board, setup_ssid="ResPaw-Setup-" + identity[-4:].upper())
            for entry in inventory(repl):
                if entry["type"] == "error":
                    raise RuntimeError("Cannot back up device inventory")
                if entry["type"] != "file":
                    continue
                name = entry["path"]
                data = read_remote_file(repl, name)
                if data != read_remote_file(repl, name) or len(data) != entry["size"]:
                    raise RuntimeError("Backup mismatch: " + name)
                before[name] = data
                target = safe_destination(backup / "files", name)
                private_write(target, data)
                report["before"].append({"path": name, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()})
            for required in ("receiver.py", "telemetry.py", "main.py"):
                if required not in before:
                    raise ValueError("Existing receiver installation required: missing " + required)
            gateway_main = (SOURCE / "main.py").read_bytes()
            files = {path.relative_to(SOURCE).as_posix(): path.read_bytes()
                     for path in sorted((SOURCE / "respaw_gateway").rglob("*"))
                     if path.is_file() and path.suffix != ".pyc" and "__pycache__" not in path.parts}
            files["respaw_gateway/ca.der"] = ca
            protocol = (ROOT / "pico/source/respaw-v2/link_protocol.py").read_bytes()
            if "link_protocol.py" in before and before["link_protocol.py"] != protocol:
                raise ValueError("Installed link_protocol.py differs; inspect it before upgrading.")
            if "link_protocol.py" not in before:
                files["link_protocol.py"] = protocol
            if "respaw-main-before-gateway.py" not in before and before["main.py"] != gateway_main:
                files["respaw-main-before-gateway.py"] = before["main.py"]
            config_temporary = ".respaw-gateway-config-preflight.json"
            files[config_temporary] = (json.dumps(value) + "\n").encode()
            for name, data in files.items():
                upload(repl, name, data)
                report["installed"].append({"path": name, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()})
            # No GPIO, UART or socket is created by these module imports.
            # Fail closed if this actual firmware cannot validate this CA.
            execute(repl, "import gc, json, time, machine, sys\n"
                          "for _name in list(sys.modules):\n"
                          " if _name == 'respaw_gateway' or _name.startswith('respaw_gateway.'): del sys.modules[_name]\n"
                          "gc.collect()\nfrom respaw_gateway import gateway, config\n"
                          "_config=config.validate(json.load(open(%r)))\n_ctx=gateway.tls_context()\n"
                          "assert _ctx.verify_mode == __import__('ssl').CERT_REQUIRED\n"
                          "assert gateway.urlparse(_config['server_url']).protocol == 'wss'\n"
                          "_portal=gateway.Portal(_config)\nassert '<form' in _portal.page()\n"
                          "assert _config['token'] not in _portal.page()\n"
                          "del _ctx, _portal, _config\ngc.collect()" % config_temporary)
            report["tls_preflight"] = True
            # Give the RTC a correct warm-boot time. Cold boot uses vendored NTP.
            now = datetime.now(timezone.utc)
            execute(repl, "machine.RTC().datetime(%r)" % ((now.year, now.month, now.day, now.weekday(), now.hour, now.minute, now.second, 0),))
            # Configuration and main activate only after verified imports, CA
            # loading and all files. Existing max30102/receiver stay byte exact.
            execute(repl, "import os\nos.rename(%r, %r)\nos.sync()" % (config_temporary, CONFIG_PATH))
            for name, data in before.items():
                if (name in ("main.py", CONFIG_PATH, config_temporary) or name.startswith("respaw_gateway/")
                        or any(part.startswith(".respaw-upload-") for part in name.split("/"))):
                    continue
                if read_remote_file(repl, name) != data:
                    raise RuntimeError("Existing device file changed: " + name)
            upload(repl, "main.py", gateway_main)
            report["installed"].append({"path": "main.py", "bytes": len(gateway_main), "sha256": hashlib.sha256(gateway_main).hexdigest()})
            report["receiver_preserved"] = True
            report["complete"] = True
    finally:
        private_write(backup / "install-report.json", (json.dumps(report, indent=2) + "\n").encode())
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
