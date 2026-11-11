"""
apps/core/views.py
------------------
Admin views for the audit log viewer, the activity feed and daily report,
and the School Settings page (Module 1).
"""

from __future__ import annotations

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import HttpResponseRedirect
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse_lazy
from django.utils import timezone
from django.views import View
from django.views.generic import ListView, TemplateView, UpdateView

from . import selectors
from .forms import (
    AuditLogFilterForm,
    DailyReportForm,
    SchoolSettingsForm,
    SystemEventFilterForm,
)
from .mixins import AdminRequiredMixin
from .models import AuditLog, Notification, SchoolSettings
from .services import update_school_settings


class AuditLogListView(AdminRequiredMixin, ListView):
    """Admin: browse the immutable audit trail, filterable by user / model / date."""

    template_name = "features/core/audit_log.html"
    context_object_name = "audit_logs"
    paginate_by = 50

    def get_queryset(self):
        qs = AuditLog.objects.select_related("actor").order_by("-created_at")
        form = AuditLogFilterForm(self.request.GET or None)
        if form.is_valid():
            actor = form.cleaned_data.get("actor")
            model = form.cleaned_data.get("content_type")
            date_from = form.cleaned_data.get("date_from")
            date_to = form.cleaned_data.get("date_to")
            if actor:
                qs = qs.filter(actor=actor)
            if model:
                qs = qs.filter(content_type=model)
            if date_from:
                qs = qs.filter(created_at__date__gte=date_from)
            if date_to:
                qs = qs.filter(created_at__date__lte=date_to)
        return qs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["filter_form"] = AuditLogFilterForm(self.request.GET or None)
        return ctx


def _group_by_day(events) -> list[dict]:
    """
    Collapse a newest-first event list into ``[{day, events}]`` groups.

    Relies on the queryset ordering: one day's events are consecutive, so a
    single pass is enough.
    """
    groups: list[dict] = []
    for event in events:
        day = timezone.localdate(event.created_at)
        if not groups or groups[-1]["day"] != day:
            groups.append({"day": day, "events": []})
        groups[-1]["events"].append(event)
    return groups


class ActivityFeedView(AdminRequiredMixin, ListView):
    """Admin: the live trail of business events, newest first, filterable."""

    template_name = "features/core/activity_feed.html"
    context_object_name = "events"
    paginate_by = 50

    def get_queryset(self):
        form = SystemEventFilterForm(self.request.GET or None)
        if form.is_valid():
            return selectors.system_event_list(
                category=form.cleaned_data["category"],
                event_type=form.cleaned_data["event_type"],
                date_from=form.cleaned_data["date_from"],
                date_to=form.cleaned_data["date_to"],
            )
        return selectors.system_event_list()

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["filter_form"] = SystemEventFilterForm(self.request.GET or None)
        ctx["days"] = _group_by_day(ctx["events"])
        return ctx


class DailyReportView(AdminRequiredMixin, TemplateView):
    """
    Admin: what happened on one school day — counts by area, money collected,
    and the event list.
    """

    template_name = "features/core/daily_report.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        form = DailyReportForm(self.request.GET or None)
        day = None
        if form.is_valid():
            day = form.cleaned_data.get("date")
        if day is None:
            day = timezone.localdate()
        ctx["filter_form"] = form
        ctx["day"] = day
        ctx["report"] = selectors.daily_activity(day)
        return ctx


class SchoolSettingsView(AdminRequiredMixin, UpdateView):
    """
    Admin: edit the school identity that every shell, report and receipt renders
    from. A blank field means "fall back to the SCHOOL_* value in the .env".
    """

    template_name = "features/core/settings.html"
    form_class = SchoolSettingsForm
    success_url = reverse_lazy("core_school:settings")

    def get_object(self, queryset=None):
        return SchoolSettings.load()

    def form_valid(self, form):
        update_school_settings(
            instance=form.instance,
            cleaned_data=form.cleaned_data,
            actor=self.request.user,
            request=self.request,
        )
        messages.success(self.request, "School settings saved.")
        return HttpResponseRedirect(self.get_success_url())

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        # What each field currently renders as, after the .env fallback.
        ctx["effective"] = selectors.school_settings_values()
        return ctx


# ── In-app notifications (every signed-in role) ──────────────────────────────
class NotificationListView(LoginRequiredMixin, ListView):
    """A user's own notifications, newest first — the bell's landing page."""

    template_name = "features/core/notification_list.html"
    context_object_name = "notifications"
    paginate_by = 30

    def get_queryset(self):
        return self.request.user.notifications.order_by("-created_at")


class NotificationOpenView(LoginRequiredMixin, View):
    """
    Mark one notification read, then follow its link.

    The recipient filter is the permission check: a notification you were not
    sent does not exist as far as this view is concerned.
    """

    def get(self, request, pk):
        notification = get_object_or_404(Notification, pk=pk, recipient=request.user)
        if not notification.is_read:
            Notification.objects.filter(pk=notification.pk).update(is_read=True)
        if notification.link:
            return redirect(notification.link)
        return redirect("core_notifications:list")


class NotificationMarkAllReadView(LoginRequiredMixin, View):
    """POST: clear the bell badge without opening every notification."""

    def post(self, request):
        updated = request.user.notifications.filter(is_read=False).update(is_read=True)
        if updated:
            messages.success(request, "All notifications marked as read.")
        return redirect("core_notifications:list")
