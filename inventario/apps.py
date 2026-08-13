from django.apps import AppConfig


class InventarioConfig(AppConfig):
    name = 'inventario'

    def ready(self):
        from . import signals  # noqa: F401
