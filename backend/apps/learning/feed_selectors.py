"""
apps/learning/feed_selectors.py
---------------------------------
Read-only queries for the class community feed (Piece 2).

Public API
----------
  feed_for_classroom(school_class, viewer)
      Returns the list of visible ClassPost objects for this class,
      annotated with reaction counts and whether *viewer* has reacted.
      Teachers see removed posts (greyed out); students and parents do not.

  post_detail(post_id, viewer)
      Single post with the same visibility rules.

  reaction_summary(post)
      Dict mapping emoji → count for one post.

  can_post(user, school_class)
      Boolean — whether this user may create a post in this class.

  is_moderator(user)
      Boolean — whether this user may pin/remove posts.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from django.db.models import Count, Q

from .models import ClassPost, ClassPostReaction

# Roles that can see the feed at all (teacher also sees removed posts).
VIEWER_ROLES = {"admin", "director", "teacher", "student", "parent"}
MODERATOR_ROLES = {"admin", "director", "teacher"}
POSTER_ROLES = {"admin", "director", "teacher", "student"}


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


@dataclass
class PostWithReactions:
    """A ClassPost bundled with its reaction summary."""
    post: ClassPost
    reactions: ReactionSummary

    @property
    def is_removed(self) -> bool:
        return self.post.is_removed


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def is_moderator(user) -> bool:
    return getattr(user, "role", "") in MODERATOR_ROLES


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


# ---------------------------------------------------------------------------
# Core selectors
# ---------------------------------------------------------------------------


def feed_for_classroom(*, school_class, viewer) -> list[PostWithReactions]:
    """
    Ordered list of posts for *school_class*.

    Pinned posts always come first; within each group posts are newest-first.
    Teachers see removed posts (they appear in the list, flagged).
    Students and parents never see removed posts.
    """
    role = getattr(viewer, "role", "")

    qs = (
        ClassPost.objects.filter(school_class=school_class)
        .select_related("author", "removed_by")
        .prefetch_related("reactions")
        .order_by("-is_pinned", "-created_at")
    )

    if role not in MODERATOR_ROLES:
        qs = qs.filter(is_removed=False)

    posts = list(qs)
    if not posts:
        return []

    post_ids = [p.pk for p in posts]

    # Bulk-fetch all reactions for these posts
    all_reactions = list(
        ClassPostReaction.objects.filter(post_id__in=post_ids)
        .values("post_id", "user_id", "emoji")
    )

    # Group by post_id
    from collections import defaultdict
    counts_map: dict[int, dict[str, int]] = defaultdict(dict)
    viewer_emoji_map: dict[int, Optional[str]] = {}

    for r in all_reactions:
        pid = r["post_id"]
        emoji = r["emoji"]
        counts_map[pid][emoji] = counts_map[pid].get(emoji, 0) + 1
        if r["user_id"] == viewer.pk:
            viewer_emoji_map[pid] = emoji

    result = []
    for post in posts:
        result.append(
            PostWithReactions(
                post=post,
                reactions=ReactionSummary(
                    counts=counts_map.get(post.pk, {}),
                    viewer_emoji=viewer_emoji_map.get(post.pk),
                ),
            )
        )
    return result


def post_detail(*, post_id: int, viewer) -> Optional[PostWithReactions]:
    """Single post with reactions.  Returns None if not found or not visible."""
    role = getattr(viewer, "role", "")
    try:
        post = (
            ClassPost.objects.select_related("author", "school_class", "removed_by")
            .prefetch_related("reactions")
            .get(pk=post_id)
        )
    except ClassPost.DoesNotExist:
        return None

    # Visibility: removed posts hidden from non-moderators
    if post.is_removed and role not in MODERATOR_ROLES:
        return None

    reactions = list(
        ClassPostReaction.objects.filter(post=post)
        .values("user_id", "emoji")
    )
    counts: dict[str, int] = {}
    viewer_emoji: Optional[str] = None
    for r in reactions:
        counts[r["emoji"]] = counts.get(r["emoji"], 0) + 1
        if r["user_id"] == viewer.pk:
            viewer_emoji = r["emoji"]

    return PostWithReactions(
        post=post,
        reactions=ReactionSummary(counts=counts, viewer_emoji=viewer_emoji),
    )


def reaction_summary(*, post: ClassPost) -> dict[str, int]:
    """Emoji → count dict for one post. No viewer context."""
    from collections import Counter
    emojis = ClassPostReaction.objects.filter(post=post).values_list("emoji", flat=True)
    return dict(Counter(emojis))
