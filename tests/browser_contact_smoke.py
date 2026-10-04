"""Contact consent in the real browser/API with a hardware transport fixture.

Run: PYTHONPATH=companion /tmp/respaw-browser-venv/bin/python tests/browser_contact_smoke.py
No robot, model provider, microphone or speech service is contacted.
"""

import argparse
import copy
import os
from pathlib import Path
import tempfile
import threading
import time

from playwright.sync_api import sync_playwright

from respaw.engine import Companion
from respaw.server import Server
from respaw.store import MemoryStore


class UnusedModel:
    model = "fixture de contacto (sin conversación)"

    def __init__(self):
        self.calls = 0

    def check(self):
        return {"ready": True, "model": self.model}

    def reply(self, *args, **kwargs):
        self.calls += 1
        raise AssertionError("A contact must not request a model response")


class UnusedSpeech:
    def status(self):
        return {"tts": False, "stt": False}

    def stop(self):
        pass

    def synthesize(self, *args):
        raise AssertionError("A contact must not synthesize speech")


class PhysicalRobotFixture:
    """A declared fixture exposing the production snapshot/command contract."""
    supports_command_cancellation = True
    supports_contact_response = True

    def __init__(self):
        self.lock = threading.Lock()
        self.simulated, self.ready, self.commands_supported = True, True, True
        self.touch, self.touch_at, self.contact = None, None, None
        self.commands = []
        self.sequence = 0
        self.listening = threading.Event()

    def snapshot(self):
        with self.lock:
            pulse = None
            if self.touch_at is not None:
                age = int((time.monotonic() - self.touch_at) * 1000)
                if age <= 2000:
                    pulse = {"id": self.touch, "age_ms": age}
            return {"simulated": self.simulated, "ready": self.ready,
                    "transport": "wifi", "capabilities": {"commands": self.commands_supported},
                    "contact": copy.deepcopy(self.contact) if self.ready else None,
                    "touch": pulse if self.ready else None, "expression": "neutral",
                    "measurement": None, "error": None}

    def command(self, command, argument="", cancelled=None):
        if cancelled and cancelled():
            raise RuntimeError("Fixture command cancelled")
        with self.lock:
            self.commands.append((command, argument))
            if command == "FACE" and argument == "listening":
                self.listening.set()

    def press(self):
        with self.lock:
            self.sequence += 1
            self.touch, self.touch_at = "fixture-touch-" + str(self.sequence), time.monotonic()
            self.contact = {"v": 1, "type": "contact", "sensor": "fsr_a8",
                            "pressed": True, "uptime_ms": self.sequence * 1000}

    def listening_count(self):
        with self.lock:
            return self.commands.count(("FACE", "listening"))


def wait_checkbox(page, enabled, checked=False):
    page.wait_for_function("""([enabled, checked]) => {
      const control = document.querySelector('#contact-response');
      return control.disabled !== enabled && control.checked === checked;
    }""", arg=[enabled, checked])


def hold_enable(page):
    held = []

    def intercept(route):
        if route.request.post_data_json.get("enabled") is True and not held:
            response = route.fetch()
            assert response.status == 200
            held.append((route, response, route.request.post_data_json))
        else:
            route.continue_()

    page.route("**/api/contact-response", intercept)
    return held


def release_enable(page, held):
    route, response, body = held[0]
    route.fulfill(response=response)
    page.unroute("**/api/contact-response")
    return body


def wait_for_hold(page, held):
    deadline = time.monotonic() + 5
    while not held and time.monotonic() < deadline:
        page.wait_for_timeout(50)
    assert held, "The response was not held"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--browser", choices=("chrome", "chromium"), default="chrome")
    args = parser.parse_args()
    screenshot = os.environ.get("RESPAW_SCREENSHOT_DIR")
    output = Path(screenshot) if screenshot else None
    if output:
        output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="respaw-browser-contact-") as temporary:
        robot, model = PhysicalRobotFixture(), UnusedModel()
        app = Companion(MemoryStore(Path(temporary) / "memory.sqlite3"), model, robot, UnusedSpeech())
        server = Server(("127.0.0.1", 0), app)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{server.server_port}"
        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(
                    channel="chrome" if args.browser == "chrome" else None, headless=True)
                page = browser.new_page(viewport={"width": 1440, "height": 1000})
                page.set_default_timeout(6500)
                errors, external, requests = [], [], []
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.on("request", lambda request: requests.append(request.url)
                        if request.url.startswith(base) else external.append(request.url))
                page.goto(base)
                page.get_by_text("Robot y conexión", exact=True).click()
                control = page.get_by_label("Responder al contacto", exact=True)
                wait_checkbox(page, False)

                # Simulation, incompatible firmware and a disconnected robot
                # never offer consent for physical actions.
                robot.simulated, robot.commands_supported = False, False
                page.wait_for_timeout(3200)
                wait_checkbox(page, False)
                robot.commands_supported, robot.ready = True, False
                page.wait_for_timeout(3200)
                wait_checkbox(page, False)
                robot.ready = True
                wait_checkbox(page, True)

                robot.press()  # Already present at arming: establish baseline.
                with page.expect_response(lambda response: response.url.endswith("/api/contact-response")) as enabled:
                    control.check()
                consent = enabled.value.json()["contact_response"]
                assert consent["enabled"] and consent["available"]
                assert type(consent["generation"]) is int
                assert type(enabled.value.request.post_data_json["generation"]) is int
                wait_checkbox(page, True, True)
                page.wait_for_timeout(500)
                assert robot.listening_count() == 0, "A pre-existing press became a new action"
                robot.press()
                assert robot.listening.wait(3), "A fresh contact did not reach FACE listening"
                page.wait_for_timeout(700)
                assert robot.listening_count() == 1, "A repeated snapshot duplicated the gesture"
                if output:
                    page.screenshot(path=str(output / "contact-response-desktop.png"), full_page=True)

                with page.expect_response(lambda response: response.url.endswith("/api/stop")) as stopped:
                    page.get_by_role("button", name="Detener", exact=True).click()
                stop_state = stopped.value.json()["contact_response"]
                assert not stop_state["enabled"] and stop_state["generation"] > consent["generation"]
                wait_checkbox(page, True)
                robot.press()
                page.wait_for_timeout(500)
                assert robot.listening_count() == 1, "Contact acted after STOP"

                # A provider or proxy can deliver a successful old response
                # after STOP. It must not repaint the consent as enabled.
                held = hold_enable(page)
                control.check()
                page.wait_for_function("() => document.querySelector('#contact-response').disabled")
                wait_for_hold(page, held)
                with page.expect_response(lambda response: response.url.endswith("/api/stop")):
                    page.get_by_role("button", name="Detener", exact=True).click()
                stale_body = release_enable(page, held)
                wait_checkbox(page, True)
                auth = {"X-ResPaw-Token": page.locator('meta[name="respaw-token"]').get_attribute("content")}
                rejected = page.request.post(base + "/api/contact-response", data=stale_body, headers=auth)
                assert rejected.status == 409, ("An old consent token was not rejected after STOP", rejected.status, rejected.text())
                assert rejected.json().get("cancelled")

                # The previous session's late success must not affect the new
                # session, even when the robot remains available throughout.
                held = hold_enable(page)
                control.check()
                page.wait_for_function("() => document.querySelector('#contact-response').disabled")
                wait_for_hold(page, held)
                with page.expect_response(lambda response: response.url.endswith("/api/session")) as changed:
                    page.get_by_role("button", name="Comenzar otra conversación").click()
                old_body = release_enable(page, held)
                assert changed.value.json()["session_id"] != old_body["session_id"]
                wait_checkbox(page, True)
                rejected = page.request.post(base + "/api/contact-response", data=old_body, headers=auth)
                assert rejected.status == 409, ("A previous session was not rejected", rejected.status, rejected.text())

                # A poll can carry the previous armed state after STOP, too.
                with page.expect_response(lambda response: response.url.endswith("/api/contact-response")):
                    control.check()
                wait_checkbox(page, True, True)
                held_poll = []

                def intercept_poll(route):
                    if not held_poll:
                        held_poll.append((route, route.fetch()))
                    else:
                        route.continue_()

                page.route("**/api/robot", intercept_poll)
                wait_for_hold(page, held_poll)
                with page.expect_response(lambda response: response.url.endswith("/api/stop")):
                    page.get_by_role("button", name="Detener", exact=True).click()
                route, response = held_poll[0]
                assert response.json()["contact_response"]["enabled"]
                route.fulfill(response=response)
                page.unroute("**/api/robot")
                page.wait_for_timeout(250)
                wait_checkbox(page, True)

                # STOP from another API client does not change this page's
                # epoch. Server generations must still reject an older poll.
                with page.expect_response(lambda response: response.url.endswith("/api/contact-response")):
                    control.check()
                wait_checkbox(page, True, True)
                held_poll.clear()
                page.route("**/api/robot", intercept_poll)
                wait_for_hold(page, held_poll)
                active_session = held_poll[0][0].request.post_data_json["session_id"]
                external_stop = page.request.post(base + "/api/stop",
                    data={"session_id": active_session}, headers=auth)
                assert external_stop.status == 200
                wait_checkbox(page, True)
                route, response = held_poll[0]
                assert response.json()["contact_response"]["enabled"]
                route.fulfill(response=response)
                page.unroute("**/api/robot")
                page.wait_for_timeout(250)
                assert not control.is_checked(), "An old poll hid another client's STOP"

                with page.expect_response(lambda response: response.url.endswith("/api/contact-response")):
                    control.check()
                wait_checkbox(page, True, True)
                robot.ready = False
                wait_checkbox(page, False)
                robot.ready = True
                wait_checkbox(page, True)
                assert not control.is_checked(), "Reconnection automatically rearmed contact response"

                # A failed status request also removes visible consent.
                with page.expect_response(lambda response: response.url.endswith("/api/contact-response")):
                    control.check()
                wait_checkbox(page, True, True)
                page.route("**/api/robot", lambda route: route.fulfill(
                    status=503, content_type="application/json", body='{"error":"Fixture offline"}'))
                wait_checkbox(page, False)
                page.unroute("**/api/robot")
                page.set_viewport_size({"width": 390, "height": 844})
                assert control.is_visible()
                assert page.evaluate("() => document.documentElement.scrollWidth <= window.innerWidth")
                if output:
                    page.screenshot(path=str(output / "contact-response-mobile.png"), full_page=True)

                assert model.calls == 0
                assert not any(url.endswith(("/api/chat", "/api/speak", "/api/memories/save")) for url in requests)
                assert all(not session.history for session in app.sessions.values())
                assert not errors, errors
                assert not external, external
                page.close()
                browser.close()
                print("Browser contact: compatible physical fixture, explicit consent, fresh pressure, "
                      "no duplicate gesture, STOP, stale consent rejection, late toggles/polls/session change, "
                      "disconnect/reconnect, failed polling and mobile layout passed; no model or speech calls.")
        finally:
            server.shutdown()
            server.server_close()
            thread.join(2)
            close = getattr(app, "close", None)
            if close:
                close()


if __name__ == "__main__":
    main()
