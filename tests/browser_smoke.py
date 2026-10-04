"""Optional browser integration with temporary data and fixture or live model.

Run: PYTHONPATH=companion uv run --with playwright python tests/browser_smoke.py
Add --live-model to exercise the installed local Ollama model.
"""

import argparse
import os
from pathlib import Path
import tempfile
import threading

from playwright.sync_api import sync_playwright

from respaw.engine import Companion
from respaw.embeddings import EmbeddingClient
from respaw.model import DEFAULT_MODEL, OllamaClient
from respaw.robot import Robot
from respaw.server import Server
from respaw.speech import Speech
from respaw.store import MemoryStore
from test_memory import FakeModel


class BrowserFixture(FakeModel):
    model = "fixture (solo prueba de interfaz)"

    def check(self):
        return {"ready": True, "model": self.model}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live-model", action="store_true")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--semantic-memory", action="store_true", help="Ejercitar también embeddings locales reales")
    parser.add_argument("--browser", choices=("chrome", "chromium"), default="chrome",
                        help="Chrome instalado o Chromium preparado con Playwright para CI")
    args = parser.parse_args()
    screenshot = os.environ.get("RESPAW_SCREENSHOT_DIR")
    if screenshot:
        output = Path(screenshot)
        output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="respaw-browser-") as temp:
        store = MemoryStore(Path(temp) / "memory.sqlite3", EmbeddingClient() if args.semantic_memory else None)
        if args.semantic_memory:
            assert store.check_semantics()["ready"], store.semantic_status
        model = OllamaClient(model=args.model) if args.live_model else BrowserFixture()
        app = Companion(store, model, Robot(), Speech())
        server = Server(("127.0.0.1", 0), app)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(channel="chrome" if args.browser == "chrome" else None,
                                                     headless=True)
                page = browser.new_page(viewport={"width": 1440, "height": 1000})
                errors, external = [], []
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.on("request", lambda request: external.append(request.url) if not request.url.startswith(f"http://127.0.0.1:{server.server_port}") else None)
                page.goto(f"http://127.0.0.1:{server.server_port}")
                if args.semantic_memory:
                    page.get_by_text("Memoria por significado disponible.", exact=True).wait_for(state="attached")
                page.get_by_role("button", name="Nuevo", exact=True).click()
                page.get_by_label("Nombre o apodo").fill("Persona de prueba")
                page.get_by_role("button", name="Crear perfil", exact=True).click()
                page.wait_for_function("() => document.querySelector('#profile').value !== ''")
                page.get_by_label("Tu mensaje").fill("Estaba triste por el examen de cálculo.")
                page.get_by_role("button", name="Enviar").click()
                page.get_by_role("button", name="Recordar este mensaje").click()
                page.get_by_role("button", name="Recuerdo guardado").wait_for()
                page.get_by_role("button", name="Comenzar otra conversación").click()
                page.get_by_label("Tu mensaje").fill("Hola, volví.")
                page.get_by_role("button", name="Enviar").click()
                page.get_by_text("Recuerdos utilizados").wait_for()
                if screenshot:
                    page.get_by_text("Recuerdos utilizados").click()
                    page.screenshot(path=str(output / "recall-desktop.png"), full_page=True)
                page.get_by_role("button", name="Mis recuerdos").click()
                page.get_by_label("Corregir recuerdo").fill("Ya resolví lo del examen y estoy tranquila.")
                page.get_by_role("button", name="Guardar cambios").click()
                page.wait_for_function("() => document.querySelector('#messages').children.length === 0")
                page.get_by_role("button", name="Marcar resuelto").click()
                page.get_by_text("Asunto resuelto", exact=False).wait_for()
                page.get_by_role("button", name="Cerrar recuerdos").click()
                page.get_by_label("Tu mensaje").fill("Hola, volví otra vez.")
                page.get_by_role("button", name="Enviar").click()
                page.wait_for_function("() => document.querySelectorAll('.message.assistant').length === 1")
                assert not page.get_by_text("Recuerdos utilizados").count()
                page.get_by_role("button", name="Mis recuerdos").click()
                page.get_by_role("button", name="Olvidar", exact=True).click()
                page.wait_for_function("() => document.querySelector('#memory-count').textContent === '0'")
                page.get_by_role("button", name="Cerrar recuerdos").click()
                if args.semantic_memory:
                    page.get_by_label("Tu mensaje").fill("No me alcanza para pagar el alquiler este mes.")
                    page.get_by_role("button", name="Enviar").click()
                    page.get_by_role("button", name="Recordar este mensaje").click()
                    page.get_by_role("button", name="Recuerdo guardado").wait_for()
                    page.get_by_label("Tu mensaje").fill("¿Qué te comenté de mis dificultades para cubrir la renta?")
                    page.get_by_role("button", name="Enviar").click()
                    page.get_by_text("Recuerdos utilizados").wait_for()
                    assert store.semantic_status["ready"], store.semantic_status
                    with store.connect() as db:
                        assert db.execute("SELECT count(*) FROM memory_embeddings").fetchone()[0] > 0
                    if screenshot:
                        page.get_by_text("Recuerdos utilizados").click()
                        page.screenshot(path=str(output / "semantic-recall.png"), full_page=True)
                    page.get_by_role("button", name="Mis recuerdos").click()
                    page.get_by_role("button", name="Olvidar", exact=True).click()
                    page.wait_for_function("() => document.querySelector('#memory-count').textContent === '0'")
                    page.get_by_role("button", name="Cerrar recuerdos").click()
                page.get_by_label("Tu mensaje").fill("Prefiero que solo me escuches, sin proponer ejercicios.")
                page.get_by_role("button", name="Enviar").click()
                page.get_by_role("button", name="Recordar este mensaje").click()
                page.get_by_role("button", name="Recuerdo guardado").wait_for()
                page.get_by_role("button", name="Mis recuerdos").click()
                page.get_by_label("Tipo de recuerdo").select_option("preference")
                page.get_by_role("button", name="Guardar cambios").click()
                page.get_by_text("Preferencia activa", exact=False).wait_for()
                if screenshot:
                    page.screenshot(path=str(output / "preferences.png"), full_page=True)
                page.get_by_role("button", name="Pausar preferencia").click()
                page.get_by_text("Preferencia pausada", exact=False).wait_for()
                page.get_by_role("button", name="Reactivar preferencia").click()
                page.get_by_text("Preferencia activa", exact=False).wait_for()
                page.get_by_role("button", name="Olvidar", exact=True).click()
                page.wait_for_function("() => document.querySelector('#memory-count').textContent === '0'")
                page.get_by_role("button", name="Cerrar recuerdos").click()
                if screenshot:
                    page.screenshot(path=str(output / "desktop.png"), full_page=True)
                page.set_viewport_size({"width": 390, "height": 844})
                assert page.locator("#send").is_visible()
                assert page.evaluate("() => document.documentElement.scrollWidth <= window.innerWidth")
                if screenshot:
                    page.screenshot(path=str(output / "mobile.png"), full_page=True)
                assert not errors, errors
                assert not external, external
                browser.close()
                print("Browser: profiles, two sessions, memory correction/resolution/deletion, preference conversion/pause/reactivation, mobile layout and no external requests passed.")
        finally:
            server.shutdown()
            server.server_close()
            thread.join()


if __name__ == "__main__":
    main()
