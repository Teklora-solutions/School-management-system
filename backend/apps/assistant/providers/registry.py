"""
apps/assistant/providers/registry.py
------------------------------------
Turns a saved ``ProviderConfig`` into a live adapter.

The API key is decrypted here and exists only inside the adapter instance for
the duration of the call. Nothing in this module logs.
"""

from __future__ import annotations

from .. import crypto
from .base import ErrorKind, ProviderError
from .deepseek import DEFAULT_BASE_URL as DEEPSEEK_BASE_URL
from .deepseek import DeepSeekProvider
from .fake import FakeProvider
from .gemini import DEFAULT_BASE_URL as GEMINI_BASE_URL
from .gemini import GeminiProvider
from .openai import DEFAULT_BASE_URL as OPENAI_BASE_URL
from .openai import OpenAIProvider
from .openai_compatible import OpenAICompatibleProvider

DEFAULT_BASE_URLS = {
    "openai": OPENAI_BASE_URL,
    "deepseek": DEEPSEEK_BASE_URL,
    "gemini": GEMINI_BASE_URL,
}


def model_for(provider, purpose: str = "chat") -> str:
    """chat | fast | embed — with sensible fallbacks for the fast slot."""
    if purpose == "embed":
        return (provider.embedding_model_name or "").strip()
    if purpose == "fast":
        return (provider.fast_model_name or provider.model_name or "").strip()
    return (provider.model_name or "").strip()


def build(provider, *, purpose: str = "chat"):
    """Build the adapter for ``provider``. Raises ProviderError(CONFIG)."""
    kind = provider.kind
    if kind == "fake":
        return FakeProvider(provider.model_name or "fake")

    if not provider.is_active:
        raise ProviderError(ErrorKind.CONFIG, "This provider is switched off.")

    model = model_for(provider, purpose)
    if not model:
        label = "embedding model" if purpose == "embed" else "model"
        raise ProviderError(
            ErrorKind.CONFIG, f"No {label} is set for “{provider.name}”."
        )
    if not provider.api_key_ciphertext:
        raise ProviderError(
            ErrorKind.CONFIG, f"No API key is saved for “{provider.name}”."
        )
    try:
        api_key = crypto.decrypt(provider.api_key_ciphertext)
    except crypto.EncryptionUnavailable as exc:
        raise ProviderError(
            ErrorKind.CONFIG,
            "The server has no encryption key configured, so the saved provider key "
            "cannot be read. Set ASSISTANT_ENCRYPTION_KEY and restart.",
        ) from exc
    except crypto.DecryptionFailed as exc:
        raise ProviderError(ErrorKind.CONFIG, str(exc)) from exc

    base_url = (provider.base_url or "").strip()
    timeout = provider.timeout_seconds or 30

    if kind == "openai":
        return OpenAIProvider(api_key=api_key, model=model, base_url=base_url, timeout=timeout)
    if kind == "deepseek":
        return DeepSeekProvider(api_key=api_key, model=model, base_url=base_url, timeout=timeout)
    if kind == "gemini":
        return GeminiProvider(api_key=api_key, model=model, base_url=base_url, timeout=timeout)
    if kind == "openai_compatible":
        if not base_url:
            raise ProviderError(
                ErrorKind.CONFIG,
                "An OpenAI-compatible provider needs its server address (base URL).",
            )
        return OpenAICompatibleProvider(
            api_key=api_key, model=model, base_url=base_url, timeout=timeout
        )

    raise ProviderError(ErrorKind.CONFIG, f"Unknown provider kind “{kind}”.")
