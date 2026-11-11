"""
apps/learning/feed_selectors.py
---------------------------------
Read-only queries for the class community feed.

Public API
----------
  feed_for_classroom(school_class, viewer)
      The original notice-board list (community feed off): pinned first,
      newest-first within each group.

  feed_page(school_class, viewer, filter_key, before_id, limit)
      One page of the community feed with a separate pinned strip, a type
      filter and an id cursor for "Show more".

  post_detail(post_id, viewer)
      Single post with the same visibility rules.

  post_thread(post_id, viewer)
      One post plus its visible replies and question status.

  new_posts_count(school_class, viewer, after_id)
  latest_visible_post_id(school_class, viewer)
      Cheap queries behind the polite poll.

  reaction_summary(post)
      Dict mapping emoji → count for one post.

  community_enabled_for(school_class)
      School switch AND class switch are both on.

  can_post / can_reply / can_mark_best / can_moderate_reply
      Boolean permission helpers used by views and templates.

  classrooms_for_feed(user)
      The classes whose feed this user may open (for the feed home chooser).
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Optional

from django.db.models import Count, Exists, OuterRef, Q
from django.urls import NoReverseMatch, reverse

from .models import ClassFeedSettings, ClassPost, ClassPostReaction, ClassPostReply

# Roles that can see the feed at all (teacher also sees removed posts).
VIEWER_ROLES = {"admin", "director", "teacher", "student", "parent"}
MODERATOR_ROLES = {"admin", "director", "teacher"}
POSTER_ROLES = {"admin", "director", "teacher", "student"}

#: Filter chips in display order. The slug is what appears in ``?filter=``.
FEED_FILTERS = [
    ("all", "All"),
    ("questions", "Questions"),
    ("announcements", "Announcements"),
    ("pinned", "Pinned"),
    ("mine", "Mine"),
]

#: Teacher triage queue: questions with no visible reply yet.
MODERATOR_FILTERS = [("unanswered", "Unanswered")]


def feed_filters_for(viewer) -> list[tuple[str, str]]:
    """The chips this viewer may use; only teachers get the Unanswered queue."""
    if getattr(viewer, "role", "") in MODERATOR_ROLES:
        return FEED_FILTERS + MODERATOR_FILTERS
    return FEED_FILTERS

#: Posts per page. Small on purpose: a class feed on a weak connection.
PAGE_SIZE = 10
PINNED_LIMIT = 3


# ---------------------------------------------------------------------------
# Data objects
# ---------------------------------------------------------------------------


@dataclass
class ReactionSummary:
    """Counts per emoji for one post, plus which emoji the viewer chose."""
    counts: dict[str, int] = field(default_factory=dict)   # emoji → count
    viewer_emoji: Optional[str] = None                      # what the viewer chose

    @property
    def total(self) -> int:
        return sum(self.counts.values())

    @property
    def rows(self) -> list[dict]:
        """One row per allowed emoji, ready for the template."""
        return [
            {
                "emoji": emoji,
                "label": label,
                "count": self.counts.get(emoji, 0),
                "active": emoji == self.viewer_emoji,
            }
            for emoji, label in ClassPostReaction.Emoji.choices
        ]


@dataclass
class PostWithReactions:
    """A ClassPost bundled with its reaction summary and reply status."""
    post: ClassPost
    reactions: ReactionSummary
    reply_count: int = 0
    has_best_answer: bool = False
    answered: bool = False
    lesson_link: Optional[dict] = None
    quiz_link: Optional[dict] = None

    @property
    def is_removed(self) -> bool:
        return self.post.is_removed

    @property
    def is_question(self) -> bool:
        return self.post.post_type == ClassPost.PostType.QUESTION

    @property
    def question_state(self) -> str:
        if not self.is_question:
            return ""
        return "answered" if self.answered else "waiting"

    @property
    def question_label(self) -> str:
        if not self.is_question:
            return ""
        return "Answered" if self.answered else "Waiting for an answer"


@dataclass
class FeedPage:
    posts: list[PostWithReactions]
    pinned: list[PostWithReactions]
    has_more: bool
    next_before_id: Optional[int]
    filter_key: str
    latest_visible_id: int


@dataclass
class PostThread:
    post: PostWithReactions
    replies: list[ClassPostReply]
    question_state: str
    best_reply_id: Optional[int]
    can_reply: bool
    can_moderate: bool
    can_choose_best: bool


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def is_moderator(user) -> bool:
    return getattr(user, "role", "") in MODERATOR_ROLES


def _feature_row(request=None):
    from apps.core.selectors import school_settings_values

    return school_settings_values(request)


def community_enabled_for(school_class, *, request=None) -> bool:
    """
    The class community feed needs BOTH switches on:
    the school-wide ``feed_community_enabled`` and this class's
    ``ClassFeedSettings.is_active``. Either one off means off.
    """
    if not _feature_row(request)["feed_community_enabled"]:
        return False
    # Query directly: the reverse one-to-one descriptor caches its row on the
    # class instance, so a switch flipped moments ago would still read stale.
    is_active = (
        ClassFeedSettings.objects.filter(school_class=school_class)
        .values_list("is_active", flat=True)
        .first()
    )
    return bool(is_active)


def can_post(user, school_class) -> bool:
    """True if user can create at least one post type in this class."""
    role = getattr(user, "role", "")
    if role not in POSTER_ROLES:
        return False
    if role in ("admin", "director"):
        return True
    if role == "teacher":
        from apps.teachers.models import TeacherAssignment
        from apps.academics.selectors import get_current_year
        year = get_current_year()
        if year is None:
            return False
        assigned = TeacherAssignment.objects.filter(
            teacher__user=user,
            school_class=school_class,
            academic_year=year,
        ).exists()
        is_class_teacher = school_class.class_teacher_id == user.pk
        return assigned or is_class_teacher
    if role == "student":
        student = getattr(user, "student_profile", None)
        return student is not None and student.current_class_id == school_class.pk
    return False


def can_view_feed(user, school_class) -> bool:
    """True if user may see the feed for this class."""
    role = getattr(user, "role", "")
    if role in ("admin", "director"):
        return True
    if role == "teacher":
        from apps.teachers.models import TeacherAssignment
        from apps.academics.selectors import get_current_year
        year = get_current_year()
        if year is None:
            return False
        assigned = TeacherAssignment.objects.filter(
            teacher__user=user,
            school_class=school_class,
            academic_year=year,
        ).exists()
        is_class_teacher = school_class.class_teacher_id == user.pk
        return assigned or is_class_teacher
    if role == "student":
        student = getattr(user, "student_profile", None)
        return student is not None and student.current_class_id == school_class.pk
    if role == "parent":
        parent = getattr(user, "parent_profile", None)
        if parent is None:
            return False
        return parent.children_links.filter(
            student__current_class=school_class
        ).exists()
    return False


def can_reply(user, post, school_class=None) -> bool:
    if post.is_removed:
        return False
    school_class = school_class or post.school_class
    if getattr(user, "role", "") not in POSTER_ROLES:
        return False
    if not community_enabled_for(school_class):
        return False
    return can_post(user, school_class)


def can_mark_best(user, post, school_class=None) -> bool:
    if post.post_type != ClassPost.PostType.QUESTION or post.is_removed:
        return False
    school_class = school_class or post.school_class
    if not community_enabled_for(school_class):
        return False
    role = getattr(user, "role", "")
    if role in MODERATOR_ROLES:
        return can_view_feed(user, school_class)
    return post.author_id is not None and post.author_id == user.pk


def can_moderate_reply(user, post, school_class=None) -> bool:
    school_class = school_class or post.school_class
    return (
        getattr(user, "role", "") in MODERATOR_ROLES
        and community_enabled_for(school_class)
        and can_view_feed(user, school_class)
    )


def classrooms_for_feed(user) -> list:
    """Classes whose feed this user may open. Small list for the chooser."""
    from apps.academics.models import SchoolClass
    from apps.academics.selectors import get_current_year

    role = getattr(user, "role", "")
    year = get_current_year()

    if role in ("admin", "director"):
        qs = SchoolClass.objects.all()
        if year is not None:
            qs = qs.filter(academic_year=year)
        return list(qs.order_by("level_order", "name"))

    if role == "teacher":
        if year is None:
            return []
        from apps.teachers.models import TeacherAssignment

        ids = set(
            TeacherAssignment.objects.filter(
                teacher__user=user, academic_year=year
            ).values_list("school_class_id", flat=True)
        )
        ids |= set(
            SchoolClass.objects.filter(
                academic_year=year, class_teacher=user
            ).values_list("pk", flat=True)
        )
        return list(
            SchoolClass.objects.filter(pk__in=ids).order_by("level_order", "name")
        )

    if role == "student":
        student = getattr(user, "student_profile", None)
        if student is None or student.current_class_id is None:
            return []
        return [student.current_class]

    if role == "parent":
        parent = getattr(user, "parent_profile", None)
        if parent is None:
            return []
        ids = parent.children_links.values_list(
            "student__current_class_id", flat=True
        )
        return list(
            SchoolClass.objects.filter(pk__in=ids).order_by("level_order", "name")
        )

    return []


# ---------------------------------------------------------------------------
# Post bundling
# ---------------------------------------------------------------------------


def _base_posts_queryset(school_class):
    qs = ClassPost.objects.select_related(
        "author",
        "removed_by",
        "lesson__module",
        "quiz__lesson__module",
    )
    if school_class is not None:
        qs = qs.filter(school_class=school_class)
    return qs


def _annotate_post_stats(qs):
    return qs.annotate(
        visible_reply_count=Count(
            "replies", filter=Q(replies__is_removed=False)
        ),
        best_answer_exists=Exists(
            ClassPostReply.objects.filter(
                post_id=OuterRef("pk"),
                is_best_answer=True,
                is_removed=False,
            )
        ),
    )


def _lesson_link(post, viewer) -> Optional[dict]:
    lesson = getattr(post, "lesson", None)
    if lesson is None:
        return None
    module = lesson.module
    if module.school_class_id != post.school_class_id:
        return None
    if not is_moderator(viewer) and not (module.is_published and lesson.is_published):
        return None
    try:
        url = reverse(
            "learning:lesson_view",
            kwargs={
                "class_id": module.school_class_id,
                "subject_id": module.subject_id,
                "lesson_id": lesson.pk,
            },
        )
    except NoReverseMatch:
        return None
    return {"url": url, "label": lesson.title}


def _quiz_link(post, viewer) -> Optional[dict]:
    quiz = getattr(post, "quiz", None)
    if quiz is None:
        return None
    lesson = quiz.lesson
    module = lesson.module
    if module.school_class_id != post.school_class_id:
        return None
    if not is_moderator(viewer) and not quiz.is_published:
        return None
    try:
        url = reverse(
            "learning:quiz_landing",
            kwargs={
                "class_id": module.school_class_id,
                "subject_id": module.subject_id,
                "lesson_id": lesson.pk,
            },
        )
    except NoReverseMatch:
        return None
    return {"url": url, "label": quiz.title}


def _bundle(posts: list, viewer) -> list[PostWithReactions]:
    """Attach reaction summaries and reply stats to a list of posts."""
    if not posts:
        return []

    post_ids = [p.pk for p in posts]
    reactions = list(
        ClassPostReaction.objects.filter(post_id__in=post_ids).values(
            "post_id", "user_id", "emoji"
        )
    )
    counts_map: dict[int, dict[str, int]] = defaultdict(dict)
    viewer_emoji_map: dict[int, Optional[str]] = {}
    for row in reactions:
        pid = row["post_id"]
        emoji = row["emoji"]
        counts_map[pid][emoji] = counts_map[pid].get(emoji, 0) + 1
        if row["user_id"] == viewer.pk:
            viewer_emoji_map[pid] = emoji

    result = []
    for post in posts:
        reply_count = int(getattr(post, "visible_reply_count", 0) or 0)
        has_best = bool(getattr(post, "best_answer_exists", False))
        result.append(
            PostWithReactions(
                post=post,
                reactions=ReactionSummary(
                    counts=counts_map.get(post.pk, {}),
                    viewer_emoji=viewer_emoji_map.get(post.pk),
                ),
                reply_count=reply_count,
                has_best_answer=has_best,
                answered=reply_count > 0,
                lesson_link=_lesson_link(post, viewer),
                quiz_link=_quiz_link(post, viewer),
            )
        )
    return result


# ---------------------------------------------------------------------------
# Core selectors
# ---------------------------------------------------------------------------


def feed_for_classroom(*, school_class, viewer) -> list[PostWithReactions]:
    """
    Ordered list of posts for *school_class* — the original notice board.

    Pinned posts always come first; within each group posts are newest-first.
    Teachers see removed posts (they appear in the list, flagged).
    Students and parents never see removed posts.
    """
    role = getattr(viewer, "role", "")

    qs = _base_posts_queryset(school_class).order_by("-is_pinned", "-created_at")
    if role not in MODERATOR_ROLES:
        qs = qs.filter(is_removed=False)

    return _bundle(list(qs), viewer)


def _apply_filter(qs, filter_key: str, viewer):
    if filter_key == "questions":
        return qs.filter(post_type=ClassPost.PostType.QUESTION)
    if filter_key == "announcements":
        return qs.filter(post_type=ClassPost.PostType.ANNOUNCEMENT)
    if filter_key == "pinned":
        return qs.filter(is_pinned=True)
    if filter_key == "mine":
        return qs.filter(author=viewer)
    if filter_key == "unanswered":
        return (
            qs.filter(
                post_type=ClassPost.PostType.QUESTION,
                is_removed=False,
            )
            .annotate(
                unanswered_replies=Count(
                    "replies", filter=Q(replies__is_removed=False)
                )
            )
            .filter(unanswered_replies=0)
        )
    return qs


def feed_page(
    *,
    school_class,
    viewer,
    filter_key: str = "all",
    before_id: Optional[int] = None,
    limit: int = PAGE_SIZE,
) -> FeedPage:
    """
    One page of the community feed.

    The stream is ordered by id (creation order) so the ``before_id`` cursor
    can never skip or duplicate a post. On the "All" tab pinned posts get
    their own small strip and are not repeated in the stream; type filters
    stay strictly chronological.
    """
    role = getattr(viewer, "role", "")
    valid_keys = {slug for slug, _ in FEED_FILTERS + MODERATOR_FILTERS}
    if filter_key not in valid_keys:
        filter_key = "all"
    if filter_key == "unanswered" and role not in MODERATOR_ROLES:
        filter_key = "all"

    base = _base_posts_queryset(school_class)
    if role not in MODERATOR_ROLES:
        base = base.filter(is_removed=False)

    pinned = []
    stream_base = _apply_filter(base, filter_key, viewer)
    if filter_key == "all":
        pinned_qs = _annotate_post_stats(
            base.filter(is_pinned=True).order_by("-id")
        )[:PINNED_LIMIT]
        pinned = _bundle(list(pinned_qs), viewer)
        stream_base = base.filter(is_pinned=False)

    qs = _annotate_post_stats(stream_base).order_by("-id")
    if before_id:
        qs = qs.filter(id__lt=before_id)

    rows = list(qs[: limit + 1])
    has_more = len(rows) > limit
    rows = rows[:limit]
    next_before_id = rows[-1].pk if rows and has_more else None

    return FeedPage(
        posts=_bundle(rows, viewer),
        pinned=pinned,
        has_more=has_more,
        next_before_id=next_before_id,
        filter_key=filter_key,
        latest_visible_id=latest_visible_post_id(
            school_class=school_class, viewer=viewer
        ),
    )


def post_detail(*, post_id: int, viewer) -> Optional[PostWithReactions]:
    """Single post with reactions.  Returns None if not found or not visible."""
    role = getattr(viewer, "role", "")
    try:
        post = _annotate_post_stats(
            _base_posts_queryset(None).filter(pk=post_id)
        ).get()
    except ClassPost.DoesNotExist:
        return None

    # Visibility: removed posts hidden from non-moderators
    if post.is_removed and role not in MODERATOR_ROLES:
        return None

    return _bundle([post], viewer)[0]


def post_thread(*, post_id: int, viewer) -> Optional[PostThread]:
    """One post, its visible replies, and the derived question state."""
    bundled = post_detail(post_id=post_id, viewer=viewer)
    if bundled is None:
        return None

    post = bundled.post
    replies_qs = post.replies.select_related("author").order_by("created_at", "id")
    if getattr(viewer, "role", "") not in MODERATOR_ROLES:
        replies_qs = replies_qs.filter(is_removed=False)
    replies = list(replies_qs)

    visible = [r for r in replies if not r.is_removed]
    best_reply_id = next((r.pk for r in visible if r.is_best_answer), None)
    question_state = ""
    if post.post_type == ClassPost.PostType.QUESTION:
        question_state = "answered" if visible else "waiting"

    return PostThread(
        post=bundled,
        replies=replies,
        question_state=question_state,
        best_reply_id=best_reply_id,
        can_reply=can_reply(viewer, post),
        can_moderate=can_moderate_reply(viewer, post),
        can_choose_best=can_mark_best(viewer, post),
    )


def new_posts_count(
    *,
    school_class,
    viewer,
    after_id: int,
    filter_key: str = "all",
) -> int:
    """Cheap count for the poll: visible posts newer than *after_id*."""
    if not after_id:
        return 0
    role = getattr(viewer, "role", "")
    if filter_key == "unanswered" and role not in MODERATOR_ROLES:
        filter_key = "all"
    qs = ClassPost.objects.filter(school_class=school_class, id__gt=after_id)
    if role not in MODERATOR_ROLES:
        qs = qs.filter(is_removed=False)
    return _apply_filter(qs, filter_key, viewer).count()


def latest_visible_post_id(*, school_class, viewer) -> int:
    """Highest visible post id for this viewer, or 0 when the feed is empty."""
    role = getattr(viewer, "role", "")
    qs = ClassPost.objects.filter(school_class=school_class)
    if role not in MODERATOR_ROLES:
        qs = qs.filter(is_removed=False)
    return qs.order_by("-id").values_list("id", flat=True).first() or 0


def reaction_summary(*, post: ClassPost) -> dict[str, int]:
    """Emoji → count dict for one post. No viewer context."""
    from collections import Counter
    emojis = ClassPostReaction.objects.filter(post=post).values_list("emoji", flat=True)
    return dict(Counter(emojis))
