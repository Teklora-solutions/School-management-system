"""
apps/learning/calendar_selectors.py
-----------------------------------
Aggregates class, teacher, and student calendar events across:
  - Live Classes (LiveClassSession)
  - Homework deadlines (Homework)
  - Quizzes (Quiz)
  - Academic Term dates (Term)
"""

from __future__ import annotations

import calendar as py_calendar
import datetime
from typing import Any

from django.urls import reverse
from django.utils import timezone

from apps.academics.models import Term
from apps.homework.models import Homework
from apps.learning.models import LiveClassSession, Quiz


def get_class_calendar_data(
    school_class_id: int,
    year: int | None = None,
    month: int | None = None,
) -> dict[str, Any]:
    """
    Build monthly calendar matrix and agenda for a given school class.
    """
    today = timezone.localdate()
    if not year or not month:
        year = today.year
        month = today.month

    # Clamp year and month
    if month < 1:
        month = 12
        year -= 1
    elif month > 12:
        month = 1
        year += 1

    first_day_of_month = datetime.date(year, month, 1)
    last_day_num = py_calendar.monthrange(year, month)[1]
    last_day_of_month = datetime.date(year, month, last_day_num)

    # 1. Fetch Live Class Sessions for this class in date range
    # Expanding range slightly to cover calendar view padding days
    grid_start = first_day_of_month - datetime.timedelta(days=7)
    grid_end = last_day_of_month + datetime.timedelta(days=7)

    events: list[dict[str, Any]] = []

    live_sessions = (
        LiveClassSession.objects.filter(
            school_class_id=school_class_id,
            scheduled_start__date__gte=grid_start,
            scheduled_start__date__lte=grid_end,
        )
        .exclude(status=LiveClassSession.Status.CANCELLED)
        .select_related("subject", "teacher")
    )
    for s in live_sessions:
        s_date = timezone.localtime(s.scheduled_start).date()
        s_time = timezone.localtime(s.scheduled_start).strftime("%H:%M")
        events.append(
            {
                "id": f"live_{s.pk}",
                "title": s.title,
                "category": "live_class",
                "category_label": "Live Class",
                "date": s_date,
                "time_str": s_time,
                "badge_class": "badge bg-danger",
                "subject_code": s.subject.code,
                "subject_name": s.subject.name,
                "is_live": s.is_live,
                "platform": s.get_platform_display(),
                "url": reverse("learning:live_session_detail", kwargs={"session_id": s.pk}),
            }
        )

    # 2. Fetch published homework due dates for this class
    homeworks = (
        Homework.objects.filter(
            school_class_id=school_class_id,
            status=Homework.Status.PUBLISHED,
            due_date__gte=grid_start,
            due_date__lte=grid_end,
        )
        .select_related("subject")
    )
    for hw in homeworks:
        events.append(
            {
                "id": f"hw_{hw.pk}",
                "title": f"Due: {hw.title}",
                "category": "homework",
                "category_label": "Homework Due",
                "date": hw.due_date,
                "time_str": "Due date",
                "badge_class": "badge bg-warning text-dark",
                "subject_code": hw.subject.code,
                "subject_name": hw.subject.name,
                "is_live": False,
                "platform": "",
                "url": reverse("homework:detail", kwargs={"pk": hw.pk})
                if _can_reverse("homework:detail")
                else "#",
            }
        )

    # 3. Fetch published quizzes for this class
    quizzes = (
        Quiz.objects.filter(
            is_published=True,
            lesson__module__school_class_id=school_class_id,
            created_at__date__gte=grid_start,
            created_at__date__lte=grid_end,
        )
        .select_related("lesson__module__subject")
    )
    for q in quizzes:
        q_date = timezone.localtime(q.created_at).date()
        subject = q.lesson.module.subject
        events.append(
            {
                "id": f"quiz_{q.pk}",
                "title": f"Quiz: {q.title}",
                "category": "quiz",
                "category_label": "Quiz",
                "date": q_date,
                "time_str": "Available",
                "badge_class": "badge bg-info text-dark",
                "subject_code": subject.code,
                "subject_name": subject.name,
                "is_live": False,
                "platform": "",
                "url": reverse(
                    "learning:quiz_landing",
                    kwargs={
                        "class_id": school_class_id,
                        "subject_id": subject.pk,
                        "lesson_id": q.lesson_id,
                    },
                ),
            }
        )

    # 4. Fetch term milestones
    terms = Term.objects.filter(
        models_or_start_end(grid_start, grid_end)
    ).select_related("academic_year")
    for t in terms:
        if grid_start <= t.start_date <= grid_end:
            events.append(
                {
                    "id": f"term_start_{t.pk}",
                    "title": f"{t.academic_year.name} {t.get_name_display()} Begins",
                    "category": "term",
                    "category_label": "Term Start",
                    "date": t.start_date,
                    "time_str": "Milestone",
                    "badge_class": "badge bg-success",
                    "subject_code": "ACAD",
                    "subject_name": "Academic Term",
                    "is_live": False,
                    "platform": "",
                    "url": "#",
                }
            )
        if grid_start <= t.end_date <= grid_end:
            events.append(
                {
                    "id": f"term_end_{t.pk}",
                    "title": f"{t.academic_year.name} {t.get_name_display()} Ends",
                    "category": "term",
                    "category_label": "Term End",
                    "date": t.end_date,
                    "time_str": "Milestone",
                    "badge_class": "badge bg-secondary",
                    "subject_code": "ACAD",
                    "subject_name": "Academic Term",
                    "is_live": False,
                    "platform": "",
                    "url": "#",
                }
            )

    # Build month matrix
    cal = py_calendar.Calendar(firstweekday=0)  # Monday start
    weeks = cal.monthdatescalendar(year, month)

    calendar_weeks = []
    for week in weeks:
        week_days = []
        for day in week:
            day_events = [ev for ev in events if ev["date"] == day]
            week_days.append(
                {
                    "date": day,
                    "day_number": day.day,
                    "is_current_month": day.month == month,
                    "is_today": day == today,
                    "events": day_events,
                }
            )
        calendar_weeks.append(week_days)

    # Agenda view: upcoming events from today onwards, sorted by date
    agenda_events = [ev for ev in events if ev["date"] >= today]
    agenda_events.sort(key=lambda ev: (ev["date"], ev["time_str"]))

    # Prev and next month calculations
    if month == 1:
        prev_month, prev_year = 12, year - 1
    else:
        prev_month, prev_year = month - 1, year

    if month == 12:
        next_month, next_year = 1, year + 1
    else:
        next_month, next_year = month + 1, year

    month_name = py_calendar.month_name[month]

    return {
        "year": year,
        "month": month,
        "month_name": month_name,
        "prev_year": prev_year,
        "prev_month": prev_month,
        "next_year": next_year,
        "next_month": next_month,
        "calendar_weeks": calendar_weeks,
        "agenda_events": agenda_events[:15],
        "total_events": len(events),
    }


def models_or_start_end(start_date: datetime.date, end_date: datetime.date):
    from django.db.models import Q
    return Q(start_date__gte=start_date, start_date__lte=end_date) | Q(
        end_date__gte=start_date, end_date__lte=end_date
    )


def _can_reverse(name: str) -> bool:
    try:
        reverse(name, kwargs={"pk": 1})
        return True
    except Exception:
        return False

