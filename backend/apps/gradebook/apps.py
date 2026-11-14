from django.apps import AppConfig


class GradebookConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.gradebook"
    label = "gradebook"
    verbose_name = "Gradebook"
