"""
apps/assistant/handoff/seed.py
--------------------------------
Seed the starter handoff rules for a Kenyan school.

Call ``seed_starter_rules()`` from the management command
``seed_handoff_rules``.  It is idempotent: rules are looked up by their
``builtin_code`` and updated, never duplicated.

The school's safeguarding lead must review and approve these rules before
going live.  Every rule is created as ``enabled=True`` but the assistant
itself is off until an admin switches it on, so there is no live risk during
set-up.

──────────────────────────────────────────────────────────────────────────────
Starter rule set for a Kenyan school — submitted for school approval
──────────────────────────────────────────────────────────────────────────────

Rule 1 — SAFEGUARDING (REQUIRED, cannot be removed or switched off)
  Fires when the guardrail module has already detected a safeguarding hit
  (the signal LOW_CONFIDENCE covers the gap; the guardrail fires first and
  does not reach routing, so this rule exists as a belt-and-braces fallback
  and for the ``bullying`` and ``medical`` topics).
  Priority: CRITICAL · Destination: role=admin

Rule 2 — Fee dispute
  Topics: fee_dispute.  Priority: URGENT.
  Destination: team "Finance Office" (create if absent).

Rule 3 — Formal complaint
  Topics: complaint.  Priority: URGENT.
  Destination: role admin.

Rule 4 — Admission interview or visit request
  Topics: admissions.  Priority: NORMAL.
  Signals: ASKS_FOR_PERSON.
  Destination: role admin.

Rule 5 — Technical problem
  Topics: technical.  Priority: NORMAL.
  Destination: role admin.

Rule 6 — Visitor asks for a person (generic)
  Signals: ASKS_FOR_PERSON.  Priority: NORMAL.
  Destination: role admin.

Rule 7 — Legal threat
  Topics: legal_threat.  Priority: CRITICAL.
  Destination: role admin.

Rule 8 — Low confidence (AI doesn't know the answer)
  Signals: LOW_CONFIDENCE, REPEATED_UNHELPFUL.  Priority: NORMAL.
  Destination: role admin.
"""

from __future__ import annotations

from ..models import HandoffPriority, HandoffRule, HandoffTeam


STARTER_RULES: list[dict] = [
    {
        "builtin_code": "safeguarding_critical",
        "name": "Safeguarding — critical (required)",
        "description": (
            "REQUIRED RULE — cannot be deleted or switched off. "
            "Fires on bullying, medical emergencies and any message the "
            "safeguarding guardrails have already flagged. "
            "Review this rule with your named safeguarding contact."
        ),
        "priority": HandoffPriority.CRITICAL,
        "is_required": True,
        "enabled": True,
        "topics": "|bullying|medical|",
        "signals": "",
        "destination_type": HandoffRule.DestinationType.ROLE,
        "destination_role": "admin",
        "working_hours_start": "00:00",
        "working_hours_end": "00:00",  # always out-of-hours = always urgent
        "outside_hours_message_en": (
            "I've sent an alert to our safeguarding team and an adult will respond. "
            "If you are in immediate danger, call 999 or Childline Kenya 116."
        ),
        "outside_hours_message_sw": (
            "Nimetuma arifa kwa timu yetu ya ulinzi na mtu mzima atajibu. "
            "Ukiwa katika hatari ya haraka, piga simu 999 au Childline Kenya 116."
        ),
        "order": 1,
    },
    {
        "builtin_code": None,
        "name": "Fee dispute",
        "description": "Route fee disputes and billing questions to the finance team.",
        "priority": HandoffPriority.URGENT,
        "is_required": False,
        "enabled": True,
        "topics": "|fee_dispute|",
        "signals": "",
        "destination_type": HandoffRule.DestinationType.ROLE,
        "destination_role": "finance",
        "working_hours_start": "08:00",
        "working_hours_end": "17:00",
        "outside_hours_message_en": (
            "The finance office is closed right now (Mon–Fri, 8 am – 5 pm Nairobi time). "
            "They will reply on the next working day."
        ),
        "outside_hours_message_sw": (
            "Ofisi ya fedha imefungwa sasa (Jumatatu–Ijumaa, saa 2 asubuhi – saa 11 usiku). "
            "Watajibu siku ya kazi inayofuata."
        ),
        "order": 10,
    },
    {
        "builtin_code": None,
        "name": "Formal complaint",
        "description": "Route formal complaints about the school or staff to the admin.",
        "priority": HandoffPriority.URGENT,
        "is_required": False,
        "enabled": True,
        "topics": "|complaint|",
        "signals": "",
        "destination_type": HandoffRule.DestinationType.ROLE,
        "destination_role": "admin",
        "working_hours_start": "08:00",
        "working_hours_end": "17:00",
        "outside_hours_message_en": (
            "The school office is currently closed. "
            "Your concern has been recorded and the admin will respond on the next working day."
        ),
        "outside_hours_message_sw": (
            "Ofisi ya shule imefungwa kwa sasa. "
            "Wasiwasi wako umerekodiwa na msimamizi atajibu siku ya kazi inayofuata."
        ),
        "order": 20,
    },
    {
        "builtin_code": None,
        "name": "Legal threat",
        "description": "Route any legal threat or mention of court action to admin immediately.",
        "priority": HandoffPriority.CRITICAL,
        "is_required": False,
        "enabled": True,
        "topics": "|legal_threat|",
        "signals": "",
        "destination_type": HandoffRule.DestinationType.ROLE,
        "destination_role": "admin",
        "working_hours_start": "00:00",
        "working_hours_end": "00:00",  # always treat as urgent
        "outside_hours_message_en": (
            "Your message has been flagged and the school administration will respond urgently."
        ),
        "outside_hours_message_sw": (
            "Ujumbe wako umebainishwa na uongozi wa shule utajibu haraka."
        ),
        "order": 5,
    },
    {
        "builtin_code": None,
        "name": "Admissions enquiry — interview or visit",
        "description": "Route admissions interview or school visit requests to admin.",
        "priority": HandoffPriority.NORMAL,
        "is_required": False,
        "enabled": True,
        "topics": "|admissions|",
        "signals": "",
        "destination_type": HandoffRule.DestinationType.ROLE,
        "destination_role": "admin",
        "working_hours_start": "08:00",
        "working_hours_end": "17:00",
        "outside_hours_message_en": (
            "Thank you for your interest. The admissions team will be in touch "
            "on the next working day (Mon–Fri, 8 am – 5 pm)."
        ),
        "outside_hours_message_sw": (
            "Asante kwa nia yako. Timu ya usajili itawasiliana nawe "
            "siku ya kazi inayofuata (Jumatatu–Ijumaa, saa 2 – saa 11)."
        ),
        "order": 30,
    },
    {
        "builtin_code": None,
        "name": "Portal or login problem",
        "description": "Route technical problems (login, portal, app) to admin.",
        "priority": HandoffPriority.NORMAL,
        "is_required": False,
        "enabled": True,
        "topics": "|technical|",
        "signals": "",
        "destination_type": HandoffRule.DestinationType.ROLE,
        "destination_role": "admin",
        "working_hours_start": "08:00",
        "working_hours_end": "17:00",
        "outside_hours_message_en": (
            "The IT support team will respond on the next working day. "
            "If urgent, please email the school office directly."
        ),
        "outside_hours_message_sw": (
            "Timu ya IT itajibu siku ya kazi inayofuata. "
            "Ikiwa ni dharura, tuma barua pepe moja kwa moja kwa ofisi ya shule."
        ),
        "order": 40,
    },
    {
        "builtin_code": None,
        "name": "Visitor asks for a person",
        "description": "Any visitor explicitly asking to speak to a human.",
        "priority": HandoffPriority.NORMAL,
        "is_required": False,
        "enabled": True,
        "topics": "",
        "signals": "|asks_for_person|",
        "destination_type": HandoffRule.DestinationType.ROLE,
        "destination_role": "admin",
        "working_hours_start": "08:00",
        "working_hours_end": "17:00",
        "outside_hours_message_en": (
            "The school office is closed right now. "
            "A member of staff will reply on the next working day. "
            "For urgent matters please call the school directly."
        ),
        "outside_hours_message_sw": (
            "Ofisi ya shule imefungwa sasa. "
            "Mwajiriwa atajibu siku ya kazi inayofuata. "
            "Kwa mambo ya dharura tafadhali piga simu shule moja kwa moja."
        ),
        "order": 50,
    },
    {
        "builtin_code": None,
        "name": "Assistant cannot answer",
        "description": (
            "The AI assistant could not find a good answer or gave repeated unhelpful replies. "
            "Route to the school office so the visitor is not left without help."
        ),
        "priority": HandoffPriority.NORMAL,
        "is_required": False,
        "enabled": True,
        "topics": "",
        "signals": "|low_confidence|repeated_unhelpful|",
        "destination_type": HandoffRule.DestinationType.ROLE,
        "destination_role": "admin",
        "working_hours_start": "08:00",
        "working_hours_end": "17:00",
        "outside_hours_message_en": (
            "I wasn't able to find a good answer to your question. "
            "The school office will reply on the next working day."
        ),
        "outside_hours_message_sw": (
            "Sikuweza kupata jibu zuri kwa swali lako. "
            "Ofisi ya shule itajibu siku ya kazi inayofuata."
        ),
        "order": 90,
    },
]


def seed_starter_rules(*, stdout=None) -> dict[str, int]:
    """
    Create or update the starter rules.  Returns counts of created/updated rows.
    Idempotent: safe to run multiple times.
    """

    def _out(msg: str) -> None:
        if stdout:
            stdout.write(msg + "\n")

    created = 0
    updated = 0
    skipped = 0

    # Ensure a finance team exists (rules reference it by role, not FK, so this
    # is advisory only — keep it for schools that prefer a team destination).
    _finance_team, _team_created = HandoffTeam.objects.get_or_create(
        name="Finance Office",
        defaults={"description": "School finance and fees queries."},
    )
    if _team_created:
        _out("Created team: Finance Office")

    for rule_data in STARTER_RULES:
        code = rule_data.get("builtin_code")
        name = rule_data["name"]

        # Look up by builtin_code if set, else by name
        if code:
            existing = HandoffRule.objects.filter(builtin_code=code).first()
        else:
            existing = HandoffRule.objects.filter(name=name).first()

        fields = {
            "description": rule_data["description"],
            "priority": rule_data["priority"],
            "is_required": rule_data["is_required"],
            "enabled": rule_data["enabled"],
            "topics": rule_data["topics"],
            "signals": rule_data["signals"],
            "destination_type": rule_data["destination_type"],
            "destination_role": rule_data.get("destination_role", ""),
            "working_hours_start": rule_data["working_hours_start"],
            "working_hours_end": rule_data["working_hours_end"],
            "outside_hours_message_en": rule_data["outside_hours_message_en"],
            "outside_hours_message_sw": rule_data["outside_hours_message_sw"],
            "order": rule_data["order"],
        }
        if code:
            fields["builtin_code"] = code

        if existing:
            # Never overwrite a required rule's enabled or is_required flags
            if existing.is_required:
                skipped += 1
                _out(f"  Skipped (required, cannot change): {name}")
                continue
            for attr, value in fields.items():
                setattr(existing, attr, value)
            existing.full_clean()
            existing.save()
            updated += 1
            _out(f"  Updated: {name}")
        else:
            rule = HandoffRule(name=name, **fields)
            rule.full_clean()
            rule.save()
            created += 1
            _out(f"  Created: {name}")

    return {"created": created, "updated": updated, "skipped": skipped}
