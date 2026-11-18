"""Provider adapters and the registry. Framework-free except the registry."""

from .base import (
    ChatMessage,
    EmbeddingReply,
    ErrorKind,
    Provider,
    ProviderError,
    ProviderReply,
)
from .registry import build, model_for

__all__ = [
    "ChatMessage",
    "EmbeddingReply",
    "ErrorKind",
    "Provider",
    "ProviderError",
    "ProviderReply",
    "build",
    "model_for",
]
