"""
apps/community/selectors.py
----------------------------
All read-only queries for the community module.

Visibility rules are enforced here — nothing that should be hidden ever
reaches a template. Every public-facing selector applies at minimum:
  1. category.is_active
  2. category.visibility matches the user (or visitor)
  3. post.status == PUBLISHED (PENDING and REMOVED are visible only to their author)
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from django.db.models import Count, Max, OuterRef, Prefetch, Q, Subquery
from django.utils import timezone

from .models import (
    Category,
    Comment,
    CommunityProfile,
    DirectConversation,
    DirectMessage,
    Post,
    Reaction,
)


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
            post_map[pk].recent_commenters = commenters
    for p in posts:
        if not hasattr(p, "recent_commenters"):
            p.recent_commenters = []


# ─── Post detail ─────────────────────────────────────────────────────────────

def post_detail(pk: int, user) -> Post | None:
    """
    Return a published post the user can see, or one of the user's own posts
    (pending or removed) so the composer redirect and My Posts links never
    dead-end on the author's own 404.
    Annotates reaction counts and prefetches visible comments.
    """
    category_ids = _visible_category_ids(user)
    visible = Q(status=Post.Status.PUBLISHED, category_id__in=category_ids)
    if getattr(user, "is_authenticated", False):
        visible |= Q(author=user)
    try:
        post = (
            Post.objects.filter(visible, pk=pk)
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

    # Prefetch comments in one query, then thread replies under their parent.
    comments = list(
        Comment.objects.filter(post=post)
        .select_related("author")
        .order_by("created_at")
    )
    replies_by_parent: dict[int, list] = {}
    for comment in comments:
        if comment.parent_id:
            replies_by_parent.setdefault(comment.parent_id, []).append(comment)
    for comment in comments:
        comment.replies_list = replies_by_parent.get(comment.pk, [])
    post._all_comments = comments
    post._comments_list = [c for c in comments if c.parent_id is None]
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


# ─── Leaderboard ─────────────────────────────────────────────────────────────

def top_contributors(user, *, days: int = 30, limit: int = 5) -> list[dict]:
    """
    Most active members by posts + replies in the last `days` days.

    Only counts content in categories the requesting user can already see,
    so the leaderboard never reveals activity that is hidden from them.
    Display names follow the same student rule as the feed.
    """
    from apps.accounts.models import User

    since = timezone.now() - timedelta(days=days)
    category_ids = _visible_category_ids(user)

    totals: dict[int, int] = {}
    post_rows = (
        Post.objects.filter(
            status=Post.Status.PUBLISHED,
            category_id__in=category_ids,
            created_at__gte=since,
            author__isnull=False,
        )
        .values("author_id")
        .annotate(total=Count("id"))
    )
    comment_rows = (
        Comment.objects.filter(
            post__status=Post.Status.PUBLISHED,
            post__category_id__in=category_ids,
            is_removed=False,
            created_at__gte=since,
            author__isnull=False,
        )
        .values("author_id")
        .annotate(total=Count("id"))
    )
    for row in post_rows:
        totals[row["author_id"]] = totals.get(row["author_id"], 0) + row["total"]
    for row in comment_rows:
        totals[row["author_id"]] = totals.get(row["author_id"], 0) + row["total"]

    if not totals:
        return []

    ranked = sorted(totals.items(), key=lambda item: (-item[1], item[0]))[:limit]
    users = User.objects.filter(pk__in=[pk for pk, _ in ranked]).in_bulk()

    board = []
    for rank, (user_id, total) in enumerate(ranked, start=1):
        member = users.get(user_id)
        if member is None:
            continue
        board.append(
            {
                "rank": rank,
                "user": member,
                "name": _member_display_name(member),
                "role": member.get_role_display(),
                "total": total,
            }
        )
    return board


def _member_display_name(user) -> str:
    if user is None:
        return "Deleted member"
    if getattr(user, "role", "") == "student":
        first = user.first_name or user.username
        last_initial = (user.last_name or "")[:1]
        return f"{first} {last_initial}." if last_initial else first
    return user.get_full_name() or user.username


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
    _attach_recent_commenters(posts)
    return posts


# ─── Postable categories (for composer dropdown) ──────────────────────────────

def postable_categories(user) -> list[Category]:
    """
    Categories the user may post in (can see + not staff_post_only unless staff).
    """
    role = getattr(user, "role", "")
    is_staff = role in ("teacher", "director", "admin")
    result = []
    for cat in Category.objects.filter(is_active=True).order_by("display_order", "id"):
        if not cat.is_visible_to(user):
            continue
        if cat.staff_post_only and not is_staff:
            continue
        result.append(cat)
    return result


# ─── Open reports / pending appeals ─────────────────────────────────────────

def open_reports():
    from .models import Report
    return list(
        Report.objects.filter(status=Report.Status.OPEN)
        .select_related("reporter", "post", "comment")
        .annotate(report_count=Count("id"))
        .order_by("-created_at")
    )


def pending_appeals():
    from .models import Appeal
    return list(
        Appeal.objects.filter(status=Appeal.Status.PENDING)
        .select_related("appellant", "warning")
        .order_by("created_at")
    )


# ─── User's own posts ─────────────────────────────────────────────────────────

def user_posts(user) -> list[Post]:
    return list(
        Post.objects.filter(author=user)
        .select_related("category")
        .order_by("-created_at")[:30]
    )


# ─── Dashboard card selectors ─────────────────────────────────────────────────

def student_community_insights(user) -> dict:
    from .models import Comment, Reaction, Warning
    my_posts = Post.objects.filter(author=user, status=Post.Status.PUBLISHED)
    reply_count = Comment.objects.filter(post__in=my_posts, is_removed=False).count()
    reaction_count = Reaction.objects.filter(post__in=my_posts).count()
    latest = (
        Comment.objects.filter(post__in=my_posts, is_removed=False)
        .select_related("post")
        .order_by("-created_at")[:3]
    )
    warnings = Warning.objects.filter(
        recipient=user, status=Warning.Status.ACTIVE
    ).count()
    return {
        "my_post_count": my_posts.count(),
        "my_reply_count": reply_count,
        "my_reaction_count": reaction_count,
        "latest_replies": list(latest),
        "active_warning_count": warnings,
    }


def parent_community_insights(user) -> dict:
    from datetime import timedelta
    from .models import Category
    # Latest public announcements
    announcements_cat = Category.objects.filter(
        slug="announcements", is_active=True
    ).first()
    latest_announcements = []
    if announcements_cat:
        latest_announcements = list(
            Post.objects.filter(
                category=announcements_cat, status=Post.Status.PUBLISHED
            ).order_by("-created_at")[:5]
        )
    # Events in the next 30 days (from public selectors)
    from apps.public.selectors import active_event_items
    upcoming_events = active_event_items()[:3]
    # My own posts
    my_posts = Post.objects.filter(author=user, status=Post.Status.PUBLISHED)
    return {
        "latest_announcements": latest_announcements,
        "upcoming_events": upcoming_events,
        "my_post_count": my_posts.count(),
    }


def teacher_community_insights(user) -> dict:
    from datetime import timedelta
    from .models import Category
    from django.utils import timezone

    my_posts = Post.objects.filter(author=user, status=Post.Status.PUBLISHED)
    total_reactions = sum(
        Post.objects.filter(pk__in=my_posts.values("pk"))
        .annotate(rc=Count("reactions"))
        .values_list("rc", flat=True)
    )
    # Unanswered questions: posts with 0 comments older than 2 days
    two_days_ago = timezone.now() - timedelta(days=2)
    visible_cat_ids = _visible_category_ids(user)
    unanswered = list(
        Post.objects.filter(
            status=Post.Status.PUBLISHED,
            category_id__in=visible_cat_ids,
            created_at__lte=two_days_ago,
        )
        .annotate(reply_count=Count("comments", filter=Q(comments__is_removed=False)))
        .filter(reply_count=0)
        .order_by("created_at")[:5]
    )
    return {
        "my_post_count": my_posts.count(),
        "my_total_reactions": total_reactions,
        "unanswered_questions": unanswered,
    }


def admin_community_summary() -> dict:
    from .models import Report, Warning, Post, Comment
    from datetime import timedelta
    from django.utils import timezone

    pending = Post.objects.filter(status=Post.Status.PENDING).count()
    open_rep = Report.objects.filter(status=Report.Status.OPEN).count() if hasattr(Report, 'Status') else 0
    active_warnings = Warning.objects.filter(status=Warning.Status.ACTIVE).count()

    week_ago = timezone.now() - timedelta(days=7)
    new_posts_week = Post.objects.filter(
        status=Post.Status.PUBLISHED, created_at__gte=week_ago
    ).count()
    new_comments_week = Comment.objects.filter(
        is_removed=False, created_at__gte=week_ago
    ).count()
    return {
        "pending_count": pending,
        "open_report_count": open_rep,
        "active_warning_count": active_warnings,
        "new_posts_week": new_posts_week,
        "new_comments_week": new_comments_week,
    }


def community_insights() -> dict:
    """Full insights for the admin insights page."""
    from .models import CommunityProfile, Post, Comment, Warning, Report
    from apps.accounts.models import User
    from datetime import timedelta
    from django.utils import timezone
    from django.db.models import Count

    now = timezone.now()
    week_ago = now - timedelta(days=7)
    month_ago = now - timedelta(days=30)

    # Activity over time (last 7 days, by day)
    daily_posts = []
    for i in range(6, -1, -1):
        day_start = (now - timedelta(days=i)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        day_end = day_start + timedelta(days=1)
        daily_posts.append({
            "label": day_start.strftime("%d %b").lstrip("0") or "1 Jan",
            "posts": Post.objects.filter(
                created_at__gte=day_start, created_at__lt=day_end,
                status=Post.Status.PUBLISHED
            ).count(),
            "comments": Comment.objects.filter(
                created_at__gte=day_start, created_at__lt=day_end,
                is_removed=False
            ).count(),
        })

    # Top categories by post count
    top_categories = list(
        Post.objects.filter(status=Post.Status.PUBLISHED)
        .values("category__name")
        .annotate(post_count=Count("id"))
        .order_by("-post_count")[:5]
    )

    # Unanswered questions > 2 days old
    two_days_ago = now - timedelta(days=2)
    unanswered = list(
        Post.objects.filter(
            status=Post.Status.PUBLISHED,
            created_at__lte=two_days_ago,
        )
        .annotate(reply_count=Count("comments", filter=Q(comments__is_removed=False)))
        .filter(reply_count=0)
        .select_related("category", "author")
        .order_by("created_at")[:10]
    )

    # Most active members (by post + comment count in last 30 days)
    from django.db.models import Q as DQ
    most_active_post = list(
        Post.objects.filter(created_at__gte=month_ago, status=Post.Status.PUBLISHED)
        .values("author__first_name", "author__last_name", "author__role")
        .annotate(post_count=Count("id"))
        .order_by("-post_count")[:5]
    )

    # Growth by role (new community profiles in last 30 days)
    growth_by_role = list(
        CommunityProfile.objects.filter(created_at__gte=month_ago)
        .values("user__role")
        .annotate(new_members=Count("id"))
        .order_by("-new_members")
    )

    # Pending / warnings / removals summary
    pending_posts_count = Post.objects.filter(status=Post.Status.PENDING).count()
    removed_posts_month = Post.objects.filter(
        status=Post.Status.REMOVED, removed_at__gte=month_ago
    ).count()
    warnings_month = Warning.objects.filter(created_at__gte=month_ago).count()
    open_reports_count = Report.objects.filter(status=Report.Status.OPEN).count()

    # Health: community storage (approximate)
    total_posts = Post.objects.count()
    total_comments = Comment.objects.count()

    return {
        "daily_activity": daily_posts,
        "top_categories": top_categories,
        "unanswered_questions": unanswered,
        "most_active_members": most_active_post,
        "growth_by_role": growth_by_role,
        "pending_posts_count": pending_posts_count,
        "removed_posts_month": removed_posts_month,
        "warnings_month": warnings_month,
        "open_reports_count": open_reports_count,
        "total_posts": total_posts,
        "total_comments": total_comments,
        "total_members": User.objects.filter(is_active=True).count(),
    }


# ─── Member map ──────────────────────────────────────────────────────────────

def map_members(viewer) -> list[dict]:
    """
    Members who opted into the map, with their shared town.

    Students are filtered out for the same safeguarding reason they are
    absent from the members directory: only staff, guardians and other
    non-student accounts are discoverable.
    """
    if viewer is None or not viewer.is_authenticated:
        return []

    profiles = (
        CommunityProfile.objects.filter(
            location_shared=True,
            latitude__isnull=False,
            longitude__isnull=False,
            user__is_active=True,
        )
        .exclude(user__role="student")
        .select_related("user")
        .order_by("location_country", "location_label", "user__first_name")
    )

    rows = []
    for profile in profiles:
        user = profile.user
        rows.append(
            {
                "user": user,
                "profile": profile,
                "name": _member_display_name(user),
                "role": user.role,
                "role_label": user.get_role_display(),
                "label": profile.location_label,
                "country": profile.location_country,
                "latitude": float(profile.latitude),
                "longitude": float(profile.longitude),
                "online": profile.is_online,
            }
        )
    return rows


def map_places(rows: list[dict]) -> list[dict]:
    """Aggregate map rows by place for the 'top places' list."""
    places: dict[str, dict] = {}
    for row in rows:
        key = row["label"]
        entry = places.setdefault(
            key, {"label": key, "country": row["country"], "count": 0}
        )
        entry["count"] += 1
    return sorted(places.values(), key=lambda entry: (-entry["count"], entry["label"]))


# ─── Member profile ──────────────────────────────────────────────────────────

def member_profile_context(target, viewer) -> dict:
    """
    Public-safe profile data for one member. Respects category visibility,
    so a profile never reveals posts the viewer could not read in the feed.
    """
    category_ids = _visible_category_ids(viewer)
    posts = (
        Post.objects.filter(
            author=target,
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
            latest_comment_at=Max(
                "comments__created_at",
                filter=Q(comments__is_removed=False),
            ),
        )
        .order_by("-created_at")
    )

    posts_count = posts.count()
    recent_posts = list(posts[:4])
    _attach_recent_commenters(recent_posts)

    replies_count = Comment.objects.filter(
        author=target,
        is_removed=False,
        post__status=Post.Status.PUBLISHED,
        post__category_id__in=category_ids,
    ).count()
    reactions_received = Reaction.objects.filter(
        post__author=target,
        post__status=Post.Status.PUBLISHED,
        post__category_id__in=category_ids,
    ).count()

    profile = CommunityProfile.objects.filter(user=target).first()

    return {
        "member": target,
        "member_cprofile": profile,
        "posts_count": posts_count,
        "replies_count": replies_count,
        "reactions_received": reactions_received,
        "recent_posts": recent_posts,
    }


# ─── Direct messages ─────────────────────────────────────────────────────────

def direct_inbox(user) -> list[dict]:
    """Conversation rows for the inbox, newest activity first."""
    from apps.community.services import can_use_direct_messages

    if not can_use_direct_messages(user):
        return []

    last_message = DirectMessage.objects.filter(
        conversation=OuterRef("pk")
    ).order_by("-created_at")

    conversations = (
        DirectConversation.objects.filter(Q(user_a=user) | Q(user_b=user))
        .select_related("user_a", "user_b")
        .annotate(
            unread=Count(
                "messages",
                filter=Q(messages__read_at__isnull=True)
                & ~Q(messages__sender_id=user.pk),
                distinct=True,
            ),
            last_body=Subquery(last_message.values("body")[:1]),
            last_sender_id=Subquery(last_message.values("sender_id")[:1]),
            last_created=Subquery(last_message.values("created_at")[:1]),
        )
        .order_by("-last_message_at", "-created_at")
    )

    rows = []
    for conversation in conversations:
        other = conversation.other_user(user)
        rows.append(
            {
                "conversation": conversation,
                "other": other,
                "name": _member_display_name(other),
                "role_label": other.get_role_display(),
                "unread": conversation.unread,
                "last_body": conversation.last_body or "",
                "last_created": conversation.last_created,
                "last_is_mine": conversation.last_sender_id == user.pk,
            }
        )
    return rows


def direct_thread_messages(conversation: DirectConversation) -> list[DirectMessage]:
    return list(
        conversation.messages.select_related("sender").order_by("created_at")
    )
