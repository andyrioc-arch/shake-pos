"""Carga presupuesto de ventas y gastos de ejemplo para mayo 2025."""
from decimal import Decimal
from django.core.management.base import BaseCommand
from presupuesto.models import PresupuestoVenta, PresupuestoGasto

ANIO, MES = 2025, 5
META_VENTAS = "60000.00"
# Las categorías ahora viven en contabilidad.CategoriaGasto; se usan sus claves.
META_GASTOS = {
    "insumos": "5000.00",
    "renta": "4500.00",
    "mercadotecnia": "3000.00",
    "sueldos": "14400.00",
    "servicios": "1500.00",
}


class Command(BaseCommand):
    help = "Carga presupuesto de ventas y gastos de ejemplo."

    def add_arguments(self, parser):
        parser.add_argument("--reset", action="store_true")

    def handle(self, *args, **opts):
        if opts["reset"]:
            PresupuestoVenta.objects.all().delete()
            PresupuestoGasto.objects.all().delete()
            self.stdout.write("Presupuesto previo borrado.")

        PresupuestoVenta.objects.update_or_create(
            anio=ANIO, mes=MES, defaults=dict(monto=Decimal(META_VENTAS))
        )
        for cat, monto in META_GASTOS.items():
            PresupuestoGasto.objects.update_or_create(
                anio=ANIO, mes=MES, categoria=cat,
                defaults=dict(monto=Decimal(monto)),
            )
        self.stdout.write(self.style.SUCCESS(
            f"✔ Presupuesto cargado: ventas ${META_VENTAS} y "
            f"{len(META_GASTOS)} categorías de gasto para {MES}/{ANIO}."
        ))
