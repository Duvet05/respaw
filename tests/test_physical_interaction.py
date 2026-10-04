from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
import tempfile
import threading
import unittest
from unittest.mock import Mock

from respaw.engine import Cancelled, Companion
from respaw.physical_interaction import PhysicalInteraction
from respaw.server import Handler
from respaw.store import MemoryStore
from test_memory import FakeModel


class Clock:
    def __init__(self):
        self.now = 100.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


class ContactRobot:
    supports_contact_response = True
    supports_command_cancellation = True

    def __init__(self):
        self.state = {"ready": True, "simulated": False, "transport": "wifi",
                      "capabilities": {"commands": True},
                      "contact": {"sensor": "fsr_a8", "pressed": False}, "touch": None}
        self.accepted = []
        self.before_send = None
        self.snapshot_once = None

    def pulse(self, identity, age_ms=0, pressed=False):
        self.state["touch"] = {"id": identity, "age_ms": age_ms}
        self.state["contact"]["pressed"] = pressed

    def snapshot(self):
        callback, self.snapshot_once = self.snapshot_once, None
        if callback:
            callback()
        return deepcopy(self.state)

    def command(self, command, argument="", cancelled=None):
        if command == "FACE" and self.before_send:
            self.before_send(cancelled)
        if cancelled and cancelled():
            raise RuntimeError("Fixture cancelled before dispatch")
        self.accepted.append((command, argument))


class PhysicalInteractionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = MemoryStore(Path(self.temp.name) / "memory.sqlite3")
        self.model = FakeModel()
        self.model.reply = Mock(side_effect=AssertionError("Contact must not invoke a model"))
        self.robot, self.clock = ContactRobot(), Clock()
        self.app = Companion(self.store, self.model, self.robot)
        self.app.physical = PhysicalInteraction(self.app, clock=self.clock, background=False)
        self.session_id = self.app.start()["session_id"]
        self.addCleanup(self.temp.cleanup)
        self.addCleanup(self.app.close)

    def arm(self, session_id=None):
        session_id = session_id or self.session_id
        generation = self.app.robot_snapshot(session_id)["contact_response"]["generation"]
        return self.app.contact_response(session_id, True, generation)["contact_response"]

    def gesture(self, identity, age_ms=0):
        self.robot.pulse(identity, age_ms)
        self.app.physical.step()

    def test_explicit_opt_in_requires_real_ready_cancelable_transport(self):
        self.assertIsNone(self.app.physical.thread)
        self.gesture("without-consent")
        self.assertEqual(self.robot.accepted, [])
        for update in ({"simulated": True}, {"ready": False}):
            original = deepcopy(self.robot.state)
            self.robot.state.update(update)
            with self.assertRaises(ValueError):
                self.arm()
            self.robot.state = original
        for attribute in ("supports_contact_response", "supports_command_cancellation"):
            setattr(self.robot, attribute, False)
            with self.assertRaises(ValueError):
                self.arm()
            setattr(self.robot, attribute, True)
        self.assertIsNone(self.app.physical.thread)
        self.assertTrue(self.arm()["enabled"])

    def test_strict_bool_and_generation_and_stale_enable_after_stop(self):
        initial = self.app.robot_snapshot(self.session_id)["contact_response"]
        for enabled, generation in ((1, initial["generation"]), ("true", initial["generation"]),
                                    (True, None), (True, True), (True, "1")):
            with self.subTest(enabled=enabled, generation=generation), self.assertRaises(ValueError):
                self.app.contact_response(self.session_id, enabled, generation)
        self.app.stop(self.session_id)
        with self.assertRaises(Cancelled):
            self.app.contact_response(self.session_id, True, initial["generation"])
        result = self.app.contact_response(self.session_id, False, "ignored")
        self.assertFalse(result["contact_response"]["enabled"])

    def test_brief_press_release_pulse_works_but_arm_baseline_and_held_do_not(self):
        self.robot.pulse("before-consent", pressed=True)
        self.arm()
        for _ in range(3):
            self.clock.advance(0.2)
            self.app.physical.step()
        self.assertEqual(self.robot.accepted, [])
        self.gesture("new-press-already-released")
        self.assertFalse(self.robot.state["contact"]["pressed"])
        self.assertEqual(self.robot.accepted, [("FACE", "listening")])
        self.clock.advance(1.1)
        self.app.physical.step()
        self.assertEqual(len(self.robot.accepted), 1)

    def test_pre_consent_pulse_delivered_late_is_consumed(self):
        self.arm()
        self.clock.advance(0.5)
        self.gesture("pre-consent-delivered-late", age_ms=700)
        self.clock.advance(0.1)
        self.gesture("pre-consent-delivered-late", age_ms=0)
        self.assertEqual(self.robot.accepted, [])
        self.gesture("post-consent", age_ms=100)
        self.assertEqual(self.robot.accepted, [("FACE", "listening")])

    def test_touch_age_and_id_are_bounded_and_bool_age_is_rejected(self):
        self.arm()
        self.clock.advance(3)
        for index, age in enumerate((-1, True, "0", 2001)):
            self.gesture("invalid-" + str(index), age)
        self.gesture("", 0)
        self.gesture("x" * 129, 0)
        self.assertEqual(self.robot.accepted, [])
        self.gesture("edge-at-ttl", 2000)
        self.assertEqual(self.robot.accepted, [("FACE", "listening")])

    def test_cooldown_coalesces_without_replaying_expired_pulses(self):
        self.arm()
        self.clock.advance(3)
        self.gesture("first")
        self.clock.advance(0.2)
        self.gesture("second")
        self.clock.advance(0.2)
        self.gesture("third")
        self.assertEqual(len(self.robot.accepted), 1)
        self.clock.advance(0.7)
        self.app.physical.step()
        self.assertEqual(len(self.robot.accepted), 2)
        self.clock.advance(0.2)
        self.gesture("expires-before-cooldown", 1900)
        self.clock.advance(1)
        self.app.physical.step()
        self.assertEqual(len(self.robot.accepted), 2)

    def test_expiry_is_checked_again_before_transport_dispatch(self):
        self.arm()
        self.robot.before_send = lambda cancelled: self.clock.advance(2.1)
        self.gesture("expires-during-connect")
        self.assertEqual(self.robot.accepted, [])

    def test_snapshot_delay_also_consumes_the_pulse_ttl(self):
        self.arm()
        self.clock.advance(1)
        self.robot.snapshot_once = lambda: self.clock.advance(2.1)
        self.gesture("expires-during-snapshot")
        self.assertEqual(self.robot.accepted, [])

    def test_contact_never_calls_model_or_memory_or_changes_history(self):
        self.store.retrieve = Mock(side_effect=AssertionError("Contact must not retrieve memory"))
        self.store.save = Mock(side_effect=AssertionError("Contact must not save memory"))
        self.arm()
        before = self.app.sessions[self.session_id].touched
        self.gesture("first")
        self.clock.advance(1.2)
        self.gesture("second")
        self.assertEqual(self.app.sessions[self.session_id].history, [])
        self.assertEqual(self.app.sessions[self.session_id].touched, before)
        self.model.reply.assert_not_called()
        self.store.retrieve.assert_not_called()
        self.store.save.assert_not_called()

    def test_presence_only_owner_poll_renews_and_expiry_disarms(self):
        other = self.session_id
        self.session_id = self.app.start()["session_id"]
        self.arm()
        self.clock.advance(29)
        self.assertFalse(self.app.robot_snapshot(other)["contact_response"]["enabled"])
        self.clock.advance(2)
        self.app.physical.step()
        self.assertFalse(self.app.physical.status(self.session_id)["enabled"])
        self.arm()
        self.clock.advance(29)
        self.app.robot_snapshot(self.session_id)
        self.clock.advance(29)
        self.app.physical.step()
        self.assertTrue(self.app.physical.status(self.session_id)["enabled"])
        self.clock.advance(2)
        self.app.physical.step()
        self.assertFalse(self.app.physical.status(self.session_id)["enabled"])

    def test_connection_loss_and_removed_session_disarm_without_rearming(self):
        self.arm()
        self.robot.state["ready"] = False
        self.app.physical.step()
        self.robot.state["ready"] = True
        self.gesture("after-reconnect")
        self.assertEqual(self.robot.accepted, [])
        self.assertFalse(self.app.physical.status(self.session_id)["enabled"])
        self.arm()
        del self.app.sessions[self.session_id]
        self.app.physical.step()
        self.assertIsNone(self.app.physical.owner)

    def test_new_profile_disarms_and_old_session_cannot_arm_with_fresh_token(self):
        first = self.store.create_profile("Primero")["id"]
        second = self.store.create_profile("Segundo")["id"]
        old = self.app.start(first)["session_id"]
        self.arm(old)
        new = self.app.start(second)["session_id"]
        generation = self.app.robot_snapshot(new)["contact_response"]["generation"]
        with self.assertRaises(Cancelled):
            self.app.contact_response(old, True, generation)
        self.arm(new)
        self.gesture("new-profile-touch")
        self.assertEqual(self.app.sessions[old].history, [])
        self.assertEqual(self.app.sessions[new].history, [])
        self.assertEqual(self.store.list_memories(first), [])
        self.assertEqual(self.store.list_memories(second), [])
        self.assertEqual(self.app.physical.owner, new)

    def test_busy_chat_consumes_pulse_even_without_worker_poll_during_inference(self):
        class ChatModel(FakeModel):
            def reply(model, name, history, memories):
                self.robot.pulse("pulse-during-inference")
                return super().reply(name, history, memories)

        self.app.model = ChatModel()
        self.arm()
        self.app.chat(self.session_id, "Hola")
        self.app.physical.step()
        self.assertEqual(self.robot.accepted, [("FACE", "warm")])
        self.clock.advance(1.1)
        self.gesture("after-chat")
        self.assertEqual(self.robot.accepted[-1], ("FACE", "listening"))

    def test_busy_poll_consumes_pulse_and_pending_cooldown(self):
        self.arm()
        self.gesture("first")
        self.clock.advance(0.2)
        self.gesture("pending")
        session = self.app.sessions[self.session_id]
        session.request_id = "active-chat"
        self.gesture("during-chat")
        session.request_id = None
        self.clock.advance(1.1)
        self.app.physical.step()
        self.assertEqual(self.robot.accepted, [("FACE", "listening")])

    def test_pulse_during_chat_delivered_after_finally_is_consumed(self):
        self.app.model = FakeModel()
        self.arm()
        self.clock.advance(1)
        self.app.chat(self.session_id, "Hola")
        self.clock.advance(0.2)
        self.gesture("busy-pulse-delivered-late", age_ms=500)
        self.gesture("busy-pulse-delivered-late", age_ms=0)
        self.assertEqual(self.robot.accepted, [("FACE", "warm")])
        self.gesture("new-post-chat-pulse")
        self.assertEqual(self.robot.accepted[-1], ("FACE", "listening"))

    def test_stop_and_new_session_cancel_pending_face_without_holding_lock(self):
        for action in (lambda: self.app.stop(self.session_id), lambda: self.app.start()):
            with self.subTest(action=action):
                self.session_id = self.app.active_session_id
                self.arm()
                entered, release = threading.Event(), threading.Event()
                observed = []

                def before_send(cancelled):
                    entered.set()
                    if not release.wait(3):
                        raise RuntimeError("Fixture wait timeout")
                    observed.append(cancelled())

                self.robot.before_send = before_send
                self.robot.pulse("race-" + str(self.app.physical.generation))
                worker = threading.Thread(target=self.app.physical.step)
                worker.start()
                try:
                    self.assertTrue(entered.wait(2))
                    action()
                finally:
                    release.set()
                    worker.join(3)
                self.assertFalse(worker.is_alive())
                self.assertEqual(observed, [True])
                self.assertFalse(any(command == "FACE" for command, argument in self.robot.accepted))
                self.robot.before_send = None

    def test_stop_invalidates_enable_while_snapshot_is_blocked(self):
        generation = self.app.robot_snapshot(self.session_id)["contact_response"]["generation"]
        entered, release = threading.Event(), threading.Event()

        def delayed_snapshot():
            entered.set()
            if not release.wait(3):
                raise RuntimeError("Fixture snapshot timeout")

        self.robot.snapshot_once = delayed_snapshot
        errors = []

        def activate():
            try:
                self.app.contact_response(self.session_id, True, generation)
            except Exception as error:
                errors.append(error)

        worker = threading.Thread(target=activate)
        worker.start()
        try:
            self.assertTrue(entered.wait(2))
            self.assertTrue(self.app.stop(self.session_id)["stopped"])
        finally:
            release.set()
            worker.join(3)
        self.assertFalse(worker.is_alive())
        self.assertEqual(len(errors), 1)
        self.assertIsInstance(errors[0], Cancelled)
        self.assertFalse(self.app.physical.status(self.session_id)["enabled"])
        self.assertIsNone(self.app.physical.thread)

    def test_actual_worker_is_lazy_and_close_cancels_transport_wait(self):
        self.app.physical = PhysicalInteraction(self.app, poll_interval=0.01)
        self.assertIsNone(self.app.physical.thread)
        self.arm()
        entered = threading.Event()

        def hold_until_cancelled(cancelled):
            entered.set()
            deadline = threading.Event()
            for _ in range(200):
                if cancelled():
                    return
                deadline.wait(0.01)
            raise RuntimeError("Fixture never received cancellation")

        self.robot.before_send = hold_until_cancelled
        self.robot.pulse("worker-pulse")
        self.assertTrue(entered.wait(2))
        self.app.close()
        self.assertFalse(self.app.physical.thread.is_alive())
        self.assertEqual(self.robot.accepted, [])

    def test_api_routes_preserve_snapshot_and_return_generation_from_stop(self):
        handler = Handler.__new__(Handler)
        handler.server = SimpleNamespace(companion=self.app)
        fields = {"session_id": self.session_id}
        response = handler.dispatch("/api/robot", fields)
        self.assertEqual({key: response[key] for key in self.robot.state}, self.robot.state)
        enabled = handler.dispatch("/api/contact-response", {
            **fields, "enabled": True, "generation": response["contact_response"]["generation"]})
        self.assertTrue(enabled["contact_response"]["enabled"])
        stopped = handler.dispatch("/api/stop", fields)
        self.assertTrue(stopped["stopped"])
        self.assertFalse(stopped["contact_response"]["enabled"])
        with self.assertRaises(Cancelled):
            handler.dispatch("/api/contact-response", {
                **fields, "enabled": True, "generation": enabled["contact_response"]["generation"]})


if __name__ == "__main__":
    unittest.main()
