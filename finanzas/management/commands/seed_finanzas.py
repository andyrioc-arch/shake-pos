"""Carga los costos fijos del negocio (renta, sueldos, mercadotecnia)."""
from decimal import Decimal
from django.core.management.base import BaseCommand
from finanzas.models import CostoFijo

# concepto -> (categoria, monto_mensual)
COSTOS_FIJOS = {
    "Renta del local": (CostoFijo.Categoria.RENTA, "4500.00"),
    "Sueldos":         (CostoFijo.Categoria.SUELDOS, "14400.00"),
    "Mercadotecnia":   (CostoFijo.Categoria.MERCADOTECNIA, "3500.00"),
}


class Command(BaseCommand):
    help = "Carga los costos fijos mensuales del negocio."

    def add_arguments(self, parser):
        parser.add_argument("--reset", action="store_true",
                            help="Borra los costos fijos existentes antes de cargar.")

    def handle(self, *args, **opts):
        if opts["reset"]:
            CostoFijo.objects.all().delete()
            self.stdout.write("Costos fijos previos borrados.")
        for concepto, (cat, monto) in COSTOS_FIJOS.items():
            CostoFijo.objects.update_or_create(
                concepto=concepto,
                defaults=dict(categoria=cat, monto_mensual=Decimal(monto)),
            )
        total = CostoFijo.total_mensual()
        self.stdout.write(self.style.SUCCESS(
            f"✔ {len(COSTOS_FIJOS)} costos fijos cargados. "
            f"Total mensual: ${total:,.2f}"
        ))
