"""apps/assistant/apps.py"""

from django.apps import AppConfig


class AssistantConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.assistant"
    verbose_name = "AI Assistant"

    def ready(self):
        from . import checks  # noqa: F401  (registers assistant.E001)
