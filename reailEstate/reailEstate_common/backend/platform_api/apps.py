from django.apps import AppConfig


class PlatformApiConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "platform_api"
    verbose_name = "Real Estate Platform"

    def ready(self):
        from .observability import configure_observability

        configure_observability()
