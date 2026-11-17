"""
apps/community/admin.py
"""

from django.contrib import admin

from .models import Category, Comment, CommunityProfile, Post, Reaction


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ["name", "slug", "visibility", "display_order", "is_active"]
    list_editable = ["display_order", "is_active"]
    list_filter = ["visibility", "is_active"]
    search_fields = ["name", "slug"]
    prepopulated_fields = {"slug": ("name",)}
    ordering = ["display_order", "name"]


@admin.register(Post)
class PostAdmin(admin.ModelAdmin):
    list_display = ["title", "author", "category", "status", "is_pinned", "created_at"]
    list_filter = ["status", "is_pinned", "category"]
    search_fields = ["title", "body", "author__first_name", "author__last_name"]
    raw_id_fields = ["author", "category", "removed_by", "approved_by"]
    readonly_fields = ["created_at", "updated_at", "approved_at", "removed_at"]
    ordering = ["-created_at"]


@admin.register(Comment)
class CommentAdmin(admin.ModelAdmin):
    list_display = ["__str__", "author", "is_removed", "created_at"]
    list_filter = ["is_removed"]
    search_fields = ["body", "author__first_name", "author__last_name"]
    raw_id_fields = ["author", "post", "removed_by"]
    readonly_fields = ["created_at", "updated_at"]


@admin.register(Reaction)
class ReactionAdmin(admin.ModelAdmin):
    list_display = ["user", "post", "emoji", "created_at"]
    list_filter = ["emoji"]
    raw_id_fields = ["user", "post"]


@admin.register(CommunityProfile)
class CommunityProfileAdmin(admin.ModelAdmin):
    list_display = ["user", "has_consented", "is_suspended", "last_seen_at"]
    list_filter = ["is_suspended"]
    search_fields = ["user__first_name", "user__last_name", "user__email"]
    readonly_fields = ["last_seen_at", "consented_at"]
