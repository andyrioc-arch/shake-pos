"""Crea los movimientos contables faltantes para ventas, compras y gastos."""
from django.core.management.base import BaseCommand

from inventario.models import Venta, Compra
from contabilidad.models import Movimiento
from contabilidad import posting


class Command(BaseCommand):
    help = ("Sincroniza ventas, compras y gastos con el libro de movimientos. "
            "Reescribe asientos al esquema sin cuentas puente.")

    def handle(self, *args, **opts):
        posting.crear_catalogo()
        nv = nc = ng = 0
        for v in Venta.objects.all():
            posting.sincronizar_venta(v); nv += 1
        for c in Compra.objects.all():
            posting.sincronizar_compra(c); nc += 1
        for m in Movimiento.objects.filter(tipo=Movimiento.Tipo.GASTO):
            posting.sincronizar_movimiento(m); ng += 1
        self.stdout.write(self.style.SUCCESS(
            f"✔ Sincronizados {nv} ventas, {nc} compras y {ng} gastos."))
