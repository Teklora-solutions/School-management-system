"""
apps/learning/selectors.py
--------------------------
Read-only queries for the Learning module.

A "classroom" is never created by hand: it is computed from data the school
already keeps. For a teacher it is their TeacherAssignment (class + subject +
optional stream) for the current year; for a student it is a subject offered to
their current class. That is what makes online classes appear automatically,
with the right students already enrolled.
"""

from __future__ import annotations

from dataclasses import dataclass

from django.db.models import Q

from apps.academics.models import ClassSubject, SchoolClass, Stream, Subject, Term
from apps.academics.selectors import get_current_term
from apps.homework.selectors import class_roster
from apps.teachers.models import TeacherAssignment


@dataclass
class Classroom:
    """One online classroom: a subject taught to a class (optionally a stream)."""

    school_class: SchoolClass
    subject: Subject
    term: Term | None
    stream: Stream | None = None
    teacher_name: str = ""
    student_count: int = 0

    @property
    def stream_name(self) -> str:
        return self.stream.name if self.stream else ""


def classrooms_for_teacher(*, teacher_user_id: int) -> dict:
    """The signed-in teacher's classrooms for the current academic year."""
    term = get_current_term()
    if term is None:
        return {"term": None, "classrooms": []}

    assignments = (
        TeacherAssignment.objects.filter(
            teacher__user_id=teacher_user_id,
            academic_year_id=term.academic_year_id,
        )
        .select_related("school_class", "subject", "stream", "teacher__user")
        .order_by(
            "school_class__level_order", "school_class__name", "subject__name"
        )
    )

    classrooms = []
    for assignment in assignments:
        student_count = class_roster(
            school_class_id=assignment.school_class_id,
            stream_id=assignment.stream_id,
        ).count()
        classrooms.append(
            Classroom(
                school_class=assignment.school_class,
                subject=assignment.subject,
                term=term,
                stream=assignment.stream,
                teacher_name=assignment.teacher.user.full_name,
                student_count=student_count,
            )
        )
    return {"term": term, "classrooms": classrooms}


def teacher_classroom(
    *, teacher_user_id: int, class_id: int, subject_id: int
) -> TeacherAssignment | None:
    """The teacher's assignment for one classroom, or ``None`` if not theirs.

    Returning the assignment (not a posted stream) keeps the scope guard in one
    place: a teacher cannot open a classroom they are not assigned to.
    """
    term = get_current_term()
    if term is None:
        return None
    return (
        TeacherAssignment.objects.filter(
            teacher__user_id=teacher_user_id,
            school_class_id=class_id,
            subject_id=subject_id,
            academic_year_id=term.academic_year_id,
        )
        .select_related("school_class", "subject", "stream", "teacher__user")
        .first()
    )


def _subjects_for_class(*, student) -> dict[int, ClassSubject]:
    """Subjects offered to the student's class, narrowed to their stream.

    Whole-class entries (stream NULL) always apply; a stream-specific entry is
    preferred over the whole-class one when the student is in that stream.
    """
    qs = ClassSubject.objects.filter(
        school_class_id=student.current_class_id
    ).select_related("subject", "stream")
    if student.current_stream_id:
        qs = qs.filter(
            Q(stream__isnull=True) | Q(stream_id=student.current_stream_id)
        )
    else:
        qs = qs.filter(stream__isnull=True)

    chosen: dict[int, ClassSubject] = {}
    for entry in qs.order_by("subject__name"):
        existing = chosen.get(entry.subject_id)
        if existing is None or (
            entry.stream_id and entry.stream_id == student.current_stream_id
        ):
            chosen[entry.subject_id] = entry
    return chosen


def classrooms_for_student(*, student) -> dict:
    """The classrooms a student is enrolled in (their class's subjects)."""
    term = get_current_term()
    classrooms: list[Classroom] = []
    if term is not None and student.current_class_id:
        chosen = _subjects_for_class(student=student)
        teachers = {
            assignment.subject_id: assignment
            for assignment in TeacherAssignment.objects.filter(
                school_class_id=student.current_class_id,
                academic_year_id=term.academic_year_id,
                subject_id__in=list(chosen.keys()),
            ).select_related("teacher__user")
        }
        for subject_id, entry in chosen.items():
            assignment = teachers.get(subject_id)
            classrooms.append(
                Classroom(
                    school_class=student.current_class,
                    subject=entry.subject,
                    term=term,
                    stream=entry.stream,
                    teacher_name=(
                        assignment.teacher.user.full_name if assignment else ""
                    ),
                )
            )
        classrooms.sort(key=lambda c: c.subject.name)
    return {"term": term, "classrooms": classrooms, "student": student}


def student_classroom(
    *, student, class_id: int, subject_id: int
) -> Classroom | None:
    """One classroom for the student, or ``None`` if it is not theirs.

    ``None`` means another class's classroom (or a subject their class does not
    offer) — the view turns that into a 404, never a 403, so it does not leak
    that the classroom exists.
    """
    if student.current_class_id != class_id:
        return None
    term = get_current_term()
    if term is None:
        return None
    chosen = _subjects_for_class(student=student)
    entry = chosen.get(subject_id)
    if entry is None:
        return None
    assignment = (
        TeacherAssignment.objects.filter(
            school_class_id=class_id,
            subject_id=subject_id,
            academic_year_id=term.academic_year_id,
        )
        .select_related("teacher__user")
        .first()
    )
    return Classroom(
        school_class=student.current_class,
        subject=entry.subject,
        term=term,
        stream=entry.stream,
        teacher_name=assignment.teacher.user.full_name if assignment else "",
    )