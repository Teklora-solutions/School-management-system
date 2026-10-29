from django.apps import AppConfig


class PublicSiteConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.public_site"
    label = "public_site"
