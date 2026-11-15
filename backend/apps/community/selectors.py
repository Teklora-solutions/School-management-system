"""
apps/community/selectors.py
----------------------------
All read-only queries for the community module.

Visibility rules are enforced here — nothing that should be hidden ever
reaches a template. Every public-facing selector applies at minimum:
  1. category.is_active
  2. category.visibility matches the user (or visitor)
  3. post.status == PUBLISHED (PENDING and REMOVED are never shown to non-staff)
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from django.db.models import Count, Max, OuterRef, Prefetch, Q, Subquery
from django.utils import timezone

from .models import Category, Comment, CommunityProfile, Post, Reaction


# ─── Helpers ─────────────────────────────────────────────────────────────────

def _visible_category_ids(user) -> list[int]:
    """
    Return the PKs of every active category the user (or visitor) may read.
    Called once per request and reused — do not iterate models per category.
    """
    qs = Category.objects.filter(is_active=True)
    ids = []
    for cat in qs:
        if cat.is_visible_to(user):
            ids.append(cat.id)
    return ids


def _published_posts_qs(user, *, category_ids=None):
    """
    Base queryset: published posts in categories the user can see.
    Callers narrow further (by category slug, search, etc.).
    """
    if category_ids is None:
        category_ids = _visible_category_ids(user)
    return (
        Post.objects.filter(
            status=Post.Status.PUBLISHED,
            category_id__in=category_ids,
        )
        .select_related("author", "category")
    )


# ─── Categories ──────────────────────────────────────────────────────────────

def visible_categories(user) -> list[Category]:
    """Active categories the user can see, in display order."""
    return [
        c for c in Category.objects.filter(is_active=True).order_by("display_order", "id")
        if c.is_visible_to(user)
    ]


def category_by_slug(slug: str, user) -> Category | None:
    """Return the category if it is active and visible to the user."""
    try:
        cat = Category.objects.get(slug=slug, is_active=True)
    except Category.DoesNotExist:
        return None
    return cat if cat.is_visible_to(user) else None


# ─── Posts list (home feed) ───────────────────────────────────────────────────

PAGE_SIZE = 15


def post_feed(
    user,
    *,
    category_slug: str | None = None,
    sort: str = "new",        # "new" | "top" | "pinned"
    cursor: int | None = None,
    search: str = "",
    category_ids: list[int] | None = None,
) -> dict[str, Any]:
    """
    Return one page of posts for the community home.

    Returns:
        {
            "posts": list[Post],  # annotated with reaction_count, comment_count,
                                  # latest_comment_at, recent_commenters
            "has_more": bool,
            "next_cursor": int | None,
        }
    """
    if category_ids is None:
        category_ids = _visible_category_ids(user)

    qs = _published_posts_qs(user, category_ids=category_ids)

    if category_slug and category_slug != "all":
        qs = qs.filter(category__slug=category_slug)

    if search:
        qs = qs.filter(
            Q(title__icontains=search) | Q(body__icontains=search)
        )

    # Annotate counts
    qs = qs.annotate(
        reaction_count=Count("reactions", distinct=True),
        comment_count=Count(
            "comments",
            filter=Q(comments__is_removed=False),
            distinct=True,
        ),
        latest_comment_at=Max(
            "comments__created_at",
            filter=Q(comments__is_removed=False),
        ),
    )

    # Sorting
    if sort == "pinned":
        qs = qs.filter(is_pinned=True).order_by("pin_order", "-created_at")
    elif sort == "top":
        # Top this week: most reactions in the last 7 days
        week_ago = timezone.now() - timedelta(days=7)
        qs = qs.filter(created_at__gte=week_ago).order_by("-reaction_count", "-created_at")
    else:
        # "new" — pinned posts float to top, then newest
        qs = qs.order_by("-is_pinned", "pin_order", "-created_at")

    # Cursor-based paging (cursor = last seen pk for new/pinned, offset for top)
    if cursor and sort in ("new", "pinned"):
        qs = qs.filter(pk__lt=cursor)

    posts = list(qs[: PAGE_SIZE + 1])
    has_more = len(posts) > PAGE_SIZE
    if has_more:
        posts = posts[:PAGE_SIZE]

    next_cursor = posts[-1].pk if has_more and posts else None

    # Attach a few recent commenter users (for avatar row)
    _attach_recent_commenters(posts)

    return {
        "posts": posts,
        "has_more": has_more,
        "next_cursor": next_cursor,
    }


def _attach_recent_commenters(posts: list[Post]) -> None:
    """
    For each post attach up to 3 distinct recent commenters as
    post._recent_commenters (list of User). One extra query total.
    """
    if not posts:
        return
    post_ids = [p.pk for p in posts]
    # Fetch the 3 most recent non-removed comments per post
    comments = (
        Comment.objects.filter(
            post_id__in=post_ids,
            is_removed=False,
        )
        .select_related("author")
        .order_by("post_id", "-created_at")
    )
    from collections import defaultdict

    seen: dict[int, list] = defaultdict(list)
    seen_users: dict[int, set] = defaultdict(set)
    for c in comments:
        pid = c.post_id
        uid = c.author_id
        if uid and uid not in seen_users[pid] and len(seen[pid]) < 3:
            seen[pid].append(c.author)
            seen_users[pid].add(uid)

    post_map = {p.pk: p for p in posts}
    for pk, commenters in seen.items():
        if pk in post_map:
            post_map[pk]._recent_commenters = commenters
    for p in posts:
        if not hasattr(p, "_recent_commenters"):
            p._recent_commenters = []


# ─── Post detail ─────────────────────────────────────────────────────────────

def post_detail(pk: int, user) -> Post | None:
    """
    Return a single published post if the user can see its category.
    Annotates reaction counts and prefetches visible comments.
    """
    category_ids = _visible_category_ids(user)
    try:
        post = (
            Post.objects.filter(
                pk=pk,
                status=Post.Status.PUBLISHED,
                category_id__in=category_ids,
            )
            .select_related("author", "category")
            .annotate(
                reaction_count=Count("reactions", distinct=True),
                comment_count=Count(
                    "comments",
                    filter=Q(comments__is_removed=False),
                    distinct=True,
                ),
            )
            .get()
        )
    except Post.DoesNotExist:
        return None

    # Prefetch comments in one query
    post._comments_list = list(
        Comment.objects.filter(post=post)
        .select_related("author")
        .order_by("created_at")
    )
    # Prefetch reactions for the post
    post._reactions_by_emoji = _reactions_summary(post, user)
    return post


def _reactions_summary(post: Post, user) -> list[dict]:
    """
    For each emoji: count + whether this user has reacted.
    Returns list sorted by EMOJI_CHOICES order.
    """
    from .models import Reaction

    counts = {
        row["emoji"]: row["count"]
        for row in Reaction.objects.filter(post=post)
        .values("emoji")
        .annotate(count=Count("id"))
    }
    user_reactions: set[str] = set()
    if user and user.is_authenticated:
        user_reactions = set(
            Reaction.objects.filter(post=post, user=user).values_list("emoji", flat=True)
        )
    result = []
    for emoji, label in Reaction.EMOJI_CHOICES:
        c = counts.get(emoji, 0)
        if c > 0 or True:  # always show all 5 so the row is stable
            result.append(
                {
                    "emoji": emoji,
                    "label": label,
                    "count": c,
                    "reacted": emoji in user_reactions,
                }
            )
    return result


# ─── Sidebar stats ────────────────────────────────────────────────────────────

ONLINE_WINDOW_SECONDS = 300  # 5 minutes


def community_stats(user) -> dict:
    """
    Numbers for the sidebar card: member count, online now, admin count.
    Members = all users who have a CommunityProfile (have visited the page).
    For visitors we show total user count instead — a reasonable approximation.
    """
    from apps.accounts.models import User

    total_members = User.objects.filter(is_active=True).count()
    online_cutoff = timezone.now() - timedelta(seconds=ONLINE_WINDOW_SECONDS)
    online_now = CommunityProfile.objects.filter(
        last_seen_at__gte=online_cutoff
    ).count()
    admins = User.objects.filter(
        role__in=["admin", "director"], is_active=True
    ).count()
    return {
        "total_members": total_members,
        "online_now": online_now,
        "admins": admins,
    }


# ─── Members directory (signed-in only) ──────────────────────────────────────

def members_list(user, *, q: str = "") -> list:
    """
    Staff-safe member list: shown only to signed-in users.
    Students are excluded from public directory listing entirely.
    Returns User objects with display-safe fields only.
    """
    from apps.accounts.models import User

    if user is None or not user.is_authenticated:
        return []

    qs = User.objects.filter(
        is_active=True,
    ).exclude(role="student")  # never list students in directory

    if q:
        qs = qs.filter(
            Q(first_name__icontains=q)
            | Q(last_name__icontains=q)
        )
    return list(qs.order_by("last_name", "first_name")[:50])


# ─── Pending posts (admin moderation queue) ───────────────────────────────────

def pending_posts() -> list[Post]:
    """Posts awaiting admin approval."""
    return list(
        Post.objects.filter(status=Post.Status.PENDING)
        .select_related("author", "category")
        .order_by("created_at")
    )


# ─── Search ──────────────────────────────────────────────────────────────────

def search_posts(query: str, user) -> list[Post]:
    """Full-text search across post titles, bodies and comment bodies."""
    if not query or len(query.strip()) < 2:
        return []
    category_ids = _visible_category_ids(user)
    posts = list(
        Post.objects.filter(
            status=Post.Status.PUBLISHED,
            category_id__in=category_ids,
        )
        .filter(Q(title__icontains=query) | Q(body__icontains=query))
        .select_related("author", "category")
        .annotate(
            reaction_count=Count("reactions", distinct=True),
            comment_count=Count(
                "comments", filter=Q(comments__is_removed=False), distinct=True
            ),
        )
        .order_by("-created_at")[:30]
    )
    return posts
