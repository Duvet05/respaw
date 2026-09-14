"""Explicit one-time downloads for optional macOS/Apple Silicon capabilities."""

import argparse
import hashlib
from pathlib import Path
import platform
import shutil
import subprocess
import tarfile
from urllib.request import urlopen

ARDUINO_URL = "https://github.com/arduino/arduino-cli/releases/download/v1.5.1/arduino-cli_1.5.1_macOS_ARM64.tar.gz"
ARDUINO_SHA = "cb952e8c1621c95ef5f1d17831c945e3d0ec5973f89c557a7ec8feb9c4f7d4c9"
WHISPER_URL = "https://codeload.github.com/ggml-org/whisper.cpp/tar.gz/refs/tags/v1.9.4"
WHISPER_SHA = "57e280cee375ab02425b806ad5146b99f6eb9357e3c2b31357c8a6af2e2e44ae"
BASE_URL = "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-base.bin"
BASE_SHA = "60ed5bc3dd14eea856493d334349b405782ddcaf0028d4b5df4088345fba2efe"


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def download(url, target, expected):
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists() and digest(target) == expected:
        print("Verificado:", target.name, flush=True)
        return
    print("Descargando:", target.name, flush=True)
    temporary = target.with_suffix(target.suffix + ".download")
    with urlopen(url, timeout=60) as source, temporary.open("wb") as destination:
        shutil.copyfileobj(source, destination, length=1024 * 1024)
    if digest(temporary) != expected:
        temporary.unlink()
        raise RuntimeError("El checksum no coincide: " + target.name)
    temporary.replace(target)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--speech", action="store_true", help="whisper.cpp 1.9.4 y modelo base multilingüe (~148 MB)")
    parser.add_argument("--arduino", action="store_true", help="Arduino CLI 1.5.1")
    parser.add_argument("--data-dir", type=Path, default=Path.home() / "Library/Application Support/ResPaw")
    args = parser.parse_args()
    if not args.speech and not args.arduino:
        parser.error("Elige --speech o --arduino.")
    if platform.system() != "Darwin" or platform.machine() != "arm64":
        parser.error("Este instalador es para macOS Apple Silicon; consulta la guía para configuración manual.")
    tools = args.data_dir / "tools"
    tools.mkdir(parents=True, exist_ok=True)
    if args.arduino:
        archive = tools / "arduino-cli.tar.gz"
        download(ARDUINO_URL, archive, ARDUINO_SHA)
        with tarfile.open(archive) as source:
            data = source.extractfile("arduino-cli").read()
        target = tools / "arduino-cli"
        target.write_bytes(data)
        target.chmod(0o755)
    if args.speech:
        for binary in ("cmake", "ffmpeg", "c++"):
            if not shutil.which(binary):
                parser.error("Falta instalar " + binary)
        archive = tools / "whisper-v1.9.4.tar.gz"
        download(WHISPER_URL, archive, WHISPER_SHA)
        source = tools / "whisper.cpp-1.9.4"
        if not source.exists():
            with tarfile.open(archive) as tar:
                tar.extractall(tools, filter="data")
        subprocess.run(["cmake", "-S", str(source), "-B", str(source / "build"),
                        "-DCMAKE_BUILD_TYPE=Release", "-DWHISPER_BUILD_TESTS=OFF",
                        "-DGGML_METAL=ON", "-DGGML_METAL_EMBED_LIBRARY=ON"], check=True)
        subprocess.run(["cmake", "--build", str(source / "build"), "--target", "whisper-cli", "-j", "4"], check=True)
        download(BASE_URL, args.data_dir / "speech/ggml-base.bin", BASE_SHA)
    print("Preparación terminada. Los archivos se conservaron fuera del repositorio.")


if __name__ == "__main__":
    main()
