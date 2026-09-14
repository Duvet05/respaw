"""Optional offline macOS speech and whisper.cpp file transcription."""

from pathlib import Path
import shutil
import subprocess
import tempfile
import threading


class Speech:
    def __init__(self, whisper_model=None, whisper_cli=None):
        self.model = Path(whisper_model).expanduser() if whisper_model else None
        self.whisper = whisper_cli or shutil.which("whisper-cli")
        self.ffmpeg = shutil.which("ffmpeg")
        self.say = shutil.which("say")
        self.process = None
        self.lock = threading.RLock()
        self.transcribing = threading.Lock()

    def status(self):
        return {"tts": bool(self.say),
                "stt": bool(self.whisper and self.ffmpeg and self.model and self.model.is_file())}

    def stop(self):
        with self.lock:
            if self.process and self.process.poll() is None:
                self.process.terminate()
                try:
                    self.process.wait(timeout=1)
                except subprocess.TimeoutExpired:
                    self.process.kill()
                    self.process.wait(timeout=1)
            self.process = None

    def speak(self, text):
        if not self.say:
            raise ValueError("La voz local de macOS no está disponible.")
        if not isinstance(text, str) or not 1 <= len(text) <= 1800:
            raise ValueError("Texto de voz no válido.")
        with self.lock:
            self.stop()
            # stdin keeps text away from option/command parsing. No shell or cloud TTS.
            self.process = subprocess.Popen([self.say, "-v", "Paulina", "-r", "175"],
                                            stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                                            stderr=subprocess.DEVNULL)
            self.process.stdin.write(text.encode("utf-8"))
            self.process.stdin.close()

    def transcribe(self, audio, suffix):
        if not self.status()["stt"]:
            raise ValueError("Configura whisper.cpp y un modelo local para usar el micrófono.")
        if suffix not in (".wav", ".webm", ".mp4", ".ogg") or not 1 <= len(audio) <= 8_000_000:
            raise ValueError("Audio no válido o demasiado largo.")
        if not self.transcribing.acquire(blocking=False):
            raise ValueError("Ya se está transcribiendo un audio.")
        try:
            with tempfile.TemporaryDirectory(prefix="respaw-audio-") as tmp:
                source, wav = Path(tmp) / ("input" + suffix), Path(tmp) / "mono.wav"
                output = Path(tmp) / "transcript"
                source.write_bytes(audio)
                container = {".wav": "wav", ".webm": "matroska", ".mp4": "mov", ".ogg": "ogg"}[suffix]
                # Force an audio container and prevent media inputs from opening network URLs.
                subprocess.run([self.ffmpeg, "-nostdin", "-v", "error", "-y",
                                "-protocol_whitelist", "file,pipe", "-f", container, "-i", str(source),
                                "-t", "35", "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le", str(wav)],
                               check=True, capture_output=True, timeout=20)
                subprocess.run([self.whisper, "-m", str(self.model), "-f", str(wav), "-l", "es",
                                "-otxt", "-of", str(output), "-np", "-nt"],
                               check=True, capture_output=True, timeout=90)
                text = output.with_suffix(".txt").read_text().strip()
                if not text or len(text) > 2000:
                    raise ValueError("No pude obtener una frase clara de ese audio.")
                return text
        except (subprocess.SubprocessError, OSError) as error:
            raise ValueError("No se pudo transcribir el audio localmente.") from error
        finally:
            self.transcribing.release()
