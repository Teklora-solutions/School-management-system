"""
apps/community/tests/fixtures.py
---------------------------------
Shared builders for community tests.
"""

from __future__ import annotations

from apps.accounts.models import User
from apps.community.models import Category, Comment, Post, Reaction
from apps.core.models import SchoolSettings


def make_user(role: str, *, username: str | None = None, **kwargs) -> User:
    uname = username or f"{role}_test_{User.objects.count()}"
    # first_name and last_name may be passed in kwargs; set defaults only if absent
    kwargs.setdefault("first_name", role.capitalize())
    kwargs.setdefault("last_name", "User")
    user = User.objects.create_user(
        username=uname,
        email=f"{uname}@test.school",
        password="testpass123",
        role=role,
        must_change_password=False,
        **kwargs,
    )
    return user


def make_category(
    *,
    slug: str = "test-general",
    name: str = "Test General",
    visibility: str = Category.Visibility.PUBLIC,
    allowed_roles: list | None = None,
    is_active: bool = True,
    display_order: int = 10,
    icon: str = "bi-chat",
    staff_post_only: bool = False,
) -> Category:
    cat, _ = Category.objects.get_or_create(
        slug=slug,
        defaults={
            "name": name,
            "visibility": visibility,
            "allowed_roles": allowed_roles or [],
            "is_active": is_active,
            "display_order": display_order,
            "icon": icon,
            "staff_post_only": staff_post_only,
        },
    )
    return cat


def make_post(
    *,
    author: User,
    category: Category,
    title: str = "Test post",
    body: str = "This is a test post body.",
    status: str = Post.Status.PUBLISHED,
    is_pinned: bool = False,
    pin_order: int = 0,
) -> Post:
    return Post.objects.create(
        author=author,
        category=category,
        title=title,
        body=body,
        status=status,
        is_pinned=is_pinned,
        pin_order=pin_order,
    )


def make_comment(*, author: User, post: Post, body: str = "Test reply.") -> Comment:
    return Comment.objects.create(author=author, post=post, body=body)


def enable_community(*, paused: bool = False, student_moderation: bool = True) -> SchoolSettings:
    """Enable the community feature on the SchoolSettings singleton."""
    settings = SchoolSettings.load()
    settings.community_enabled = True
    settings.community_paused = paused
    settings.community_student_moderation = student_moderation
    settings.save()
    return settings


def disable_community() -> SchoolSettings:
    settings = SchoolSettings.load()
    settings.community_enabled = False
    settings.save()
    return settings
