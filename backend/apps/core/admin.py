"""
apps/core/admin.py
"""

from django.contrib import admin

from .models import AuditLog, Notification


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = (
        "created_at",
        "actor",
        "action",
        "content_type",
        "object_id",
        "request_id",
    )
    list_filter = ("action", "content_type")
    search_fields = ("actor__email", "action", "object_id", "request_id")
    readonly_fields = (
        "created_at",
        "updated_at",
        "actor",
        "action",
        "content_type",
        "object_id",
        "change_summary",
        "request_id",
        "ip_address",
    )

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display = ("created_at", "recipient", "title", "category", "is_read")
    list_filter = ("is_read", "category")
    search_fields = ("recipient__email", "title")
