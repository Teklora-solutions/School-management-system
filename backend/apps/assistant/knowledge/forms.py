"""
Admin forms for the knowledge base.

Every form that can change an audience calls ``scope.scope_problems`` so a
document can never be saved broader than its collection, even by a crafted
POST.
"""

from __future__ import annotations

from urllib.parse import urlsplit

from django import forms
from django.forms import inlineformset_factory

from apps.academics.models import SchoolClass
from apps.accounts.models import User

from ..models import (
    KnowledgeAudience,
    KnowledgeCollection,
    KnowledgeDocument,
    KnowledgeQAPair,
)
from . import scope
from .roles import roles_key, roles_list
from .validators import validate_knowledge_file

TEXT = forms.TextInput(attrs={"class": "form-control"})
SELECT = forms.Select(attrs={"class": "form-select"})
DATE = forms.DateInput(attrs={"class": "form-control", "type": "date"})
ROLE_CHOICES = User.Role.choices


def class_queryset():
    return SchoolClass.objects.select_related("academic_year").order_by(
        "-academic_year__start_date", "level_order", "name"
    )


class KnowledgeCollectionForm(forms.ModelForm):
    roles = forms.MultipleChoiceField(
        required=False,
        choices=ROLE_CHOICES,
        label="Signed-in roles",
        help_text="Leave empty to allow every signed-in role.",
        widget=forms.CheckboxSelectMultiple(),
    )
    classes = forms.ModelMultipleChoiceField(
        required=False,
        queryset=SchoolClass.objects.none(),
        label="Classes",
        help_text="Leave empty to allow every class.",
        widget=forms.SelectMultiple(attrs={"class": "form-select", "size": 8}),
    )

    class Meta:
        model = KnowledgeCollection
        fields = (
            "name",
            "slug",
            "description",
            "audience",
            "status",
            "effective_from",
            "effective_to",
        )
        widgets = {
            "name": TEXT,
            "slug": TEXT,
            "description": forms.Textarea(attrs={"class": "form-control", "rows": 3}),
            "audience": SELECT,
            "status": SELECT,
            "effective_from": DATE,
            "effective_to": DATE,
        }
        labels = {
            "slug": "Short name for the address (optional)",
            "audience": "Widest audience for this collection",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["classes"].queryset = class_queryset()
        if self.instance.pk:
            self.fields["roles"].initial = roles_list(self.instance.roles)
            self.fields["classes"].initial = self.instance.classes.all()

    def clean_roles(self):
        return roles_key(self.cleaned_data.get("roles"))

    def clean(self):
        cleaned = super().clean()
        audience = cleaned.get("audience")
        roles = cleaned.get("roles")
        classes = cleaned.get("classes")
        if audience == KnowledgeAudience.PUBLIC and roles:
            self.add_error("roles", "A public collection cannot be limited to roles.")
        if audience == KnowledgeAudience.PUBLIC and classes:
            self.add_error("classes", "A public collection cannot be limited to classes.")
        return cleaned


class KnowledgeDocumentForm(forms.ModelForm):
    """Metadata shared by every way of adding a document."""

    roles = forms.MultipleChoiceField(
        required=False,
        choices=ROLE_CHOICES,
        label="Signed-in roles",
        help_text="Leave empty to allow every signed-in role.",
        widget=forms.CheckboxSelectMultiple(),
    )
    classes = forms.ModelMultipleChoiceField(
        required=False,
        queryset=SchoolClass.objects.none(),
        label="Classes",
        help_text="Leave empty to allow every class.",
        widget=forms.SelectMultiple(attrs={"class": "form-select", "size": 8}),
    )

    class Meta:
        model = KnowledgeDocument
        fields = (
            "collection",
            "title",
            "language",
            "audience",
            "status",
            "effective_from",
            "effective_to",
        )
        widgets = {
            "collection": SELECT,
            "title": TEXT,
            "language": SELECT,
            "audience": SELECT,
            "status": SELECT,
            "effective_from": DATE,
            "effective_to": DATE,
        }
        labels = {
            "audience": "Who may read this document",
            "status": "Status",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["collection"].queryset = KnowledgeCollection.objects.order_by("name")
        self.fields["classes"].queryset = class_queryset()
        if self.instance.pk:
            self.fields["roles"].initial = roles_list(self.instance.roles)
            self.fields["classes"].initial = self.instance.classes.all()

    def clean_roles(self):
        return roles_key(self.cleaned_data.get("roles"))

    def clean(self):
        cleaned = super().clean()
        collection = cleaned.get("collection")
        audience = cleaned.get("audience")
        roles = cleaned.get("roles")
        classes = cleaned.get("classes")
        if not collection or not audience:
            return cleaned
        class_ids = [pk for pk in classes.values_list("pk", flat=True)] if classes else []
        problems = scope.scope_problems(
            audience=audience,
            roles=roles,
            class_ids=class_ids,
            parent_audience=collection.audience,
            parent_roles=collection.roles,
            parent_class_ids=list(
                collection.classes.values_list("pk", flat=True)
            ),
        )
        for problem in problems:
            if "role" in problem.lower():
                self.add_error("roles", problem)
            elif "class" in problem.lower():
                self.add_error("classes", problem)
            else:
                self.add_error("audience", problem)
        return cleaned


class KnowledgeUploadForm(KnowledgeDocumentForm):
    file = forms.FileField(
        label="Document file",
        validators=validate_knowledge_file,
        widget=forms.FileInput(
            attrs={"class": "form-control", "accept": ".pdf,.docx,.txt,.md"}
        ),
        help_text="PDF, Word (.docx), plain text or Markdown, up to 20 MB.",
    )

    class Meta(KnowledgeDocumentForm.Meta):
        fields = KnowledgeDocumentForm.Meta.fields + ("file",)


class KnowledgeUrlForm(KnowledgeDocumentForm):
    source_url = forms.URLField(
        label="Web page address",
        max_length=500,
        widget=forms.URLInput(attrs={"class": "form-control", "placeholder": "https://"}),
        help_text="The page is fetched when indexing arrives (K2). Nothing is fetched now.",
    )

    class Meta(KnowledgeDocumentForm.Meta):
        fields = KnowledgeDocumentForm.Meta.fields + ("source_url",)

    def clean_source_url(self):
        value = (self.cleaned_data.get("source_url") or "").strip()
        parts = urlsplit(value)
        if parts.scheme not in ("http", "https"):
            raise forms.ValidationError("Use an http:// or https:// address.")
        if parts.username or parts.password:
            raise forms.ValidationError("Remove the username and password from the address.")
        return value


class KnowledgePasteForm(KnowledgeDocumentForm):
    body_text = forms.CharField(
        label="Text",
        min_length=20,
        strip=True,
        widget=forms.Textarea(attrs={"class": "form-control", "rows": 14}),
        help_text="Paste the wording exactly as it should be answerable.",
    )

    class Meta(KnowledgeDocumentForm.Meta):
        fields = KnowledgeDocumentForm.Meta.fields + ("body_text",)


class KnowledgeFAQForm(KnowledgeDocumentForm):
    body_text = forms.CharField(
        label="Introduction (optional)",
        required=False,
        strip=True,
        widget=forms.Textarea(attrs={"class": "form-control", "rows": 3}),
        help_text="A short note above the questions, if one is needed.",
    )

    class Meta(KnowledgeDocumentForm.Meta):
        fields = KnowledgeDocumentForm.Meta.fields + ("body_text",)


class KnowledgeDocumentEditForm(KnowledgeDocumentForm):
    change_note = forms.CharField(
        label="What changed (optional)",
        required=False,
        max_length=200,
        widget=TEXT,
        help_text="Saved in the version history when you change the title.",
    )

    class Meta(KnowledgeDocumentForm.Meta):
        fields = KnowledgeDocumentForm.Meta.fields


class KnowledgeQAPairForm(forms.ModelForm):
    class Meta:
        model = KnowledgeQAPair
        fields = ("question", "answer")
        widgets = {
            "question": forms.TextInput(attrs={"class": "form-control"}),
            "answer": forms.Textarea(attrs={"class": "form-control", "rows": 3}),
        }


KnowledgeQAPairFormSet = inlineformset_factory(
    KnowledgeDocument,
    KnowledgeQAPair,
    form=KnowledgeQAPairForm,
    extra=4,
    can_delete=True,
)


class KnowledgeStatusForm(forms.Form):
    status = forms.ChoiceField(
        choices=KnowledgeDocument._meta.get_field("status").choices,
        widget=SELECT,
        label="Status",
    )
