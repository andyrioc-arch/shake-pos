"""Crea el catálogo de cuentas y algunos gastos operativos de ejemplo."""
from datetime import date
from decimal import Decimal
from django.core.management.base import BaseCommand
from contabilidad.models import Asiento, Cuenta, Movimiento
from contabilidad import posting

# (fecha, categoría, monto, concepto)
GASTOS = [
    (date(2025, 5, 1), "renta", "4500.00", "Renta del local"),
    (date(2025, 5, 3), "mercadotecnia", "3500.00", "Campaña digital"),
    (date(2025, 5, 15), "sueldos", "8000.00", "Nómina quincena"),
]


class Command(BaseCommand):
    help = "Crea el catálogo de cuentas y gastos operativos de ejemplo."

    def add_arguments(self, parser):
        parser.add_argument("--reset", action="store_true",
                            help="Borra gastos y asientos antes de cargar.")

    def handle(self, *args, **opts):
        if opts["reset"]:
            Movimiento.objects.filter(tipo=Movimiento.Tipo.GASTO).delete()
            Asiento.objects.filter(automatico=True).delete()
            self.stdout.write("Gastos y asientos previos borrados.")

        posting.crear_catalogo()
        self.stdout.write(f"✔ Catálogo de cuentas: {Cuenta.objects.count()} cuentas.")

        for fecha, categoria, monto, concepto in GASTOS:
            mov = posting.registrar_gasto(fecha, categoria, Decimal(monto), concepto)
            posting.marcar_facturado(mov, True, fecha)
        self.stdout.write(f"✔ {len(GASTOS)} gastos operativos (facturados).")
        self.stdout.write(self.style.SUCCESS("Contabilidad inicializada."))
