from decimal import Decimal
from datetime import date
from django.test import TestCase
from finanzas.models import CostoFijo, InversionInicial, MovimientoEfectivo
from finanzas import calculos
from inventario.models import Ingrediente, Receta, RecetaIngrediente, Venta, Compra


class CostoFijoTests(TestCase):
    def test_total_mensual(self):
        CostoFijo.objects.create(concepto="Renta", monto_mensual=Decimal("4500"))
        CostoFijo.objects.create(concepto="Sueldos", monto_mensual=Decimal("14400"))
        self.assertEqual(CostoFijo.total_mensual(), Decimal("18900"))

    def test_inactivo_no_cuenta(self):
        CostoFijo.objects.create(concepto="Renta", monto_mensual=Decimal("4500"))
        CostoFijo.objects.create(
            concepto="Viejo", monto_mensual=Decimal("1000"), activo=False
        )
        self.assertEqual(CostoFijo.total_mensual(), Decimal("4500"))


class InversionTests(TestCase):
    def test_total(self):
        InversionInicial.objects.create(concepto="Licuadora", monto=Decimal("5000"))
        InversionInicial.objects.create(concepto="Congelador", monto=Decimal("8000"))
        self.assertEqual(InversionInicial.total(), Decimal("13000"))


class PuntoEquilibrioTests(TestCase):
    def setUp(self):
        ing = Ingrediente.objects.create(
            nombre="Leche", unidad_compra="litro", cantidad_por_unidad=1000,
            unidad_receta="ml", costo_unidad_compra=Decimal("30.00"),
        )
        self.rec = Receta.objects.create(
            nombre="Shake", precio_venta=Decimal("100.00")
        )
        RecetaIngrediente.objects.create(
            receta=self.rec, ingrediente=ing, cantidad=500  # 500*0.03=15 costo
        )
        CostoFijo.objects.create(concepto="Renta", monto_mensual=Decimal("8500"))

    def test_margen_contribucion_sin_ventas(self):
        # Sin ventas: usa promedio de recetas activas: 100 - 15 = 85
        mc, unidades = calculos.margen_contribucion_promedio()
        self.assertEqual(mc, Decimal("85.00"))
        self.assertEqual(unidades, 0)

    def test_punto_equilibrio(self):
        # 8500 / 85 = 100 shakes/mes
        pe = calculos.punto_equilibrio()
        self.assertEqual(pe["unidades_mes"], Decimal("100"))

    def test_margen_con_ventas(self):
        Venta.objects.create(fecha=date(2025, 5, 1), receta=self.rec, cantidad=4)
        mc, unidades = calculos.margen_contribucion_promedio()
        self.assertEqual(mc, Decimal("85.00"))
        self.assertEqual(unidades, 4)


class FlujoRecuperacionTests(TestCase):
    def setUp(self):
        ing = Ingrediente.objects.create(
            nombre="Leche", unidad_compra="litro", cantidad_por_unidad=1000,
            unidad_receta="ml", costo_unidad_compra=Decimal("30.00"),
        )
        self.rec = Receta.objects.create(
            nombre="Shake", precio_venta=Decimal("100.00")
        )
        RecetaIngrediente.objects.create(
            receta=self.rec, ingrediente=ing, cantidad=100  # costo 3
        )
        CostoFijo.objects.create(concepto="Renta", monto_mensual=Decimal("1000"))
        InversionInicial.objects.create(concepto="Equipo", monto=Decimal("5000"))

    def test_recuperacion_se_detecta(self):
        # Mes con muchas ventas: 200 shakes * (100-3) - 1000 fijos = 18400 > 5000
        Venta.objects.create(fecha=date(2025, 5, 1), receta=self.rec, cantidad=200)
        fm = calculos.flujo_mensual()
        self.assertIsNotNone(fm["recuperado_en"])
        self.assertEqual(fm["recuperado_en"], (2025, 5))


class FlujoMultiMesTests(TestCase):
    """Flujo de efectivo a través de varios meses con compras y extras."""

    def setUp(self):
        ing = Ingrediente.objects.create(
            nombre="Leche", unidad_compra="litro", cantidad_por_unidad=1000,
            unidad_receta="ml", costo_unidad_compra=Decimal("30.00"),
        )
        self.rec = Receta.objects.create(
            nombre="Shake", precio_venta=Decimal("100.00")
        )
        RecetaIngrediente.objects.create(
            receta=self.rec, ingrediente=ing, cantidad=100  # costo variable 3
        )
        CostoFijo.objects.create(concepto="Renta", monto_mensual=Decimal("1000"))

    def test_filas_ordenadas_por_periodo(self):
        Venta.objects.create(fecha=date(2025, 4, 1), receta=self.rec, cantidad=10)
        Venta.objects.create(fecha=date(2025, 5, 1), receta=self.rec, cantidad=20)
        Venta.objects.create(fecha=date(2025, 6, 1), receta=self.rec, cantidad=15)
        fm = calculos.flujo_mensual()
        periodos = [(f["anio"], f["mes"]) for f in fm["filas"]]
        self.assertEqual(periodos, [(2025, 4), (2025, 5), (2025, 6)])

    def test_ganancia_operativa_por_mes(self):
        # Abril: 10 ventas * 100 = 1000 ingreso; costo var 10*3=30; fijos 1000
        # ganancia_op = 1000 - 30 - 1000 = -30
        Venta.objects.create(fecha=date(2025, 4, 1), receta=self.rec, cantidad=10)
        fm = calculos.flujo_mensual()
        abril = fm["filas"][0]
        self.assertEqual(abril["ingresos"], Decimal("1000"))
        self.assertEqual(abril["costo_variable"], Decimal("30"))
        self.assertEqual(abril["ganancia_operativa"], Decimal("-30"))

    def test_acumulado_se_suma_entre_meses(self):
        # Mes 1: ganancia_op X; Mes 2: ganancia_op Y; acumulado mes 2 = X+Y
        Venta.objects.create(fecha=date(2025, 4, 1), receta=self.rec, cantidad=50)
        Venta.objects.create(fecha=date(2025, 5, 1), receta=self.rec, cantidad=50)
        fm = calculos.flujo_mensual()
        # cada mes: 50*100 - 50*3 - 1000 = 5000 - 150 - 1000 = 3850
        self.assertEqual(fm["filas"][0]["ganancia_acumulada"], Decimal("3850"))
        self.assertEqual(fm["filas"][1]["ganancia_acumulada"], Decimal("7700"))

    def test_flujo_efectivo_incluye_compras(self):
        Venta.objects.create(fecha=date(2025, 5, 1), receta=self.rec, cantidad=20)
        ing = Ingrediente.objects.get(nombre="Leche")
        Compra.objects.create(
            fecha=date(2025, 5, 2), ingrediente=ing,
            cantidad=5, costo_unitario=Decimal("30"),  # total 150
        )
        fm = calculos.flujo_mensual()
        mayo = fm["filas"][0]
        # flujo = ingresos(2000) - compras(150) - fijos(1000) + extras(0) = 850
        self.assertEqual(mayo["compras"], Decimal("150"))
        self.assertEqual(mayo["flujo_efectivo"], Decimal("850"))

    def test_movimiento_efectivo_extra_afecta_flujo(self):
        Venta.objects.create(fecha=date(2025, 5, 1), receta=self.rec, cantidad=20)
        MovimientoEfectivo.objects.create(
            fecha=date(2025, 5, 3), tipo=MovimientoEfectivo.Tipo.SALIDA,
            concepto="Reparación", monto=Decimal("500"),
        )
        MovimientoEfectivo.objects.create(
            fecha=date(2025, 5, 4), tipo=MovimientoEfectivo.Tipo.ENTRADA,
            concepto="Capital", monto=Decimal("2000"),
        )
        fm = calculos.flujo_mensual()
        mayo = fm["filas"][0]
        # extras = -500 + 2000 = 1500
        self.assertEqual(mayo["extras"], Decimal("1500"))
        # flujo = 2000 - 0 compras - 1000 fijos + 1500 = 2500
        self.assertEqual(mayo["flujo_efectivo"], Decimal("2500"))

    def test_recuperacion_en_mes_correcto(self):
        InversionInicial.objects.create(concepto="Equipo", monto=Decimal("7000"))
        # Mes 1: ganancia_op 3850 (no recupera); Mes 2: acum 7700 (recupera)
        Venta.objects.create(fecha=date(2025, 4, 1), receta=self.rec, cantidad=50)
        Venta.objects.create(fecha=date(2025, 5, 1), receta=self.rec, cantidad=50)
        fm = calculos.flujo_mensual()
        self.assertEqual(fm["recuperado_en"], (2025, 5))

    def test_sin_inversion_no_hay_recuperacion(self):
        Venta.objects.create(fecha=date(2025, 5, 1), receta=self.rec, cantidad=50)
        fm = calculos.flujo_mensual()
        self.assertIsNone(fm["recuperado_en"])

    def test_movimiento_con_signo(self):
        salida = MovimientoEfectivo.objects.create(
            fecha=date(2025, 5, 1), tipo=MovimientoEfectivo.Tipo.SALIDA,
            concepto="X", monto=Decimal("300"),
        )
        entrada = MovimientoEfectivo.objects.create(
            fecha=date(2025, 5, 1), tipo=MovimientoEfectivo.Tipo.ENTRADA,
            concepto="Y", monto=Decimal("300"),
        )
        self.assertEqual(salida.monto_con_signo, Decimal("-300"))
        self.assertEqual(entrada.monto_con_signo, Decimal("300"))


class PanelFinancieroViewTests(TestCase):
    def setUp(self):
        from django.contrib.auth.models import User
        self.user = User.objects.create_superuser("admin", "a@a.com", "pass")
        self.client.force_login(self.user)
        CostoFijo.objects.create(concepto="Renta", monto_mensual=Decimal("4500"))

    def test_panel_carga(self):
        resp = self.client.get("/finanzas/")
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Panel financiero")

    def test_panel_requiere_login(self):
        self.client.logout()
        resp = self.client.get("/finanzas/")
        self.assertEqual(resp.status_code, 302)  # redirige a login
