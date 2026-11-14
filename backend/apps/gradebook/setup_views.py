"""
apps/gradebook/setup_views.py
-----------------------------
The teacher's editing screens behind the grid: weights, hand-recorded work,
its marks, and one-pupil excuses.

Every view resolves the setup through the signed-in teacher's assignment for
that classroom and term, so the object id in the URL can never widen the scope.
"""

from __future__ import annotations

from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views import View

from apps.core.mixins import TeacherRequiredMixin

from . import services
from .forms import ExcuseForm, ManualItemForm, ManualScoresForm, WeightsForm
from .models import GradebookExcuse, GradebookSetup
from .views import GradebookEnabledMixin


def _scoped_setup(request, setup_id: int) -> GradebookSetup:
    setup = get_object_or_404(
        GradebookSetup.objects.select_related(
            "school_class", "subject", "term", "stream", "exam_sheet__exam"
        ),
        pk=setup_id,
    )
    assignment = services.resolve_classroom(
        teacher_user_id=request.user.pk,
        school_class_id=setup.school_class_id,
        subject_id=setup.subject_id,
        term=setup.term,
    )
    if (assignment.stream_id or None) != (setup.stream_id or None):
        raise PermissionDenied("You are not assigned to this gradebook.")
    return setup


def _setup_context(
    request,
    setup: GradebookSetup,
    *,
    weights_form=None,
    excuse_form=None,
) -> dict:
    items = services.collect_items(setup)
    labels = {
        (item["source_kind"], item["source_id"]): (
            f"{item['bucket_label']}: {item['title']}"
        )
        for item in items
    }
    excuses = [
        {
            "excuse": excuse,
            "label": labels.get(
                (excuse.source_kind, excuse.source_id),
                f"{excuse.get_source_kind_display()} #{excuse.source_id}",
            ),
        }
        for excuse in setup.excuses.select_related("student__user").order_by(
            "student__user__last_name", "student__admission_number", "source_kind"
        )
    ]
    return {
        "setup": setup,
        "term": setup.term,
        "locked": services.term_is_locked(setup.term),
        "items": items,
        "manual_items": list(setup.manual_items.prefetch_related("scores")),
        "weights_form": weights_form or WeightsForm(setup=setup),
        "excuse_form": excuse_form or ExcuseForm(setup=setup, items=items),
        "excuses": excuses,
        "grid_href": (
            reverse(
                "gradebook:teacher_grid",
                args=[setup.school_class_id, setup.subject_id],
            )
            + f"?term={setup.term_id}"
        ),
    }


class TeacherGradebookSetupView(TeacherRequiredMixin, GradebookEnabledMixin, View):
    template_name = "features/gradebook/teacher_setup.html"

    def get(self, request, setup_id: int):
        setup = _scoped_setup(request, setup_id)
        return render(request, self.template_name, _setup_context(request, setup))

    def post(self, request, setup_id: int):
        setup = _scoped_setup(request, setup_id)
        action = request.POST.get("action")

        if action == "weights":
            form = WeightsForm(request.POST, setup=setup)
            if form.is_valid():
                try:
                    services.update_weights(
                        setup=setup,
                        homework_weight=form.cleaned_data["homework_weight"],
                        quiz_weight=form.cleaned_data["quiz_weight"],
                        lab_weight=form.cleaned_data["lab_weight"],
                        exam_weight=form.cleaned_data["exam_weight"],
                        exam_sheet=form.cleaned_data.get("exam_sheet"),
                        actor=request.user,
                        request=request,
                    )
                except ValidationError as exc:
                    form.add_error(None, exc)
                else:
                    messages.success(request, "Weights updated.")
                    return redirect("gradebook:teacher_setup", setup_id=setup.pk)
            return render(
                request,
                self.template_name,
                _setup_context(request, setup, weights_form=form),
            )

        if action == "excuse":
            items = services.collect_items(setup)
            form = ExcuseForm(request.POST, setup=setup, items=items)
            if form.is_valid():
                try:
                    services.record_excuse(
                        setup=setup,
                        student=form.cleaned_data["student"],
                        source_kind=form.cleaned_data["source_kind"],
                        source_id=form.cleaned_data["source_id"],
                        reason=form.cleaned_data["reason"],
                        actor=request.user,
                        request=request,
                    )
                except ValidationError as exc:
                    form.add_error(None, exc)
                else:
                    messages.success(
                        request, "The pupil is excused from that piece of work."
                    )
                    return redirect("gradebook:teacher_setup", setup_id=setup.pk)
            return render(
                request,
                self.template_name,
                _setup_context(request, setup, excuse_form=form),
            )

        messages.error(request, "Unknown action.")
        return redirect("gradebook:teacher_setup", setup_id=setup.pk)


class TeacherManualItemView(TeacherRequiredMixin, GradebookEnabledMixin, View):
    """Create or edit one hand-recorded item and, once it exists, its marks."""

    template_name = "features/gradebook/manual_item_form.html"

    def _item(self, request, setup: GradebookSetup, item_id: int | None):
        if item_id is None:
            return None
        return get_object_or_404(setup.manual_items, pk=item_id)

    def _context(self, request, setup, item, *, item_form=None, scores_form=None):
        return {
            "setup": setup,
            "term": setup.term,
            "item": item,
            "locked": services.term_is_locked(setup.term),
            "item_form": item_form or ManualItemForm(setup=setup, instance=item),
            "scores_form": scores_form
            or (ManualScoresForm(setup=setup, item=item) if item else None),
            "has_marks": bool(item and item.scores.exists()),
            "setup_href": reverse("gradebook:teacher_setup", args=[setup.pk]),
        }

    def get(self, request, setup_id: int, item_id: int | None = None):
        setup = _scoped_setup(request, setup_id)
        item = self._item(request, setup, item_id)
        return render(
            request, self.template_name, self._context(request, setup, item)
        )

    def post(self, request, setup_id: int, item_id: int | None = None):
        setup = _scoped_setup(request, setup_id)
        item = self._item(request, setup, item_id)
        action = request.POST.get("action", "item")

        if action == "scores":
            if item is None:
                messages.error(request, "Save the item before entering marks.")
                return redirect("gradebook:manual_item_create", setup_id=setup.pk)
            form = ManualScoresForm(request.POST, setup=setup, item=item)
            if form.is_valid():
                scores = {
                    student.pk: form.cleaned_data.get(f"score_{student.pk}")
                    for student, _field in form.score_fields
                }
                try:
                    result = services.save_manual_scores(
                        item=item,
                        scores=scores,
                        actor=request.user,
                        request=request,
                    )
                except ValidationError as exc:
                    form.add_error(None, exc)
                else:
                    messages.success(
                        request,
                        f"{result['saved']} mark"
                        f"{'' if result['saved'] == 1 else 's'} saved"
                        + (
                            f"; {result['cleared']} cleared."
                            if result["cleared"]
                            else "."
                        ),
                    )
                    return redirect(
                        "gradebook:manual_item_edit",
                        setup_id=setup.pk,
                        item_id=item.pk,
                    )
            return render(
                request,
                self.template_name,
                self._context(request, setup, item, scores_form=form),
            )

        form = ManualItemForm(request.POST, setup=setup, instance=item)
        if form.is_valid():
            data = form.cleaned_data
            try:
                if item is None:
                    item = services.create_manual_item(
                        setup=setup,
                        bucket=data["bucket"],
                        title=data["title"],
                        max_score=data["max_score"],
                        worked_on=data["worked_on"],
                        reason=data["reason"],
                        counts_for_gradebook=data["counts_for_gradebook"],
                        actor=request.user,
                        request=request,
                    )
                    messages.success(
                        request, "Item saved. You can enter the marks now."
                    )
                    return redirect(
                        "gradebook:manual_item_edit",
                        setup_id=setup.pk,
                        item_id=item.pk,
                    )
                services.update_manual_item(
                    item=item,
                    bucket=data["bucket"],
                    title=data["title"],
                    max_score=data["max_score"],
                    worked_on=data["worked_on"],
                    reason=data["reason"],
                    counts_for_gradebook=data["counts_for_gradebook"],
                    actor=request.user,
                    request=request,
                )
            except ValidationError as exc:
                form.add_error(None, exc)
            else:
                messages.success(request, "Item updated.")
                return redirect(
                    "gradebook:manual_item_edit",
                    setup_id=setup.pk,
                    item_id=item.pk,
                )

        return render(
            request,
            self.template_name,
            self._context(request, setup, item, item_form=form),
        )


class TeacherManualItemRemoveView(TeacherRequiredMixin, GradebookEnabledMixin, View):
    def post(self, request, setup_id: int, item_id: int):
        setup = _scoped_setup(request, setup_id)
        item = get_object_or_404(setup.manual_items, pk=item_id)
        try:
            services.remove_manual_item(
                item=item, actor=request.user, request=request
            )
        except ValidationError as exc:
            messages.error(request, exc.messages[0])
        else:
            messages.success(request, "Item removed.")
        return redirect("gradebook:teacher_setup", setup_id=setup.pk)


class TeacherExcuseRevokeView(TeacherRequiredMixin, GradebookEnabledMixin, View):
    def post(self, request, setup_id: int, excuse_id: int):
        setup = _scoped_setup(request, setup_id)
        excuse = get_object_or_404(GradebookExcuse, pk=excuse_id, setup=setup)
        try:
            services.revoke_excuse(
                excuse=excuse, actor=request.user, request=request
            )
        except ValidationError as exc:
            messages.error(request, exc.messages[0])
        else:
            messages.success(
                request, "The excuse was revoked; the item counts again."
            )
        return redirect("gradebook:teacher_setup", setup_id=setup.pk)
