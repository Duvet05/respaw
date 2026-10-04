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


def arguments(argv=None):
    parser = argparse.ArgumentParser(description="ResPaw: conversación, memoria y cuerpo")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--provider", choices=("ollama", "openai"), default="ollama")
    parser.add_argument("--model", default=os.environ.get("RESPAW_MODEL"))
    parser.add_argument("--model-timeout", type=float, default=75, help="Tiempo máximo de inferencia en segundos")
    parser.add_argument("--ollama", default="http://127.0.0.1:11434")
    parser.add_argument("--embedding-model", default=os.environ.get("RESPAW_EMBEDDING_MODEL", DEFAULT_EMBEDDING_MODEL))
    parser.add_argument("--no-semantic-memory", action="store_true", help="Usar solo búsqueda por palabras y contexto")
    parser.add_argument("--semantic-threshold", type=float, default=0.42, help="Similitud mínima para recuerdos semánticos")
    default_data = Path.home() / ("Library/Application Support/ResPaw" if sys.platform == "darwin" else ".local/share/respaw")
    parser.add_argument("--data-dir", type=Path, default=default_data)
    transport = parser.add_mutually_exclusive_group()
    transport.add_argument("--device", help="Puerto USB del Mega con firmware v2")
    transport.add_argument("--robot-link", help="Operador del enlace Pico, por ejemplo ws://127.0.0.1:8767/operator")
    parser.add_argument("--robot-token-file", type=Path, help="Archivo privado con el token del enlace")
    parser.add_argument("--speech-provider", choices=("local", "cloud"), default="local")
    parser.add_argument("--whisper-model", default=os.environ.get("RESPAW_WHISPER_MODEL"))
    parser.add_argument("--whisper-cli", default=os.environ.get("RESPAW_WHISPER_CLI"))
    args = parser.parse_args(argv)
    if not 1 <= args.model_timeout <= 300:
        parser.error("--model-timeout debe estar entre 1 y 300 segundos.")
    if bool(args.robot_link) != bool(args.robot_token_file):
        parser.error("--robot-link y --robot-token-file se usan juntos.")
    return args


def main(argv=None):
    args = arguments(argv)
    if args.provider == "openai":
        from .cloud_model import CloudClient, DEFAULT_CLOUD_MODEL
        model = CloudClient(model=args.model or DEFAULT_CLOUD_MODEL, timeout=args.model_timeout)
    else:
        model = OllamaClient(args.ollama, args.model or DEFAULT_MODEL, timeout=args.model_timeout)
    embedder = (None if args.no_semantic_memory or args.provider != "ollama"
                else EmbeddingClient(args.ollama, args.embedding_model))
    store = MemoryStore(args.data_dir / "memory.sqlite3", embedder, args.semantic_threshold)
    store.check_semantics()
    if args.robot_link:
        from .network_robot import NetworkRobot
        robot = NetworkRobot(args.robot_link, token_file=args.robot_token_file)
    else:
        robot = Robot(args.device)
    whisper_model = args.whisper_model or str(args.data_dir / "speech/ggml-base.bin")
    whisper_cli = args.whisper_cli
    bundled_cli = args.data_dir / "tools/whisper.cpp-1.9.4/build/bin/whisper-cli"
    if not whisper_cli and bundled_cli.is_file():
        whisper_cli = str(bundled_cli)
    if args.speech_provider == "cloud":
        from .cloud_speech import CloudSpeech
        speech = CloudSpeech()
    else:
        speech = Speech(whisper_model, whisper_cli)
    app = Companion(store, model, robot, speech)
    server = Server(("127.0.0.1", args.port), app)
    print(f"ResPaw: http://127.0.0.1:{server.server_port}", flush=True)
    transport = "enlace Pico" if args.robot_link else "USB" if args.device else "simulado"
    print(f"Modelo: {model.model} ({args.provider}); robot: {transport}", flush=True)
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
