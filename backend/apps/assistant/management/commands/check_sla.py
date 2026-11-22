"""
management command: check_sla
------------------------------
Run the SLA overdue check manually (also called by the Q scheduler).

Usage
-----
    python manage.py check_sla
"""

from __future__ import annotations

from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Check all open handoffs for SLA breaches, send reminders and escalate."

    def handle(self, *args, **options):
        from apps.assistant.tasks import check_sla_task

        result = check_sla_task()
        self.stdout.write(
            self.style.SUCCESS(
                f"SLA check complete: {result['overdue']} overdue, "
                f"{result['reminded']} reminded, {result['escalated']} escalated."
            )
        )
