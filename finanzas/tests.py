from decimal import Decimal
from datetime import date
from django.test import TestCase
from finanzas.models import CostoFijo, InversionInicial, MovimientoEfectivo
from finanzas import calculos
from inventario.models import (
    Extra, Ingrediente, Receta, RecetaIngrediente, Venta, VentaExtra, Compra)


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

    def test_margen_cuenta_el_add_on_por_los_dos_lados(self):
        """Corrección de P4: el add-on entra con su cargo Y con su costo.

        El baseline de P0 congelaba 85.00 porque el cálculo comparaba
        `precio_efectivo` (sin el cargo del add-on) contra `receta.costo_receta`
        (sin su costo): los dos lados se excluían y el add-on era invisible.
        Ahora se restan ingreso y costo de la línea completa, así que un add-on
        que cobra 12 y cuesta 4 sube el margen 8 sobre las 4 unidades: +2.00.

        Lo que este test sigue cazando es el desbalance: si alguien mete el
        costo del add-on sin su cargo, el margen baja y el punto de equilibrio
        sube sin que nadie lo pida.
        """
        cafe = Ingrediente.objects.create(
            nombre="Café", unidad_compra="kg", cantidad_por_unidad=1000,
            unidad_receta="g", costo_unidad_compra=Decimal("400.00"))
        shot = Extra.objects.create(
            nombre="Shot", ingrediente=cafe, cantidad=Decimal("10"),
            cargo=Decimal("12.00"))          # cuesta 10*0.40 = 4.00
        venta = Venta.objects.create(
            fecha=date(2025, 5, 1), receta=self.rec, cantidad=4)
        VentaExtra.objects.create(venta=venta, extra=shot, cantidad=1)

        self.assertEqual(venta.cargo_extras, Decimal("12.00"))
        self.assertEqual(venta.costo_extras, Decimal("4.00"))
        mc, unidades = calculos.margen_contribucion_promedio()
        # (4*100 + 12 de cargo − 4*15 de receta − 4 de café) / 4 = 87.00
        self.assertEqual(mc, Decimal("87.00"))
        self.assertEqual(unidades, 4)

    def test_punto_equilibrio_baseline_con_ventas(self):
        # Congela la cadena completa: margen 85 sobre costos fijos de 8500.
        Venta.objects.create(fecha=date(2025, 5, 1), receta=self.rec, cantidad=4)
        pe = calculos.punto_equilibrio()
        self.assertEqual(pe["costos_fijos"], Decimal("8500"))
        self.assertEqual(pe["margen_contribucion"], Decimal("85.00"))
        self.assertEqual(pe["unidades_mes"], Decimal("100"))


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


class CosteoRealEnLosPanelesTests(TestCase):
    """P4: los paneles leen el costo guardado, no el estimado del catálogo."""

    def setUp(self):
        self.leche = Ingrediente.objects.create(
            nombre="Leche", unidad_compra="litro",
            cantidad_por_unidad=Decimal("1000"), unidad_receta="ml",
            costo_unidad_compra=Decimal("999"))   # catálogo absurdo a propósito
        self.rec = Receta.objects.create(
            nombre="Shake", precio_venta=Decimal("100"))
        RecetaIngrediente.objects.create(
            receta=self.rec, ingrediente=self.leche, cantidad=Decimal("200"))
        CostoFijo.objects.create(concepto="Renta", monto_mensual=Decimal("1000"))

    def _compra(self, dia, litros, monto):
        return Compra.objects.create(
            fecha=date(2026, 8, dia), ingrediente=self.leche,
            cantidad=Decimal(str(litros)), monto_total=Decimal(monto))

    def test_el_margen_usa_el_costo_fifo_y_no_el_catalogo(self):
        self._compra(1, 1, "20")               # 200 ml por venta → $4
        Venta.objects.create(fecha=date(2026, 8, 2), receta=self.rec, cantidad=1)

        mc, unidades = calculos.margen_contribucion_promedio()
        self.assertEqual(mc, Decimal("96.00"))  # con el catálogo daría −99.80
        self.assertEqual(unidades, 1)

    def test_el_flujo_usa_el_costo_fifo(self):
        self._compra(1, 1, "20")
        Venta.objects.create(fecha=date(2026, 8, 2), receta=self.rec, cantidad=1)

        fila = calculos.flujo_mensual()["filas"][0]
        self.assertEqual(fila["costo_variable"], Decimal("4.00"))

    def test_la_cortesia_no_entra_al_margen_ni_al_costo_variable(self):
        self._compra(1, 1, "20")
        Venta.objects.create(fecha=date(2026, 8, 2), receta=self.rec, cantidad=1)
        Venta.objects.create(fecha=date(2026, 8, 2), receta=self.rec, cantidad=1,
                             es_cortesia=True)

        # La cortesía consumió inventario, pero su costo es mercadotecnia: si
        # entrara aquí, el margen del producto se vería la mitad de bueno.
        mc, unidades = calculos.margen_contribucion_promedio()
        self.assertEqual(mc, Decimal("96.00"))
        self.assertEqual(unidades, 1)

        fila = calculos.flujo_mensual()["filas"][0]
        self.assertEqual(fila["costo_variable"], Decimal("4.00"))
        self.assertEqual(fila["ingresos"], Decimal("100"))
        # Pero el insumo de la cortesía sí se gastó: va en su propia columna y
        # se resta igual, o el retorno de la inversión se ve mejor de lo que es.
        self.assertEqual(fila["cortesias"], Decimal("4.00"))
        self.assertEqual(fila["ganancia_operativa"], Decimal("-908.00"))


class SinConsultasPorVentaTests(TestCase):
    """El costo de los paneles no puede crecer con el número de ventas.

    Perder un `prefetch_related` no rompe ningún número: solo agrega una
    consulta por venta. Es la clase de regresión que nadie nota hasta que el
    panel tarda en producción, así que se fija aquí.
    """

    def setUp(self):
        self.leche = Ingrediente.objects.create(
            nombre="Leche", unidad_compra="litro",
            cantidad_por_unidad=Decimal("1000"), unidad_receta="ml",
            costo_unidad_compra=Decimal("30"))
        self.rec = Receta.objects.create(
            nombre="Shake", precio_venta=Decimal("100"))
        RecetaIngrediente.objects.create(
            receta=self.rec, ingrediente=self.leche, cantidad=Decimal("200"))
        CostoFijo.objects.create(concepto="Renta", monto_mensual=Decimal("1000"))

    def _vender(self, cuantas):
        for _ in range(cuantas):
            Venta.objects.create(fecha=date(2026, 8, 2), receta=self.rec,
                                 cantidad=1)

    def _consultas(self, fn):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext
        with CaptureQueriesContext(connection) as cap:
            fn()
        return len(cap)

    def test_el_margen_no_consulta_una_vez_por_venta(self):
        self._vender(2)
        con_dos = self._consultas(calculos.margen_contribucion_promedio)
        self._vender(6)          # cuatro veces más ventas
        con_ocho = self._consultas(calculos.margen_contribucion_promedio)
        self.assertEqual(con_dos, con_ocho)

    def test_el_flujo_no_consulta_una_vez_por_venta(self):
        self._vender(2)
        con_dos = self._consultas(calculos.flujo_mensual)
        self._vender(6)
        con_ocho = self._consultas(calculos.flujo_mensual)
        self.assertEqual(con_dos, con_ocho)

    def test_el_catalogo_tampoco_consulta_una_vez_por_ingrediente(self):
        """La otra rama de `costo_receta`: quien NO precarga.

        El catálogo y el admin recorren recetas sin prefetch. Si `costo_receta`
        se quedara con `.all()` a secas para servir al panel, aquí pagaría una
        consulta por ingrediente de cada receta.
        """
        for n in range(4):
            ing = Ingrediente.objects.create(
                nombre=f"Extra {n}", unidad_compra="kg",
                cantidad_por_unidad=Decimal("1000"), unidad_receta="g",
                costo_unidad_compra=Decimal("100"))
            RecetaIngrediente.objects.create(
                receta=self.rec, ingrediente=ing, cantidad=Decimal("10"))

        def leer():
            for r in Receta.objects.all():
                r.costo_receta

        # 5 ingredientes en la receta y aun así: 1 receta + 1 join.
        self.assertEqual(self._consultas(leer), 2)
