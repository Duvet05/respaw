from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from pico_recover import RawRepl


class RawReplWriteTests(unittest.TestCase):
    def test_partial_and_temporarily_blocked_writes_preserve_long_source(self):
        repl = RawRepl("unused")
        repl.fd = 42
        received = bytearray()
        calls = 0

        def write(fd, data):
            nonlocal calls
            self.assertEqual(fd, 42)
            calls += 1
            if calls == 2:
                raise BlockingIOError
            size = min(17, len(data))
            received.extend(data[:size])
            return size

        source = "value = 'á'\n" * 100
        with patch("pico_recover.os.write", side_effect=write), patch("pico_recover.time.sleep"), \
                patch("pico_recover.select.select"), patch.object(repl, "_read_until", return_value=b"OKdone\x04\x04>"):
            self.assertEqual(repl.exec(source), (b"done", b""))
        self.assertEqual(received, source.encode("utf-8") + b"\x04")

    def test_unwritable_port_times_out_without_sending_execution_marker(self):
        repl = RawRepl("unused")
        repl.fd = 42
        with patch("pico_recover.time.monotonic", side_effect=(0, 0, 2)), \
                patch("pico_recover.os.write", side_effect=BlockingIOError), patch("pico_recover.select.select"):
            with self.assertRaises(TimeoutError):
                repl.exec("print('not sent')", timeout=1)
