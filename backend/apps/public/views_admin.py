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
