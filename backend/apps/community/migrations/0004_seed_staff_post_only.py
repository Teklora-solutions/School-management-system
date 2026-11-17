"""
0004_seed_staff_post_only.py
----------------------------
Sets staff_post_only=True on the three categories that should be
school-curated: Announcements, Events and Reminders, and Spotlight.
Seeds the initial BlockedWord list (hold-for-review, never outright block).
"""

from django.db import migrations

STAFF_ONLY_SLUGS = [
    "announcements",
    "events-and-reminders",
    "spotlight",
]

# Words / phrases whose presence holds a post for review.
# Grouped by type; the type is stored in the note field for admin context.
# This list is intentionally conservative — it queues for human review,
# never silently rejects.
BLOCKED_WORDS = [
    # ── English profanity (clear cases only) ──────────────────────────────
    ("damn",        "English profanity"),
    ("crap",        "English profanity"),
    ("hell",        "English mild — context-dependent, queue for review"),
    ("idiot",       "English insult"),
    ("stupid",      "English insult — common in bullying"),
    ("dumb",        "English insult"),
    ("loser",       "English insult"),
    ("hate you",    "English hostility phrase"),
    ("shut up",     "English hostility phrase"),
    ("kill",        "English — safety signal, context-dependent"),
    ("die",         "English — safety signal, context-dependent"),
    ("hurt myself", "English safeguarding signal"),
    ("hurt you",    "English threat signal"),
    # ── Kiswahili / Sheng (common on Kenyan school platforms) ─────────────
    ("mjinga",      "Kiswahili insult — fool/idiot"),
    ("mwizi",       "Kiswahili — thief, used as insult"),
    ("wewe ni",     "Kiswahili phrase starter — often precedes an insult"),
    ("tafuta",      "Sheng — context-dependent"),
    ("punda",       "Kiswahili mild insult — donkey"),
    ("fala",        "Sheng insult — fool"),
    ("meffi",       "Sheng profanity"),
    ("umbwa",       "Kiswahili insult — dog, used offensively"),
    # ── Safeguarding / privacy signals ───────────────────────────────────
    ("my number",   "Privacy — may be sharing contact details"),
    ("phone number","Privacy — may be sharing contact details"),
    ("whatsapp me", "Privacy — off-platform contact"),
    ("call me",     "Privacy — off-platform contact"),
    ("meet me",     "Safety — potential off-site meeting arrangement"),
    ("come to",     "Safety — location-sharing signal"),
    ("my address",  "Privacy — location signal"),
]


def seed_staff_only(apps, schema_editor):
    Category = apps.get_model("community", "Category")
    Category.objects.filter(slug__in=STAFF_ONLY_SLUGS).update(staff_post_only=True)


def seed_blocked_words(apps, schema_editor):
    BlockedWord = apps.get_model("community", "BlockedWord")
    for word, note in BLOCKED_WORDS:
        BlockedWord.objects.get_or_create(
            word=word,
            defaults={"note": note, "is_active": True},
        )


def unseed_staff_only(apps, schema_editor):
    Category = apps.get_model("community", "Category")
    Category.objects.filter(slug__in=STAFF_ONLY_SLUGS).update(staff_post_only=False)


def unseed_blocked_words(apps, schema_editor):
    BlockedWord = apps.get_model("community", "BlockedWord")
    words = [w for w, _ in BLOCKED_WORDS]
    BlockedWord.objects.filter(word__in=words).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("community", "0003_participation_and_moderation"),
    ]

    operations = [
        migrations.RunPython(seed_staff_only,    reverse_code=unseed_staff_only),
        migrations.RunPython(seed_blocked_words, reverse_code=unseed_blocked_words),
    ]
