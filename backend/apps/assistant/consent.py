"""
apps/assistant/consent.py
-------------------------
Consent wording for the assistant.

STATUS: DRAFT. Nothing here has been approved. No user-facing screen may show
this text until an admin records approval (flag + name + date) in
``AssistantSettings``. ``visible_consent()`` enforces that gate; tests assert it
returns nothing before approval.
"""

from __future__ import annotations

DRAFT_STATUS = "DRAFT — awaiting review by the admin and director. Do not display to users."

DRAFT_CONSENT_EN = (
    "This assistant answers from the school's approved information. Please don't share "
    "personal details (yours or your child's) in the chat. We automatically mask likely "
    "names, phone numbers, emails and ID numbers, but masking cannot be guaranteed perfect. "
    "A member of staff can take over if needed."
)

DRAFT_CONSENT_SW = (
    "Msaidizi huyu hujibu kwa kutumia taarifa zilizoidhinishwa na shule. Tafadhali usiweke "
    "taarifa binafsi (zako au za mtoto wako) kwenye mazungumzo. Tunaficha kwa makadirio "
    "majina, namba za simu, barua pepe na namba za kitambulisho, lakini hatuwezi kuhakikisha "
    "kuficha huku kwa asilimia mia moja. Mfanyakazi wa shule anaweza kuchukua mazungumzo."
)


def visible_consent(settings_row) -> dict[str, str] | None:
    """
    Return the consent lines only when an admin has recorded approval.

    The gate is deliberately strict: flag AND name AND date. Until then this
    returns ``None`` and the caller must show nothing.
    """
    if not settings_row.consent_ready:
        return None
    return {"en": DRAFT_CONSENT_EN, "sw": DRAFT_CONSENT_SW}
