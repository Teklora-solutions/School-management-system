"""
apps/dashboards/views.py
------------------------
Home views for each role dashboard.
Student and parent home views supply live context data (Module 4).
Director home and all sub-pages are in views_director.py (Module 5).
Admin/teacher/finance analytics to be added in Module 6.
"""

from django.views.generic import TemplateView

from apps.academics.models import SchoolClass
from apps.academics.selectors import get_current_term
from apps.accounts.models import User
from apps.communications.models import MessageThread
from apps.communications.selectors import announcements_for_user
from apps.core.features import feature_disabled_message, feature_enabled
from apps.core.mixins import (
    AdminRequiredMixin,
    FinanceRequiredMixin,
    ParentRequiredMixin,
    StudentRequiredMixin,
    TeacherRequiredMixin,
)
from apps.core.models import AuditLog
from apps.core.money import currency_code
from apps.finance.interface import dashboard_kpis, student_balance
from apps.results.models import ResultSheet
from apps.students.models import Student
from apps.teachers import selectors as teacher_selectors

# Director home is fully implemented in views_director (Module 5)
from .views_director import DirectorHomeView  # noqa: F401


class AdminHomeView(AdminRequiredMixin, TemplateView):
    template_name = "dashboards/admin/home.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["currency"] = currency_code(self.request)

        ctx["student_count"] = Student.objects.filter(status="active").count()
        ctx["teacher_count"] = User.objects.filter(
            role="teacher", is_active=True
        ).count()
        ctx["class_count"] = SchoolClass.objects.filter(
            academic_year__is_current=True
        ).count()
        ctx["results_awaiting_confirmation"] = ResultSheet.objects.filter(
            status=ResultSheet.Status.SUBMITTED
        ).count()
        ctx["open_message_count"] = MessageThread.objects.filter(
            status=MessageThread.Status.OPEN
        ).count()
        term = get_current_term()
        ctx["current_term"] = term
        if term:
            kpis = dashboard_kpis(term=term)
            ctx["fees_collected"] = kpis["collected_this_term"]
            ctx["fees_outstanding"] = kpis["outstanding_total"]
            ctx["fees_collected_today"] = kpis["collected_today"]

        ctx["recent_announcements"] = announcements_for_user(user=self.request.user)[:5]
        ctx["recent_audit_events"] = AuditLog.objects.select_related("actor")[:8]
        return ctx


class TeacherHomeView(TeacherRequiredMixin, TemplateView):
    template_name = "dashboards/teacher/home.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        from apps.academics.selectors import get_current_term

        ctx["current_term"] = get_current_term()
        ctx.update(
            teacher_selectors.teacher_home_summary(
                teacher_user_id=self.request.user.pk, term=ctx["current_term"]
            )
        )
        ctx["recent_announcements"] = announcements_for_user(user=self.request.user)[:5]
        return ctx


class StudentHomeView(StudentRequiredMixin, TemplateView):
    template_name = "dashboards/student/home.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        user = self.request.user

        if not hasattr(user, "student_profile"):
            return ctx

        student = user.student_profile
        ctx["student"] = student

        from apps.results.selectors import student_latest_result_summary

        ctx["result_summary"] = student_latest_result_summary(student_id=student.pk)

        from apps.academics.selectors import get_current_term

        current_term = get_current_term()
        ctx["current_term"] = current_term
        ctx["fees_enabled"] = feature_enabled("fees")
        if ctx["fees_enabled"]:
            ctx["fee_balance"] = student_balance(student_id=student.pk)
        else:
            ctx["fee_balance"] = None
            ctx["fees_disabled_message"] = feature_disabled_message("fees")

        hw_list = []
        if student.current_class_id and current_term:
            from apps.homework.selectors import homework_due_soon, mark_new

            hw_list = mark_new(
                list(
                    homework_due_soon(
                        school_class_id=student.current_class_id,
                        stream_id=student.current_stream_id,
                        term_id=current_term.pk,
                        days=7,
                    )
                )
            )
        ctx["homework_due_soon"] = hw_list

        from apps.communications.selectors import (
            announcements_for_user,
            unread_announcement_count,
        )

        ctx["recent_announcements"] = announcements_for_user(user=user).order_by(
            "is_read", "-created_at"
        )[:5]
        ctx["unread_announcements"] = unread_announcement_count(user=user)

        return ctx


class FinanceHomeView(FinanceRequiredMixin, TemplateView):
    template_name = "dashboards/finance/home.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        from apps.academics.selectors import get_current_term

        term = get_current_term()
        ctx["current_term"] = term
        ctx["currency"] = currency_code(self.request)
        if term:
            # Not gated on feature_enabled("fees") on purpose: a school that
            # switches fee *billing* off still has receipts to reconcile.
            ctx.update(dashboard_kpis(term=term))

        from apps.communications.selectors import announcements_for_user

        ctx["recent_announcements"] = announcements_for_user(user=self.request.user)[:5]
        return ctx


class ParentHomeView(ParentRequiredMixin, TemplateView):
    template_name = "dashboards/parent/home.html"

    def _get_active_student(self, request):
        if not hasattr(request.user, "parent_profile"):
            return None
        from apps.students.models import Student

        child_ids = list(
            request.user.parent_profile.children_links.values_list(
                "student_id", flat=True
            )
        )
        if not child_ids:
            return None
        selected_id = request.session.get("selected_child_id")
        if selected_id and int(selected_id) in child_ids:
            target_id = int(selected_id)
        else:
            primary = (
                request.user.parent_profile.children_links.filter(is_primary=True)
                .values_list("student_id", flat=True)
                .first()
            )
            target_id = primary or child_ids[0]
        return (
            Student.objects.select_related("user", "current_class", "current_stream")
            .filter(pk=target_id)
            .first()
        )

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        user = self.request.user

        if hasattr(user, "parent_profile"):
            from apps.students.models import Student

            children = list(
                Student.objects.filter(
                    guardian_links__parent=user.parent_profile
                ).select_related("user", "current_class")
            )
        else:
            children = []
        ctx["children"] = children
        ctx["children_count"] = len(children)

        student = self._get_active_student(self.request)
        ctx["student"] = student

        if student:
            from apps.results.selectors import student_latest_result_summary

            ctx["result_summary"] = student_latest_result_summary(student_id=student.pk)

            from apps.academics.selectors import get_current_term

            current_term = get_current_term()
            ctx["current_term"] = current_term
            ctx["fees_enabled"] = feature_enabled("fees")
            if ctx["fees_enabled"]:
                ctx["fee_balance"] = student_balance(student_id=student.pk)
            else:
                ctx["fee_balance"] = None
                ctx["fees_disabled_message"] = feature_disabled_message("fees")

            hw_list = []
            if student.current_class_id and current_term:
                from apps.homework.selectors import homework_due_soon, mark_new

                hw_list = mark_new(
                    list(
                        homework_due_soon(
                            school_class_id=student.current_class_id,
                            stream_id=student.current_stream_id,
                            term_id=current_term.pk,
                            days=7,
                        )
                    )
                )
            ctx["homework_due_soon"] = hw_list

        from apps.communications.selectors import (
            announcements_for_user,
            unread_announcement_count,
        )

        ctx["recent_announcements"] = announcements_for_user(user=user).order_by(
            "is_read", "-created_at"
        )[:5]
        ctx["unread_announcements"] = unread_announcement_count(user=user)

        return ctx
