"""Small Spanish retrieval comparison, separate from threshold calibration.

Uses synthetic episodes, no opening/recency fallback, and no conversation model.
The scores describe these fixtures, not a general memory benchmark.
"""

import argparse
import json
from pathlib import Path
import tempfile
import time

from respaw.embeddings import DEFAULT_EMBEDDING_MODEL, EmbeddingClient
from respaw.store import MemoryStore


EPISODES = {
    "rent": "No me alcanza para pagar el alquiler este mes.",
    "sleep": "Últimamente me despierto varias veces por la noche.",
    "math": "Desaprobé la evaluación de cálculo del viernes.",
    "pet": "Mi perrita Luna murió hace tres días.",
    "speaking": "Cuando expongo delante de mi clase se me quiebra la voz.",
    "work": "El lunes empiezo mis prácticas en una empresa de diseño.",
    "music": "Dejé de tocar el piano y quiero retomarlo pronto.",
    "family": "Mi hermano dejó de hablarme desde nuestra pelea.",
}
QUERIES = [
    ("rent", "¿Qué te comenté de mis dificultades para cubrir la renta?"),
    ("sleep", "¿Recuerdas mis problemas para descansar sin interrupciones?"),
    ("math", "Sigo dándole vueltas a mis dificultades con las matemáticas."),
    ("pet", "Me cuesta aceptar la muerte de mi mascota."),
    ("speaking", "¿Qué me pasaba al hablar en público?"),
    ("work", "Estoy pensando en ese primer empleo para adquirir experiencia."),
    ("music", "¿Qué instrumento musical pensaba volver a practicar?"),
    ("family", "¿Te había dicho que estaba distanciada de un familiar?"),
    (None, "¿Qué desayuné ayer?"),
    (None, "¿Dónde compré mi mochila?"),
    (None, "¿Cuál es mi color favorito?"),
    (None, "¿Qué película vi contigo?"),
    (None, "¿Cómo se llama mi dentista?"),
    (None, "¿Cuándo vence mi pasaporte?"),
]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default=DEFAULT_EMBEDDING_MODEL)
    parser.add_argument("--threshold", type=float, default=0.42)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    embedder = EmbeddingClient(model=args.model)
    identity = embedder.identity()
    cases = []
    with tempfile.TemporaryDirectory(prefix="respaw-retrieval-") as temp:
        path = Path(temp) / "memory.sqlite3"
        hybrid = MemoryStore(path, embedder, args.threshold)
        lexical = MemoryStore(path)
        person = hybrid.create_profile("Perfil sintético")["id"]
        other = hybrid.create_profile("Otro perfil sintético")["id"]
        labels = {}
        for label, quote in EPISODES.items():
            memory = hybrid.save(person, quote, "synthetic-earlier-session")
            labels[memory["id"]] = label
        for expected, query in QUERIES:
            started = time.monotonic()
            baseline = lexical.retrieve(person, query, opening=False)
            retrieved = hybrid.retrieve(person, query, opening=False)
            if not hybrid.semantic_status["ready"]:
                raise RuntimeError(hybrid.semantic_status["error"])
            case = {"query": query, "expected": expected,
                    "lexical": [labels[m["id"]] for m in baseline],
                    "hybrid": [labels[m["id"]] for m in retrieved],
                    "latency_ms": round(1000 * (time.monotonic() - started))}
            cases.append(case)
            print(json.dumps(case, ensure_ascii=False), flush=True)
        assert not hybrid.retrieve(other, QUERIES[0][1]), "Se cruzaron perfiles."
        hybrid.forget(person)
        assert not hybrid.retrieve(person, QUERIES[0][1]), "Reapareció un recuerdo olvidado."

    positives = [case for case in cases if case["expected"]]
    negatives = [case for case in cases if case["expected"] is None]
    metrics = {mode: {
        "recall_at_4": sum(case["expected"] in case[mode] for case in positives) / len(positives),
        "top_1": sum(case[mode][:1] == [case["expected"]] for case in positives) / len(positives),
        "unrelated_with_candidates": sum(bool(case[mode]) for case in negatives),
    } for mode in ("lexical", "hybrid")}
    report = {"model": identity, "threshold": args.threshold,
              "scope": "8 synthetic Spanish paraphrases and 6 unrelated queries; not a benchmark",
              "positive_count": len(positives), "negative_count": len(negatives),
              "metrics": metrics, "cases": cases}
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(metrics, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
