"""
Erase the masked excerpt of closed safeguarding alerts after the retention period.

DRAFT: the retention period (``AssistantSettings.alert_retention_months``,
proposed 12 months) is awaiting approval. This command is deliberately manual
and dry-run by default; it is not scheduled by any deploy.

The alert row is kept: category, profile, dates, acknowledgement, closure and
outcome all remain as the minimal accountability record. Only the excerpt text
is cleared, and ``excerpt_erased_at`` records when.

``--alert-id`` erases one closed alert's excerpt early, for a deletion request
the safeguarding process has cleared. An open or acknowledged alert is refused.
Never prints the excerpt.
"""

from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError
from django.shortcuts import get_object_or_404

from apps.assistant import alerts
from apps.assistant.models import AssistantSettings, SafeguardingAlert


class Command(BaseCommand):
    help = "Erase closed safeguarding alert excerpts past the proposed retention period."

    def add_arguments(self, parser):
        parser.add_argument(
            "--apply",
            action="store_true",
            help="Write the erasure. Without this flag the command only reports.",
        )
        parser.add_argument(
            "--alert-id",
            type=int,
            help="Erase one closed alert's excerpt early (deletion request).",
        )

    def handle(self, *args, **options):
        if options["alert_id"]:
            alert = get_object_or_404(SafeguardingAlert, pk=options["alert_id"])
            if not alert.is_closed:
                raise CommandError(
                    "Only a closed alert's excerpt can be erased. Close it first."
                )
            if not alert.excerpt:
                self.stdout.write("That alert has no excerpt to erase.")
                return
            if options["apply"]:
                alerts.erase_excerpts(alert_id=alert.pk, reason="request")
                self.stdout.write(self.style.SUCCESS("Excerpt erased; the record was kept."))
            else:
                self.stdout.write(
                    f"Alert {alert.pk} ({alert.category}) would have its excerpt erased. "
                    "Run again with --apply to write."
                )
            return

        months = AssistantSettings.load().alert_retention_months or 0
        if months <= 0:
            self.stdout.write("Retention is off (0 months); no excerpt is due for erasure.")
            return

        due = alerts.erasable_excerpts()
        if options["apply"]:
            erased = alerts.erase_excerpts(reason="retention")
            self.stdout.write(
                self.style.SUCCESS(
                    f"Erased {len(erased)} excerpt(s) from alerts closed more than "
                    f"{months} month(s) ago; the records were kept."
                )
            )
        else:
            self.stdout.write(
                f"{len(due)} excerpt(s) would be erased (closed more than {months} "
                "month(s) ago). Run again with --apply to write."
            )
