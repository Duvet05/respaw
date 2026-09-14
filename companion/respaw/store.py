"""SQLite episodic retrieval. Only explicitly saved user statements persist."""

from contextlib import contextmanager
from datetime import date, datetime, timezone
import hashlib
import math
from pathlib import Path
import re
import sqlite3
import struct
import time
import unicodedata
import uuid

from .embeddings import normalized_vector
from .model import ModelError


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def normalize(text):
    return "".join(c for c in unicodedata.normalize("NFD", text.lower())
                   if unicodedata.category(c) != "Mn")


STOP_WORDS = set("a al algo como con cual cuando de del el ella en es esa ese eso esta estaba estoy fue ha he hola la las lo los me mi mis mucho muy no nos o para pero por que se si sin sobre su te tengo tu un una unos y ya yo ultima vez recuerdas recuerdo".split())


def search_terms(text):
    return list(dict.fromkeys(t for t in re.findall(r"[a-z0-9]+", normalize(text))
                             if len(t) > 2 and t not in STOP_WORDS))[:16]


def is_general_opening(query):
    """Allow recency for a greeting/recap, not an explicit new subject."""
    return len(query) <= 180 and set(search_terms(query)) <= set(
        "volvi vuelvo vuelto regrese regreso nuevo nueva otra aqui tal estas hoy "
        "buenas buenos dias tardes noches respaw recuerdos hablamos conversamos acuerdas".split())


FOLLOWUP_TERMS = set("asi eso esa ese aquello asunto tema anterior antes despues entonces luego sentia sentias senti sentiste sentir paso pasaba sucedio sucedido dije dijiste decia conte contaste contado hablamos hablabamos habiamos".split())


def is_followup(query):
    """Conservative lexical fallback for short references, not semantic search."""
    terms = set(search_terms(query))
    words = " ".join(re.findall(r"[a-z0-9]+", normalize(query)))
    cues = set(words.split()) & FOLLOWUP_TERMS
    return len(query) <= 180 and (bool(cues) and terms <= FOLLOWUP_TERMS
                                 or words in ("por que", "y por que"))


def clean_text(value, limit=2000):
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise ValueError("Texto vacío o demasiado largo.")
    if any(ord(c) < 32 and c not in "\n\t" for c in value):
        raise ValueError("El texto contiene caracteres de control.")
    return value.strip()


class MemoryStore:
    def __init__(self, path, embedder=None, semantic_threshold=0.42):
        self.path = Path(path)
        if not 0 < semantic_threshold <= 1:
            raise ValueError("Umbral semántico no válido.")
        self.embedder = embedder
        self.semantic_threshold = semantic_threshold
        self.semantic_retry_at = 0
        self.semantic_status = {"enabled": embedder is not None, "ready": False,
                                "model": embedder.model if embedder else None, "error": None}
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with self.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS profiles (
                    id TEXT PRIMARY KEY, name TEXT NOT NULL, created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS memories (
                    rowid INTEGER PRIMARY KEY, id TEXT UNIQUE NOT NULL,
                    user_id TEXT NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
                    quote TEXT NOT NULL, topic TEXT NOT NULL DEFAULT '',
                    kind TEXT NOT NULL CHECK(kind IN ('episode', 'preference')),
                    source_session TEXT NOT NULL, created_at TEXT NOT NULL,
                    event_date TEXT, updated_at TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'open' CHECK(status IN ('open','resolved'))
                );
                CREATE INDEX IF NOT EXISTS memories_user ON memories(user_id, status, created_at);
                CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5(
                    quote, topic, content='memories', content_rowid='rowid',
                    tokenize='unicode61 remove_diacritics 2'
                );
                CREATE TRIGGER IF NOT EXISTS memories_ai AFTER INSERT ON memories BEGIN
                    INSERT INTO memories_fts(rowid, quote, topic) VALUES (new.rowid, new.quote, new.topic);
                END;
                CREATE TRIGGER IF NOT EXISTS memories_ad AFTER DELETE ON memories BEGIN
                    INSERT INTO memories_fts(memories_fts, rowid, quote, topic)
                    VALUES ('delete', old.rowid, old.quote, old.topic);
                END;
                CREATE TRIGGER IF NOT EXISTS memories_au AFTER UPDATE ON memories BEGIN
                    INSERT INTO memories_fts(memories_fts, rowid, quote, topic)
                    VALUES ('delete', old.rowid, old.quote, old.topic);
                    INSERT INTO memories_fts(rowid, quote, topic) VALUES (new.rowid, new.quote, new.topic);
                END;
                CREATE TABLE IF NOT EXISTS memory_embeddings (
                    memory_id TEXT NOT NULL REFERENCES memories(id) ON DELETE CASCADE,
                    model_key TEXT NOT NULL, signature TEXT NOT NULL,
                    dimensions INTEGER NOT NULL, vector BLOB NOT NULL,
                    PRIMARY KEY(memory_id, model_key)
                );
                CREATE TRIGGER IF NOT EXISTS memories_embedding_update
                AFTER UPDATE OF quote, topic, kind, status ON memories BEGIN
                    DELETE FROM memory_embeddings WHERE memory_id=old.id;
                END;
            """)
        self.path.chmod(0o600)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA secure_delete=ON")
        try:
            with db:
                yield db
        finally:
            db.close()

    def profiles(self):
        with self.connect() as db:
            return [dict(r) for r in db.execute("SELECT * FROM profiles ORDER BY created_at")]

    def profile(self, user_id):
        if not isinstance(user_id, str):
            raise ValueError("Identificador de perfil no válido.")
        with self.connect() as db:
            row = db.execute("SELECT * FROM profiles WHERE id=?", (user_id,)).fetchone()
        if row is None:
            raise ValueError("El perfil no existe.")
        return dict(row)

    def create_profile(self, name):
        profile = {"id": str(uuid.uuid4()), "name": clean_text(name, 80), "created_at": now_iso()}
        with self.connect() as db:
            db.execute("INSERT INTO profiles VALUES (:id,:name,:created_at)", profile)
        return profile

    def list_memories(self, user_id):
        self.profile(user_id)
        with self.connect() as db:
            return [dict(r) for r in db.execute(
                "SELECT * FROM memories WHERE user_id=? ORDER BY updated_at DESC, rowid DESC", (user_id,))]

    def save(self, user_id, quote, source_session, topic="", kind="episode", event_date=None):
        self.profile(user_id)
        quote = clean_text(quote)
        if kind not in ("episode", "preference"):
            raise ValueError("Tipo de recuerdo no válido.")
        if event_date:
            date.fromisoformat(event_date)
        if not isinstance(topic, str) or len(topic) > 120:
            raise ValueError("Tema demasiado largo.")
        stamp = now_iso()
        memory = dict(id=str(uuid.uuid4()), user_id=user_id, quote=quote, topic=topic.strip(),
                      kind=kind, source_session=source_session, created_at=stamp,
                      event_date=event_date or None, updated_at=stamp, status="open")
        with self.connect() as db:
            db.execute("""INSERT INTO memories
                (id,user_id,quote,topic,kind,source_session,created_at,event_date,updated_at)
                VALUES (:id,:user_id,:quote,:topic,:kind,:source_session,:created_at,:event_date,:updated_at)
                """, memory)
        return memory

    def revise(self, user_id, memory_id, quote, status="open", kind=None):
        if not isinstance(memory_id, str):
            raise ValueError("Identificador de recuerdo no válido.")
        quote = clean_text(quote)
        if status not in ("open", "resolved"):
            raise ValueError("Estado no válido.")
        if kind is not None and kind not in ("episode", "preference"):
            raise ValueError("Tipo de recuerdo no válido.")
        with self.connect() as db:
            result = db.execute("""UPDATE memories SET quote=?, status=?, updated_at=?, kind=COALESCE(?,kind)
                WHERE user_id=? AND id=?""", (quote, status, now_iso(), kind, user_id, memory_id))
            if not result.rowcount:
                raise ValueError("El recuerdo no existe en este perfil.")

    def forget(self, user_id, memory_id=None):
        self.profile(user_id)
        if memory_id is not None and not isinstance(memory_id, str):
            raise ValueError("Identificador de recuerdo no válido.")
        with self.connect() as db:
            if memory_id is None:
                db.execute("DELETE FROM memories WHERE user_id=?", (user_id,))
            else:
                result = db.execute("DELETE FROM memories WHERE user_id=? AND id=?", (user_id, memory_id))
                if not result.rowcount:
                    raise ValueError("El recuerdo no existe en este perfil.")

    def check_semantics(self):
        if self.embedder:
            try:
                self.embedder.identity()
                self.semantic_status = {**self.semantic_status, "ready": True, "error": None}
                self.semantic_retry_at = 0
            except ModelError as error:
                self._semantic_unavailable(error)
        return dict(self.semantic_status)

    def _semantic_unavailable(self, error):
        self.semantic_status = {**self.semantic_status, "ready": False, "error": str(error)}
        self.semantic_retry_at = time.monotonic() + 30

    def _semantic_matches(self, user_id, query):
        # Generic greetings and short references use recency/session context.
        if not self.embedder or is_general_opening(query) or is_followup(query) or time.monotonic() < self.semantic_retry_at:
            return []
        try:
            model_key = self.embedder.identity()
            with self.connect() as db:
                rows = [dict(r) for r in db.execute("""
                    SELECT m.*, e.signature AS indexed_signature FROM memories m
                    LEFT JOIN memory_embeddings e ON e.memory_id=m.id AND e.model_key=?
                    WHERE m.user_id=? AND (m.kind='episode' OR m.status='open')
                    ORDER BY m.updated_at DESC, m.rowid DESC""", (model_key, user_id))]
            if not rows:
                return []
            missing = [m for m in rows if m["indexed_signature"] != memory_signature(m)]
            # Bound initial backfill work per request. Further turns index the remainder.
            batch = missing[:16]
            if batch:
                vectors = self.embedder.vectors([memory_document(m) for m in batch])
                if len(vectors) != len(batch):
                    raise ModelError("Faltan embeddings de recuerdos.")
                with self.connect() as db:
                    for memory, vector in zip(batch, vectors):
                        vector = normalized_vector(vector)
                        # A late result cannot restore a deleted or revised memory.
                        db.execute("""INSERT OR REPLACE INTO memory_embeddings
                            SELECT id, ?, ?, ?, ? FROM memories
                            WHERE id=? AND user_id=? AND quote=? AND topic=? AND kind=? AND status=?""",
                            (model_key, memory_signature(memory), len(vector), struct.pack(f"<{len(vector)}f", *vector),
                             memory["id"], user_id, memory["quote"], memory["topic"], memory["kind"], memory["status"]))
            query_vectors = self.embedder.vectors([query], query=True)
            if len(query_vectors) != 1:
                raise ModelError("Embedding de consulta no válido.")
            query_vector = normalized_vector(query_vectors[0])
            # Read again after inference so mutations also affect candidate selection.
            with self.connect() as db:
                indexed = [dict(r) for r in db.execute("""
                    SELECT m.*, e.signature AS indexed_signature, e.dimensions, e.vector
                    FROM memories m JOIN memory_embeddings e ON e.memory_id=m.id
                    WHERE m.user_id=? AND e.model_key=? AND (m.kind='episode' OR m.status='open')
                    """, (user_id, model_key))]
            scored = []
            for memory in indexed:
                if memory["indexed_signature"] != memory_signature(memory):
                    continue
                dimensions, blob = memory.pop("dimensions"), memory.pop("vector")
                memory.pop("indexed_signature")
                if dimensions != len(query_vector) or len(blob) != dimensions * 4:
                    raise ModelError("El índice semántico tiene una dimensión incompatible.")
                vector = normalized_vector(list(struct.unpack(f"<{dimensions}f", blob)))
                score = math.fsum(a * b for a, b in zip(query_vector, vector))
                if score >= self.semantic_threshold:
                    scored.append((score, memory))
            scored.sort(key=lambda item: (item[0], item[1]["updated_at"], item[1]["rowid"]), reverse=True)
            self.semantic_status = {**self.semantic_status, "ready": True, "error": None,
                                    "pending": max(0, len(missing) - len(batch))}
            return [m for _, m in scored[:8]]
        except (ModelError, ValueError, TypeError, struct.error) as error:
            self._semantic_unavailable(error)
            return []

    def retrieve(self, user_id, query, opening=False, limit=4, recent_ids=()):
        """Filter identity/status in SQL; never fall back to another person's data.

        Relevant resolved events are available only on an explicit topic match;
        a general opening can retrieve recent open episodes. Reserve up to two places for
        active preferences. Short follow-ups can re-read the last cited episodes.
        """
        self.profile(user_id)
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 4:
            raise ValueError("Límite de recuerdos no válido.")
        if not isinstance(recent_ids, (tuple, list)) or len(recent_ids) > 4 or any(not isinstance(i, str) for i in recent_ids):
            raise ValueError("Referencias de contexto no válidas.")
        terms = search_terms(query)
        matches, recent, opening_episodes = [], [], []
        with self.connect() as db:
            if terms:
                expression = " OR ".join('"' + t + '"' for t in terms)
                for kind, count in (("preference", 2), ("episode", limit)):
                    matches.extend(dict(r) for r in db.execute("""
                        SELECT m.* FROM memories_fts f JOIN memories m ON m.rowid=f.rowid
                        WHERE memories_fts MATCH ? AND m.user_id=? AND m.kind=?
                        AND (m.kind='episode' OR m.status='open')
                        ORDER BY bm25(memories_fts), m.updated_at DESC, m.rowid DESC LIMIT ?
                        """, (expression, user_id, kind, count)))
            preferences = [dict(r) for r in db.execute("""
                SELECT * FROM memories WHERE user_id=? AND status='open'
                AND kind='preference' ORDER BY updated_at DESC, rowid DESC LIMIT 2
                """, (user_id,))]
            if recent_ids and is_followup(query):
                placeholders = ",".join("?" for _ in recent_ids)
                found = {r["id"]: dict(r) for r in db.execute(f"""
                    SELECT * FROM memories WHERE user_id=? AND kind='episode'
                    AND id IN ({placeholders})""", (user_id, *recent_ids))}
                recent = [found[i] for i in recent_ids if i in found]
            if opening and is_general_opening(query):
                opening_episodes = [dict(r) for r in db.execute("""
                    SELECT * FROM memories WHERE user_id=? AND status='open' AND kind='episode'
                    ORDER BY created_at DESC, rowid DESC LIMIT ?""", (user_id, limit))]
        semantic = self._semantic_matches(user_id, query)
        ranked = {kind: fuse_rankings([m for m in matches if m["kind"] == kind],
                                     [m for m in semantic if m["kind"] == kind])
                  for kind in ("preference", "episode")}
        # Matching preferences win over recency; episodes cannot crowd them out.
        preferred = unique_memories(ranked["preference"] + preferences)[:2]
        episodes = recent + ranked["episode"] + opening_episodes
        selected = unique_memories(preferred + episodes)[:limit]
        if not selected:
            return []
        placeholders = ",".join("?" for _ in selected)
        with self.connect() as db:
            current = {r["id"]: dict(r) for r in db.execute(
                f"SELECT * FROM memories WHERE user_id=? AND id IN ({placeholders})",
                (user_id, *(m["id"] for m in selected)))}
        # Never return a candidate that changed while the embedding model was running.
        return [current[m["id"]] for m in selected if m["id"] in current
                and memory_signature(current[m["id"]]) == memory_signature(m)]


def unique_memories(memories):
    return list({m["id"]: m for m in memories}.values())


def memory_document(memory):
    return (memory["topic"] + "\n" if memory["topic"] else "") + memory["quote"]


def memory_signature(memory):
    fields = (memory["quote"], memory["topic"], memory["kind"], memory["status"])
    return hashlib.sha256(repr(fields).encode("utf-8")).hexdigest()


def fuse_rankings(lexical, semantic):
    """Reciprocal rank fusion; cosine is a relevance filter, not a probability."""
    candidates, scores = {}, {}
    for ranking in (lexical, semantic):
        for rank, memory in enumerate(ranking, 1):
            candidates.setdefault(memory["id"], memory)
            scores[memory["id"]] = scores.get(memory["id"], 0) + 1 / (60 + rank)
    return sorted(candidates.values(), key=lambda m: scores[m["id"]], reverse=True)
