from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from respaw.embeddings import EmbeddingClient, normalized_vector
from respaw.engine import Cancelled, Companion
from respaw.model import ModelError
from respaw.robot import Robot
from respaw.store import MemoryStore
from test_memory import FakeModel


class FakeEmbedder:
    """Known directions exercise retrieval contracts without an installed model."""
    model = "fixture"

    def __init__(self):
        self.version = "fixture@v1"
        self.calls = []
        self.directions = {
            "El alquiler me tiene intranquila.": [1, 0, 0],
            "¿Cómo afrontaba la renta?": [1, 0, 0],
            "Mi mascota murió.": [0, 1, 0],
            "Extraño a mi perro fallecido.": [0, 1, 0],
            "¿Cuál es la capital de Noruega?": [0, 0, 1],
        }

    def identity(self):
        return self.version

    def vectors(self, texts, query=False):
        self.calls.append((query, list(texts)))
        return [self.directions.get(text, [0, 0, 1]) for text in texts]


class SemanticTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "memory.sqlite3"
        self.embedder = FakeEmbedder()
        self.store = MemoryStore(self.path, self.embedder)
        self.ana = self.store.create_profile("Ana")["id"]
        self.luis = self.store.create_profile("Luis")["id"]

    def save(self, quote, user=None, **kwargs):
        return self.store.save(user or self.ana, quote, "earlier-session", **kwargs)

    def index_count(self):
        with self.store.connect() as db:
            return db.execute("SELECT count(*) FROM memory_embeddings").fetchone()[0]

    def test_paraphrase_without_matching_words_and_unrelated_abstention(self):
        memory = self.save("El alquiler me tiene intranquila.")
        self.assertEqual(MemoryStore(self.path).retrieve(self.ana, "¿Cómo afrontaba la renta?"), [])
        found = self.store.retrieve(self.ana, "¿Cómo afrontaba la renta?")
        self.assertEqual([m["id"] for m in found], [memory["id"]])
        self.assertNotIn("vector", found[0])
        self.assertEqual(self.store.retrieve(self.ana, "¿Cuál es la capital de Noruega?"), [])

    def test_lexical_match_survives_low_semantic_score(self):
        memory = self.save("El alquiler me tiene intranquila.")
        self.assertEqual(self.store.retrieve(self.ana, "alquiler")[0]["id"], memory["id"])

    def test_only_current_profile_is_embedded_or_retrieved(self):
        own = self.save("El alquiler me tiene intranquila.")
        self.save("Mi mascota murió.", user=self.luis)
        self.assertEqual(self.store.retrieve(self.ana, "¿Cómo afrontaba la renta?")[0]["id"], own["id"])
        self.assertEqual(self.index_count(), 1)
        self.assertNotIn("Mi mascota murió.", [text for _, texts in self.embedder.calls for text in texts])
        self.assertEqual(self.store.retrieve(self.ana, "Extraño a mi perro fallecido."), [])

    def test_cache_survives_restart_but_queries_are_not_stored(self):
        self.save("El alquiler me tiene intranquila.")
        self.store.retrieve(self.ana, "¿Cómo afrontaba la renta?")
        self.embedder.calls.clear()
        restarted = MemoryStore(self.path, self.embedder)
        self.assertEqual(len(restarted.retrieve(self.ana, "¿Cómo afrontaba la renta?")), 1)
        self.assertEqual(self.embedder.calls, [(True, ["¿Cómo afrontaba la renta?"])])
        self.assertEqual(self.index_count(), 1)
        self.assertEqual(len(self.store.list_memories(self.ana)), 1)

    def test_changed_model_digest_reindexes_instead_of_mixing_vectors(self):
        self.save("El alquiler me tiene intranquila.")
        self.store.retrieve(self.ana, "¿Cómo afrontaba la renta?")
        self.embedder.calls.clear()
        self.embedder.version = "fixture@v2"
        self.store.retrieve(self.ana, "¿Cómo afrontaba la renta?")
        self.assertEqual(self.embedder.calls[0], (False, ["El alquiler me tiene intranquila."]))
        self.assertEqual(self.index_count(), 2)
        self.store.forget(self.ana)
        self.assertEqual(self.index_count(), 0)

    def test_revision_invalidates_old_vectors_and_retrieves_new_content(self):
        memory = self.save("El alquiler me tiene intranquila.")
        self.store.retrieve(self.ana, "¿Cómo afrontaba la renta?")
        self.store.revise(self.ana, memory["id"], "Mi mascota murió.")
        self.assertEqual(self.index_count(), 0)
        self.assertEqual(self.store.retrieve(self.ana, "¿Cómo afrontaba la renta?"), [])
        found = self.store.retrieve(self.ana, "Extraño a mi perro fallecido.")
        self.assertEqual(found[0]["quote"], "Mi mascota murió.")

    def test_forgetting_removes_vectors_without_touching_another_profile(self):
        memory = self.save("El alquiler me tiene intranquila.")
        self.save("Mi mascota murió.", user=self.luis)
        self.store.retrieve(self.ana, "¿Cómo afrontaba la renta?")
        self.store.retrieve(self.luis, "Extraño a mi perro fallecido.")
        self.store.forget(self.ana, memory["id"])
        self.assertEqual(self.index_count(), 1)
        self.assertEqual(self.store.retrieve(self.ana, "¿Cómo afrontaba la renta?"), [])
        self.assertEqual(len(self.store.retrieve(self.luis, "Extraño a mi perro fallecido.")), 1)

    def test_paused_preferences_are_neither_embedded_nor_retrieved(self):
        memory = self.save("El alquiler me tiene intranquila.", kind="preference")
        self.store.retrieve(self.ana, "¿Cómo afrontaba la renta?")
        self.store.revise(self.ana, memory["id"], memory["quote"], "resolved")
        self.assertEqual(self.index_count(), 0)
        self.embedder.calls.clear()
        self.assertEqual(self.store.retrieve(self.ana, "¿Cómo afrontaba la renta?", opening=True), [])
        self.assertEqual(self.embedder.calls, [])

    def test_resolved_episode_requires_a_topic_and_greetings_skip_embedding(self):
        memory = self.save("El alquiler me tiene intranquila.")
        self.store.revise(self.ana, memory["id"], memory["quote"], "resolved")
        for query in ("Hola, volví", "Hola, volví otra vez", "Buenas tardes"):
            with self.subTest(query=query):
                self.assertEqual(self.store.retrieve(self.ana, query, opening=True), [])
        self.assertEqual(self.embedder.calls, [])
        self.assertEqual(self.store.retrieve(self.ana, "¿Cómo afrontaba la renta?")[0]["status"], "resolved")

    def test_followup_uses_current_cited_memory_without_embedding(self):
        memory = self.save("El alquiler me tiene intranquila.")
        found = self.store.retrieve(self.ana, "¿Y eso?", recent_ids=[memory["id"]])
        self.assertEqual(found[0]["id"], memory["id"])
        self.assertEqual(self.embedder.calls, [])

    def test_unavailable_model_falls_back_with_a_retry_cooldown(self):
        self.save("El alquiler me tiene intranquila.")
        with patch.object(self.embedder, "identity", side_effect=ModelError("Modelo no instalado")) as check:
            self.assertEqual(len(self.store.retrieve(self.ana, "alquiler")), 1)
            self.assertEqual(len(self.store.retrieve(self.ana, "alquiler")), 1)
            self.assertEqual(check.call_count, 1)
        self.assertFalse(self.store.semantic_status["ready"])
        self.assertIn("no instalado", self.store.semantic_status["error"])
        self.assertTrue(self.store.check_semantics()["ready"])
        self.assertEqual(len(self.store.retrieve(self.ana, "¿Cómo afrontaba la renta?")), 1)

    def test_invalid_embedding_batch_is_atomic_and_falls_back(self):
        self.save("El alquiler me tiene intranquila.")
        self.save("Mi mascota murió.")
        with patch.object(self.embedder, "vectors", return_value=[[1, 0, 0], [0, 0, 0]]):
            self.assertEqual(len(self.store.retrieve(self.ana, "alquiler")), 1)
        self.assertEqual(self.index_count(), 0)
        self.assertFalse(self.store.semantic_status["ready"])

    def test_corrupt_cached_vector_falls_back_without_crashing_chat(self):
        memory = self.save("El alquiler me tiene intranquila.")
        self.store.retrieve(self.ana, "¿Cómo afrontaba la renta?")
        with self.store.connect() as db:
            db.execute("UPDATE memory_embeddings SET vector=? WHERE memory_id=?", (b"broken", memory["id"]))
        self.assertEqual(len(self.store.retrieve(self.ana, "alquiler")), 1)
        self.assertFalse(self.store.semantic_status["ready"])

    def test_backfill_is_bounded_and_advances_across_turns(self):
        for number in range(19):
            self.save("Un recuerdo " + str(number))
        self.store.retrieve(self.ana, "investigación")
        self.assertEqual(self.index_count(), 16)
        self.assertEqual(self.store.semantic_status["pending"], 3)
        self.store.retrieve(self.ana, "investigación")
        self.assertEqual(self.index_count(), 19)
        self.assertEqual(self.store.semantic_status["pending"], 0)
        self.assertEqual([len(texts) for query, texts in self.embedder.calls if not query], [16, 3])

    def test_stop_during_embedding_does_not_wait_for_inference(self):
        self._during_embedding("stop")

    def test_forget_during_embedding_cancels_reply_and_cannot_restore_index(self):
        self._during_embedding("forget")

    def test_revision_during_embedding_cannot_restore_stale_index(self):
        self._during_embedding("revise")

    def _during_embedding(self, operation):
        memory = self.save("El alquiler me tiene intranquila.")
        model, robot = FakeModel(), Robot()
        app = Companion(self.store, model, robot)
        session = app.start(self.ana)["session_id"]
        entered, release = threading.Event(), threading.Event()
        original = self.embedder.vectors

        def slow(texts, query=False):
            if not query:
                entered.set()
                if not release.wait(4):
                    raise ModelError("Fixture timeout")
            return original(texts, query=query)

        def mutate():
            if operation == "stop":
                app.stop(session)
            elif operation == "forget":
                app.change_memory(session, memory["id"])
            else:
                app.change_memory(session, memory["id"], "Mi mascota murió.")

        with patch.object(self.embedder, "vectors", side_effect=slow), ThreadPoolExecutor(2) as pool:
            chat = pool.submit(app.chat, session, "¿Cómo afrontaba la renta?")
            try:
                self.assertTrue(entered.wait(2))
                pool.submit(mutate).result(timeout=1)
            finally:
                release.set()
            with self.assertRaises(Cancelled):
                chat.result(timeout=2)
        self.assertEqual(model.calls, [])
        self.assertEqual(robot.expression, "neutral")
        if operation != "stop":
            self.assertEqual(self.index_count(), 0)
            self.assertEqual(self.store.retrieve(self.ana, "¿Cómo afrontaba la renta?"), [])


class EmbeddingClientTests(unittest.TestCase):
    def test_query_instruction_and_document_normalization(self):
        client = EmbeddingClient()
        with patch.object(client, "request", return_value={"embeddings": [[3, 4]]}) as request:
            self.assertEqual(client.vectors(["texto"]), [[0.6, 0.8]])
            self.assertEqual(request.call_args.args[1]["input"], ["texto"])
            client.vectors(["consulta"], query=True)
            payload = request.call_args.args[1]
            self.assertIn("\nQuery: consulta", payload["input"][0])
            self.assertFalse(payload["truncate"])

    def test_invalid_values_counts_and_dimensions_are_rejected(self):
        for vector in ([], [1], [0, 0], [True, 0], [float("nan"), 1], [float("inf"), 1], ["1", 2]):
            with self.subTest(vector=vector), self.assertRaises(ModelError):
                normalized_vector(vector)
        client = EmbeddingClient()
        for vectors in (None, [], [[1, 0]], [[1, 0], [1, 0, 0]]):
            with self.subTest(vectors=vectors), patch.object(client, "request", return_value={"embeddings": vectors}):
                with self.assertRaises(ModelError):
                    client.vectors(["uno", "dos"])

    def test_cache_identity_includes_digest_and_remote_models_are_rejected(self):
        client = EmbeddingClient()
        entry = {"name": client.model, "digest": "abc"}
        with patch.object(client, "request", return_value={"models": [entry]}):
            self.assertIn("@abc:", client.identity())
            entry["remote_host"] = "https://example.com"
            with self.assertRaises(ModelError):
                client.identity()
        with self.assertRaises(ValueError):
            EmbeddingClient("https://example.com")
