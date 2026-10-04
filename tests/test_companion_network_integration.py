from pathlib import Path
import tempfile
import threading
import unittest

from respaw.__main__ import arguments
from respaw.engine import Cancelled, Companion
from respaw.store import MemoryStore
from test_memory import FakeModel


class DelayedRobot:
    supports_command_cancellation = True

    def __init__(self):
        self.entered = threading.Event()
        self.release = threading.Event()
        self.commands = []

    def snapshot(self):
        return {"ready": True, "contact": {"sensor": "fsr_a8", "pressed": True}}

    def command(self, command, argument="", cancelled=None):
        if command == "FACE":
            self.entered.set()
            if not self.release.wait(3):
                raise RuntimeError("Fixture acknowledgement timeout")
            if cancelled and cancelled():
                raise RuntimeError("Cancelled before dispatch")
        self.commands.append((command, argument))


class NetworkCompanionTests(unittest.TestCase):
    def test_stop_invalidates_turn_without_waiting_for_network_ack(self):
        with tempfile.TemporaryDirectory() as directory:
            robot = DelayedRobot()
            app = Companion(MemoryStore(Path(directory) / "memory.sqlite3"), FakeModel(), robot)
            session = app.start()["session_id"]
            errors = []

            def chat():
                try:
                    app.chat(session, "Hola")
                except Exception as error:
                    errors.append(error)

            worker = threading.Thread(target=chat)
            worker.start()
            try:
                self.assertTrue(robot.entered.wait(2))
                stopped = threading.Event()
                stopper = threading.Thread(target=lambda: (app.stop(session), stopped.set()))
                stopper.start()
                self.assertTrue(stopped.wait(1), "STOP was blocked by the FACE acknowledgement")
                stopper.join(1)
                self.assertEqual(robot.commands, [("STOP", "")])
            finally:
                robot.release.set()
                worker.join(3)
            self.assertFalse(worker.is_alive())
            self.assertEqual(len(errors), 1)
            self.assertIsInstance(errors[0], Cancelled)
            self.assertFalse(any(m["role"] == "assistant" for m in app.session(session).history))
            self.assertEqual(robot.commands, [("STOP", "")])

    def test_sensor_context_does_not_create_a_persistent_memory(self):
        class ContextModel(FakeModel):
            supports_robot_context = True

            def reply(self, name, history, memories, robot_context=None):
                self.robot_context = robot_context
                return super().reply(name, history, memories)

        with tempfile.TemporaryDirectory() as directory:
            store = MemoryStore(Path(directory) / "memory.sqlite3")
            user = store.create_profile("Prueba")["id"]
            model, robot = ContextModel(), DelayedRobot()
            robot.release.set()
            app = Companion(store, model, robot)
            app.chat(app.start(user)["session_id"], "Hola")
            self.assertTrue(model.robot_context["contact"]["pressed"])
            self.assertEqual(store.list_memories(user), [])

    def test_cli_keeps_simulation_and_offline_as_defaults(self):
        args = arguments([])
        self.assertEqual((args.provider, args.speech_provider), ("ollama", "local"))
        self.assertIsNone(args.device)
        self.assertIsNone(args.robot_link)
        args = arguments(["--provider", "openai", "--speech-provider", "cloud", "--robot-link",
                          "ws://127.0.0.1:8767/operator", "--robot-token-file", "/tmp/fixture.token"])
        self.assertEqual(args.provider, "openai")
        for invalid in (["--robot-link", "ws://127.0.0.1:8767/operator"],
                        ["--robot-token-file", "/tmp/fixture.token"],
                        ["--device", "usb", "--robot-link", "ws://127.0.0.1:8767/operator"],
                        ["--model-timeout", "0"]):
            with self.subTest(arguments=invalid), self.assertRaises(SystemExit):
                arguments(invalid)
