"""
apps/attendance/tests/test_models.py
------------------------------------
The two constraints the whole attendance design rests on: one register per
class per day, and one line per student per register.
"""

from __future__ import annotations

import datetime

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.utils import timezone

from apps.attendance.models import AttendanceEntry, AttendanceRegister
from apps.dashboards.tests.fixtures import make_class, make_student_user, make_year


class AttendanceRegisterTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.year = make_year()
        cls.school_class = make_class(cls.year, name="Grade 5", level_order=5)
        cls.day = datetime.date(2025, 9, 1)

    def _register(self, **kwargs) -> AttendanceRegister:
        defaults = {"school_class": self.school_class, "date": self.day}
        defaults.update(kwargs)
        return AttendanceRegister.objects.create(**defaults)

    def test_one_register_per_class_per_day(self):
        self._register()
        with self.assertRaises(IntegrityError), transaction.atomic():
            self._register()

    def test_a_second_day_for_the_same_class_is_allowed(self):
        self._register()
        other = self._register(date=datetime.date(2025, 9, 2))
        self.assertEqual(AttendanceRegister.objects.count(), 2)
        self.assertEqual(other.date, datetime.date(2025, 9, 2))

    def test_str_names_the_class_and_the_day(self):
        self.assertEqual(str(self._register()), "Grade 5 (2025/2026) - 01/09/2025")

    def test_a_future_date_is_rejected_by_clean(self):
        register = self._register(date=timezone.localdate() + datetime.timedelta(days=1))
        with self.assertRaises(ValidationError) as ctx:
            register.full_clean(exclude=["recorded_by"])
        self.assertIn("date", ctx.exception.message_dict)

    def test_today_is_not_a_future_date(self):
        register = self._register(date=timezone.localdate())
        register.full_clean(exclude=["recorded_by"])


class AttendanceEntryTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.year = make_year()
        cls.school_class = make_class(cls.year)
        cls.day = datetime.date(2025, 9, 1)
        cls.register = AttendanceRegister.objects.create(
            school_class=cls.school_class, date=cls.day
        )
        cls.student = make_student_user(cls.school_class)

    def _entry(self, **kwargs) -> AttendanceEntry:
        defaults = {
            "register": self.register,
            "student": self.student,
            "status": AttendanceEntry.Status.PRESENT,
            "date": self.day,
        }
        defaults.update(kwargs)
        return AttendanceEntry.objects.create(**defaults)

    def test_one_line_per_student_per_register(self):
        self._entry()
        with self.assertRaises(IntegrityError), transaction.atomic():
            self._entry(status=AttendanceEntry.Status.ABSENT)

    def test_the_same_student_can_be_marked_on_two_days(self):
        self._entry()
        second = AttendanceRegister.objects.create(
            school_class=self.school_class, date=datetime.date(2025, 9, 2)
        )
        AttendanceEntry.objects.create(
            register=second,
            student=self.student,
            status=AttendanceEntry.Status.ABSENT,
            date=second.date,
        )
        self.assertEqual(AttendanceEntry.objects.count(), 2)

    def test_status_defaults_to_present(self):
        self.assertEqual(self._entry().status, "present")

    def test_deleting_a_student_is_blocked_by_their_marks(self):
        from django.db.models import ProtectedError

        self._entry()
        with self.assertRaises(ProtectedError):
            self.student.delete()

    def test_removing_a_register_removes_its_lines(self):
        self._entry()
        self.register.delete()
        self.assertEqual(AttendanceEntry.objects.count(), 0)
