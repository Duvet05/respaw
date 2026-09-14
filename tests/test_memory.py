from pathlib import Path
import tempfile
import threading
import unittest

from respaw.engine import Cancelled, Companion
from respaw.model import validate_reply
from respaw.robot import Robot
from respaw.store import MemoryStore


class FakeModel:
    """Only for deterministic contract tests, never used as a conversation fallback."""
    def __init__(self):
        self.calls = []

    def reply(self, name, history, memories):
        self.calls.append((name, history, memories))
        return {"reply": "¿Cómo has estado?", "expression": "warm", "offer": "none",
                "memory_ids": [m["id"] for m in memories]}


class StoreFixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "memory.sqlite3"
        self.store = MemoryStore(self.path)
        self.ana = self.store.create_profile("Ana")["id"]
        self.luis = self.store.create_profile("Luis")["id"]

    def save(self, text, user=None, **kwargs):
        return self.store.save(user or self.ana, text, "previous-session", **kwargs)


class MemoryTests(StoreFixture):

    def test_recall_across_restart_and_implicit_greeting(self):
        memory = self.save("Estaba triste porque desaprobé el examen de física.")
        restarted = MemoryStore(self.path)
        result = restarted.retrieve(self.ana, "Hola, volví", opening=True)
        self.assertEqual([m["id"] for m in result], [memory["id"]])
        self.assertEqual(result[0]["quote"], memory["quote"])
        self.assertIsNone(result[0]["event_date"])

    def test_identity_filter_applies_to_lexical_and_recent_retrieval(self):
        self.save("Estaba triste por el examen secreto de Ana.")
        own = self.save("Mi examen de química salió bien.", user=self.luis)
        for query, opening in [("examen", False), ("hola", True)]:
            result = self.store.retrieve(self.luis, query, opening=opening)
            self.assertEqual([m["id"] for m in result], [own["id"]])

    def test_accent_insensitive_retrieval(self):
        memory = self.save("Me preocupa la exposición del martes.")
        self.assertEqual(self.store.retrieve(self.ana, "exposicion")[0]["id"], memory["id"])

    def test_query_syntax_cannot_escape_scope(self):
        self.save("examen", user=self.luis)
        self.assertEqual(self.store.retrieve(self.ana, '" OR user_id:* NEAR(examen) --'), [])

    def test_unrelated_turn_does_not_force_an_episode(self):
        self.save("Me preocupa el examen de cálculo.")
        self.assertEqual(self.store.retrieve(self.ana, "Quiero hablar de mi gato"), [])

    def test_opening_on_a_new_subject_does_not_force_the_latest_episode(self):
        memory = self.save("Me preocupa el examen de cálculo.")
        for query in ("Hola, quiero hablar de mi gato", "Me gustaría contarte sobre un viaje"):
            with self.subTest(query=query):
                self.assertEqual(self.store.retrieve(self.ana, query, opening=True), [])
        for query in ("Hola, volví otra vez", "Buenas tardes", "¿Qué recuerdas?"):
            with self.subTest(query=query):
                self.assertEqual(self.store.retrieve(self.ana, query, opening=True)[0]["id"], memory["id"])

    def test_preferences_can_inform_unrelated_turns(self):
        self.save("Prefiero respuestas breves.", kind="preference")
        self.assertEqual(len(self.store.retrieve(self.ana, "Estoy cansada")), 1)

    def test_preferences_are_not_crowded_out_by_matching_episodes(self):
        preference = self.save("Prefiero que me escuches sin ejercicios.", kind="preference")
        for topic in ("física", "cálculo", "biología", "química", "historia"):
            self.save("Me preocupa el examen de " + topic)
        result = self.store.retrieve(self.ana, "examen")
        self.assertEqual(len(result), 4)
        self.assertEqual(result[0]["id"], preference["id"])
        self.assertEqual(sum(m["kind"] == "episode" for m in result), 3)

    def test_matching_preferences_do_not_hide_the_episode(self):
        for number in range(6):
            self.save("Prefiero hablar del examen " + str(number), kind="preference")
        episode = self.save("El examen de cálculo fue difícil.")
        result = self.store.retrieve(self.ana, "examen")
        self.assertIn(episode["id"], [m["id"] for m in result])
        self.assertEqual(sum(m["kind"] == "preference" for m in result), 2)

    def test_paused_preference_is_not_retrieved_even_by_its_words(self):
        preference = self.save("Prefiero respuestas breves.", kind="preference")
        self.store.revise(self.ana, preference["id"], preference["quote"], "resolved")
        self.assertEqual(self.store.retrieve(self.ana, "respuestas breves", opening=True), [])
        self.store.revise(self.ana, preference["id"], preference["quote"], "open")
        self.assertEqual(self.store.retrieve(self.ana, "hola")[0]["id"], preference["id"])

    def test_episode_can_become_preference_without_losing_provenance(self):
        memory = self.save("Prefiero que solo me escuches.", event_date="2026-09-10")
        self.store.revise(self.ana, memory["id"], memory["quote"], kind="preference")
        found = self.store.retrieve(self.ana, "Quiero hablar de mi gato")[0]
        self.assertEqual(found["kind"], "preference")
        for field in ("id", "source_session", "created_at", "event_date"):
            self.assertEqual(found[field], memory[field])
        with self.assertRaises(ValueError):
            self.store.revise(self.luis, memory["id"], "Otro recuerdo", kind="episode")
        with self.assertRaises(ValueError):
            self.store.revise(self.ana, memory["id"], "Otro recuerdo", kind="unsupported")

    def test_followup_uses_current_version_and_scopes_recent_ids(self):
        memory = self.save("Estaba triste por el examen.")
        foreign = self.save("Estaba triste por un secreto de Luis.", user=self.luis)
        ids = [memory["id"], foreign["id"]]
        found = self.store.retrieve(self.ana, "¿Por qué me sentía así?", recent_ids=ids)
        self.assertEqual([m["id"] for m in found], [memory["id"]])
        self.store.revise(self.ana, memory["id"], "Fue por mi exposición.")
        self.assertEqual(self.store.retrieve(self.ana, "¿Y eso?", recent_ids=ids)[0]["quote"], "Fue por mi exposición.")
        self.store.forget(self.ana, memory["id"])
        self.assertEqual(self.store.retrieve(self.ana, "¿Y eso?", recent_ids=ids), [])

    def test_followup_does_not_override_a_new_topic(self):
        memory = self.save("Estaba triste por el examen.")
        for query in ("Prefiero hablar de mi gato", "¿Y eso del perro?", "Hola"):
            with self.subTest(query=query):
                self.assertEqual(self.store.retrieve(self.ana, query, recent_ids=[memory["id"]]), [])

    def test_resolved_episode_not_reopened_by_greeting(self):
        memory = self.save("Me preocupa el examen.")
        self.store.revise(self.ana, memory["id"], "El examen ya está resuelto.", "resolved")
        self.assertEqual(self.store.retrieve(self.ana, "hola", opening=True), [])
        found = self.store.retrieve(self.ana, "examen")
        self.assertEqual(found[0]["status"], "resolved")

    def test_correction_replaces_old_indexed_claim(self):
        memory = self.save("Estoy triste por matemáticas.")
        self.store.revise(self.ana, memory["id"], "Me equivoqué: era por biología.")
        self.assertEqual(self.store.retrieve(self.ana, "matematicas"), [])
        self.assertIn("biología", self.store.retrieve(self.ana, "biologia")[0]["quote"])

    def test_forget_removes_fts_and_recent_memory(self):
        memory = self.save("Mi preocupación era una exposición.")
        self.store.forget(self.ana, memory["id"])
        self.assertEqual(self.store.retrieve(self.ana, "exposicion", opening=True), [])
        self.assertEqual(self.store.list_memories(self.ana), [])

    def test_cannot_change_another_profile(self):
        memory = self.save("Mi recuerdo.")
        with self.assertRaises(ValueError):
            self.store.revise(self.luis, memory["id"], "Otra cosa")
        with self.assertRaises(ValueError):
            self.store.forget(self.luis, memory["id"])

    def test_recorded_date_and_event_date_are_distinct(self):
        memory = self.save("El examen fue ayer.", event_date="2026-09-10")
        self.assertEqual(memory["event_date"], "2026-09-10")
        self.assertIn("T", memory["created_at"])
        with self.assertRaises(ValueError):
            self.save("Fecha inventada", event_date="2026-99-10")

    def test_forget_all_preserves_other_profiles(self):
        self.save("Ana")
        self.save("Luis", user=self.luis)
        self.store.forget(self.ana)
        self.assertEqual(len(self.store.list_memories(self.luis)), 1)


class SessionTests(StoreFixture):
    def setUp(self):
        super().setUp()
        self.model = FakeModel()
        self.robot = Robot()
        self.app = Companion(self.store, self.model, self.robot)
        self.session = self.app.start(self.ana)["session_id"]

    def test_conversation_is_ephemeral_until_explicit_save(self):
        result = self.app.chat(self.session, "Estoy triste por mi examen.")
        self.assertEqual(self.store.list_memories(self.ana), [])
        self.app.save_memory(self.session, result["user_message_id"])
        second = self.app.start(self.ana)["session_id"]
        response = self.app.chat(second, "Hola, volví")
        self.assertIn("examen", response["sources"][0]["quote"])

    def test_guest_cannot_persist(self):
        guest = self.app.start()["session_id"]
        result = self.app.chat(guest, "Estoy triste")
        with self.assertRaises(ValueError):
            self.app.save_memory(guest, result["user_message_id"])

    def test_cited_episode_survives_a_followup_but_not_a_topic_change(self):
        memory = self.save("Estaba triste por el examen de cálculo.")
        self.app.chat(self.session, "Hola, volví")
        followup = self.app.chat(self.session, "¿Por qué me sentía así?")
        self.assertEqual([m["id"] for m in followup["sources"]], [memory["id"]])
        self.app.chat(self.session, "Prefiero hablar de mi gato")
        self.assertFalse(self.app.session(self.session).recent_memory_ids)
        self.assertFalse(self.app.chat(self.session, "¿Y eso?")["sources"])

    def test_uncited_memory_does_not_become_session_focus(self):
        self.save("Estaba triste por el examen.")
        original = self.model.reply

        def without_citation(*args):
            return {**original(*args), "memory_ids": []}

        self.model.reply = without_citation
        self.app.chat(self.session, "Hola, volví")
        self.assertFalse(self.app.session(self.session).recent_memory_ids)
        self.app.chat(self.session, "¿Por qué me sentía así?")
        self.assertEqual(self.model.calls[-1][2], [])

    def test_cannot_save_assistant_or_foreign_message(self):
        self.app.chat(self.session, "Hola")
        assistant_id = self.app.session(self.session).history[-1]["id"]
        with self.assertRaises(ValueError):
            self.app.save_memory(self.session, assistant_id)
        second = self.app.start(self.luis)["session_id"]
        foreign_id = self.app.chat(second, "Hola")["user_message_id"]
        with self.assertRaises(ValueError):
            self.app.save_memory(self.session, foreign_id)

    def test_deletion_clears_context_in_all_profile_sessions(self):
        result = self.app.chat(self.session, "Me preocupa el examen.")
        memory = self.app.save_memory(self.session, result["user_message_id"])
        second = self.app.start(self.ana)["session_id"]
        self.app.chat(second, "Hola")
        self.app.change_memory(self.session, memory["id"])
        self.assertFalse(self.app.session(second).history)
        self.assertFalse(self.app.session(second).recent_memory_ids)
        self.app.chat(second, "¿Qué recuerdas del examen?")
        self.assertEqual(self.model.calls[-1][2], [])
        self.assertEqual(len(self.model.calls[-1][1]), 1)

    def test_stop_cancels_late_reply_and_hardware_action(self):
        self._test_inflight(lambda: self.app.stop(self.session))

    def test_forget_cancels_inflight_old_memory(self):
        memory = self.save("Examen pendiente")
        self._test_inflight(lambda: self.app.change_memory(self.session, memory["id"]))

    def _test_inflight(self, interrupt):
        entered, release = threading.Event(), threading.Event()
        original = self.model.reply
        failures = []

        def slow(*args):
            entered.set()
            release.wait(3)
            return original(*args)

        self.model.reply = slow

        def run():
            try:
                self.app.chat(self.session, "Hola")
            except Exception as error:
                failures.append(error)

        worker = threading.Thread(target=run)
        worker.start()
        self.assertTrue(entered.wait(2))
        interrupt()
        release.set()
        worker.join(3)
        self.assertFalse(worker.is_alive())
        self.assertIsInstance(failures[0], Cancelled)
        self.assertEqual(self.robot.expression, "neutral")
        self.assertFalse(any(m["role"] == "assistant" for m in self.app.session(self.session).history))

    def test_model_cannot_reference_unretrieved_memories_or_raw_actuators(self):
        answer = {"reply": "Hola", "expression": "warm", "offer": "none", "memory_ids": ["someone-else"]}
        with self.assertRaises(ValueError):
            validate_reply(answer, [])
        answer["memory_ids"] = []
        answer["expression"] = "write_gpio_1"
        with self.assertRaises(ValueError):
            validate_reply(answer, [])

    def test_simulator_does_not_invent_physiology(self):
        self.robot.command("MEASURE")
        reading = self.robot.snapshot()["measurement"]
        self.assertFalse(reading["valid"])
        self.assertNotIn("bpm", reading)
