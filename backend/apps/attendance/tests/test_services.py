"""
apps/attendance/tests/test_services.py
--------------------------------------
``record_register`` is the only way attendance enters the system, so these
tests are about what happens when it is called twice: the counts the Director
reads must not move.
"""

from __future__ import annotations

import datetime

from django.test import TestCase

from apps.attendance.models import AttendanceEntry, AttendanceRegister
from apps.attendance.services import record_register
from apps.core.models import AuditLog, SystemEvent
from apps.dashboards.tests.fixtures import (
    make_class,
    make_student_user,
    make_user,
    make_year,
)

MONDAY = datetime.date(2025, 9, 1)


class RecordRegisterTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.year = make_year()
        cls.school_class = make_class(cls.year, name="Grade 5", level_order=5)
        cls.anna = make_student_user(cls.school_class, admission_number="ADM0001")
        cls.bob = make_student_user(cls.school_class, admission_number="ADM0002")
        cls.teacher = make_user("teacher", username="att_teacher")

    def marks(self, anna="present", bob="present"):
        return [(self.anna, anna, ""), (self.bob, bob, "")]

    def test_records_one_register_with_a_line_per_student(self):
        register = record_register(
            school_class=self.school_class,
            date=MONDAY,
            marks=self.marks(anna="present", bob="absent"),
            actor=self.teacher,
        )
        self.assertEqual(AttendanceRegister.objects.count(), 1)
        self.assertEqual(register.entries.count(), 2)
        by_student = {e.student_id: e for e in register.entries.all()}
        self.assertEqual(by_student[self.anna.pk].status, "present")
        self.assertEqual(by_student[self.bob.pk].status, "absent")

    def test_the_entry_date_is_copied_from_the_register(self):
        register = record_register(
            school_class=self.school_class, date=MONDAY, marks=self.marks()
        )
        entry = register.entries.first()
        self.assertEqual(entry.date, MONDAY)

    def test_calling_it_twice_updates_lines_instead_of_doubling_them(self):
        record_register(
            school_class=self.school_class,
            date=MONDAY,
            marks=self.marks(anna="present", bob="present"),
        )
        record_register(
            school_class=self.school_class,
            date=MONDAY,
            marks=self.marks(anna="absent", bob="present"),
        )
        self.assertEqual(AttendanceRegister.objects.count(), 1)
        self.assertEqual(AttendanceEntry.objects.count(), 2)
        anna = AttendanceEntry.objects.get(student=self.anna)
        self.assertEqual(anna.status, "absent")

    def test_a_student_already_marked_can_be_added_by_a_later_call(self):
        record_register(
            school_class=self.school_class,
            date=MONDAY,
            marks=[(self.anna, "present", "")],
        )
        register = record_register(
            school_class=self.school_class,
            date=MONDAY,
            marks=[(self.bob, "late", "Arrived after assembly")],
        )
        self.assertEqual(register.entries.count(), 2)
        self.assertEqual(
            register.entries.get(student=self.bob).reason, "Arrived after assembly"
        )

    def test_the_same_student_twice_in_one_call_keeps_the_last_mark(self):
        register = record_register(
            school_class=self.school_class,
            date=MONDAY,
            marks=[
                (self.anna, "absent", ""),
                (self.anna, "present", ""),
            ],
        )
        self.assertEqual(register.entries.count(), 1)
        self.assertEqual(register.entries.get().status, "present")

    def test_the_first_fill_keeps_authorship_when_the_sheet_is_corrected(self):
        first = record_register(
            school_class=self.school_class,
            date=MONDAY,
            marks=self.marks(anna="present", bob="present"),
            actor=self.teacher,
        )
        recorded_at = first.recorded_at
        another = make_user("teacher", username="att_teacher_2")
        second = record_register(
            school_class=self.school_class,
            date=MONDAY,
            marks=self.marks(anna="late", bob="present"),
            actor=another,
        )
        self.assertEqual(second.recorded_by, self.teacher)
        self.assertEqual(second.recorded_at, recorded_at)

    def test_a_register_created_without_an_actor_can_be_claimed_later(self):
        record_register(
            school_class=self.school_class, date=MONDAY, marks=self.marks()
        )
        self.assertIsNone(AttendanceRegister.objects.get().recorded_by)
        register = record_register(
            school_class=self.school_class,
            date=MONDAY,
            marks=self.marks(),
            actor=self.teacher,
        )
        self.assertEqual(register.recorded_by, self.teacher)
        self.assertIsNotNone(register.recorded_at)


class AttendanceActivityTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.year = make_year()
        cls.school_class = make_class(cls.year, name="Grade 5", level_order=5)
        cls.anna = make_student_user(cls.school_class, admission_number="ADM0001")
        cls.bob = make_student_user(cls.school_class, admission_number="ADM0002")
        cls.teacher = make_user("teacher", username="att_teacher")

    def _record(self):
        return record_register(
            school_class=self.school_class,
            date=MONDAY,
            marks=[
                (self.anna, "present", ""),
                (self.bob, "absent", "No reason given"),
            ],
            actor=self.teacher,
        )

    def test_it_emits_a_feed_row_with_the_rendered_sentence(self):
        register = self._record()
        event = SystemEvent.objects.get(type="attendance.register_recorded")
        self.assertEqual(event.category, "attendance")
        self.assertEqual(
            event.message, "Attendance for Grade 5 on 2025-09-01: 1/2 present"
        )
        self.assertEqual(event.actor, self.teacher)
        self.assertEqual(event.data["absent"], 1)
        self.assertEqual(event.data["total"], 2)
        self.assertEqual(event.data["date"], MONDAY.isoformat())
        self.assertEqual(event.request_id, "")
        self.assertIsNotNone(register)

    def test_it_writes_an_audit_row_for_who_marked_it(self):
        register = self._record()
        audit = AuditLog.objects.get(action="attendance.register_recorded")
        self.assertEqual(audit.content_type, "attendance.AttendanceRegister")
        self.assertEqual(audit.object_id, str(register.pk))
        self.assertEqual(audit.actor, self.teacher)
        self.assertEqual(audit.change_summary["present"], 1)

    def test_the_structured_log_sink_still_receives_the_event(self):
        with self.assertLogs("apps.events", level="INFO") as ctx:
            self._record()
        record = ctx.records[0]
        self.assertEqual(record.event, "register_recorded")
        self.assertEqual(record.actor_id, self.teacher.pk)
        self.assertEqual(record.data["total"], 2)

    def test_late_counts_as_present_in_the_feed_totals(self):
        record_register(
            school_class=self.school_class,
            date=MONDAY,
            marks=[
                (self.anna, "late", ""),
                (self.bob, "excused", "Medical"),
            ],
            actor=self.teacher,
        )
        event = SystemEvent.objects.get(type="attendance.register_recorded")
        self.assertEqual(event.data["present"], 1)
        self.assertEqual(event.data["late"], 1)
        self.assertEqual(event.data["excused"], 1)
        self.assertEqual(event.data["total"], 2)


class RecordRegisterQueryTests(TestCase):
    """The write is one transaction over a class roster; it must not scale badly."""

    @classmethod
    def setUpTestData(cls):
        cls.year = make_year()
        cls.school_class = make_class(cls.year)
        cls.students = [
            make_student_user(cls.school_class, admission_number=f"Q{i:04d}")
            for i in range(20)
        ]
        cls.teacher = make_user("teacher", username="budget_teacher")

    def test_a_first_fill_of_twenty_students_costs_a_fixed_number_of_queries(self):
        marks = [(s, "present", "") for s in self.students]
        # Register lookup + insert, one bulk insert for the whole roster, the
        # register update, the status count, and the two activity rows.
        with self.assertNumQueries(14):
            record_register(
                school_class=self.school_class,
                date=MONDAY,
                marks=marks,
                actor=self.teacher,
            )

    def test_the_cost_does_not_grow_with_the_roster(self):
        """
        The point of the bulk path: forty pupils cost exactly what twenty cost.
        """
        bigger_year = make_year(name="2026/2027", is_current=False)
        bigger_class = make_class(bigger_year, name="Grade 9", level_order=9)
        many = [
            make_student_user(bigger_class, admission_number=f"R{i:04d}")
            for i in range(40)
        ]
        with self.assertNumQueries(14):
            record_register(
                school_class=bigger_class,
                date=MONDAY,
                marks=[(s, "present", "") for s in many],
                actor=self.teacher,
            )

    def test_a_correction_costs_the_same_whatever_the_roster_size(self):
        marks = [(s, "present", "") for s in self.students]
        record_register(
            school_class=self.school_class, date=MONDAY, marks=marks
        )
        changed = [(s, "absent", "") for s in self.students[:5]] + [
            (s, "present", "") for s in self.students[5:]
        ]
        with self.assertNumQueries(11):
            record_register(
                school_class=self.school_class, date=MONDAY, marks=changed
            )

    def test_a_no_op_correction_writes_nothing_extra(self):
        marks = [(s, "present", "") for s in self.students]
        record_register(school_class=self.school_class, date=MONDAY, marks=marks)
        before = AttendanceEntry.objects.filter(status="present").count()
        record_register(school_class=self.school_class, date=MONDAY, marks=marks)
        self.assertEqual(AttendanceEntry.objects.count(), 20)
        self.assertEqual(AttendanceEntry.objects.filter(status="present").count(), before)
