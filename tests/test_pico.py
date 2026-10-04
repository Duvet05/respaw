import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("pico_telemetry", ROOT / "pico/source/respaw-v2/telemetry.py")
telemetry = importlib.util.module_from_spec(spec)
spec.loader.exec_module(telemetry)


def heartbeat(uptime=10000, measuring=False):
    return {"v": 1, "type": "heartbeat", "board": "mega2560", "sensor": True,
            "audio": False, "measuring": measuring, "uptime_ms": uptime}


def measurement():
    return {"v": 1, "type": "measurement", "valid": True, "bpm": 75, "sdnn": 10,
            "rmssd": 20, "rr_count": 20, "rmssd_pairs": 19, "rejected": 0,
            "motion_checked": False, "window_ms": 30000}


def frame(event):
    return (json.dumps(event, separators=(",", ":")) + "\n").encode()


class PicoTests(unittest.TestCase):
    def test_commands_contact_and_actual_ack_error_frames_are_bounded_and_preserved(self):
        receiver = telemetry.Receiver()
        ready = {"v": 1, "type": "ready", "board": "mega2560", "sensor": True,
                 "audio": False, "commands": True}
        contact = {"v": 1, "type": "contact", "sensor": "fsr_a8", "pressed": True, "uptime_ms": 12}
        ack = {"v": 1, "type": "ack", "id": 32768, "command": "PING"}
        error = {"v": 1, "type": "error", "id": 1, "reason": "controller_busy"}
        self.assertEqual(receiver.feed(frame(ready) + frame(contact) + frame(ack) + frame(error), 10),
                         [ready, contact, ack, error])
        status = receiver.snapshot(11)
        self.assertTrue(status["capabilities"]["commands"])
        self.assertEqual(status["contact"], {"sensor": "fsr_a8", "pressed": True, "uptime_ms": 12})
        for event in ({**ready, "commands": 1}, {**contact, "pressed": 1}, {**contact, "sensor": "head"},
                      {**ack, "id": True}, {**ack, "command": "BAD"}, {**error, "reason": "invented"}):
            with self.subTest(event=event), self.assertRaises(ValueError):
                telemetry.parse_frame(json.dumps(event))
        self.assertIsNone(receiver.snapshot(6010)["contact"])
        receiver.feed(frame(ack), 6011)
        self.assertIsNone(receiver.snapshot(6011)["contact"])
        receiver.feed(frame(contact), 6012)
        receiver.feed(frame(ready), 6013)
        self.assertIsNone(receiver.snapshot(6013)["contact"])

    def test_production_mega_frames_are_accepted_by_pico(self):
        with tempfile.TemporaryDirectory(prefix="respaw-telemetry-") as temp:
            binary = Path(temp) / "frames"
            compiler = shutil.which("c++")
            self.assertIsNotNone(compiler)
            subprocess.run([compiler, "-std=c++11", "-Wall", "-Wextra", "-Werror",
                            "-fsanitize=address,undefined", "-fno-omit-frame-pointer",
                            "-I", str(ROOT / "mega2560/source/respaw-v2"),
                            str(ROOT / "tests/telemetry_fixture.cpp"), "-o", str(binary)], check=True)
            raw = subprocess.check_output([str(binary)], timeout=10)
        events = telemetry.Receiver().feed(raw, 0)
        self.assertEqual(len(events), 5)
        self.assertEqual(events[0]["sensor"], True)
        self.assertEqual(events[1]["measuring"], True)
        self.assertAlmostEqual(events[2]["bpm"], 75)
        self.assertAlmostEqual(events[2]["rmssd"], 20)
        self.assertEqual(events[3]["reason"], "contact_lost")
        self.assertEqual(events[4]["reason"], "insufficient_signal")
        self.assertNotIn("bpm", events[3])

    def test_fragmented_crlf_and_multiple_frames(self):
        receiver = telemetry.Receiver()
        raw = frame(heartbeat()).replace(b"\n", b"\r\n") + frame(measurement())
        events = []
        for index in range(0, len(raw), 7):
            events.extend(receiver.feed(raw[index:index + 7], 100))
        self.assertEqual(len(events), 2)
        self.assertEqual(receiver.snapshot(100)["measurement"]["bpm"], 75)

    def test_overflow_and_invalid_bytes_discard_whole_line(self):
        for prefix in (b"x" * 10000, b"\x00", b"\xff"):
            receiver = telemetry.Receiver()
            self.assertEqual(receiver.feed(prefix, 0), [])
            self.assertLessEqual(len(receiver.buffer), telemetry.MAX_LINE)
            self.assertEqual(receiver.feed(frame(measurement()), 1), [])
            self.assertEqual(receiver.rejected, 1)
            self.assertEqual(len(receiver.feed(frame(heartbeat()), 2)), 1)

    def test_bad_frames_do_not_refresh_link_or_stop_parser(self):
        receiver = telemetry.Receiver()
        receiver.feed(frame(heartbeat()), 0)
        for bad in (b"[]\n", b"not json\n", b'{"v":true,"type":"ready"}\n', b'{"v":2}\n'):
            self.assertEqual(receiver.feed(bad, 5900), [])
        self.assertFalse(receiver.snapshot(6000)["connected"])
        self.assertEqual(receiver.rejected, 4)

    def test_nonfinite_booleans_and_quality_failures_are_rejected(self):
        bad_values = (("bpm", float("nan")), ("rmssd", float("inf")), ("sdnn", -1),
                      ("bpm", True), ("bpm", 201), ("rr_count", 9), ("rr_count", 129),
                      ("rmssd_pairs", 8), ("rmssd_pairs", 20), ("rejected", 6),
                      ("window_ms", 1000), ("motion_checked", True), ("valid", 1))
        for field, value in bad_values:
            with self.subTest(field=field, value=value):
                event = measurement()
                event[field] = value
                receiver = telemetry.Receiver()
                self.assertEqual(receiver.feed(frame(event), 0), [])
                self.assertEqual(receiver.rejected, 1)

    def test_invalid_measurement_replaces_previous_values(self):
        receiver = telemetry.Receiver()
        receiver.feed(frame(measurement()), 0)
        invalid = measurement()
        invalid.update(valid=False, reason="cancelled", window_ms=12)
        self.assertEqual(receiver.feed(frame(invalid), 1), [])
        for key in ("bpm", "sdnn", "rmssd"):
            del invalid[key]
        receiver.feed(frame(invalid), 2)
        latest = receiver.snapshot(2)["measurement"]
        self.assertFalse(latest["valid"])
        self.assertNotIn("bpm", latest)

    def test_legacy_numbers_remain_unvalidated_without_emotional_label(self):
        receiver = telemetry.Receiver()
        events = receiver.feed(b"<BPM=75;SDNN=10;RMSSD=20;ESTADO=ANSIOSO>\r\n", 0)
        self.assertEqual(len(events), 1)
        self.assertFalse(events[0]["valid"])
        self.assertEqual(events[0]["legacy_values"]["bpm"], 75)
        self.assertNotIn("ANSIOSO", json.dumps(events[0]))
        self.assertNotIn("bpm", events[0])
        for bad in (b"<BPM=NaN;SDNN=10;RMSSD=20;ESTADO=X>\n",
                    b"<BPM=75;BPM=80;SDNN=10;RMSSD=20;ESTADO=X>\n"):
            self.assertEqual(receiver.feed(bad, 1), [])

    def test_link_timeout_does_not_resurrect_previous_measurement(self):
        receiver = telemetry.Receiver()
        receiver.feed(frame(measurement()), 0)
        self.assertIsNotNone(receiver.snapshot(5999)["measurement"])
        self.assertIsNone(receiver.snapshot(6000)["measurement"])
        receiver.feed(frame(heartbeat()), 7000)
        self.assertTrue(receiver.snapshot(7000)["connected"])
        self.assertIsNone(receiver.snapshot(7000)["measurement"])

    def test_measurement_expires_even_while_heartbeats_continue(self):
        receiver = telemetry.Receiver()
        receiver.feed(frame(measurement()), 0)
        for now in range(2000, 62000, 2000):
            receiver.feed(frame(heartbeat(uptime=now)), now)
        self.assertTrue(receiver.snapshot(60000)["connected"])
        self.assertIsNone(receiver.snapshot(60000)["measurement"])

    def test_capture_announcement_allows_bounded_silence(self):
        receiver = telemetry.Receiver()
        receiver.feed(frame(measurement()), 0)
        receiver.feed(frame(heartbeat(measuring=True)), 100)
        self.assertIsNone(receiver.snapshot(100)["measurement"])
        self.assertTrue(receiver.snapshot(30100)["connected"])
        self.assertFalse(receiver.snapshot(36100)["connected"])
        receiver.feed(frame(measurement()), 30100)
        self.assertFalse(receiver.snapshot(30100)["measuring"])
        self.assertTrue(receiver.snapshot(30100)["measurement"]["valid"])

    def test_mega_reboot_clears_measurement_but_uptime_wrap_does_not(self):
        receiver = telemetry.Receiver()
        receiver.feed(frame(heartbeat(uptime=10000)), 0)
        receiver.feed(frame(measurement()), 10)
        receiver.feed(frame(heartbeat(uptime=100)), 100)
        self.assertIsNone(receiver.snapshot(100)["measurement"])
        receiver.feed(frame(heartbeat(uptime=0xFFFFFF00)), 200)
        receiver.feed(frame(measurement()), 210)
        receiver.feed(frame(heartbeat(uptime=100)), 300)
        self.assertIsNotNone(receiver.snapshot(300)["measurement"])
        receiver.feed(frame({"v": 1, "type": "ready", "board": "mega2560", "sensor": True, "audio": False}), 400)
        self.assertIsNone(receiver.snapshot(400)["measurement"])

    def test_pico_ticks_wrap_uses_platform_difference(self):
        period = 1 << 30
        def wrapped_diff(now, before):
            return ((now - before + period // 2) % period) - period // 2
        with patch.object(telemetry, "ticks_diff", wrapped_diff):
            receiver = telemetry.Receiver()
            receiver.feed(frame(measurement()), period - 1000)
            self.assertTrue(receiver.snapshot(1000)["connected"])
            self.assertEqual(receiver.snapshot(1000)["measurement_age_ms"], 2000)
            self.assertFalse(receiver.snapshot(6000)["connected"])


if __name__ == "__main__":
    unittest.main()
