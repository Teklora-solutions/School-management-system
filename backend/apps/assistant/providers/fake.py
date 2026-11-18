"""
apps/assistant/providers/fake.py
--------------------------------
The provider every test and local development uses. No network, no key.

Error modes are selected by the model name, so the gateway's timeout, rate
limit, malformed-reply and fallback paths can be tested deterministically:

    fake                 normal reply
    fake-timeout         raises a retryable timeout
    fake-rate-limit      raises a retryable rate-limit error
    fake-server          raises a retryable server error
    fake-auth            raises a non-retryable auth error
    fake-malformed       raises a malformed-reply error
    fake-blocked         raises a content-blocked error
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence

from .base import ChatMessage, EmbeddingReply, ErrorKind, ProviderError, ProviderReply

REPLY_PREFIX = "Fake assistant reply. No outside provider was contacted."
EMBEDDING_DIMENSION = 8


class FakeProvider:
    def __init__(self, model_name: str = "") -> None:
        self.model = model_name or "fake"
        self.mode = self.model[len("fake-") :] if self.model.startswith("fake-") else ""

    def _raise_for_mode(self) -> None:
        mapping = {
            "timeout": (ErrorKind.TIMEOUT, "Fake timeout."),
            "rate-limit": (ErrorKind.RATE_LIMIT, "Fake rate limit."),
            "server": (ErrorKind.SERVER, "Fake server error."),
            "auth": (ErrorKind.AUTH, "Fake invalid key."),
            "malformed": (ErrorKind.MALFORMED, "Fake malformed reply."),
            "blocked": (ErrorKind.CONTENT_BLOCKED, "Fake safety block."),
        }
        if self.mode in mapping:
            kind, detail = mapping[self.mode]
            raise ProviderError(kind, detail)

    def chat(
        self,
        messages: Sequence[ChatMessage],
        *,
        model: str | None = None,
        timeout: int | None = None,
        max_tokens: int = 400,
    ) -> ProviderReply:
        self._raise_for_mode()
        last_user = ""
        for message in reversed(messages):
            if message.role == "user":
                last_user = message.content.strip()
                break
        excerpt = last_user[:120]
        text = f"{REPLY_PREFIX} You said: {excerpt}" if excerpt else REPLY_PREFIX
        tokens_in = sum(len(message.content.split()) for message in messages)
        return ProviderReply(
            text=text,
            tokens_in=tokens_in,
            tokens_out=len(text.split()),
            model=model or self.model,
        )

    def embed(
        self, texts: Sequence[str], *, model: str | None = None, timeout: int | None = None
    ) -> EmbeddingReply:
        self._raise_for_mode()
        vectors = []
        for text in texts:
            digest = hashlib.sha256(text.encode("utf-8")).digest()
            vectors.append(
                [round((digest[i] / 255.0) * 2 - 1, 6) for i in range(EMBEDDING_DIMENSION)]
            )
        return EmbeddingReply(
            vectors=vectors,
            tokens=sum(len(text.split()) for text in texts),
            model=model or self.model,
            dimension=EMBEDDING_DIMENSION,
        )
