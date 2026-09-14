"""Small local embedding client; no provider fallback and no query persistence."""

import math

from .model import ModelError, OllamaClient

DEFAULT_EMBEDDING_MODEL = "qwen3-embedding:0.6b"
QUERY_INSTRUCTION = "Given a user's message, retrieve their relevant past statements and personal preferences."


def normalized_vector(vector):
    if not isinstance(vector, list) or not 2 <= len(vector) <= 4096:
        raise ModelError("Dimensión del embedding no válida.")
    if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in vector):
        raise ModelError("El embedding contiene valores no válidos.")
    norm = math.hypot(*vector)
    if not math.isfinite(norm) or norm <= 1e-12:
        raise ModelError("El embedding no contiene una dirección válida.")
    return [v / norm for v in vector]


class EmbeddingClient(OllamaClient):
    def __init__(self, endpoint="http://127.0.0.1:11434", model=DEFAULT_EMBEDDING_MODEL):
        super().__init__(endpoint, model, timeout=12)

    def identity(self):
        info = self.check()
        digest = info.get("digest")
        if not isinstance(digest, str) or not digest:
            raise ModelError("El modelo de memoria no informa su versión.")
        return f"{self.model}@{digest}:retrieval-v1"

    def vectors(self, texts, query=False):
        if not isinstance(texts, list) or not 1 <= len(texts) <= 16:
            raise ValueError("Lote de embeddings no válido.")
        if any(not isinstance(t, str) or not 1 <= len(t) <= 2200 for t in texts):
            raise ValueError("Texto de embedding no válido.")
        inputs = [f"Instruct: {QUERY_INSTRUCTION}\nQuery: {text}" for text in texts] if query else texts
        result = self.request("/api/embed", {
            "model": self.model, "input": inputs, "truncate": False,
            "keep_alive": "5m", "options": {"num_ctx": 2048},
        })
        vectors = result.get("embeddings")
        if not isinstance(vectors, list) or len(vectors) != len(texts):
            raise ModelError("El modelo devolvió un número incorrecto de embeddings.")
        vectors = [normalized_vector(v) for v in vectors]
        if len({len(v) for v in vectors}) != 1:
            raise ModelError("Las dimensiones de los embeddings no coinciden.")
        return vectors
