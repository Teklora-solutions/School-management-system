"""
apps/teachers/views.py
----------------------
Teacher list, detail, assignment management, and class-teacher assignment.
"""

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse, reverse_lazy
from django.views import View
from django.views.generic import DetailView, ListView, TemplateView

from apps.academics.models import AcademicYear, SchoolClass, Stream, Term
from apps.academics.selectors import academic_year_list, get_current_year
from apps.core.mixins import AdminRequiredMixin, SchoolReadMixin, TeacherRequiredMixin
from apps.results import services as results_services
from apps.results.forms import MarksEntryForm
from apps.results.models import ResultSheet
from apps.students.models import Student

from . import selectors, services
from .forms import TeacherAssignmentForm, TeacherFilterForm
from .models import TeacherAssignment

User = get_user_model()


def _selected_year_id(raw) -> int | None:
    """The year a page is showing: the queried one if valid, else the current year."""
    if raw and str(raw).isdigit() and AcademicYear.objects.filter(pk=int(raw)).exists():
        return int(raw)
    current = get_current_year()
    return current.pk if current else None


class TeacherListView(SchoolReadMixin, ListView):
    template_name = "features/teachers/teacher_list.html"
    context_object_name = "teachers"
    paginate_by = 30

    def get_queryset(self):
        self.filter_form = TeacherFilterForm(self.request.GET or None)
        search = ""
        if self.filter_form.is_valid():
            search = self.filter_form.cleaned_data.get("search") or ""
        return selectors.teacher_list(search=search)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["filter_form"] = getattr(self, "filter_form", TeacherFilterForm())
        return ctx


class TeacherDetailView(AdminRequiredMixin, DetailView):
    template_name = "features/teachers/teacher_detail.html"
    context_object_name = "teacher"

    def get_object(self, queryset=None):
        return selectors.get_teacher(self.kwargs["pk"])

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        year_id = _selected_year_id(self.request.GET.get("year"))
        ctx["years"] = academic_year_list()
        ctx["selected_year_id"] = year_id
        ctx["groups"] = selectors.teacher_detail_groups(teacher_pk=self.kwargs["pk"])
        ctx["summary"] = selectors.teacher_profile_summary(
            teacher_pk=self.kwargs["pk"], year_id=year_id
        )
        return ctx


class TeacherAssignmentCreateView(AdminRequiredMixin, View):
    template_name = "features/teachers/assignment_form.html"

    @staticmethod
    def _year_id(request) -> int | None:
        """The year the dropdowns are scoped to: the submitted or queried one, else current."""
        raw = request.POST.get("academic_year") or request.GET.get("year")
        if raw and str(raw).isdigit():
            year_id = int(raw)
            if AcademicYear.objects.filter(pk=year_id).exists():
                return year_id
        current = get_current_year()
        return current.pk if current else None

    def _context(self, form, year_id):
        picker = selectors.assignment_picker(year_id=year_id)
        picker["bound"] = form.is_bound
        return {"form": form, "picker": picker}

    def get(self, request):
        year_id = self._year_id(request)
        initial = {"academic_year": year_id}
        # A class page can open this form already scoped; the class must belong
        # to the year the dropdowns are showing.
        class_id = request.GET.get("class")
        if (
            class_id
            and str(class_id).isdigit()
            and SchoolClass.objects.filter(
                pk=int(class_id), academic_year_id=year_id
            ).exists()
        ):
            initial["school_class"] = int(class_id)
            stream_id = request.GET.get("stream")
            if (
                stream_id
                and str(stream_id).isdigit()
                and Stream.objects.filter(
                    pk=int(stream_id), school_class_id=int(class_id)
                ).exists()
            ):
                initial["stream"] = int(stream_id)
        form = TeacherAssignmentForm(initial=initial)
        return render(request, self.template_name, self._context(form, year_id))

    def post(self, request):
        form = TeacherAssignmentForm(request.POST)
        if form.is_valid():
            try:
                assignment = services.assign_subject_to_teacher(
                    teacher=form.cleaned_data["teacher"],
                    school_class=form.cleaned_data["school_class"],
                    subject=form.cleaned_data["subject"],
                    academic_year=form.cleaned_data["academic_year"],
                    stream=form.cleaned_data.get("stream"),
                    actor=request.user,
                    request=request,
                )
            except ValidationError as exc:
                if hasattr(exc, "message_dict"):
                    for field, errs in exc.message_dict.items():
                        for err in errs:
                            form.add_error(field if field in form.fields else None, err)
                else:
                    form.add_error(None, exc)
            else:
                messages.success(request, f"Assignment created: {assignment}")
                next_url = request.POST.get("next") or reverse_lazy(
                    "teachers:assignment_list"
                )
                return redirect(next_url)
        return render(
            request,
            self.template_name,
            self._context(form, self._year_id(request)),
        )


class TeacherAssignmentListView(AdminRequiredMixin, ListView):
    template_name = "features/teachers/assignment_list.html"
    context_object_name = "assignments"
    paginate_by = 50

    def get_queryset(self):
        year_id = self.request.GET.get("year")
        class_id = self.request.GET.get("class")
        self.year_id = int(year_id) if year_id else None
        self.class_id = int(class_id) if class_id else None
        return selectors.assignment_list(year_id=self.year_id, class_id=self.class_id)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["years"] = academic_year_list()
        ctx["classes"] = SchoolClass.objects.select_related("academic_year").all()
        ctx["selected_year_id"] = getattr(self, "year_id", None)
        ctx["selected_class_id"] = getattr(self, "class_id", None)
        return ctx


class TeacherAssignmentDeleteView(AdminRequiredMixin, View):
    def post(self, request, pk):
        assignment = get_object_or_404(TeacherAssignment, pk=pk)
        next_url = request.POST.get("next") or reverse_lazy("teachers:assignment_list")
        services.remove_assignment(
            assignment=assignment, actor=request.user, request=request
        )
        messages.success(request, "Assignment removed.")
        return redirect(next_url)


class AssignmentMatrixView(AdminRequiredMixin, View):
    template_name = "features/teachers/assignment_matrix.html"

    def get(self, request):
        year_id = request.GET.get("year")
        current_year = get_current_year()
        if not year_id and current_year:
            year_id = current_year.pk
        matrix = selectors.assignment_matrix(year_id=int(year_id)) if year_id else {}
        years = academic_year_list()
        return render(
            request,
            self.template_name,
            {
                "matrix": matrix,
                "years": years,
                "selected_year_id": int(year_id) if year_id else None,
            },
        )


class SubjectDistributionView(AdminRequiredMixin, TemplateView):
    """Every class in a year and who teaches which of its subjects."""

    template_name = "features/teachers/subject_distribution.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        year_id = _selected_year_id(self.request.GET.get("year"))
        ctx["years"] = academic_year_list()
        ctx["selected_year_id"] = year_id
        if year_id:
            ctx.update(selectors.subject_distribution(year_id=year_id))
        else:
            ctx.update(
                {
                    "cards": [],
                    "class_count": 0,
                    "subject_count": 0,
                    "unassigned_count": 0,
                    "conflict_count": 0,
                }
            )
        return ctx


class TeacherWorkloadView(AdminRequiredMixin, TemplateView):
    """All teachers for a year: homeroom, classes, subjects and pupil counts."""

    template_name = "features/teachers/workload.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        year_id = _selected_year_id(self.request.GET.get("year"))
        ctx["years"] = academic_year_list()
        ctx["selected_year_id"] = year_id
        if year_id:
            ctx.update(selectors.teacher_workload(year_id=year_id))
        else:
            ctx.update({"rows": [], "teacher_count": 0, "assigned_count": 0, "homeroom_count": 0})
        return ctx


class SetClassTeacherView(AdminRequiredMixin, View):
    """POST handler used from the class detail page."""

    def post(self, request, pk):
        school_class = get_object_or_404(SchoolClass, pk=pk)
        teacher_user_id = request.POST.get("teacher_user") or None
        teacher_user = None
        if teacher_user_id:
            teacher_user = get_object_or_404(User, pk=teacher_user_id, role="teacher")
        try:
            services.set_class_teacher(
                school_class=school_class,
                teacher_user=teacher_user,
                actor=request.user,
                request=request,
            )
            messages.success(request, "Class teacher updated.")
        except ValidationError as exc:
            messages.error(request, "; ".join(exc.messages))
        return redirect("academics:class_detail", pk=pk)


class MyClassesView(TeacherRequiredMixin, TemplateView):
    """The signed-in teacher's own classes for the current academic year."""

    template_name = "features/teachers/my_classes.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        if not hasattr(self.request.user, "teacher_profile"):
            return ctx
        ctx.update(
            selectors.current_year_classes_for_teacher(
                teacher_user_id=self.request.user.pk
            )
        )
        return ctx


class MarksEntryListView(TeacherRequiredMixin, TemplateView):
    """Teacher: pick one of their subject assignments to enter exam marks."""

    template_name = "features/teachers/marks_list.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx.update(
            selectors.assignments_for_marks(teacher_user_id=self.request.user.pk)
        )
        return ctx


class MarksEntryView(TeacherRequiredMixin, TemplateView):
    """
    Teacher: enter exam marks for one class + subject (+ stream) assignment.

    Marks are saved as a draft sheet; "Submit to admin" hands it over for
    confirmation. Once submitted the teacher can no longer edit it.
    """

    template_name = "features/teachers/marks_entry.html"

    def _assignment(self) -> TeacherAssignment:
        profile = getattr(self.request.user, "teacher_profile", None)
        if profile is None:
            raise PermissionDenied("No teacher profile is linked to your account.")
        return get_object_or_404(
            TeacherAssignment.objects.select_related(
                "school_class", "subject", "stream", "academic_year"
            ),
            pk=self.kwargs["assignment_id"],
            teacher=profile,
        )

    @staticmethod
    def _resolve_term_exam(data, assignment):
        terms = list(
            Term.objects.filter(academic_year=assignment.academic_year).order_by(
                "name"
            )
        )
        wanted_term = data.get("term")
        term = next((t for t in terms if str(t.pk) == wanted_term), None)
        if term is None:
            term = next((t for t in terms if t.is_current), terms[0] if terms else None)

        exams: list = []
        exam = None
        if term is not None:
            exams = results_services.ensure_exams_for_term(term)
            wanted_exam = data.get("exam")
            exam = next((e for e in exams if str(e.pk) == wanted_exam), None)
            if exam is None:
                exam = exams[0] if exams else None
        return terms, term, exams, exam

    def _sheet(self, assignment, term, exam) -> ResultSheet:
        sheet, _ = ResultSheet.objects.get_or_create(
            school_class=assignment.school_class,
            subject=assignment.subject,
            term=term,
            exam=exam,
            stream=assignment.stream,
            defaults={"teacher": self.request.user.teacher_profile},
        )
        if sheet.teacher_id is None:
            sheet.teacher = self.request.user.teacher_profile
            sheet.save(update_fields=["teacher", "updated_at"])
        return sheet

    @staticmethod
    def _students(assignment) -> list:
        qs = Student.objects.filter(
            current_class=assignment.school_class,
            status__in=[Student.Status.ACTIVE, Student.Status.REPEATING],
        )
        if assignment.stream_id:
            qs = qs.filter(current_stream_id=assignment.stream_id)
        return list(qs.select_related("user").order_by("admission_number"))

    def _context(self, assignment, terms, term, exams, exam, sheet, students, form):
        entries = (
            {entry.student_id: entry for entry in sheet.entries.all()} if sheet else {}
        )
        rows = [
            {
                "student": student,
                "field": form[f"score_{student.pk}"] if form else None,
                "entry": entries.get(student.pk),
            }
            for student in students
        ]
        return {
            "assignment": assignment,
            "terms": terms,
            "term": term,
            "exams": exams,
            "exam": exam,
            "sheet": sheet,
            "students": students,
            "form": form,
            "rows": rows,
            "can_edit": sheet is not None
            and sheet.status == ResultSheet.Status.DRAFT,
            "entered_count": len(entries),
        }

    def get(self, request, assignment_id):
        assignment = self._assignment()
        terms, term, exams, exam = self._resolve_term_exam(request.GET, assignment)

        sheet = None
        students: list = []
        form = None
        if term is not None and exam is not None:
            sheet = self._sheet(assignment, term, exam)
            students = self._students(assignment)
            initial = {
                f"score_{entry.student_id}": entry.raw_score
                for entry in sheet.entries.all()
            }
            form = MarksEntryForm(students=students, initial=initial)

        return self.render_to_response(
            self._context(assignment, terms, term, exams, exam, sheet, students, form)
        )

    def post(self, request, assignment_id):
        assignment = self._assignment()
        terms, term, exams, exam = self._resolve_term_exam(request.POST, assignment)
        if term is None or exam is None:
            messages.error(request, "Choose a term and exam first.")
            return redirect("teachers:marks_list")

        sheet = self._sheet(assignment, term, exam)
        students = self._students(assignment)
        form = MarksEntryForm(request.POST, students=students)
        ctx = self._context(
            assignment, terms, term, exams, exam, sheet, students, form
        )

        if sheet.status != ResultSheet.Status.DRAFT:
            messages.error(
                request,
                "This sheet has been submitted to the admin and can no longer be edited.",
            )
            return self.render_to_response(ctx)

        if not form.is_valid():
            messages.error(request, "Marks must be between 0 and 100.")
            return self.render_to_response(ctx)

        scores = form.scores()
        for student in students:
            results_services.upsert_score_entry(
                sheet=sheet,
                student=student,
                raw_score=scores.get(student.pk),
                actor=request.user,
                request=request,
            )

        query = f"?term={term.pk}&exam={exam.pk}"
        target = reverse("teachers:marks_entry", args=[assignment.pk]) + query

        if request.POST.get("action") == "submit":
            if not sheet.entries.exists():
                messages.error(
                    request, "Enter at least one mark before submitting to the admin."
                )
                return self.render_to_response(ctx)
            results_services.submit_sheet(
                sheet=sheet, actor=request.user, request=request
            )
            messages.success(
                request, "Marks submitted to the admin for confirmation."
            )
        else:
            messages.success(request, "Marks saved as draft.")
        return redirect(target)
