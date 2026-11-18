"""
apps/assistant/providers/openai.py
----------------------------------
OpenAI's documented endpoints:
  POST {base}/chat/completions
  POST {base}/embeddings
Authentication: ``Authorization: Bearer``.

DeepSeek and generic OpenAI-compatible servers reuse this adapter because they
document the same shape; only their base URL and capabilities differ.
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

DEFAULT_BASE_URL = "https://api.openai.com/v1"


class OpenAIProvider:
    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        base_url: str = "",
        timeout: int = 30,
    ) -> None:
        self.api_key = api_key
        self.model = model
        self.base_url = (base_url or DEFAULT_BASE_URL).rstrip("/")
        self.timeout = timeout

    # ── chat ─────────────────────────────────────────────────────────────────
    def chat(
        self,
        messages: Sequence[ChatMessage],
        *,
        model: str | None = None,
        timeout: int | None = None,
        max_tokens: int = 400,
    ) -> ProviderReply:
        payload = {
            "model": model or self.model,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "max_tokens": max_tokens,
            "temperature": 0.2,
        }
        data = self._post("/chat/completions", payload, timeout)
        try:
            choice = data["choices"][0]
            text = choice["message"]["content"] or ""
            finish = choice.get("finish_reason")
            usage = data.get("usage") or {}
        except (KeyError, IndexError, TypeError) as exc:
            raise ProviderError(ErrorKind.MALFORMED) from exc
        if finish == "content_filter" or not str(text).strip():
            raise ProviderError(ErrorKind.CONTENT_BLOCKED)
        return ProviderReply(
            text=str(text),
            tokens_in=int(usage.get("prompt_tokens") or 0),
            tokens_out=int(usage.get("completion_tokens") or 0),
            model=str(data.get("model") or model or self.model),
        )

    # ── embeddings ───────────────────────────────────────────────────────────
    def embed(
        self,
        texts: Sequence[str],
        *,
        model: str | None = None,
        timeout: int | None = None,
    ) -> EmbeddingReply:
        payload = {"model": model or self.model, "input": list(texts)}
        data = self._post("/embeddings", payload, timeout)
        try:
            rows = sorted(data["data"], key=lambda row: row.get("index", 0))
            vectors = [list(map(float, row["embedding"])) for row in rows]
            usage = data.get("usage") or {}
        except (KeyError, TypeError, ValueError) as exc:
            raise ProviderError(ErrorKind.MALFORMED) from exc
        if not vectors:
            raise ProviderError(ErrorKind.MALFORMED)
        return EmbeddingReply(
            vectors=vectors,
            tokens=int(usage.get("prompt_tokens") or usage.get("total_tokens") or 0),
            model=str(data.get("model") or model or self.model),
            dimension=len(vectors[0]),
        )

    # ── internals ────────────────────────────────────────────────────────────
    def _post(self, path: str, payload: dict, timeout: int | None) -> dict:
        try:
            return http.post_json(
                f"{self.base_url}{path}",
                payload,
                headers={"Authorization": f"Bearer {self.api_key}"},
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
    if status in (401, 403):
        return ProviderError(ErrorKind.AUTH, message or None, status=status)
    if status == 429:
        return ProviderError(ErrorKind.RATE_LIMIT, message or None, status=status)
    if status == 404:
        return ProviderError(
            ErrorKind.BAD_REQUEST,
            message
            or "The provider could not find that model or endpoint (HTTP 404). Check the "
            "model name and the server address.",
            status=status,
        )
    if status in (400, 405, 409, 422):
        return ProviderError(
            ErrorKind.BAD_REQUEST,
            message
            or "The provider rejected the request (HTTP "
            f"{status}). This is usually a wrong model name or a setting it does not accept.",
            status=status,
        )
    if status >= 500:
        return ProviderError(ErrorKind.SERVER, message or None, status=status)
    return ProviderError(ErrorKind.BAD_REQUEST, message or None, status=status)
