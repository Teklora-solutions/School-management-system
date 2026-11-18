"""
The knowledge admin screens: who may open them, what a save stores, versioning,
the audience ceiling on a crafted POST, and permission-checked downloads.
"""

from __future__ import annotations

from django.core.files.base import ContentFile
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse

from apps.assistant.models import (
    KnowledgeAudience,
    KnowledgeCollection,
    KnowledgeDocument,
    KnowledgeStatus,
)
from apps.core.models import AuditLog

from .knowledge_helpers import (
    KnowledgeTestCase,
    pdf_bytes,
    pdf_upload,
)

BACKEND = "apps.accounts.backends.EmailOrAdmissionBackend"


class ScreenAccessTests(KnowledgeTestCase):
    def test_an_admin_can_open_every_knowledge_screen(self):
        collection = self.make_collection("Fees")
        document = self.make_document(collection)
        self.client.force_login(self.admin, backend=BACKEND)
        urls = [
            reverse("assistant:knowledge_collections"),
            reverse("assistant:knowledge_collection_create"),
            reverse("assistant:knowledge_collection_update", args=[collection.pk]),
            reverse("assistant:knowledge_documents"),
            reverse("assistant:knowledge_document_new"),
            reverse("assistant:knowledge_document_new_upload"),
            reverse("assistant:knowledge_document_new_url"),
            reverse("assistant:knowledge_document_new_paste"),
            reverse("assistant:knowledge_document_new_faq"),
            reverse("assistant:knowledge_document", args=[document.pk]),
            reverse("assistant:knowledge_document_edit", args=[document.pk]),
        ]
        for url in urls:
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 200)

    def test_teachers_and_parents_are_refused(self):
        collection = self.make_collection("Fees")
        document = self.make_document(collection)
        for user in (self.teacher, self.parent):
            self.client.force_login(user, backend=BACKEND)
            for url in (
                reverse("assistant:knowledge_collections"),
                reverse("assistant:knowledge_documents"),
                reverse("assistant:knowledge_document", args=[document.pk]),
                reverse("assistant:knowledge_document_download", args=[document.pk]),
            ):
                with self.subTest(role=user.role, url=url):
                    self.assertEqual(self.client.get(url).status_code, 403)

    def test_a_visitor_is_sent_to_login(self):
        response = self.client.get(reverse("assistant:knowledge_documents"))
        self.assertEqual(response.status_code, 302)
        self.assertIn("/login/", response["Location"])


class CollectionScreenTests(KnowledgeTestCase):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.admin, backend=BACKEND)

    def post(self, **overrides):
        data = {
            "name": "Admissions",
            "slug": "",
            "description": "How to apply.",
            "audience": "public",
            "status": "approved",
            "effective_from": "",
            "effective_to": "",
        }
        data.update(overrides)
        return self.client.post(reverse("assistant:knowledge_collection_create"), data)

    def test_a_collection_is_saved_with_an_owner_and_a_slug(self):
        response = self.post()
        self.assertEqual(response.status_code, 302)
        collection = KnowledgeCollection.objects.get(name="Admissions")
        self.assertEqual(collection.slug, "admissions")
        self.assertEqual(collection.owner, self.admin)
        self.assertTrue(
            AuditLog.objects.filter(action="assistant.knowledge_collection_saved").exists()
        )

    def test_a_public_collection_cannot_be_role_limited(self):
        response = self.post(audience="public", roles=["teacher"])
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "public collection cannot be limited to roles")
        self.assertFalse(KnowledgeCollection.objects.filter(name="Admissions").exists())


class DocumentCreationTests(KnowledgeTestCase):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.admin, backend=BACKEND)
        self.collection = self.make_collection("Admissions", audience=KnowledgeAudience.PUBLIC)

    def paste_data(self, **overrides):
        data = {
            "collection": self.collection.pk,
            "title": "Opening hours",
            "language": "en",
            "audience": "signed_in",
            "status": "approved",
            "effective_from": "",
            "effective_to": "",
            "body_text": "The office opens at 8 am and closes at 4 pm on school days.",
        }
        data.update(overrides)
        return data

    def test_pasted_text_gets_a_first_version_and_an_audit_entry(self):
        response = self.client.post(
            reverse("assistant:knowledge_document_new_paste"), self.paste_data()
        )
        document = KnowledgeDocument.objects.get()
        self.assertRedirects(
            response, reverse("assistant:knowledge_document", args=[document.pk])
        )
        self.assertEqual(document.source_type, KnowledgeDocument.SourceType.PASTED)
        self.assertEqual(document.owner, self.admin)
        self.assertEqual(document.version, 1)
        self.assertEqual(document.versions.count(), 1)
        self.assertTrue(document.content_sha256)
        self.assertTrue(
            AuditLog.objects.filter(action="assistant.knowledge_document_added").exists()
        )

    def test_a_public_document_cannot_be_saved_on_a_signed_in_shelf(self):
        signed = self.make_collection("Signed shelf")
        response = self.client.post(
            reverse("assistant:knowledge_document_new_paste"),
            self.paste_data(collection=signed.pk, audience="public"),
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "signed-in only")
        self.assertEqual(KnowledgeDocument.objects.count(), 0)

    def test_a_public_document_cannot_be_role_limited_by_a_crafted_post(self):
        response = self.client.post(
            reverse("assistant:knowledge_document_new_paste"),
            self.paste_data(audience="public", roles=["parent"]),
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "public document cannot be limited")
        self.assertEqual(KnowledgeDocument.objects.count(), 0)

    def test_uploading_a_pdf_stores_it_privately(self):
        response = self.client.post(
            reverse("assistant:knowledge_document_new_upload"),
            self.paste_data(file=pdf_upload()),
        )
        document = KnowledgeDocument.objects.get()
        self.assertEqual(response.status_code, 302)
        self.assertEqual(document.source_type, KnowledgeDocument.SourceType.PDF)
        self.assertTrue(document.file_sha256)
        self.assertTrue(str(document.file.path).startswith(self.media_dir))
        with self.assertRaises(ValueError):
            document.file.url

    def test_a_renamed_executable_is_refused(self):
        bad = SimpleUploadedFile("fees.pdf", b"MZ\x90\x00\x03", content_type="application/pdf")
        response = self.client.post(
            reverse("assistant:knowledge_document_new_upload"),
            self.paste_data(file=bad),
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "not a PDF")
        self.assertEqual(KnowledgeDocument.objects.count(), 0)

    def test_a_web_page_is_stored_and_a_second_copy_is_flagged(self):
        url = "https://school.example/admissions"
        data = self.paste_data(title="Admissions page", source_url=url)
        self.client.post(reverse("assistant:knowledge_document_new_url"), data)

        second = self.client.post(
            reverse("assistant:knowledge_document_new_url"),
            self.paste_data(title="Admissions page copy", source_url=url),
        )
        self.assertEqual(second.status_code, 302)
        first_doc = KnowledgeDocument.objects.get(title="Admissions page")
        copy_doc = KnowledgeDocument.objects.get(title="Admissions page copy")
        self.assertEqual(copy_doc.duplicate_of, first_doc)

    def test_a_web_page_must_be_http(self):
        response = self.client.post(
            reverse("assistant:knowledge_document_new_url"),
            self.paste_data(source_url="javascript:alert(1)"),
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(KnowledgeDocument.objects.count(), 0)

    def faq_data(self, **overrides):
        data = self.paste_data(title="Fees FAQ", body_text="")
        data.update(
            {
                "qa_pairs-TOTAL_FORMS": "4",
                "qa_pairs-INITIAL_FORMS": "0",
                "qa_pairs-MIN_NUM_FORMS": "0",
                "qa_pairs-MAX_NUM_FORMS": "1000",
                "qa_pairs-0-question": "When are fees due?",
                "qa_pairs-0-answer": "By the fifth day of each term.",
            }
        )
        data.update(overrides)
        return data

    def test_a_faq_document_saves_its_pairs(self):
        response = self.client.post(
            reverse("assistant:knowledge_document_new_faq"), self.faq_data()
        )
        document = KnowledgeDocument.objects.get()
        self.assertEqual(response.status_code, 302)
        self.assertEqual(document.source_type, KnowledgeDocument.SourceType.FAQ)
        self.assertEqual(document.qa_pairs.count(), 1)
        self.assertEqual(document.qa_pairs.get().question, "When are fees due?")

    def test_a_faq_document_needs_at_least_one_pair(self):
        data = self.faq_data()
        data["qa_pairs-0-question"] = ""
        data["qa_pairs-0-answer"] = ""
        response = self.client.post(
            reverse("assistant:knowledge_document_new_faq"), data
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "at least one question")
        self.assertEqual(KnowledgeDocument.objects.count(), 0)

    def test_the_detail_page_never_prints_a_storage_link(self):
        self.client.post(
            reverse("assistant:knowledge_document_new_upload"),
            self.paste_data(file=pdf_upload()),
        )
        document = KnowledgeDocument.objects.get()
        page = self.client.get(
            reverse("assistant:knowledge_document", args=[document.pk])
        ).content.decode()
        self.assertNotIn("/media/", page)
        self.assertNotIn(document.file.name, page)


class DocumentEditTests(KnowledgeTestCase):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.admin, backend=BACKEND)
        self.collection = self.make_collection("Fees")
        self.document = self.make_document(self.collection, title="Old title")

    def edit_data(self, **overrides):
        data = {
            "collection": self.collection.pk,
            "title": self.document.title,
            "language": "en",
            "audience": self.document.audience,
            "status": self.document.status,
            "effective_from": "",
            "effective_to": "",
            "change_note": "",
        }
        data.update(overrides)
        return data

    def test_renaming_adds_a_version(self):
        response = self.client.post(
            reverse("assistant:knowledge_document_edit", args=[self.document.pk]),
            self.edit_data(title="New title", change_note="Name changed by the school"),
        )
        self.assertRedirects(
            response, reverse("assistant:knowledge_document", args=[self.document.pk])
        )
        self.document.refresh_from_db()
        self.assertEqual(self.document.title, "New title")
        self.assertEqual(self.document.version, 2)
        self.assertEqual(self.document.versions.count(), 2)
        self.assertEqual(self.document.versions.first().note, "Name changed by the school")

    def test_editing_cannot_widen_the_audience(self):
        signed = self.make_collection("Signed shelf")
        document = self.make_document(signed, title="Signed document")
        response = self.client.post(
            reverse("assistant:knowledge_document_edit", args=[document.pk]),
            self.edit_data(
                collection=signed.pk, title="Signed document", audience="public"
            ),
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "signed-in only")
        document.refresh_from_db()
        self.assertEqual(document.audience, KnowledgeAudience.SIGNED_IN)

    def test_status_changes_are_saved_and_audited(self):
        response = self.client.post(
            reverse("assistant:knowledge_document_status", args=[self.document.pk]),
            {"status": KnowledgeStatus.ARCHIVED},
        )
        self.assertRedirects(
            response, reverse("assistant:knowledge_document", args=[self.document.pk])
        )
        self.document.refresh_from_db()
        self.assertEqual(self.document.status, KnowledgeStatus.ARCHIVED)
        self.assertTrue(
            AuditLog.objects.filter(action="assistant.knowledge_document_status").exists()
        )


class DownloadTests(KnowledgeTestCase):
    def setUp(self):
        super().setUp()
        self.collection = self.make_collection("Fees")
        self.document = self.make_document(
            self.collection,
            title="Fee policy",
            source_type=KnowledgeDocument.SourceType.PDF,
        )
        self.document.file.save("fees.pdf", ContentFile(pdf_bytes()), save=True)

    def test_an_admin_downloads_an_attachment_from_private_storage(self):
        self.client.force_login(self.admin, backend=BACKEND)
        response = self.client.get(
            reverse("assistant:knowledge_document_download", args=[self.document.pk])
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("attachment", response["Content-Disposition"])
        self.assertEqual(response["X-Content-Type-Options"], "nosniff")
        body = b"".join(response.streaming_content)
        self.assertTrue(body.startswith(b"%PDF-"))

    def test_a_teacher_cannot_download(self):
        self.client.force_login(self.teacher, backend=BACKEND)
        response = self.client.get(
            reverse("assistant:knowledge_document_download", args=[self.document.pk])
        )
        self.assertEqual(response.status_code, 403)

    def test_a_document_without_a_file_has_no_download(self):
        pasted = self.make_document(self.collection, title="Pasted only")
        self.client.force_login(self.admin, backend=BACKEND)
        response = self.client.get(
            reverse("assistant:knowledge_document_download", args=[pasted.pk])
        )
        self.assertEqual(response.status_code, 404)
