"""
A cheap proof of work for the public chat widget.

A bot that wants to post a question must first find a number whose SHA-256,
combined with the challenge, starts with a few zero hex digits. On a phone
that is a fraction of a second; a script posting continuously has to pay it
every time. It is not a strong defence and is not meant to be one: the daily
site cap and the budget are the real cost controls.

Each challenge is bound to one visitor and is deleted the moment it is used,
so a solved answer cannot be replayed or shared.
"""

from __future__ import annotations

import hashlib
import secrets

from django.core.cache import cache

DIFFICULTY = 4
CHALLENGE_TTL_SECONDS = 10 * 60
MAX_NONCE_CHARS = 32


def _key(visitor_digest: str, challenge: str) -> str:
    return f"assistant:pow:{visitor_digest}:{challenge}"


def issue(visitor_digest: str) -> dict:
    challenge = secrets.token_hex(16)
    cache.set(_key(visitor_digest, challenge), 1, CHALLENGE_TTL_SECONDS)
    return {"challenge": challenge, "difficulty": DIFFICULTY}


def digest_for(challenge: str, nonce: str) -> str:
    return hashlib.sha256(f"{challenge}:{nonce}".encode("utf-8")).hexdigest()


def verify(visitor_digest: str, challenge: str, nonce: str) -> bool:
    challenge = (challenge or "").strip()
    nonce = str(nonce or "").strip()
    if not challenge or not nonce or len(nonce) > MAX_NONCE_CHARS:
        return False
    key = _key(visitor_digest, challenge)
    if not cache.get(key):
        return False
    if not digest_for(challenge, nonce).startswith("0" * DIFFICULTY):
        return False
    cache.delete(key)
    return True
