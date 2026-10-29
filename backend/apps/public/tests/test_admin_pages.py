"""
apps/public/tests/test_admin_pages.py
-------------------------------------
P4: the Website section of the Admin Portal — gallery management and the
enquiry inbox.

Four things are pinned here, because each is a way this screen could quietly
go wrong:

  * **Only admin edits the public face.** Who may reach these pages is the
    matrix in ``apps/core/tests/test_security_matrix.py``; what is checked here
    is that a rejected role's ``POST`` changes nothing at all, not merely that
    it gets a 403.
  * **An empty file box is not a delete.** Django reads a posted ``None`` on a
    file input as *nobody picked a file* — saving a photograph's caption must
    never wipe its image.
  * **Every mutation is audited, and the audit stays inside its lane.** A
    create logs what the row was born with, an edit logs only what moved, a
    no-op logs nothing, and an enquiry's audit row carries the sender's name
    and address but not their letter.
  * **Destructive routes are POST-only**, so a crawler or a link pre-fetch
    cannot delete a photograph.
"""

from __future__ import annotations

import re

from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connections
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from apps.core.models import AuditLog
from apps.dashboards.tests.fixtures import make_user

from ..models import ContactMessage, GalleryPhoto
from .fixtures import MEDIA_TMP, make_photo, upload


def post_photo(client, url: str, *, title: str, image=None, **extra):
    """
    Submit the photograph form.

    ``image`` goes in the data dict because that is where the test client puts
    uploads — there is no ``files=`` argument.
    """
    data = {"title": title, "caption": "", "display_order": "0", **extra}
    if image is not None:
        data["image"] = image
    return client.post(url, data)


@override_settings(MEDIA_ROOT=MEDIA_TMP)
class GalleryManagementTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = make_user("admin", username="website_admin")
        cls.director = make_user("director", username="website_director")
        cls.photo = make_photo(title="Sports day", display_order=1)
        cls.enquiry = ContactMessage.objects.create(
            name="Visitor",
            email="visitor@example.test",
            message="Please call me back.",
        )

    def setUp(self):
        self.client.force_login(self.admin)

    # ── adding ────────────────────────────────────────────────────────────
    def test_admin_uploads_a_photograph_and_it_is_audited(self):
        response = post_photo(
            self.client,
            reverse("public_admin:gallery_add"),
            title="Speech day",
            image=upload("speech.png"),
            display_order="3",
            is_published="on",
        )
        self.assertEqual(response.status_code, 302)
        photo = GalleryPhoto.objects.get(title="Speech day")
        self.assertEqual(photo.display_order, 3)
        self.assertTrue(photo.is_published)
        self.assertTrue(photo.image.name, "the uploaded file is on the row")

        entry = AuditLog.objects.get(action="public.gallery_photo_created")
        self.assertEqual(entry.actor, self.admin)
        self.assertEqual(entry.content_type, "public.GalleryPhoto")
        self.assertEqual(entry.object_id, str(photo.pk))
        self.assertEqual(
            entry.change_summary,
            {
                "title": "Speech day",
                "image": photo.image.name,
                "display_order": 3,
                "is_published": True,
            },
        )

    def test_a_photograph_cannot_be_added_without_an_image(self):
        response = post_photo(
            self.client,
            reverse("public_admin:gallery_add"),
            title="Nothing to show",
        )
        self.assertEqual(response.status_code, 200)
        self.assertFormError(
            response.context["form"], "image", "This field is required."
        )
        self.assertFalse(GalleryPhoto.objects.filter(title="Nothing to show").exists())

    def test_a_file_that_is_not_an_image_is_rejected(self):
        response = post_photo(
            self.client,
            reverse("public_admin:gallery_add"),
            title="Homework list",
            image=SimpleUploadedFile("list.txt", b"not a picture", content_type="text/plain"),
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("image", response.context["form"].errors)
        self.assertFalse(GalleryPhoto.objects.filter(title="Homework list").exists())

    def test_a_blank_title_is_rejected_rather_than_stored(self):
        response = self.client.post(
            reverse("public_admin:gallery_add"),
            {"title": "   ", "caption": "", "display_order": "0", "image": upload()},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(GalleryPhoto.objects.count(), 1)

    # ── editing ───────────────────────────────────────────────────────────
    def test_editing_the_caption_keeps_the_photograph(self):
        """The whole point of the ``apply_cleaned_data`` rule, seen from a page."""
        before = self.photo.image.name
        response = self.client.post(
            reverse("public_admin:gallery_edit", args=[self.photo.pk]),
            {
                "title": "Sports day",
                "caption": "Under the mango tree",
                "display_order": "1",
                "is_published": "on",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.photo.refresh_from_db()
        self.assertEqual(self.photo.caption, "Under the mango tree")
        self.assertEqual(self.photo.image.name, before)

    def test_choosing_a_file_replaces_the_photograph(self):
        before = self.photo.image.name
        response = post_photo(
            self.client,
            reverse("public_admin:gallery_edit", args=[self.photo.pk]),
            title="Sports day",
            image=upload("new.png"),
            display_order="1",
            is_published="on",
        )
        self.assertEqual(response.status_code, 302)
        self.photo.refresh_from_db()
        self.assertNotEqual(self.photo.image.name, before)

        entry = AuditLog.objects.get(action="public.gallery_photo_updated")
        # The log keeps the name the file was posted under, not the path it was
        # filed to when the row saved — "new.png", not "gallery/new.png". It is
        # the same asymmetry School Settings has always had: the "from" is a
        # stored path and the "to" is what the admin picked.
        self.assertEqual(entry.change_summary["image"]["from"], before)
        self.assertTrue(entry.change_summary["image"]["to"].endswith("new.png"))
        self.assertNotIn("title", entry.change_summary, "the title did not move")

    def test_saving_without_changing_anything_writes_no_audit_row(self):
        post_photo(
            self.client,
            reverse("public_admin:gallery_edit", args=[self.photo.pk]),
            title="Sports day",
            display_order="1",
            is_published="on",
        )
        self.assertFalse(
            AuditLog.objects.filter(action="public.gallery_photo_updated").exists()
        )

    def test_the_edit_page_shows_a_published_photograph_as_checked(self):
        """
        The checkbox is what keeps a photograph live.

        An unchecked box posts nothing, and the form reads that as *hide it* —
        so if the page ever stopped rendering the box checked, editing a caption
        would silently take the photo off the website.
        """
        page = self.client.get(
            reverse("public_admin:gallery_edit", args=[self.photo.pk])
        ).content.decode()
        box = re.search(r"<input[^>]*is_published[^>]*>", page).group()
        self.assertIn("checked", box, box)

    def test_unchecking_the_box_hides_the_photograph(self):
        response = post_photo(
            self.client,
            reverse("public_admin:gallery_edit", args=[self.photo.pk]),
            title="Sports day",
            display_order="1",
        )
        self.assertEqual(response.status_code, 302)
        self.photo.refresh_from_db()
        self.assertFalse(self.photo.is_published)

    def test_editing_a_missing_photograph_is_404(self):
        response = self.client.get(reverse("public_admin:gallery_edit", args=[9999]))
        self.assertEqual(response.status_code, 404)

    # ── publishing ────────────────────────────────────────────────────────
    def test_hiding_a_photograph_takes_it_off_the_public_page(self):
        response = self.client.post(
            reverse("public_admin:gallery_publish", args=[self.photo.pk]),
            {"action": "unpublish"},
        )
        self.assertEqual(response.status_code, 302)
        self.photo.refresh_from_db()
        self.assertFalse(self.photo.is_published)

        # Read the rows the page was given, not its text: the confirmation that
        # the photo was hidden names it, and a visitor sees that message too.
        public = self.client.get(reverse("public:gallery"))
        self.assertNotIn(self.photo.pk, [row.pk for row in public.context["photos"]])

        entry = AuditLog.objects.get(action="public.gallery_photo_updated")
        self.assertEqual(
            entry.change_summary, {"is_published": {"from": True, "to": False}}
        )

    def test_publishing_it_again_brings_it_back(self):
        self.photo.is_published = False
        self.photo.save(update_fields=["is_published"])
        self.client.post(
            reverse("public_admin:gallery_publish", args=[self.photo.pk]),
            {"action": "publish"},
        )
        self.photo.refresh_from_db()
        self.assertTrue(self.photo.is_published)
        public = self.client.get(reverse("public:gallery"))
        self.assertIn(self.photo.pk, [row.pk for row in public.context["photos"]])

    def test_the_toggle_redirects_back_where_the_admin_came_from(self):
        response = self.client.post(
            reverse("public_admin:gallery_publish", args=[self.photo.pk]),
            {"action": "unpublish", "next": reverse("public_admin:gallery_list")},
        )
        self.assertEqual(response["Location"], reverse("public_admin:gallery_list"))

    # ── deleting ──────────────────────────────────────────────────────────
    def test_deleting_removes_the_row_and_logs_the_title(self):
        response = self.client.post(
            reverse("public_admin:gallery_delete", args=[self.photo.pk])
        )
        self.assertEqual(response.status_code, 302)
        self.assertFalse(GalleryPhoto.objects.filter(pk=self.photo.pk).exists())
        entry = AuditLog.objects.get(action="public.gallery_photo_deleted")
        self.assertEqual(entry.change_summary, {"title": "Sports day"})

    def test_destructive_routes_reject_a_get(self):
        for name, args in [
            ("public_admin:gallery_delete", [self.photo.pk]),
            ("public_admin:gallery_publish", [self.photo.pk]),
            ("public_admin:enquiry_handled", [self.enquiry.pk]),
            ("public_admin:enquiry_delete", [self.enquiry.pk]),
        ]:
            response = self.client.get(reverse(name, args=args))
            self.assertEqual(response.status_code, 405, name)
        self.assertTrue(GalleryPhoto.objects.filter(pk=self.photo.pk).exists())

    # ── the list page ─────────────────────────────────────────────────────
    def test_the_list_shows_hidden_photographs_too(self):
        make_photo(title="Not for the web", is_published=False)
        page = self.client.get(reverse("public_admin:gallery_list")).content.decode()
        self.assertIn("Sports day", page)
        self.assertIn("Not for the web", page)
        self.assertIn("Hidden", page)

    def test_a_role_outside_admin_changes_nothing(self):
        self.client.force_login(self.director)
        for name, args, data in [
            ("public_admin:gallery_delete", [self.photo.pk], {}),
            ("public_admin:gallery_publish", [self.photo.pk], {"action": "unpublish"}),
            ("public_admin:enquiry_delete", [self.enquiry.pk], {}),
        ]:
            response = self.client.post(reverse(name, args=args), data)
            self.assertEqual(response.status_code, 403, name)
        self.assertTrue(GalleryPhoto.objects.filter(pk=self.photo.pk).exists())
        self.assertTrue(ContactMessage.objects.filter(pk=self.enquiry.pk).exists())
        self.photo.refresh_from_db()
        self.assertTrue(self.photo.is_published)
        self.assertFalse(AuditLog.objects.exists())


class EnquiryInboxTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = make_user("admin", username="inbox_admin")
        cls.teacher = make_user("teacher", username="inbox_teacher")
        cls.open_one = ContactMessage.objects.create(
            name="Asiedu Family",
            email="asiedu@example.test",
            phone="+233 20 000 0000",
            message="We would like to visit next Tuesday.",
        )
        cls.handled_one = ContactMessage.objects.create(
            name="Kofi Mensah",
            email="kofi@example.test",
            message="Is the uniform available in blue?",
            handled=True,
            handled_at=timezone.now(),
        )

    def setUp(self):
        self.client.force_login(self.admin)

    def test_marking_handled_stamps_the_time_and_logs_it(self):
        response = self.client.post(
            reverse("public_admin:enquiry_handled", args=[self.open_one.pk]),
            {"action": "handled"},
        )
        self.assertEqual(response.status_code, 302)
        self.open_one.refresh_from_db()
        self.assertTrue(self.open_one.handled)
        self.assertIsNotNone(self.open_one.handled_at)

        entry = AuditLog.objects.get(action="public.enquiry_handled")
        self.assertEqual(entry.actor, self.admin)
        self.assertEqual(entry.change_summary, {"handled": {"from": False, "to": True}})

    def test_reopening_clears_the_time_and_logs_the_other_word(self):
        self.client.post(
            reverse("public_admin:enquiry_handled", args=[self.handled_one.pk]),
            {"action": "reopen"},
        )
        self.handled_one.refresh_from_db()
        self.assertFalse(self.handled_one.handled)
        self.assertIsNone(self.handled_one.handled_at)
        self.assertTrue(
            AuditLog.objects.filter(action="public.enquiry_reopened").exists()
        )

    def test_ticking_an_already_ticked_box_is_not_an_event(self):
        self.client.post(
            reverse("public_admin:enquiry_handled", args=[self.handled_one.pk]),
            {"action": "handled"},
        )
        self.assertFalse(
            AuditLog.objects.filter(content_type="public.ContactMessage").exists()
        )

    def test_the_filter_shows_only_the_rows_it_promises(self):
        rows = {
            status: [
                row.name
                for row in self.client.get(
                    reverse("public_admin:enquiry_list"), {"status": status}
                ).context["enquiries"]
            ]
            for status in ("all", "open", "handled")
        }
        self.assertEqual(rows["all"], ["Kofi Mensah", "Asiedu Family"])
        self.assertEqual(rows["open"], ["Asiedu Family"])
        self.assertEqual(rows["handled"], ["Kofi Mensah"])

    def test_a_junk_filter_falls_back_instead_of_erroring(self):
        response = self.client.get(
            reverse("public_admin:enquiry_list"), {"status": "banana"}
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.context["enquiries"]), 2)

    def test_the_search_finds_a_word_in_the_message_not_just_the_name(self):
        response = self.client.get(
            reverse("public_admin:enquiry_list"), {"q": "uniform"}
        )
        names = [row.name for row in response.context["enquiries"]]
        self.assertEqual(names, ["Kofi Mensah"])

    def test_the_counts_match_the_tables(self):
        context = self.client.get(reverse("public_admin:enquiry_list")).context
        self.assertEqual(context["counts"], {"total": 2, "open": 1})

    def test_deleting_keeps_the_sender_out_of_the_message_body(self):
        response = self.client.post(
            reverse("public_admin:enquiry_delete", args=[self.open_one.pk])
        )
        self.assertEqual(response.status_code, 302)
        self.assertFalse(ContactMessage.objects.filter(pk=self.open_one.pk).exists())

        entry = AuditLog.objects.get(action="public.enquiry_deleted")
        self.assertEqual(
            list(entry.change_summary), ["name", "email"], "no body in the log"
        )
        self.assertEqual(
            entry.change_summary,
            {"name": "Asiedu Family", "email": "asiedu@example.test"},
        )

    def test_pagination_links_carry_the_filter_forward(self):
        for n in range(31):
            ContactMessage.objects.create(
                name=f"Family {n}", email=f"f{n}@example.test", message="hello"
            )
        page = self.client.get(
            reverse("public_admin:enquiry_list"), {"status": "open", "page": 2}
        ).content.decode()
        self.assertIn("status=open&amp;page=1", page)

    def test_a_role_outside_admin_cannot_read_the_inbox(self):
        self.client.force_login(self.teacher)
        response = self.client.get(reverse("public_admin:enquiry_list"))
        self.assertEqual(response.status_code, 403)


class WebsitePageQueryBudgetTests(TestCase):
    """
    The admin reads these two lists on every visit, so each is held to the
    measured count plus two — the same rule the public pages keep.
    """

    #: Measured against the fixture below: gallery 6, enquiries 8 — most of
    #: which is the shared admin shell rather than the table being listed.
    CEILINGS = {"public_admin:gallery_list": 8, "public_admin:enquiry_list": 10}

    @classmethod
    def setUpTestData(cls):
        cls.admin = make_user("admin", username="budget_admin")
        # Name-only images: this test counts queries, and a real upload would
        # write files the run has no interest in.
        for order in range(4):
            GalleryPhoto.objects.create(
                title=f"Photo {order}",
                image=f"gallery/budget-{order}.png",
                display_order=order,
            )
        for n in range(4):
            ContactMessage.objects.create(
                name=f"Family {n}", email=f"f{n}@example.test", message="hello"
            )

    def test_each_page_stays_inside_its_budget(self):
        self.client.force_login(self.admin)
        over = []
        for url_name, ceiling in self.CEILINGS.items():
            with CaptureQueriesContext(connections["default"]) as ctx:
                status = self.client.get(reverse(url_name)).status_code
            self.assertEqual(status, 200, url_name)
            if len(ctx) > ceiling:
                over.append(
                    f"  {url_name}: {len(ctx)} queries, ceiling {ceiling}\n"
                    + "\n".join(f"    {query['sql'][:110]}" for query in ctx)
                )
        if over:
            self.fail("A Website management page out-grew its budget:\n" + "\n".join(over))
