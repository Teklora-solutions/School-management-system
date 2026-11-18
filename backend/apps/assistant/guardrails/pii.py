"""
apps/assistant/guardrails/pii.py
--------------------------------
Minimisation before text leaves the server.

Detects and masks:
  * email addresses
  * Kenyan phone numbers (07…, 01…, +254…)
  * labelled ID / admission numbers
  * likely person names: after titles (Mr/Mrs/Ms/Dr/Prof), after "my name is",
    "call me", "jina langu ni", "ninaitwa", and two capitalised words after
    "I am"/"I'm".

Honest limitation: this is a heuristic, not a named-entity model. It reduces
what is sent; it cannot guarantee perfection. Names with no context, unusual
spellings, mixed-language sentences and Sheng nicknames may get through. The
consent wording says exactly that. No external call is made to detect names.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

EMAIL_RE = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b", re.IGNORECASE)
PHONE_RE = re.compile(r"(?:\+?254[\s-]?|0)(?:7\d{2}|1\d{2})[\s-]?\d{3}[\s-]?\d{3}\b")
LABELLED_ID_RE = re.compile(
    r"\b(?:admission(?:\s*(?:no|number))?|adm\s*(?:no|number)?|id\s*(?:no|number)?)\b"
    r"[\s:#-]*(?:is\s+)?"
    # The value must contain a digit so the label's own words are never caught.
    r"(?=[A-Z0-9/-]*\d)([A-Z0-9][A-Z0-9/-]{2,23})\b",
    re.IGNORECASE,
)
ADMISSION_PATH_RE = re.compile(r"\b\d{3,6}/\d{4}/\d{1,5}\b")

# Only the trigger phrase ignores case; the name itself must still be
# capitalised, so ordinary lowercase words ("call me on ...") stay intact.
TITLE_RE = re.compile(
    r"(?i:\b(?:mr|mrs|ms|miss|dr|prof)\.?)\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+){0,2})"
)
NAME_PHRASE_RE = re.compile(
    r"(?i:\b(?:my\s+name\s+is|call\s+me|jina\s+langu\s+ni|ninaitwa))\s+"
    r"([A-Z][a-z]+(?:\s+[A-Z][a-z]+){0,2})"
)
IAM_RE = re.compile(
    r"(?i:\b(?:i\s+am|i'm))\s+([A-Z][a-z]+\s+[A-Z][a-z]+)\b"
)
RELATION_NAME_RE = re.compile(
    r"(?i:\b(?:my\s+(?:son|daughter|child|boy|girl)|mtoto\s+wangu))\s+"
    r"(?:is\s+(?:called\s+)?)?([A-Z][a-z]+(?:\s+[A-Z][a-z]+){0,2})"
)

MASK_TOKENS = {
    "email": "[EMAIL]",
    "phone": "[PHONE]",
    "id": "[ID]",
    "name": "[NAME]",
}


@dataclass
class MaskedText:
    text: str
    counts: dict[str, int] = field(default_factory=dict)

    @property
    def total(self) -> int:
        return sum(self.counts.values())

    @property
    def was_masked(self) -> bool:
        return bool(self.total)


def mask_text(raw: str) -> MaskedText:
    if not raw:
        return MaskedText("")
    counts = {"email": 0, "phone": 0, "id": 0, "name": 0}
    text = raw

    def _sub(pattern, token, key):
        nonlocal text

        def repl(match):
            counts[key] += 1
            return token

        text = pattern.sub(repl, text)

    _sub(EMAIL_RE, MASK_TOKENS["email"], "email")
    _sub(PHONE_RE, MASK_TOKENS["phone"], "phone")

    def _mask_labelled(match):
        counts["id"] += 1
        return match.group(0).replace(match.group(1), MASK_TOKENS["id"])

    text = LABELLED_ID_RE.sub(_mask_labelled, text)
    _sub(ADMISSION_PATH_RE, MASK_TOKENS["id"], "id")

    def _mask_name_phrase(match):
        counts["name"] += 1
        return match.group(0)[: match.start(1) - match.start(0)] + MASK_TOKENS["name"]

    text = TITLE_RE.sub(_mask_name_phrase, text)
    text = NAME_PHRASE_RE.sub(_mask_name_phrase, text)
    text = IAM_RE.sub(_mask_name_phrase, text)
    text = RELATION_NAME_RE.sub(_mask_name_phrase, text)

    return MaskedText(text=text, counts={k: v for k, v in counts.items() if v})


def mask_excerpt(text: str, limit: int = 300) -> str:
    """A masked, length-capped excerpt for an alert row."""
    return mask_text(text).text[:limit]
