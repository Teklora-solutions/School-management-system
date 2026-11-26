"""
apps/api/apps.py
----------------
App config for the external integration API.
"""

from django.apps import AppConfig


class ApiConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.api"
    verbose_name = "External API"

    def ready(self):
        # Register OpenAPI schema extension for our custom authenticator.
        import apps.api.schema  # noqa: F401
