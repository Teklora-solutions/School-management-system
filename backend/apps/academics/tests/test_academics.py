"""
apps/academics/tests/test_academics.py
----------------------------------------
Module 1 — academic structure invariants, plus the A1 uniformity rules:
the year picker, the three terms it provisions, and the guarded year delete.

Covers:
  - the shared calendar's year and term windows
  - only one current academic year at a time
  - only one current term at a time
  - a created year arrives with exactly three terms
  - a year cannot be deleted while it is current or still pointed at
  - classes with optional streams
  - class–subject mapping uniqueness
"""

from __future__ import annotations

import datetime

from django.core.exceptions import ValidationError
from django.test import SimpleTestCase, TestCase
from django.urls import reverse

from apps.dashboards.tests.fixtures import make_user
from apps.finance.models import FeeStructure

from .. import calendar
from ..forms import AcademicYearCreateForm, TermForm
from ..models import AcademicYear, ClassSubject, SchoolClass, Stream, Term
from ..services import (
    create_academic_year,
    create_academic_year_with_terms,
    create_class,
    create_subject,
    create_term,
    delete_academic_year,
    map_class_subject,
)


class CalendarTests(SimpleTestCase):
    """Pure date maths — the picker's promise is checkable without a database."""

    def test_term_slots_match_the_term_model_choices(self):
        self.assertEqual(list(calendar.TERM_SLOTS), list(Term.TermName.values))

    def test_selectable_years_run_from_the_running_year_to_2030(self):
        # October sits inside the 2026/2027 year.
        self.assertEqual(calendar.current_start_year(datetime.date(2026, 10, 21)), 2026)
        # March is still the year that opened the previous September.
        self.assertEqual(calendar.current_start_year(datetime.date(2026, 3, 1)), 2025)
        years = calendar.selectable_start_years(datetime.date(2026, 10, 21))
        self.assertEqual(years, [2026, 2027, 2028, 2029, 2030])

    def test_year_name_and_dates(self):
        self.assertEqual(calendar.year_name(2026), "2026/2027")
        self.assertEqual(
            calendar.year_dates(2026),
            (datetime.date(2026, 9, 1), datetime.date(2027, 7, 31)),
        )

    def test_three_term_windows(self):
        self.assertEqual(
            calendar.term_windows(2026),
            [
                ("term_1", datetime.date(2026, 9, 1), datetime.date(2026, 12, 20)),
                ("term_2", datetime.date(2027, 1, 11), datetime.date(2027, 4, 10)),
                ("term_3", datetime.date(2027, 5, 10), datetime.date(2027, 7, 31)),
            ],
        )

    def test_slot_running_on_finds_the_live_window(self):
        self.assertEqual(
            calendar.slot_running_on(2026, datetime.date(2026, 10, 21)), "term_1"
        )
        # The long holidays belong to no term.
        self.assertIsNone(calendar.slot_running_on(2026, datetime.date(2026, 8, 15)))

    def test_parse_start_year_flags_legacy_names(self):
        self.assertEqual(calendar.parse_start_year("2026/2027"), 2026)
        for legacy in ("1000", "3958", "2026/2028", "", "  "):
            self.assertIsNone(calendar.parse_start_year(legacy), legacy)

    def test_model_reports_whether_a_year_came_from_the_picker(self):
        shaped = AcademicYear(name="2026/2027")
        legacy = AcademicYear(name="3958")
        self.assertTrue(shaped.from_calendar_picker)
        self.assertFalse(legacy.from_calendar_picker)


class YearProvisioningTests(TestCase):
    def test_creating_a_year_creates_its_three_terms(self):
        year = create_academic_year_with_terms(
            start_year=2028, today=datetime.date(2026, 10, 21)
        )
        self.assertEqual(year.name, "2028/2029")
        self.assertEqual(
            [term.name for term in year.terms.order_by("start_date")],
            ["term_1", "term_2", "term_3"],
        )
        # A future year has no term running yet, so nothing steals the flag.
        self.assertEqual(Term.objects.filter(is_current=True).count(), 0)

    def test_current_year_marks_the_running_term(self):
        year = create_academic_year_with_terms(
            start_year=2026, is_current=True, today=datetime.date(2026, 10, 21)
        )
        self.assertTrue(year.is_current)
        current = Term.objects.filter(is_current=True)
        self.assertEqual(current.count(), 1)
        self.assertEqual(current.first(), year.terms.get(name="term_1"))

    def test_the_year_the_admin_picks_cannot_be_duplicated(self):
        create_academic_year_with_terms(start_year=2027, today=datetime.date(2026, 10, 21))
        with self.assertRaises(ValidationError):
            create_academic_year_with_terms(
                start_year=2027, today=datetime.date(2026, 10, 21)
            )
        self.assertEqual(AcademicYear.objects.count(), 1)


class AcademicYearCreateFormTests(TestCase):
    def test_years_already_created_are_not_offered(self):
        create_academic_year(
            name="2026/2027",
            start_date=datetime.date(2026, 9, 1),
            end_date=datetime.date(2027, 7, 31),
        )
        form = AcademicYearCreateForm(today=datetime.date(2026, 10, 21))
        self.assertEqual(
            [value for value, _ in form.fields["start_year"].choices],
            ["2027", "2028", "2029", "2030"],
        )

    def test_only_picker_years_are_accepted(self):
        form = AcademicYearCreateForm(
            {"start_year": "2031", "is_current": False},
            today=datetime.date(2026, 10, 21),
        )
        self.assertFalse(form.is_valid())

    def test_is_current_is_pre_ticked_while_no_year_exists(self):
        form = AcademicYearCreateForm(today=datetime.date(2026, 10, 21))
        self.assertIs(form.fields["is_current"].initial, True)

    def test_a_term_slot_already_used_is_rejected(self):
        year = create_academic_year_with_terms(
            start_year=2029, today=datetime.date(2026, 10, 21)
        )
        form = TermForm(
            {
                "academic_year": year.pk,
                "name": "term_1",
                "start_date": "2029-09-01",
                "end_date": "2029-12-20",
            }
        )
        self.assertFalse(form.is_valid())
        self.assertIn("already has Term 1", form.errors["name"][0])

    def test_a_missing_slot_is_still_offered(self):
        year = create_academic_year(
            name="1000",
            start_date=datetime.date(2026, 10, 19),
            end_date=datetime.date(2027, 5, 20),
        )
        create_term(
            academic_year=year,
            name="term_1",
            start_date=datetime.date(2026, 10, 19),
            end_date=datetime.date(2026, 11, 21),
        )
        form = TermForm(year=year)
        self.assertEqual(
            [value for value, _ in form.fields["name"].choices],
            ["term_2", "term_3"],
        )


class YearDeletionTests(TestCase):
    def setUp(self):
        self.year = create_academic_year_with_terms(
            start_year=2029, today=datetime.date(2026, 10, 21)
        )

    def test_current_year_cannot_be_deleted(self):
        self.year.is_current = True
        self.year.save()
        with self.assertRaises(ValidationError):
            delete_academic_year(year=self.year)
        self.assertTrue(AcademicYear.objects.filter(pk=self.year.pk).exists())

    def test_empty_year_takes_its_terms_and_classes_with_it(self):
        create_class(academic_year=self.year, name="Grade 8", level_order=8)
        year_pk = self.year.pk
        delete_academic_year(year=self.year)
        self.assertFalse(AcademicYear.objects.filter(pk=year_pk).exists())
        self.assertEqual(Term.objects.filter(academic_year_id=year_pk).count(), 0)
        self.assertEqual(
            SchoolClass.objects.filter(academic_year_id=year_pk).count(), 0
        )

    def test_year_with_a_fee_structure_is_protected(self):
        school_class = create_class(
            academic_year=self.year, name="Grade 9", level_order=9
        )
        FeeStructure.objects.create(
            school_class=school_class, term=self.year.terms.first()
        )
        with self.assertRaises(ValidationError):
            delete_academic_year(year=self.year)
        self.assertTrue(AcademicYear.objects.filter(pk=self.year.pk).exists())
        self.assertTrue(SchoolClass.objects.filter(pk=school_class.pk).exists())


class CurrentYearTermTests(TestCase):
    def test_only_one_current_academic_year(self):
        year_1 = create_academic_year(
            name="2024/2025",
            start_date=datetime.date(2024, 9, 1),
            end_date=datetime.date(2025, 7, 31),
            is_current=True,
        )
        year_2 = create_academic_year(
            name="2025/2026",
            start_date=datetime.date(2025, 9, 1),
            end_date=datetime.date(2026, 7, 31),
            is_current=True,
        )

        year_1.refresh_from_db()
        year_2.refresh_from_db()
        self.assertFalse(year_1.is_current)
        self.assertTrue(year_2.is_current)
        self.assertEqual(AcademicYear.objects.filter(is_current=True).count(), 1)

    def test_only_one_current_term_across_years(self):
        year_1 = create_academic_year(
            name="2024/2025",
            start_date=datetime.date(2024, 9, 1),
            end_date=datetime.date(2025, 7, 31),
        )
        year_2 = create_academic_year(
            name="2025/2026",
            start_date=datetime.date(2025, 9, 1),
            end_date=datetime.date(2026, 7, 31),
        )
        term_1 = create_term(
            academic_year=year_1,
            name="term_1",
            start_date=datetime.date(2024, 9, 1),
            end_date=datetime.date(2024, 12, 20),
            is_current=True,
        )
        term_2 = create_term(
            academic_year=year_2,
            name="term_1",
            start_date=datetime.date(2025, 9, 1),
            end_date=datetime.date(2025, 12, 20),
            is_current=True,
        )

        term_1.refresh_from_db()
        term_2.refresh_from_db()
        self.assertFalse(term_1.is_current)
        self.assertTrue(term_2.is_current)
        self.assertEqual(Term.objects.filter(is_current=True).count(), 1)

    def test_invalid_dates_are_rejected(self):
        with self.assertRaises(ValidationError):
            create_academic_year(
                name="Bad Year",
                start_date=datetime.date(2026, 1, 1),
                end_date=datetime.date(2025, 1, 1),
            )


class AcademicStructureTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.year = AcademicYear.objects.create(
            name="2025/2026",
            start_date=datetime.date(2025, 9, 1),
            end_date=datetime.date(2026, 7, 31),
        )

    def test_class_can_have_optional_streams(self):
        school_class = create_class(
            academic_year=self.year, name="Grade 5", level_order=5
        )
        self.assertEqual(school_class.streams.count(), 0)

        stream = Stream.objects.create(school_class=school_class, name="Blue")
        self.assertEqual(list(school_class.streams.all()), [stream])

    def test_create_subject_normalises_code(self):
        subject = create_subject(name="Mathematics", code=" math ")
        self.assertEqual(subject.code, "MATH")

    def test_duplicate_subject_code_rejected(self):
        create_subject(name="Mathematics", code="MATH")
        with self.assertRaises(ValidationError):
            create_subject(name="Further Mathematics", code="MATH")

    def test_class_subject_mapping_is_unique(self):
        school_class = create_class(
            academic_year=self.year, name="Grade 6", level_order=6
        )
        subject = create_subject(name="English", code="ENG")

        map_class_subject(school_class=school_class, subject=subject)
        with self.assertRaises(ValidationError):
            map_class_subject(school_class=school_class, subject=subject)

        # A different stream is allowed.
        stream = Stream.objects.create(school_class=school_class, name="Gold")
        mapping = map_class_subject(
            school_class=school_class, subject=subject, stream=stream
        )
        self.assertEqual(mapping.stream, stream)
        self.assertEqual(
            ClassSubject.objects.filter(
                school_class=school_class, subject=subject
            ).count(),
            2,
        )


class YearPickerViewTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        # make_user clears must_change_password, which otherwise sends every
        # request through the forced password-change redirect.
        cls.admin = make_user("admin", username="acad_admin")
        cls.teacher = make_user("teacher", username="acad_teacher")

    def test_add_page_offers_a_year_dropdown_and_no_free_text_name(self):
        self.client.force_login(self.admin)
        page = self.client.get(reverse("academics:year_create"))
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, 'name="start_year"')
        self.assertNotContains(page, 'name="name"')
        self.assertContains(page, "2026/2027")

    def test_picking_a_year_creates_it_with_three_terms(self):
        self.client.force_login(self.admin)
        response = self.client.post(
            reverse("academics:year_create"),
            {"start_year": "2029", "is_current": "on"},
        )
        year = AcademicYear.objects.get(name="2029/2030")
        self.assertRedirects(response, reverse("academics:year_detail", args=[year.pk]))
        self.assertEqual(year.terms.count(), 3)
        self.assertTrue(year.is_current)

    def test_a_non_admin_cannot_create_a_year(self):
        self.client.force_login(self.teacher)
        response = self.client.post(
            reverse("academics:year_create"), {"start_year": "2029"}
        )
        self.assertEqual(response.status_code, 403)
        self.assertFalse(AcademicYear.objects.exists())

    def test_term_dates_are_editable_from_the_year_page(self):
        self.client.force_login(self.admin)
        year = create_academic_year_with_terms(
            start_year=2029, today=datetime.date(2026, 10, 21)
        )
        term = year.terms.get(name="term_2")
        self.client.post(
            reverse("academics:term_update", args=[term.pk]),
            {"start_date": "2028-02-01", "end_date": "2028-04-28"},
        )
        term.refresh_from_db()
        self.assertEqual(term.start_date, datetime.date(2028, 2, 1))
        self.assertEqual(term.end_date, datetime.date(2028, 4, 28))

    def test_bad_term_dates_are_rejected_not_saved(self):
        self.client.force_login(self.admin)
        year = create_academic_year_with_terms(
            start_year=2029, today=datetime.date(2026, 10, 21)
        )
        term = year.terms.get(name="term_1")
        self.client.post(
            reverse("academics:term_update", args=[term.pk]),
            {"start_date": "2029-12-01", "end_date": "2029-09-01"},
        )
        term.refresh_from_db()
        self.assertEqual(term.start_date, datetime.date(2029, 9, 1))

    def test_deleting_a_year_removes_its_scaffolding(self):
        self.client.force_login(self.admin)
        year = create_academic_year_with_terms(
            start_year=2030, today=datetime.date(2026, 10, 21)
        )
        response = self.client.post(
            reverse("academics:year_delete", args=[year.pk])
        )
        self.assertRedirects(response, reverse("academics:year_list"))
        self.assertFalse(AcademicYear.objects.filter(pk=year.pk).exists())

    def test_years_list_flags_a_legacy_name(self):
        self.client.force_login(self.admin)
        create_academic_year(
            name="3958",
            start_date=datetime.date(2026, 10, 21),
            end_date=datetime.date(2027, 3, 21),
        )
        page = self.client.get(reverse("academics:year_list"))
        self.assertContains(page, "Legacy")
