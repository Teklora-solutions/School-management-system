"""
apps/attendance/tests/test_selectors.py
---------------------------------------
The numbers the Director portal will render. What is being pinned here is the
*rate convention* — late counts as present, excused counts as absent — and the
query cost, which is what makes a per-class or per-weekday report safe to put on
one page.

Dates are chosen from a known week: 2025-09-01 is a Monday, 2025-09-05 a Friday.
"""

from __future__ import annotations

import datetime

from django.test import TestCase

from apps.attendance.models import AttendanceEntry, AttendanceRegister
from apps.attendance.selectors import (
    by_class,
    by_weekday,
    chronic_absences,
    marking_compliance,
    register_for,
    summarise,
)
from apps.dashboards.tests.fixtures import (
    make_class,
    make_student_user,
    make_year,
)

MON = datetime.date(2025, 9, 1)
TUE = datetime.date(2025, 9, 2)
WED = datetime.date(2025, 9, 3)
FRI = datetime.date(2025, 9, 5)


class AttendanceSelectorTestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.year = make_year()
        cls.jhs = make_class(cls.year, name="JHS 1", level_order=7)
        cls.basic5 = make_class(cls.year, name="Basic 5", level_order=5)
        cls.pupils = [
            make_student_user(cls.basic5, admission_number=f"B{i:03d}")
            for i in range(4)
        ]
        cls.jhs_pupils = [
            make_student_user(cls.jhs, admission_number=f"J{i:03d}")
            for i in range(2)
        ]

    def mark(self, school_class, date, statuses):
        """statuses: {student: status} — added to that class's register for the day."""
        register, _ = AttendanceRegister.objects.get_or_create(
            school_class=school_class, date=date
        )
        AttendanceEntry.objects.bulk_create(
            [
                AttendanceEntry(
                    register=register,
                    student=student,
                    status=status,
                    date=date,
                )
                for student, status in statuses.items()
            ]
        )
        return register


class SummariseTests(AttendanceSelectorTestCase):
    def test_late_counts_as_present(self):
        self.mark(
            self.basic5,
            MON,
            {
                self.pupils[0]: "present",
                self.pupils[1]: "late",
                self.pupils[2]: "absent",
                self.pupils[3]: "absent",
            },
        )
        row = summarise(date_from=MON, date_to=MON, school_class=self.basic5)
        self.assertEqual(row["total"], 4)
        self.assertEqual(row["attended"], 2)
        self.assertEqual(row["rate"], 50.0)
        self.assertEqual(row["late"], 1)
        self.assertEqual(row["absent"], 2)

    def test_an_excused_absence_does_not_lift_the_rate(self):
        self.mark(
            self.basic5,
            MON,
            {
                self.pupils[0]: "present",
                self.pupils[1]: "excused",
            },
        )
        row = summarise(date_from=MON, date_to=MON, school_class=self.basic5)
        self.assertEqual(row["excused"], 1)
        self.assertEqual(row["rate"], 50.0)

    def test_no_records_at_all_gives_no_rate_rather_than_zero(self):
        row = summarise(date_from=MON, date_to=FRI)
        self.assertEqual(row["total"], 0)
        self.assertIsNone(row["rate"])

    def test_the_whole_school_without_a_class_counts_every_class(self):
        self.mark(self.basic5, MON, {self.pupils[0]: "present"})
        self.mark(self.jhs, MON, {self.jhs_pupils[0]: "absent"})
        row = summarise(date_from=MON, date_to=MON)
        self.assertEqual(row["total"], 2)
        self.assertEqual(row["attended"], 1)
        self.assertEqual(row["rate"], 50.0)

    def test_a_closed_range_excludes_days_outside_it(self):
        self.mark(self.basic5, MON, {self.pupils[0]: "present"})
        self.mark(self.basic5, FRI, {self.pupils[1]: "absent"})
        row = summarise(date_from=TUE, date_to=TUE)
        self.assertEqual(row["total"], 0)


class ByClassTests(AttendanceSelectorTestCase):
    def test_one_row_per_class_with_its_own_rate(self):
        self.mark(
            self.basic5,
            MON,
            {s: "present" for s in self.pupils},
        )
        self.mark(
            self.jhs,
            MON,
            {self.jhs_pupils[0]: "present", self.jhs_pupils[1]: "absent"},
        )
        rows = by_class(date_from=MON, date_to=FRI)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["class_name"], "Basic 5")
        self.assertEqual(rows[0]["rate"], 100.0)
        self.assertEqual(rows[1]["class_name"], "JHS 1")
        self.assertEqual(rows[1]["rate"], 50.0)

    def test_class_ids_scope_the_query_in_sql(self):
        self.mark(self.basic5, MON, {self.pupils[0]: "present"})
        self.mark(self.jhs, MON, {self.jhs_pupils[0]: "present"})
        rows = by_class(date_from=MON, date_to=FRI, class_ids=[self.jhs.pk])
        self.assertEqual([r["class_name"] for r in rows], ["JHS 1"])

    def test_one_query_regardless_of_class_count(self):
        self.mark(self.basic5, MON, {self.pupils[0]: "present"})
        self.mark(self.jhs, MON, {self.jhs_pupils[0]: "present"})
        with self.assertNumQueries(1):
            list(by_class(date_from=MON, date_to=FRI))


class ByWeekdayTests(AttendanceSelectorTestCase):
    def test_every_weekday_is_returned_even_the_empty_ones(self):
        self.mark(self.basic5, MON, {s: "present" for s in self.pupils})
        rows = by_weekday(date_from=MON, date_to=FRI)
        self.assertEqual([r["label"] for r in rows], [
            "Monday",
            "Tuesday",
            "Wednesday",
            "Thursday",
            "Friday",
            "Saturday",
            "Sunday",
        ])
        monday = rows[0]
        self.assertEqual(monday["total"], 4)
        self.assertEqual(monday["rate"], 100.0)
        for row in rows[1:]:
            self.assertEqual(row["total"], 0)
            self.assertIsNone(row["rate"])

    def test_a_weekday_dip_is_visible(self):
        self.mark(self.basic5, MON, {s: "present" for s in self.pupils})
        self.mark(self.basic5, FRI, {s: "absent" for s in self.pupils})
        rows = {r["label"]: r for r in by_weekday(date_from=MON, date_to=FRI)}
        self.assertEqual(rows["Monday"]["rate"], 100.0)
        self.assertEqual(rows["Friday"]["rate"], 0.0)

    def test_one_query_for_the_whole_week(self):
        self.mark(self.basic5, MON, {self.pupils[0]: "present"})
        with self.assertNumQueries(1):
            by_weekday(date_from=MON, date_to=FRI)


class ChronicAbsenceTests(AttendanceSelectorTestCase):
    def mark_all(self, date, student, status):
        self.mark(self.basic5, date, {student: status})

    def test_only_students_past_the_threshold_appear(self):
        for day in (MON, TUE):
            self.mark_all(day, self.pupils[0], "absent")
            self.mark_all(day, self.pupils[1], "absent")
        self.mark_all(MON, self.pupils[2], "absent")

        rows = chronic_absences(
            date_from=MON, date_to=FRI, threshold=2, class_ids=[self.basic5.pk]
        )
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["absences"], 2)
        self.assertNotIn(self.pupils[2].pk, [r["student_id"] for r in rows])

    def test_threshold_is_inclusive(self):
        for day in (MON, TUE):
            self.mark_all(day, self.pupils[0], "absent")
        self.assertEqual(
            len(
                chronic_absences(
                    date_from=MON, date_to=FRI, threshold=2, class_ids=[self.basic5.pk]
                )
            ),
            1,
        )
        self.assertEqual(
            len(
                chronic_absences(
                    date_from=MON, date_to=FRI, threshold=3, class_ids=[self.basic5.pk]
                )
            ),
            0,
        )

    def test_late_and_excused_do_not_count_as_absences(self):
        self.mark_all(MON, self.pupils[0], "late")
        self.mark_all(TUE, self.pupils[0], "excused")
        self.assertEqual(
            chronic_absences(date_from=MON, date_to=FRI, threshold=1), []
        )

    def test_the_name_is_built_from_the_user_fields(self):
        self.mark_all(MON, self.pupils[0], "absent")
        row = chronic_absences(date_from=MON, date_to=FRI, threshold=1)[0]
        self.assertEqual(row["name"], "Test Student")
        self.assertEqual(row["admission_number"], self.pupils[0].admission_number)

    def test_one_query_for_the_list(self):
        self.mark_all(MON, self.pupils[0], "absent")
        with self.assertNumQueries(1):
            chronic_absences(date_from=MON, date_to=FRI, threshold=1)


class MarkingComplianceTests(AttendanceSelectorTestCase):
    def test_missing_registers_show_as_expected_but_unfilled(self):
        self.mark(self.basic5, MON, {self.pupils[0]: "present"})
        report = marking_compliance(
            date_from=MON, date_to=WED, school_classes=[self.basic5, self.jhs]
        )
        self.assertEqual(report["expected"], 6)
        self.assertEqual(report["filled"], 1)
        by_name = {r["class_name"]: r for r in report["by_class"]}
        self.assertEqual(by_name[self.basic5.name]["filled"], 1)
        self.assertEqual(by_name[self.jhs.name]["filled"], 0)
        self.assertEqual(by_name[self.jhs.name]["expected"], 3)

    def test_a_class_with_no_register_at_all_still_appears(self):
        report = marking_compliance(
            date_from=MON, date_to=TUE, school_classes=[self.jhs]
        )
        self.assertEqual(report["expected"], 2)
        self.assertEqual(report["filled"], 0)
        self.assertEqual(len(report["by_class"]), 1)

    def test_one_query_covers_any_number_of_classes(self):
        self.mark(self.basic5, MON, {self.pupils[0]: "present"})
        with self.assertNumQueries(1):
            marking_compliance(
                date_from=MON, date_to=FRI, school_classes=[self.basic5, self.jhs]
            )


class RegisterForTests(AttendanceSelectorTestCase):
    def test_returns_none_when_the_class_was_never_marked(self):
        self.assertIsNone(register_for(school_class=self.jhs, date=MON))

    def test_entries_are_prefetched_not_queried_later(self):
        self.mark(self.basic5, MON, {s: "present" for s in self.pupils})
        with self.assertNumQueries(2):
            register = register_for(school_class=self.basic5, date=MON)
            names = [entry.student.user.full_name for entry in register.entries.all()]
        self.assertEqual(len(names), len(self.pupils))

    def test_the_service_written_register_is_read_back(self):
        from apps.attendance.services import record_register

        register = record_register(
            school_class=self.basic5,
            date=MON,
            marks=[(self.pupils[0], "present", "")],
        )
        read = register_for(school_class=self.basic5, date=MON)
        self.assertEqual(read.pk, register.pk)
