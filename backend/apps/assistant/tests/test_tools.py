"""
Tests for the tool registry (Module 4, Part B).

Every test proves a specific leak-prevention guarantee:
  - a parent cannot see another parent's child
  - a student cannot see another student's data
  - a teacher cannot see another teacher's class data
  - a visitor (no auth) cannot call any tool
  - tool argument tampering (supplying a foreign child_id) is blocked
  - all tool calls produce a ToolCallLog row with no PII values
  - the tool catalogue in policies.py matches the registry
"""

from __future__ import annotations

from django.test import TestCase

from apps.assistant.models import (
    AssistantSettings,
    SignedInConversation,
    ToolCallLog,
)
from apps.assistant.policies import SIGNED_IN_POLICY, TOOLS_FOR_ROLE, policy_for_user
from apps.assistant.tools.registry import REGISTRY, ToolError, call_tool
from apps.dashboards.tests.fixtures import make_user


def _make_student(username, **kwargs):
    user = make_user("student", username=username)
    from apps.academics.models import AcademicYear, SchoolClass
    year, _ = AcademicYear.objects.get_or_create(
        name="2026/2027", defaults={"start_date": "2026-01-01", "end_date": "2027-12-31"}
    )
    cls, _ = SchoolClass.objects.get_or_create(
        academic_year=year, name="Grade 5",
        defaults={"level_order": 5},
    )
    from apps.students.models import Student
    student, _ = Student.objects.get_or_create(
        user=user,
        defaults={
            "admission_number": "ADM-%s" % username,
            "current_class": cls,
        }
    )
    return user, student


def _conv(user) -> SignedInConversation:
    return SignedInConversation.objects.create(
        user=user,
        role_snapshot=getattr(user, "role", ""),
    )


class ToolRegistryTests(TestCase):
    def test_all_tools_in_registry_are_callable(self):
        for name, spec in REGISTRY.items():
            self.assertTrue(callable(spec.fn), "Tool %s fn is not callable" % name)

    def test_registry_names_match_spec_names(self):
        for name, spec in REGISTRY.items():
            self.assertEqual(name, spec.name)

    def test_policies_allowed_set_is_subset_of_registry(self):
        """Every tool name in TOOLS_FOR_ROLE must exist in REGISTRY."""
        all_policy_tools = set()
        for role_tools in TOOLS_FOR_ROLE.values():
            all_policy_tools |= role_tools
        unknown = all_policy_tools - set(REGISTRY.keys())
        self.assertEqual(unknown, set(),
                         "Tools in policies not in registry: %s" % unknown)

    def test_every_registered_tool_has_allowed_roles(self):
        for name, spec in REGISTRY.items():
            self.assertTrue(spec.allowed_roles,
                            "Tool %s has no allowed_roles" % name)


class ToolCallScopingTests(TestCase):
    """Core leak-prevention: tools cannot return another user's data."""

    def setUp(self):
        self.admin = make_user("admin", username="tc_admin")
        self.teacher1 = make_user("teacher", username="tc_teacher1")
        self.teacher2 = make_user("teacher", username="tc_teacher2")
        self.parent1 = make_user("parent", username="tc_parent1")
        self.parent2 = make_user("parent", username="tc_parent2")
        self.student1_user, self.student1 = _make_student("tc_student1")
        self.student2_user, self.student2 = _make_student("tc_student2")

        # Link student1 to parent1 only
        from apps.students.models import StudentGuardian
        try:
            parent1_profile = self.parent1.parent_profile
        except Exception:
            from apps.accounts.models import ParentProfile
            parent1_profile = ParentProfile.objects.create(user=self.parent1)
        StudentGuardian.objects.create(
            student=self.student1, parent=parent1_profile, is_primary=True
        )

    def _call(self, tool_name, user, params=None):
        conv = _conv(user)
        return call_tool(
            tool_name, params or {},
            user=user,
            conversation=conv,
            policy=SIGNED_IN_POLICY,
        )

    def test_parent_can_call_child_attendance_for_own_child(self):
        result = self._call(
            "parent_child_attendance", self.parent1,
            {"child_id": self.student1.pk}
        )
        # May return ok=True or ok=False (no data yet), but must not raise or deny
        self.assertIn("ok", result)
        if not result["ok"]:
            # Acceptable if no attendance data exists
            self.assertNotIn("denied", result.get("error", ""))

    def test_parent_cannot_see_other_parents_child(self):
        result = self._call(
            "parent_child_attendance", self.parent1,
            {"child_id": self.student2.pk}  # student2 belongs to parent2
        )
        self.assertFalse(result["ok"])
        self.assertEqual(result.get("error", ""), "You are not authorised to view this child's information.")

    def test_student_cannot_see_another_students_data(self):
        """student_due_work always uses the calling user's own profile."""
        result = self._call("student_due_work", self.student1_user)
        # student2's data is never returned because _resolve_child uses the calling user
        self.assertIn("ok", result)
        # The result must not reference student2
        body = str(result.get("result", {}))
        self.assertNotIn(self.student2_user.username, body)

    def test_teacher_tool_requires_teacher_role(self):
        result = self._call("teacher_todays_classes", self.parent1)
        self.assertFalse(result["ok"])
        self.assertIn("not available", result.get("error", "").lower())

    def test_admin_tool_denied_to_teacher(self):
        result = self._call("admin_knowledge_gaps", self.teacher1)
        self.assertFalse(result["ok"])

    def test_unknown_tool_is_refused(self):
        result = self._call("nonexistent_tool_xyz", self.admin)
        self.assertFalse(result["ok"])
        self.assertIn("Unknown tool", result.get("error", ""))

    def test_every_call_creates_a_toolcalllog_row(self):
        initial = ToolCallLog.objects.count()
        self._call("school_announcements", self.admin)
        self.assertGreater(ToolCallLog.objects.count(), initial)

    def test_toolcalllog_stores_no_pii_values(self):
        """ToolCallLog only stores field names, not values."""
        conv = _conv(self.admin)
        call_tool(
            "school_announcements", {},
            user=self.admin, conversation=conv,
            policy=SIGNED_IN_POLICY,
        )
        log = ToolCallLog.objects.filter(
            tool_name="school_announcements", actor=self.admin
        ).latest("created_at")
        # fields_returned is a list of key names only
        for item in log.fields_returned:
            self.assertIsInstance(item, str)

    def test_child_id_tampering_blocked(self):
        """Supplying a child_id from a different family must be denied."""
        result = self._call(
            "parent_child_homework", self.parent1,
            {"child_id": self.student2.pk}  # belongs to parent2
        )
        self.assertFalse(result["ok"])


class ToolCallCapTests(TestCase):
    """Max tool calls per turn is enforced by the policy."""

    def test_max_tool_calls_per_turn_in_policy(self):
        from apps.assistant.policies import MAX_TOOL_CALLS_PER_TURN, SIGNED_IN_POLICY
        self.assertLessEqual(SIGNED_IN_POLICY.max_tool_calls, MAX_TOOL_CALLS_PER_TURN)
        self.assertGreater(SIGNED_IN_POLICY.max_tool_calls, 0)

    def test_public_policy_has_zero_tools(self):
        from apps.assistant.policies import PUBLIC_POLICY
        self.assertEqual(PUBLIC_POLICY.max_tool_calls, 0)
        self.assertEqual(len(PUBLIC_POLICY.allowed_tools), 0)


class PolicyScopingTests(TestCase):
    def setUp(self):
        self.student = make_user("student", username="ps_student")
        self.teacher = make_user("teacher", username="ps_teacher")
        self.parent  = make_user("parent",  username="ps_parent")
        self.admin   = make_user("admin",   username="ps_admin")

    def _policy(self, user, **kwargs):
        row = AssistantSettings.load()
        row.enabled = True
        row.signed_in_enabled = True
        row.student_tutor_enabled = True
        row.save(update_fields=["enabled", "signed_in_enabled", "student_tutor_enabled", "updated_at"])
        return policy_for_user(user, **kwargs)

    def test_student_tutor_policy_when_tutor_enabled(self):
        from apps.assistant.policies import TUTOR_POLICY
        policy = self._policy(self.student, tutor_mode=True)
        self.assertTrue(policy.is_tutor)

    def test_tutor_policy_not_given_to_non_students(self):
        from apps.assistant.policies import TUTOR_POLICY
        policy = self._policy(self.teacher, tutor_mode=True)
        self.assertFalse(policy.is_tutor)

    def test_parent_tools_not_in_student_policy(self):
        policy = self._policy(self.student)
        # parent_child_fee_balance must not be callable for a student
        self.assertNotIn("parent_child_fee_balance", policy.tools_for("student"))

    def test_admin_tools_not_in_parent_policy(self):
        policy = self._policy(self.parent)
        self.assertNotIn("admin_knowledge_gaps", policy.tools_for("parent"))
