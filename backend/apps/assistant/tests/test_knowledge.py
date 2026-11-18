"""
The knowledge model, upload checks and audience scoping.

The scoping tests deliberately try to break the rule: a public document on a
signed-in shelf, a role-scoped document read by the wrong role, a class-scoped
document read by another class, drafts and expired documents.
"""

from __future__ import annotations

import datetime

from django.contrib.auth.models import AnonymousUser
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase
from django.utils import timezone

from apps.assistant.knowledge import scope
from apps.assistant.knowledge.validators import KnowledgeFileValidator
from apps.assistant.models import (
    KnowledgeAudience,
    KnowledgeDocument,
    KnowledgeStatus,
)

from .knowledge_helpers import (
    KnowledgeTestCase,
    docx_bytes,
    docx_upload,
    pdf_bytes,
    pdf_upload,
    text_upload,
)


class CollectionModelTests(KnowledgeTestCase):
    def test_slug_is_made_from_the_name(self):
        collection = self.make_collection("Parent Guide")
        self.assertEqual(collection.slug, "parent-guide")

    def test_slug_collisions_get_a_suffix(self):
        first = self.make_collection("Fees!")
        second = self.make_collection("Fees?")
        self.assertEqual(first.slug, "fees")
        self.assertEqual(second.slug, "fees-2")

    def test_a_public_collection_cannot_be_limited_to_roles(self):
        collection = self.make_collection(
            "Public info", audience=KnowledgeAudience.PUBLIC, roles=["parent"]
        )
        with self.assertRaises(ValidationError):
            collection.full_clean()

    def test_end_date_before_start_is_rejected(self):
        collection = self.make_collection(
            "Old fees",
            effective_from=datetime.date(2026, 9, 1),
            effective_to=datetime.date(2026, 8, 1),
        )
        with self.assertRaises(ValidationError):
            collection.full_clean()


class DocumentScopeTests(KnowledgeTestCase):
    def test_a_document_cannot_be_public_on_a_signed_in_shelf(self):
        collection = self.make_collection(
            "Signed shelf", audience=KnowledgeAudience.SIGNED_IN
        )
        document = self.make_document(
            collection, title="Leaky", audience=KnowledgeAudience.PUBLIC
        )
        problems = document.scope_problems()
        self.assertTrue(problems)
        self.assertIn("public", " ".join(problems).lower())

    def test_a_document_cannot_name_roles_the_shelf_excludes(self):
        collection = self.make_collection(
            "Staff shelf", audience=KnowledgeAudience.SIGNED_IN, roles=["teacher"]
        )
        document = self.make_document(collection, title="Parents", roles=["parent"])
        self.assertTrue(document.scope_problems())

    def test_a_document_must_name_roles_when_the_shelf_does(self):
        collection = self.make_collection(
            "Parents shelf", audience=KnowledgeAudience.SIGNED_IN, roles=["parent"]
        )
        document = self.make_document(collection, title="Any role", roles=[])
        self.assertTrue(document.scope_problems())

    def test_a_document_cannot_name_classes_the_shelf_excludes(self):
        collection = self.make_collection("Form 2 shelf", classes=[self.form2])
        document = self.make_document(
            collection, title="Form 3 notes", classes=[self.form3]
        )
        self.assertTrue(document.scope_problems())

    def test_a_document_must_name_classes_when_the_shelf_does(self):
        collection = self.make_collection("Form 2 only", classes=[self.form2])
        document = self.make_document(collection, title="All classes", classes=[])
        self.assertTrue(document.scope_problems())

    def test_narrower_document_on_a_public_shelf_is_allowed(self):
        collection = self.make_collection(
            "Public shelf", audience=KnowledgeAudience.PUBLIC
        )
        document = self.make_document(
            collection,
            title="Staff only",
            audience=KnowledgeAudience.SIGNED_IN,
            roles=["teacher"],
        )
        self.assertEqual(document.scope_problems(), [])

    def test_a_public_document_cannot_be_role_scoped(self):
        collection = self.make_collection(
            "Public shelf", audience=KnowledgeAudience.PUBLIC
        )
        document = self.make_document(
            collection,
            title="Public but hidden",
            audience=KnowledgeAudience.PUBLIC,
            roles=["parent"],
        )
        self.assertTrue(document.scope_problems())

    def test_full_clean_raises_for_a_broader_document(self):
        collection = self.make_collection(
            "Signed shelf", audience=KnowledgeAudience.SIGNED_IN
        )
        document = self.make_document(
            collection, title="Leaky", audience=KnowledgeAudience.PUBLIC
        )
        with self.assertRaises(ValidationError):
            document.full_clean()


class VisibilityTests(KnowledgeTestCase):
    def setUp(self):
        super().setUp()
        self.public_collection = self.make_collection(
            "Public info", audience=KnowledgeAudience.PUBLIC
        )
        self.public_doc = self.make_document(
            self.public_collection,
            title="Opening hours",
            audience=KnowledgeAudience.PUBLIC,
        )
        self.signed_collection = self.make_collection("General")
        self.signed_doc = self.make_document(self.signed_collection, title="Term dates")

        self.staff_collection = self.make_collection(
            "Staff", roles=["teacher", "admin", "director"]
        )
        self.teacher_doc = self.make_document(
            self.staff_collection, title="Marking guide", roles=["teacher"]
        )

        self.class_collection = self.make_collection("Form 2", classes=[self.form2])
        self.form2_doc = self.make_document(
            self.class_collection, title="Form 2 trips", classes=[self.form2]
        )

    def visible_ids(self, user, on_date=None):
        return set(
            scope.visible_documents(user, on_date).values_list("pk", flat=True)
        )

    def test_a_visitor_sees_only_public_documents(self):
        self.assertEqual(self.visible_ids(AnonymousUser()), {self.public_doc.pk})

    def test_a_signed_in_user_sees_public_and_unrestricted_documents(self):
        # The parent's child is in Form 2, so the Form 2 document is expected too.
        self.assertEqual(
            self.visible_ids(self.parent),
            {self.public_doc.pk, self.signed_doc.pk, self.form2_doc.pk},
        )

    def test_a_role_scoped_document_is_invisible_to_other_roles(self):
        ids = self.visible_ids(self.parent)
        self.assertNotIn(self.teacher_doc.pk, ids)
        self.assertIn(self.teacher_doc.pk, self.visible_ids(self.teacher))

    def test_a_class_scoped_document_matches_the_students_class(self):
        self.assertIn(self.form2_doc.pk, self.visible_ids(self.student2.user))
        self.assertNotIn(self.form2_doc.pk, self.visible_ids(self.student3.user))

    def test_a_parent_sees_class_documents_for_their_own_children(self):
        self.assertIn(self.form2_doc.pk, self.visible_ids(self.parent))

    def test_a_teacher_sees_class_documents_for_their_homeroom(self):
        form3_collection = self.make_collection("Form 3", classes=[self.form3])
        form3_doc = self.make_document(
            form3_collection, title="Form 3 trips", classes=[self.form3]
        )
        self.assertIn(form3_doc.pk, self.visible_ids(self.teacher))
        self.assertNotIn(form3_doc.pk, self.visible_ids(self.parent))

    def test_drafts_and_archived_documents_are_never_visible(self):
        draft = self.make_document(
            self.signed_collection, title="Draft", status=KnowledgeStatus.DRAFT
        )
        archived = self.make_document(
            self.signed_collection, title="Archived", status=KnowledgeStatus.ARCHIVED
        )
        ids = self.visible_ids(self.parent)
        self.assertNotIn(draft.pk, ids)
        self.assertNotIn(archived.pk, ids)

    def test_a_draft_or_archived_collection_hides_its_documents(self):
        draft_collection = self.make_collection(
            "Draft shelf", status=KnowledgeStatus.DRAFT
        )
        hidden = self.make_document(draft_collection, title="Inside a draft shelf")
        self.assertNotIn(hidden.pk, self.visible_ids(self.parent))

    def test_effective_dates_are_respected_including_the_boundaries(self):
        today = timezone.localdate()
        future = self.make_document(
            self.signed_collection,
            title="Next term",
            effective_from=today + datetime.timedelta(days=1),
        )
        expired = self.make_document(
            self.signed_collection,
            title="Last term",
            effective_to=today - datetime.timedelta(days=1),
        )
        starts_today = self.make_document(
            self.signed_collection, title="Starts today", effective_from=today
        )
        ids = self.visible_ids(self.parent)
        self.assertNotIn(future.pk, ids)
        self.assertNotIn(expired.pk, ids)
        self.assertIn(starts_today.pk, ids)

    def test_a_hand_edited_public_document_cannot_escape_its_shelf(self):
        # Bypasses clean() on purpose: a database edit must not widen access.
        leak = KnowledgeDocument.objects.create(
            collection=self.signed_collection,
            title="Edited in the database",
            source_type=KnowledgeDocument.SourceType.PASTED,
            body_text="Leaked wording",
            audience=KnowledgeAudience.PUBLIC,
            status=KnowledgeStatus.APPROVED,
        )
        self.assertNotIn(leak.pk, self.visible_ids(AnonymousUser()))
        self.assertNotIn(leak.pk, self.visible_ids(self.parent))

    def test_a_public_document_cannot_be_read_through_a_visitor_shortcut(self):
        self.assertNotIn(self.signed_doc.pk, self.visible_ids(AnonymousUser()))


class UploadValidatorTests(SimpleTestCase):
    def test_a_real_pdf_passes(self):
        KnowledgeFileValidator()(pdf_upload())

    def test_a_real_docx_passes(self):
        KnowledgeFileValidator()(docx_upload())

    def test_plain_text_passes(self):
        KnowledgeFileValidator()(text_upload())

    def test_a_renamed_executable_is_rejected(self):
        fake = SimpleUploadedFile("fees.pdf", b"MZ\x90\x00\x03", content_type="application/pdf")
        with self.assertRaises(ValidationError):
            KnowledgeFileValidator()(fake)

    def test_bytes_must_match_the_extension(self):
        with self.assertRaises(ValidationError):
            KnowledgeFileValidator()(
                SimpleUploadedFile("notes.txt", pdf_bytes(), content_type="text/plain")
            )

    def test_a_zip_that_is_not_a_word_document_is_rejected(self):
        import io
        import zipfile

        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("other.xml", "<x/>")
        with self.assertRaises(ValidationError):
            KnowledgeFileValidator()(
                SimpleUploadedFile("policy.docx", buffer.getvalue())
            )

    def test_an_unknown_extension_is_rejected(self):
        with self.assertRaises(ValidationError):
            KnowledgeFileValidator()(
                SimpleUploadedFile("fees.exe", b"MZ\x90\x00", content_type="application/octet-stream")
            )

    def test_a_file_with_binary_bytes_is_not_text(self):
        with self.assertRaises(ValidationError):
            KnowledgeFileValidator()(
                SimpleUploadedFile("notes.txt", b"hello\x00world", content_type="text/plain")
            )

    def test_the_size_cap_is_enforced(self):
        small_cap = KnowledgeFileValidator(max_mb=0.001)
        with self.assertRaises(ValidationError):
            small_cap(SimpleUploadedFile("notes.txt", b"x" * 2000, content_type="text/plain"))
