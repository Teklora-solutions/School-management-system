"""Regression tests for markdown sanitization, lesson attachment gating, and quiz save."""

from __future__ import annotations

from django.test import TestCase
from django.urls import reverse

from apps.accounts.models import User
from apps.core.models import SchoolSettings
from apps.learning.markdown_utils import render_notes


class MarkdownSanitizationTests(TestCase):
    def test_markdown_strips_user_rel_and_blocks_mailto_img(self):
        md = (
            '[Click](https://example.com "target=")\n\n'
            '<a href="https://example.com" rel="opener">Raw</a>\n\n'
            '<img src="mailto:evil@example.com" alt="x">'
        )
        html = str(render_notes(md, notes_format="markdown"))
        self.assertNotIn('rel="opener"', html)
        self.assertIn('target="_blank"', html)
        self.assertIn('rel="noopener noreferrer"', html)
        self.assertNotIn("mailto:evil@example.com", html)


class LessonAttachmentFeatureGateTests(TestCase):
    def test_lesson_attachment_respects_learning_feature_flag(self):
        admin = User.objects.create_user(
            username="learn_admin",
            email="learn_admin@example.com",
            password="Password123!",
            role="admin",
            must_change_password=False,
        )
        row = SchoolSettings.load()
        row.feature_learning = False
        row.save()
        self.client.force_login(admin)
        resp = self.client.get(
            reverse(
                "learning:lesson_attachment_file",
                kwargs={"lesson_id": 9999, "attachment_id": 9999},
            )
        )
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Learning")
