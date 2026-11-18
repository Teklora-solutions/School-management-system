"""
apps/assistant/providers/fake.py
--------------------------------
The only provider in this build, and the one every test uses.

It never touches the network. Its reply is deterministic, so screens and tests
can prove the gateway and its bookkeeping work without a key or a bill.
"""

from __future__ import annotations

from collections.abc import Sequence

from .base import ChatMessage, ProviderReply

REPLY_PREFIX = "Fake assistant reply. No outside provider was contacted."


class FakeProvider:
    def __init__(self, model_name: str = "") -> None:
        self.model_name = model_name or "fake-1"

    def generate(
        self,
        messages: Sequence[ChatMessage],
        *,
        max_output_tokens: int = 400,
    ) -> ProviderReply:
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
            model=self.model_name,
        )
