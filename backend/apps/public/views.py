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

import hashlib
import logging
import secrets

from django.conf import settings
from django.core.cache import cache
from django.core.mail import EmailMessage
from django.http import JsonResponse
from django.urls import reverse_lazy
from django.views.generic import FormView, TemplateView

from . import selectors
from .forms import ContactUsForm
from .models import ContactMessage

logger = logging.getLogger(__name__)

ENQUIRY_COOKIE = "pub_enquiry_visitor"
ENQUIRY_COOKIE_SALT = "public-enquiry"
ENQUIRY_LIMIT = 5
ENQUIRY_WINDOW_SECONDS = 10 * 60
ENQUIRY_COOKIE_MAX_AGE = 60 * 60 * 24 * 365
RATE_LIMIT_MESSAGE = (
    "You've sent several messages in the last few minutes. "
    "Please wait a little and try again, or call the school on the number below."
)
HONEYPOT_MESSAGE = (
    "We couldn't accept this message. Please check the form and try again."
)


def _signed_visitor(request):
    return request.get_signed_cookie(
        ENQUIRY_COOKIE, salt=ENQUIRY_COOKIE_SALT, default=None
    )


def _enquiry_bucket(request) -> str:
    """Rate-limit key: the visitor's cookie, or their address before we set one.

    The cookie is deliberately the primary key — a shared school or carrier
    address should not spend one family's allowance — and the address bucket
    only covers the very first request from a visitor who has no cookie yet.
    """
    token = _signed_visitor(request)
    if token:
        return f"public-enquiry:{token}"
    address = (
        request.META.get("HTTP_CF_CONNECTING_IP")
        or request.META.get("REMOTE_ADDR", "")
    )
    digest = hashlib.sha256(address.encode()).hexdigest()[:20]
    return f"public-enquiry:ip:{digest}"


def _enquiry_hits(request) -> int:
    """Count this attempt against the window. A broken cache must not block."""
    key = _enquiry_bucket(request)
    try:
        cache.add(key, 0, ENQUIRY_WINDOW_SECONDS)
        return cache.incr(key)
    except Exception:  # noqa: BLE001
        logger.warning("Enquiry rate limit could not use the cache", exc_info=True)
        return 0


def _wants_json(request) -> bool:
    return request.headers.get("X-Requested-With") == "XMLHttpRequest"


def _form_errors(form) -> dict[str, list[str]]:
    return {
        field: [str(error) for error in errors]
        for field, errors in form.errors.items()
    }


class PublicPageMixin:
    """
    Context shared by all five pages: the nav, the school's identity, and the
    portal doors that make the footer the wide one.
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
        ctx["hero_slides"] = selectors.active_hero_slides()
        ctx["feature_cards"] = selectors.active_feature_cards()
        ctx["testimonials"] = selectors.active_testimonials()
        ctx["event_items"] = selectors.active_event_items()
        return ctx


class AboutView(PublicPageMixin, TemplateView):
    template_name = "public/about.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["facts"] = selectors.home_facts()
        ctx["team_members"] = selectors.active_team_members()
        ctx["timeline_events"] = selectors.active_timeline_events()
        return ctx


class AcademicsView(PublicPageMixin, TemplateView):
    template_name = "public/academics.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["overview"] = selectors.academics_overview()
        ctx["facts"] = selectors.home_facts()
        return ctx


class GalleryView(PublicPageMixin, TemplateView):
    template_name = "public/gallery.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["photos"] = selectors.published_photos()
        ctx["event_items"] = selectors.active_event_items()
        return ctx


class ContactView(PublicPageMixin, FormView):
    """
    Contact details plus an enquiry form.

    Success is reported only after the row exists. The page works without
    JavaScript (normal POST, redirect, session flag); with JavaScript the same
    endpoint answers JSON so the page can show a confirmed message or a retry
    without losing what the visitor typed.
    """

    template_name = "public/contact.html"
    form_class = ContactUsForm
    success_url = reverse_lazy("public:contact")

    def post(self, request, *args, **kwargs):
        self.visitor_token = _signed_visitor(request) or secrets.token_hex(16)
        if _enquiry_hits(request) >= ENQUIRY_LIMIT:
            form = self.get_form()
            form.add_error(None, RATE_LIMIT_MESSAGE)
            if _wants_json(request):
                return self._respond(
                    JsonResponse(
                        {
                            "ok": False,
                            "errors": {"__all__": [RATE_LIMIT_MESSAGE]},
                            "retry": True,
                        },
                        status=429,
                    )
                )
            return self._respond(
                self.render_to_response(self.get_context_data(form=form))
            )
        return super().post(request, *args, **kwargs)

    def _respond(self, response):
        response.set_signed_cookie(
            ENQUIRY_COOKIE,
            self.visitor_token,
            salt=ENQUIRY_COOKIE_SALT,
            max_age=ENQUIRY_COOKIE_MAX_AGE,
            httponly=True,
            samesite="Lax",
            secure=self.request.is_secure(),
        )
        return response

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        saved = False
        if self.request.session.session_key:
            saved = self.request.session.pop("enquiry_saved", False)
        ctx["enquiry_saved"] = saved
        ctx["faqs"] = selectors.active_faqs()
        ctx["admission_steps"] = selectors.active_admission_steps()
        ctx["key_dates"] = selectors.active_key_dates()
        return ctx

    def form_valid(self, form):
        if form.cleaned_data.get("website"):
            logger.info("Discarded a honeypot enquiry")
            form.add_error(None, HONEYPOT_MESSAGE)
            return self.form_invalid(form)

        enquiry = ContactMessage.objects.create(
            **{
                field: value
                for field, value in form.cleaned_data.items()
                if field != "website"
            }
        )
        self.mail_to_school(enquiry)

        if _wants_json(self.request):
            return self._respond(JsonResponse({"ok": True}))

        self.request.session["enquiry_saved"] = True
        return self._respond(super().form_valid(form))

    def form_invalid(self, form):
        if _wants_json(self.request):
            return self._respond(
                JsonResponse(
                    {"ok": False, "errors": _form_errors(form)}, status=400
                )
            )
        return self._respond(super().form_invalid(form))

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
