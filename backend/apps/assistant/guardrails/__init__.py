"""Guardrails: mandatory safeguarding, PII minimisation, injection defence."""

from .injection import build_messages, clean_user_text, fence_sources
from .pii import MaskedText, mask_excerpt, mask_text
from .rules import MANDATORY_RULES, mandatory_rule_ids
from .safe_reply import build as build_safe_reply
from .screen import ScreeningResult, screen

__all__ = [
    "MANDATORY_RULES",
    "MaskedText",
    "ScreeningResult",
    "build_messages",
    "build_safe_reply",
    "clean_user_text",
    "fence_sources",
    "mandatory_rule_ids",
    "mask_excerpt",
    "mask_text",
    "screen",
]
