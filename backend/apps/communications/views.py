"""
apps/communications/views.py
-----------------------------
Views for announcements (student/parent) and the message thread system
(complaints, inquiries, satisfaction messages).

Access rules:
  - Announcement list/read: all authenticated roles.
  - Message create: student or parent only.
  - Thread detail: sender OR staff (admin/director/teacher/finance).
  - Admin inbox: admin or director only.
"""

from __future__ import annotations

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied
from django.shortcuts import get_object_or_404, redirect
from django.views.generic import TemplateView, View

from apps.core.mixins import RoleRequiredMixin

from . import selectors, services
from .forms import AnnouncementForm, InboxFilterForm, MessageThreadForm, ThreadReplyForm
from .models import MessageThread

# ---------------------------------------------------------------------------
# Announcements
# ---------------------------------------------------------------------------


class AnnouncementListView(LoginRequiredMixin, TemplateView):
    template_name = "features/communications/announcement_list.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["announcements"] = selectors.announcements_for_user(user=self.request.user)
        ctx["unread_count"] = selectors.unread_announcement_count(
            user=self.request.user
        )
        return ctx


class AnnouncementMarkReadView(LoginRequiredMixin, View):
    """AJAX-friendly POST: mark an announcement as read."""

    def post(self, request, pk, *args, **kwargs):
        # Verify the announcement is actually visible to this user before marking read
        visible = (
            selectors.announcements_for_user(user=request.user).filter(pk=pk).exists()
        )
        if not visible:
            raise PermissionDenied("Announcement not accessible.")
        selectors.mark_announcement_read(announcement_id=pk, user=request.user)
        # Redirect back or return 204
        next_url = (
            request.POST.get("next")
            or request.META.get("HTTP_REFERER")
            or "communications:list"
        )
        if next_url.startswith("/"):
            return redirect(next_url)
        return redirect("communications:list")


class AnnouncementMarkAllReadView(LoginRequiredMixin, View):
    def post(self, request, *args, **kwargs):
        ann_qs = selectors.announcements_for_user(user=request.user).filter(
            is_read=False
        )
        for ann in ann_qs:
            selectors.mark_announcement_read(announcement_id=ann.pk, user=request.user)
        messages.success(request, "All announcements marked as read.")
        return redirect("communications:list")


class AnnouncementCreateView(RoleRequiredMixin, TemplateView):
    """Admin and director can create announcements."""

    allowed_roles = ["admin", "director"]
    template_name = "features/communications/announcement_create.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["form"] = AnnouncementForm(
            self.request.POST or None, self.request.FILES or None
        )
        return ctx

    def post(self, request, *args, **kwargs):
        form = AnnouncementForm(request.POST, request.FILES)
        if not form.is_valid():
            messages.error(request, "Please fix the form errors.")
            return self.render_to_response({"form": form})
        target_classes = list(form.cleaned_data.get("target_classes", []))
        ann = services.create_announcement(
            title=form.cleaned_data["title"],
            body=form.cleaned_data["body"],
            audience=form.cleaned_data["audience"],
            target_roles=list(form.cleaned_data.get("target_roles", [])),
            target_class_ids=[c.pk for c in target_classes],
            attachment=form.cleaned_data.get("attachment"),
            publish_at=form.cleaned_data.get("publish_at"),
            expires_at=form.cleaned_data.get("expires_at"),
            is_published=bool(form.cleaned_data.get("is_published", True)),
            created_by=request.user,
        )
        messages.success(request, f'Announcement "{ann.title}" created.')
        return redirect("communications:list")


# ---------------------------------------------------------------------------
# Message Threads — student/parent side
# ---------------------------------------------------------------------------


class MessageCreateView(RoleRequiredMixin, TemplateView):
    allowed_roles = ["student", "parent"]
    template_name = "features/communications/message_create.html"

    def _get_on_behalf_of(self, request):
        """For parents: get the currently selected child."""
        if request.user.role == "parent" and hasattr(request.user, "parent_profile"):
            from apps.students.models import Student

            selected_id = request.session.get("selected_child_id")
            child_ids = list(
                request.user.parent_profile.children_links.values_list(
                    "student_id", flat=True
                )
            )
            if child_ids:
                if selected_id and int(selected_id) in child_ids:
                    student_id = int(selected_id)
                else:
                    primary = (
                        request.user.parent_profile.children_links.filter(
                            is_primary=True
                        )
                        .values_list("student_id", flat=True)
                        .first()
                    )
                    student_id = primary or child_ids[0]
                return Student.objects.filter(pk=student_id).first()
        return None

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["form"] = MessageThreadForm(
            self.request.POST or None, self.request.FILES or None
        )
        ctx["on_behalf_of"] = self._get_on_behalf_of(self.request)
        return ctx

    def post(self, request, *args, **kwargs):
        form = MessageThreadForm(request.POST, request.FILES)
        if not form.is_valid():
            messages.error(request, "Please fix the form errors.")
            return self.render_to_response(
                {"form": form, "on_behalf_of": self._get_on_behalf_of(request)}
            )

        on_behalf_of = self._get_on_behalf_of(request)
        try:
            thread = services.create_message_thread(
                sender=request.user,
                message_type=form.cleaned_data["message_type"],
                subject=form.cleaned_data["subject"],
                body=form.cleaned_data["body"],
                attachment=form.cleaned_data.get("attachment"),
                on_behalf_of=on_behalf_of,
            )
            messages.success(
                request, "Your message has been sent. We will get back to you soon."
            )
            return redirect("communications:thread_detail", pk=thread.pk)
        except Exception as exc:  # noqa: BLE001
            messages.error(request, f"Could not send message: {exc}")
        return self.render_to_response({"form": form, "on_behalf_of": on_behalf_of})


class ThreadDetailView(LoginRequiredMixin, TemplateView):
    template_name = "features/communications/thread_detail.html"

    def _check_access(self, request, thread):
        """Sender and staff can view; anyone else gets 403."""
        if request.user == thread.sender:
            return
        if (
            request.user.role in ("admin", "director", "teacher", "finance")
            or request.user.is_superuser
        ):
            return
        raise PermissionDenied("You do not have permission to view this thread.")

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        thread = get_object_or_404(MessageThread, pk=kwargs["pk"])
        self._check_access(self.request, thread)
        ctx["thread"] = thread
        ctx["thread_messages"] = selectors.thread_messages(thread_id=thread.pk)
        is_staff = (
            self.request.user.role in ("admin", "director", "teacher", "finance")
            or self.request.user.is_superuser
        )
        ctx["reply_form"] = (
            ThreadReplyForm()
            if is_staff
            else ThreadReplyForm(initial={"new_status": ""})
        )
        ctx["is_staff"] = is_staff
        ctx["can_reply"] = True  # both sender and staff can reply
        return ctx

    def post(self, request, *args, **kwargs):
        thread = get_object_or_404(MessageThread, pk=kwargs["pk"])
        self._check_access(request, thread)
        form = ThreadReplyForm(request.POST, request.FILES)
        if not form.is_valid():
            messages.error(request, "Please fix the form errors.")
            return redirect("communications:thread_detail", pk=thread.pk)

        is_staff = (
            request.user.role in ("admin", "director", "teacher", "finance")
            or request.user.is_superuser
        )
        new_status = form.cleaned_data.get("new_status") if is_staff else None

        services.reply_to_thread(
            thread=thread,
            author=request.user,
            body=form.cleaned_data["body"],
            attachment=form.cleaned_data.get("attachment"),
            new_status=new_status or None,
        )
        messages.success(request, "Reply sent.")
        return redirect("communications:thread_detail", pk=thread.pk)


class MyMessagesView(RoleRequiredMixin, TemplateView):
    allowed_roles = ["student", "parent"]
    template_name = "features/communications/my_messages.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        status_filter = self.request.GET.get("status", "")
        ctx["threads"] = selectors.threads_for_sender(
            user=self.request.user, status=status_filter
        )
        ctx["status_filter"] = status_filter
        ctx["status_choices"] = MessageThread.Status.choices
        return ctx


# ---------------------------------------------------------------------------
# Admin Inbox
# ---------------------------------------------------------------------------


class InboxView(RoleRequiredMixin, TemplateView):
    allowed_roles = ["admin", "director"]
    template_name = "features/communications/inbox.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        form = InboxFilterForm(self.request.GET or None)
        ctx["filter_form"] = form
        status = msg_type = search = ""
        if form.is_valid():
            status = form.cleaned_data.get("status") or ""
            msg_type = form.cleaned_data.get("message_type") or ""
            search = form.cleaned_data.get("search") or ""
        ctx["threads"] = selectors.threads_inbox(
            status=status, message_type=msg_type, search=search
        )
        # Counts for the status tabs
        ctx["open_count"] = MessageThread.objects.filter(
            status=MessageThread.Status.OPEN
        ).count()
        ctx["in_progress_count"] = MessageThread.objects.filter(
            status=MessageThread.Status.IN_PROGRESS
        ).count()
        ctx["resolved_count"] = MessageThread.objects.filter(
            status=MessageThread.Status.RESOLVED
        ).count()
        return ctx


class InboxThreadView(RoleRequiredMixin, TemplateView):
    """Staff-side thread detail (same ThreadDetailView logic, but separate URL for nav active state)."""

    allowed_roles = ["admin", "director", "teacher", "finance"]
    template_name = "features/communications/thread_detail.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        thread = get_object_or_404(MessageThread, pk=kwargs["pk"])
        ctx["thread"] = thread
        ctx["thread_messages"] = selectors.thread_messages(thread_id=thread.pk)
        ctx["reply_form"] = ThreadReplyForm()
        ctx["is_staff"] = True
        ctx["can_reply"] = True
        # Build assignable staff list
        from apps.accounts.models import User

        ctx["staff_users"] = User.objects.filter(
            role__in=["admin", "director"], is_active=True
        ).order_by("last_name", "first_name")
        return ctx

    def post(self, request, *args, **kwargs):
        thread = get_object_or_404(MessageThread, pk=kwargs["pk"])
        action = request.POST.get("action", "reply")

        if action == "assign":
            assignee_id = request.POST.get("assignee_id")
            if assignee_id:
                from apps.accounts.models import User

                assignee = get_object_or_404(User, pk=assignee_id)
                services.assign_thread(
                    thread=thread, assignee=assignee, actor=request.user
                )
                messages.success(
                    request, f"Thread assigned to {assignee.get_full_name()}."
                )
            return redirect("communications:inbox_thread", pk=thread.pk)

        if action == "status":
            new_status = request.POST.get("new_status")
            if new_status in dict(MessageThread.Status.choices):
                services.update_thread_status(thread=thread, status=new_status)
                messages.success(
                    request, f"Status updated to {thread.get_status_display()}."
                )
            return redirect("communications:inbox_thread", pk=thread.pk)

        # Default: reply
        form = ThreadReplyForm(request.POST, request.FILES)
        if not form.is_valid():
            messages.error(request, "Please fix the form errors.")
            return redirect("communications:inbox_thread", pk=thread.pk)
        services.reply_to_thread(
            thread=thread,
            author=request.user,
            body=form.cleaned_data["body"],
            attachment=form.cleaned_data.get("attachment"),
            new_status=form.cleaned_data.get("new_status") or None,
        )
        messages.success(request, "Reply sent.")
        return redirect("communications:inbox_thread", pk=thread.pk)
