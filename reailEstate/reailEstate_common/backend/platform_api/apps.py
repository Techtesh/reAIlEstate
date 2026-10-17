from django.apps import AppConfig


class PlatformApiConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "platform_api"
    verbose_name = "Real Estate Platform"

    def ready(self):
        from .floor_plan_vision import _configured_profiles
        from .observability import configure_observability

        _configured_profiles()
        configure_observability()
