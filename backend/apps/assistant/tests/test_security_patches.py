"""Regression tests for security and correctness patches in apps.assistant."""

from __future__ import annotations

import io
import zipfile
from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import RequestFactory, TestCase, override_settings
from django.urls import reverse

from apps.accounts.models import User
from apps.assistant import checks, signed_in_chat
from apps.assistant.forms import ProviderForm
from apps.assistant.handoff.routing import create_handoff
from apps.assistant.knowledge.validators import KnowledgeFileValidator
from apps.assistant.models import (
    AssistantSettings,
    ChatConversation,
    ChatMessage,
    CustomGuardrailRule,
    Handoff,
    HandoffPriority,
    HandoffRule,
    ProviderConfig,
    SignedInConversation,
    SignedInMessage,
)
from apps.assistant.providers import http as provider_http


class ProviderSSRFTests(TestCase):
    def test_provider_form_rejects_private_and_local_urls(self):
        for bad_url in (
            "http://127.0.0.1:8000/v1",
            "http://169.254.169.254/latest/meta-data",
            "http://localhost:11434/v1",
            "http://10.0.0.5/v1",
        ):
            form = ProviderForm(
                data={
                    "name": "Local",
                    "kind": ProviderConfig.Kind.OPENAI_COMPATIBLE,
                    "model_name": "gpt-4o-mini",
                    "base_url": bad_url,
                    "timeout_seconds": 30,
                    "priority": 1,
                }
            )
            self.assertFalse(form.is_valid(), f"Expected {bad_url} to be rejected")
            self.assertIn("base_url", form.errors)

    def test_post_json_refuses_private_ip_and_non_http_scheme(self):
        for bad_url in (
            "file:///etc/passwd",
            "http://127.0.0.1:8080/v1/chat",
            "http://169.254.169.254/latest",
        ):
            with self.assertRaises(provider_http.TransportNetworkError):
                provider_http.post_json(bad_url, {"a": 1}, headers={})


class EncryptionCheckTests(TestCase):
    @override_settings(ASSISTANT_ENCRYPTION_KEY="not-a-valid-fernet-key")
    def test_invalid_fernet_key_triggers_e001(self):
        errors = checks.assistant_encryption_key_check(None)
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0].id, "assistant.E001")


class DocxZipBombValidatorTests(TestCase):
    def test_oversized_uncompressed_docx_is_rejected(self):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("word/document.xml", b"<w:document/>")
            zf.writestr("word/huge.bin", b"A" * (1024 * 1024))
        upload = SimpleUploadedFile("bomb.docx", buf.getvalue())
        validator = KnowledgeFileValidator(max_mb=20)
        with patch(
            "apps.assistant.knowledge.validators.MAX_DOCX_UNCOMPRESSED_BYTES", 1024
        ):
            with self.assertRaises(ValidationError):
                validator(upload)


class SignedInHandoffAndGuardrailTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(
            username="asst_admin",
            email="asst_admin@example.com",
            password="Password123!",
            role="admin",
            must_change_password=False,
        )
        self.teacher = User.objects.create_user(
            username="asst_teacher",
            email="asst_teacher@example.com",
            password="Password123!",
            role="teacher",
            must_change_password=False,
        )

    def test_create_handoff_supports_signed_in_conversation(self):
        rule = HandoffRule.objects.create(
            name="Escalation",
            signals=f"|{HandoffRule.Signal.ASKS_FOR_PERSON}|",
            priority=HandoffPriority.NORMAL,
            destination_role="admin",
            enabled=True,
        )
        conv = SignedInConversation.objects.create(
            user=self.teacher,
            role_snapshot="teacher",
        )
        SignedInMessage.objects.create(
            conversation=conv,
            role=SignedInMessage.Role.USER,
            body="Please connect me to a human.",
        )
        handoff = create_handoff(conv, rule, AssistantSettings.read())
        self.assertIsNone(handoff.conversation)
        self.assertEqual(handoff.signed_in_conversation, conv)
        self.assertTrue(
            SignedInMessage.objects.filter(
                conversation=conv, role=SignedInMessage.Role.SYSTEM
            ).exists()
        )

    def test_signed_in_chat_runs_custom_guardrail_rules(self):
        CustomGuardrailRule.objects.create(
            name="Blocked phrase",
            category="other",
            patterns="forbidden_secret_phrase",
            is_active=True,
        )
        settings_row = AssistantSettings.read()
        settings_row.enabled = True
        settings_row.signed_in_enabled = True
        settings_row.save()
        request = RequestFactory().post("/assistant/signed-in/send/")
        request.user = self.teacher
        turn = signed_in_chat.begin(request, text="Tell me about forbidden_secret_phrase")
        done = signed_in_chat.complete(turn)
        last_msg = SignedInMessage.objects.filter(
            conversation=done.conversation, role=SignedInMessage.Role.ASSISTANT
        ).last()
        self.assertIsNotNone(last_msg)
        self.assertEqual(last_msg.blocked_reason, "safeguarding")


class CsvExportNeutralizationTests(TestCase):
    def test_csv_export_neutralizes_formula_prefixes(self):
        admin = User.objects.create_user(
            username="csv_admin",
            email="csv_admin@example.com",
            password="Password123!",
            role="admin",
            must_change_password=False,
        )
        conv = ChatConversation.objects.create(visitor_key="v" * 32)
        ChatMessage.objects.create(
            conversation=conv,
            role=ChatMessage.Role.VISITOR,
            body="=CMD('calc')",
        )
        self.client.force_login(admin)
        resp = self.client.get(reverse("assistant:conversation_export", kwargs={"pk": conv.pk}))
        self.assertEqual(resp.status_code, 200, f"Unexpected redirect to {resp.get('Location')}")
        self.assertIn(b"'=CMD('calc')", resp.content)
