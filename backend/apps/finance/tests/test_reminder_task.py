"""The fee-reminder background task must create a real announcement."""

from django.test import TestCase

from apps.communications.models import Announcement
from apps.dashboards.tests.fixtures import make_user

from ..tasks import send_fee_reminder_task


class FeeReminderTaskTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = make_user("admin", username="reminder_admin")

    def test_task_creates_role_targeted_announcement(self):
        send_fee_reminder_task(
            student_ids=[],
            title="Fees due",
            body="Please clear your balance.",
            actor_id=self.admin.pk,
        )
        announcement = Announcement.objects.get(title="Fees due")
        self.assertEqual(announcement.audience, Announcement.Audience.BY_ROLE)
        self.assertEqual(announcement.target_roles, ["student", "parent"])
