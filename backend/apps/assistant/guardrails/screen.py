"""
apps/assistant/guardrails/screen.py
-----------------------------------
Runs the mandatory safeguarding floor plus any active admin-added rules.

The call site is the service layer, on every message, before any provider is
contacted. There is no setting that turns this off and no screen that edits the
mandatory pack.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .rules import MANDATORY_RULES, Rule


@dataclass(frozen=True)
class Hit:
    rule_id: str
    category: str
    label: str
    severity: str
    matched: str


@dataclass
class ScreeningResult:
    hits: list[Hit] = field(default_factory=list)
    text: str = ""

    @property
    def triggered(self) -> bool:
        return bool(self.hits)

    @property
    def categories(self) -> list[str]:
        seen: list[str] = []
        for hit in self.hits:
            if hit.category not in seen:
                seen.append(hit.category)
        return seen

    @property
    def rule_ids(self) -> list[str]:
        return [hit.rule_id for hit in self.hits]

    @property
    def urgent(self) -> bool:
        return any(hit.severity == "urgent" for hit in self.hits)


def normalise(text: str) -> str:
    text = (text or "").replace("\u2019", "'").replace("\u2018", "'")
    text = text.replace("\u201c", '"').replace("\u201d", '"')
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _compile(patterns) -> list[re.Pattern]:
    return [re.compile(rf"\b{re.escape(p)}\b", re.IGNORECASE) for p in patterns]


_MANDATORY_COMPILED = [
    (rule, _compile(rule.patterns)) for rule in MANDATORY_RULES
]


def _match_rule(rule: Rule, compiled, text: str) -> Hit | None:
    for pattern in compiled:
        match = pattern.search(text)
        if match:
            return Hit(
                rule_id=rule.id,
                category=rule.category,
                label=rule.label,
                severity=rule.severity,
                matched=match.group(0),
            )
    return None


def screen(text: str, custom_rules=None) -> ScreeningResult:
    """
    Return every hit. ``custom_rules`` is an iterable of objects with
    ``name``, ``category``, ``pattern_list()`` and ``pk`` (CustomGuardrailRule).
    """
    normalised = normalise(text)
    hits: list[Hit] = []

    for rule, compiled in _MANDATORY_COMPILED:
        hit = _match_rule(rule, compiled, normalised)
        if hit:
            hits.append(hit)

    for rule in custom_rules or ():
        compiled = _compile(rule.pattern_list())
        hit = _match_rule(
            Rule(
                id=f"CUSTOM-{rule.pk}",
                category=rule.category,
                label=rule.name,
                severity="concern",
                patterns=tuple(rule.pattern_list()),
            ),
            compiled,
            normalised,
        )
        if hit:
            hits.append(hit)

    # Rules that only matter alongside another hit are kept only if one exists.
    categories_present = {hit.category for hit in hits if hit.severity == "urgent"}
    filtered = []
    for hit in hits:
        rule = next((r for r in MANDATORY_RULES if r.id == hit.rule_id), None)
        if rule is not None and rule.requires_another_hit:
            if not categories_present:
                continue
        filtered.append(hit)

    return ScreeningResult(hits=filtered, text=normalised)
