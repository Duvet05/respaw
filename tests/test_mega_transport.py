import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class MegaTransportTests(unittest.TestCase):
    def test_production_dispatch_routes_and_contact_edges_with_sanitizers(self):
        compiler = shutil.which("c++")
        self.assertIsNotNone(compiler)
        with tempfile.TemporaryDirectory(prefix="respaw-mega-transport-") as temp:
            binary = Path(temp) / "transport"
            subprocess.run([compiler, "-std=c++11", "-Wall", "-Wextra", "-Werror",
                            "-fsanitize=address,undefined", "-fno-omit-frame-pointer",
                            "-I", str(ROOT / "mega2560/source/respaw-v2"),
                            str(ROOT / "tests/mega_transport_fixture.cpp"), "-o", str(binary)], check=True)
            raw = subprocess.check_output([str(binary)], timeout=10)
        frames = [json.loads(line) for line in raw.splitlines()]
        self.assertEqual([frame["type"] for frame in frames],
                         ["ready", "heartbeat", "contact", "contact", "ack", "error"])
        self.assertTrue(frames[0]["commands"])
        self.assertTrue(frames[1]["commands"])
        self.assertEqual(frames[2], {"v": 1, "type": "contact", "sensor": "fsr_a8", "pressed": True, "uptime_ms": 124})
        self.assertEqual(frames[3]["pressed"], False)
        self.assertEqual(frames[4], {"v": 1, "type": "ack", "id": 42, "command": "FACE"})
        self.assertEqual(frames[5]["reason"], "controller_busy")


if __name__ == "__main__":
    unittest.main()
