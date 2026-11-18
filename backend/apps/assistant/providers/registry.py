"""apps/assistant/providers/registry.py — kind string → adapter instance."""

from __future__ import annotations

from .base import Provider, ProviderUnavailable
from .fake import FakeProvider


def build_provider(*, kind: str, model_name: str = "") -> Provider:
    if kind == "fake":
        return FakeProvider(model_name=model_name)
    raise ProviderUnavailable(
        "This kind of provider is not available in this build. Only the fake "
        "provider can be used until the knowledge module is built."
    )
