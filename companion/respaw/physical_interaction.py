"""One opt-in session can respond to a fresh touch without invoking an AI model."""

import threading
import time


class ContactResponseChanged(RuntimeError):
    pass


class PhysicalInteraction:
    def __init__(self, companion, *, clock=time.monotonic, poll_interval=0.1, background=True):
        self.app = companion
        self.clock = clock
        self.poll_interval = poll_interval
        self.background = background
        self.generation = 0
        self.owner = None
        self.present = 0
        self.armed_at = 0
        self.seen = None
        self.pending = None
        self.last_sent = None
        self.thread = None
        self._wake = threading.Event()
        self._closed = threading.Event()

    def _snapshot(self):
        try:
            snapshot = self.app.robot.snapshot()
            return snapshot if isinstance(snapshot, dict) else {}
        except (OSError, RuntimeError):
            return {}

    def _available(self, snapshot):
        return (not self._closed.is_set() and snapshot.get("ready") is True
                and snapshot.get("simulated") is False
                and getattr(self.app.robot, "supports_contact_response", False) is True
                and getattr(self.app.robot, "supports_command_cancellation", False) is True)

    @staticmethod
    def _pulse_id(snapshot):
        touch = snapshot.get("touch")
        value = touch.get("id") if isinstance(touch, dict) else None
        return value if isinstance(value, str) and 1 <= len(value) <= 128 else None

    def disarm_locked(self):
        # Advance even when already disabled: a pre-STOP enable must stay stale.
        self.generation += 1
        self.owner = None
        self.pending = None
        self.seen = None
        self.last_sent = None
        self._wake.set()

    def _maintain_locked(self, snapshot, now):
        if self.owner is not None and (self.owner not in self.app.sessions
                or self.owner != self.app.active_session_id
                or not self._available(snapshot) or now - self.present >= 30):
            self.disarm_locked()

    def _state_locked(self, session_id, snapshot):
        return {"enabled": self.owner == session_id and self.owner is not None,
                "available": self._available(snapshot), "generation": self.generation}

    def status(self, session_id):
        snapshot = self._snapshot()
        with self.app.lock:
            self._maintain_locked(snapshot, self.clock())
            return self._state_locked(session_id, snapshot)

    def poll_snapshot(self, session_id):
        snapshot = self._snapshot()
        with self.app.lock:
            self.app.session(session_id)
            now = self.clock()
            self._maintain_locked(snapshot, now)
            if self.owner == session_id:
                self.present = now
            return {**snapshot, "contact_response": self._state_locked(session_id, snapshot)}

    def set_enabled(self, session_id, enabled, generation=None):
        if type(enabled) is not bool:
            raise ValueError("La respuesta al contacto requiere enabled booleano.")
        with self.app.lock:
            self.app.session(session_id)
            if not enabled:
                self.disarm_locked()
            elif type(generation) is not int:
                raise ValueError("Actualiza el estado del robot antes de activar el contacto.")
            elif (generation != self.generation or session_id != self.app.active_session_id
                    or self._closed.is_set()):
                raise ContactResponseChanged("La sesión cambió o se detuvo; actualiza el estado antes de activar el contacto.")
        # Snapshot is outside the conversation lock, so STOP can invalidate the
        # generation even if a transport takes time to provide its state.
        snapshot = self._snapshot()
        with self.app.lock:
            if enabled:
                if (generation != self.generation or session_id != self.app.active_session_id
                        or self._closed.is_set()):
                    raise ContactResponseChanged("La sesión cambió o se detuvo; actualiza el estado antes de activar el contacto.")
                if not self._available(snapshot):
                    raise ValueError("El robot no está listo para responder al contacto.")
                self.generation += 1
                self.owner = session_id
                self.present = self.clock()
                self.armed_at = self.present
                self.seen = self._pulse_id(snapshot)
                self.pending = None
                self.last_sent = None
                if self.background and self.thread is None:
                    self.thread = threading.Thread(target=self._run, daemon=True, name="respaw-contact-response")
                    self.thread.start()
                self._wake.set()
            return {"contact_response": self._state_locked(session_id, snapshot)}

    def chat_started_locked(self):
        if self.owner is not None:
            self.generation += 1
            self.pending = None

    def chat_finished_snapshot(self):
        with self.app.lock:
            armed = self.owner is not None
        return self._snapshot() if armed else None

    def chat_finished_locked(self, snapshot):
        if self.owner is not None:
            # A pulse observed after inference may have happened while busy.
            # Move the event-time cutoff as well as consuming the cached ID.
            self.armed_at = self.clock()
            self.pending = None
        if snapshot is not None:
            pulse_id = self._pulse_id(snapshot)
            if pulse_id is not None:
                self.seen = pulse_id

    def _busy_locked(self):
        return any(session.request_id is not None for session in self.app.sessions.values())

    def step(self):
        """Consume the latest pulse and dispatch at most one bounded gesture."""
        observed_at = self.clock()
        snapshot = self._snapshot()
        now = self.clock()
        with self.app.lock:
            self._maintain_locked(snapshot, now)
            if self.owner is None:
                return
            pulse_id = self._pulse_id(snapshot)
            if self._busy_locked():
                if pulse_id is not None:
                    self.seen = pulse_id
                self.pending = None
                return
            if pulse_id is not None and pulse_id != self.seen:
                self.seen = pulse_id
                age = snapshot["touch"].get("age_ms")
                self.pending = ((pulse_id, observed_at + (2000 - age) / 1000)
                                if type(age) is int and 0 <= age <= 2000
                                and age / 1000 + now - observed_at <= 2
                                and age / 1000 <= observed_at - self.armed_at else None)
            if self.pending is None:
                return
            if now > self.pending[1]:
                self.pending = None
                return
            if self.last_sent is not None and now - self.last_sent < 1:
                return
            owner, generation = self.owner, self.generation
            session_generation = self.app.sessions[owner].generation
            expires = self.pending[1]
            self.pending = None
            self.last_sent = now

        def cancelled():
            current = self._snapshot()
            with self.app.lock:
                self._maintain_locked(current, self.clock())
                session = self.app.sessions.get(owner)
                return (self._closed.is_set() or self.owner != owner or self.generation != generation
                        or self.clock() > expires or session is None
                        or session.generation != session_generation or self._busy_locked())

        if cancelled():
            return
        try:
            self.app.robot.command("FACE", "listening", cancelled=cancelled)
        except (OSError, RuntimeError):
            # Consume the pulse once. Never replay a gesture after an uncertain ACK.
            pass

    def _run(self):
        while not self._closed.is_set():
            with self.app.lock:
                armed = self.owner is not None
            if armed:
                self.step()
                self._wake.wait(self.poll_interval)
            else:
                self._wake.wait()
            self._wake.clear()

    def close(self):
        with self.app.lock:
            self._closed.set()
            self.disarm_locked()
            thread = self.thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=5)
            if thread.is_alive():
                raise RuntimeError("No se pudo cerrar la respuesta al contacto.")
