from decimal import Decimal
from datetime import date
from django.test import TestCase
from contabilidad.models import Cuenta, Asiento, MovimientoContable
from contabilidad import posting


class FifoCogsTests(TestCase):
    """Costo de Ventas por FIFO (IAS 2): consume capas de la más antigua primero."""

    def setUp(self):
        from inventario.models import Ingrediente, Receta, RecetaIngrediente
        from contabilidad.models import Movimiento
        posting.crear_catalogo()
        self.Movimiento = Movimiento
        i = Ingrediente.objects.create(
            nombre="Leche", categoria="liquido", unidad_compra="litro",
            cantidad_por_unidad=Decimal("1000"), unidad_receta="ml",
            costo_unidad_compra=Decimal("20"))
        self.r = Receta.objects.create(
            nombre="Shake", perfil="x", precio_venta=Decimal("116"))  # sub 100 + IVA 16
        RecetaIngrediente.objects.create(receta=self.r, ingrediente=i, cantidad=Decimal("200"))
        self.i = i

    def _compra(self, dia, costo):
        from inventario.models import Compra
        c = Compra.objects.create(fecha=date(2026, 7, dia), ingrediente=self.i,
                                  cantidad=Decimal("1"), costo_unitario=Decimal(costo))
        posting.marcar_facturado(self.Movimiento.objects.get(compra=c), True, date(2026, 7, dia))
        return c

    def _venta(self, dia, cant):
        from inventario.models import Venta
        v = Venta.objects.create(fecha=date(2026, 7, dia), receta=self.r, cantidad=cant)
        posting.marcar_facturado(self.Movimiento.objects.get(venta=v), True, date(2026, 7, dia))
        return v

    def test_fifo_cruza_capas_y_cuadra(self):
        self._compra(1, "20")   # capa 1: 1000ml @ 0.02
        self._compra(2, "30")   # capa 2: 1000ml @ 0.03
        vA = self._venta(5, 4)  # 800ml -> 16.00 (toda capa 1)
        vB = self._venta(6, 2)  # 400ml -> 200*0.02 + 200*0.03 = 10.00 (cruza)

        cogs = posting.fifo_cogs()
        self.assertEqual(cogs[vA.id], Decimal("16.00"))
        self.assertEqual(cogs[vB.id], Decimal("10.00"))

        er = posting.estado_resultados(2026, 7)
        self.assertEqual(er["total_ingresos"], Decimal("600.00"))     # 6 * 100 neto
        self.assertEqual(er["total_costo_ventas"], Decimal("26.00"))
        self.assertEqual(er["utilidad_bruta"], Decimal("574.00"))

        bg = posting.balance_general(2026, 7)
        self.assertTrue(bg["cuadra"])
        inv = [a for a in bg["activos"] if a["nombre"] == "Inventario"]
        self.assertEqual(inv[0]["monto"], Decimal("24.00"))           # 50 comprado - 26 vendido
        self.assertTrue(posting.balanza_comprobacion(2026, 7)["cuadra"])


class CortesiaTests(TestCase):
    """Una cortesía es gratis pero consume inventario; su costo va a Cortesías."""

    def test_cortesia_gratis_consume_inventario_y_gasto_promocion(self):
        from inventario.models import (
            Ingrediente, Receta, RecetaIngrediente, Venta, Compra)
        from contabilidad.models import Movimiento
        posting.crear_catalogo()
        i = Ingrediente.objects.create(
            nombre="Leche", categoria="liquido", unidad_compra="litro",
            cantidad_por_unidad=Decimal("1000"), unidad_receta="ml",
            costo_unidad_compra=Decimal("20"))
        r = Receta.objects.create(nombre="Shake", precio_venta=Decimal("100"))
        RecetaIngrediente.objects.create(receta=r, ingrediente=i, cantidad=Decimal("200"))
        c = Compra.objects.create(fecha=date(2026, 8, 1), ingrediente=i,
                                  cantidad=Decimal("1"), costo_unitario=Decimal("20"))
        posting.marcar_facturado(Movimiento.objects.get(compra=c), True, date(2026, 8, 1))

        v = Venta.objects.create(fecha=date(2026, 8, 5), receta=r,
                                 cantidad=2, es_cortesia=True)
        self.assertEqual(v.ingreso, Decimal("0"))            # gratis
        i.refresh_from_db()
        self.assertEqual(i.stock_disponible, Decimal("600"))  # 1000 - 400ml consumidos

        posting.marcar_facturado(Movimiento.objects.get(venta=v), True, date(2026, 8, 5))
        er = posting.estado_resultados(2026, 8)
        self.assertEqual(er["total_ingresos"], Decimal("0"))
        self.assertEqual(er["total_costo_ventas"], Decimal("0"))
        gastos = {g["nombre"]: g["monto"] for g in er["gastos"]}
        self.assertEqual(gastos["Cortesías y promociones"], Decimal("8"))  # 400ml * 0.02
        self.assertTrue(posting.balance_general(2026, 8)["cuadra"])


class CatalogoTests(TestCase):
    def test_crear_catalogo_idempotente(self):
        posting.crear_catalogo()
        n1 = Cuenta.objects.count()
        posting.crear_catalogo()  # otra vez
        n2 = Cuenta.objects.count()
        self.assertEqual(n1, n2)
        self.assertEqual(n1, 16)

    def test_naturaleza_cuentas(self):
        posting.crear_catalogo()
        self.assertTrue(Cuenta.objects.get(codigo="101").es_deudora)  # Activo
        self.assertTrue(Cuenta.objects.get(codigo="501").es_deudora)  # Gasto
        self.assertFalse(Cuenta.objects.get(codigo="401").es_deudora) # Ingreso
        self.assertFalse(Cuenta.objects.get(codigo="201").es_deudora) # Pasivo


class GastoOperativoTests(TestCase):
    """Gastos operativos (sueldos, renta…) por el libro de movimientos."""

    def setUp(self):
        posting.crear_catalogo()

    def test_gasto_no_facturado_fuera_de_resultados(self):
        posting.registrar_gasto(date(2025, 5, 1), "renta", Decimal("4500"))
        r = posting.estado_resultados(2025, 5)
        self.assertEqual(r["total_gastos"], Decimal("0"))     # aún no facturado
        self.assertTrue(posting.balanza_comprobacion(2025, 5)["cuadra"])

    def test_gasto_facturado_entra_a_resultados_en_su_cuenta(self):
        from contabilidad.models import Movimiento
        mov = posting.registrar_gasto(date(2025, 5, 1), "renta", Decimal("4500"), "Local")
        posting.marcar_facturado(mov, True, date(2025, 5, 1))
        mov.refresh_from_db()
        r = posting.estado_resultados(2025, 5)
        self.assertEqual(r["total_gastos"], Decimal("4500"))  # gasto operativo
        self.assertEqual(r["total_costo_ventas"], Decimal("0"))
        cuentas = [m.cuenta.codigo for m in mov.asiento_reconocimiento.movimientos.all()]
        self.assertIn("502", cuentas)   # Renta
        self.assertTrue(posting.balance_general(2025, 5)["cuadra"])


class ReportesTests(TestCase):
    def setUp(self):
        posting.crear_catalogo()
        mov = posting.registrar_gasto(date(2025, 5, 1), "sueldos", Decimal("400.00"))
        posting.marcar_facturado(mov, True, date(2025, 5, 1))

    def test_balanza_cuadra(self):
        b = posting.balanza_comprobacion()
        self.assertTrue(b["cuadra"])
        self.assertEqual(b["total_debe"], b["total_haber"])

    def test_estado_resultados(self):
        r = posting.estado_resultados()
        self.assertEqual(r["total_gastos"], Decimal("400.00"))   # gasto operativo
        self.assertEqual(r["utilidad"], Decimal("-400.00"))

    def test_balance_cuadra(self):
        bg = posting.balance_general()
        self.assertTrue(bg["cuadra"])
        self.assertEqual(bg["total_activo"], bg["total_pasivo_capital"])


class ValidacionAsientoTests(TestCase):
    """Verifica la detección de asientos descuadrados e inválidos."""

    def setUp(self):
        posting.crear_catalogo()
        self.caja = Cuenta.objects.get(codigo="101")

    def test_movimiento_debe_y_haber_se_rechaza(self):
        from django.core.exceptions import ValidationError
        a = Asiento.objects.create(fecha=date(2025, 5, 1), concepto="X")
        m = MovimientoContable(
            asiento=a, cuenta=self.caja,
            debe=Decimal("100"), haber=Decimal("50"),
        )
        with self.assertRaises(ValidationError):
            m.full_clean()

    def test_movimiento_sin_monto_se_rechaza(self):
        from django.core.exceptions import ValidationError
        a = Asiento.objects.create(fecha=date(2025, 5, 1), concepto="X")
        m = MovimientoContable(asiento=a, cuenta=self.caja)
        with self.assertRaises(ValidationError):
            m.full_clean()

    def test_asiento_descuadrado_se_detecta(self):
        a = Asiento.objects.create(fecha=date(2025, 5, 1), concepto="Malo")
        MovimientoContable.objects.create(
            asiento=a, cuenta=self.caja, debe=Decimal("100")
        )
        self.assertFalse(a.cuadrado)

    def test_asiento_cuadrado_se_detecta(self):
        ventas = Cuenta.objects.get(codigo="401")
        a = Asiento.objects.create(fecha=date(2025, 5, 1), concepto="Bueno")
        MovimientoContable.objects.create(
            asiento=a, cuenta=self.caja, debe=Decimal("100")
        )
        MovimientoContable.objects.create(
            asiento=a, cuenta=ventas, haber=Decimal("100")
        )
        self.assertTrue(a.cuadrado)


class ReportesViewTests(TestCase):
    def setUp(self):
        from django.contrib.auth.models import User
        posting.crear_catalogo()
        self.user = User.objects.create_superuser("admin", "a@a.com", "pass")
        self.client.force_login(self.user)
        posting.registrar_gasto(date(2025, 5, 1), "renta", Decimal("4500"))

    def test_reportes_carga(self):
        resp = self.client.get("/contabilidad/")
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Balanza de comprobación")
        self.assertContains(resp, "Estado de resultados")
        self.assertContains(resp, "Balance general")

    def test_reportes_requiere_login(self):
        self.client.logout()
        resp = self.client.get("/contabilidad/")
        self.assertEqual(resp.status_code, 302)

    def test_admin_movimiento_carga(self):
        resp = self.client.get("/admin/contabilidad/movimiento/")
        self.assertEqual(resp.status_code, 200)

    def test_admin_asiento_carga(self):
        resp = self.client.get("/admin/contabilidad/asiento/")
        self.assertEqual(resp.status_code, 200)
