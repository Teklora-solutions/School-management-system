"""
apps/api/admin.py
-----------------
Django admin registration for the API integration models.

APIClient rows show the key prefix (safe to display) and scopes.
The plain-text key is never shown here — it was returned once at creation.

WebhookEndpoint and WebhookDelivery are read-mostly; delivery rows are
append-only by convention so only the status filter and retry action matter.
"""

from django.contrib import admin
from django.utils.html import format_html
from django.utils import timezone

from .models import APIClient, WebhookDelivery, WebhookEndpoint


# ---------------------------------------------------------------------------
# APIClient
# ---------------------------------------------------------------------------


@admin.register(APIClient)
class APIClientAdmin(admin.ModelAdmin):
    list_display = [
        "name",
        "key_prefix_display",
        "scopes_display",
        "is_active",
        "last_used_at",
        "created_at",
    ]
    list_filter = ["is_active", "created_at"]
    search_fields = ["name", "description"]
    readonly_fields = [
        "key_prefix",
        "key_hash",
        "last_used_at",
        "created_at",
        "updated_at",
        "created_by",
    ]
    fieldsets = [
        (
            "Identity",
            {
                "fields": ["name", "description", "created_by", "is_active"],
            },
        ),
        (
            "Key (read-only)",
            {
                "fields": ["key_prefix", "key_hash"],
                "description": (
                    "The plain-text key was shown once at creation and is not "
                    "stored here. Revoke and create a new client if the key is lost."
                ),
            },
        ),
        (
            "Access control",
            {
                "fields": ["scopes", "allowed_ips"],
            },
        ),
        (
            "Timestamps",
            {
                "fields": ["last_used_at", "created_at", "updated_at"],
                "classes": ["collapse"],
            },
        ),
    ]

    @admin.display(description="Key prefix")
    def key_prefix_display(self, obj):
        return format_html("<code>{}…</code>", obj.key_prefix)

    @admin.display(description="Scopes")
    def scopes_display(self, obj):
        return ", ".join(obj.scopes) if obj.scopes else "—"

    def has_delete_permission(self, request, obj=None):
        # Force revocation via is_active rather than row deletion to preserve audit trail.
        return False


# ---------------------------------------------------------------------------
# WebhookEndpoint
# ---------------------------------------------------------------------------


@admin.register(WebhookEndpoint)
class WebhookEndpointAdmin(admin.ModelAdmin):
    list_display = [
        "name",
        "url",
        "events_display",
        "is_active",
        "consecutive_failures",
        "disabled_at",
        "created_at",
    ]
    list_filter = ["is_active", "created_at"]
    search_fields = ["name", "url"]
    readonly_fields = [
        "consecutive_failures",
        "disabled_at",
        "created_by",
        "created_at",
        "updated_at",
    ]
    fieldsets = [
        (
            "Endpoint",
            {
                "fields": ["name", "url", "is_active", "created_by"],
            },
        ),
        (
            "Subscription",
            {
                "fields": ["events"],
                "description": (
                    'List of event type strings, e.g. ["student.enrolled", "payment.received"]. '
                    'Use ["*"] to subscribe to all events.'
                ),
            },
        ),
        (
            "Signing secret",
            {
                "fields": ["secret"],
                "description": "Share this with the endpoint owner so they can verify our signatures.",
            },
        ),
        (
            "Health",
            {
                "fields": ["consecutive_failures", "disabled_at"],
                "classes": ["collapse"],
            },
        ),
        (
            "Timestamps",
            {
                "fields": ["created_at", "updated_at"],
                "classes": ["collapse"],
            },
        ),
    ]
    actions = ["re_enable_endpoints"]

    @admin.display(description="Events")
    def events_display(self, obj):
        return ", ".join(obj.events) if obj.events else "—"

    @admin.action(description="Re-enable selected endpoints (clear failure state)")
    def re_enable_endpoints(self, request, queryset):
        updated = queryset.update(
            is_active=True,
            consecutive_failures=0,
            disabled_at=None,
        )
        self.message_user(request, f"{updated} endpoint(s) re-enabled.")


# ---------------------------------------------------------------------------
# WebhookDelivery
# ---------------------------------------------------------------------------


@admin.register(WebhookDelivery)
class WebhookDeliveryAdmin(admin.ModelAdmin):
    list_display = [
        "event_type",
        "endpoint_name",
        "status",
        "attempts",
        "response_status_code",
        "last_attempted_at",
        "next_retry_at",
        "created_at",
    ]
    list_filter = ["status", "event_type", "created_at"]
    search_fields = ["event_type", "endpoint__name"]
    readonly_fields = [
        "endpoint",
        "event_type",
        "idempotency_key",
        "payload",
        "status",
        "attempts",
        "next_retry_at",
        "response_status_code",
        "response_body",
        "last_attempted_at",
        "delivered_at",
        "error_detail",
        "created_at",
        "updated_at",
    ]
    actions = ["retry_deliveries"]

    @admin.display(description="Endpoint")
    def endpoint_name(self, obj):
        return obj.endpoint.name

    @admin.action(description="Retry selected failed deliveries")
    def retry_deliveries(self, request, queryset):
        from django_q.tasks import async_task

        retried = 0
        for delivery in queryset.filter(
            status__in=[WebhookDelivery.Status.FAILED, WebhookDelivery.Status.ABANDONED]
        ):
            WebhookDelivery.objects.filter(pk=delivery.pk).update(
                status=WebhookDelivery.Status.PENDING,
                next_retry_at=timezone.now(),
            )
            async_task(
                "apps.api.webhooks.deliver_webhook",
                delivery.pk,
                task_name=f"webhook:admin-retry:{delivery.pk}",
            )
            retried += 1
        self.message_user(request, f"{retried} delivery/deliveries queued for retry.")

    def has_add_permission(self, request):
        return False  # Deliveries are created by the system only.

    def has_delete_permission(self, request, obj=None):
        return False  # Delivery log is append-only.
