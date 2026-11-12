"""
apps/public/tests/test_public_site.py
-------------------------------------
The school's five public pages: who may see them, what they are allowed to say,
and what they must never leak.

Four rules hold this module together, and each has a test:

  * **Open to everyone.** No role mixin sits on these views, so an anonymous
    visitor and a signed-in parent get the same page. The matrix in
    ``apps/core/tests/test_security_matrix.py`` records the same five paths as
    deliberately public.
  * **Real numbers, labelled.** Where a page counts something, the count is a
    row count from the table that owns it, and a count the school has not
    entered renders as an em dash rather than a confident zero. Prose and
    imagery the school has not supplied fall back to presentation copy in the
    template — never to a private value.
  * **No private numbers.** A public page may name a class or a subject; it must
    never carry a pupil, a money figure or a balance. The static test reads the
    template files for the vocabulary that would mean it had crossed that line.
  * **One shell.** These pages extend ``public/base.html``, which renders the
    public navbar and footer itself — never the ``base_template`` variable,
    which resolves to the viewer's role sidebar.
"""

from __future__ import annotations

import re
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

from django.conf import settings
from django.core import mail
from django.core.cache import cache
from django.core.mail import EmailMessage
from django.db import connections
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from apps.academics.models import AcademicYear, SchoolClass, Stream, Subject, Term
from apps.accounts.portals import PORTALS
from apps.core.models import SchoolSettings
from apps.dashboards.tests.fixtures import make_user
from apps.students.models import Student

from .. import selectors
from ..models import ContactMessage
from .fixtures import MEDIA_TMP, make_photo, upload

PAGE_URL_NAMES = ["home", "about", "academics", "gallery", "contact"]
ALL_ROLES = ["admin", "director", "teacher", "student", "parent", "finance"]

TEMPLATES_DIR = Path(settings.TEMPLATES[0]["DIRS"][0])
PUBLIC_TEMPLATES = sorted((TEMPLATES_DIR / "public").glob("*.html"))
#: Everything a public page is made of, including the shared shell it renders
#: through — a comment in ``base.html`` reaches a visitor just as surely as one
#: in ``home.html``.
SHELL_TEMPLATES = PUBLIC_TEMPLATES + [
    TEMPLATES_DIR / "base.html",
    TEMPLATES_DIR / "partials" / "_footer_base.html",
]

# Words that would mean a public page was reading a private table. Checked
# against the template's own expressions and tags, not its prose: a page may
# say the school bills by term, it may not print what anybody owes.
PRIVATE_TOKENS = (
    "amount_",
    "invoice",
    "receipt",
    "balance",
    "outstanding",
    "admission_number",
    "fee_structure",
    "student.",
    "payment",
)

COMMENT_RE = re.compile(r"\{#.*?#\}", re.DOTALL)
TAG_RE = re.compile(r"{{.*?}}|{%.*?%}", re.DOTALL)


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _without_comments(path: Path) -> str:
    """Template text with ``{# … #}`` blocks removed."""
    return COMMENT_RE.sub("", _read(path))


def set_settings(**kwargs) -> SchoolSettings:
    row, _ = SchoolSettings.objects.get_or_create(pk=SchoolSettings.SINGLETON_PK)
    for field, value in kwargs.items():
        setattr(row, field, value)
    row.save()
    return row


def make_student(school_class: SchoolClass, admission_number: str) -> Student:
    user = make_user("student", username=f"stu_{admission_number.lower()}")
    return Student.objects.create(
        user=user,
        admission_number=admission_number,
        current_class=school_class,
        status=Student.Status.ACTIVE,
    )


class PublicPagesTest(TestCase):
    """Every page answers, for an anonymous visitor and for every role."""

    @classmethod
    def setUpTestData(cls):
        cls.users = {role: make_user(role) for role in ALL_ROLES}

    def test_anonymous_visitor_sees_all_five_pages(self):
        for name in PAGE_URL_NAMES:
            response = self.client.get(reverse(f"public:{name}"))
            self.assertEqual(response.status_code, 200, f"{name} should not need a login")

    def test_every_signed_in_role_sees_all_five_pages(self):
        failures = []
        for role, user in self.users.items():
            self.client.force_login(user)
            for name in PAGE_URL_NAMES:
                status = self.client.get(reverse(f"public:{name}")).status_code
                if status != 200:
                    failures.append(f"  {role} -> {name}: {status}")
        if failures:
            self.fail("A signed-in user was blocked from a public page:\n" + "\n".join(failures))

    def test_pages_render_the_school_name_motto_and_the_nav(self):
        set_settings(name="Greenfield Academy", motto="Learn today, lead tomorrow")
        body = self.client.get(reverse("public:home")).content.decode()
        self.assertIn("Greenfield Academy", body)
        self.assertIn("Learn today, lead tomorrow", body)
        for name in PAGE_URL_NAMES:
            self.assertIn(reverse(f"public:{name}"), body)

    def test_the_root_serves_the_public_home_and_dashboard_routes_users(self):
        resp = self.client.get("/")
        self.assertEqual(resp.status_code, 200)
        self.assertTemplateUsed(resp, "public/home.html")
        for role in ALL_ROLES:
            self.client.force_login(self.users[role])
            root = self.client.get("/")
            self.assertEqual(root.status_code, 200)
            self.assertTemplateUsed(root, "public/home.html")
            self.assertRedirects(
                self.client.get(reverse("accounts:dashboard_redirect")),
                self.users[role].get_dashboard_url(),
                status_code=302,
            )

    def test_visitors_get_a_sign_in_link_and_users_a_dashboard_link(self):
        body = self.client.get(reverse("public:home")).content.decode()
        self.assertIn(f'href="{reverse("accounts:login")}"', body)

        self.client.force_login(self.users["teacher"])
        body = self.client.get(reverse("public:home")).content.decode()
        self.assertIn(self.users["teacher"].get_dashboard_url(), body)

    def test_no_template_source_is_served_to_a_visitor(self):
        """
        Catches at the browser end what the static test catches at the file end:
        an unterminated comment, or a tag written where Django will read it,
        shows up as literal ``{# …`` text in the page a parent reads.
        """
        for name in PAGE_URL_NAMES:
            body = self.client.get(reverse(f"public:{name}")).content.decode()
            self.assertNotIn("{#", body, f"{name} is serving template source")
            self.assertNotIn("#}", body, f"{name} is serving template source")

    def test_a_signed_in_parent_sees_no_sidebar(self):
        """
        The public shell is its own document, not the app shell with a role
        sidebar bolted on: a parent reading the About page gets the public
        navbar and footer, and none of the dashboard's navigation.
        """
        self.client.force_login(self.users["parent"])
        body = self.client.get(reverse("public:about")).content.decode()
        self.assertIn('class="pub-nav"', body)
        self.assertIn('class="pub-footer"', body)
        self.assertNotIn('id="app-sidebar"', body)
        self.assertNotIn("sidebar-item", body)


class FooterPortalLinksTest(TestCase):
    """The footer is where a first-time visitor finds the door that is theirs."""

    def test_portal_links_mirror_the_portal_registry(self):
        links = selectors.portal_links()
        self.assertEqual(len(links), len(PORTALS))
        for link, portal in zip(links, PORTALS.values()):
            self.assertEqual(link["slug"], portal.slug)
            self.assertEqual(link["label"], portal.label)
            self.assertEqual(link["blurb"], portal.blurb)
            self.assertEqual(
                link["url"],
                reverse("accounts:role_login", kwargs={"role": portal.slug}),
            )

    def test_every_portal_door_appears_in_the_footer_of_every_page(self):
        for name in PAGE_URL_NAMES:
            body = self.client.get(reverse(f"public:{name}")).content.decode()
            self.assertIn('class="pub-footer"', body, f"{name} lost the public footer")
            self.assertIn("Portal Login", body)
            for slug in PORTALS:
                self.assertIn(f"/login/{slug}/", body, f"{name} footer misses {slug}")

    def test_app_pages_do_not_get_the_public_footer(self):
        """
        One footer per audience: the six portal doors belong to the public
        website, and the signed-in admin dashboard must not grow a second,
        public-facing footer underneath its own.
        """
        self.client.force_login(make_user("admin"))
        body = self.client.get(reverse("dashboards:admin_home")).content.decode()
        self.assertNotIn('class="pub-footer"', body)
        self.assertNotIn("Portal Login", body)


class CalendarFixtureTest(TestCase):
    """Shared current-year fixture for the pages that read the calendar."""

    @classmethod
    def setUpTestData(cls):
        cls.year = AcademicYear.objects.create(
            name="2025/2026",
            start_date=date(2025, 9, 1),
            end_date=date(2026, 7, 31),
            is_current=True,
        )
        cls.last_year = AcademicYear.objects.create(
            name="2024/2025",
            start_date=date(2024, 9, 1),
            end_date=date(2025, 7, 31),
        )
        cls.junior = SchoolClass.objects.create(
            academic_year=cls.year, name="Grade 3", level_order=3
        )
        cls.senior = SchoolClass.objects.create(
            academic_year=cls.year, name="Grade 9", level_order=9
        )
        Stream.objects.create(name="Blue", school_class=cls.senior)
        SchoolClass.objects.create(
            academic_year=cls.last_year, name="Grade 4 last year", level_order=4
        )
        Term.objects.create(
            academic_year=cls.year,
            name=Term.TermName.TERM_1,
            start_date=date(2025, 9, 1),
            end_date=date(2025, 12, 20),
            is_current=True,
        )
        Term.objects.create(
            academic_year=cls.year,
            name=Term.TermName.TERM_2,
            start_date=date(2026, 1, 5),
            end_date=date(2026, 4, 10),
        )
        Subject.objects.create(name="Mathematics", code="MATH")
        Subject.objects.create(name="Biology", code="BIO")


class HomeFactsTest(CalendarFixtureTest):
    """The counters come from the tables; a zero is dropped, not displayed."""

    def test_counters_match_the_tables_and_ignore_other_years(self):
        make_student(self.junior, "ADM-PUB-1")
        make_student(self.junior, "ADM-PUB-2")
        graduate = make_student(self.junior, "ADM-PUB-GRAD")
        graduate.status = Student.Status.GRADUATED
        graduate.save()
        make_user("teacher")
        make_user("teacher", username="off_staff", is_active=False)

        facts = selectors.home_facts()
        self.assertEqual(facts["pupils"], 2)
        self.assertEqual(facts["teachers"], 1)
        self.assertEqual(facts["classes"], 2)
        self.assertEqual(facts["terms"], 2)
        self.assertEqual(facts["subjects"], 2)
        self.assertTrue(facts["has_current_year"])

        body = self.client.get(reverse("public:home")).content.decode()
        for label in ("Students Enrolled", "Subjects Offered", "Qualified Teachers"):
            self.assertIn(label, body)
        self.assertIn('data-count="2"', body)
        self.assertNotIn("Grade 4 last year", body)

    def test_no_current_year_says_so_instead_of_counting_nothing(self):
        AcademicYear.objects.update(is_current=False)
        facts = selectors.home_facts()
        self.assertEqual(facts["classes"], 0)
        self.assertEqual(facts["terms"], 0)
        self.assertFalse(facts["has_current_year"])

        body = self.client.get(reverse("public:home")).content.decode()
        self.assertIn(
            "A school community, in the figures the school has entered.", body
        )
        self.assertNotIn('data-count="0"', body)

    def test_a_zero_fact_is_an_em_dash_not_a_zero(self):
        """
        An empty roll is not the same as an unknown one, but a public band
        cannot tell the difference — so the count the school has not filled is
        an em dash, and no counter animation is handed a zero.
        """
        body = self.client.get(reverse("public:home")).content.decode()
        self.assertIn("Students Enrolled", body)
        self.assertNotIn('data-count="0"', body)
        self.assertGreaterEqual(body.count(">—<"), 1)

    def test_home_sends_visitors_to_the_gallery_instead_of_embedding_photos(self):
        """The gallery is the one place photographs live; home links to it."""
        with override_settings(MEDIA_ROOT=MEDIA_TMP):
            make_photo(title="Harvest", display_order=1)
            body = self.client.get(reverse("public:home")).content.decode()
        self.assertIn(f'href="{reverse("public:gallery")}"', body)
        self.assertIn("See Gallery", body)
        self.assertNotIn("Harvest", body)


class AboutPageTest(TestCase):
    """The written story wins; where none is written the shell's copy stands in."""

    def test_an_unwritten_story_uses_the_shell_copy_and_leaves_numbers_blank(self):
        body = self.client.get(reverse("public:about")).content.decode()
        self.assertIn("A thriving school community guided by the belief", body)
        self.assertIn("To provide a nurturing, inquiry-driven education", body)
        self.assertIn("To be the school our community trusts", body)
        self.assertIn("Curiosity, kindness, integrity and excellence", body)
        self.assertGreaterEqual(body.count(">—<"), 5)

    def test_written_fields_replace_the_placeholders(self):
        set_settings(
            about="We teach eleven to eighteen year olds on two sites.",
            mission="Every child known by name.",
            vision="A school the town is proud of.",
            established_on=date(2001, 9, 1),
        )
        body = self.client.get(reverse("public:about")).content.decode()
        self.assertIn("We teach eleven to eighteen year olds", body)
        self.assertIn("Every child known by name.", body)
        self.assertIn("A school the town is proud of.", body)
        self.assertNotIn("A thriving school community guided by the belief", body)
        self.assertIn(">Founded</span><strong>2001</strong>", body)

    def test_years_running_counts_completed_years_only(self):
        today = timezone.localdate()
        self.assertEqual(selectors._whole_years(today - timedelta(days=1), today), 0)
        self.assertEqual(selectors._whole_years(today - timedelta(days=400), today), 1)
        self.assertEqual(
            selectors._whole_years(today.replace(year=today.year - 3), today), 3
        )
        self.assertIsNone(selectors._whole_years(None, today))
        self.assertIsNone(selectors._whole_years(today + timedelta(days=1), today))

    def test_an_unwritten_founding_date_shows_a_dash_not_a_guess(self):
        set_settings(established_on=None)
        identity = selectors.site_identity()
        self.assertIsNone(identity["established_year"])
        self.assertIsNone(identity["years_running"])

        home = self.client.get(reverse("public:home")).content.decode()
        self.assertIn("Welcome", home)
        self.assertNotIn("Educating young people since", home)
        self.assertNotIn("Trusted by families since", home)

        about = self.client.get(reverse("public:about")).content.decode()
        self.assertIn(">Founded</span><strong>—</strong>", about)
        self.assertIn("Our <span>Story</span>", about)
        self.assertNotIn("Years of <span>Believing in Children</span>", about)


class AcademicsPageTest(CalendarFixtureTest):
    """The real calendar, including when there is nothing real to show."""

    def test_shows_the_current_year_terms_and_classes(self):
        body = self.client.get(reverse("public:academics")).content.decode()
        self.assertIn("2025/2026", body)
        self.assertIn("Grade 3", body)
        self.assertIn("Grade 9", body)
        self.assertIn("Term 1", body)
        self.assertIn("</i> Now</span>", body)
        self.assertIn("1 Sep 2025", body)
        self.assertIn("20 Dec 2025", body)
        self.assertIn("<strong>2</strong><span>Subjects</span>", body)

    def test_classes_list_lowest_level_first(self):
        body = self.client.get(reverse("public:academics")).content.decode()
        self.assertLess(body.index("Grade 3"), body.index("Grade 9"))
        self.assertNotIn("Grade 4 last year", body)

    def test_no_current_year_means_no_calendar_section(self):
        AcademicYear.objects.update(is_current=False)
        body = self.client.get(reverse("public:academics")).content.decode()
        self.assertIn("Admissions Open", body)
        self.assertNotIn("Academic Calendar", body)
        self.assertNotIn("Grade 3", body)

    def test_only_the_current_year_is_published_not_the_archive(self):
        body = self.client.get(reverse("public:academics")).content.decode()
        self.assertIn("2025/2026", body)
        self.assertNotIn("2024/2025", body)

    def test_does_not_name_a_pupil_or_a_staff_id(self):
        make_student(self.junior, "ADM-PUB-9")
        from apps.teachers.models import TeacherProfile

        TeacherProfile.objects.create(user=make_user("teacher"), staff_id="TCH-PUB-1")
        body = self.client.get(reverse("public:academics")).content.decode()
        self.assertNotIn("ADM-PUB-9", body)
        self.assertNotIn("TCH-PUB-1", body)


@override_settings(MEDIA_ROOT=MEDIA_TMP)
class GalleryPageTest(TestCase):
    def test_lists_published_photos_in_display_order_and_hides_drafts(self):
        make_photo(title="Harvest", display_order=2)
        make_photo(title="Sports day", display_order=1)
        make_photo(title="Not for the web", display_order=3, is_published=False)

        self.assertEqual(
            [photo.title for photo in selectors.published_photos()],
            ["Sports day", "Harvest"],
        )

        body = self.client.get(reverse("public:gallery")).content.decode()
        self.assertIn("Sports day", body)
        self.assertIn("Harvest", body)
        self.assertNotIn("Not for the web", body)
        self.assertLess(body.index("Sports day"), body.index("Harvest"))
        self.assertEqual(body.count('class="pub-gallery__item"'), 2)
        self.assertNotIn("picsum.photos/seed/sfa-g1/", body)

    def test_an_empty_gallery_is_filled_with_the_placeholder_set(self):
        """
        With no uploads the template shows its twelve stock pictures rather
        than an empty grid, and no local media path is invented alongside them.
        """
        body = self.client.get(reverse("public:gallery")).content.decode()
        self.assertEqual(body.count('class="pub-gallery__item"'), 12)
        self.assertIn("picsum.photos/seed/sfa-g1/", body)
        self.assertNotIn("/media/gallery/", body)


class ContactPageTest(TestCase):
    """
    An enquiry is confirmed only after the row exists. The browser posts the
    same form either way; with JavaScript it is sent in the background and the
    page reads a JSON answer, without it the visitor is redirected to a page
    that says the message was saved.
    """

    def setUp(self):
        self.url = reverse("public:contact")
        cache.clear()

    def post(self, headers=None, **overrides):
        payload = {
            "name": "  Ama Mensah ",
            "email": "  Ama@Example.com ",
            "phone": " +233 20 000 0000 ",
            "message": "Which class would my daughter join? ",
            "website": "",
        }
        payload.update(overrides)
        return self.client.post(self.url, payload, **(headers or {}))

    def ajax(self, **overrides):
        return self.post(
            headers={"HTTP_X_REQUESTED_WITH": "XMLHttpRequest"}, **overrides
        )

    def test_the_honeypot_is_a_hidden_field_the_visitor_is_not_asked_to_fill(self):
        body = self.client.get(self.url).content.decode()
        self.assertIn('type="hidden"', body)
        self.assertIn('name="website"', body)

    def test_the_page_has_a_place_for_every_error_the_browser_may_report(self):
        body = self.client.get(self.url).content.decode()
        for name in ("name", "email", "phone", "message"):
            self.assertIn(f'data-error="{name}"', body)
        self.assertIn('id="pubFormSuccess"', body)
        self.assertIn('id="pubFormError"', body)

    def test_valid_enquiry_is_saved_trimmed_and_mailed(self):
        set_settings(email="office@greenfield.test")
        with override_settings(SCHOOL_EMAIL=""):
            response = self.post()
        self.assertRedirects(response, self.url, status_code=302)

        enquiry = ContactMessage.objects.get()
        self.assertEqual(enquiry.name, "Ama Mensah")
        self.assertEqual(enquiry.email, "ama@example.com")
        self.assertEqual(enquiry.phone, "+233 20 000 0000")
        self.assertEqual(enquiry.message, "Which class would my daughter join?")

        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ["office@greenfield.test"])
        self.assertEqual(mail.outbox[0].reply_to, ["ama@example.com"])
        self.assertIn("Ama Mensah", mail.outbox[0].subject)
        self.assertIn("Which class would my daughter join?", mail.outbox[0].body)
        self.assertIn("ama@example.com", mail.outbox[0].body)

    def test_without_javascript_the_saved_page_confirms_once(self):
        response = self.post()
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], self.url)

        confirmation = self.client.get(self.url).content.decode()
        self.assertIn('class="pub-form-success pub-form-success--static"', confirmation)
        self.assertIn("Inquiry Submitted!", confirmation)

        refreshed = self.client.get(self.url).content.decode()
        self.assertNotIn('class="pub-form-success pub-form-success--static"', refreshed)

    def test_ajax_success_is_answered_only_after_the_row_is_stored(self):
        response = self.ajax()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"ok": True})
        self.assertEqual(ContactMessage.objects.count(), 1)

    def test_ajax_validation_error_is_answered_with_the_field_errors(self):
        response = self.ajax(email="not-an-email", message="")
        self.assertEqual(response.status_code, 400)
        body = response.json()
        self.assertFalse(body["ok"])
        self.assertIn("email", body["errors"])
        self.assertIn("message", body["errors"])
        self.assertEqual(ContactMessage.objects.count(), 0)

    def test_a_dead_mailbox_does_not_lose_the_enquiry_or_500_the_page(self):
        set_settings(email="office@greenfield.test")
        with patch.object(EmailMessage, "send", side_effect=OSError("smtp down")):
            response = self.post()
        self.assertEqual(response.status_code, 302)
        self.assertEqual(ContactMessage.objects.count(), 1)

    @override_settings(SCHOOL_EMAIL="")
    def test_no_school_email_still_records_the_enquiry_and_sends_nothing(self):
        set_settings(email="")
        self.assertEqual(self.post().status_code, 302)
        self.assertEqual(ContactMessage.objects.count(), 1)
        self.assertEqual(len(mail.outbox), 0)

    def test_invalid_email_or_missing_message_is_refused(self):
        response = self.post(email="not-an-email", message="")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(ContactMessage.objects.count(), 0)
        body = response.content.decode()
        self.assertIn("Enter a valid email address", body)
        self.assertIn("This field is required", body)
        self.assertNotIn('class="pub-form-success pub-form-success--static"', body)

    def test_oversized_message_is_refused(self):
        response = self.post(message="x" * 2001)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(ContactMessage.objects.count(), 0)

    def test_a_spam_bot_gets_an_error_and_nothing_is_saved(self):
        set_settings(email="office@greenfield.test")
        response = self.post(website="http://spam.example")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(ContactMessage.objects.count(), 0)
        self.assertEqual(len(mail.outbox), 0)
        body = response.content.decode()
        self.assertIn("accept this message", body)
        self.assertNotIn('class="pub-form-success pub-form-success--static"', body)

    def test_a_visitor_is_stopped_after_five_messages_in_ten_minutes(self):
        set_settings(email="")
        for _ in range(5):
            self.assertEqual(self.post().status_code, 302)
        self.assertEqual(ContactMessage.objects.count(), 5)

        refused = self.post()
        self.assertEqual(refused.status_code, 200)
        self.assertIn("several messages in the last few minutes", refused.content.decode())
        self.assertEqual(ContactMessage.objects.count(), 5)

    def test_the_rate_limit_answers_json_429_for_the_browser(self):
        set_settings(email="")
        for _ in range(5):
            self.ajax()
        refused = self.ajax()
        self.assertEqual(refused.status_code, 429)
        body = refused.json()
        self.assertFalse(body["ok"])
        self.assertTrue(body["retry"])
        self.assertIn("__all__", body["errors"])
        self.assertEqual(ContactMessage.objects.count(), 5)

    def test_the_limit_does_not_lock_out_a_neighbour_at_the_same_address(self):
        """The cookie, not the address, is the bucket: one family's allowance."""
        set_settings(email="")
        for _ in range(5):
            self.post()
        self.assertEqual(self.post().status_code, 200)

        self.client.cookies.clear()
        self.assertEqual(self.post().status_code, 302)
        self.assertEqual(ContactMessage.objects.count(), 6)

    def test_a_broken_cache_cannot_block_a_real_enquiry(self):
        class BrokenCache:
            def add(self, *args, **kwargs):
                raise RuntimeError("cache down")

            def incr(self, *args, **kwargs):
                raise RuntimeError("cache down")

        with patch("apps.public.views.cache", BrokenCache()):
            for _ in range(6):
                self.assertEqual(self.post().status_code, 302)
        self.assertEqual(ContactMessage.objects.count(), 6)

    def test_a_signed_in_user_can_use_the_form_too(self):
        set_settings(email="office@greenfield.test")
        self.client.force_login(make_user("parent"))
        self.assertEqual(self.post().status_code, 302)
        self.assertEqual(ContactMessage.objects.count(), 1)


class QueryBudgetTest(CalendarFixtureTest):
    """
    A public page is read by everyone, including whatever crawls the school's
    address next month, so each one is held to a fixed number of queries. The
    ceilings are the measured count plus two — raise them deliberately, with a
    reason, never because a test went red.
    """

    # Measured against the fixture below: home 14, about 11, academics 15,
    # gallery 5, contact 6 — each ceiling is that number plus two. The counts
    # include the savepoint pair a TestCase-wrapped request emits.
    #
    # The public redesign added small published tables the old pages did not
    # read (hero slides, "why choose us" cards, testimonials, events), which is
    # why home and about sit above their old budgets. Academics is the outlier:
    # it runs both ``academics_overview`` and ``home_facts``, so it asks for the
    # current year and the subject count twice. Folding those two selectors
    # together is a known follow-up; until then the ceiling records the cost
    # rather than hiding it.
    CEILINGS = {
        "home": 16,
        "about": 13,
        "academics": 17,
        "gallery": 7,
        "contact": 8,
    }

    def test_each_page_stays_inside_its_budget(self):
        over = []
        for name, ceiling in self.CEILINGS.items():
            with CaptureQueriesContext(connections["default"]) as ctx:
                status = self.client.get(reverse(f"public:{name}")).status_code
            self.assertEqual(status, 200, name)
            if len(ctx) > ceiling:
                over.append(
                    f"  {name}: {len(ctx)} queries, ceiling {ceiling}\n"
                    + "\n".join(f"    {query['sql'][:110]}" for query in ctx)
                )
        if over:
            self.fail("A public page out-grew its query budget:\n" + "\n".join(over))


class StaticContractTest(TestCase):
    """
    The lines a public page must never cross, checked against the template
    files themselves so a later edit cannot cross one by accident.
    """

    def test_public_pages_extend_the_public_shell_not_a_role_shell(self):
        offenders = [
            path.name
            for path in PUBLIC_TEMPLATES
            if "{% extends base_template %}" in _without_comments(path)
        ]
        self.assertEqual(
            offenders,
            [],
            "A public template that extends base_template bolts the viewer's "
            "role sidebar onto a public page",
        )
        app_shell = [
            path.name
            for path in PUBLIC_TEMPLATES
            if '{% extends "base.html" %}' in _without_comments(path)
        ]
        self.assertEqual(
            app_shell,
            [],
            "A public template must extend public/base.html, not the app shell",
        )
        shell = _without_comments(TEMPLATES_DIR / "public" / "base.html")
        self.assertNotIn("{% extends", shell)
        self.assertIn('<nav class="pub-nav"', shell)
        self.assertIn('<footer class="pub-footer">', shell)

    def test_no_public_comment_contains_a_tag_in_disguise(self):
        """
        Django's lexer matches ``{% … %}`` before it matches ``{# … #}``, so a
        brace pair written inside a template comment is not documentation, it is
        a live tag. This cost the public shell a second ``{% extends %}`` once
        already, and every page 500ed.
        """
        disguised = []
        for path in PUBLIC_TEMPLATES:
            text = path.read_text(encoding="utf-8")
            for comment in COMMENT_RE.finditer(text):
                body = comment.group()
                if "{%" in body or "{{" in body:
                    disguised.append(f"{path.name}: {body.strip()[:70]!r}")
        if disguised:
            self.fail(
                "A template comment contains what the lexer will read as a tag "
                "(spell it without the braces):\n" + "\n".join(f"  {d}" for d in disguised)
            )

    def test_no_comment_spans_lines_where_a_visitor_would_read_it(self):
        """
        ``{# … #}`` closes at the first ``#}`` on the *same line*. A comment that
        breaks onto a second line is not a comment: Django treats the text as
        content and sends it to the browser, which is how the footer's own
        docstring appeared on all five public pages.
        """
        leaking = []
        for path in SHELL_TEMPLATES:
            for line_number, line in enumerate(
                path.read_text(encoding="utf-8").splitlines(), start=1
            ):
                opens = line.count("{#")
                closes = line.count("#}")
                if opens and opens != closes:
                    leaking.append(f"{path.name}:{line_number}: {line.strip()[:60]}")
        if leaking:
            self.fail(
                "A template comment is left open at the end of its line, so it "
                "renders as page text — use {% comment %}…{% endcomment %}:\n"
                + "\n".join(f"  {item}" for item in leaking)
            )

    def test_public_templates_never_read_a_private_field(self):
        """
        Only the expressions and tags are searched, not the prose: the About page
        may explain that fees are billed by term, but no ``{{ invoice.balance }}``
        may ever appear on a page a stranger can open.
        """
        offenders = []
        for path in PUBLIC_TEMPLATES:
            text = _without_comments(path).lower()
            for tag in TAG_RE.findall(text):
                for token in PRIVATE_TOKENS:
                    if token in tag:
                        offenders.append(f"{path.name} → {token!r} in {tag.strip()[:80]}")
        if offenders:
            self.fail(
                "A public page reaches for private data:\n"
                + "\n".join(f"  {o}" for o in offenders)
            )

    def test_nav_lists_every_public_page_once(self):
        self.assertEqual([name for name, _ in selectors.PUBLIC_NAV], PAGE_URL_NAMES)
        labels = [label for _, label in selectors.PUBLIC_NAV]
        self.assertEqual(len(labels), len(set(labels)), "duplicate nav label")

    def test_nav_marks_only_the_current_page_active(self):
        links = selectors.nav_links(SimpleRequest("/about/"))
        self.assertEqual([link["label"] for link in links if link["is_active"]], ["About"])

    def test_the_home_page_shows_all_five_links_in_order(self):
        set_settings(name="Greenfield Academy")
        body = self.client.get(reverse("public:home")).content.decode()
        positions = [body.index(reverse(f"public:{name}")) for name in PAGE_URL_NAMES]
        self.assertEqual(positions, sorted(positions))

    def test_the_public_academics_page_does_not_shadow_the_staff_app(self):
        """/academics/ belongs to the staff app; the public page is /programme/."""
        self.assertEqual(reverse("public:academics"), "/programme/")
        self.assertEqual(reverse("academics:year_list"), "/academics/")


class SimpleRequest:
    def __init__(self, path):
        self.path = path


class IdentityTest(TestCase):
    @override_settings(SCHOOL_NAME="My School", SCHOOL_MOTTO="Excellence in Education")
    def test_blank_settings_fall_back_to_the_environment(self):
        set_settings(name="", motto="", address="")
        identity = selectors.site_identity()
        self.assertEqual(identity["name"], "My School")
        self.assertEqual(identity["motto"], "Excellence in Education")
        self.assertEqual(identity["address"], "")

    def test_entered_settings_win(self):
        set_settings(name="Greenfield Academy", address="14 Ridge Road\nAccra")
        identity = selectors.site_identity()
        self.assertEqual(identity["name"], "Greenfield Academy")
        self.assertEqual(identity["address"], "14 Ridge Road\nAccra")

    def test_the_settings_row_is_read_once_per_request_not_once_per_call(self):
        """
        A page asks for the school's identity more than once — the context
        processor, the shell, and the Contact view mailing an enquiry. Without a
        request there is nothing to memoise on and each call reads; with one, the
        shared values and the website-only fields cost one primary-key read on a
        singleton row for the whole page.
        """
        set_settings(name="Greenfield Academy")
        with CaptureQueriesContext(connections["default"]) as ctx:
            selectors.site_identity()
        uncached = len(ctx)
        with CaptureQueriesContext(connections["default"]) as ctx:
            selectors.site_identity()
            selectors.site_identity()
        self.assertEqual(
            len(ctx), uncached * 2, "no request means no memo, so each call reads"
        )

        request = SimpleRequest("/home/")
        with CaptureQueriesContext(connections["default"]) as ctx:
            selectors.site_identity(request)
            first = len(ctx.captured_queries)
            selectors.site_identity(request)
            selectors.site_identity(request)
        self.assertEqual(first, 1, "the values and the row are one read")
        self.assertEqual(
            len(ctx.captured_queries),
            first,
            "the per-request memo was skipped: a page re-read the settings row",
        )

    def test_the_logo_only_appears_once_a_file_is_attached(self):
        with override_settings(MEDIA_ROOT=MEDIA_TMP):
            set_settings(name="Greenfield Academy")
            body = self.client.get(reverse("public:home")).content.decode()
            self.assertNotIn("pub-nav__logo", body)
            self.assertNotIn("pub-footer__logo", body)

            row = set_settings()
            row.logo = upload("crest.png")
            row.save()
            body = self.client.get(reverse("public:home")).content.decode()
            self.assertIn("pub-nav__logo", body)
            self.assertIn("pub-footer__logo", body)
            self.assertIn(row.logo.url, body)


class GalleryModelTest(TestCase):
    @override_settings(MEDIA_ROOT=MEDIA_TMP)
    def test_ordering_and_publish_flag_round_trip(self):
        photo = make_photo(title="Speech day", display_order=7, is_published=False)
        photo.refresh_from_db()
        self.assertEqual(photo.display_order, 7)
        self.assertFalse(photo.is_published)
        self.assertEqual(selectors.published_photos(), [])

    def test_enquiries_are_newest_first(self):
        """``created_at`` is set by the clock, so the two rows are moved apart."""
        first = ContactMessage.objects.create(
            name="Ama", email="ama@test.school", message="first"
        )
        second = ContactMessage.objects.create(
            name="Kwame", email="kwame@test.school", message="second"
        )
        now = timezone.now()
        ContactMessage.objects.filter(pk=first.pk).update(created_at=now - timedelta(hours=1))
        ContactMessage.objects.filter(pk=second.pk).update(created_at=now)
        self.assertEqual(
            [enquiry.name for enquiry in ContactMessage.objects.all()],
            ["Kwame", "Ama"],
        )
