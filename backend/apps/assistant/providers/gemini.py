"""
apps/assistant/providers/gemini.py
----------------------------------
Google Gemini's documented REST endpoints (v1beta):
  POST {base}/models/{model}:generateContent
  POST {base}/models/{model}:embedContent
Authentication: ``x-goog-api-key`` header (not Bearer).

The request/response shape is Google's own (contents/parts, usageMetadata),
which is why it needs a separate adapter rather than the OpenAI-compatible one.
"""

from __future__ import annotations

from collections.abc import Sequence

from . import http
from .base import (
    ChatMessage,
    EmbeddingReply,
    ErrorKind,
    ProviderError,
    ProviderReply,
    redact,
)

DEFAULT_BASE_URL = "https://generativelanguage.googleapis.com/v1beta"


class GeminiProvider:
    def __init__(
        self, *, api_key: str, model: str, base_url: str = "", timeout: int = 30
    ) -> None:
        self.api_key = api_key
        self.model = model
        self.base_url = (base_url or DEFAULT_BASE_URL).rstrip("/")
        self.timeout = timeout

    def chat(
        self,
        messages: Sequence[ChatMessage],
        *,
        model: str | None = None,
        timeout: int | None = None,
        max_tokens: int = 400,
    ) -> ProviderReply:
        model_name = model or self.model
        contents = []
        system_parts = []
        for message in messages:
            if message.role == "system":
                system_parts.append(message.content)
                continue
            contents.append(
                {
                    "role": "model" if message.role == "assistant" else "user",
                    "parts": [{"text": message.content}],
                }
            )
        payload: dict = {
            "contents": contents,
            "generationConfig": {"maxOutputTokens": max_tokens, "temperature": 0.2},
        }
        if system_parts:
            payload["systemInstruction"] = {"parts": [{"text": "\n".join(system_parts)}]}

        data = self._post(f"/models/{model_name}:generateContent", payload, timeout)
        if (data.get("promptFeedback") or {}).get("blockReason"):
            raise ProviderError(ErrorKind.CONTENT_BLOCKED)
        try:
            candidate = data["candidates"][0]
            if candidate.get("finishReason") == "SAFETY":
                raise ProviderError(ErrorKind.CONTENT_BLOCKED)
            parts = candidate["content"]["parts"]
            text = "".join(str(part.get("text") or "") for part in parts)
            usage = data.get("usageMetadata") or {}
            model_returned = data.get("modelVersion") or model_name
        except (KeyError, IndexError, TypeError) as exc:
            raise ProviderError(ErrorKind.MALFORMED) from exc
        if not text.strip():
            raise ProviderError(ErrorKind.CONTENT_BLOCKED)
        return ProviderReply(
            text=text,
            tokens_in=int(usage.get("promptTokenCount") or 0),
            tokens_out=int(usage.get("candidatesTokenCount") or 0),
            model=str(model_returned),
        )

    def embed(
        self,
        texts: Sequence[str],
        *,
        model: str | None = None,
        timeout: int | None = None,
    ) -> EmbeddingReply:
        model_name = model or self.model
        vectors: list[list[float]] = []
        for text in texts:
            data = self._post(
                f"/models/{model_name}:embedContent",
                {"content": {"parts": [{"text": text}]}},
                timeout,
            )
            try:
                vectors.append(list(map(float, data["embedding"]["values"])))
            except (KeyError, TypeError, ValueError) as exc:
                raise ProviderError(ErrorKind.MALFORMED) from exc
        if not vectors:
            raise ProviderError(ErrorKind.MALFORMED)
        return EmbeddingReply(
            vectors=vectors,
            tokens=0,
            model=model_name,
            dimension=len(vectors[0]),
        )

    def _post(self, path: str, payload: dict, timeout: int | None) -> dict:
        try:
            return http.post_json(
                f"{self.base_url}{path}",
                payload,
                headers={"x-goog-api-key": self.api_key},
                timeout=timeout or self.timeout,
            )
        except http.HttpStatusError as exc:
            raise _map_status(exc, self.api_key) from exc
        except http.TransportTimeout as exc:
            raise ProviderError(ErrorKind.TIMEOUT) from exc
        except http.TransportNetworkError as exc:
            raise ProviderError(ErrorKind.NETWORK) from exc
        except ValueError as exc:
            raise ProviderError(ErrorKind.MALFORMED) from exc


def _map_status(exc: http.HttpStatusError, secret: str = "") -> ProviderError:
    status = exc.status
    message = redact(exc.message, secret)
    lowered = message.lower()
    if status in (401, 403) or "api key" in lowered or "api_key_invalid" in lowered:
        return ProviderError(ErrorKind.AUTH, message or None, status=status)
    if status == 429:
        return ProviderError(ErrorKind.RATE_LIMIT, message or None, status=status)
    if status == 404:
        return ProviderError(
            ErrorKind.BAD_REQUEST,
            message
            or "Gemini could not find that model (check the model name) or the endpoint "
            "(check the API version and server address).",
            status=status,
        )
    if status >= 500:
        return ProviderError(ErrorKind.SERVER, message or None, status=status)
    return ProviderError(
        ErrorKind.BAD_REQUEST,
        message
        or "Gemini rejected the request. This is usually a wrong model name or a "
        "setting it does not accept.",
        status=status,
    )
