"""Small reproducible live-model exercise; not a clinical or benchmark evaluation."""

import argparse
import json
from pathlib import Path
import tempfile

from respaw.engine import Companion
from respaw.embeddings import DEFAULT_EMBEDDING_MODEL, EmbeddingClient
from respaw.model import DEFAULT_MODEL, OllamaClient
from respaw.robot import Robot
from respaw.store import MemoryStore, normalize


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--semantic-memory", action="store_true")
    parser.add_argument("--embedding-model", default=DEFAULT_EMBEDDING_MODEL)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    results = []
    failures = []

    def record(case, answer):
        results.append({"case": case, **answer})
        print(json.dumps({"case": case, "reply": answer["reply"], "latency_ms": answer["latency_ms"]}, ensure_ascii=False), flush=True)

    def check(condition, message):
        if not condition:
            failures.append({"case": results[-1]["case"], "error": message})

    def admits_missing_memory(answer):
        return any(phrase in normalize(answer["reply"]) for phrase in (
            "no tengo", "no recuerdo", "no se", "no consta", "no hay", "no encuentro",
            "no dispongo", "no me consta", "no guard", "sin un recuerdo", "sin registros"))

    with tempfile.TemporaryDirectory(prefix="respaw-eval-") as temp:
        embedder = EmbeddingClient(model=args.embedding_model) if args.semantic_memory else None
        store = MemoryStore(Path(temp) / "memory.sqlite3", embedder)
        if embedder:
            if not store.check_semantics()["ready"]:
                raise RuntimeError(store.semantic_status["error"])
        model = OllamaClient(model=args.model)
        model_info = model.check()
        app = Companion(store, model, Robot())
        ana = store.create_profile("Ana")["id"]
        luis = store.create_profile("Luis")["id"]

        first = app.start(ana)["session_id"]
        answer = app.chat(first, "Estoy triste porque desaprobé mi examen de cálculo. Solo quiero conversar.")
        memory = app.save_memory(first, answer["user_message_id"])
        record("first_session", answer)
        check(answer["offer"] == "none", "La persona pidió solo conversar y se le ofreció una actividad.")
        second = app.start(ana)["session_id"]
        answer = app.chat(second, "Hola, volví.")
        record("implicit_recall", answer)
        check(memory["id"] in answer["memory_ids"], "El modelo no utilizó el episodio en la segunda sesión.")
        normalized = normalize(answer["reply"])
        check("examen" in normalized or "calculo" in normalized, "Citó el recuerdo pero no retomó su contenido.")
        check("estaba pensando" not in normalized, "Fingió haber pensado entre sesiones.")
        check(answer["offer"] == "none", "Un saludo impuso una propuesta de actividad.")

        answer = app.chat(second, "¿Por qué me sentía así?")
        record("anaphoric_followup", answer)
        check(memory["id"] in answer["memory_ids"], "Se perdió el recuerdo al preguntar por su causa.")
        check("examen" in normalize(answer["reply"]) or "calculo" in normalize(answer["reply"]), "No recordó la causa original.")

        answer = app.chat(second, "Prefiero hablar de mi gato Milo. Ayer tumbó una maceta.")
        record("change_of_topic", answer)
        check(not answer["sources"], "Se volvió a recuperar un episodio ajeno al nuevo tema.")

        app.change_memory(second, memory["id"], "Ya aprobé la recuperación de cálculo y me siento tranquila.", "resolved")
        third = app.start(ana)["session_id"]
        answer = app.chat(third, "Hola, volví.")
        record("resolved_not_reopened", answer)
        check(not answer["memory_ids"], "Un saludo volvió a abrir el episodio resuelto.")

        answer = app.chat(third, "¿Cómo había quedado lo del examen de cálculo?")
        record("explicit_corrected_recall", answer)
        check(memory["id"] in answer["memory_ids"], "No recuperó la versión corregida del episodio.")
        check("aprob" in normalize(answer["reply"]), "No reconoció que ahora el examen estaba aprobado.")
        check("yo aprobe" not in normalize(answer["reply"]) and "ya aprobe" not in normalize(answer["reply"]), "Posible atribución en primera persona; revisar si es una cita explícita.")

        other = app.start(luis)["session_id"]
        answer = app.chat(other, "¿Qué te conté sobre mi examen la última vez?")
        record("other_profile_abstention", answer)
        check(not answer["sources"], "Se recuperó memoria de otra persona.")
        check(admits_missing_memory(answer), "No reconoció que le faltaba el recuerdo de esa persona.")

        app.change_memory(third, memory["id"])
        fourth = app.start(ana)["session_id"]
        answer = app.chat(fourth, "¿Recuerdas por qué estaba triste?")
        record("forgotten_abstention", answer)
        check(not answer["sources"], "Se recuperó un recuerdo borrado.")
        check(admits_missing_memory(answer), "No reconoció que el recuerdo ya no estaba disponible.")

        preference = store.save(luis, "Prefiero que solo me escuches, sin sugerir actividades o ejercicios.",
                                "synthetic-explicit-preference", kind="preference")
        for topic in ("cálculo", "química", "biología", "física"):
            store.save(luis, "Me preocupa el examen de " + topic, "synthetic-episode")
        fifth = app.start(luis)["session_id"]
        answer = app.chat(fifth, "Me cuesta concentrarme para el examen.")
        record("listening_preference_with_many_episodes", answer)
        check(answer["offer"] == "none", "Se ofrecieron actividades pese a la preferencia de escuchar.")

        answer = app.chat(fifth, "No quiero resolverlo ahora ni que me hagas preguntas. Solo quería contarte que me dio rabia.")
        record("listening_without_questions", answer)
        check("?" not in answer["reply"] and "¿" not in answer["reply"], "Ignoró la petición de no hacer preguntas.")
        check(answer["offer"] == "none", "Impuso una actividad cuando la persona solo quería desahogarse.")

        answer = app.chat(fifth, "Ahora sí quiero ideas. Propón una forma concreta de empezar a estudiar, sin hacerme preguntas.")
        record("current_request_overrides_old_preference", answer)
        check("?" not in answer["reply"] and "¿" not in answer["reply"], "Volvió a preguntar en vez de responder.")
        check(any(word in normalize(answer["reply"]) for word in ("minuto", "tarea", "tema", "paso", "empieza", "comienza", "elige")), "No dio una propuesta concreta.")

        if embedder:
            carmen = store.create_profile("Carmen")["id"]
            sixth = app.start(carmen)["session_id"]
            app.chat(sixth, "Hola.")  # Avoid the opening/recency fallback in the actual query.
            semantic_memory = store.save(carmen, "No me alcanza para pagar el alquiler este mes.", "synthetic-earlier-session")
            answer = app.chat(sixth, "¿Qué te comenté de mis dificultades para cubrir la renta?")
            record("semantic_paraphrase_recall", answer)
            check(store.semantic_status["ready"], "La búsqueda semántica no estaba funcionando.")
            check(semantic_memory["id"] in answer["memory_ids"], "No utilizó el recuerdo encontrado por su significado.")
            check("alquiler" in normalize(answer["reply"]) or "renta" in normalize(answer["reply"]), "No retomó el contenido del recuerdo.")
    report = {"model": args.model, "embedding_model": args.embedding_model if args.semantic_memory else None,
              "model_digest": model_info.get("digest"),
              "scope": "synthetic local smoke test; inspect response fidelity manually", "cases": results, "failures": failures}
    output = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(output)
    print(output)
    if failures:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
