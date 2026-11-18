"""
apps/assistant/providers/deepseek.py
------------------------------------
DeepSeek documents an OpenAI-compatible API:
  base URL https://api.deepseek.com
  POST chat/completions, Authorization: Bearer
  models verified 2026-11-18: deepseek-flash, deepseek-v4-pro
  no embeddings endpoint is documented, so embedding requests are refused.
"""

from __future__ import annotations

from .base import EmbeddingReply, ErrorKind, ProviderError
from .openai import OpenAIProvider

DEFAULT_BASE_URL = "https://api.deepseek.com"


class DeepSeekProvider(OpenAIProvider):
    def __init__(
        self, *, api_key: str, model: str, base_url: str = "", timeout: int = 30
    ) -> None:
        super().__init__(
            api_key=api_key,
            model=model,
            base_url=base_url or DEFAULT_BASE_URL,
            timeout=timeout,
        )

    def embed(self, texts, *, model: str | None = None, timeout: int | None = None) -> EmbeddingReply:
        raise ProviderError(
            ErrorKind.UNSUPPORTED,
            "DeepSeek does not publish an embeddings endpoint. Choose another provider "
            "as the embedding provider.",
        )
