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
        max_length=40,
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
    staff_post_only = models.BooleanField(
        default=False,
        help_text=(
            "When on, only teachers, directors and admins may post here. "
            "Students and parents can read but not post."
        ),
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
    # Thread lock (admin can stop new replies without removing content)
    is_locked = models.BooleanField(
        default=False,
        help_text="Locked threads show content but accept no new replies.",
    )
    # Edit tracking
    edited_at = models.DateTimeField(null=True, blank=True)
    edit_count = models.PositiveSmallIntegerField(default=0)

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
    A reply to a post, or a reply to another reply.

    Threads are one level deep: replying to a reply attaches the new
    comment to the same top-level parent, so the feed never nests deeper
    than the reference layout.

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
    parent = models.ForeignKey(
        "self",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="replies",
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
    # Edit tracking
    edited_at = models.DateTimeField(null=True, blank=True)
    edit_count = models.PositiveSmallIntegerField(default=0)

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
    suspended_until = models.DateTimeField(null=True, blank=True)
    # Admin-set mute (cannot post/reply, can still read)
    is_muted = models.BooleanField(default=False)
    muted_until = models.DateTimeField(null=True, blank=True)
    muted_reason = models.CharField(max_length=300, blank=True)
    # Strike counter (auto-incremented by moderation service)
    strike_count = models.PositiveSmallIntegerField(default=0)
    # Rate-limit: last post timestamp and daily count
    last_post_at = models.DateTimeField(null=True, blank=True)
    posts_today = models.PositiveSmallIntegerField(default=0)
    posts_today_date = models.DateField(null=True, blank=True)
    # Opt-in member-map location. Only a town label is stored — never an
    # address — and members choose whether it is visible to others.
    location_label = models.CharField(max_length=120, blank=True)
    location_country = models.CharField(max_length=80, blank=True)
    latitude = models.DecimalField(
        max_digits=9, decimal_places=6, null=True, blank=True
    )
    longitude = models.DecimalField(
        max_digits=9, decimal_places=6, null=True, blank=True
    )
    location_shared = models.BooleanField(
        default=False,
        help_text="Show this member's town on the community map.",
    )
    location_updated_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "community_profile"

    def __str__(self) -> str:
        return f"CommunityProfile({self.user})"

    @property
    def has_consented(self) -> bool:
        return self.consented_at is not None

    @property
    def has_location(self) -> bool:
        return (
            bool(self.location_label)
            and self.latitude is not None
            and self.longitude is not None
        )

    @property
    def is_on_map(self) -> bool:
        return self.has_location and self.location_shared

    @property
    def is_online(self) -> bool:
        """Active in the last 5 minutes."""
        if self.last_seen_at is None:
            return False
        return (timezone.now() - self.last_seen_at).total_seconds() < 300

    @property
    def is_currently_muted(self) -> bool:
        if not self.is_muted:
            return False
        if self.muted_until and timezone.now() >= self.muted_until:
            return False
        return True

    @property
    def is_currently_suspended(self) -> bool:
        if not self.is_suspended:
            return False
        if self.suspended_until and timezone.now() >= self.suspended_until:
            return False
        return True

    @classmethod
    def touch(cls, user) -> "CommunityProfile":
        """Update or create the profile, bumping last_seen_at."""
        profile, _ = cls.objects.get_or_create(user=user)
        cls.objects.filter(pk=profile.pk).update(last_seen_at=timezone.now())
        profile.last_seen_at = timezone.now()
        return profile


# ─── Direct messages ──────────────────────────────────────────────────────────

class DirectConversation(BaseModel):
    """
    A private one-to-one conversation between two community members.

    The pair is stored in ascending user-id order (user_a < user_b) so a
    conversation can only exist once; `other_user()` hides that detail.
    Students are not part of member-to-member messaging (safeguarding).
    """

    user_a = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="direct_conversations_as_a",
    )
    user_b = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="direct_conversations_as_b",
    )
    last_message_at = models.DateTimeField(
        null=True, blank=True, db_index=True
    )

    class Meta:
        db_table = "community_direct_conversation"
        ordering = ["-last_message_at", "-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["user_a", "user_b"],
                name="uniq_community_direct_pair",
            ),
            models.CheckConstraint(
                condition=models.Q(user_a__lt=models.F("user_b")),
                name="community_direct_pair_ordered",
            ),
        ]

    def __str__(self) -> str:
        return f"DirectConversation({self.user_a_id}<->{self.user_b_id})"

    def other_user(self, user):
        return self.user_b if user.pk == self.user_a_id else self.user_a

    def includes(self, user) -> bool:
        return user.pk in (self.user_a_id, self.user_b_id)


class DirectMessage(BaseModel):
    """A single private message inside a DirectConversation."""

    conversation = models.ForeignKey(
        DirectConversation,
        on_delete=models.CASCADE,
        related_name="messages",
    )
    sender = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="direct_messages_sent",
    )
    body = models.TextField(max_length=2_000)
    read_at = models.DateTimeField(null=True, blank=True, db_index=True)

    class Meta:
        db_table = "community_direct_message"
        ordering = ["created_at"]
        indexes = [
            models.Index(fields=["conversation", "created_at"]),
        ]

    def __str__(self) -> str:
        return f"DirectMessage#{self.pk} in conversation {self.conversation_id}"


# ─── PostDraft ────────────────────────────────────────────────────────────────

class PostDraft(BaseModel):
    """
    Per-user autosaved draft for the composer.

    One row per user — overwritten on every autosave. Cleared when the post
    is submitted or the user signs out. Stored server-side so it survives a
    browser crash on a shared school phone.
    """

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="community_draft",
    )
    category = models.ForeignKey(
        Category,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    title = models.CharField(max_length=200, blank=True)
    body = models.TextField(max_length=10_000, blank=True)

    class Meta:
        db_table = "community_post_draft"

    def __str__(self) -> str:
        return f"Draft by {self.user}"


# ─── Warning ──────────────────────────────────────────────────────────────────

class Warning(BaseModel):
    """
    A formal warning issued by admin to a community member.

    Three levels, each carrying an increasingly clear message.
    A warning is always attached to the original content that caused it
    (post or comment), so it is never a surprise to the recipient.
    """

    class Level(models.TextChoices):
        REMINDER = "reminder", "Friendly reminder"
        MUTE = "mute", "Second warning (short posting pause)"
        SUSPENSION = "suspension", "Third warning (community suspension)"
        MANUAL = "manual", "Manual warning (admin discretion)"

    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        APPEALED = "appealed", "Under appeal"
        LIFTED = "lifted", "Lifted by admin"
        EXPIRED = "expired", "Expired"

    recipient = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="community_warnings",
        db_index=True,
    )
    issued_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name="community_warnings_issued",
    )
    level = models.CharField(max_length=20, choices=Level.choices)
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.ACTIVE, db_index=True
    )
    # The content that triggered the warning
    post = models.ForeignKey(
        Post,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="warnings",
    )
    comment = models.ForeignKey(
        "Comment",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="warnings",
    )
    # What the admin writes
    internal_reason = models.TextField(
        max_length=1_000,
        help_text="Internal note visible to admins only.",
    )
    # What the user sees (warm, plain language)
    user_message = models.TextField(
        max_length=1_000,
        help_text="Message shown to the recipient on their notification page.",
    )
    lifted_at = models.DateTimeField(null=True, blank=True)
    lifted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="community_warnings_lifted",
    )
    lift_reason = models.CharField(max_length=300, blank=True)

    class Meta:
        db_table = "community_warning"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["recipient", "status", "-created_at"]),
        ]

    def __str__(self) -> str:
        return f"Warning ({self.level}) → {self.recipient}"


# ─── Report ───────────────────────────────────────────────────────────────────

class Report(BaseModel):
    """
    A signed-in member's report of a post or comment.

    One active report per (reporter, content) pair — a member who reports
    the same post twice just updates the reason. Admin dismisses or acts.
    """

    class Reason(models.TextChoices):
        INAPPROPRIATE = "inappropriate", "Inappropriate or offensive"
        BULLYING = "bullying", "Bullying or harassment"
        SPAM = "spam", "Spam or advertising"
        SAFETY = "safety", "Safety concern"
        WRONG_CATEGORY = "wrong_category", "Wrong category"
        OTHER = "other", "Other"

    class Status(models.TextChoices):
        OPEN = "open", "Open"
        ACTIONED = "actioned", "Actioned"
        DISMISSED = "dismissed", "Dismissed"

    reporter = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="community_reports_filed",
        db_index=True,
    )
    post = models.ForeignKey(
        Post,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="reports",
    )
    comment = models.ForeignKey(
        Comment,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="reports",
    )
    reason = models.CharField(max_length=30, choices=Reason.choices)
    detail = models.CharField(max_length=500, blank=True)
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.OPEN, db_index=True
    )
    resolved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="community_reports_resolved",
    )
    resolved_at = models.DateTimeField(null=True, blank=True)
    resolution_note = models.CharField(max_length=300, blank=True)

    class Meta:
        db_table = "community_report"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["status", "-created_at"]),
        ]
        # One active report per (reporter, post) or (reporter, comment)
        constraints = [
            models.UniqueConstraint(
                fields=["reporter", "post"],
                condition=models.Q(post__isnull=False, status="open"),
                name="unique_open_report_per_post",
            ),
        ]

    def __str__(self) -> str:
        target = f"post {self.post_id}" if self.post_id else f"comment {self.comment_id}"
        return f"Report on {target} by {self.reporter}"


# ─── Appeal ───────────────────────────────────────────────────────────────────

class Appeal(BaseModel):
    """
    A member's one-shot reply to a warning or removal.

    One appeal per warning. After it is submitted the member cannot edit it.
    Admin reads it and decides: uphold or lift.
    """

    class Status(models.TextChoices):
        PENDING = "pending", "Awaiting admin review"
        UPHELD = "upheld", "Warning upheld"
        LIFTED = "lifted", "Warning lifted following appeal"

    warning = models.OneToOneField(
        Warning,
        on_delete=models.CASCADE,
        related_name="appeal",
    )
    appellant = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="community_appeals",
    )
    message = models.TextField(max_length=1_000)
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.PENDING, db_index=True
    )
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="community_appeals_reviewed",
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)
    admin_response = models.TextField(max_length=1_000, blank=True)

    class Meta:
        db_table = "community_appeal"
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"Appeal on warning {self.warning_id} by {self.appellant}"


# ─── BlockedWord ──────────────────────────────────────────────────────────────

class BlockedWord(BaseModel):
    """
    A word or phrase whose presence in a post or comment holds it for review.

    Matching is case-insensitive and whole-word (so "bad" does not match
    "badminton"). A match does not block the content outright — it sets the
    post/comment status to PENDING so a human can read it.
    """

    word = models.CharField(max_length=100, unique=True)
    is_active = models.BooleanField(default=True, db_index=True)
    note = models.CharField(
        max_length=200,
        blank=True,
        help_text="Internal note about why this word is on the list.",
    )

    class Meta:
        db_table = "community_blocked_word"
        ordering = ["word"]

    def __str__(self) -> str:
        return self.word


# ─── CategoryModerator ────────────────────────────────────────────────────────

class CategoryModerator(BaseModel):
    """
    Delegates limited moderation rights to a trusted teacher for one category.

    A moderator can: approve/reject pending posts, hide content (queues for
    admin review), and dismiss reports.
    A moderator cannot: issue warnings, mute, suspend, edit categories, or
    access content in other categories via this role.
    """

    category = models.ForeignKey(
        Category,
        on_delete=models.CASCADE,
        related_name="moderators",
        db_index=True,
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="community_moderated_categories",
        db_index=True,
    )
    assigned_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name="community_moderator_assignments",
    )
    is_active = models.BooleanField(default=True)

    class Meta:
        db_table = "community_category_moderator"
        unique_together = [("category", "user")]

    def __str__(self) -> str:
        return f"{self.user} moderates #{self.category}"
