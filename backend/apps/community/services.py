"""
apps/community/services.py
---------------------------
All write operations for the community (Modules 2 and 3).

Every public-facing mutation:
  - checks permissions in this layer (not just in views)
  - sanitises user input
  - writes the row
  - audit-logs the action
  - sends notifications where appropriate

Rate limits (per post/comment submission):
  - Max 3 posts per minute per user
  - Max 30 posts per day per user
  - Duplicate detection: same title + first 100 chars of body within 60 s

Strike levels:
  - 1st removal in 30 days  → Reminder (notification only)
  - 2nd removal in 30 days  → 24-hour mute
  - 3rd removal in 30 days  → 7-day suspension
  - Admin can always override / lift early
"""

from __future__ import annotations

import hashlib
import re
from datetime import timedelta

import bleach

from django.core.cache import cache
from django.utils import timezone
from django.utils.text import slugify

from apps.core.audit_log import log_action
from apps.core.notifications import notify

from .models import (
    Appeal,
    BlockedWord,
    Category,
    CategoryModerator,
    Comment,
    CommunityProfile,
    Post,
    PostDraft,
    Reaction,
    Report,
    Warning,
)

# ─── Sanitisation ─────────────────────────────────────────────────────────────

ALLOWED_TAGS = ["b", "strong", "em", "i", "u", "ul", "ol", "li", "p", "br", "a"]
ALLOWED_ATTRS = {"a": ["href", "title", "rel"]}
EDIT_WINDOW_SECONDS = 15 * 60  # 15 minutes


def _sanitise(text: str) -> str:
    return bleach.clean(text, tags=ALLOWED_TAGS, attributes=ALLOWED_ATTRS, strip=True)


def _plain_sanitise(text: str) -> str:
    return bleach.clean(text, tags=[], attributes={}, strip=True).strip()


# ─── Word filter ──────────────────────────────────────────────────────────────

_BLOCKED_CACHE_KEY = "community:blocked_words"
_BLOCKED_CACHE_TTL = 60 * 10  # 10 minutes


def _blocked_words() -> list[str]:
    """Return active blocked words, cached for 10 minutes."""
    cached = cache.get(_BLOCKED_CACHE_KEY)
    if cached is not None:
        return cached
    words = list(
        BlockedWord.objects.filter(is_active=True).values_list("word", flat=True)
    )
    cache.set(_BLOCKED_CACHE_KEY, words, _BLOCKED_CACHE_TTL)
    return words


def _contains_blocked_word(text: str) -> bool:
    """True if text contains any active blocked word (whole-word, case-insensitive)."""
    lower = text.lower()
    for word in _blocked_words():
        pattern = r"\b" + re.escape(word.lower()) + r"\b"
        if re.search(pattern, lower):
            return True
    return False


def invalidate_blocked_words_cache() -> None:
    cache.delete(_BLOCKED_CACHE_KEY)


# ─── Rate limiting ────────────────────────────────────────────────────────────

RATE_PER_MINUTE = 3
RATE_PER_DAY = 30


def _check_rate_limit(profile: CommunityProfile) -> tuple[bool, str]:
    """
    Return (allowed, reason). Updates counters on the profile row.
    Per-minute limit uses Django cache; per-day uses a date field on the profile.
    """
    now = timezone.now()
    today = timezone.localdate()

    # Per-minute: cache key per user
    minute_key = f"community:posts_per_min:{profile.user_id}"
    minute_count = cache.get(minute_key, 0)
    if minute_count >= RATE_PER_MINUTE:
        return False, (
            "You're posting very quickly. Please wait a moment before posting again."
        )
    cache.set(minute_key, minute_count + 1, 60)

    # Per-day: stored on the profile
    if profile.posts_today_date != today:
        profile.posts_today = 0
        profile.posts_today_date = today

    if profile.posts_today >= RATE_PER_DAY:
        return False, (
            "You've reached today's posting limit. "
            "Come back tomorrow to share more — your ideas are valued!"
        )

    profile.posts_today += 1
    profile.last_post_at = now
    CommunityProfile.objects.filter(pk=profile.pk).update(
        posts_today=profile.posts_today,
        posts_today_date=profile.posts_today_date,
        last_post_at=now,
    )
    return True, ""


def _check_duplicate(user, title: str, body: str) -> bool:
    """True if the user submitted the same content in the last 60 seconds."""
    fingerprint = hashlib.sha1(
        f"{user.pk}:{title[:100]}:{body[:100]}".encode()
    ).hexdigest()
    key = f"community:dup:{fingerprint}"
    if cache.get(key):
        return True
    cache.set(key, 1, 60)
    return False


# ─── CommunityProfile helpers ─────────────────────────────────────────────────

def touch_profile(user) -> CommunityProfile:
    return CommunityProfile.touch(user)


def grant_consent(user) -> CommunityProfile:
    """Record that the user has accepted the community rules."""
    profile, _ = CommunityProfile.objects.get_or_create(user=user)
    if not profile.consented_at:
        profile.consented_at = timezone.now()
        profile.save(update_fields=["consented_at"])
    return profile


# ─── Category management ──────────────────────────────────────────────────────

def create_category(*, actor, name: str, icon: str = "", description: str = "",
                    visibility: str = Category.Visibility.PUBLIC,
                    allowed_roles: list[str] | None = None,
                    display_order: int = 0,
                    staff_post_only: bool = False) -> Category:
    name = _plain_sanitise(name)[:80]
    slug = _unique_slug(name)
    cat = Category.objects.create(
        slug=slug, name=name, icon=icon[:10],
        description=_plain_sanitise(description)[:300],
        visibility=visibility, allowed_roles=allowed_roles or [],
        display_order=display_order, staff_post_only=staff_post_only,
    )
    log_action(actor=actor, action="community.category.create",
               content_type="community.Category", object_id=cat.pk,
               change_summary={"name": name, "visibility": visibility})
    return cat


def update_category(*, actor, category: Category, name: str | None = None,
                    icon: str | None = None, description: str | None = None,
                    visibility: str | None = None, allowed_roles: list[str] | None = None,
                    display_order: int | None = None, is_active: bool | None = None,
                    staff_post_only: bool | None = None) -> Category:
    changes: dict = {}
    if name is not None:
        category.name = _plain_sanitise(name)[:80]; changes["name"] = category.name
    if icon is not None:
        category.icon = icon[:10]; changes["icon"] = icon
    if description is not None:
        category.description = _plain_sanitise(description)[:300]
    if visibility is not None:
        category.visibility = visibility; changes["visibility"] = visibility
    if allowed_roles is not None:
        category.allowed_roles = allowed_roles; changes["allowed_roles"] = allowed_roles
    if display_order is not None:
        category.display_order = display_order
    if is_active is not None:
        category.is_active = is_active; changes["is_active"] = is_active
    if staff_post_only is not None:
        category.staff_post_only = staff_post_only; changes["staff_post_only"] = staff_post_only
    category.save()
    log_action(actor=actor, action="community.category.update",
               content_type="community.Category", object_id=category.pk,
               change_summary=changes)
    return category


def _unique_slug(name: str) -> str:
    base = slugify(name)[:55] or "category"
    slug, n = base, 1
    while Category.objects.filter(slug=slug).exists():
        slug = f"{base}-{n}"; n += 1
    return slug


# ─── Posting gate ─────────────────────────────────────────────────────────────

def can_post_in_category(user, category: Category) -> tuple[bool, str]:
    """
    Return (allowed, reason). Called before showing the composer and again on submit.
    """
    if not category.is_active:
        return False, "This category is not available."
    if not category.is_visible_to(user):
        return False, "You cannot post in this category."

    role = getattr(user, "role", "")
    staff_roles = {"teacher", "director", "admin"}
    if category.staff_post_only and role not in staff_roles:
        return False, "Only staff members may post in this category."

    try:
        profile = user.community_profile
        if profile.is_currently_suspended:
            return False, (
                "Your community posting is paused for a short time. "
                "You can still read all posts. "
                "Please contact the school office if you have questions."
            )
        if profile.is_currently_muted:
            until = profile.muted_until
            if until:
                # Cross-platform time format (no %-I which is Linux-only)
                when = until.strftime("%I:%M %p").lstrip("0") or "12:00 AM"
            else:
                when = "soon"
            return False, (
                f"Your posting is paused until {when} (Nairobi time). "
                "You can still read and browse."
            )
        if not profile.has_consented:
            return False, "consent_required"
    except CommunityProfile.DoesNotExist:
        return False, "consent_required"

    return True, ""


# ─── Post creation ────────────────────────────────────────────────────────────

def create_post(*, actor, category: Category, title: str, body: str,
                student_moderation: bool = True, request=None) -> Post:
    allowed, reason = can_post_in_category(actor, category)
    if not allowed:
        raise PermissionError(reason)

    title = _plain_sanitise(title)[:200]
    body = _sanitise(body)[:10_000]

    # Duplicate detection
    if _check_duplicate(actor, title, body):
        raise ValueError(
            "It looks like you just sent the same post. "
            "If that was a mistake, wait a moment and try again."
        )

    # Rate limit
    profile, _ = CommunityProfile.objects.get_or_create(user=actor)
    allowed_rate, rate_reason = _check_rate_limit(profile)
    if not allowed_rate:
        raise ValueError(rate_reason)

    # Determine initial status
    role = getattr(actor, "role", "")
    is_student = role == "student"
    is_public = category.visibility == Category.Visibility.PUBLIC
    word_hold = _contains_blocked_word(title + " " + body)

    if word_hold or (is_student and is_public and student_moderation):
        status = Post.Status.PENDING
    else:
        status = Post.Status.PUBLISHED

    post = Post.objects.create(
        author=actor, category=category,
        title=title, body=body, status=status,
    )

    # Clear draft if one existed
    PostDraft.objects.filter(user=actor).delete()

    log_action(actor=actor, action="community.post.create",
               content_type="community.Post", object_id=post.pk,
               change_summary={"category": category.name, "status": status,
                               "title": title[:80]},
               request=request)

    if status == Post.Status.PENDING:
        _notify_admins_pending(post)

    return post


def edit_post(*, actor, post: Post, title: str | None = None,
              body: str | None = None, request=None) -> Post:
    """Author can edit within EDIT_WINDOW_SECONDS; admin can edit any time."""
    role = getattr(actor, "role", "")
    is_staff = role in ("admin", "director")
    is_author = post.author_id == actor.pk
    if not (is_staff or is_author):
        raise PermissionError("You cannot edit this post.")
    if is_author and not is_staff:
        age = (timezone.now() - post.created_at).total_seconds()
        if age > EDIT_WINDOW_SECONDS:
            raise PermissionError(
                "Posts can only be edited within 15 minutes of posting."
            )
    if title is not None:
        post.title = _plain_sanitise(title)[:200]
    if body is not None:
        post.body = _sanitise(body)[:10_000]
    post.edited_at = timezone.now()
    post.edit_count += 1
    post.save()
    log_action(actor=actor, action="community.post.edit",
               content_type="community.Post", object_id=post.pk,
               request=request)
    return post


# ─── Post moderation ──────────────────────────────────────────────────────────

def approve_post(*, actor, post: Post, request=None) -> Post:
    _require_mod_or_staff(actor, post.category)
    if post.status != Post.Status.PENDING:
        raise ValueError("Only pending posts can be approved.")
    post.status = Post.Status.PUBLISHED
    post.approved_at = timezone.now()
    post.approved_by = actor
    post.save()
    # Notify author
    if post.author:
        notify(recipient=post.author, title="Your post has been published!",
               message=f'"{post.title}" is now live in {post.category.name}.',
               link=post.share_path, category="community")
    log_action(actor=actor, action="community.post.approve",
               content_type="community.Post", object_id=post.pk, request=request)
    return post


def remove_post(*, actor, post: Post, reason_code: str = "other",
                reason_note: str = "", request=None) -> Post:
    role = getattr(actor, "role", "")
    is_staff = role == "admin"
    is_mod = _is_category_mod(actor, post.category)
    is_author = post.author_id == actor.pk
    if not (is_staff or is_mod or is_author):
        raise PermissionError("You cannot remove this post.")

    reason = _plain_sanitise(reason_note)[:300]
    post.status = Post.Status.REMOVED
    post.removed_at = timezone.now()
    post.removed_by = actor
    post.removal_reason = reason
    post.save()

    # Notify author (kind, neutral)
    if post.author and post.author_id != actor.pk:
        notify(recipient=post.author,
               title="A post of yours was removed",
               message=(
                   "One of your community posts has been removed by a moderator. "
                   "If you think this was a mistake, you can appeal from your notifications."
               ),
               link="/community/my-posts/",
               category="community_moderation")

    log_action(actor=actor, action="community.post.remove",
               content_type="community.Post", object_id=post.pk,
               change_summary={"reason_code": reason_code, "note": reason},
               request=request)

    # Auto-strike only when actor is admin (not category moderator, not director)
    actor_role = getattr(actor, "role", "")
    if actor_role == "admin" and post.author:
        _apply_strike(actor=actor, recipient=post.author,
                      post=post, internal_reason=reason or reason_code)
    return post


def restore_post(*, actor, post: Post, request=None) -> Post:
    _require_staff(actor)
    if post.status != Post.Status.REMOVED:
        raise ValueError("Only removed posts can be restored.")
    post.status = Post.Status.PUBLISHED
    post.removed_at = None
    post.removed_by = None
    post.removal_reason = ""
    post.save()
    if post.author:
        notify(recipient=post.author,
               title="Your post has been restored",
               message=f'"{post.title}" is back in {post.category.name}.',
               link=post.share_path, category="community")
    log_action(actor=actor, action="community.post.restore",
               content_type="community.Post", object_id=post.pk, request=request)
    return post


def pin_post(*, actor, post: Post, pin_order: int = 0, request=None) -> Post:
    _require_staff(actor)
    post.is_pinned = True
    post.pin_order = pin_order
    post.save()
    log_action(actor=actor, action="community.post.pin",
               content_type="community.Post", object_id=post.pk,
               change_summary={"pin_order": pin_order}, request=request)
    return post


def unpin_post(*, actor, post: Post, request=None) -> Post:
    _require_staff(actor)
    post.is_pinned = False
    post.save()
    log_action(actor=actor, action="community.post.unpin",
               content_type="community.Post", object_id=post.pk, request=request)
    return post


def lock_post(*, actor, post: Post, request=None) -> Post:
    _require_staff(actor)
    post.is_locked = True
    post.save()
    log_action(actor=actor, action="community.post.lock",
               content_type="community.Post", object_id=post.pk, request=request)
    return post


def unlock_post(*, actor, post: Post, request=None) -> Post:
    _require_staff(actor)
    post.is_locked = False
    post.save()
    log_action(actor=actor, action="community.post.unlock",
               content_type="community.Post", object_id=post.pk, request=request)
    return post


def move_post(*, actor, post: Post, new_category: Category, request=None) -> Post:
    _require_staff(actor)
    old_name = post.category.name
    post.category = new_category
    post.save()
    log_action(actor=actor, action="community.post.move",
               content_type="community.Post", object_id=post.pk,
               change_summary={"from": old_name, "to": new_category.name},
               request=request)
    return post


# ─── Comments ─────────────────────────────────────────────────────────────────

def create_comment(*, actor, post: Post, body: str, request=None) -> Comment:
    if post.status != Post.Status.PUBLISHED:
        raise PermissionError("You cannot reply to this post.")
    if post.is_locked:
        raise PermissionError("This thread is locked and no longer accepts replies.")
    if not post.category.is_visible_to(actor):
        raise PermissionError("You cannot reply in this category.")

    allowed, reason = can_post_in_category(actor, post.category)
    if not allowed:
        raise PermissionError(reason)

    body = _sanitise(body)[:3_000]

    # Duplicate check
    if _check_duplicate(actor, "", body):
        raise ValueError("It looks like you just sent the same reply.")

    # Rate limit (comments count toward the same daily limit)
    profile, _ = CommunityProfile.objects.get_or_create(user=actor)
    allowed_rate, rate_reason = _check_rate_limit(profile)
    if not allowed_rate:
        raise ValueError(rate_reason)

    word_hold = _contains_blocked_word(body)
    comment = Comment.objects.create(
        post=post, author=actor, body=body,
        is_removed=False,
    )

    if word_hold:
        # Soft-flag: mark comment as removed pending review (admin will see it)
        comment.is_removed = True
        comment.removed_at = timezone.now()
        comment.save(update_fields=["is_removed", "removed_at"])

    log_action(actor=actor, action="community.comment.create",
               content_type="community.Comment", object_id=comment.pk,
               change_summary={"post_id": post.pk}, request=request)

    # Notify post author (not if they're replying to themselves)
    if post.author and post.author_id != actor.pk:
        notify(recipient=post.author,
               title=f"{actor.first_name or 'Someone'} replied to your post",
               message=f'In "{post.title}": {body[:100]}',
               link=post.share_path + "#comments",
               category="community")
    return comment


def edit_comment(*, actor, comment: Comment, body: str, request=None) -> Comment:
    role = getattr(actor, "role", "")
    is_staff = role in ("admin", "director")
    is_author = comment.author_id == actor.pk
    if not (is_staff or is_author):
        raise PermissionError("You cannot edit this comment.")
    if is_author and not is_staff:
        age = (timezone.now() - comment.created_at).total_seconds()
        if age > EDIT_WINDOW_SECONDS:
            raise PermissionError("Comments can only be edited within 15 minutes.")
    comment.body = _sanitise(body)[:3_000]
    comment.edited_at = timezone.now()
    comment.edit_count += 1
    comment.save()
    log_action(actor=actor, action="community.comment.edit",
               content_type="community.Comment", object_id=comment.pk,
               request=request)
    return comment


def remove_comment(*, actor, comment: Comment,
                   reason_code: str = "other", reason_note: str = "",
                   request=None) -> Comment:
    role = getattr(actor, "role", "")
    is_staff = role == "admin"
    is_mod = _is_category_mod(actor, comment.post.category)
    is_author = comment.author_id == actor.pk
    if not (is_staff or is_mod or is_author):
        raise PermissionError("You cannot remove this comment.")
    comment.is_removed = True
    comment.removed_at = timezone.now()
    comment.removed_by = actor
    comment.save()
    if comment.author and comment.author_id != actor.pk:
        notify(recipient=comment.author,
               title="A reply of yours was removed",
               message=(
                   "One of your replies has been removed by a moderator. "
                   "You can appeal from your notifications."
               ),
               link="/community/my-posts/",
               category="community_moderation")
    log_action(actor=actor, action="community.comment.remove",
               content_type="community.Comment", object_id=comment.pk,
               change_summary={"reason_code": reason_code,
                               "note": _plain_sanitise(reason_note)[:300]},
               request=request)
    if (is_staff or is_mod) and comment.author:
        actor_role = getattr(actor, "role", "")
        if actor_role == "admin":
            _apply_strike(actor=actor, recipient=comment.author,
                          comment=comment,
                          internal_reason=reason_note or reason_code)
    return comment


def restore_comment(*, actor, comment: Comment, request=None) -> Comment:
    _require_staff(actor)
    comment.is_removed = False
    comment.removed_at = None
    comment.removed_by = None
    comment.save()
    log_action(actor=actor, action="community.comment.restore",
               content_type="community.Comment", object_id=comment.pk,
               request=request)
    return comment


# ─── Reactions ────────────────────────────────────────────────────────────────

def toggle_reaction(*, actor, post: Post, emoji: str) -> dict:
    if post.status != Post.Status.PUBLISHED:
        raise PermissionError("Cannot react to this post.")
    if not post.category.is_visible_to(actor):
        raise PermissionError("Cannot react in this category.")
    if emoji not in Reaction.EMOJI_VALUES:
        raise ValueError(f"'{emoji}' is not an allowed reaction.")
    reaction, created = Reaction.objects.get_or_create(post=post, user=actor, emoji=emoji)
    if not created:
        reaction.delete()
    count = Reaction.objects.filter(post=post, emoji=emoji).count()
    return {"added": created, "count": count}


# ─── Drafts ───────────────────────────────────────────────────────────────────

def save_draft(*, user, title: str = "", body: str = "",
               category_id: int | None = None) -> PostDraft:
    """Autosave — one row per user, upsert."""
    draft, _ = PostDraft.objects.update_or_create(
        user=user,
        defaults={
            "title": _plain_sanitise(title)[:200],
            "body": _sanitise(body)[:10_000],
            "category_id": category_id,
        },
    )
    return draft


def clear_draft(user) -> None:
    PostDraft.objects.filter(user=user).delete()


def get_draft(user) -> PostDraft | None:
    return PostDraft.objects.filter(user=user).select_related("category").first()


# ─── Warnings ─────────────────────────────────────────────────────────────────

_STRIKE_WINDOW_DAYS = 30
_WARNING_MESSAGES = {
    Warning.Level.REMINDER: (
        "One of your posts was reviewed by a moderator and has been removed. "
        "This is a friendly reminder to review our community rules — "
        "no action has been taken on your account."
    ),
    Warning.Level.MUTE: (
        "This is a second notice about your community posts. "
        "Your posting is paused for 24 hours. "
        "You can still read everything. It will lift automatically."
    ),
    Warning.Level.SUSPENSION: (
        "Your community access has been paused for 7 days. "
        "You can still use all other school features — lessons, results and fees are unaffected. "
        "Please contact the school office if you have questions or think this was a mistake."
    ),
    Warning.Level.MANUAL: (
        "A note from the school community moderator is waiting for you. "
        "Please review it and reach out if you have questions."
    ),
}


def issue_warning(*, actor, recipient, level: str,
                  internal_reason: str, user_message: str = "",
                  post: Post | None = None,
                  comment: Comment | None = None,
                  request=None) -> Warning:
    if getattr(actor, "role", "") != "admin":
        raise PermissionError("Only admins can issue warnings.")
    msg = user_message or _WARNING_MESSAGES.get(level, "")
    warning = Warning.objects.create(
        recipient=recipient,
        issued_by=actor,
        level=level,
        internal_reason=_plain_sanitise(internal_reason)[:1_000],
        user_message=msg,
        post=post,
        comment=comment,
    )
    # Apply mute/suspension if the level calls for it
    profile, _ = CommunityProfile.objects.get_or_create(user=recipient)
    now = timezone.now()
    if level == Warning.Level.MUTE:
        profile.is_muted = True
        profile.muted_until = now + timedelta(hours=24)
        profile.muted_reason = internal_reason[:300]
        profile.save(update_fields=["is_muted", "muted_until", "muted_reason"])
    elif level == Warning.Level.SUSPENSION:
        profile.is_suspended = True
        profile.suspended_until = now + timedelta(days=7)
        profile.suspended_reason = internal_reason[:300]
        profile.save(update_fields=["is_suspended", "suspended_until", "suspended_reason"])

    notify(recipient=recipient,
           title="A message from the community moderator",
           message=msg[:200],
           link=f"/community/warning/{warning.pk}/",
           category="community_moderation")

    log_action(actor=actor, action="community.warning.issue",
               content_type="community.Warning", object_id=warning.pk,
               change_summary={"level": level, "recipient": str(recipient)},
               request=request)
    return warning


def lift_warning(*, actor, warning: Warning, reason: str = "", request=None) -> Warning:
    _require_staff(actor)
    warning.status = Warning.Status.LIFTED
    warning.lifted_at = timezone.now()
    warning.lifted_by = actor
    warning.lift_reason = _plain_sanitise(reason)[:300]
    warning.save()
    # If this was the active mute/suspension, lift it
    profile = getattr(warning.recipient, "community_profile", None)
    if profile:
        if warning.level == Warning.Level.MUTE:
            CommunityProfile.objects.filter(pk=profile.pk).update(
                is_muted=False, muted_until=None
            )
        elif warning.level == Warning.Level.SUSPENSION:
            CommunityProfile.objects.filter(pk=profile.pk).update(
                is_suspended=False, suspended_until=None
            )
    notify(recipient=warning.recipient,
           title="Your community access has been restored",
           message="Your posting has been re-enabled. Welcome back.",
           link="/community/",
           category="community_moderation")
    log_action(actor=actor, action="community.warning.lift",
               content_type="community.Warning", object_id=warning.pk,
               request=request)
    return warning


def _apply_strike(*, actor, recipient, internal_reason: str,
                  post: Post | None = None,
                  comment: Comment | None = None) -> Warning | None:
    """
    Determine which strike level applies based on removal history in the last
    30 days, then issue the warning automatically.
    """
    window_start = timezone.now() - timedelta(days=_STRIKE_WINDOW_DAYS)
    recent_removals = Warning.objects.filter(
        recipient=recipient,
        created_at__gte=window_start,
        status=Warning.Status.ACTIVE,
    ).count()

    if recent_removals == 0:
        level = Warning.Level.REMINDER
    elif recent_removals == 1:
        level = Warning.Level.MUTE
    else:
        level = Warning.Level.SUSPENSION

    # Increment strike counter
    CommunityProfile.objects.filter(user=recipient).update(
        strike_count=models_strike_increment(recipient)
    )

    return issue_warning(
        actor=actor, recipient=recipient,
        level=level, internal_reason=internal_reason,
        post=post, comment=comment,
    )


def models_strike_increment(user) -> int:
    """Return the new strike_count value (read + 1)."""
    try:
        return user.community_profile.strike_count + 1
    except Exception:
        return 1


# ─── Mute / suspend (manual) ─────────────────────────────────────────────────

def mute_user(*, actor, user, duration_hours: int = 24,
              reason: str = "", request=None) -> CommunityProfile:
    _require_staff(actor)
    profile, _ = CommunityProfile.objects.get_or_create(user=user)
    profile.is_muted = True
    profile.muted_until = timezone.now() + timedelta(hours=duration_hours)
    profile.muted_reason = _plain_sanitise(reason)[:300]
    profile.save(update_fields=["is_muted", "muted_until", "muted_reason"])
    log_action(actor=actor, action="community.user.mute",
               content_type="accounts.User", object_id=user.pk,
               change_summary={"hours": duration_hours, "reason": reason},
               request=request)
    return profile


def unmute_user(*, actor, user, request=None) -> CommunityProfile:
    _require_staff(actor)
    profile, _ = CommunityProfile.objects.get_or_create(user=user)
    profile.is_muted = False
    profile.muted_until = None
    profile.save(update_fields=["is_muted", "muted_until"])
    log_action(actor=actor, action="community.user.unmute",
               content_type="accounts.User", object_id=user.pk, request=request)
    return profile


def suspend_user(*, actor, user, duration_days: int = 7,
                 reason: str = "", request=None) -> CommunityProfile:
    _require_staff(actor)
    profile, _ = CommunityProfile.objects.get_or_create(user=user)
    profile.is_suspended = True
    profile.suspended_until = timezone.now() + timedelta(days=duration_days)
    profile.suspended_reason = _plain_sanitise(reason)[:300]
    profile.save(update_fields=["is_suspended", "suspended_until", "suspended_reason"])
    notify(recipient=user,
           title="Your community access is paused",
           message=_WARNING_MESSAGES[Warning.Level.SUSPENSION],
           link="/community/", category="community_moderation")
    log_action(actor=actor, action="community.user.suspend",
               content_type="accounts.User", object_id=user.pk,
               change_summary={"days": duration_days, "reason": reason},
               request=request)
    return profile


def unsuspend_user(*, actor, user, request=None) -> CommunityProfile:
    _require_staff(actor)
    profile, _ = CommunityProfile.objects.get_or_create(user=user)
    profile.is_suspended = False
    profile.suspended_until = None
    profile.save(update_fields=["is_suspended", "suspended_until"])
    notify(recipient=user,
           title="Your community access has been restored",
           message="Welcome back! You can post and reply again.",
           link="/community/", category="community_moderation")
    log_action(actor=actor, action="community.user.unsuspend",
               content_type="accounts.User", object_id=user.pk, request=request)
    return profile


# ─── Reports ──────────────────────────────────────────────────────────────────

def file_report(*, reporter, post: Post | None = None,
                comment: Comment | None = None,
                reason: str, detail: str = "") -> Report:
    if post is None and comment is None:
        raise ValueError("A report must target a post or a comment.")

    # Upsert: update the existing open report rather than creating a duplicate
    if post:
        report, _ = Report.objects.update_or_create(
            reporter=reporter, post=post,
            defaults={"reason": reason,
                      "detail": _plain_sanitise(detail)[:500],
                      "status": Report.Status.OPEN},
        )
    else:
        report, _ = Report.objects.update_or_create(
            reporter=reporter, comment=comment,
            defaults={"reason": reason,
                      "detail": _plain_sanitise(detail)[:500],
                      "status": Report.Status.OPEN},
        )
    return report


def resolve_report(*, actor, report: Report,
                   resolution: str, note: str = "",
                   request=None) -> Report:
    """resolution = 'actioned' or 'dismissed'"""
    _require_mod_or_staff(actor, _report_category(report))
    report.status = resolution
    report.resolved_by = actor
    report.resolved_at = timezone.now()
    report.resolution_note = _plain_sanitise(note)[:300]
    report.save()
    log_action(actor=actor, action=f"community.report.{resolution}",
               content_type="community.Report", object_id=report.pk,
               request=request)
    return report


def _report_category(report: Report) -> Category | None:
    if report.post:
        return report.post.category
    if report.comment:
        return report.comment.post.category
    return None


# ─── Appeals ──────────────────────────────────────────────────────────────────

def file_appeal(*, appellant, warning: Warning, message: str) -> Appeal:
    if warning.recipient_id != appellant.pk:
        raise PermissionError("You can only appeal your own warnings.")
    if hasattr(warning, "appeal"):
        raise ValueError("You have already submitted an appeal for this warning.")
    if warning.status not in (Warning.Status.ACTIVE,):
        raise ValueError("This warning cannot be appealed.")
    appeal = Appeal.objects.create(
        warning=warning,
        appellant=appellant,
        message=_plain_sanitise(message)[:1_000],
    )
    warning.status = Warning.Status.APPEALED
    warning.save(update_fields=["status"])
    # Notify all admins
    from apps.accounts.models import User
    from apps.core.notifications import notify_many
    notify_many(
        recipients=User.objects.filter(role="admin", is_active=True),
        title="Community appeal submitted",
        message=f"{appellant.first_name or appellant.username} has appealed a warning.",
        link=f"/community/admin/appeal/{appeal.pk}/",
        category="community_moderation",
    )
    return appeal


def review_appeal(*, actor, appeal: Appeal, decision: str,
                  admin_response: str = "", request=None) -> Appeal:
    """decision = 'upheld' or 'lifted'"""
    _require_staff(actor)
    appeal.status = decision
    appeal.reviewed_by = actor
    appeal.reviewed_at = timezone.now()
    appeal.admin_response = _plain_sanitise(admin_response)[:1_000]
    appeal.save()
    if decision == Appeal.Status.LIFTED:
        lift_warning(actor=actor, warning=appeal.warning,
                     reason="Lifted following appeal.", request=request)
    else:
        appeal.warning.status = Warning.Status.ACTIVE
        appeal.warning.save(update_fields=["status"])
        notify(recipient=appeal.appellant,
               title="Your appeal has been reviewed",
               message=(
                   "The moderator has reviewed your appeal. "
                   "The original decision has been upheld. "
                   "If you have further questions please contact the school office."
               ),
               link=f"/community/warning/{appeal.warning_id}/",
               category="community_moderation")
    log_action(actor=actor, action=f"community.appeal.{decision}",
               content_type="community.Appeal", object_id=appeal.pk,
               request=request)
    return appeal


# ─── Category moderator delegation ───────────────────────────────────────────

def assign_moderator(*, actor, category: Category, user, request=None) -> CategoryModerator:
    _require_staff(actor)
    role = getattr(user, "role", "")
    if role not in ("teacher", "admin", "director"):
        raise PermissionError("Only teachers, directors or admins can be moderators.")
    mod, created = CategoryModerator.objects.get_or_create(
        category=category, user=user,
        defaults={"assigned_by": actor, "is_active": True},
    )
    if not created:
        mod.is_active = True
        mod.assigned_by = actor
        mod.save()
    log_action(actor=actor, action="community.moderator.assign",
               content_type="community.CategoryModerator", object_id=mod.pk,
               change_summary={"category": category.name, "user": str(user)},
               request=request)
    return mod


def remove_moderator(*, actor, mod: CategoryModerator, request=None) -> None:
    _require_staff(actor)
    mod.is_active = False
    mod.save()
    log_action(actor=actor, action="community.moderator.remove",
               content_type="community.CategoryModerator", object_id=mod.pk,
               request=request)


# ─── Data retention & export ─────────────────────────────────────────────────

def anonymise_user_content(*, actor, user, request=None) -> dict:
    """
    Anonymise all community content for a user (parental erasure request).
    Posts and comments are kept (thread integrity) but the author FK is set
    to NULL. The body is replaced with '[Content removed]'.
    Returns counts for the audit log.
    """
    _require_staff(actor)
    now = timezone.now()
    post_count = Post.objects.filter(author=user).update(
        author=None,
        title="[Content removed]",
        body="[Content removed at the request of the account holder.]",
        updated_at=now,
    )
    comment_count = Comment.objects.filter(author=user).update(
        author=None,
        body="[Content removed at the request of the account holder.]",
        updated_at=now,
    )
    # Remove draft and profile
    PostDraft.objects.filter(user=user).delete()
    CommunityProfile.objects.filter(user=user).update(
        consented_at=None,
        last_seen_at=None,
    )
    summary = {"posts_anonymised": post_count, "comments_anonymised": comment_count}
    log_action(actor=actor, action="community.user.anonymise",
               content_type="accounts.User", object_id=user.pk,
               change_summary=summary, request=request)
    return summary


def export_user_content(user) -> list[dict]:
    """Return a list of dicts describing all the user's posts and comments."""
    rows = []
    for post in Post.objects.filter(author=user).order_by("created_at"):
        rows.append({
            "type": "post",
            "id": post.pk,
            "category": post.category.name,
            "title": post.title,
            "body": post.body,
            "status": post.status,
            "created_at": post.created_at.isoformat(),
        })
    for comment in Comment.objects.filter(author=user).select_related("post").order_by("created_at"):
        rows.append({
            "type": "comment",
            "id": comment.pk,
            "post_id": comment.post_id,
            "post_title": comment.post.title if comment.post else "",
            "body": comment.body,
            "is_removed": comment.is_removed,
            "created_at": comment.created_at.isoformat(),
        })
    return rows


# ─── Notifications helpers ────────────────────────────────────────────────────

def _notify_admins_pending(post: Post) -> None:
    from apps.accounts.models import User
    from apps.core.notifications import notify_many
    notify_many(
        recipients=User.objects.filter(role="admin", is_active=True),
        title="Community post awaiting approval",
        message=f'"{post.title}" in {post.category.name} needs review.',
        link=f"/community/admin/queue/",
        category="community_moderation",
    )


# ─── Permission helpers ───────────────────────────────────────────────────────

def _require_staff(actor) -> None:
    """Admin only. Director is community-read-only."""
    role = getattr(actor, "role", "")
    if role != "admin":
        raise PermissionError("Only admins can perform this moderation action.")


def _is_category_mod(user, category: Category) -> bool:
    return CategoryModerator.objects.filter(
        user=user, category=category, is_active=True
    ).exists()


def _require_mod_or_staff(actor, category: Category | None) -> None:
    """Admin, or an active category moderator. Director is read-only."""
    role = getattr(actor, "role", "")
    if role == "admin":
        return
    if category and _is_category_mod(actor, category):
        return
    raise PermissionError("You do not have moderation rights here.")
