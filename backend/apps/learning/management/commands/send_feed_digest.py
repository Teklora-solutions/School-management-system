"""
Send the morning class-feed digest now, without waiting for 07:00.

This is the phone-test button's server side: it runs the same task the worker
would run, with the weekday check bypassed so a test on a Saturday still sends.
It does NOT bypass the one-digest-per-teacher-per-day guard, so running it
twice in one day will not spam a teacher.

Usage
-----
    python manage.py send_feed_digest
"""

from django.core.management.base import BaseCommand

from apps.learning.digest_tasks import send_teacher_digests


class Command(BaseCommand):
    help = "Send today's unanswered-question digest to teachers (manual run)."

    def handle(self, *args, **options):
        sent = send_teacher_digests(force=True)
        self.stdout.write(
            self.style.SUCCESS(f"Digest notifications sent: {sent}")
        )
