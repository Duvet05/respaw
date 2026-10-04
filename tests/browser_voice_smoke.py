"""Browser voice integration using real WAV bytes and delayed local fixtures.

Run with the existing Chrome and a Playwright environment:
  PYTHONPATH=companion python tests/browser_voice_smoke.py
No model, speech API, microphone or robot hardware is used.
"""

import argparse
from array import array
import base64
import io
import math
import os
from pathlib import Path
import sys
import tempfile
import threading
import wave
from unittest.mock import patch
from urllib.error import URLError

from playwright.sync_api import sync_playwright

from respaw.cloud_model import CloudClient
from respaw.cloud_speech import CloudSpeech, DEFAULT_VOICE_ID
from respaw.engine import Companion
from respaw.robot import Robot
from respaw.server import Server
from respaw.store import MemoryStore
from test_memory import FakeModel


def wav_fixture():
    samples = array("h", (int(300 * math.sin(2 * math.pi * 220 * index / 16000))
                          for index in range(3 * 16000)))
    if sys.byteorder != "little":
        samples.byteswap()
    output = io.BytesIO()
    with wave.open(output, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(16000)
        audio.writeframes(samples.tobytes())
    return output.getvalue()


class CloudModelFixture(FakeModel):
    model = "fixture nube (sin llamadas API)"

    def check(self):
        return {"ready": True, "configured": True, "provider": "openai", "model": self.model}


class CloudVoiceFixture:
    def __init__(self):
        self.audio = wav_fixture()
        self.texts = []
        self.stop_count = 0
        self.entered = threading.Event()
        self.release = threading.Event()
        self.release.set()

    def status(self):
        return {"provider": "cloud", "playback": "browser", "tts": True, "stt": False}

    def hold(self):
        self.entered.clear()
        self.release.clear()

    def synthesize(self, text):
        self.texts.append(text)
        self.entered.set()
        if not self.release.wait(15):
            raise RuntimeError("Fixture synthesis timeout")
        # Intentionally return even after STOP: the server and browser must also
        # guard against a provider whose response cannot be cancelled remotely.
        return self.audio, "audio/wav"

    def stop(self):
        self.stop_count += 1


def send_message(page, text):
    page.wait_for_function("() => !document.querySelector('#send').disabled")
    page.get_by_label("Tu mensaje").fill(text)
    page.get_by_role("button", name="Enviar").click()


def wait_for_playback(page):
    page.wait_for_function("""() => {
      const audio = document.querySelector('#voice-audio');
      return !audio.hidden && audio.src.startsWith('blob:') && audio.readyState >= 2
        && !audio.paused && audio.duration > 2.9 && audio.duration < 3.1;
    }""")


def assert_stopped(page):
    page.wait_for_function("""() => {
      const audio = document.querySelector('#voice-audio');
      return audio.hidden && audio.paused && !audio.getAttribute('src');
    }""")


def check_missing_credentials(browser, temporary, output):
    class NoRequests:
        calls = 0

        def open(self, *args, **kwargs):
            self.calls += 1
            raise URLError("Fixture blocked an unexpected provider request")

    denied = NoRequests()
    with patch.dict(os.environ, {"OPENAI_API_KEY": ""}):
        model = CloudClient()
    model.opener = denied
    speech = CloudSpeech(openai_api_key="", elevenlabs_api_key="", voice_id=DEFAULT_VOICE_ID, opener=denied)
    robot = Robot()
    app = Companion(MemoryStore(Path(temporary) / "unconfigured.sqlite3"), model, robot, speech)
    server = Server(("127.0.0.1", 0), app)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    page = browser.new_page(viewport={"width": 1440, "height": 1000})
    errors, external = [], []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.on("request", lambda request: external.append(request.url) if not request.url.startswith(base) else None)
    try:
        with page.expect_response(lambda response: response.url.endswith("/api/status")) as checked:
            page.goto(base)
        status = checked.value.json()
        assert status["model"]["provider"] == "openai"
        assert status["model"]["configured"] is False and status["model"]["ready"] is False
        assert status["speech"]["provider"] == "cloud"
        assert status["speech"]["tts"] is False and status["speech"]["stt"] is False
        page.get_by_text("● Conversación en nube", exact=True).wait_for()
        assert page.locator("#voice").is_disabled() and page.locator("#mic").is_disabled()
        assert "local" not in page.locator("#model-status").inner_text().lower()
        with page.expect_response(lambda response: response.url.endswith("/api/chat")) as rejected:
            send_message(page, "Prueba sin claves configuradas.")
        assert rejected.value.status == 503
        assert rejected.value.json()["model_unavailable"]
        if output:
            page.screenshot(path=str(output / "cloud-configuration-pending.png"), full_page=True)
        assert denied.calls == 0, "Missing credentials attempted a provider request"
        assert not errors, errors
        assert not external, external
    finally:
        page.close()
        server.shutdown()
        server.server_close()
        thread.join(2)
        robot.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--browser", choices=("chrome", "chromium"), default="chrome")
    args = parser.parse_args()
    screenshot = os.environ.get("RESPAW_SCREENSHOT_DIR")
    output = Path(screenshot) if screenshot else None
    if output:
        output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="respaw-browser-voice-") as temporary:
        speech, robot = CloudVoiceFixture(), Robot()
        app = Companion(MemoryStore(Path(temporary) / "memory.sqlite3"), CloudModelFixture(), robot, speech)
        server = Server(("127.0.0.1", 0), app)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{server.server_port}"
        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(
                    channel="chrome" if args.browser == "chrome" else None, headless=True,
                    args=["--autoplay-policy=no-user-gesture-required"])
                page = browser.new_page(viewport={"width": 1440, "height": 1000})
                page.set_default_timeout(5000)
                errors, external = [], []
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.on("request", lambda request: external.append(request.url)
                        if not request.url.startswith(base) and not request.url.startswith("blob:" + base) else None)
                page.goto(base)
                page.get_by_text("● Conversación en nube", exact=True).wait_for()
                assert "proveedores en nube" in page.locator("#privacy-notice").inner_text()
                assert page.locator("#voice").is_enabled()
                page.get_by_label("Leer las respuestas en voz alta").check()

                with page.expect_response(lambda response: response.url.endswith("/api/speak")) as spoken:
                    send_message(page, "Primera respuesta con voz de prueba.")
                first = spoken.value
                assert first.status == 200
                body = first.json()
                assert body["mime"] == "audio/wav"
                assert base64.b64decode(body["audio"]) == speech.audio
                original_session = first.request.post_data_json["session_id"]
                wait_for_playback(page)
                if output:
                    page.screenshot(path=str(output / "cloud-voice-desktop.png"), full_page=True)
                with page.expect_response(lambda response: response.url.endswith("/api/stop")) as stopped:
                    page.get_by_role("button", name="Detener", exact=True).click()
                assert stopped.value.json()["stopped"]
                assert speech.stop_count >= 1
                assert_stopped(page)

                speech.hold()
                send_message(page, "Detener mientras llega el audio.")
                assert speech.entered.wait(3), "Synthesis did not start"
                with page.expect_response(lambda response: response.url.endswith("/api/speak")) as late_stop:
                    with page.expect_response(lambda response: response.url.endswith("/api/stop"), timeout=2000) as stopped:
                        page.get_by_role("button", name="Detener", exact=True).click()
                    assert stopped.value.json()["stopped"]
                    assert_stopped(page)
                    speech.release.set()
                assert late_stop.value.status == 409
                assert late_stop.value.json()["cancelled"]
                assert "audio" not in late_stop.value.json()
                assert_stopped(page)

                speech.hold()
                send_message(page, "Cambiar de sesión mientras llega el audio.")
                assert speech.entered.wait(3), "Synthesis did not start"
                with page.expect_response(lambda response: response.url.endswith("/api/speak")) as late_session:
                    with page.expect_response(lambda response: response.url.endswith("/api/session")) as changed:
                        page.get_by_role("button", name="Comenzar otra conversación").click()
                    assert changed.value.json()["session_id"] != original_session
                    page.wait_for_function("() => document.querySelector('#messages').children.length === 0")
                    assert_stopped(page)
                    speech.release.set()
                assert late_session.value.status == 409
                assert late_session.value.json()["cancelled"]
                assert "audio" not in late_session.value.json()
                assert_stopped(page)
                assert "Sesión de invitado" in page.locator("#notice").inner_text()

                with page.expect_response(lambda response: response.url.endswith("/api/speak")) as fresh:
                    send_message(page, "Voz en la sesión nueva.")
                assert fresh.value.status == 200
                wait_for_playback(page)
                page.set_viewport_size({"width": 390, "height": 844})
                assert page.evaluate("() => document.documentElement.scrollWidth <= window.innerWidth")
                assert page.locator("#voice-audio").is_visible()
                assert page.get_by_role("button", name="Comenzar otra conversación").is_visible()
                if output:
                    page.screenshot(path=str(output / "cloud-voice-mobile.png"), full_page=True)
                with page.expect_response(lambda response: response.url.endswith("/api/stop")) as disabled:
                    page.get_by_label("Leer las respuestas en voz alta").uncheck()
                assert disabled.value.json()["stopped"]
                assert_stopped(page)
                assert len(speech.texts) == 4
                assert not errors, errors
                assert not external, external
                check_missing_credentials(browser, temporary, output)
                browser.close()
                print("Browser voice: WAV decoding/playback, STOP during playback and synthesis, "
                      "session change with late audio, fresh voice, mobile layout, missing credentials "
                      "and no external requests passed.")
        finally:
            speech.release.set()
            server.shutdown()
            server.server_close()
            thread.join(2)
            robot.close()


if __name__ == "__main__":
    main()
