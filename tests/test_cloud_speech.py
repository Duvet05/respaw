import io
import json
import os
import threading
import traceback
import unittest
from email import policy
from email.message import Message
from email.parser import BytesParser
from unittest.mock import patch
from urllib.error import HTTPError, URLError

from respaw.cloud_speech import (
    CloudSpeech, DEFAULT_VOICE_ID, MAX_AUDIO_BYTES, MAX_SPEECH_BYTES,
    OPENAI_TRANSCRIPTIONS_URL, _NoRedirects,
)


class Response:
    def __init__(self, data, content_type="application/json", url=None, content_length=None,
                 status=200, on_read=None):
        self.body = io.BytesIO(data)
        self.headers = Message()
        self.headers["Content-Type"] = content_type
        if content_length is not None:
            self.headers["Content-Length"] = content_length
        self.url, self.status, self.on_read = url, status, on_read
        self.read_sizes = []
        self.closed = False

    def geturl(self):
        return self.url

    def read(self, size):
        self.read_sizes.append(size)
        result = self.body.read(size)
        if self.on_read:
            callback, self.on_read = self.on_read, None
            callback()
        return result

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.closed = True
        self.body.close()


class Opener:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def open(self, request, timeout):
        self.calls.append((request, timeout))
        result = self.responses.pop(0)
        if isinstance(result, Exception):
            raise result
        result.url = result.url or request.full_url
        return result


def transcript(text="Hola, estoy aquí."):
    return Response(json.dumps({"text": text}, ensure_ascii=False).encode("utf-8"))


class CloudSpeechTests(unittest.TestCase):
    def client(self, *responses, **kwargs):
        opener = Opener(*responses)
        client = CloudSpeech(openai_api_key="sk-openai-fixture-secret",
                             elevenlabs_api_key="eleven-fixture-secret", opener=opener, **kwargs)
        return client, opener

    def test_configuration_reports_only_configured_capabilities(self):
        with patch.dict(os.environ, {"OPENAI_API_KEY": "", "ELEVENLABS_API_KEY": "",
                                    "RESPAW_ELEVENLABS_VOICE_ID": ""}, clear=True):
            client = CloudSpeech(opener=Opener())
            self.assertFalse(client.status()["stt"] or client.status()["tts"])
            self.assertTrue(client.status()["voice_id"])
        with patch.dict(os.environ, {}, clear=True):
            client = CloudSpeech(opener=Opener())
            self.assertFalse(client.status()["stt"])
            self.assertFalse(client.status()["tts"])
            with self.assertRaisesRegex(ValueError, "OPENAI_API_KEY"):
                client.transcribe(b"audio", ".wav")
            with self.assertRaisesRegex(ValueError, "ELEVENLABS_API_KEY"):
                client.synthesize("Hola")
        with patch.dict(os.environ, {"OPENAI_API_KEY": "openai-secret", "ELEVENLABS_API_KEY": "eleven-secret",
                                    "RESPAW_ELEVENLABS_VOICE_ID": "My_voice-123"}, clear=True):
            client = CloudSpeech(opener=Opener())
            status = client.status()
            self.assertTrue(status["stt"] and status["tts"])
            self.assertEqual(status["voice_id"], "My_voice-123")
            self.assertEqual(status["playback"], "browser")
            self.assertEqual(status["stt_model"], "gpt-4o-mini-transcribe")
            self.assertEqual(status["tts_model"], "eleven_multilingual_v2")
            self.assertNotIn("openai-secret", json.dumps(status))
            self.assertNotIn("eleven-secret", json.dumps(status))
            disabled = CloudSpeech(openai_api_key="", elevenlabs_api_key="", opener=Opener())
            self.assertFalse(disabled.status()["stt"] or disabled.status()["tts"])

    def test_binary_multipart_uses_the_explicit_spanish_transcription_contract(self):
        audio = b"RIFF\x00\xff\r\nprivate audio"
        response = transcript("  Hola, estoy aquí.  ")
        client, opener = self.client(response, timeout=12)
        self.assertEqual(client.transcribe(audio, ".wav"), "Hola, estoy aquí.")
        request, timeout = opener.calls[0]
        self.assertEqual(timeout, 12)
        self.assertEqual(request.full_url, OPENAI_TRANSCRIPTIONS_URL)
        self.assertEqual(request.method, "POST")
        self.assertEqual(request.get_header("Authorization"), "Bearer sk-openai-fixture-secret")
        self.assertIsNone(request.get_header("Xi-api-key"))
        multipart = BytesParser(policy=policy.default).parsebytes(
            ("Content-Type: " + request.get_header("Content-type") + "\r\nMIME-Version: 1.0\r\n\r\n").encode()
            + request.data)
        parts = {part.get_param("name", header="content-disposition"): part for part in multipart.iter_parts()}
        self.assertEqual(set(parts), {"model", "language", "response_format", "file"})
        self.assertEqual(parts["model"].get_payload(decode=True), b"gpt-4o-mini-transcribe")
        self.assertEqual(parts["language"].get_payload(decode=True), b"es")
        self.assertEqual(parts["response_format"].get_payload(decode=True), b"json")
        self.assertEqual(parts["file"].get_filename(), "audio.wav")
        self.assertEqual(parts["file"].get_content_type(), "audio/wav")
        self.assertEqual(parts["file"].get_payload(decode=True), audio)
        self.assertTrue(response.closed)

    def test_browser_audio_containers_and_the_exact_upload_limit(self):
        for suffix in (".webm", ".ogg", ".mp4", ".m4a", ".mp3", ".flac", ".mpeg", ".mpga"):
            with self.subTest(suffix=suffix):
                client, opener = self.client(transcript())
                self.assertTrue(client.transcribe(memoryview(b"audio"), suffix))
                self.assertIn(("filename=\"audio" + suffix + "\"").encode(), opener.calls[0][0].data)
        client, opener = self.client(transcript())
        self.assertTrue(client.transcribe(b"a" * MAX_AUDIO_BYTES, ".wav"))
        self.assertGreater(len(opener.calls[0][0].data), MAX_AUDIO_BYTES)

    def test_invalid_audio_and_filename_injection_never_contact_provider(self):
        client, opener = self.client()
        for audio, suffix in ((b"", ".wav"), ("audio", ".wav"), (None, ".wav"),
                              (b"x" * (MAX_AUDIO_BYTES + 1), ".wav"), (b"audio", ".exe"),
                              (b"audio", '.wav\"\r\nX-Secret: 1'), (b"audio", [])):
            with self.subTest(suffix=suffix), self.assertRaises(ValueError):
                client.transcribe(audio, suffix)
        self.assertEqual(opener.calls, [])

    def test_multilingual_tts_returns_mp3_for_browser_without_local_playback(self):
        response = Response(b"ID3-fixture-mp3", "audio/mpeg")
        client, opener = self.client(response)
        self.assertEqual(client.synthesize("Hola, ¿cómo estás?"), (b"ID3-fixture-mp3", "audio/mpeg"))
        request, _ = opener.calls[0]
        self.assertEqual(request.full_url, "https://api.elevenlabs.io/v1/text-to-speech/" +
                         DEFAULT_VOICE_ID + "?output_format=mp3_44100_128")
        self.assertEqual(request.get_header("Xi-api-key"), "eleven-fixture-secret")
        self.assertIsNone(request.get_header("Authorization"))
        self.assertEqual(json.loads(request.data), {"text": "Hola, ¿cómo estás?", "model_id": "eleven_multilingual_v2"})
        self.assertEqual(request.get_header("Accept"), "audio/mpeg")
        self.assertTrue(response.closed)

    def test_text_and_configuration_cannot_escape_size_or_header_url_limits(self):
        client, opener = self.client()
        for text in ("", "  ", None, True, "x" * 1801, "bad\ud800"):
            with self.subTest(text_type=type(text).__name__), self.assertRaises(ValueError):
                client.synthesize(text)
        self.assertEqual(opener.calls, [])
        for kwargs in ({"voice_id": "../other?key=secret"}, {"voice_id": ""},
                       {"voice_id": "https://foreign.example"}, {"voice_id": "a" * 129},
                       {"openai_api_key": "secret\r\nHeader: evil"}, {"elevenlabs_api_key": "é"},
                       {"timeout": 0}, {"timeout": 121}, {"timeout": True},
                       {"timeout": float("nan")}, {"timeout": float("inf")}):
            with self.subTest(fields=list(kwargs)), self.assertRaises(ValueError):
                CloudSpeech(opener=Opener(), **kwargs)
        client, opener = self.client(Response(b"ID3", "audio/mp3"), voice_id="Spanish_voice-1")
        self.assertEqual(client.synthesize("ñ" * 1800)[1], "audio/mpeg")
        self.assertIn("/Spanish_voice-1?", opener.calls[0][0].full_url)

    def test_provider_errors_do_not_expose_keys_content_or_chained_errors(self):
        secret = "SECRET-user-content-and-key"
        for failure in (HTTPError("https://api.openai.com", 401, secret, {}, io.BytesIO(secret.encode())),
                        HTTPError("https://api.elevenlabs.io", 429, secret, {}, io.BytesIO(secret.encode())),
                        URLError(secret), OSError(secret), TimeoutError(secret)):
            for operation in ("stt", "tts"):
                with self.subTest(operation=operation, failure=type(failure).__name__):
                    client, _ = self.client(failure)
                    try:
                        client.transcribe(b"private audio", ".wav") if operation == "stt" else client.synthesize("private text")
                    except ValueError as error:
                        self.assertNotIn(secret, str(error))
                        self.assertNotIn(secret, "".join(traceback.format_exception(error)))
                        self.assertNotIn("private audio", str(error))
                        self.assertNotIn("private text", str(error))
                    else:
                        self.fail("Provider failure was accepted")

    def test_redirects_are_rejected_before_reading_foreign_content(self):
        with self.assertRaises(ValueError):
            _NoRedirects().redirect_request(None, None, 302, None, {}, "https://foreign.example/")
        for response in (Response(b"ID3", "audio/mpeg", url="https://foreign.example/"),
                         Response(b"ID3", "audio/mpeg", status=302)):
            client, _ = self.client(response)
            with self.assertRaises(ValueError):
                client.synthesize("Hola")
            self.assertEqual(response.read_sizes, [])
            self.assertTrue(response.closed)

    def test_wrong_formats_empty_and_oversized_responses_are_rejected(self):
        for response in (Response(b"ID3", "application/json"), Response(b"", "audio/mpeg"),
                         Response(b"x" * (MAX_SPEECH_BYTES + 1), "audio/mpeg"),
                         Response(b"ID3", "audio/mpeg", content_length=str(MAX_SPEECH_BYTES + 1)),
                         Response(b"ID3", "audio/mpeg", content_length="20"),
                         Response(b"ID3", "audio/mpeg", content_length="-1")):
            with self.subTest(content_type=response.headers["Content-Type"]):
                client, _ = self.client(response)
                with self.assertRaises(ValueError):
                    client.synthesize("Hola")
                self.assertTrue(response.closed)
                self.assertTrue(all(size <= 65_536 for size in response.read_sizes))
        client, _ = self.client(Response(b"x" * MAX_SPEECH_BYTES, "audio/mpeg"))
        self.assertEqual(len(client.synthesize("Hola")[0]), MAX_SPEECH_BYTES)

    def test_invalid_transcriptions_do_not_become_chat_messages(self):
        for data in (b"not json", b"\xff", b"[]", b"{}", b'{"text":false}', b'{"text":"  "}',
                     b'{"text":"hello\\ud800"}', b"[" * 1500 + b"]" * 1500,
                     json.dumps({"text": "a" * 2001}).encode(), b"x" * 32_769):
            with self.subTest(length=len(data)):
                client, _ = self.client(Response(data))
                with self.assertRaises(ValueError):
                    client.transcribe(b"audio", ".webm")

    def test_response_completed_after_deadline_is_not_returned(self):
        client, _ = self.client(Response(b"ID3", "audio/mpeg"), timeout=3)
        with patch("respaw.cloud_speech.time.monotonic", side_effect=[0, 1, 4]), self.assertRaises(ValueError):
            client.synthesize("Hola")

    def test_stop_discards_late_results_and_new_requests_can_succeed(self):
        for operation in ("stt", "tts"):
            with self.subTest(operation=operation):
                first = transcript() if operation == "stt" else Response(b"ID3", "audio/mpeg")
                second = transcript("Nueva frase") if operation == "stt" else Response(b"ID3-new", "audio/mpeg")
                client, _ = self.client(first, second)
                first.on_read = client.stop
                with self.assertRaisesRegex(ValueError, "cancelada"):
                    client.transcribe(b"audio", ".wav") if operation == "stt" else client.synthesize("Primera frase")
                self.assertTrue(first.closed)
                if operation == "stt":
                    self.assertEqual(client.transcribe(b"audio", ".wav"), "Nueva frase")
                else:
                    self.assertEqual(client.synthesize("Nueva frase"), (b"ID3-new", "audio/mpeg"))

    def test_same_operation_cannot_make_duplicate_concurrent_provider_calls(self):
        for operation in ("stt", "tts"):
            with self.subTest(operation=operation):
                entered, release = threading.Event(), threading.Event()
                response = transcript() if operation == "stt" else Response(b"ID3", "audio/mpeg")
                errors = []

                def blocked_read():
                    entered.set()
                    if not release.wait(2):
                        raise TimeoutError("Test synchronization failed")

                response.on_read = blocked_read
                client, opener = self.client(response)

                def request():
                    try:
                        client.transcribe(b"audio", ".wav") if operation == "stt" else client.synthesize("Hola")
                    except Exception as error:
                        errors.append(error)

                worker = threading.Thread(target=request)
                worker.start()
                try:
                    self.assertTrue(entered.wait(2))
                    with self.assertRaisesRegex(ValueError, "Ya se está"):
                        client.transcribe(b"audio", ".wav") if operation == "stt" else client.synthesize("Hola")
                    self.assertEqual(len(opener.calls), 1)
                finally:
                    release.set()
                    worker.join(2)
                self.assertFalse(worker.is_alive())
                self.assertEqual(errors, [])


if __name__ == "__main__":
    unittest.main()
