"""Optional cloud transcription and browser audio, with no local audio playback.

Credentials remain on the companion server. ``stop`` discards results of requests
already in progress; it cannot revoke a request already received by a provider.
"""

import json
import math
import os
import re
import secrets
import ssl
import threading
import time
from urllib.request import HTTPRedirectHandler, HTTPSHandler, ProxyHandler, Request, build_opener


OPENAI_TRANSCRIPTIONS_URL = "https://api.openai.com/v1/audio/transcriptions"
ELEVENLABS_TTS_URL = "https://api.elevenlabs.io/v1/text-to-speech/"
TRANSCRIPTION_MODEL = "gpt-4o-mini-transcribe"
SPEECH_MODEL = "eleven_multilingual_v2"
DEFAULT_VOICE_ID = "JBFqnCBsd6RMkjVDRZzb"
MAX_AUDIO_BYTES = 10_000_000
MAX_SPEECH_BYTES = 4_000_000
MAX_TRANSCRIPTION_BYTES = 32_768
MAX_TEXT_CHARS = 1800

_AUDIO_TYPES = {
    ".flac": "audio/flac", ".mp3": "audio/mpeg", ".mp4": "audio/mp4",
    ".mpeg": "audio/mpeg", ".mpga": "audio/mpeg", ".m4a": "audio/mp4",
    ".ogg": "audio/ogg", ".wav": "audio/wav", ".webm": "audio/webm",
}


class _NoRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError("El servicio de voz devolvió una redirección no permitida.")


class _Stopped(ValueError):
    pass


class CloudSpeech:
    def __init__(self, openai_api_key=None, elevenlabs_api_key=None, voice_id=None,
                 timeout=30, opener=None):
        self._openai_key = self._key(openai_api_key, "OPENAI_API_KEY")
        self._elevenlabs_key = self._key(elevenlabs_api_key, "ELEVENLABS_API_KEY")
        self.voice_id = ((os.environ.get("RESPAW_ELEVENLABS_VOICE_ID") or DEFAULT_VOICE_ID)
                         if voice_id is None else voice_id)
        if not isinstance(self.voice_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", self.voice_id):
            raise ValueError("RESPAW_ELEVENLABS_VOICE_ID no válido.")
        if (isinstance(timeout, bool) or not isinstance(timeout, (int, float))
                or not math.isfinite(timeout) or not 1 <= timeout <= 120):
            raise ValueError("El tiempo de espera de voz debe estar entre 1 y 120 segundos.")
        self.timeout = timeout
        self._opener = (opener if opener is not None else build_opener(
            ProxyHandler({}), _NoRedirects(), HTTPSHandler(context=ssl.create_default_context())))
        self._lock = threading.Lock()
        self._generation = 0
        self._transcribing = threading.Lock()
        self._synthesizing = threading.Lock()

    @staticmethod
    def _key(value, name):
        value = os.environ.get(name, "") if value is None else value
        if (not isinstance(value, str) or len(value) > 512
                or any(not 33 <= ord(char) <= 126 for char in value)):
            raise ValueError("Credencial de voz no válida.")
        return value

    def status(self):
        """Report configured capabilities without contacting either provider."""
        return {"stt": bool(self._openai_key), "tts": bool(self._elevenlabs_key),
                "provider": "cloud", "playback": "browser",
                "stt_model": TRANSCRIPTION_MODEL, "tts_model": SPEECH_MODEL,
                "voice_id": self.voice_id}

    def stop(self):
        with self._lock:
            self._generation += 1

    def _current_generation(self):
        with self._lock:
            return self._generation

    def _check_active(self, generation):
        with self._lock:
            if generation != self._generation:
                raise _Stopped("Operación de voz cancelada.")

    def _request(self, request, generation, limit, content_types, message):
        """Read only bounded successful responses from the original HTTPS URL."""
        self._check_active(generation)
        deadline = time.monotonic() + self.timeout
        try:
            with self._opener.open(request, timeout=self.timeout) as response:
                self._check_active(generation)
                if response.geturl() != request.full_url or getattr(response, "status", 200) != 200:
                    raise ValueError("Respuesta de voz no válida.")
                content_type = response.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
                if content_type not in content_types:
                    raise ValueError("Formato de respuesta de voz no válido.")
                size = response.headers.get("Content-Length")
                if size is not None and (not size.isdecimal() or int(size) > limit):
                    raise ValueError("Respuesta de voz demasiado larga.")
                read = getattr(response, "read1", response.read)
                data = bytearray()
                while True:
                    self._check_active(generation)
                    if time.monotonic() >= deadline:
                        raise TimeoutError("Tiempo de espera agotado.")
                    chunk = read(min(65_536, limit + 1 - len(data)))
                    self._check_active(generation)
                    if time.monotonic() >= deadline:
                        raise TimeoutError("Tiempo de espera agotado.")
                    if not isinstance(chunk, bytes):
                        raise ValueError("Respuesta de voz no válida.")
                    data.extend(chunk)
                    if len(data) > limit:
                        raise ValueError("Respuesta de voz demasiado larga.")
                    if not chunk:
                        break
                if not data:
                    raise ValueError("Respuesta de voz vacía.")
                if size is not None and len(data) != int(size):
                    raise ValueError("Respuesta de voz incompleta.")
                self._check_active(generation)
                return bytes(data)
        except Exception:
            # Provider errors may contain credentials or user content. Do not
            # read their bodies or include their messages/chained tracebacks.
            self._check_active(generation)
            raise ValueError(message) from None

    def transcribe(self, audio, suffix):
        if not self._openai_key:
            raise ValueError("Configura OPENAI_API_KEY para usar el micrófono en la nube.")
        size = audio.nbytes if isinstance(audio, memoryview) else len(audio) if isinstance(audio, (bytes, bytearray)) else 0
        if (not isinstance(suffix, str) or suffix not in _AUDIO_TYPES
                or not 1 <= size <= MAX_AUDIO_BYTES):
            raise ValueError("Audio no válido o demasiado largo.")
        if not self._transcribing.acquire(blocking=False):
            raise ValueError("Ya se está transcribiendo un audio.")
        try:
            generation = self._current_generation()
            audio = bytes(audio)
            boundary = "respaw-" + secrets.token_hex(24)
            while boundary.encode("ascii") in audio:
                boundary = "respaw-" + secrets.token_hex(24)
            parts = []
            for name, value in (("model", TRANSCRIPTION_MODEL), ("language", "es"), ("response_format", "json")):
                parts.append(("--%s\r\nContent-Disposition: form-data; name=\"%s\"\r\n\r\n%s\r\n"
                              % (boundary, name, value)).encode("ascii"))
            parts.extend([
                ("--%s\r\nContent-Disposition: form-data; name=\"file\"; filename=\"audio%s\"\r\n"
                 "Content-Type: %s\r\n\r\n" % (boundary, suffix, _AUDIO_TYPES[suffix])).encode("ascii"),
                audio, ("\r\n--%s--\r\n" % boundary).encode("ascii"),
            ])
            request = Request(OPENAI_TRANSCRIPTIONS_URL, data=b"".join(parts), method="POST", headers={
                "Authorization": "Bearer " + self._openai_key,
                "Content-Type": "multipart/form-data; boundary=" + boundary,
                "Accept": "application/json",
            })
            result = self._request(request, generation, MAX_TRANSCRIPTION_BYTES, {"application/json"},
                                   "No se pudo transcribir el audio en la nube.")
            try:
                decoded = json.loads(result)
                text = decoded.get("text") if isinstance(decoded, dict) else None
                if not isinstance(text, str) or not 1 <= len(text.strip()) <= 2000:
                    raise ValueError("Transcripción no válida.")
                text = text.strip()
                text.encode("utf-8")
            except (ValueError, UnicodeError, RecursionError):
                raise ValueError("No pude obtener una frase clara de ese audio.") from None
            self._check_active(generation)
            return text
        finally:
            self._transcribing.release()

    def synthesize(self, text):
        if not self._elevenlabs_key:
            raise ValueError("Configura ELEVENLABS_API_KEY para usar la voz en la nube.")
        if not isinstance(text, str) or not text.strip() or len(text) > MAX_TEXT_CHARS:
            raise ValueError("Texto de voz no válido.")
        try:
            payload = json.dumps({"text": text, "model_id": SPEECH_MODEL}, ensure_ascii=False).encode("utf-8")
        except UnicodeError:
            raise ValueError("Texto de voz no válido.") from None
        if not self._synthesizing.acquire(blocking=False):
            raise ValueError("Ya se está generando una voz.")
        try:
            generation = self._current_generation()
            request = Request(ELEVENLABS_TTS_URL + self.voice_id + "?output_format=mp3_44100_128",
                              data=payload, method="POST", headers={
                                  "xi-api-key": self._elevenlabs_key,
                                  "Content-Type": "application/json", "Accept": "audio/mpeg",
                              })
            result = self._request(request, generation, MAX_SPEECH_BYTES, {"audio/mpeg", "audio/mp3"},
                                   "No se pudo generar la voz en la nube.")
            self._check_active(generation)
            return result, "audio/mpeg"
        finally:
            self._synthesizing.release()
