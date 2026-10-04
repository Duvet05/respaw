"""Small localhost UI/API; no external assets, analytics or browser speech services."""

import base64
import binascii
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import secrets
from urllib.parse import urlsplit

from .engine import Cancelled
from .model import ModelError

ASSETS = Path(__file__).parent / "web"


class Server(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address, companion):
        if address[0] != "127.0.0.1":
            raise ValueError("La interfaz debe escuchar solo en 127.0.0.1.")
        self.companion = companion
        self.token = secrets.token_urlsafe(32)
        super().__init__(address, Handler)


class Handler(BaseHTTPRequestHandler):
    def setup(self):
        super().setup()
        self.connection.settimeout(20)

    def log_message(self, format, *args):
        # No utterances, profile IDs or memory contents in request logs.
        pass

    def send(self, data, status=200, content_type="application/json; charset=utf-8"):
        body = json.dumps(data, ensure_ascii=False, allow_nan=False).encode() if isinstance(data, (dict, list)) else data
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self' data:; media-src 'self' blob:; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def authorized(self, api=False):
        port = self.server.server_address[1]
        hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}
        host = self.headers.get("Host", "")
        origin = self.headers.get("Origin")
        if host not in hosts or origin is not None and origin != "http://" + host:
            self.send({"error": "Origen no permitido."}, 403)
            return False
        if api and not secrets.compare_digest(self.headers.get("X-ResPaw-Token", ""), self.server.token):
            self.send({"error": "Abre la aplicación local para iniciar la sesión."}, 403)
            return False
        return True

    def do_GET(self):
        if not self.authorized():
            return
        path = urlsplit(self.path).path
        assets = {"/": ("index.html", "text/html"), "/app.js": ("app.js", "text/javascript"), "/style.css": ("style.css", "text/css")}
        if path in assets:
            name, mime = assets[path]
            content = (ASSETS / name).read_bytes().replace(b"__CSRF_TOKEN__", self.server.token.encode())
            self.send(content, content_type=mime + "; charset=utf-8")
        else:
            self.send({"error": "Ruta no encontrada."}, 404)

    def do_POST(self):
        if not self.authorized(api=True):
            return
        try:
            if self.headers.get_content_type() != "application/json":
                raise ValueError("Se requiere JSON.")
            size = int(self.headers.get("Content-Length", "0"))
            limit = 14_000_000 if self.path == "/api/transcribe" else 16_000
            if not 1 <= size <= limit:
                raise ValueError("Petición vacía o demasiado grande.")
            body = json.loads(self.rfile.read(size))
            if not isinstance(body, dict):
                raise ValueError("JSON no válido.")
            self.send(self.dispatch(self.path, body))
        except Cancelled as error:
            self.send({"error": str(error), "cancelled": True}, 409)
        except (ValueError, KeyError, TypeError, binascii.Error) as error:
            self.send({"error": str(error) if isinstance(error, ValueError) else "Faltan datos o no son válidos."}, 400)
        except ModelError as error:
            self.send({"error": str(error), "model_unavailable": True}, 503)
        except (OSError, RuntimeError):
            self.send({"error": "No se pudo completar la operación local."}, 503)

    def dispatch(self, path, body):
        app = self.server.companion
        if path == "/api/status":
            try:
                model = app.model.check()
            except ModelError as error:
                model = {"ready": False, "error": str(error), "model": app.model.model}
            return {"model": model, "speech": app.speech.status(), "robot": app.robot.snapshot(),
                    "retrieval": dict(app.store.semantic_status)}
        if path == "/api/profiles":
            return app.store.profiles()
        if path == "/api/profiles/create":
            return app.store.create_profile(body["name"])
        if path == "/api/session":
            return app.start(body.get("user_id") or None)
        session_id = body["session_id"]
        with app.lock:
            app.session(session_id)
        if path == "/api/chat":
            return app.chat(session_id, body["text"])
        if path == "/api/stop":
            return app.stop(session_id)
        if path == "/api/contact-response":
            return app.contact_response(session_id, body["enabled"], body.get("generation"))
        if path == "/api/memories":
            return app.memories(session_id)
        if path == "/api/memories/save":
            return app.save_memory(session_id, body["message_id"], body.get("topic", ""),
                                   body.get("kind", "episode"), body.get("event_date"))
        if path == "/api/memories/change":
            return app.change_memory(session_id, body.get("memory_id"), body.get("quote"),
                                     body.get("status", "open"), body.get("kind"))
        if path == "/api/activity":
            return app.choose_activity(session_id, body["activity"])
        if path == "/api/measure":
            app.robot.command("MEASURE")
            return app.robot.snapshot()
        if path == "/api/robot":
            return app.robot_snapshot(session_id)
        if path == "/api/speak":
            # Only speak the last actual assistant response, not arbitrary submitted text.
            with app.lock:
                session = app.session(session_id)
                last = session.history[-1] if session.history else None
                if not last or last["role"] != "assistant":
                    raise ValueError("No hay una respuesta para leer.")
                text, message_id, generation = last["content"], last["id"], session.generation
                if not callable(getattr(app.speech, "synthesize", None)):
                    app.speech.speak(text)
                    return {"speaking": True}
            audio, mime = app.speech.synthesize(text)
            with app.lock:
                if (session.generation != generation or not session.history
                        or session.history[-1]["id"] != message_id):
                    raise Cancelled("Audio descartado: la conversación cambió o se detuvo.")
            return {"speaking": True, "audio": base64.b64encode(audio).decode("ascii"), "mime": mime}
        if path == "/api/transcribe":
            audio = base64.b64decode(body["audio"], validate=True)
            return {"text": app.speech.transcribe(audio, body["suffix"])}
        raise ValueError("Ruta no encontrada.")
