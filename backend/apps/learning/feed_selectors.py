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

import re
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Optional

from django.db.models import Count, Exists, OuterRef, Q
from django.urls import NoReverseMatch, reverse
from django.utils import timezone

from .models import (
    ClassFeedSettings,
    ClassPoll,
    ClassPost,
    ClassPostAttachment,
    ClassPostReaction,
    ClassPostReply,
)

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
    can_edit: bool = False
    lesson_link: Optional[dict] = None
    quiz_link: Optional[dict] = None
    link_card: Optional[dict] = None
    poll: object = None
    photos: list = field(default_factory=list)
    files: list = field(default_factory=list)
    pending_attachments: list = field(default_factory=list)
    removed_attachments: list = field(default_factory=list)
    media_controls: list = field(default_factory=list)

    @property
    def has_media(self) -> bool:
        return bool(self.photos or self.files)

    @property
    def body_segments(self) -> list[dict]:
        """Plain text and resolved @mention segments; never raw HTML."""
        return self.post.body_segments

    @property
    def is_scheduled(self) -> bool:
        return self.post.is_scheduled

    @property
    def is_removed(self) -> bool:
        return self.post.is_removed

    @property
    def is_question(self) -> bool:
        return self.post.post_type == ClassPost.PostType.QUESTION

    @property
    def is_poll(self) -> bool:
        return self.post.post_type == ClassPost.PostType.POLL

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


def _class_feed_row(school_class):
    """
    This class's two switches in one query.  Read directly rather than through
    the reverse one-to-one descriptor: the descriptor caches its row on the
    class instance, so a switch flipped moments ago would still read stale.
    """
    return (
        ClassFeedSettings.objects.filter(school_class=school_class)
        .values_list(
            "is_active", "polls_enabled", "media_enabled", "student_uploads_enabled"
        )
        .first()
    )


def school_media_switch_on(request=None) -> bool:
    """The school-wide switch for photos and files on class feeds."""
    return bool(_feature_row(request)["feed_media_enabled"])


def school_student_uploads_switch_on(request=None) -> bool:
    """The school-wide switch that lets pupils attach media."""
    return bool(_feature_row(request)["feed_student_uploads_enabled"])


def community_enabled_for(school_class, *, request=None) -> bool:
    """
    The class community feed needs BOTH switches on:
    the school-wide ``feed_community_enabled`` and this class's
    ``ClassFeedSettings.is_active``. Either one off means off.
    """
    if not _feature_row(request)["feed_community_enabled"]:
        return False
    row = _class_feed_row(school_class)
    return bool(row and row[0])


def mention_candidates(school_class) -> list[dict]:
    """
    Everyone who can read this class's feed: its pupils, the teachers
    assigned to it, and its class teacher.  Admins/directors are deliberately
    left out — a class feed should not become a way to ping the whole school.
    """
    from apps.teachers.models import TeacherAssignment

    rows: list[dict] = []
    seen: set[int] = set()

    def add(user):
        if user is None or user.pk in seen:
            return
        seen.add(user.pk)
        rows.append(
            {
                "id": user.pk,
                "name": user.get_full_name() or user.get_username(),
                "first": (user.first_name or user.get_username()).strip(),
            }
        )

    for student in (
        school_class.students.select_related("user")
        .filter(user__is_active=True)
        .order_by("user__first_name", "user__last_name")
    ):
        add(student.user)

    for assignment in (
        TeacherAssignment.objects.filter(school_class=school_class)
        .select_related("teacher__user")
        .order_by("teacher__user__first_name")
    ):
        add(assignment.teacher.user)

    add(school_class.class_teacher)
    return rows


def mention_picker_for(school_class) -> list[str]:
    """Display names for the composer's "Mention" picker, pupils first."""
    return [row["name"] for row in mention_candidates(school_class)]


def _scheduled_visible(qs, viewer):
    """Pupils and parents do not see a post before its scheduled time."""
    if getattr(viewer, "role", "") in MODERATOR_ROLES:
        return qs
    now = timezone.now()
    return qs.filter(Q(scheduled_at__isnull=True) | Q(scheduled_at__lte=now))


def _apply_search(qs, search: str):
    term = (search or "").strip()[:80]
    if not term:
        return qs
    return qs.filter(
        Q(body__icontains=term)
        | Q(author__first_name__icontains=term)
        | Q(author__last_name__icontains=term)
    )


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


def polls_visible_to(viewer, school_class) -> bool:
    """
    Poll posts disappear for students and parents when the class polls
    switch is off. Teachers keep seeing them, like any other removed or
    switched-off content, so the history is not lost.
    """
    if getattr(viewer, "role", "") in MODERATOR_ROLES:
        return True
    if not _feature_row(None)["feed_community_enabled"]:
        return False
    row = _class_feed_row(school_class)
    return bool(row and row[0] and row[1])


def can_reply(user, post, school_class=None) -> bool:
    if post.is_removed or post.is_scheduled:
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


_URL_PATTERN = re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE)


def _link_card(post, viewer) -> Optional[dict]:
    """
    The first web address in the body, shown as a plain card below the post.

    Only teachers and above get a clickable card for now: a pupil's link is
    reviewed by a teacher before it becomes openable (C5).  The card shows
    the address and host; nothing is fetched, so there is no preview, no
    tracking pixel and no server-side request to the destination.
    """
    if getattr(viewer, "role", "") not in MODERATOR_ROLES:
        return None
    match = _URL_PATTERN.search(post.body or "")
    if match is None:
        return None
    url = match.group(0).rstrip(".,;:!?)]}\"'")
    if len(url) < 12:
        return None
    from urllib.parse import urlsplit

    host = urlsplit(url).netloc.lower()
    if not host:
        return None
    if host.startswith("www."):
        host = host[4:]
    return {"url": url, "domain": host}


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


def _attachment_entry(post, attachment) -> dict:
    """One attachment rendered as a dict; every URL goes through the safe view."""
    base = reverse(
        "learning:feed_attachment_file",
        args=[post.school_class_id, post.pk, attachment.pk],
    )
    name = attachment.original_name or attachment.file.name.rsplit("/", 1)[-1]
    return {
        "id": attachment.pk,
        "name": name,
        "alt": attachment.alt_text or "",
        "size": attachment.size_label,
        "ext": attachment.extension.lstrip(".").upper()[:5],
        "icon": attachment.icon,
        "is_image": attachment.is_image,
        "status": attachment.approval_status,
        "pending": attachment.is_pending,
        "removed": attachment.is_removed,
        "thumb_url": base + "?thumb=1" if attachment.is_image else "",
        "url": base,
        "download_url": base + "?download=1",
        "uploaded_by": (
            attachment.uploaded_by.get_full_name()
            if attachment.uploaded_by
            else "Unknown"
        ),
    }


def _bundle_attachments(posts: list, viewer) -> dict[int, dict]:
    """Group this page's visible attachments by post (one query)."""
    from .feed_media_services import attachment_visible_to, media_enabled_for

    if not posts:
        return {}
    if not media_enabled_for(posts[0].school_class):
        return {}

    post_by_id = {post.pk: post for post in posts}
    grouped: dict[int, dict] = {}
    moderator = is_moderator(viewer)
    rows = (
        ClassPostAttachment.objects.filter(post_id__in=post_by_id)
        .select_related("uploaded_by")
        .order_by("ordering", "pk")
    )
    for attachment in rows:
        if not attachment_visible_to(attachment, viewer):
            continue
        post = post_by_id[attachment.post_id]
        entry = _attachment_entry(post, attachment)
        bucket = grouped.setdefault(
            attachment.post_id,
            {
                "photos": [],
                "files": [],
                "pending": [],
                "removed": [],
                "controls": [],
            },
        )
        if attachment.is_removed:
            bucket["removed"].append(entry)
        elif attachment.is_pending:
            bucket["pending"].append(entry)
        elif attachment.is_image:
            bucket["photos"].append(entry)
        else:
            bucket["files"].append(entry)
        if moderator:
            bucket["controls"].append(entry)
    return grouped


def _bundle(posts: list, viewer) -> list[PostWithReactions]:
    """Attach reaction summaries, reply stats and poll data to posts."""
    if not posts:
        return []

    from .poll_selectors import build_poll_summary

    attachments_by_post = _bundle_attachments(posts, viewer)
    post_ids = [p.pk for p in posts]
    polls = {
        poll.post_id: poll
        for poll in ClassPoll.objects.filter(post_id__in=post_ids)
    }
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
        media = attachments_by_post.get(
            post.pk,
            {"photos": [], "files": [], "pending": [], "removed": [], "controls": []},
        )
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
                can_edit=(
                    post.author_id is not None
                    and post.author_id == getattr(viewer, "pk", None)
                    and not post.is_removed
                ),
                lesson_link=_lesson_link(post, viewer),
                quiz_link=_quiz_link(post, viewer),
                link_card=_link_card(post, viewer),
                poll=(
                    build_poll_summary(polls[post.pk], viewer)
                    if post.pk in polls
                    else None
                ),
                photos=media["photos"],
                files=media["files"],
                pending_attachments=media["pending"],
                removed_attachments=media["removed"],
                media_controls=media["controls"],
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
    qs = _scheduled_visible(qs, viewer)
    if role not in MODERATOR_ROLES:
        qs = qs.filter(is_removed=False)
        if not polls_visible_to(viewer, school_class):
            qs = qs.exclude(post_type=ClassPost.PostType.POLL)

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
    search: str = "",
) -> FeedPage:
    """
    One page of the community feed.

    The stream is ordered by id (creation order) so the ``before_id`` cursor
    can never skip or duplicate a post. On the "All" tab pinned posts get
    their own small strip and are not repeated in the stream; type filters
    stay strictly chronological.  ``search`` is a simple case-insensitive
    match on the body and the author's names (80 characters at most).
    """
    role = getattr(viewer, "role", "")
    valid_keys = {slug for slug, _ in FEED_FILTERS + MODERATOR_FILTERS}
    if filter_key not in valid_keys:
        filter_key = "all"
    if filter_key == "unanswered" and role not in MODERATOR_ROLES:
        filter_key = "all"

    base = _base_posts_queryset(school_class)
    base = _scheduled_visible(base, viewer)
    base = _apply_search(base, search)
    if role not in MODERATOR_ROLES:
        base = base.filter(is_removed=False)
        if not polls_visible_to(viewer, school_class):
            base = base.exclude(post_type=ClassPost.PostType.POLL)

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
    if post.is_scheduled and role not in MODERATOR_ROLES:
        return None
    if (
        post.post_type == ClassPost.PostType.POLL
        and role not in MODERATOR_ROLES
        and not polls_visible_to(viewer, post.school_class)
    ):
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
    qs = _scheduled_visible(qs, viewer)
    if role not in MODERATOR_ROLES:
        qs = qs.filter(is_removed=False)
        if not polls_visible_to(viewer, school_class):
            qs = qs.exclude(post_type=ClassPost.PostType.POLL)
    return _apply_filter(qs, filter_key, viewer).count()


def latest_visible_post_id(*, school_class, viewer) -> int:
    """Highest visible post id for this viewer, or 0 when the feed is empty."""
    role = getattr(viewer, "role", "")
    qs = ClassPost.objects.filter(school_class=school_class)
    qs = _scheduled_visible(qs, viewer)
    if role not in MODERATOR_ROLES:
        qs = qs.filter(is_removed=False)
        if not polls_visible_to(viewer, school_class):
            qs = qs.exclude(post_type=ClassPost.PostType.POLL)
    return qs.order_by("-id").values_list("id", flat=True).first() or 0


def reaction_summary(*, post: ClassPost) -> dict[str, int]:
    """Emoji → count dict for one post. No viewer context."""
    from collections import Counter
    emojis = ClassPostReaction.objects.filter(post=post).values_list("emoji", flat=True)
    return dict(Counter(emojis))
