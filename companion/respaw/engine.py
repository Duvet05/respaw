"""Session state and memory lifecycle independent from the language model."""

from dataclasses import dataclass, field
import threading
import time
import uuid

from .model import ACTIVITIES, validate_reply
from .physical_interaction import ContactResponseChanged, PhysicalInteraction
from .store import clean_text


class Cancelled(RuntimeError):
    pass


@dataclass
class Session:
    id: str
    user_id: str | None
    name: str
    history: list = field(default_factory=list)
    recent_memory_ids: list = field(default_factory=list)
    generation: int = 0
    request_id: str | None = None
    touched: float = field(default_factory=time.monotonic)


class Companion:
    def __init__(self, store, model, robot, speech=None):
        self.store, self.model, self.robot, self.speech = store, model, robot, speech
        self.sessions = {}
        self.lock = threading.RLock()
        self.active_session_id = None
        self.physical = PhysicalInteraction(self)

    def start(self, user_id=None):
        name = self.store.profile(user_id)["name"] if user_id else "Invitado"
        session = Session(str(uuid.uuid4()), user_id, name)
        with self.lock:
            # No conversation transcripts on disk; discard inactive sessions.
            for key in list(self.sessions):
                old = self.sessions[key]
                if time.monotonic() - old.touched > 3600 and old.request_id is None:
                    del self.sessions[key]
            if len(self.sessions) >= 100:
                raise ValueError("Demasiadas sesiones abiertas; reinicia la aplicación.")
            self.physical.disarm_locked()
            self.sessions[session.id] = session
            self.active_session_id = session.id
        return {"session_id": session.id, "name": name, "guest": user_id is None}

    def session(self, session_id):
        if not isinstance(session_id, str) or session_id not in self.sessions:
            raise ValueError("Inicia una sesión primero.")
        session = self.sessions[session_id]
        session.touched = time.monotonic()
        return session

    def chat(self, session_id, text):
        text = clean_text(text, 2000)
        with self.lock:
            session = self.session(session_id)
            if session.request_id is not None:
                raise ValueError("Espera la respuesta o pulsa Detener.")
            request_id = str(uuid.uuid4())
            session.request_id = request_id
            self.physical.chat_started_locked()
            generation = session.generation
            opening = not session.history
            user_message = {"id": str(uuid.uuid4()), "role": "user", "content": text}
            session.history.append(user_message)
            # Bound context while retaining a few recent exchanges.
            history, remaining = [], 6000
            for message in reversed(session.history[-10:]):
                if len(message["content"]) > remaining:
                    break
                history.insert(0, message.copy())
                remaining -= len(message["content"])
            recent_ids = session.recent_memory_ids.copy()
        started = time.monotonic()
        try:
            # Embedding inference must not hold the lock needed by STOP or forgetting.
            memories = self.store.retrieve(session.user_id, text, opening=opening,
                                           recent_ids=recent_ids) if session.user_id else []
            with self.lock:
                if session.generation != generation or session.request_id != request_id:
                    raise Cancelled("Respuesta descartada: la sesión cambió o se detuvo.")
            context = ({"robot_context": self.robot.snapshot()}
                       if getattr(self.model, "supports_robot_context", False) else {})
            answer = validate_reply(self.model.reply(session.name, history, memories, **context), memories)
            cancelled = lambda: session.generation != generation or session.request_id != request_id
            # Network acknowledgements can take seconds. STOP must be able to
            # invalidate this turn while the transport waits for the Mega.
            if getattr(self.robot, "supports_command_cancellation", False):
                try:
                    self.robot.command("FACE", answer["expression"], cancelled=cancelled)
                except (RuntimeError, OSError):
                    pass
            with self.lock:
                if cancelled():
                    raise Cancelled("Respuesta descartada: la sesión cambió o se detuvo.")
                if not getattr(self.robot, "supports_command_cancellation", False):
                    try:
                        self.robot.command("FACE", answer["expression"])
                    except (RuntimeError, OSError):
                        pass
                session.history.append({"id": str(uuid.uuid4()), "role": "assistant", "content": answer["reply"]})
                session.history = session.history[-40:]
                sources = [m for m in memories if m["id"] in answer["memory_ids"]]
                session.recent_memory_ids = [m["id"] for m in sources if m["kind"] == "episode"]
                return {**answer, "user_message_id": user_message["id"],
                        "latency_ms": round((time.monotonic() - started) * 1000),
                        "sources": sources, "retrieval": dict(self.store.semantic_status)}
        finally:
            physical_snapshot = self.physical.chat_finished_snapshot()
            with self.lock:
                if session.request_id == request_id:
                    self.physical.chat_finished_locked(physical_snapshot)
                    session.request_id = None

    def stop(self, session_id):
        with self.lock:
            session = self.session(session_id)
            session.generation += 1
            session.request_id = None
            self.physical.disarm_locked()
            if self.speech:
                self.speech.stop()
        try:
            self.robot.command("STOP")
        except (RuntimeError, OSError):
            pass
        return {"stopped": True, "contact_response": self.physical.status(session_id)}

    def contact_response(self, session_id, enabled, generation=None):
        try:
            return self.physical.set_enabled(session_id, enabled, generation)
        except ContactResponseChanged as error:
            raise Cancelled(str(error)) from None

    def robot_snapshot(self, session_id):
        return self.physical.poll_snapshot(session_id)

    def close(self):
        self.physical.close()

    def save_memory(self, session_id, message_id, topic="", kind="episode", event_date=None):
        with self.lock:
            session = self.session(session_id)
            if not session.user_id:
                raise ValueError("El modo invitado no guarda recuerdos. Crea o elige un perfil.")
            for message in session.history:
                if message["id"] == message_id and message["role"] == "user":
                    return self.store.save(session.user_id, message["content"], session.id,
                                           topic=topic, kind=kind, event_date=event_date)
        raise ValueError("Solo puedes guardar una intervención propia de esta sesión.")

    def memories(self, session_id):
        with self.lock:
            session = self.session(session_id)
            return self.store.list_memories(session.user_id) if session.user_id else []

    def change_memory(self, session_id, memory_id, quote=None, status="open", kind=None):
        with self.lock:
            session = self.session(session_id)
            if not session.user_id:
                raise ValueError("El modo invitado no tiene recuerdos.")
            if quote is None:
                self.store.forget(session.user_id, memory_id)
            else:
                self.store.revise(session.user_id, memory_id, quote, status, kind)
            # In-flight generation and recent turns cannot resurrect deleted/corrected facts.
            for other in self.sessions.values():
                if other.user_id == session.user_id:
                    other.generation += 1
                    other.request_id = None
                    other.history.clear()
                    other.recent_memory_ids.clear()
            if self.speech:
                self.speech.stop()
            self.physical.disarm_locked()
        return {"changed": True, "conversation_reset": True}

    def choose_activity(self, session_id, activity):
        with self.lock:
            self.session(session_id)
            if activity not in ACTIVITIES:
                raise ValueError("Actividad desconocida.")
        self.stop(session_id)
        with self.lock:
            session = self.session(session_id)
            session.history.append({"id": str(uuid.uuid4()), "role": "user",
                                    "content": "He elegido: " + ACTIVITIES[activity]["name"]})
        return ACTIVITIES[activity]
