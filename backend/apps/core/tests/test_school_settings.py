"""
apps/core/tests/test_school_settings.py
---------------------------------------
Admin Portal checkpoint 4 (School Settings): the editable SchoolSettings
singleton, its .env fallback, its audit trail and its admin-only gate.
"""

from __future__ import annotations

import base64
import tempfile

from django.core.files.base import ContentFile
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from apps.accounts.models import User
from apps.core.models import AuditLog, SchoolSettings
from apps.core.selectors import school_settings_values

#: A 1x1 PNG. ``ImageField`` validates the bytes, so a placeholder string is not
#: a file the form will accept.
PNG_BYTES = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)


class SchoolSettingsReadTests(TestCase):
    """Effective values: the DB row wins, blanks fall back to settings."""

    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create_user(
            username="settings_admin",
            email="settings_admin@test.school",
            password="Testpass1!",
            role=User.Role.ADMIN,
            must_change_password=False,
        )

    @override_settings(SCHOOL_NAME="Env School", SCHOOL_CURRENCY="NGN")
    def test_blank_fields_fall_back_to_settings(self):
        values = school_settings_values()
        self.assertEqual(values["name"], "Env School")
        self.assertEqual(values["currency"], "NGN")
        self.assertTrue(values["show_positions"])

    @override_settings(SCHOOL_NAME="Env School")
    def test_saved_row_beats_the_env_value(self):
        row = SchoolSettings.load()
        row.name = "Saved School"
        row.save()

        self.assertEqual(school_settings_values()["name"], "Saved School")

    @override_settings(SCHOOL_NAME="Env School")
    def test_context_processor_exposes_effective_values(self):
        row = SchoolSettings.load()
        row.motto = "Learn today"
        row.save()

        self.client.force_login(self.admin)
        context = self.client.get(reverse("core_school:settings")).context

        self.assertEqual(context["SCHOOL_NAME"], "Env School")
        self.assertEqual(context["SCHOOL_MOTTO"], "Learn today")
        self.assertIn("SCHOOL_CURRENCY", context)
        self.assertIn("SCHOOL_SHOW_POSITIONS", context)

    def test_reading_settings_does_not_create_a_row(self):
        """The context processor runs on every request, including anonymous ones."""
        school_settings_values()
        self.assertEqual(SchoolSettings.objects.count(), 0)


class SchoolSettingsWriteTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create_user(
            username="settings_writer",
            email="settings_writer@test.school",
            password="Testpass1!",
            role=User.Role.ADMIN,
            must_change_password=False,
        )
        cls.url = reverse("core_school:settings")

    def test_admin_can_open_the_page(self):
        self.client.force_login(self.admin)
        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "School Settings")
        self.assertContains(response, "Save Settings")

    def test_admin_can_save_and_it_stays_a_singleton(self):
        self.client.force_login(self.admin)
        response = self.client.post(
            self.url,
            {
                "name": "Bright Future School",
                "motto": "Knowledge first",
                "phone": "+233 20 000 0000",
                "email": "bureau@bright.school",
                "currency": "ghs",
                "show_positions": "on",
                "quiet_hours_start": "20",
                "quiet_hours_end": "6",
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(SchoolSettings.objects.count(), 1)
        row = SchoolSettings.objects.get(pk=SchoolSettings.SINGLETON_PK)
        self.assertEqual(row.name, "Bright Future School")
        self.assertEqual(row.email, "bureau@bright.school")
        # clean_currency() normalises the ISO code.
        self.assertEqual(row.currency, "GHS")
        self.assertTrue(row.show_positions)

    def test_unchecking_positions_persists_as_false(self):
        self.client.force_login(self.admin)
        self.client.post(
            self.url,
            {"name": "X", "motto": "", "phone": "", "email": "", "currency": "GHS",
             "quiet_hours_start": "20", "quiet_hours_end": "6"},
        )

        self.assertFalse(SchoolSettings.objects.get(pk=1).show_positions)
        self.assertFalse(school_settings_values()["show_positions"])

    def test_changing_a_field_records_before_and_after_in_the_audit_log(self):
        row = SchoolSettings.load()
        row.name = "Old Name"
        row.currency = "GHS"
        row.save()

        self.client.force_login(self.admin)
        self.client.post(
            self.url,
            {
                "name": "New Name",
                "motto": row.motto,
                "phone": row.phone,
                "email": row.email,
                "currency": "GHS",
                "show_positions": "on",
                "fees_enabled": "on" if row.fees_enabled else "",
                "quiet_hours_start": str(row.quiet_hours_start),
                "quiet_hours_end": str(row.quiet_hours_end),
            },
        )

        entry = AuditLog.objects.get(action="core.school_settings_updated")
        self.assertEqual(entry.actor, self.admin)
        self.assertEqual(entry.content_type, "core.SchoolSettings")
        self.assertEqual(
            entry.change_summary["name"], {"from": "Old Name", "to": "New Name"}
        )
        # Untouched fields must not appear in the diff.
        self.assertNotIn("currency", entry.change_summary)

    def test_saving_identical_values_writes_no_audit_row(self):
        row = SchoolSettings.load()
        self.client.force_login(self.admin)
        self.client.post(
            self.url,
            {
                "name": row.name,
                "motto": row.motto,
                "phone": row.phone,
                "email": row.email,
                "currency": row.currency,
                "show_positions": "on",
                # Every switch and field the form owns must be sent in its stored
                # state, or the "identical" save is a real change to an omitted field.
                "fees_enabled": "on" if row.fees_enabled else "",
                "learning_enabled": "on" if row.learning_enabled else "",
                "engagement_enabled": "on" if row.engagement_enabled else "",
                "quizzes_enabled": "on" if row.quizzes_enabled else "",
                "live_classes_enabled": "on" if row.live_classes_enabled else "",
                "feed_community_enabled": "on" if row.feed_community_enabled else "",
                "notification_quiet_hours_enabled": "on" if row.notification_quiet_hours_enabled else "",
                "quiet_hours_start": str(row.quiet_hours_start),
                "quiet_hours_end": str(row.quiet_hours_end),
                "theme": row.theme or "dark",
            },
        )

        self.assertFalse(
            AuditLog.objects.filter(action="core.school_settings_updated").exists()
        )

    @override_settings(MEDIA_ROOT=tempfile.mkdtemp())
    def test_saving_the_name_does_not_delete_the_logo(self):
        """
        The public site added an image field to this row. A file input posts
        nothing when nobody touches it, and that must not read as "clear it" —
        otherwise editing the school's name would wipe its crest.
        """
        row = SchoolSettings.load()
        row.name = "Old Name"
        row.logo = ContentFile(PNG_BYTES, name="crest.png")
        row.save()
        stored_name = row.logo.name

        self.client.force_login(self.admin)
        self.client.post(
            self.url,
            {
                "name": "New Name",
                "motto": row.motto,
                "phone": row.phone,
                "email": row.email,
                "currency": row.currency,
                "show_positions": "on",
                "fees_enabled": "on" if row.fees_enabled else "",
                "learning_enabled": "on" if row.learning_enabled else "",
                "engagement_enabled": "on" if row.engagement_enabled else "",
                "quizzes_enabled": "on" if row.quizzes_enabled else "",
                "live_classes_enabled": "on" if row.live_classes_enabled else "",
                "feed_community_enabled": "on" if row.feed_community_enabled else "",
                "notification_quiet_hours_enabled": "on" if row.notification_quiet_hours_enabled else "",
                "quiet_hours_start": str(row.quiet_hours_start),
                "quiet_hours_end": str(row.quiet_hours_end),
                "theme": row.theme or "dark",
            },
        )

        row.refresh_from_db()
        self.assertEqual(row.name, "New Name")
        self.assertEqual(row.logo.name, stored_name)
        entry = AuditLog.objects.get(action="core.school_settings_updated")
        self.assertEqual(list(entry.change_summary), ["name"])

    @override_settings(MEDIA_ROOT=tempfile.mkdtemp())
    def test_an_uploaded_logo_is_audited_by_its_name_not_its_object(self):
        """
        The audit summary goes into a JSON column, so a FieldFile would have
        raised on save — and a log row is not a second copy of the upload.
        """
        row = SchoolSettings.load()
        self.client.force_login(self.admin)
        response = self.client.post(
            self.url,
            {
                "name": row.name,
                "motto": row.motto,
                "phone": row.phone,
                "email": row.email,
                "currency": row.currency,
                "show_positions": "on",
                "fees_enabled": "on" if row.fees_enabled else "",
                # The test client takes an upload in the data dict, not a
                # separate ``files`` argument — as a kwarg it is dropped.
                "logo": SimpleUploadedFile("crest.png", PNG_BYTES),
            },
        )
        self.assertEqual(response.status_code, 302)

        entry = AuditLog.objects.get(action="core.school_settings_updated")
        summary = entry.change_summary["logo"]
        self.assertIsNone(summary["from"])
        self.assertEqual(summary["to"], "crest.png")
        self.assertEqual(
            SchoolSettings.load().logo.name, f"school/logo/{summary['to']}"
        )

    def test_invalid_email_is_rejected_without_saving(self):
        self.client.force_login(self.admin)
        response = self.client.post(
            self.url,
            {"name": "Y", "motto": "", "phone": "", "email": "not-an-email",
             "quiet_hours_start": "20", "quiet_hours_end": "6"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Enter a valid email address")
        self.assertEqual(SchoolSettings.objects.get(pk=1).name, "")


class SchoolSettingsAccessTests(TestCase):
    """Only the admin role may read or write school identity."""

    NON_ADMIN_ROLES = [
        User.Role.TEACHER,
        User.Role.STUDENT,
        User.Role.PARENT,
        User.Role.DIRECTOR,
        User.Role.FINANCE,
    ]

    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create_user(
            username="gate_admin",
            email="gate_admin@test.school",
            password="Testpass1!",
            role=User.Role.ADMIN,
            must_change_password=False,
        )
        cls.users = {
            role: User.objects.create_user(
                username=f"gate_{role}",
                email=f"gate_{role}@test.school",
                password="Testpass1!",
                role=role,
                must_change_password=False,
            )
            for role in cls.NON_ADMIN_ROLES
        }
        # A superuser that declares a non-admin role is still held to that role.
        cls.director_superuser = User.objects.create_superuser(
            username="gate_sup_director",
            email="gate_sup_director@test.school",
            password="Testpass1!",
            role=User.Role.DIRECTOR,
            must_change_password=False,
        )
        cls.url = reverse("core_school:settings")

    def test_non_admin_roles_are_denied(self):
        payload = {"name": "Sneaky", "motto": "", "phone": "", "email": ""}
        for role, user in self.users.items():
            with self.subTest(role=role):
                self.client.force_login(user)
                self.assertEqual(self.client.get(self.url).status_code, 403)
                self.assertEqual(self.client.post(self.url, payload).status_code, 403)
        self.assertEqual(SchoolSettings.objects.filter(name="Sneaky").count(), 0)

    def test_superuser_with_another_role_is_denied(self):
        self.client.force_login(self.director_superuser)
        self.assertEqual(self.client.get(self.url).status_code, 403)

    def test_admin_is_allowed(self):
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get(self.url).status_code, 200)
