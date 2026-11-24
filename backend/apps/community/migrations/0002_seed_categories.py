"""
0002_seed_categories.py
-----------------------
Seeds the eleven default community categories.

This runs automatically as part of migrate. If the school has already created
categories the seed is skipped (guards on slug uniqueness). Each category can
be renamed, reordered or hidden by an admin at any time.

Visibility defaults
~~~~~~~~~~~~~~~~~~~
  All          — virtual; not a real category
  Announcements          — PUBLIC
  General Discussion     — PUBLIC
  Homework Help          — PUBLIC
  Wins and Celebrations  — PUBLIC
  Parents Corner         — SIGNED_IN (parent, teacher, director, admin)
  Teachers Lounge        — ROLE     (teacher, director, admin only)
  Events and Reminders   — PUBLIC
  Student Voice          — SIGNED_IN
  Spotlight              — PUBLIC
  Resources and Ideas    — PUBLIC
"""

from django.db import migrations, models


SEED = [
    {
        "slug": "announcements",
        "name": "Announcements",
        "icon": "bi-megaphone",
        "description": "Official notices from the school.",
        "visibility": "public",
        "allowed_roles": [],
        "display_order": 10,
    },
    {
        "slug": "general-discussion",
        "name": "General Discussion",
        "icon": "bi-chat-dots",
        "description": "Open conversations for everyone in our community.",
        "visibility": "public",
        "allowed_roles": [],
        "display_order": 20,
    },
    {
        "slug": "homework-help",
        "name": "Homework Help",
        "icon": "bi-book",
        "description": "Ask questions, share tips, help each other learn.",
        "visibility": "public",
        "allowed_roles": [],
        "display_order": 30,
    },
    {
        "slug": "wins-and-celebrations",
        "name": "Wins and Celebrations",
        "icon": "bi-stars",
        "description": "Share good news, achievements and proud moments.",
        "visibility": "public",
        "allowed_roles": [],
        "display_order": 40,
    },
    {
        "slug": "parents-corner",
        "name": "Parents Corner",
        "icon": "bi-people",
        "description": "A space for parents and guardians to connect.",
        "visibility": "signed_in",
        "allowed_roles": ["parent", "teacher", "director", "admin"],
        "display_order": 50,
    },
    {
        "slug": "teachers-lounge",
        "name": "Teachers Lounge",
        "icon": "bi-person-workspace",
        "description": "Staff-only discussion area.",
        "visibility": "role",
        "allowed_roles": ["teacher", "director", "admin"],
        "display_order": 60,
    },
    {
        "slug": "events-and-reminders",
        "name": "Events and Reminders",
        "icon": "bi-calendar-event",
        "description": "Upcoming school events, dates and reminders.",
        "visibility": "public",
        "allowed_roles": [],
        "display_order": 70,
    },
    {
        "slug": "student-voice",
        "name": "Student Voice",
        "icon": "bi-mic",
        "description": "Students: share your ideas and have your say.",
        "visibility": "signed_in",
        "allowed_roles": [],
        "display_order": 80,
    },
    {
        "slug": "spotlight",
        "name": "Spotlight",
        "icon": "bi-trophy",
        "description": "Student and teacher achievements in the spotlight.",
        "visibility": "public",
        "allowed_roles": [],
        "display_order": 90,
    },
    {
        "slug": "resources-and-ideas",
        "name": "Resources and Ideas",
        "icon": "bi-lightbulb",
        "description": "Useful links, documents and creative ideas.",
        "visibility": "public",
        "allowed_roles": [],
        "display_order": 100,
    },
]


def seed_categories(apps, schema_editor):
    Category = apps.get_model("community", "Category")
    for data in SEED:
        Category.objects.get_or_create(slug=data["slug"], defaults=data)


def unseed_categories(apps, schema_editor):
    # Only remove rows that match the seed exactly (slug match).
    # A school that has renamed a category keeps their version.
    Category = apps.get_model("community", "Category")
    slugs = [d["slug"] for d in SEED]
    Category.objects.filter(slug__in=slugs).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("community", "0001_initial"),
    ]

    operations = [
        migrations.AlterField(
            model_name="category",
            name="icon",
            field=models.CharField(
                blank=True,
                help_text="Bootstrap Icons class, e.g. 'bi-megaphone'. Shown on the pill.",
                max_length=40,
            ),
        ),
        migrations.RunPython(seed_categories, reverse_code=unseed_categories),
    ]
