"""
apps/learning/poll_selectors.py
---------------------------------
Read-side view of a class poll: the tally, who may see it, and what the
viewer has chosen. Counts are computed from votes on every read, so they
cannot drift when a worker is down.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from django.db.models import Count
from django.utils import timezone

from .models import ClassPoll

MODERATOR_ROLES = {"admin", "director", "teacher"}


@dataclass
class PollOptionRow:
    id: int
    text: str
    count: int
    percent: int
    chosen: bool


@dataclass
class PollSummary:
    poll: ClassPoll
    is_closed: bool
    closes_at: Optional[datetime]
    results_visibility: str
    show_results: bool
    results_hidden: bool
    can_vote: bool
    has_voted: bool
    viewer_option_id: Optional[int]
    viewer_option_text: str
    total_votes: int
    turnout: int
    options: list[PollOptionRow] = field(default_factory=list)
    viewer_is_teacher: bool = False
    viewer_is_parent: bool = False

    @property
    def vote_label(self) -> str:
        return "vote" if self.total_votes == 1 else "votes"


def build_poll_summary(
    poll: ClassPoll, viewer, *, now=None
) -> PollSummary:
    """Everything a template needs to render one poll for one viewer."""
    from apps.students.models import Student

    from .poll_services import polls_enabled_for

    now = now or timezone.now()
    role = getattr(viewer, "role", "")
    is_teacher = role in MODERATOR_ROLES
    is_parent = role == "parent"
    post = poll.post
    is_closed = poll.is_closed(now)

    option_rows = list(poll.options.all())
    counts = {
        row["option_id"]: row["n"]
        for row in poll.votes.values("option_id").annotate(n=Count("id"))
    }
    total_votes = sum(counts.values())

    viewer_vote = None
    if getattr(viewer, "is_authenticated", False):
        viewer_vote = poll.votes.filter(voter=viewer).first()

    enabled = polls_enabled_for(post.school_class)
    student = getattr(viewer, "student_profile", None)
    enrolled = (
        student is not None
        and student.current_class_id == post.school_class_id
    )

    can_vote = bool(
        role == "student"
        and enabled
        and not post.is_removed
        and not is_closed
        and enrolled
    )

    if is_teacher:
        show_results = True
    else:
        show_results = enabled and (
            is_closed
            or poll.results_visibility
            == ClassPoll.ResultsVisibility.LIVE
        )

    options = []
    for option in option_rows:
        count = counts.get(option.pk, 0)
        percent = (
            int(count * 100 / total_votes + 0.5) if total_votes else 0
        )
        options.append(
            PollOptionRow(
                id=option.pk,
                text=option.text,
                count=count,
                percent=percent,
                chosen=viewer_vote is not None
                and viewer_vote.option_id == option.pk,
            )
        )

    viewer_option_text = ""
    if viewer_vote is not None:
        viewer_option_text = next(
            (
                option.text
                for option in option_rows
                if option.pk == viewer_vote.option_id
            ),
            "",
        )

    turnout = Student.objects.filter(
        current_class=post.school_class,
        status=Student.Status.ACTIVE,
    ).count()

    return PollSummary(
        poll=poll,
        is_closed=is_closed,
        closes_at=poll.closes_at,
        results_visibility=poll.results_visibility,
        show_results=show_results,
        results_hidden=not show_results,
        can_vote=can_vote,
        has_voted=viewer_vote is not None,
        viewer_option_id=viewer_vote.option_id if viewer_vote else None,
        viewer_option_text=viewer_option_text,
        total_votes=total_votes,
        turnout=turnout,
        options=options,
        viewer_is_teacher=is_teacher,
        viewer_is_parent=is_parent,
    )
