"""
apps/public/views.py
--------------------
Five pages that are open to everyone.

No role mixin appears in this file, and that is the point: ``RoleRequiredMixin``
raises 403 for an anonymous visitor, and a school website whose first-time
visitor meets a 403 is not a website. Access is also asserted in
``apps/core/tests/test_security_matrix.py``, where each of these paths is
recorded as deliberately public.
"""

from __future__ import annotations

import logging

from django.conf import settings
from django.contrib import messages
from django.core.mail import EmailMessage
from django.urls import reverse_lazy
from django.views.generic import FormView, TemplateView

from . import selectors
from .forms import ContactUsForm
from .models import ContactMessage

logger = logging.getLogger(__name__)


class PublicPageMixin:
    """
    Context shared by all five pages: the nav, the school's identity, and the
    portal doors that make the footer the wide one.

    ``portal_links`` is the flag ``partials/_footer_base.html`` reads. App pages
    never set it, so they keep the one-line footer they have always had and this
    is the only place the columns appear — one footer file, two presentations.
    """

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["nav_links"] = selectors.nav_links(self.request)
        ctx["portal_links"] = selectors.portal_links()
        ctx["identity"] = selectors.site_identity(self.request)
        return ctx


class HomeView(PublicPageMixin, TemplateView):
    template_name = "public/home.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["facts"] = selectors.home_facts()
        ctx["photos"] = selectors.published_photos()[:4]
        return ctx


class AboutView(PublicPageMixin, TemplateView):
    template_name = "public/about.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["facts"] = selectors.home_facts()
        return ctx


class AcademicsView(PublicPageMixin, TemplateView):
    template_name = "public/academics.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["overview"] = selectors.academics_overview()
        return ctx


class GalleryView(PublicPageMixin, TemplateView):
    template_name = "public/gallery.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["photos"] = selectors.published_photos()
        return ctx


class ContactView(PublicPageMixin, FormView):
    """
    Contact details plus an enquiry form.

    The enquiry is saved first and mailed second. A mailbox that is down is not
    a reason to lose a parent's question, and it is certainly not a reason to
    show a visitor a 500 on the page that is supposed to invite them in.
    """

    template_name = "public/contact.html"
    form_class = ContactUsForm
    success_url = reverse_lazy("public:contact")

    def form_valid(self, form):
        if form.cleaned_data.get("website"):
            logger.info(
                "Discarded a honeypot enquiry from %s", form.cleaned_data["email"]
            )
            return super().form_valid(form)

        enquiry = ContactMessage.objects.create(
            **{
                field: value
                for field, value in form.cleaned_data.items()
                if field != "website"
            }
        )
        self.mail_to_school(enquiry)
        messages.success(
            self.request,
            "Thank you — your message is with the school. "
            "We reply to the email address you gave us.",
        )
        return super().form_valid(form)

    def mail_to_school(self, enquiry: ContactMessage) -> None:
        identity = selectors.site_identity(self.request)
        recipient = identity["email"]
        if not recipient:
            logger.info(
                "Website enquiry %s saved; no school email is configured to mail it to",
                enquiry.pk,
            )
            return
        try:
            EmailMessage(
                subject=f"Website enquiry from {enquiry.name}",
                body=(
                    f"Name: {enquiry.name}\n"
                    f"Email: {enquiry.email}\n"
                    f"Phone: {enquiry.phone or '—'}\n\n"
                    f"{enquiry.message}\n"
                ),
                from_email=settings.DEFAULT_FROM_EMAIL,
                to=[recipient],
                reply_to=[enquiry.email],
            ).send()
        except Exception:
            logger.warning(
                "Website enquiry %s was saved but could not be mailed",
                enquiry.pk,
                exc_info=True,
            )
