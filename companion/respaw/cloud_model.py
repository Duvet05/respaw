"""Explicit OpenAI Responses provider; SQLite memory stays in the Companion."""

import json
import os
import re
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from .model import ModelError, reply_context, validate_reply

DEFAULT_CLOUD_MODEL = "gpt-4o-mini"


class CloudNoRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ModelError("OpenAI intentó redirigir la conexión; se detuvo la petición.")


class CloudClient:
    supports_robot_context = True

    def __init__(self, model=DEFAULT_CLOUD_MODEL, api_key=None, timeout=45):
        if not isinstance(model, str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,160}", model):
            raise ValueError("Modelo OpenAI no válido.")
        key = os.environ.get("OPENAI_API_KEY") if api_key is None else api_key
        if api_key is None and key == "":
            key = None
        if key is not None and (not isinstance(key, str) or not re.fullmatch(r"[A-Za-z0-9_-]{10,512}", key)):
            raise ValueError("Configura OPENAI_API_KEY para usar el proveedor OpenAI.")
        self.model, self.api_key, self.timeout = model, key, timeout
        self.opener = build_opener(ProxyHandler({}), CloudNoRedirects())
        self.available = False

    def request(self, data):
        if self.api_key is None:
            raise ModelError("Configura OPENAI_API_KEY para activar el modelo GPT.")
        request = Request("https://api.openai.com/v1/responses",
                          data=json.dumps(data, ensure_ascii=False, allow_nan=False).encode("utf-8"),
                          headers={"Authorization": "Bearer " + self.api_key, "Content-Type": "application/json"})
        try:
            with self.opener.open(request, timeout=self.timeout) as response:
                body = response.read(1_000_001)
            if len(body) > 1_000_000:
                raise ModelError("La respuesta de OpenAI es demasiado grande.")
            result = json.loads(body)
            if not isinstance(result, dict):
                raise ValueError("Invalid provider response")
            return result
        except HTTPError as error:
            messages = {401: "OpenAI rechazó la clave configurada.",
                        403: "La cuenta no tiene acceso a este modelo OpenAI.",
                        429: "OpenAI no pudo atender la petición por cuota o límite de uso."}
            raise ModelError(messages.get(error.code, "OpenAI no pudo atender la petición.")) from None
        except (URLError, OSError, ValueError) as error:
            raise ModelError("El proveedor OpenAI no está disponible.") from error

    def check(self):
        # No speculative requests or credential disclosure in a UI health check.
        result = {"ready": self.available, "configured": self.api_key is not None, "provider": "openai", "model": self.model}
        if self.api_key is None:
            result["error"] = "Configura OPENAI_API_KEY para activar el modelo GPT."
        elif not self.available:
            result["error"] = "OpenAI configurado; su disponibilidad se comprobará al conversar."
        return result

    def reply(self, name, history, memories, robot_context=None):
        if self.api_key is None:
            raise ModelError("Configura OPENAI_API_KEY para activar el modelo GPT.")
        schema, messages, no_questions = reply_context(name, history, memories, robot_context)
        self.available = False
        result = self.request({
            "model": self.model, "input": messages, "store": False,
            "max_output_tokens": 1200,
            "text": {"format": {"type": "json_schema", "name": "respaw_reply", "schema": schema, "strict": True}},
        })
        try:
            if result.get("status") != "completed" or not isinstance(result.get("output"), list):
                raise ValueError("Incomplete provider response")
            texts = []
            for item in result["output"]:
                if not isinstance(item, dict):
                    raise ValueError("Invalid output item")
                if item.get("type") != "message":
                    continue
                if item.get("role") != "assistant" or not isinstance(item.get("content"), list):
                    raise ValueError("Invalid output message")
                for content in item["content"]:
                    if not isinstance(content, dict) or content.get("type") != "output_text" or not isinstance(content.get("text"), str):
                        raise ValueError("Refused or invalid output")
                    texts.append(content["text"])
            if len(texts) != 1:
                raise ValueError("Missing or ambiguous structured output")
            answer = json.loads(texts[0])
            answer = validate_reply(answer, memories, allow_questions=not no_questions)
            self.available = True
            return answer
        except (KeyError, TypeError, ValueError) as error:
            raise ModelError("OpenAI no produjo una respuesta válida; no se ejecutó ninguna acción.") from error
