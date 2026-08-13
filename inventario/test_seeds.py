"""Tests de los comandos seed: que corran sin error y carguen datos."""
from django.test import TestCase
from django.core.management import call_command
from io import StringIO

from inventario.models import Ingrediente, Receta, Venta
from finanzas.models import CostoFijo
from contabilidad.models import Cuenta, Movimiento, Asiento
from contabilidad.posting import CATALOGO
from presupuesto.models import PresupuestoVenta


class SeedCommandsTests(TestCase):
    def _run(self, *args):
        call_command(*args, stdout=StringIO())

    def test_seed_inventario(self):
        self._run("seed", "--reset")
        self.assertGreater(Ingrediente.objects.count(), 0)
        self.assertEqual(Receta.objects.count(), 6)
        self.assertGreater(Venta.objects.count(), 0)

    def test_seed_finanzas(self):
        self._run("seed_finanzas", "--reset")
        self.assertEqual(CostoFijo.total_mensual(), 22400)

    def test_seed_contabilidad_genera_asientos_que_cuadran(self):
        self._run("seed_contabilidad", "--reset")
        self.assertEqual(Cuenta.objects.count(), len(CATALOGO))
        self.assertGreater(
            Movimiento.objects.filter(tipo=Movimiento.Tipo.GASTO).count(), 0)
        # Todos los asientos generados deben cuadrar
        self.assertTrue(all(a.cuadrado for a in Asiento.objects.all()))

    def test_seed_presupuesto(self):
        self._run("seed_presupuesto", "--reset")
        self.assertGreater(PresupuestoVenta.objects.count(), 0)

    def test_seeds_no_dejan_asientos_huerfanos(self):
        self._run("seed_contabilidad", "--reset")
        # Todo asiento automático debe estar ligado a un movimiento del libro.
        usados = set()
        for m in Movimiento.objects.all():
            usados.add(m.asiento_flujo_id)
            usados.add(m.asiento_reconocimiento_id)
        huerfanos = (Asiento.objects.filter(automatico=True)
                     .exclude(id__in=usados).count())
        self.assertEqual(huerfanos, 0)
