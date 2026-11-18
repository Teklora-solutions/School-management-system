"""
apps/assistant/guardrails/rules.py
----------------------------------
The mandatory safeguarding rule pack.

These rules live in code, not in the database, on purpose:
  * they cannot be disabled, edited or deleted from any screen;
  * a database restore cannot remove them;
  * the test suite can prove they are always loaded.

Admins may ADD rules (``CustomGuardrailRule``); they can never remove these.

Phrases are matched case-insensitively on word boundaries after the text is
normalised (curly apostrophes straightened, whitespace collapsed). English,
Kiswahili and common Sheng are included. This is a floor, not a classifier:
indirect phrasing is the job of the optional second layer described in
``docs/ai-assistant/architecture.md``, which is not built.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Rule:
    id: str
    category: str
    label: str
    severity: str  # "urgent" or "concern"
    patterns: tuple[str, ...]
    requires_another_hit: bool = False


# Category keys match CustomGuardrailRule.Category so admin-added rules and
# mandatory rules are reported the same way.
MANDATORY_RULES: tuple[Rule, ...] = (
    Rule(
        id="M-SELF-HARM-1",
        category="self_harm",
        label="Self-harm or suicide (English)",
        severity="urgent",
        patterns=(
            "kill myself",
            "killing myself",
            "end my life",
            "take my own life",
            "want to die",
            "wanna die",
            "cut myself",
            "hurting myself",
            "hurt myself",
            "suicide",
        ),
    ),
    Rule(
        id="M-SELF-HARM-2",
        category="self_harm",
        label="Self-harm or suicide (Kiswahili/Sheng)",
        severity="urgent",
        patterns=(
            "kujiua",
            "kujidhuru",
            "nataka kufa",
            "kujimaliza",
            "nitajimaliza",
            "nimechoka na maisha",
            "sitaki kuishi",
        ),
    ),
    Rule(
        id="M-ABUSE-1",
        category="abuse",
        label="Physical or sexual abuse (English)",
        severity="urgent",
        patterns=(
            "beats me",
            "hits me",
            "touches me",
            "touching me",
            "private parts",
            "raped",
            "rape",
            "molested",
            "molest",
            "sexual abuse",
            "touches my sister",
        ),
    ),
    Rule(
        id="M-ABUSE-2",
        category="abuse",
        label="Physical or sexual abuse (Kiswahili/Sheng)",
        severity="urgent",
        patterns=(
            "ananipiga",
            "ananishika vibaya",
            "ubakaji",
            "kubakwa",
            "amenibaka",
            "ananibaka",
        ),
    ),
    Rule(
        id="M-NEGLECT-1",
        category="neglect",
        label="Neglect or no safe home",
        severity="urgent",
        patterns=(
            "no food at home",
            "nothing to eat at home",
            "sleeping outside",
            "left alone at home",
            "hakuna chakula",
            "nimeachwa peke yangu",
            "sina mahali pa kulala",
        ),
    ),
    Rule(
        id="M-THREAT-1",
        category="threat",
        label="Threat or weapon (English)",
        severity="urgent",
        patterns=(
            "i will kill",
            "i will hurt",
            "kill him",
            "kill her",
            "kill them",
            "bring a gun",
            "bring a knife",
            "shoot them",
            "bomb",
        ),
    ),
    Rule(
        id="M-THREAT-2",
        category="threat",
        label="Threat or weapon (Kiswahili/Sheng)",
        severity="urgent",
        patterns=(
            "nitamuua",
            "nitamdhuru",
            "nitaleta kisu",
            "nitaleta bunduki",
            "bunduki",
        ),
    ),
    Rule(
        id="M-MEDICAL-1",
        category="medical",
        label="Medical emergency (English)",
        severity="urgent",
        patterns=(
            "can't breathe",
            "cannot breathe",
            "bleeding badly",
            "overdose",
            "poisoned",
            "unconscious",
            "having a seizure",
        ),
    ),
    Rule(
        id="M-MEDICAL-2",
        category="medical",
        label="Medical emergency (Kiswahili/Sheng)",
        severity="urgent",
        patterns=(
            "ajali",
            "moto",
            "sumu",
            "amenyeshwa sumu",
            "anavuja damu",
            "anashindwa kupumua",
        ),
    ),
    Rule(
        id="M-COERCION-1",
        category="abuse",
        label="Secrecy/coercion combined with another concern",
        severity="concern",
        patterns=(
            "don't tell anyone",
            "dont tell anyone",
            "keep this secret",
            "usimwambie mtu",
            "ananiambia nisimwambie mtu",
        ),
        requires_another_hit=True,
    ),
)

URGENT_CATEGORIES = frozenset(
    rule.category for rule in MANDATORY_RULES if rule.severity == "urgent"
)


def mandatory_rule_ids() -> tuple[str, ...]:
    return tuple(rule.id for rule in MANDATORY_RULES)
