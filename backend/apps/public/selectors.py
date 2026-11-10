"""
apps/public/selectors.py
------------------------
Every number and name on the school's public pages, read from the tables the
school already fills.

Nothing here makes anything up. A fact the school has not entered comes back
empty so the template can say "not written yet" — a website for a real school
must not guess how many pupils it has or finish a head teacher's sentence.

These are read-only selectors, so a public page can be held to a query budget.
Each one takes ``request`` only when it wants the per-request settings memo.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from django.db.models import Count, Q
from django.urls import reverse
from django.utils import timezone

from apps.academics.models import AcademicYear, SchoolClass, Subject, Term
from apps.accounts.models import User
from apps.accounts.portals import PORTALS
from apps.core.models import SchoolSettings
from apps.core.selectors import school_settings_values
from apps.students.models import Student

from .models import (
    AdmissionStep,
    ContactMessage,
    EventItem,
    FAQ,
    FeatureCard,
    GalleryPhoto,
    HeroSlide,
    KeyDate,
    TeamMember,
    TimelineEvent,
    Testimonial,
)

# The public nav, in the order a visitor should meet it. ``url_name`` resolves
# inside the ``public`` namespace, so a page can be renamed here and nowhere else.
PUBLIC_NAV: tuple[tuple[str, str], ...] = (
    ("home", "Home"),
    ("about", "About"),
    ("academics", "Academics"),
    ("gallery", "Gallery"),
    ("contact", "Contact"),
)


def site_identity(request: Any = None) -> dict:
    """
    The school's public identity: name and motto and contact details from the
    effective settings, plus the story fields that only the website uses.

    ``school_settings_values`` already resolves the blank-means-env rule and
    memoises per request, so the shared fields are taken from it rather than
    read twice. The six website-only fields come off the same singleton row.

    The result is memoised on the request too: the Contact page asks for the
    school's email address once to render the page and once again to mail an
    enquiry, and one page should not read one row twice for that.
    """
    if request is not None:
        memo = getattr(request, "_site_identity", None)
        if memo is not None:
            return memo

    effective = school_settings_values(request)
    row = SchoolSettings.objects.filter(pk=SchoolSettings.SINGLETON_PK).first()

    def _str(field: str) -> str:
        return (getattr(row, field, "") or "").strip()

    established_on = getattr(row, "established_on", None)
    logo = getattr(row, "logo", None)
    values = {
        # ── Core identity (env-fallback fields) ──────────────────────────
        "name": effective["name"],
        "motto": effective["motto"],
        "phone": effective["phone"],
        "email": effective["email"],
        # ── About / story ─────────────────────────────────────────────────
        "address": _str("address"),
        "logo_url": logo.url if logo else "",
        "about": _str("about"),
        "mission": _str("mission"),
        "vision": _str("vision"),
        "values": _str("values"),
        # ── Dates ─────────────────────────────────────────────────────────
        "established_on": established_on,
        "established_year": established_on.year if established_on else None,
        "years_running": _whole_years(established_on, timezone.localdate()),
        # ── Home hero ─────────────────────────────────────────────────────
        "hero_eyebrow": _str("hero_eyebrow"),
        "hero_title": _str("hero_title"),
        "hero_title_highlight": _str("hero_title_highlight"),
        "hero_text": _str("hero_text"),
        # ── Home CTA band ─────────────────────────────────────────────────
        "cta_title": _str("cta_title"),
        "cta_text": _str("cta_text"),
        # ── Appearance ────────────────────────────────────────────────────
        "theme": _str("theme") or "dark",
        # ── Footer ────────────────────────────────────────────────────────
        "footer_about": _str("footer_about"),
        # ── Social links ──────────────────────────────────────────────────
        "facebook_url": _str("facebook_url"),
        "instagram_url": _str("instagram_url"),
        "youtube_url": _str("youtube_url"),
        "linkedin_url": _str("linkedin_url"),
        # ── Contact / admissions extras ───────────────────────────────────
        "admissions_email": _str("admissions_email"),
        "office_hours": _str("office_hours"),
        "map_embed_url": _str("map_embed_url"),
    }
    if request is not None:
        request._site_identity = values
    return values


def _whole_years(since: date | None, today: date) -> int | None:
    """Completed years between two dates — never a rounded-up anniversary."""
    if since is None or since > today:
        return None
    return today.year - since.year - ((today.month, today.day) < (since.month, since.day))


def nav_links(request) -> list[dict]:
    """The public nav with the current page marked, for the shared navbar."""
    links = []
    for url_name, label in PUBLIC_NAV:
        url = reverse(f"public:{url_name}")
        links.append(
            {
                "label": label,
                "url": url,
                "is_active": request.path == url,
            }
        )
    return links


def portal_links() -> list[dict]:
    """
    The six sign-in doors, read out of ``accounts.portals.PORTALS``.

    That registry is already the single source for the chooser and the
    role-pinned login pages, so a portal added or renamed there shows up in
    this footer without a second list to keep in step.
    """
    return [
        {
            "slug": portal.slug,
            "label": portal.label,
            "blurb": portal.blurb,
            "icon": portal.icon,
            "url": reverse("accounts:role_login", kwargs={"role": portal.slug}),
        }
        for portal in PORTALS.values()
    ]


def home_facts() -> dict:
    """
    Counters for the home page's fact band.

    Each is a real row count from the table that owns it. When the school has
    not marked an academic year current, ``classes`` and ``terms`` come back 0
    and the band drops them rather than showing a zero that reads as "empty
    school".
    """
    current_year = AcademicYear.objects.filter(is_current=True).first()
    teachers = User.objects.filter(role=User.Role.TEACHER, is_active=True)
    return {
        "pupils": Student.objects.filter(status=Student.Status.ACTIVE).count(),
        "teachers": teachers.count(),
        "classes": SchoolClass.objects.filter(academic_year=current_year).count()
        if current_year
        else 0,
        "terms": Term.objects.filter(academic_year=current_year).count()
        if current_year
        else 0,
        "subjects": Subject.objects.count(),
        "has_current_year": current_year is not None,
    }


def academics_overview() -> dict:
    """
    What the school teaches, for the public Academics page.

    Class *names* and subject *names* are on this page; no pupil, guardian,
    teacher or fee figure is, which is the line a public page must not cross.
    """
    year = AcademicYear.objects.filter(is_current=True).first()
    terms: list[Term] = []
    classes: list[SchoolClass] = []
    if year is not None:
        terms = list(Term.objects.filter(academic_year=year).order_by("start_date"))
        classes = list(
            SchoolClass.objects.filter(academic_year=year)
            # The page lists each class's streams; without this it pays one
            # query per class, which a public page should never do.
            .prefetch_related("streams")
            .order_by("level_order", "name")
        )
    subjects = list(Subject.objects.order_by("name").values_list("name", flat=True))
    return {
        "year": year,
        "terms": terms,
        "classes": classes,
        "subjects": subjects,
        "years": list(AcademicYear.objects.order_by("-start_date")[:6]),
    }


def published_photos() -> list[GalleryPhoto]:
    """Every gallery photo a visitor is allowed to see, in display order."""
    return list(GalleryPhoto.objects.filter(is_published=True))


# ── The school's own Website pages ────────────────────────────────────────────
# Reads for the admin screens, kept beside the public ones so the two views of
# the same table cannot drift into disagreeing about what "in display order"
# means.


def photo_for_management() -> list[GalleryPhoto]:
    """
    Every photograph, published or held back, in the order the public page will
    show the published ones.
    """
    return list(GalleryPhoto.objects.all())


def enquiry_counts() -> dict:
    """
    How many enquiries there are and how many are still open.

    One query, because the list page shows both numbers as chips above a table
    it is about to query a third time for the page itself.
    """
    row = ContactMessage.objects.aggregate(
        total=Count("pk"),
        open_count=Count("pk", filter=Q(handled=False)),
    )
    return {"total": row["total"], "open": row["open_count"]}


def enquiry_list(*, status: str = "all", q: str = ""):
    """
    The enquiry inbox, newest first, narrowed by the two filters the page offers.

    ``q`` searches the sender's name, their address and the message body: an
    office looking for "Kofi" or for "uniform" should find it without being
    told which column the words were typed into.
    """
    queryset = ContactMessage.objects.all()
    if status == "handled":
        queryset = queryset.filter(handled=True)
    elif status == "open":
        queryset = queryset.filter(handled=False)
    if q:
        queryset = queryset.filter(
            Q(name__icontains=q) | Q(email__icontains=q) | Q(message__icontains=q)
        )
    return queryset


# ── New dynamic content selectors ─────────────────────────────────────────────

def active_hero_slides() -> list[HeroSlide]:
    """Hero carousel slides in display order."""
    return list(HeroSlide.objects.filter(is_active=True))


def active_feature_cards() -> list[FeatureCard]:
    """'Why Choose Us' cards in display order."""
    return list(FeatureCard.objects.filter(is_active=True))


def active_testimonials() -> list[Testimonial]:
    """Parent testimonials in display order."""
    return list(Testimonial.objects.filter(is_active=True))


def active_team_members() -> list[TeamMember]:
    """Leadership team cards in display order."""
    return list(TeamMember.objects.filter(is_active=True))


def active_timeline_events() -> list[TimelineEvent]:
    """History timeline items in display order."""
    return list(TimelineEvent.objects.filter(is_active=True))


def active_event_items() -> list[EventItem]:
    """Upcoming events shown on the School Life / Gallery page."""
    return list(EventItem.objects.filter(is_active=True))


def active_faqs() -> list[FAQ]:
    """FAQ accordion items in display order."""
    return list(FAQ.objects.filter(is_active=True))


def active_admission_steps() -> list[AdmissionStep]:
    """Numbered admissions process steps in display order."""
    return list(AdmissionStep.objects.filter(is_active=True))


def active_key_dates() -> list[KeyDate]:
    """Key admissions dates in display order."""
    return list(KeyDate.objects.filter(is_active=True))


# ── Management selectors (admin screens) ──────────────────────────────────────

def all_hero_slides() -> list[HeroSlide]:
    return list(HeroSlide.objects.all())


def all_feature_cards() -> list[FeatureCard]:
    return list(FeatureCard.objects.all())


def all_testimonials() -> list[Testimonial]:
    return list(Testimonial.objects.all())


def all_team_members() -> list[TeamMember]:
    return list(TeamMember.objects.all())


def all_timeline_events() -> list[TimelineEvent]:
    return list(TimelineEvent.objects.all())


def all_event_items() -> list[EventItem]:
    return list(EventItem.objects.all())


def all_faqs() -> list[FAQ]:
    return list(FAQ.objects.all())


def all_admission_steps() -> list[AdmissionStep]:
    return list(AdmissionStep.objects.all())


def all_key_dates() -> list[KeyDate]:
    return list(KeyDate.objects.all())
