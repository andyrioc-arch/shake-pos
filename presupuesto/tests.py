from decimal import Decimal
from datetime import date
from django.test import TestCase
from presupuesto.models import PresupuestoVenta, PresupuestoGasto
from presupuesto import comparativo
from inventario.models import Ingrediente, Receta, RecetaIngrediente, Venta
from contabilidad import posting


class ComparativoVentasTests(TestCase):
    def setUp(self):
        self.rec = Receta.objects.create(
            nombre="Shake", precio_venta=Decimal("100.00")
        )
        PresupuestoVenta.objects.create(anio=2025, mes=5, monto=Decimal("10000"))

    def test_comparativo_ventas(self):
        Venta.objects.create(fecha=date(2025, 5, 1), receta=self.rec, cantidad=30)
        filas = comparativo.comparativo_ventas()
        fila = next(f for f in filas if "Mayo 2025" in f["periodo"])
        self.assertEqual(fila["meta"], Decimal("10000"))
        self.assertEqual(fila["real"], Decimal("3000"))  # 30*100
        self.assertEqual(fila["diferencia"], Decimal("-7000"))
        self.assertEqual(fila["pct"], Decimal("30"))

    def test_meta_cero_pct_none(self):
        # Ventas sin presupuesto: meta 0, pct None
        Venta.objects.create(fecha=date(2025, 6, 1), receta=self.rec, cantidad=5)
        filas = comparativo.comparativo_ventas()
        fila = next(f for f in filas if "Junio 2025" in f["periodo"])
        self.assertIsNone(fila["pct"])


class ComparativoGastosTests(TestCase):
    def setUp(self):
        posting.crear_catalogo()
        PresupuestoGasto.objects.create(
            anio=2025, mes=5, categoria="insumos",
            monto=Decimal("5000"),
        )

    def test_comparativo_gastos(self):
        posting.registrar_gasto(date(2025, 5, 1), "insumos", Decimal("3500"))
        filas = comparativo.comparativo_gastos()
        fila = next(f for f in filas if "Insumos" in f["categoria"]
                    and "Mayo 2025" in f["periodo"])
        self.assertEqual(fila["meta"], Decimal("5000"))
        self.assertEqual(fila["real"], Decimal("3500"))
        self.assertEqual(fila["diferencia"], Decimal("-1500"))  # gastó menos: bueno


class TotalesYMultiMesTests(TestCase):
    def setUp(self):
        self.rec = Receta.objects.create(
            nombre="Shake", precio_venta=Decimal("100.00")
        )

    def test_totales_ventas_suman_varios_meses(self):
        PresupuestoVenta.objects.create(anio=2025, mes=4, monto=Decimal("8000"))
        PresupuestoVenta.objects.create(anio=2025, mes=5, monto=Decimal("10000"))
        Venta.objects.create(fecha=date(2025, 4, 1), receta=self.rec, cantidad=30)
        Venta.objects.create(fecha=date(2025, 5, 1), receta=self.rec, cantidad=50)
        r = comparativo.resumen()
        self.assertEqual(r["tot_meta_ventas"], Decimal("18000"))
        self.assertEqual(r["tot_real_ventas"], Decimal("8000"))  # 30*100 + 50*100

    def test_gasto_real_sin_presupuesto_aparece(self):
        # Gasto real sin meta definida: debe aparecer con meta 0
        posting.crear_catalogo()
        posting.registrar_gasto(date(2025, 5, 1), "servicios", Decimal("800"))
        filas = comparativo.comparativo_gastos()
        fila = next(f for f in filas if "Servicios" in f["categoria"])
        self.assertEqual(fila["meta"], Decimal("0"))
        self.assertEqual(fila["real"], Decimal("800"))
        self.assertIsNone(fila["pct"])  # meta 0 -> pct None

    def test_presupuesto_sin_gasto_real_aparece(self):
        # Meta definida pero sin gasto real: real = 0
        PresupuestoGasto.objects.create(
            anio=2025, mes=5, categoria="renta",
            monto=Decimal("4500"),
        )
        filas = comparativo.comparativo_gastos()
        fila = next(f for f in filas if "Renta" in f["categoria"])
        self.assertEqual(fila["meta"], Decimal("4500"))
        self.assertEqual(fila["real"], Decimal("0"))
        self.assertEqual(fila["diferencia"], Decimal("-4500"))  # gastó menos: bueno

    def test_gasto_registrado_aparece_como_real(self):
        posting.crear_catalogo()
        PresupuestoGasto.objects.create(
            anio=2025, mes=5, categoria="insumos", monto=Decimal("1000"),
        )
        posting.registrar_gasto(date(2025, 5, 1), "insumos", Decimal("1000"))
        filas = comparativo.comparativo_gastos()
        fila = next(f for f in filas if "Insumos" in f["categoria"])
        self.assertEqual(fila["real"], Decimal("1000"))

    def test_resumen_sin_datos_no_explota(self):
        r = comparativo.resumen()
        self.assertEqual(r["tot_meta_ventas"], Decimal("0"))
        self.assertEqual(r["tot_real_ventas"], Decimal("0"))
        self.assertEqual(r["ventas"], [])


class PanelPresupuestoViewTests(TestCase):
    def setUp(self):
        from django.contrib.auth.models import User
        self.user = User.objects.create_superuser("admin", "a@a.com", "pass")
        self.client.force_login(self.user)
        PresupuestoVenta.objects.create(anio=2025, mes=5, monto=Decimal("60000"))

    def test_panel_carga(self):
        resp = self.client.get("/presupuesto/")
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Presupuesto vs. real")

    def test_panel_requiere_login(self):
        self.client.logout()
        resp = self.client.get("/presupuesto/")
        self.assertEqual(resp.status_code, 302)
