"""
apps/assistant/providers/base.py
--------------------------------
The contract every provider adapter satisfies. Nothing here imports Django,
so adapters can be moved behind a separate service later without a rewrite.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class ChatMessage:
    role: str  # system | user | assistant
    content: str


@dataclass(frozen=True)
class ProviderReply:
    text: str
    tokens_in: int
    tokens_out: int
    model: str


@dataclass(frozen=True)
class EmbeddingReply:
    vectors: list[list[float]]
    tokens: int
    model: str
    dimension: int


class ErrorKind:
    AUTH = "auth"
    RATE_LIMIT = "rate_limit"
    TIMEOUT = "timeout"
    NETWORK = "network"
    BAD_REQUEST = "bad_request"
    SERVER = "server"
    CONTENT_BLOCKED = "content_blocked"
    MALFORMED = "malformed"
    CONFIG = "config"
    UNSUPPORTED = "unsupported"


RETRYABLE_KINDS = frozenset(
    {ErrorKind.RATE_LIMIT, ErrorKind.TIMEOUT, ErrorKind.NETWORK, ErrorKind.SERVER}
)

PLAIN_MESSAGES = {
    ErrorKind.AUTH: (
        "The provider rejected the API key (authentication failed). Check that the key "
        "is correct, active and belongs to this provider."
    ),
    ErrorKind.RATE_LIMIT: (
        "The provider refused the request because of a rate limit or quota. Check the "
        "provider's spending limit and plan, then try again."
    ),
    ErrorKind.TIMEOUT: "The provider did not answer before the timeout.",
    ErrorKind.NETWORK: (
        "This server could not reach the provider (network or address problem). Check "
        "the server address and the internet connection."
    ),
    ErrorKind.BAD_REQUEST: (
        "The provider rejected the request. Check the model name and the provider settings."
    ),
    ErrorKind.SERVER: "The provider reported a server error. Try again later.",
    ErrorKind.CONTENT_BLOCKED: "The provider's safety filter blocked this request.",
    ErrorKind.MALFORMED: "The provider sent a reply this system could not read.",
    ErrorKind.CONFIG: "This provider is not configured correctly.",
    ErrorKind.UNSUPPORTED: "This provider does not support that capability.",
}


class ProviderError(RuntimeError):
    """A normalised failure. ``safe_message`` is safe to show and to store."""

    def __init__(self, kind: str, detail: str = "", *, status: int | None = None):
        self.kind = kind
        self.status = status
        self.detail = (detail or "")[:200]
        plain = PLAIN_MESSAGES.get(kind, "The provider failed.")
        if kind in (ErrorKind.CONFIG, ErrorKind.UNSUPPORTED):
            # These details are our own plain explanation, not a provider's text.
            self.safe_message = self.detail or plain
        else:
            self.safe_message = (
                f"{plain} Provider said: {self.detail}" if self.detail else plain
            )
        self.safe_message = self.safe_message[:200]
        super().__init__(self.safe_message)

    @property
    def retryable(self) -> bool:
        return self.kind in RETRYABLE_KINDS


def redact(text: str, secret: str) -> str:
    """Remove a secret from text a provider sent back, before it is stored."""
    text = text or ""
    if secret:
        text = text.replace(secret, "[key]")
    return text[:190]


class Provider(Protocol):
    model: str

    def chat(
        self,
        messages: Sequence[ChatMessage],
        *,
        model: str | None = None,
        timeout: int | None = None,
        max_tokens: int = 400,
    ) -> ProviderReply: ...

    def embed(
        self, texts: Sequence[str], *, model: str | None = None, timeout: int | None = None
    ) -> EmbeddingReply: ...
