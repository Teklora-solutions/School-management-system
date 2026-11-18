"""
apps/assistant/tests/test_providers.py
--------------------------------------
Adapter request/response shapes, proven with the HTTP helper patched out.
No test opens a socket.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase

from apps.assistant.providers import ErrorKind, ProviderError
from apps.assistant.providers import http
from apps.assistant.providers.deepseek import DeepSeekProvider
from apps.assistant.providers.fake import FakeProvider
from apps.assistant.providers.gemini import GeminiProvider
from apps.assistant.providers.openai import OpenAIProvider
from apps.assistant.providers.registry import build, model_for
from apps.assistant.providers.base import ChatMessage

MESSAGES = [
    ChatMessage(role="system", content="Be brief."),
    ChatMessage(role="user", content="Hello"),
]


class OpenAIShapeTests(SimpleTestCase):
    @patch("apps.assistant.providers.openai.http.post_json")
    def test_chat_request_and_reply_are_mapped(self, post_json):
        post_json.return_value = {
            "model": "gpt-test",
            "choices": [{"message": {"content": "Hi there"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 11, "completion_tokens": 3},
        }
        provider = OpenAIProvider(api_key="sk-fake", model="gpt-test")
        reply = provider.chat(MESSAGES, max_tokens=50)

        url, payload = post_json.call_args.args
        headers = post_json.call_args.kwargs["headers"]
        self.assertEqual(url, "https://api.openai.com/v1/chat/completions")
        self.assertEqual(headers["Authorization"], "Bearer sk-fake")
        self.assertEqual(payload["model"], "gpt-test")
        self.assertEqual(payload["messages"][1]["content"], "Hello")
        self.assertEqual(payload["max_tokens"], 50)
        self.assertEqual(reply.text, "Hi there")
        self.assertEqual(reply.tokens_in, 11)
        self.assertEqual(reply.tokens_out, 3)

    @patch("apps.assistant.providers.openai.http.post_json")
    def test_a_rejected_key_is_an_auth_error_and_the_key_is_redacted(self, post_json):
        secret = "sk-fake-secret-value-1234567890"
        post_json.side_effect = http.HttpStatusError(
            401, f"Incorrect API key provided: {secret}"
        )
        provider = OpenAIProvider(api_key=secret, model="gpt-test")
        with self.assertRaises(ProviderError) as caught:
            provider.chat(MESSAGES)
        self.assertEqual(caught.exception.kind, ErrorKind.AUTH)
        self.assertNotIn(secret, caught.exception.safe_message)
        self.assertIn("[key]", caught.exception.safe_message)

    @patch("apps.assistant.providers.openai.http.post_json")
    def test_content_filter_is_reported_as_blocked(self, post_json):
        post_json.return_value = {
            "choices": [{"message": {"content": ""}, "finish_reason": "content_filter"}]
        }
        provider = OpenAIProvider(api_key="sk-fake", model="gpt-test")
        with self.assertRaises(ProviderError) as caught:
            provider.chat(MESSAGES)
        self.assertEqual(caught.exception.kind, ErrorKind.CONTENT_BLOCKED)

    @patch("apps.assistant.providers.openai.http.post_json")
    def test_a_404_names_the_model_or_endpoint(self, post_json):
        post_json.side_effect = http.HttpStatusError(404, "")
        provider = OpenAIProvider(api_key="sk-fake", model="wrong-model")
        with self.assertRaises(ProviderError) as caught:
            provider.chat(MESSAGES)
        self.assertEqual(caught.exception.kind, ErrorKind.BAD_REQUEST)
        self.assertIn("model or endpoint", caught.exception.safe_message)

    @patch("apps.assistant.providers.openai.http.post_json")
    def test_a_429_names_the_quota(self, post_json):
        post_json.side_effect = http.HttpStatusError(429, "")
        provider = OpenAIProvider(api_key="sk-fake", model="gpt-test")
        with self.assertRaises(ProviderError) as caught:
            provider.chat(MESSAGES)
        self.assertIn("rate limit or quota", caught.exception.safe_message)

    @patch("apps.assistant.providers.openai.http.post_json")
    def test_network_and_timeout_failures_are_plain(self, post_json):
        cases = [
            (http.TransportNetworkError("dns failure"), "could not reach"),
            (http.TransportTimeout(), "did not answer"),
        ]
        for error, expected in cases:
            with self.subTest(error=type(error).__name__):
                post_json.side_effect = error
                provider = OpenAIProvider(api_key="sk-fake", model="gpt-test")
                with self.assertRaises(ProviderError) as caught:
                    provider.chat(MESSAGES)
                self.assertIn(expected, caught.exception.safe_message)

    @patch("apps.assistant.providers.openai.http.post_json")
    def test_embeddings_are_sorted_and_sized(self, post_json):
        post_json.return_value = {
            "data": [
                {"index": 1, "embedding": [0.4, 0.5]},
                {"index": 0, "embedding": [0.1, 0.2]},
            ],
            "usage": {"prompt_tokens": 7},
            "model": "text-embedding-test",
        }
        provider = OpenAIProvider(api_key="sk-fake", model="text-embedding-test")
        reply = provider.embed(["a", "b"])
        self.assertEqual(reply.vectors, [[0.1, 0.2], [0.4, 0.5]])
        self.assertEqual(reply.dimension, 2)
        self.assertEqual(reply.tokens, 7)


class GeminiShapeTests(SimpleTestCase):
    @patch("apps.assistant.providers.gemini.http.post_json")
    def test_chat_request_and_reply_are_mapped(self, post_json):
        post_json.return_value = {
            "candidates": [
                {"content": {"parts": [{"text": "Habari"}]}, "finishReason": "STOP"}
            ],
            "usageMetadata": {"promptTokenCount": 9, "candidatesTokenCount": 2},
            "modelVersion": "gemini-test",
        }
        provider = GeminiProvider(api_key="AIza-fake", model="gemini-test")
        reply = provider.chat(MESSAGES, max_tokens=40)

        url, payload = post_json.call_args.args
        headers = post_json.call_args.kwargs["headers"]
        self.assertTrue(url.endswith("/models/gemini-test:generateContent"))
        self.assertEqual(headers["x-goog-api-key"], "AIza-fake")
        self.assertEqual(payload["systemInstruction"]["parts"][0]["text"], "Be brief.")
        self.assertEqual(payload["contents"][0]["parts"][0]["text"], "Hello")
        self.assertEqual(payload["generationConfig"]["maxOutputTokens"], 40)
        self.assertEqual(reply.text, "Habari")
        self.assertEqual(reply.tokens_in, 9)

    @patch("apps.assistant.providers.gemini.http.post_json")
    def test_a_safety_block_is_reported(self, post_json):
        post_json.return_value = {"promptFeedback": {"blockReason": "SAFETY"}}
        provider = GeminiProvider(api_key="AIza-fake", model="gemini-test")
        with self.assertRaises(ProviderError) as caught:
            provider.chat(MESSAGES)
        self.assertEqual(caught.exception.kind, ErrorKind.CONTENT_BLOCKED)

    @patch("apps.assistant.providers.gemini.http.post_json")
    def test_a_rejected_gemini_key_is_named(self, post_json):
        post_json.side_effect = http.HttpStatusError(403, "API key not valid")
        provider = GeminiProvider(api_key="AIza-fake", model="gemini-test")
        with self.assertRaises(ProviderError) as caught:
            provider.chat(MESSAGES)
        self.assertEqual(caught.exception.kind, ErrorKind.AUTH)
        self.assertIn("rejected the API key", caught.exception.safe_message)

    @patch("apps.assistant.providers.gemini.http.post_json")
    def test_a_gemini_404_names_the_model_or_endpoint(self, post_json):
        post_json.side_effect = http.HttpStatusError(404, "")
        provider = GeminiProvider(api_key="AIza-fake", model="wrong-model")
        with self.assertRaises(ProviderError) as caught:
            provider.chat(MESSAGES)
        self.assertIn("Gemini could not find that model", caught.exception.safe_message)

    @patch("apps.assistant.providers.gemini.http.post_json")
    def test_embeddings_use_embed_content(self, post_json):
        post_json.return_value = {"embedding": {"values": [0.1, 0.2, 0.3]}}
        provider = GeminiProvider(api_key="AIza-fake", model="gemini-embedding-test")
        reply = provider.embed(["hello"])
        url, payload = post_json.call_args.args
        self.assertTrue(url.endswith(":embedContent"))
        self.assertEqual(payload["content"]["parts"][0]["text"], "hello")
        self.assertEqual(reply.dimension, 3)


class DeepSeekTests(SimpleTestCase):
    def test_embeddings_are_refused_with_a_clear_reason(self):
        provider = DeepSeekProvider(api_key="sk-fake", model="deepseek-flash")
        with self.assertRaises(ProviderError) as caught:
            provider.embed(["hello"])
        self.assertEqual(caught.exception.kind, ErrorKind.UNSUPPORTED)
        self.assertIn("embeddings endpoint", str(caught.exception).lower())

    def test_the_default_base_url_is_deepseek(self):
        provider = DeepSeekProvider(api_key="sk-fake", model="deepseek-flash")
        self.assertEqual(provider.base_url, "https://api.deepseek.com")


class FakeProviderTests(SimpleTestCase):
    def test_error_modes_are_selected_by_model_name(self):
        cases = {
            "fake-timeout": ErrorKind.TIMEOUT,
            "fake-rate-limit": ErrorKind.RATE_LIMIT,
            "fake-auth": ErrorKind.AUTH,
            "fake-malformed": ErrorKind.MALFORMED,
            "fake-blocked": ErrorKind.CONTENT_BLOCKED,
        }
        for model, expected in cases.items():
            with self.subTest(model=model):
                with self.assertRaises(ProviderError) as caught:
                    FakeProvider(model).chat(MESSAGES)
                self.assertEqual(caught.exception.kind, expected)

    def test_embeddings_are_deterministic(self):
        first = FakeProvider("fake").embed(["same text"])
        second = FakeProvider("fake").embed(["same text"])
        self.assertEqual(first.vectors, second.vectors)


class RegistryTests(SimpleTestCase):
    def provider(self, **overrides):
        data = {
            "kind": "fake",
            "is_active": True,
            "model_name": "fake",
            "fast_model_name": "",
            "embedding_model_name": "",
            "base_url": "",
            "api_key_ciphertext": "",
            "name": "Test",
            "timeout_seconds": 30,
        }
        data.update(overrides)
        return SimpleNamespace(**data)

    def test_fake_needs_no_key(self):
        self.assertIsInstance(build(self.provider()), FakeProvider)

    def test_a_real_provider_needs_a_model(self):
        with self.assertRaises(ProviderError) as caught:
            build(self.provider(kind="openai", model_name=""))
        self.assertEqual(caught.exception.kind, ErrorKind.CONFIG)

    def test_a_real_provider_needs_a_key(self):
        with self.assertRaises(ProviderError) as caught:
            build(self.provider(kind="openai", model_name="gpt-test"))
        self.assertEqual(caught.exception.kind, ErrorKind.CONFIG)

    def test_fast_falls_back_to_the_main_model(self):
        provider = self.provider(model_name="main")
        self.assertEqual(model_for(provider, "fast"), "main")

    def test_embeddings_never_fall_back_to_the_chat_model(self):
        provider = self.provider(model_name="main")
        self.assertEqual(model_for(provider, "embed"), "")
