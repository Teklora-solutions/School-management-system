"""
management command: seed_handoff_rules
--------------------------------------
Seed the Kenyan starter handoff rules for a new installation.

Usage
-----
    python manage.py seed_handoff_rules
    python manage.py seed_handoff_rules --dry-run

The command is idempotent: rules looked up by their ``builtin_code`` (for
required rules) or by name are updated, never duplicated.

Required rules (safeguarding) are never modified by this command.
"""

from __future__ import annotations

from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Seed the Kenyan starter handoff rules (idempotent)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Show what would be created without saving anything.",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]

        if dry_run:
            self.stdout.write(self.style.WARNING("Dry run — nothing will be saved.\n"))
            from apps.assistant.handoff.seed import STARTER_RULES
            self.stdout.write(f"{len(STARTER_RULES)} rules would be processed:\n")
            for rule in STARTER_RULES:
                self.stdout.write(f"  • {rule['name']} ({rule['priority']})")
            return

        from apps.assistant.handoff.seed import seed_starter_rules

        counts = seed_starter_rules(stdout=self.stdout)
        self.stdout.write(
            self.style.SUCCESS(
                f"\nDone — {counts['created']} created, "
                f"{counts['updated']} updated, "
                f"{counts['skipped']} skipped (required rules are never overwritten)."
            )
        )
        self.stdout.write(
            self.style.WARNING(
                "\nReminder: review the safeguarding rule and set a named "
                "safeguarding contact in AI Assistant → Settings."
            )
        )
