"""
apps/assistant/providers/base.py
--------------------------------
The shape every provider adapter must fit.

Nothing here imports Django. A provider receives messages and returns text plus
token counts; it never sees a database row or a Django request.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class ChatMessage:
    """One turn of a conversation: ``system``, ``user`` or ``assistant``."""

    role: str
    content: str


@dataclass(frozen=True)
class ProviderReply:
    text: str
    tokens_in: int
    tokens_out: int
    model: str


class ProviderUnavailable(RuntimeError):
    """The configured adapter cannot run in this build."""


class Provider(Protocol):
    def generate(
        self,
        messages: Sequence[ChatMessage],
        *,
        max_output_tokens: int = 400,
    ) -> ProviderReply: ...
