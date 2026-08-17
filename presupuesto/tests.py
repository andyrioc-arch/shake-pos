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


class PeriodoFueraDeRangoTests(TestCase):
    """Un periodo imposible no entra ni por la URL ni por el formulario.

    Por la URL revienta la pantalla y ya. Por el formulario es peor: se GUARDA
    —los validadores del modelo solo corren en full_clean(), y aquí se escribe
    con update_or_create— y a partir de ahí la portada revienta para todos, sin
    querystring de por medio, hasta que alguien borre la fila a mano.
    """

    MALOS = [
        ("mes cero", 2026, 0),
        ("mes trece", 2026, 13),
        ("mes negativo", 2026, -1),
        ("anio cero", 0, 6),
        ("anio de cinco cifras", 99999, 6),
        ("anio de veinte cifras", 10 ** 20, 6),
        ("mes de diez cifras", 2026, 10 ** 9),
    ]

    def setUp(self):
        from django.contrib.auth.models import User
        posting.crear_catalogo()
        self.user = User.objects.create_superuser("admin", "a@a.com", "pass")
        self.client.force_login(self.user)

    def test_el_panel_aguanta_la_url(self):
        for caso, anio, mes in self.MALOS:
            with self.subTest(caso=caso):
                resp = self.client.get(f"/presupuesto/?anio={anio}&mes={mes}")
                self.assertEqual(resp.status_code, 200)

    def test_el_formulario_no_guarda_un_periodo_imposible(self):
        from django.urls import reverse
        for caso, anio, mes in self.MALOS:
            with self.subTest(caso=caso):
                resp = self.client.post(
                    reverse("presupuesto_venta_guardar"),
                    {"anio": anio, "mes": mes, "monto": "1000"})
                self.assertEqual(resp.status_code, 302)
        self.assertEqual(PresupuestoVenta.objects.count(), 0)

    def test_la_portada_sobrevive_a_una_fila_ya_envenenada(self):
        """Validar la entrada no limpia lo que se guardó antes de validarla."""
        PresupuestoVenta.objects.create(
            anio=2026, mes=13, monto=Decimal("1000"))
        PresupuestoGasto.objects.create(
            anio=2026, mes=13, categoria="insumos", monto=Decimal("500"))

        self.assertEqual(self.client.get("/").status_code, 200)
        self.assertEqual(self.client.get("/presupuesto/").status_code, 200)

    def test_un_periodo_bueno_si_se_guarda(self):
        from django.urls import reverse
        self.client.post(reverse("presupuesto_venta_guardar"),
                         {"anio": 2026, "mes": 8, "monto": "1000"})
        self.assertEqual(
            PresupuestoVenta.objects.get(anio=2026, mes=8).monto,
            Decimal("1000"))

    def test_una_venta_vieja_sigue_contando(self):
        """Acotar el periodo es para lo que se CAPTURA, no para lo que ya pasó.

        Una venta con fecha rara sigue siendo dinero cobrado: si desaparece del
        comparativo, desaparece también de los totales que lee la portada.
        """
        rec = Receta.objects.create(nombre="Shake", precio_venta=Decimal("100.00"))
        Venta.objects.create(fecha=date(2019, 5, 1), receta=rec, cantidad=3)

        periodos = [f["periodo"] for f in comparativo.comparativo_ventas()]
        self.assertIn("Mayo 2019", periodos)
        self.assertEqual(comparativo.resumen()["tot_real_ventas"],
                         Decimal("300.00"))


class EntradasImposiblesTests(TestCase):
    """Lo que un POST fabricado puede meter por los formularios."""

    def setUp(self):
        from django.contrib.auth.models import User
        posting.crear_catalogo()
        self.user = User.objects.create_superuser("admin", "a@a.com", "pass")
        self.client.force_login(self.user)

    def test_ningun_monto_imposible_se_guarda(self):
        from django.urls import reverse
        for malo in ("NaN", "Infinity", "-Infinity", "abc"):
            with self.subTest(monto=malo):
                self.client.post(reverse("presupuesto_venta_guardar"), {
                    "anio": 2026, "mes": 8, "monto": malo})
                self.assertEqual(PresupuestoVenta.objects.count(), 0)

    def test_un_pk_que_no_es_numero_no_truena(self):
        """Sin int(), armar la consulta lanza ValueError y sale un 500."""
        from django.urls import reverse
        resp = self.client.post(reverse("presupuesto_gasto_eliminar"), {
            "anio": 2026, "mes": 8, "pk": "abc"})
        self.assertEqual(resp.status_code, 302)

    def test_se_sigue_borrando_por_pk_bueno(self):
        from django.urls import reverse
        g = PresupuestoGasto.objects.create(
            anio=2026, mes=8, categoria="insumos", monto=Decimal("100"))
        self.client.post(reverse("presupuesto_gasto_eliminar"), {
            "anio": 2026, "mes": 8, "pk": g.pk})
        self.assertEqual(PresupuestoGasto.objects.count(), 0)
