import base64
import json
from pathlib import Path
import tempfile
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import ProxyHandler, Request, build_opener

from respaw.engine import Companion
from respaw.robot import Robot
from respaw.server import Server
from respaw.store import MemoryStore
from test_memory import FakeModel


class VoiceFixture:
    def __init__(self):
        self.entered = threading.Event()
        self.release = threading.Event()
        self.release.set()
        self.texts = []
        self.stopped = False

    def synthesize(self, text):
        self.texts.append(text)
        self.entered.set()
        if not self.release.wait(3):
            raise RuntimeError("Fixture voice timeout")
        return b"fixture audio", "audio/mpeg"

    def stop(self):
        self.stopped = True


class CloudVoiceApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.speech = VoiceFixture()
        self.app = Companion(MemoryStore(Path(self.temp.name) / "memory.sqlite3"),
                             FakeModel(), Robot(), self.speech)
        self.server = Server(("127.0.0.1", 0), self.app)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.session = self.app.start()["session_id"]
        self.answer = self.app.chat(self.session, "Hola")

    def tearDown(self):
        self.speech.release.set()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(2)
        self.temp.cleanup()

    def post(self, path, **fields):
        body = json.dumps({"session_id": self.session, **fields}).encode()
        request = Request(f"http://127.0.0.1:{self.server.server_port}/api/{path}", data=body,
                          headers={"Content-Type": "application/json", "X-ResPaw-Token": self.server.token})
        with build_opener(ProxyHandler({})).open(request, timeout=4) as response:
            return json.load(response)

    def test_returns_only_last_assistant_audio_without_synthesizing_submitted_text(self):
        response = self.post("speak", text="Texto que no se debe sintetizar")
        self.assertEqual(base64.b64decode(response["audio"]), b"fixture audio")
        self.assertEqual(response["mime"], "audio/mpeg")
        self.assertEqual(self.speech.texts, [self.answer["reply"]])

    def test_stop_can_finish_during_synthesis_and_late_audio_is_discarded(self):
        self.speech.release.clear()
        errors = []

        def synthesize():
            try:
                self.post("speak")
            except HTTPError as error:
                errors.append((error.code, json.load(error)))

        worker = threading.Thread(target=synthesize)
        worker.start()
        try:
            self.assertTrue(self.speech.entered.wait(2))
            self.assertTrue(self.post("stop")["stopped"])
            self.assertTrue(self.speech.stopped)
        finally:
            self.speech.release.set()
            worker.join(3)
        self.assertFalse(worker.is_alive())
        self.assertEqual(errors[0][0], 409)
        self.assertTrue(errors[0][1]["cancelled"])
        self.assertNotIn("audio", errors[0][1])
