import argparse
import os
from pathlib import Path
import sys

from .engine import Companion
from .embeddings import DEFAULT_EMBEDDING_MODEL, EmbeddingClient
from .model import DEFAULT_MODEL, OllamaClient
from .robot import Robot
from .server import Server
from .speech import Speech
from .store import MemoryStore


def main():
    parser = argparse.ArgumentParser(description="ResPaw: conversación y memoria local")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--model", default=os.environ.get("RESPAW_MODEL", DEFAULT_MODEL))
    parser.add_argument("--ollama", default="http://127.0.0.1:11434")
    parser.add_argument("--embedding-model", default=os.environ.get("RESPAW_EMBEDDING_MODEL", DEFAULT_EMBEDDING_MODEL))
    parser.add_argument("--no-semantic-memory", action="store_true", help="Usar solo búsqueda por palabras y contexto")
    parser.add_argument("--semantic-threshold", type=float, default=0.42, help="Similitud mínima para recuerdos semánticos")
    default_data = Path.home() / ("Library/Application Support/ResPaw" if sys.platform == "darwin" else ".local/share/respaw")
    parser.add_argument("--data-dir", type=Path, default=default_data)
    parser.add_argument("--device", help="Puerto USB del Mega con firmware v2; omitir para simular")
    parser.add_argument("--whisper-model", default=os.environ.get("RESPAW_WHISPER_MODEL"))
    parser.add_argument("--whisper-cli", default=os.environ.get("RESPAW_WHISPER_CLI"))
    args = parser.parse_args()
    model = OllamaClient(args.ollama, args.model)
    embedder = None if args.no_semantic_memory else EmbeddingClient(args.ollama, args.embedding_model)
    store = MemoryStore(args.data_dir / "memory.sqlite3", embedder, args.semantic_threshold)
    store.check_semantics()
    robot = Robot(args.device)
    whisper_model = args.whisper_model or str(args.data_dir / "speech/ggml-base.bin")
    whisper_cli = args.whisper_cli
    bundled_cli = args.data_dir / "tools/whisper.cpp-1.9.4/build/bin/whisper-cli"
    if not whisper_cli and bundled_cli.is_file():
        whisper_cli = str(bundled_cli)
    speech = Speech(whisper_model, whisper_cli)
    app = Companion(store, model, robot, speech)
    server = Server(("127.0.0.1", args.port), app)
    print(f"ResPaw: http://127.0.0.1:{server.server_port}", flush=True)
    print(f"Modelo local: {args.model}; robot: {'USB' if args.device else 'simulado'}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        speech.stop()
        robot.close()


if __name__ == "__main__":
    main()
