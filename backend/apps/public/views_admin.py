"""
apps/public/views_admin.py
--------------------------
The school's own Website pages, inside the Admin Portal.

Everything here is admin-only: publishing a photograph and reading what strangers
sent the school are both acts of editing what the public sees, and the audit
trail names who did them. The five anonymous pages live in ``views.py``; this
file is the door on the other side of them.

The destructive actions are POST-only views — a ``GET`` returns 405 — which is
how the rest of the project keeps a crawler or a pre-fetch from deleting a row.
"""

from __future__ import annotations

from urllib.parse import urlencode

from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.generic import ListView, TemplateView, View

from apps.core.mixins import AdminRequiredMixin

from . import selectors, services
from .forms import EnquiryFilterForm, GalleryPhotoForm
from .models import ContactMessage, GalleryPhoto


class WebsiteAdminMixin(AdminRequiredMixin):
    """
    What every page in this file shares: admin-only, plus a one-line way to
    render a page that is built by hand instead of through a generic.

    The sidebar needs no extra marker: the context processor already marks a
    parent entry active when one of its children is, so both Website pages light
    the group on their own.
    """

    def render(self, request, context=None):
        """For the views that render by hand instead of through a generic."""
        return render(request, self.template_name, context or {})


class GalleryListView(WebsiteAdminMixin, ListView):
    template_name = "features/public/gallery_list.html"
    context_object_name = "photos"
    paginate_by = 24

    def get_queryset(self):
        return selectors.photo_for_management()


class GalleryPhotoFormMixin(WebsiteAdminMixin):
    """
    One form, used for both adding a photograph and editing one.

    ``request.FILES`` is what carries the image, and the service — not the form
    — decides that a submission which posted nothing for the file box is leaving
    the current photograph alone.
    """

    template_name = "features/public/gallery_form.html"

    def get_form(self, instance=None):
        return GalleryPhotoForm(
            self.request.POST or None,
            self.request.FILES or None,
            instance=instance,
        )


class GalleryPhotoCreateView(GalleryPhotoFormMixin, View):
    def get(self, request):
        return self.render(request, {"form": GalleryPhotoForm()})

    def post(self, request):
        form = self.get_form()
        if form.is_valid():
            photo = services.save_gallery_photo(
                instance=form.instance,
                cleaned_data=form.cleaned_data,
                actor=request.user,
                request=request,
            )
            messages.success(request, f"Added “{photo.title}” to the gallery.")
            return redirect("public_admin:gallery_list")
        return self.render(request, {"form": form})


class GalleryPhotoEditView(GalleryPhotoFormMixin, View):
    def get(self, request, pk):
        photo = get_object_or_404(GalleryPhoto, pk=pk)
        return self.render(request, {"form": GalleryPhotoForm(instance=photo), "photo": photo})

    def post(self, request, pk):
        photo = get_object_or_404(GalleryPhoto, pk=pk)
        form = self.get_form(instance=photo)
        if form.is_valid():
            services.save_gallery_photo(
                instance=form.instance,
                cleaned_data=form.cleaned_data,
                actor=request.user,
                request=request,
            )
            messages.success(request, f"Saved “{form.instance.title}”.")
            return redirect("public_admin:gallery_list")
        return self.render(request, {"form": form, "photo": photo})


class GalleryPhotoPublishView(WebsiteAdminMixin, View):
    """
    Publish or hide one photograph straight from the list, with no form.

    This goes through the same service as the edit page, so the audit row reads
    ``public.gallery_photo_updated`` either way: a toggle is an edit, and
    inventing a second action word for it would split one event across two
    names in the log.
    """

    def post(self, request, pk):
        photo = get_object_or_404(GalleryPhoto, pk=pk)
        publish = request.POST.get("action") == "publish"
        services.save_gallery_photo(
            instance=photo,
            cleaned_data={"is_published": publish},
            actor=request.user,
            request=request,
        )
        messages.success(
            request,
            f"“{photo.title}” is {'live on the website' if publish else 'hidden from the website'}.",
        )
        return redirect(request.POST.get("next") or "public_admin:gallery_list")


class GalleryPhotoDeleteView(WebsiteAdminMixin, View):
    def post(self, request, pk):
        photo = get_object_or_404(GalleryPhoto, pk=pk)
        title = photo.title
        services.delete_gallery_photo(
            instance=photo, actor=request.user, request=request
        )
        messages.success(request, f"Deleted “{title}”.")
        return redirect(request.POST.get("next") or "public_admin:gallery_list")


class EnquiryListView(WebsiteAdminMixin, ListView):
    template_name = "features/public/enquiry_list.html"
    context_object_name = "enquiries"
    paginate_by = 30

    def get_filters(self) -> dict:
        """The saved filters — invalid input falls back to the defaults."""
        form = self.get_form()
        return {"status": "all", "q": ""} if not form.is_valid() else form.cleaned_data

    def get_form(self) -> EnquiryFilterForm:
        return EnquiryFilterForm(self.request.GET or None)

    def get_queryset(self):
        return selectors.enquiry_list(**self.get_filters())

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["filter_form"] = self.get_form()
        ctx["counts"] = selectors.enquiry_counts()
        # Pagination links carry the filters forward. Without this, clicking
        # page two of "still open" silently shows the unfiltered list, and an
        # admin reads rows they did not ask for.
        params = {key: value for key, value in self.request.GET.items() if key != "page"}
        ctx["filter_params"] = f"{urlencode(params)}&" if params else ""
        return ctx


class EnquiryDetailView(WebsiteAdminMixin, TemplateView):
    template_name = "features/public/enquiry_detail.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["enquiry"] = get_object_or_404(ContactMessage, pk=kwargs["pk"])
        return ctx


class EnquiryHandledView(WebsiteAdminMixin, View):
    """Tick or untick the handled mark — the enquiry's only workflow."""

    def post(self, request, pk):
        enquiry = get_object_or_404(ContactMessage, pk=pk)
        handled = request.POST.get("action") == "handled"
        services.set_enquiry_handled(
            instance=enquiry, handled=handled, actor=request.user, request=request
        )
        messages.success(request, "Marked as handled." if handled else "Reopened.")
        return redirect(request.POST.get("next") or "public_admin:enquiry_list")


class EnquiryDeleteView(WebsiteAdminMixin, View):
    def post(self, request, pk):
        enquiry = get_object_or_404(ContactMessage, pk=pk)
        name = enquiry.name
        services.delete_enquiry(instance=enquiry, actor=request.user, request=request)
        messages.success(request, f"Deleted the enquiry from {name}.")
        return redirect(request.POST.get("next") or "public_admin:enquiry_list")


# ── Generic content-item view helpers ────────────────────────────────────────
# All nine new content models share exactly the same CRUD pattern:
#   list → add → edit → toggle-active → delete
# One pair of base classes handles all of them.

from . import services as _svc
from .forms import (
    AdmissionStepForm,
    EventItemForm,
    FAQForm,
    FeatureCardForm,
    HeroSlideForm,
    KeyDateForm,
    TeamMemberForm,
    TimelineEventForm,
    TestimonialForm,
)
from .models import (
    AdmissionStep,
    EventItem,
    FAQ,
    FeatureCard,
    HeroSlide,
    KeyDate,
    TeamMember,
    TimelineEvent,
    Testimonial,
)


class ContentListView(WebsiteAdminMixin, ListView):
    """Generic list for any ordered-content model."""
    paginate_by = 30
    context_object_name = "items"

    def get_queryset(self):
        return self.model.objects.all()


class ContentFormMixin(WebsiteAdminMixin):
    """Shared logic for create/edit views of content-model items."""
    form_class = None   # set on each concrete subclass
    list_url_name = ""  # e.g. "public_admin:hero_list"

    def get_form(self, instance=None):
        return self.form_class(
            self.request.POST or None,
            self.request.FILES or None,
            instance=instance,
        )

    def _save_and_redirect(self, form, success_msg):
        _svc.save_content_item(
            instance=form.instance,
            cleaned_data=form.cleaned_data,
            actor=self.request.user,
            request=self.request,
        )
        messages.success(self.request, success_msg)
        return redirect(self.list_url_name)


class ContentCreateView(ContentFormMixin, View):
    def get(self, request):
        return self.render(request, {"form": self.form_class()})

    def post(self, request):
        form = self.get_form()
        if form.is_valid():
            return self._save_and_redirect(form, "Item added.")
        return self.render(request, {"form": form})


class ContentEditView(ContentFormMixin, View):
    model = None

    def _get_item(self, pk):
        return get_object_or_404(self.model, pk=pk)

    def get(self, request, pk):
        item = self._get_item(pk)
        return self.render(request, {"form": self.form_class(instance=item), "item": item})

    def post(self, request, pk):
        item = self._get_item(pk)
        form = self.get_form(instance=item)
        if form.is_valid():
            return self._save_and_redirect(form, "Saved.")
        return self.render(request, {"form": form, "item": item})


class ContentToggleView(WebsiteAdminMixin, View):
    """POST-only: flip the is_active flag of a content item."""
    model = None
    list_url_name = ""

    def post(self, request, pk):
        item = get_object_or_404(self.model, pk=pk)
        activate = request.POST.get("action") == "activate"
        _svc.save_content_item(
            instance=item,
            cleaned_data={"is_active": activate},
            actor=request.user,
            request=request,
        )
        label = "shown on the website" if activate else "hidden from the website"
        messages.success(request, f'"{item}" is now {label}.')
        return redirect(request.POST.get("next") or self.list_url_name)


class ContentDeleteView(WebsiteAdminMixin, View):
    """POST-only: delete a content item."""
    model = None
    list_url_name = ""

    def post(self, request, pk):
        item = get_object_or_404(self.model, pk=pk)
        name = str(item)
        _svc.delete_content_item(instance=item, actor=request.user, request=request)
        messages.success(request, f'Deleted "{name}".')
        return redirect(request.POST.get("next") or self.list_url_name)


# ── Hero Slides ───────────────────────────────────────────────────────────────

class HeroSlideListView(ContentListView):
    model = HeroSlide
    template_name = "features/public/hero_slide_list.html"


class HeroSlideCreateView(ContentCreateView):
    form_class = HeroSlideForm
    template_name = "features/public/content_form.html"
    list_url_name = "public_admin:hero_list"

    def get(self, request):
        return self.render(request, {
            "form": self.form_class(),
            "title": "Add hero slide",
            "back_url": reverse(self.list_url_name),
        })

    def post(self, request):
        form = self.get_form()
        if form.is_valid():
            return self._save_and_redirect(form, "Hero slide added.")
        return self.render(request, {"form": form, "title": "Add hero slide", "back_url": reverse(self.list_url_name)})


class HeroSlideEditView(ContentEditView):
    model = HeroSlide
    form_class = HeroSlideForm
    template_name = "features/public/content_form.html"
    list_url_name = "public_admin:hero_list"

    def get(self, request, pk):
        item = self._get_item(pk)
        return self.render(request, {"form": self.form_class(instance=item), "item": item, "title": f"Edit: {item}", "back_url": reverse(self.list_url_name)})

    def post(self, request, pk):
        item = self._get_item(pk)
        form = self.get_form(instance=item)
        if form.is_valid():
            return self._save_and_redirect(form, f'Saved "{form.instance}".')
        return self.render(request, {"form": form, "item": item, "title": f"Edit: {item}", "back_url": reverse(self.list_url_name)})


class HeroSlideToggleView(ContentToggleView):
    model = HeroSlide
    list_url_name = "public_admin:hero_list"


class HeroSlideDeleteView(ContentDeleteView):
    model = HeroSlide
    list_url_name = "public_admin:hero_list"


# ── Feature Cards ─────────────────────────────────────────────────────────────

class FeatureCardListView(ContentListView):
    model = FeatureCard
    template_name = "features/public/feature_card_list.html"


class FeatureCardCreateView(ContentCreateView):
    form_class = FeatureCardForm
    template_name = "features/public/content_form.html"
    list_url_name = "public_admin:feature_list"

    def get(self, request):
        return self.render(request, {"form": self.form_class(), "title": "Add feature card", "back_url": reverse(self.list_url_name)})

    def post(self, request):
        form = self.get_form()
        if form.is_valid():
            return self._save_and_redirect(form, "Feature card added.")
        return self.render(request, {"form": form, "title": "Add feature card", "back_url": reverse(self.list_url_name)})


class FeatureCardEditView(ContentEditView):
    model = FeatureCard
    form_class = FeatureCardForm
    template_name = "features/public/content_form.html"
    list_url_name = "public_admin:feature_list"

    def get(self, request, pk):
        item = self._get_item(pk)
        return self.render(request, {"form": self.form_class(instance=item), "item": item, "title": f"Edit: {item}", "back_url": reverse(self.list_url_name)})

    def post(self, request, pk):
        item = self._get_item(pk)
        form = self.get_form(instance=item)
        if form.is_valid():
            return self._save_and_redirect(form, "Saved.")
        return self.render(request, {"form": form, "item": item, "title": f"Edit: {item}", "back_url": reverse(self.list_url_name)})


class FeatureCardToggleView(ContentToggleView):
    model = FeatureCard
    list_url_name = "public_admin:feature_list"


class FeatureCardDeleteView(ContentDeleteView):
    model = FeatureCard
    list_url_name = "public_admin:feature_list"


# ── Testimonials ──────────────────────────────────────────────────────────────

class TestimonialListView(ContentListView):
    model = Testimonial
    template_name = "features/public/testimonial_list.html"


class TestimonialCreateView(ContentCreateView):
    form_class = TestimonialForm
    template_name = "features/public/content_form.html"
    list_url_name = "public_admin:testimonial_list"

    def get(self, request):
        return self.render(request, {"form": self.form_class(), "title": "Add testimonial", "back_url": reverse(self.list_url_name)})

    def post(self, request):
        form = self.get_form()
        if form.is_valid():
            return self._save_and_redirect(form, "Testimonial added.")
        return self.render(request, {"form": form, "title": "Add testimonial", "back_url": reverse(self.list_url_name)})


class TestimonialEditView(ContentEditView):
    model = Testimonial
    form_class = TestimonialForm
    template_name = "features/public/content_form.html"
    list_url_name = "public_admin:testimonial_list"

    def get(self, request, pk):
        item = self._get_item(pk)
        return self.render(request, {"form": self.form_class(instance=item), "item": item, "title": f"Edit testimonial", "back_url": reverse(self.list_url_name)})

    def post(self, request, pk):
        item = self._get_item(pk)
        form = self.get_form(instance=item)
        if form.is_valid():
            return self._save_and_redirect(form, "Saved.")
        return self.render(request, {"form": form, "item": item, "title": "Edit testimonial", "back_url": reverse(self.list_url_name)})


class TestimonialToggleView(ContentToggleView):
    model = Testimonial
    list_url_name = "public_admin:testimonial_list"


class TestimonialDeleteView(ContentDeleteView):
    model = Testimonial
    list_url_name = "public_admin:testimonial_list"


# ── Team Members ──────────────────────────────────────────────────────────────

class TeamMemberListView(ContentListView):
    model = TeamMember
    template_name = "features/public/team_member_list.html"


class TeamMemberCreateView(ContentCreateView):
    form_class = TeamMemberForm
    template_name = "features/public/content_form.html"
    list_url_name = "public_admin:team_list"

    def get(self, request):
        return self.render(request, {"form": self.form_class(), "title": "Add team member", "back_url": reverse(self.list_url_name)})

    def post(self, request):
        form = self.get_form()
        if form.is_valid():
            return self._save_and_redirect(form, "Team member added.")
        return self.render(request, {"form": form, "title": "Add team member", "back_url": reverse(self.list_url_name)})


class TeamMemberEditView(ContentEditView):
    model = TeamMember
    form_class = TeamMemberForm
    template_name = "features/public/content_form.html"
    list_url_name = "public_admin:team_list"

    def get(self, request, pk):
        item = self._get_item(pk)
        return self.render(request, {"form": self.form_class(instance=item), "item": item, "title": f"Edit: {item.name}", "back_url": reverse(self.list_url_name)})

    def post(self, request, pk):
        item = self._get_item(pk)
        form = self.get_form(instance=item)
        if form.is_valid():
            return self._save_and_redirect(form, "Saved.")
        return self.render(request, {"form": form, "item": item, "title": f"Edit: {item.name}", "back_url": reverse(self.list_url_name)})


class TeamMemberToggleView(ContentToggleView):
    model = TeamMember
    list_url_name = "public_admin:team_list"


class TeamMemberDeleteView(ContentDeleteView):
    model = TeamMember
    list_url_name = "public_admin:team_list"


# ── Timeline Events ───────────────────────────────────────────────────────────

class TimelineEventListView(ContentListView):
    model = TimelineEvent
    template_name = "features/public/timeline_event_list.html"


class TimelineEventCreateView(ContentCreateView):
    form_class = TimelineEventForm
    template_name = "features/public/content_form.html"
    list_url_name = "public_admin:timeline_list"

    def get(self, request):
        return self.render(request, {"form": self.form_class(), "title": "Add timeline event", "back_url": reverse(self.list_url_name)})

    def post(self, request):
        form = self.get_form()
        if form.is_valid():
            return self._save_and_redirect(form, "Timeline event added.")
        return self.render(request, {"form": form, "title": "Add timeline event", "back_url": reverse(self.list_url_name)})


class TimelineEventEditView(ContentEditView):
    model = TimelineEvent
    form_class = TimelineEventForm
    template_name = "features/public/content_form.html"
    list_url_name = "public_admin:timeline_list"

    def get(self, request, pk):
        item = self._get_item(pk)
        return self.render(request, {"form": self.form_class(instance=item), "item": item, "title": f"Edit: {item}", "back_url": reverse(self.list_url_name)})

    def post(self, request, pk):
        item = self._get_item(pk)
        form = self.get_form(instance=item)
        if form.is_valid():
            return self._save_and_redirect(form, "Saved.")
        return self.render(request, {"form": form, "item": item, "title": f"Edit: {item}", "back_url": reverse(self.list_url_name)})


class TimelineEventToggleView(ContentToggleView):
    model = TimelineEvent
    list_url_name = "public_admin:timeline_list"


class TimelineEventDeleteView(ContentDeleteView):
    model = TimelineEvent
    list_url_name = "public_admin:timeline_list"


# ── Event Items ───────────────────────────────────────────────────────────────

class EventItemListView(ContentListView):
    model = EventItem
    template_name = "features/public/event_item_list.html"


class EventItemCreateView(ContentCreateView):
    form_class = EventItemForm
    template_name = "features/public/content_form.html"
    list_url_name = "public_admin:event_list"

    def get(self, request):
        return self.render(request, {"form": self.form_class(), "title": "Add event", "back_url": reverse(self.list_url_name)})

    def post(self, request):
        form = self.get_form()
        if form.is_valid():
            return self._save_and_redirect(form, "Event added.")
        return self.render(request, {"form": form, "title": "Add event", "back_url": reverse(self.list_url_name)})


class EventItemEditView(ContentEditView):
    model = EventItem
    form_class = EventItemForm
    template_name = "features/public/content_form.html"
    list_url_name = "public_admin:event_list"

    def get(self, request, pk):
        item = self._get_item(pk)
        return self.render(request, {"form": self.form_class(instance=item), "item": item, "title": f"Edit: {item}", "back_url": reverse(self.list_url_name)})

    def post(self, request, pk):
        item = self._get_item(pk)
        form = self.get_form(instance=item)
        if form.is_valid():
            return self._save_and_redirect(form, "Saved.")
        return self.render(request, {"form": form, "item": item, "title": f"Edit: {item}", "back_url": reverse(self.list_url_name)})


class EventItemToggleView(ContentToggleView):
    model = EventItem
    list_url_name = "public_admin:event_list"


class EventItemDeleteView(ContentDeleteView):
    model = EventItem
    list_url_name = "public_admin:event_list"


# ── FAQs ──────────────────────────────────────────────────────────────────────

class FAQListView(ContentListView):
    model = FAQ
    template_name = "features/public/faq_list.html"


class FAQCreateView(ContentCreateView):
    form_class = FAQForm
    template_name = "features/public/content_form.html"
    list_url_name = "public_admin:faq_list"

    def get(self, request):
        return self.render(request, {"form": self.form_class(), "title": "Add FAQ", "back_url": reverse(self.list_url_name)})

    def post(self, request):
        form = self.get_form()
        if form.is_valid():
            return self._save_and_redirect(form, "FAQ added.")
        return self.render(request, {"form": form, "title": "Add FAQ", "back_url": reverse(self.list_url_name)})


class FAQEditView(ContentEditView):
    model = FAQ
    form_class = FAQForm
    template_name = "features/public/content_form.html"
    list_url_name = "public_admin:faq_list"

    def get(self, request, pk):
        item = self._get_item(pk)
        return self.render(request, {"form": self.form_class(instance=item), "item": item, "title": "Edit FAQ", "back_url": reverse(self.list_url_name)})

    def post(self, request, pk):
        item = self._get_item(pk)
        form = self.get_form(instance=item)
        if form.is_valid():
            return self._save_and_redirect(form, "Saved.")
        return self.render(request, {"form": form, "item": item, "title": "Edit FAQ", "back_url": reverse(self.list_url_name)})


class FAQToggleView(ContentToggleView):
    model = FAQ
    list_url_name = "public_admin:faq_list"


class FAQDeleteView(ContentDeleteView):
    model = FAQ
    list_url_name = "public_admin:faq_list"


# ── Admission Steps ───────────────────────────────────────────────────────────

class AdmissionStepListView(ContentListView):
    model = AdmissionStep
    template_name = "features/public/admission_step_list.html"


class AdmissionStepCreateView(ContentCreateView):
    form_class = AdmissionStepForm
    template_name = "features/public/content_form.html"
    list_url_name = "public_admin:admission_list"

    def get(self, request):
        return self.render(request, {"form": self.form_class(), "title": "Add admission step", "back_url": reverse(self.list_url_name)})

    def post(self, request):
        form = self.get_form()
        if form.is_valid():
            return self._save_and_redirect(form, "Admission step added.")
        return self.render(request, {"form": form, "title": "Add admission step", "back_url": reverse(self.list_url_name)})


class AdmissionStepEditView(ContentEditView):
    model = AdmissionStep
    form_class = AdmissionStepForm
    template_name = "features/public/content_form.html"
    list_url_name = "public_admin:admission_list"

    def get(self, request, pk):
        item = self._get_item(pk)
        return self.render(request, {"form": self.form_class(instance=item), "item": item, "title": f"Edit: {item}", "back_url": reverse(self.list_url_name)})

    def post(self, request, pk):
        item = self._get_item(pk)
        form = self.get_form(instance=item)
        if form.is_valid():
            return self._save_and_redirect(form, "Saved.")
        return self.render(request, {"form": form, "item": item, "title": f"Edit: {item}", "back_url": reverse(self.list_url_name)})


class AdmissionStepToggleView(ContentToggleView):
    model = AdmissionStep
    list_url_name = "public_admin:admission_list"


class AdmissionStepDeleteView(ContentDeleteView):
    model = AdmissionStep
    list_url_name = "public_admin:admission_list"


# ── Key Dates ─────────────────────────────────────────────────────────────────

class KeyDateListView(ContentListView):
    model = KeyDate
    template_name = "features/public/key_date_list.html"


class KeyDateCreateView(ContentCreateView):
    form_class = KeyDateForm
    template_name = "features/public/content_form.html"
    list_url_name = "public_admin:keydate_list"

    def get(self, request):
        return self.render(request, {"form": self.form_class(), "title": "Add key date", "back_url": reverse(self.list_url_name)})

    def post(self, request):
        form = self.get_form()
        if form.is_valid():
            return self._save_and_redirect(form, "Key date added.")
        return self.render(request, {"form": form, "title": "Add key date", "back_url": reverse(self.list_url_name)})


class KeyDateEditView(ContentEditView):
    model = KeyDate
    form_class = KeyDateForm
    template_name = "features/public/content_form.html"
    list_url_name = "public_admin:keydate_list"

    def get(self, request, pk):
        item = self._get_item(pk)
        return self.render(request, {"form": self.form_class(instance=item), "item": item, "title": f"Edit: {item}", "back_url": reverse(self.list_url_name)})

    def post(self, request, pk):
        item = self._get_item(pk)
        form = self.get_form(instance=item)
        if form.is_valid():
            return self._save_and_redirect(form, "Saved.")
        return self.render(request, {"form": form, "item": item, "title": f"Edit: {item}", "back_url": reverse(self.list_url_name)})


class KeyDateToggleView(ContentToggleView):
    model = KeyDate
    list_url_name = "public_admin:keydate_list"


class KeyDateDeleteView(ContentDeleteView):
    model = KeyDate
    list_url_name = "public_admin:keydate_list"


# ── Website overview dashboard ────────────────────────────────────────────────

class WebsiteOverviewView(WebsiteAdminMixin, TemplateView):
    """
    Landing page for the Website section of the Admin Portal.
    Shows counts for every content type with quick-action links.
    """
    template_name = "features/public/website_overview.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["counts"] = {
            "hero_slides": HeroSlide.objects.count(),
            "feature_cards": FeatureCard.objects.count(),
            "testimonials": Testimonial.objects.count(),
            "team_members": TeamMember.objects.count(),
            "timeline_events": TimelineEvent.objects.count(),
            "event_items": EventItem.objects.count(),
            "faqs": FAQ.objects.count(),
            "admission_steps": AdmissionStep.objects.count(),
            "key_dates": KeyDate.objects.count(),
            "gallery_photos": GalleryPhoto.objects.count(),
        }
        return ctx
