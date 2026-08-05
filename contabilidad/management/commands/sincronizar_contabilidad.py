"""Crea los movimientos contables faltantes para ventas y compras existentes."""
from django.core.management.base import BaseCommand

from inventario.models import Venta, Compra
from contabilidad import posting


class Command(BaseCommand):
    help = "Sincroniza ventas y compras de inventario con el libro de movimientos."

    def handle(self, *args, **opts):
        posting.crear_catalogo()
        nv = nc = 0
        for v in Venta.objects.all():
            posting.sincronizar_venta(v); nv += 1
        for c in Compra.objects.all():
            posting.sincronizar_compra(c); nc += 1
        self.stdout.write(self.style.SUCCESS(
            f"✔ Sincronizados {nv} ventas y {nc} compras."))
