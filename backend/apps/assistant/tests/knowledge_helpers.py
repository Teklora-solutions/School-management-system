"""Shared fixtures for the knowledge-base tests. Not a test module."""

from __future__ import annotations

import io
import shutil
import tempfile
import zipfile

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings

from apps.accounts.models import ParentProfile
from apps.assistant.knowledge.roles import roles_key
from apps.assistant.models import (
    KnowledgeAudience,
    KnowledgeCollection,
    KnowledgeDocument,
    KnowledgeDocumentVersion,
    KnowledgeStatus,
)
from apps.dashboards.tests.fixtures import (
    make_class,
    make_student_user,
    make_user,
    make_year,
)
from apps.students.models import StudentGuardian


def pdf_bytes() -> bytes:
    return b"%PDF-1.4\n1 0 obj\n<< /Type /Catalog >>\nendobj\ntrailer\n<<>>\n%%EOF\n"


def docx_bytes() -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("word/document.xml", "<w:document/>")
        archive.writestr("[Content_Types].xml", "<Types/>")
    return buffer.getvalue()


def pdf_upload(name: str = "fees.pdf") -> SimpleUploadedFile:
    return SimpleUploadedFile(name, pdf_bytes(), content_type="application/pdf")


def docx_upload(name: str = "policy.docx") -> SimpleUploadedFile:
    return SimpleUploadedFile(
        name,
        docx_bytes(),
        content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )


def text_upload(name: str = "notes.txt", text: str = "Fees are due each term.") -> SimpleUploadedFile:
    return SimpleUploadedFile(name, text.encode("utf-8"), content_type="text/plain")


class KnowledgeTestCase(TestCase):
    """Academic year, two classes, one user per role, private media per test."""

    @classmethod
    def setUpTestData(cls):
        cls.year = make_year(name="2026/2027")
        cls.form2 = make_class(cls.year, name="Form 2", level_order=2)
        cls.form3 = make_class(cls.year, name="Form 3", level_order=3)
        cls.admin = make_user("admin", username="kb_admin")
        cls.teacher = make_user("teacher", username="kb_teacher")
        cls.parent = make_user("parent", username="kb_parent")
        cls.student2 = make_student_user(cls.form2, admission_number="KB-002")
        cls.student3 = make_student_user(cls.form3, admission_number="KB-003")
        cls.form3.class_teacher = cls.teacher
        cls.form3.save(update_fields=["class_teacher"])
        cls.parent_profile = ParentProfile.objects.create(user=cls.parent)
        StudentGuardian.objects.create(
            student=cls.student2, parent=cls.parent_profile
        )

    def setUp(self):
        self.media_dir = tempfile.mkdtemp(prefix="knowledge-")
        self.settings_override = override_settings(KNOWLEDGE_MEDIA_ROOT=self.media_dir)
        self.settings_override.enable()
        self.addCleanup(self.settings_override.disable)
        self.addCleanup(shutil.rmtree, self.media_dir, True)

    def make_collection(self, name: str = "Fees", classes=(), **kwargs) -> KnowledgeCollection:
        data = {
            "audience": KnowledgeAudience.SIGNED_IN,
            "status": KnowledgeStatus.APPROVED,
        }
        data.update(kwargs)
        if isinstance(data.get("roles"), (list, tuple, set)):
            data["roles"] = roles_key(data["roles"])
        collection = KnowledgeCollection.objects.create(name=name, **data)
        if classes:
            collection.classes.set(classes)
        return collection

    def make_document(self, collection, title: str = "Fee policy", classes=(), **kwargs):
        data = {
            "source_type": KnowledgeDocument.SourceType.PASTED,
            "body_text": f"{title} wording.",
            "audience": KnowledgeAudience.SIGNED_IN,
            "status": KnowledgeStatus.APPROVED,
        }
        data.update(kwargs)
        if isinstance(data.get("roles"), (list, tuple, set)):
            data["roles"] = roles_key(data["roles"])
        document = KnowledgeDocument.objects.create(
            collection=collection, title=title, **data
        )
        if classes:
            document.classes.set(classes)
        # Real documents always start with a version row; fixtures should too.
        KnowledgeDocumentVersion.objects.create(
            document=document, version=1, title=title, content=data.get("body_text", "")
        )
        return document
