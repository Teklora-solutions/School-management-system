"""
apps/assistant/guardrails/safe_reply.py
---------------------------------------
The fixed reply shown when safeguarding rules fire.

Wording follows the approved order: the school's own safeguarding contact
comes FIRST, then 999/112, then Childline Kenya 116. If the admin has not set
a contact, the reply still goes out with the emergency numbers and the school
office, and the alert is still raised.
"""

from __future__ import annotations

CHILDLINE = "116"
EMERGENCY = "999 or 112"


def build(settings_row) -> str:
    if settings_row.has_safeguarding_contact:
        contact = settings_row.safeguarding_contact_name.strip()
        phone = settings_row.safeguarding_contact_phone.strip()
        email = settings_row.safeguarding_contact_email.strip()
        reach = ", ".join(part for part in (phone, email) if part)
        contact_en = f"{contact}" + (f" — {reach}" if reach else "")
        contact_sw = contact_en
    else:
        contact_en = "the school office"
        contact_sw = "ofisi ya shule"

    return (
        "I'm passing this to a person who can help, and I won't try to handle it as "
        "ordinary school business.\n\n"
        f"School safeguarding contact: {contact_en}\n"
        f"Immediate danger: call {EMERGENCY}.\n"
        f"A child can call Childline Kenya free on {CHILDLINE}.\n\n"
        "Nakupitisha kwa mtu anayeweza kusaidia; sitajibu hili kama swali la kawaida la shule.\n"
        f"Mtu wa usalama wa shule: {contact_sw}\n"
        f"Hatari ya haraka: piga {EMERGENCY}.\n"
        f"Mtoto anaweza kupiga Childline Kenya bure kwa {CHILDLINE}."
    )
