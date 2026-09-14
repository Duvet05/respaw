from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class FirmwareTests(unittest.TestCase):
    def test_portable_firmware_core_with_sanitizers(self):
        compiler = shutil.which("c++")
        self.assertIsNotNone(compiler, "Instala las herramientas C++ para verificar el firmware.")
        with tempfile.TemporaryDirectory(prefix="respaw-core-") as temp:
            target = Path(temp) / "core-test"
            subprocess.run([compiler, "-std=c++11", "-Wall", "-Wextra", "-Werror",
                            "-fsanitize=address,undefined", "-fno-omit-frame-pointer",
                            "-I", str(ROOT / "mega2560/source/respaw-v2"),
                            str(ROOT / "tests/firmware_core_test.cpp"), "-o", str(target)], check=True)
            subprocess.run([str(target)], check=True, timeout=10)
