"""
apps/gradebook/release_views.py
-------------------------------
The teacher's release page: freeze the grid into a numbered version that
families can read, and show what has been released before.

The page never edits marks. A correction goes back to the grid or the source
(homework, quizzes, hand-recorded marks), and is then released again with a
reason, which moves the family from one frozen version to the next.
"""

from __future__ import annotations

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views import View

from apps.core.mixins import TeacherRequiredMixin
from apps.results.models import ResultSheet

from . import selectors, services
from .forms import ReleaseForm
from .setup_views import _scoped_setup
from .views import GradebookEnabledMixin


class TeacherGradebookReleaseView(TeacherRequiredMixin, GradebookEnabledMixin, View):
    template_name = "features/gradebook/teacher_release.html"

    def _grid_url(self, setup) -> str:
        return (
            reverse(
                "gradebook:teacher_grid",
                args=[setup.school_class_id, setup.subject_id],
            )
            + f"?term={setup.term_id}"
        )

    def _report_card_block(self, setup, latest, locked: bool) -> tuple[bool, str | None]:
        """
        Whether the copy-to-report-card button may run, and the sentence saying
        why not when it may not.
        """
        sheet = setup.exam_sheet
        if latest is None:
            return False, services.RELEASE_REQUIRED_MESSAGE
        if sheet is None:
            return False, services.SHEET_REQUIRED_MESSAGE
        if (setup.stream_id or None) != (sheet.stream_id or None):
            return False, services.STREAM_MISMATCH_MESSAGE
        if locked:
            return False, services.LOCKED_MESSAGE
        if sheet.status != ResultSheet.Status.DRAFT:
            return False, services.SHEET_NOT_DRAFT_MESSAGE.format(
                status=sheet.get_status_display().lower()
            )
        return True, None

    def _context(self, setup, *, form=None) -> dict:
        releases = list(
            setup.releases.select_related("released_by").order_by("-version")[:5]
        )
        latest = releases[0] if releases else None
        locked = services.term_is_locked(setup.term)
        can_send, send_blocked = self._report_card_block(setup, latest, locked)
        return {
            "setup": setup,
            "term": setup.term,
            "grid": services.build_grid(setup),
            "locked": locked,
            "latest": latest,
            "next_version": (latest.version + 1) if latest else 1,
            "releases": releases,
            "form": form or ReleaseForm(setup=setup),
            "unreleased_homework": selectors.unreleased_homework(setup).count(),
            "grid_href": self._grid_url(setup),
            "exam_sheet": setup.exam_sheet,
            "can_send": can_send,
            "send_blocked": send_blocked,
        }

    def get(self, request, setup_id: int):
        setup = _scoped_setup(request, setup_id)
        return render(request, self.template_name, self._context(setup))

    def post(self, request, setup_id: int):
        setup = _scoped_setup(request, setup_id)
        form = ReleaseForm(request.POST, setup=setup)
        if form.is_valid():
            try:
                release = services.release_gradebook(
                    setup=setup,
                    reason=form.cleaned_data["reason"],
                    actor=request.user,
                    request=request,
                )
            except ValidationError as exc:
                form.add_error(None, exc)
            else:
                messages.success(
                    request,
                    f"Version {release.version} released. Families now see "
                    "these marks.",
                )
                return redirect(self._grid_url(setup))
        return render(request, self.template_name, self._context(setup, form=form))


class TeacherGradebookReportCardView(TeacherRequiredMixin, GradebookEnabledMixin, View):
    """POST only: copy the latest release onto the nominated Draft sheet."""

    def post(self, request, setup_id: int):
        setup = _scoped_setup(request, setup_id)
        try:
            outcome = services.send_to_report_card(
                setup=setup, actor=request.user, request=request
            )
        except ValidationError as exc:
            messages.error(request, exc.messages[0])
        else:
            count = outcome["written"]
            messages.success(
                request,
                f"{count} weighted mark{'' if count == 1 else 's'} sent to the "
                f"{setup.subject.name} report card sheet for "
                f"{setup.term.get_name_display()}. It stays a draft until you "
                "submit it.",
            )
        return redirect("gradebook:teacher_release", setup_id=setup.pk)
