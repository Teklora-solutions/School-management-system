"""
apps/community/models.py
------------------------
School-wide public community: categories, posts, reactions and comments.

Design decisions
~~~~~~~~~~~~~~~~
- One level of comments only (replies to posts, not to comments) so the
  page stays readable on a phone.
- Visibility is on the category, not the post: the admin controls who can
  see a category once; authors do not choose per-post.
- Student posts in PUBLIC categories are held for admin approval before
  they appear publicly (controlled by SchoolSettings.community_student_moderation).
  Posts in SIGNED_IN / ROLE categories from students are visible immediately
  to the allowed audience.
- Student display name: first name + last initial only. No photo until
  safe-avatar support lands.
- Reactions use a fixed set of five child-safe emoji; no arbitrary freeform
  emoji so the page stays welcoming.
- Pinned posts can stack; order is controlled by pin_order (lower = higher).
- Soft-delete only: removed posts stay in the DB with status=REMOVED so
  audit trail is intact.
"""

from __future__ import annotations

from django.conf import settings
from django.db import models
from django.utils import timezone

from apps.core.models import BaseModel


# ─── Category ────────────────────────────────────────────────────────────────

class Category(BaseModel):
    """
    A topic grouping for community posts.

    Visibility controls who can *read* posts in this category:
      PUBLIC      — anyone, including unauthenticated visitors
      SIGNED_IN   — any logged-in user regardless of role
      ROLE        — only users whose role is in allowed_roles
    """

    class Visibility(models.TextChoices):
        PUBLIC = "public", "Public (anyone can read)"
        SIGNED_IN = "signed_in", "Signed-in members only"
        ROLE = "role", "Specific roles only"

    # Slug used in URLs and templates; set once and never changed publicly.
    slug = models.SlugField(max_length=60, unique=True)
    name = models.CharField(max_length=80)
    icon = models.CharField(
        max_length=10,
        blank=True,
        help_text="Bootstrap Icons class, e.g. 'bi-megaphone'. Shown on the pill.",
    )
    description = models.CharField(max_length=300, blank=True)
    visibility = models.CharField(
        max_length=20,
        choices=Visibility.choices,
        default=Visibility.PUBLIC,
        db_index=True,
    )
    # JSON list of role slugs, e.g. ["teacher", "director", "admin"]
    # Only used when visibility == ROLE.
    allowed_roles = models.JSONField(
        default=list,
        blank=True,
        help_text=(
            "Role slugs that can read this category when visibility is "
            "'Specific roles only'. E.g. [\"teacher\", \"director\", \"admin\"]."
        ),
    )
    display_order = models.PositiveSmallIntegerField(default=0, db_index=True)
    is_active = models.BooleanField(
        default=True,
        db_index=True,
        help_text="Hidden categories are not shown to anyone.",
    )

    class Meta:
        db_table = "community_category"
        ordering = ["display_order", "id"]
        verbose_name = "category"
        verbose_name_plural = "categories"

    def __str__(self) -> str:
        return self.name

    def is_visible_to(self, user) -> bool:
        """
        Whether *user* (or None for a visitor) can read this category.
        Does not check is_active — callers must filter that separately.
        """
        if not self.is_active:
            return False
        if self.visibility == self.Visibility.PUBLIC:
            return True
        if user is None or not user.is_authenticated:
            return False
        if self.visibility == self.Visibility.SIGNED_IN:
            return True
        # ROLE
        return getattr(user, "role", "") in (self.allowed_roles or [])


# ─── Post ─────────────────────────────────────────────────────────────────────

class Post(BaseModel):
    """
    A community post: the top-level piece of content in a category.

    Status flow:
      PENDING  → (admin approves) → PUBLISHED
               → (admin rejects)  → REMOVED
      PUBLISHED → (admin/author removes) → REMOVED
    Student posts in Public categories start PENDING; all others start PUBLISHED.
    """

    class Status(models.TextChoices):
        PENDING = "pending", "Pending approval"
        PUBLISHED = "published", "Published"
        REMOVED = "removed", "Removed"

    author = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name="community_posts",
        db_index=True,
    )
    category = models.ForeignKey(
        Category,
        on_delete=models.PROTECT,
        related_name="posts",
        db_index=True,
    )
    title = models.CharField(max_length=200)
    body = models.TextField(max_length=10_000)
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.PUBLISHED,
        db_index=True,
    )
    # Pinning
    is_pinned = models.BooleanField(default=False, db_index=True)
    pin_order = models.PositiveSmallIntegerField(
        default=0,
        help_text="Lower numbers appear higher in the pinned stack.",
    )
    # Moderation
    removed_at = models.DateTimeField(null=True, blank=True)
    removed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="community_posts_removed",
    )
    removal_reason = models.CharField(max_length=300, blank=True)
    # Approval
    approved_at = models.DateTimeField(null=True, blank=True)
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="community_posts_approved",
    )

    class Meta:
        db_table = "community_post"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["category", "status", "-created_at"]),
            models.Index(fields=["status", "is_pinned", "pin_order"]),
        ]

    def __str__(self) -> str:
        return self.title

    @property
    def author_display_name(self) -> str:
        """
        First name + last initial for students.
        Full name for all other roles (and deleted accounts).
        """
        user = self.author
        if user is None:
            return "Deleted member"
        if getattr(user, "role", "") == "student":
            first = user.first_name or user.username
            last_initial = (user.last_name or "")[:1]
            return f"{first} {last_initial}." if last_initial else first
        return user.get_full_name() or user.username

    @property
    def author_role_display(self) -> str:
        if self.author is None:
            return ""
        return self.author.get_role_display()

    @property
    def share_path(self) -> str:
        from django.urls import reverse
        return reverse("community:post_detail", kwargs={"pk": self.pk})


# ─── Comment ──────────────────────────────────────────────────────────────────

class Comment(BaseModel):
    """
    A reply to a post.  One level only — no reply-to-reply.

    Soft-delete: removed comments keep their row with is_removed=True so
    the thread count stays accurate and the audit trail is intact.
    The template shows "[comment removed]" in place of the body.
    """

    post = models.ForeignKey(
        Post,
        on_delete=models.CASCADE,
        related_name="comments",
        db_index=True,
    )
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name="community_comments",
        db_index=True,
    )
    body = models.TextField(max_length=3_000)
    is_removed = models.BooleanField(default=False, db_index=True)
    removed_at = models.DateTimeField(null=True, blank=True)
    removed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="community_comments_removed",
    )

    class Meta:
        db_table = "community_comment"
        ordering = ["created_at"]
        indexes = [
            models.Index(fields=["post", "is_removed", "created_at"]),
        ]

    def __str__(self) -> str:
        return f"Comment on '{self.post}' by {self.author}"

    @property
    def author_display_name(self) -> str:
        user = self.author
        if user is None:
            return "Deleted member"
        if getattr(user, "role", "") == "student":
            first = user.first_name or user.username
            last_initial = (user.last_name or "")[:1]
            return f"{first} {last_initial}." if last_initial else first
        return user.get_full_name() or user.username

    @property
    def author_role_display(self) -> str:
        if self.author is None:
            return ""
        return self.author.get_role_display()


# ─── Reaction ─────────────────────────────────────────────────────────────────

class Reaction(BaseModel):
    """
    A single emoji reaction from one user on one post.
    One row per (user, post, emoji) — a user can react with multiple
    different emoji but only once per emoji per post.
    """

    # Child-safe fixed set.
    EMOJI_CHOICES = [
        ("👍", "Thumbs up"),
        ("❤️", "Heart"),
        ("😂", "Laugh"),
        ("🎉", "Celebrate"),
        ("💡", "Idea"),
    ]
    EMOJI_VALUES = [e[0] for e in EMOJI_CHOICES]

    post = models.ForeignKey(
        Post,
        on_delete=models.CASCADE,
        related_name="reactions",
        db_index=True,
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="community_reactions",
        db_index=True,
    )
    emoji = models.CharField(max_length=8, choices=EMOJI_CHOICES)

    class Meta:
        db_table = "community_reaction"
        unique_together = [("post", "user", "emoji")]
        indexes = [
            models.Index(fields=["post", "emoji"]),
        ]

    def __str__(self) -> str:
        return f"{self.user} reacted {self.emoji} on post {self.post_id}"


# ─── CommunityProfile ─────────────────────────────────────────────────────────

class CommunityProfile(BaseModel):
    """
    Thin per-user community record.

    Tracks consent (required before a user can post) and the timestamp
    used for the "online now" counter (active in the last 5 minutes).
    One row per user, created lazily on first community visit.
    """

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="community_profile",
    )
    consented_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When the user accepted the community rules. Required to post.",
    )
    last_seen_at = models.DateTimeField(
        null=True,
        blank=True,
        db_index=True,
        help_text="Updated on each community page visit. Used for the online-now count.",
    )
    # Admin-set suspension
    is_suspended = models.BooleanField(default=False)
    suspended_reason = models.CharField(max_length=300, blank=True)

    class Meta:
        db_table = "community_profile"

    def __str__(self) -> str:
        return f"CommunityProfile({self.user})"

    @property
    def has_consented(self) -> bool:
        return self.consented_at is not None

    @property
    def is_online(self) -> bool:
        """Active in the last 5 minutes."""
        if self.last_seen_at is None:
            return False
        return (timezone.now() - self.last_seen_at).seconds < 300

    @classmethod
    def touch(cls, user) -> "CommunityProfile":
        """Update or create the profile, bumping last_seen_at."""
        profile, _ = cls.objects.get_or_create(user=user)
        cls.objects.filter(pk=profile.pk).update(last_seen_at=timezone.now())
        profile.last_seen_at = timezone.now()
        return profile
