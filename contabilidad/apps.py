from django.apps import AppConfig


class ContabilidadConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "contabilidad"
    verbose_name = "Contabilidad"

    def ready(self):
        from . import signals  # noqa: F401
