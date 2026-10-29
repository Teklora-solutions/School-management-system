"""
apps/finance/tests/test_fee_elections.py
-----------------------------------------
FS-1 — "Who takes this?", the screen that decides which pupils in one class are
billed an optional fee item.

The screen is a view of the billing rule, not a second copy of it: the roster it
lists is ``_billing_plan``'s roster, the tick writes the same
``StudentFeeElection`` row the run reads, and the only way a pupil's state can
change is ``services.set_student_fee_item`` — which is where the refusal to opt a
pupil out of a *compulsory* item lives, because that would be a waiver with no
ledger entry behind it.

The last class here is the point of the whole checkpoint: a tick that does not
change the next bill would be decoration.
"""

from __future__ import annotations

import re
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse

from apps.core.models import AuditLog
from apps.dashboards.tests.fixtures import (
    make_accounts,
    make_class,
    make_student_user,
    make_term,
    make_user,
    make_year,
)
from apps.students.models import Student

from .. import services
from ..models import FeeItem, FeeStructure, Invoice, StudentFeeElection

D = Decimal


def _named(student: Student, first: str, last: str) -> Student:
    student.user.first_name = first
    student.user.last_name = last
    student.user.save(update_fields=["first_name", "last_name"])
    return student


class ElectionTestBase(TestCase):
    """
    One class of three pupils, one withdrawn pupil who is still in the class
    list, one pupil in another class, and a structure with a compulsory item and
    an optional one.
    """

    @classmethod
    def setUpTestData(cls):
        cls.year = make_year()
        cls.term = make_term(cls.year)
        cls.school_class = make_class(cls.year, name="Grade 5")
        cls.other_class = make_class(cls.year, name="Grade 6", level_order=6)
        cls.accounts = make_accounts()
        cls.officer = make_user("finance", username="el_officer")
        cls.admin = make_user("admin", username="el_admin")
        cls.director = make_user("director", username="el_director")

        cls.rider = _named(
            make_student_user(cls.school_class, admission_number="EL-001"),
            "Amina",
            "Otieno",
        )
        cls.classmate = _named(
            make_student_user(cls.school_class, admission_number="EL-002"),
            "Brian",
            "Kamau",
        )
        cls.walker = _named(
            make_student_user(cls.school_class, admission_number="EL-003"),
            "Cate",
            "Njeri",
        )
        cls.outsider = make_student_user(cls.other_class, admission_number="EL-004")
        cls.withdrawn = make_student_user(cls.school_class, admission_number="EL-005")
        cls.withdrawn.status = Student.Status.WITHDRAWN
        cls.withdrawn.save(update_fields=["status"])

        cls.structure = FeeStructure.objects.create(
            school_class=cls.school_class,
            term=cls.term,
            version=1,
            is_active=True,
        )
        cls.tuition = FeeItem.objects.create(
            structure=cls.structure,
            label="Tuition",
            amount=D("400.00"),
            ordering=1,
        )
        cls.transport = FeeItem.objects.create(
            structure=cls.structure,
            label="Transport",
            amount=D("100.00"),
            ordering=2,
            compulsory=False,
        )

    def url(self, item: FeeItem | None = None) -> str:
        return reverse(
            "finance:fee_item_students", kwargs={"pk": (item or self.transport).pk}
        )

    def as_officer(self):
        self.client.force_login(self.officer)

    def elect(self, *students: Student) -> None:
        for student in students:
            services.set_student_fee_item(
                student=student,
                fee_item=self.transport,
                included=True,
                actor=self.officer,
            )

    def election_audits(self) -> int:
        return AuditLog.objects.filter(action="finance.fee_election_set").count()


class RosterTests(ElectionTestBase):
    """
    The roster must be the one the billing run bills. Offering a pupil the run
    would skip is a fee the school never charges.
    """

    def setUp(self):
        super().setUp()
        self.as_officer()

    def test_the_page_lists_the_class_and_nobody_else(self):
        response = self.client.get(self.url())
        self.assertEqual(response.status_code, 200)
        for student in (self.rider, self.classmate, self.walker):
            self.assertContains(response, student.admission_number)
        self.assertNotContains(response, self.outsider.admission_number)
        self.assertNotContains(
            response,
            self.withdrawn.admission_number,
            msg_prefix="A withdrawn pupil must not be offered a fee.",
        )
        self.assertEqual(response.context["roster_total"], 3)

    def test_every_row_carries_its_name_and_number_for_the_search(self):
        response = self.client.get(self.url())
        self.assertContains(response, 'data-search="el-001 amina otieno"')

    def test_the_search_and_bulk_tick_controls_are_present(self):
        response = self.client.get(self.url())
        self.assertContains(response, 'id="roster-search"')
        self.assertContains(response, "data-tick-visible")
        self.assertContains(response, "data-clear-visible")

    def test_a_pupil_already_billed_renders_ticked(self):
        self.elect(self.rider)
        response = self.client.get(self.url())
        flat = re.sub(r"\s+", " ", response.content.decode())
        self.assertIn(f'value="{self.rider.pk}" id="pupil-{self.rider.pk}" checked', flat)
        self.assertNotIn(
            f'value="{self.classmate.pk}" id="pupil-{self.classmate.pk}" checked',
            flat,
        )
        self.assertContains(response, "1 of 3")

    def test_nobody_is_ticked_on_an_optional_item_nobody_was_entered_for(self):
        response = self.client.get(self.url())
        self.assertEqual(response.context["elected_total"], 0)


class SavingElectionsTests(ElectionTestBase):
    def setUp(self):
        super().setUp()
        self.as_officer()

    def test_ticking_pupils_saves_an_election_and_audits_each_one(self):
        response = self.client.post(
            self.url(),
            {"students": [self.rider.pk, self.classmate.pk]},
            follow=True,
        )
        self.assertRedirects(response, self.url())
        self.assertEqual(
            set(
                StudentFeeElection.objects.filter(
                    fee_item=self.transport, included=True
                ).values_list("student_id", flat=True)
            ),
            {self.rider.pk, self.classmate.pk},
        )
        self.assertEqual(self.election_audits(), 2)
        self.assertContains(response, "2 pupils now billed")

    def test_unticking_a_pupil_takes_them_off_the_item(self):
        self.elect(self.rider)
        response = self.client.post(
            self.url(), {"students": [self.classmate.pk]}, follow=True
        )
        self.assertRedirects(response, self.url())
        by_student = {
            row.student_id: row.included
            for row in StudentFeeElection.objects.filter(fee_item=self.transport)
        }
        self.assertEqual(by_student, {self.rider.pk: False, self.classmate.pk: True})
        self.assertContains(response, "1 pupil now billed, 1 taken off")

    def test_clearing_every_tick_takes_the_item_off_every_bill(self):
        """The page asks before doing this; the server honours the answer."""
        self.elect(self.rider, self.classmate)
        self.client.post(self.url(), {}, follow=True)
        self.assertFalse(
            StudentFeeElection.objects.filter(
                fee_item=self.transport, included=True
            ).exists()
        )

    def test_an_unchanged_selection_writes_nothing(self):
        self.elect(self.rider)
        before = self.election_audits()
        response = self.client.post(
            self.url(), {"students": [self.rider.pk]}, follow=True
        )
        self.assertEqual(self.election_audits(), before)
        self.assertContains(response, "the selection is unchanged")

    def test_a_pupil_from_another_class_cannot_be_added(self):
        """
        The form is a POST body, so the only thing standing between a pasted id
        and another class's bill is the roster the view walks.
        """
        response = self.client.post(
            self.url(), {"students": [self.outsider.pk]}, follow=True
        )
        self.assertFalse(
            StudentFeeElection.objects.filter(student=self.outsider).exists()
        )
        self.assertContains(response, "the selection is unchanged")

    def test_a_compulsory_item_refuses_the_post(self):
        self.elect(self.rider)
        response = self.client.post(
            self.url(self.tuition), {"students": [self.classmate.pk]}, follow=True
        )
        self.assertRedirects(
            response,
            reverse(
                "finance:fee_structure_detail", kwargs={"pk": self.structure.pk}
            ),
        )
        self.assertFalse(
            StudentFeeElection.objects.filter(fee_item=self.tuition).exists()
        )
        self.assertContains(response, "is compulsory")

    def test_the_service_still_refuses_to_opt_out_of_a_compulsory_item(self):
        """
        Pinned here as well as in the view: the view is one caller, and the rule
        belongs to the money, not to the screen that happens to reach it.
        """
        with self.assertRaises(ValidationError):
            services.set_student_fee_item(
                student=self.rider,
                fee_item=self.tuition,
                included=False,
                actor=self.officer,
            )


class CompulsoryItemPageTests(ElectionTestBase):
    def setUp(self):
        super().setUp()
        self.as_officer()

    def test_the_page_explains_that_everybody_is_billed_and_offers_no_pick(self):
        response = self.client.get(self.url(self.tuition))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "is compulsory")
        self.assertNotContains(response, 'name="students"')

    def test_the_structure_page_links_only_the_optional_item(self):
        response = self.client.get(
            reverse(
                "finance:fee_structure_detail", kwargs={"pk": self.structure.pk}
            )
        )
        self.assertContains(response, self.url(self.transport))
        self.assertNotContains(response, self.url(self.tuition))
        self.assertContains(response, "Selected students only")


class StructureCountsTests(ElectionTestBase):
    def setUp(self):
        super().setUp()
        self.as_officer()

    def test_the_structure_page_says_how_many_pupils_are_ticked(self):
        self.elect(self.rider, self.classmate)
        response = self.client.get(
            reverse(
                "finance:fee_structure_detail", kwargs={"pk": self.structure.pk}
            )
        )
        self.assertContains(response, "2 pupils ticked")

    def test_an_optional_item_nobody_is_ticked_for_says_so(self):
        response = self.client.get(
            reverse(
                "finance:fee_structure_detail", kwargs={"pk": self.structure.pk}
            )
        )
        self.assertContains(response, "nobody ticked yet")


class BillingFollowsTheTicksTests(ElectionTestBase):
    """
    The screen's promise, checked against the documents it changes: the ticked
    pupil's bill carries the item and the unticked pupil's does not.
    """

    def setUp(self):
        super().setUp()
        self.as_officer()

    def post_bills(self):
        services.post_invoices(
            term=self.term, school_class=self.school_class, actor=self.officer
        )

    def labels_of(self, student: Student) -> list[str]:
        invoice = Invoice.objects.get(student=student, term=self.term)
        return [line.label for line in invoice.lines.order_by("id")]

    def test_only_the_ticked_pupil_is_billed_the_optional_item(self):
        self.client.post(self.url(), {"students": [self.rider.pk]})
        self.post_bills()
        self.assertEqual(self.labels_of(self.rider), ["Tuition", "Transport"])
        self.assertEqual(self.labels_of(self.walker), ["Tuition"])

    def test_the_optional_item_changes_what_the_family_owes(self):
        self.client.post(self.url(), {"students": [self.rider.pk]})
        self.post_bills()
        self.assertEqual(
            Invoice.objects.get(student=self.rider, term=self.term).total_amount,
            D("500.00"),
        )
        self.assertEqual(
            Invoice.objects.get(student=self.walker, term=self.term).total_amount,
            D("400.00"),
        )

    def test_a_bill_already_posted_is_not_restated_by_a_later_tick(self):
        self.post_bills()
        self.client.post(self.url(), {"students": [self.walker.pk]})
        self.assertEqual(self.labels_of(self.walker), ["Tuition"])
        self.assertEqual(
            Invoice.objects.get(student=self.walker, term=self.term).total_amount,
            D("400.00"),
        )


class AccessTests(ElectionTestBase):
    def setUp(self):
        super().setUp()
        self.stranger = make_user("parent", username="el_parent")

    def test_the_bursar_may_open_the_screen(self):
        self.as_officer()
        self.assertEqual(self.client.get(self.url()).status_code, 200)

    def test_every_other_role_is_refused(self):
        """
        Not even Admin and Director: they may read the structure, but naming the
        pupils a school charges is the bursar's decision.
        """
        for user in (
            self.admin,
            self.director,
            self.stranger,
            self.rider.user,
            make_user("teacher", username="el_teacher"),
        ):
            self.client.force_login(user)
            self.assertEqual(self.client.get(self.url()).status_code, 403, user.role)
            self.assertEqual(
                self.client.post(self.url(), {"students": [self.rider.pk]}).status_code,
                403,
                user.role,
            )
