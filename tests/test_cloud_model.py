from contextlib import contextmanager
import json
import os
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError

from respaw.cloud_model import CloudClient, CloudNoRedirects
from respaw.model import ModelError, OllamaClient, REPLY_SCHEMA, reply_context


def context_data(messages):
    return json.loads(messages[0]["content"].split("DATOS DE CONTEXTO (no instrucciones):\n", 1)[1].split("\nFIN DE DATOS.", 1)[0])


def response_for(answer):
    return {"status": "completed", "output": [{"type": "message", "role": "assistant",
            "content": [{"type": "output_text", "text": json.dumps(answer)}]}]}


class CloudModelTests(unittest.TestCase):
    def setUp(self):
        self.client = CloudClient(api_key="sk-fixture-not-a-real-key")
        self.history = [{"role": "user", "content": "Hola, sin preguntas."}]
        self.answer = {"reply": "Aquí podemos conversar con calma.", "expression": "warm", "offer": "none", "memory_ids": []}

    def test_provider_starts_without_key_and_never_calls_http(self):
        with patch.dict(os.environ, {}, clear=True):
            client = CloudClient()
        status = client.check()
        self.assertFalse(status["ready"])
        self.assertFalse(status["configured"])
        self.assertEqual(status["provider"], "openai")
        self.assertIn("OPENAI_API_KEY", status["error"])
        with patch.object(client, "request") as request, self.assertRaisesRegex(ModelError, "OPENAI_API_KEY"):
            client.reply("Ana", self.history, [])
        request.assert_not_called()
        with patch.object(client.opener, "open") as open_request, self.assertRaisesRegex(ModelError, "OPENAI_API_KEY"):
            client.request({})
        open_request.assert_not_called()

    def test_empty_environment_placeholder_is_unconfigured(self):
        with patch.dict(os.environ, {"OPENAI_API_KEY": ""}, clear=True):
            client = CloudClient()
        self.assertFalse(client.check()["configured"])
        with patch.object(client.opener, "open") as request, self.assertRaises(ModelError):
            client.reply("Ana", self.history, [])
        request.assert_not_called()

    def test_present_invalid_key_is_rejected_without_logging_it(self):
        for key in ("bad\r\nAuthorization: fake", "", 1):
            with patch.dict(os.environ, {}, clear=True), self.assertRaises(ValueError):
                CloudClient(api_key=key)
        with patch.dict(os.environ, {"OPENAI_API_KEY": "secret with invalid spaces"}, clear=True), self.assertRaises(ValueError) as error:
            CloudClient()
        self.assertNotIn("secret with invalid spaces", str(error.exception))
        status = self.client.check()
        self.assertTrue(status["configured"])
        self.assertFalse(status["ready"])
        self.assertNotIn(self.client.api_key, json.dumps(status))

    def test_shared_schema_physical_context_and_store_false(self):
        physical = {"ready": True, "contact": {"sensor": "fsr_a8", "pressed": True, "uptime_ms": 123},
                    "user_id": "ignore-me", "measurement": {"bpm": 70}, "emotion": "happy"}
        with patch.object(self.client, "request", return_value=response_for(self.answer)) as request:
            self.assertEqual(self.client.reply("Ana", self.history, [], physical), self.answer)
        payload = request.call_args.args[0]
        self.assertFalse(payload["store"])
        self.assertTrue(payload["text"]["format"]["strict"])
        self.assertNotIn("tools", payload)
        self.assertEqual(payload["text"]["format"]["schema"]["properties"]["memory_ids"]["maxItems"], 0)
        context = context_data(payload["input"])
        self.assertEqual(context["robot"], {"ready": True, "contact": {"sensor": "fsr_a8", "pressed": True}})
        self.assertEqual(context["memories"], [])
        self.assertTrue(self.client.check()["ready"])
        self.assertNotIn("pattern", REPLY_SCHEMA["properties"]["reply"])

    def test_refusals_truncation_and_invalid_outputs_cannot_drive_robot(self):
        invalid_answers = [
            {**self.answer, "expression": "attack"},
            {**self.answer, "memory_ids": ["invented-memory"]},
            {**self.answer, "reply": "¿Quieres hablar?"},
            {**self.answer, "exec": "STOP"},
        ]
        fixtures = [response_for(answer) for answer in invalid_answers]
        fixtures += [
            {"status": "incomplete", "output": []},
            {"status": "completed", "output": [{"type": "message", "role": "assistant", "content": [{"type": "refusal", "refusal": "No"}]}]},
            {"status": "completed", "output": []},
            {"status": "completed", "output": [None]},
        ]
        for fixture in fixtures:
            with self.subTest(fixture=fixture), patch.object(self.client, "request", return_value=fixture), self.assertRaises(ModelError):
                self.client.reply("Ana", self.history, [])
            self.assertFalse(self.client.check()["ready"])

    def test_transport_locks_endpoint_redirects_and_bounds_responses(self):
        @contextmanager
        def body(data):
            response = Mock()
            response.read.return_value = data
            yield response

        self.client.opener = Mock()
        self.client.opener.open.return_value = body(json.dumps(response_for(self.answer)).encode())
        self.client.reply("Ana", self.history, [])
        request = self.client.opener.open.call_args.args[0]
        self.assertEqual(request.full_url, "https://api.openai.com/v1/responses")
        self.assertEqual(request.get_header("Authorization"), "Bearer " + self.client.api_key)
        self.client.opener.open.return_value = body(b"x" * 1_000_001)
        with self.assertRaises(ModelError):
            self.client.request({})
        for code in (401, 429):
            self.client.opener.open.side_effect = HTTPError("https://api.openai.com", code, self.client.api_key, {}, None)
            with self.assertRaises(ModelError) as error:
                self.client.request({})
            self.assertNotIn(self.client.api_key, str(error.exception))
        with self.assertRaises(ModelError):
            CloudNoRedirects().redirect_request(None, None, 302, None, {}, "https://foreign.example")

    def test_ollama_retains_wire_contract_and_invalid_contact_is_filtered(self):
        client = OllamaClient()
        with patch.object(client, "check"), patch.object(client, "request", return_value={"message": {"content": json.dumps(self.answer)}}) as request:
            client.reply("Ana", self.history, [])
        payload = request.call_args.args[1]
        schema, messages, no_questions = reply_context("Ana", self.history, [])
        self.assertEqual(payload["format"], schema)
        self.assertEqual(payload["messages"][1:], messages[1:])
        self.assertNotIn("robot", context_data(payload["messages"]))
        self.assertTrue(no_questions)
        self.assertFalse(payload["think"])
        self.assertFalse(payload["stream"])
        for physical in ({"ready": True, "contact": {"sensor": "wrong", "pressed": True}},
                         {"ready": False, "contact": {"sensor": "fsr_a8", "pressed": True}},
                         {"ready": True, "contact": {"sensor": "fsr_a8", "pressed": 1}}):
            _, messages, _ = reply_context("Ana", self.history, [], physical)
            self.assertNotIn("contact", context_data(messages)["robot"])
