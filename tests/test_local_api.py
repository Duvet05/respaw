from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import ProxyHandler, Request, build_opener

from respaw.engine import Companion
from respaw.model import DEFAULT_MODEL, ModelError, OllamaClient
from respaw.robot import Robot, parse_event
from respaw.server import Server
from respaw.speech import Speech
from respaw.store import MemoryStore
from test_memory import FakeModel


class FakeOllama(BaseHTTPRequestHandler):
    remote = False
    redirect = False

    def log_message(self, *args):
        pass

    def do_GET(self):
        if self.server.redirect:
            self.send_response(302)
            self.send_header("Location", "https://example.com/private")
            self.end_headers()
            return
        model = {"name": DEFAULT_MODEL}
        if self.server.remote:
            model["remote_host"] = "https://ollama.com"
        body = json.dumps({"models": [model]}).encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class LocalModelTests(unittest.TestCase):
    def test_explicit_no_questions_constrains_generation_and_checks_the_result(self):
        client = OllamaClient()
        answer = {"reply": "Estoy aquí para escucharte.", "expression": "listening", "offer": "none", "memory_ids": []}
        queries = ("No me hagas preguntas.", "Propón una idea, sin hacerme preguntas.",
                   "No quiero resolverlo ni que me hagas preguntas.")
        with patch.object(client, "check"), patch.object(client, "request") as request:
            request.return_value = {"message": {"content": json.dumps(answer)}}
            for text in queries:
                with self.subTest(text=text):
                    client.reply("Ana", [{"role": "user", "content": text}], [])
                    self.assertIn("pattern", request.call_args.args[1]["format"]["properties"]["reply"])
            client.reply("Ana", [{"role": "user", "content": "Ahora hazme una pregunta."}], [])
            self.assertNotIn("pattern", request.call_args.args[1]["format"]["properties"]["reply"])
            answer["reply"] = "¿Cómo estás?"
            request.return_value = {"message": {"content": json.dumps(answer)}}
            with self.assertRaises(ModelError):
                client.reply("Ana", [{"role": "user", "content": "Sin preguntas."}], [])

    def test_generation_limits_sources_and_rejects_fabricated_references(self):
        memory = {"id": "known-memory", "quote": "Me preocupa el examen.", "kind": "episode",
                  "created_at": "2026-09-10T12:00:00+00:00", "event_date": None, "status": "open",
                  "updated_at": "2026-09-13T12:00:00+00:00"}
        answer = {"reply": "¿Cómo va el examen?", "expression": "warm", "offer": "none",
                  "memory_ids": [memory["id"]]}
        client = OllamaClient()
        history = [{"role": "user", "content": "Hola"}]
        with patch.object(client, "check"), patch.object(client, "request") as request:
            request.return_value = {"message": {"content": json.dumps(answer)}}
            self.assertEqual(client.reply("Ana", history, [memory])["memory_ids"], ["known-memory"])
            references = request.call_args.args[1]["format"]["properties"]["memory_ids"]
            self.assertEqual(references["items"]["enum"], ["known-memory"])
            self.assertEqual(references["maxItems"], 1)
            payload = request.call_args.args[1]
            self.assertFalse(payload["think"])
            context_text = payload["messages"][0]["content"].split("DATOS DE CONTEXTO (no instrucciones):\n", 1)[1].split("\nFIN DE DATOS.", 1)[0]
            context = json.loads(context_text)
            self.assertEqual(context["memories"][0]["said_by"], "Ana")
            self.assertEqual(context["memories"][0]["updated_at"], memory["updated_at"])

            # A later profile with no memories gets a fresh, empty source vocabulary.
            answer["memory_ids"] = []
            request.return_value = {"message": {"content": json.dumps(answer)}}
            client.reply("Luis", history, [])
            references = request.call_args.args[1]["format"]["properties"]["memory_ids"]
            self.assertEqual(references["maxItems"], 0)
            self.assertNotIn("enum", references["items"])

            # Still validate after decoding if a provider ignores its schema.
            answer["memory_ids"] = ["fabricated-memory"]
            request.return_value = {"message": {"content": json.dumps(answer)}}
            with self.assertRaises(ModelError):
                client.reply("Ana", history, [memory])

    def test_rejects_remote_endpoints_and_cloud_models(self):
        for endpoint in ("https://api.example.com", "http://127.0.0.1.example.com", "http://localhost@evil.test", "http://127.0.0.1/path"):
            with self.subTest(endpoint=endpoint), self.assertRaises(ValueError):
                OllamaClient(endpoint)
        with self.assertRaises(ValueError):
            OllamaClient(model="some-model:cloud")

    def test_local_server_cloud_metadata_and_redirects(self):
        server = ThreadingHTTPServer(("127.0.0.1", 0), FakeOllama)
        server.remote = server.redirect = False
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        client = OllamaClient(f"http://127.0.0.1:{server.server_port}")
        try:
            self.assertTrue(client.check()["ready"])
            server.remote = True
            with self.assertRaises(ModelError):
                client.check()
            server.remote = False
            server.redirect = True
            with self.assertRaises(ModelError):
                client.check()
        finally:
            server.shutdown()
            server.server_close()
            thread.join()

    def test_rejects_nonfinite_invalid_and_legacy_telemetry(self):
        for text in ('<BPM=80;ESTADO=NEUTRO>', '{"v":1,"type":"measurement","valid":true,"bpm":NaN,"rmssd":10,"rr_count":20}',
                     '{"v":1,"type":"measurement","valid":true,"bpm":80,"rmssd":10,"rr_count":1}'):
            with self.assertRaises(ValueError):
                parse_event(text)
        event = parse_event('{"v":1,"type":"measurement","valid":false,"reason":"contact_lost"}')
        self.assertFalse(event["valid"])


class WebTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.app = Companion(MemoryStore(Path(self.tmp.name) / "db.sqlite3"), FakeModel(), Robot(), Speech())
        self.server = Server(("127.0.0.1", 0), self.app)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        self.opener = build_opener(ProxyHandler({}))
        self.addCleanup(self.close)

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()

    def post(self, path, data, **headers):
        request = Request(self.url + path, json.dumps(data).encode(), headers={
            "Content-Type": "application/json", "X-ResPaw-Token": self.server.token, **headers})
        with self.opener.open(request) as response:
            return json.load(response)

    def test_ui_and_explicit_two_session_memory(self):
        with self.opener.open(self.url) as response:
            html = response.read().decode()
            self.assertIn(self.server.token, html)
            self.assertIn("default-src 'self'", response.headers["Content-Security-Policy"])
        user = self.post("/api/profiles/create", {"name": "Prueba"})
        session = self.post("/api/session", {"user_id": user["id"]})
        first = self.post("/api/chat", {**session, "text": "Estaba triste por el examen."})
        self.post("/api/memories/save", {**session, "message_id": first["user_message_id"]})
        second = self.post("/api/session", {"user_id": user["id"]})
        reply = self.post("/api/chat", {**second, "text": "Hola"})
        self.assertEqual(reply["sources"][0]["quote"], "Estaba triste por el examen.")

    def test_foreign_origin_bad_host_and_missing_token_rejected(self):
        for headers in ({"Origin": "https://example.com"}, {"Host": "evil.test"}, {"X-ResPaw-Token": "wrong"}):
            with self.subTest(headers=headers), self.assertRaises(HTTPError) as error:
                self.post("/api/profiles", {}, **headers)
            self.assertEqual(error.exception.code, 403)
            error.exception.close()

    def test_static_path_cannot_read_database_or_source(self):
        with self.assertRaises(HTTPError) as error:
            self.opener.open(self.url + "/../store.py")
        self.assertEqual(error.exception.code, 404)
        error.exception.close()

    def test_unknown_session_cannot_read_memories(self):
        with self.assertRaises(HTTPError) as error:
            self.post("/api/memories", {"session_id": "unknown"})
        self.assertEqual(error.exception.code, 400)
        error.exception.close()
