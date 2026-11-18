"""
apps/assistant/providers/openai_compatible.py
---------------------------------------------
A generic adapter for servers that implement the OpenAI chat/embeddings shape
(Ollama, vLLM, LM Studio, Together, Groq, local gateways, …). The admin must
enter the base URL, including any version prefix (for example
``https://host.example/v1``).
"""

from __future__ import annotations

from .openai import OpenAIProvider


class OpenAICompatibleProvider(OpenAIProvider):
    def __init__(
        self, *, api_key: str, model: str, base_url: str, timeout: int = 30
    ) -> None:
        super().__init__(
            api_key=api_key, model=model, base_url=base_url, timeout=timeout
        )
