"""
apps/learning/poll_services.py
--------------------------------
Class polls on the community feed.

Rules (agreed for F2b)
----------------------
* Teachers create a poll as a post with 2–6 options. The question is the
  post body, so it is sanitised, length-capped and moderated exactly like
  every other post.
* One vote per student is enforced by the database: the unique constraint
  ``uniq_poll_vote_per_student`` on (poll, voter). Changing a vote before
  the poll closes updates that one row.
* A vote is refused once ``closes_at`` has passed. Closing is decided by
  comparing the clock on every read and on every POST; no worker or
  scheduled job is involved, so a poll still closes when django-q is down.
* Only students currently in the poll's class may vote. The view and this
  service both check it.
* Teachers always see the tally. Students and parents see it live or only
  after close, depending on the teacher's choice. Nobody sees who voted.
* Voting earns no points; there is nothing to farm by tapping.
* A removed poll post is invisible to students and parents (the normal feed
  visibility rule) and voting is refused. Votes are kept for the record.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import Count
from django.utils import timezone

from .models import (
    ClassFeedSettings,
    ClassPoll,
    ClassPollOption,
    ClassPollVote,
    ClassPost,
)
from .poll_selectors import build_poll_summary

MIN_OPTIONS = 2
MAX_OPTIONS = 6
OPTION_MAX_LENGTH = 120

MODERATOR_ROLES = {"admin", "director", "teacher"}


def polls_enabled_for(school_class) -> bool:
    """
    Polls need the community feed on AND this class's polls switch on.

    The polls switch defaults off, so no class gains polls until a teacher
    deliberately turns them on.
    """
    from .feed_selectors import community_enabled_for

    if not community_enabled_for(school_class):
        return False
    return bool(
        ClassFeedSettings.objects.filter(
            school_class=school_class, polls_enabled=True
        ).exists()
    )


def _assert_polls_on(school_class) -> None:
    if not polls_enabled_for(school_class):
        raise PermissionDenied(
            "Polls are not switched on for this class."
        )


@transaction.atomic
def create_poll(
    *,
    author,
    school_class,
    question: str,
    options: list[str],
    results_visibility: str = ClassPoll.ResultsVisibility.AFTER_CLOSE,
    closes_at=None,
) -> ClassPoll:
    """
    Create a poll post and its options.

    ``closes_at`` arrives as an aware datetime (the view interprets the
    browser's Nairobi-time input). It may be None for a poll with no
    automatic close.
    """
    from .feed_services import create_post

    if getattr(author, "role", "") not in MODERATOR_ROLES:
        raise PermissionDenied("Only teachers can create a poll.")
    _assert_polls_on(school_class)

    question = (question or "").strip()
    if not question:
        raise ValueError("A poll needs a question.")
    if len(question) > 2000:
        raise ValueError("The poll question cannot exceed 2000 characters.")

    cleaned = [(option or "").strip() for option in options]
    cleaned = [option for option in cleaned if option]
    if not MIN_OPTIONS <= len(cleaned) <= MAX_OPTIONS:
        raise ValueError("A poll needs between 2 and 6 options.")
    if any(len(option) > OPTION_MAX_LENGTH for option in cleaned):
        raise ValueError(
            f"Each option must be {OPTION_MAX_LENGTH} characters or fewer."
        )
    folded = [option.casefold() for option in cleaned]
    if len(set(folded)) != len(folded):
        raise ValueError("Each option must be different.")
    if results_visibility not in ClassPoll.ResultsVisibility.values:
        raise ValueError("Choose when pupils may see the results.")
    if (
        results_visibility == ClassPoll.ResultsVisibility.AFTER_CLOSE
        and closes_at is None
    ):
        raise ValueError(
            "Choose a closing time, or show the results while voting."
        )

    if closes_at is not None and closes_at <= timezone.now():
        raise ValueError("The closing time must be in the future.")

    post = create_post(
        author=author,
        school_class=school_class,
        body=question,
        post_type=ClassPost.PostType.POLL,
        allow_poll=True,
    )
    poll = ClassPoll.objects.create(
        post=post,
        results_visibility=results_visibility,
        closes_at=closes_at,
    )
    ClassPollOption.objects.bulk_create(
        [
            ClassPollOption(poll=poll, text=text, position=index)
            for index, text in enumerate(cleaned, start=1)
        ]
    )
    return poll


@transaction.atomic
def cast_vote(*, poll: ClassPoll, voter, option_id) -> ClassPollVote:
    """
    Record or change *voter*'s vote.

    The student's role and class are checked here as well as in the view.
    The unique constraint on (poll, voter) makes a second vote impossible
    even if two requests arrive together.
    """
    post = poll.post
    if getattr(voter, "role", "") != "student":
        raise PermissionDenied("Only students can vote in a class poll.")
    if post.is_removed:
        raise PermissionDenied("This poll has been removed.")
    _assert_polls_on(post.school_class)

    student = getattr(voter, "student_profile", None)
    if student is None or student.current_class_id != post.school_class_id:
        raise PermissionDenied(
            "You can only vote in polls for your own class."
        )

    if poll.is_closed():
        raise PermissionDenied("This poll has closed; votes can no longer change.")

    option = poll.options.filter(pk=option_id).first()
    if option is None:
        raise ValueError("Choose one of the poll options.")

    vote, _created = ClassPollVote.objects.update_or_create(
        poll=poll,
        voter=voter,
        defaults={"option": option},
    )
    return vote


def poll_question(post: ClassPost) -> str:
    """The question text; kept separate so templates never rebuild it."""
    return post.body
