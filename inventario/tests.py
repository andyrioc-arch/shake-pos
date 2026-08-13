from decimal import Decimal
from datetime import date
from django.test import TestCase
from inventario.alarmas import alarmas_margen
from inventario.models import (
    ConfiguracionAlarmas, Ingrediente, Nota, Receta, RecetaIngrediente, Compra,
    Venta, Extra, VentaSustitucion, VentaExtra,
)


class IngredienteCostoTests(TestCase):
    def setUp(self):
        self.leche = Ingrediente.objects.create(
            nombre="Leche", unidad_compra="litro", cantidad_por_unidad=1000,
            unidad_receta="ml", costo_unidad_compra=Decimal("30.00"),
        )

    def test_costo_unidad_receta(self):
        # 30 / 1000 = 0.03 por ml
        self.assertEqual(self.leche.costo_unidad_receta, Decimal("0.03"))

    def test_costo_unidad_receta_cero_no_explota(self):
        x = Ingrediente.objects.create(
            nombre="X", unidad_compra="kg", cantidad_por_unidad=Decimal("0.0001"),
            unidad_receta="g", costo_unidad_compra=Decimal("0"),
        )
        self.assertEqual(x.costo_unidad_receta, Decimal("0"))


class RecetaCostoTests(TestCase):
    def setUp(self):
        self.leche = Ingrediente.objects.create(
            nombre="Leche", unidad_compra="litro", cantidad_por_unidad=1000,
            unidad_receta="ml", costo_unidad_compra=Decimal("30.00"),
        )
        self.proteina = Ingrediente.objects.create(
            nombre="Proteína", unidad_compra="kg", cantidad_por_unidad=1000,
            unidad_receta="g", costo_unidad_compra=Decimal("450.00"),
        )
        self.rec = Receta.objects.create(
            nombre="Shake Test", precio_venta=Decimal("95.00")
        )
        RecetaIngrediente.objects.create(
            receta=self.rec, ingrediente=self.leche, cantidad=200  # 200ml*0.03=6
        )
        RecetaIngrediente.objects.create(
            receta=self.rec, ingrediente=self.proteina, cantidad=30  # 30g*0.45=13.5
        )

    def test_costo_receta(self):
        self.assertEqual(self.rec.costo_receta, Decimal("19.50"))

    def test_margen(self):
        # (95 - 19.5) / 95 = 0.794...
        self.assertAlmostEqual(float(self.rec.margen), 0.7947, places=3)

    def test_ganancia_unitaria(self):
        self.assertEqual(self.rec.ganancia_unitaria, Decimal("75.50"))


class InventarioStockTests(TestCase):
    def setUp(self):
        self.leche = Ingrediente.objects.create(
            nombre="Leche", unidad_compra="litro", cantidad_por_unidad=1000,
            unidad_receta="ml", costo_unidad_compra=Decimal("30.00"),
        )
        self.rec = Receta.objects.create(
            nombre="Shake", precio_venta=Decimal("95.00")
        )
        RecetaIngrediente.objects.create(
            receta=self.rec, ingrediente=self.leche, cantidad=200
        )

    def test_total_comprado(self):
        Compra.objects.create(
            fecha=date(2025, 5, 1), ingrediente=self.leche,
            cantidad=2, costo_unitario=Decimal("30.00"),  # 2 litros = 2000ml
        )
        self.assertEqual(self.leche.total_comprado, Decimal("2000"))

    def test_el_total_sale_del_monto_pagado(self):
        compra = Compra.objects.create(
            fecha=date(2025, 5, 1), ingrediente=self.leche,
            cantidad=Decimal("1.5"), monto_total=Decimal("47.33"),
        )
        # Ni se recalcula ni se redondea: es lo que se pagó.
        self.assertEqual(compra.total, Decimal("47.33"))

    def test_una_compra_vieja_sin_monto_cae_al_unitario(self):
        # Filas anteriores a la migración 0008: el unitario sigue mandando.
        compra = Compra.objects.create(
            fecha=date(2025, 5, 1), ingrediente=self.leche,
            cantidad=2, costo_unitario=Decimal("30.00"),
        )
        self.assertIsNone(compra.monto_total)
        self.assertEqual(compra.total, Decimal("60.00"))

    def test_el_monto_pagado_le_gana_al_unitario(self):
        compra = Compra.objects.create(
            fecha=date(2025, 5, 1), ingrediente=self.leche,
            cantidad=Decimal("3"), costo_unitario=Decimal("15.77"),
            monto_total=Decimal("47.33"),      # 3 × 15.77 = 47.31, no 47.33
        )
        self.assertEqual(compra.total, Decimal("47.33"))

    def test_consumo_y_stock(self):
        Compra.objects.create(
            fecha=date(2025, 5, 1), ingrediente=self.leche,
            cantidad=2, costo_unitario=Decimal("30.00"),  # 2000ml
        )
        Venta.objects.create(fecha=date(2025, 5, 2), receta=self.rec, cantidad=3)
        # consumo = 200ml * 3 = 600ml ; stock = 2000 - 600 = 1400
        self.assertEqual(self.leche.total_consumido, Decimal("600"))
        self.assertEqual(self.leche.stock_disponible, Decimal("1400"))

    def test_minimo_para_cinco(self):
        # 200ml por receta * 5 = 1000
        self.assertEqual(self.leche.minimo_para_cinco, Decimal("1000"))

    def test_alerta_faltante(self):
        # Sin compras: stock 0 < 1000 mínimo -> falta
        self.assertTrue(self.leche.hay_faltante)
        self.assertEqual(self.leche.faltante, Decimal("1000"))


class VentaTests(TestCase):
    def setUp(self):
        self.rec = Receta.objects.create(
            nombre="Shake", precio_venta=Decimal("95.00")
        )

    def test_precio_unitario_se_autollena(self):
        v = Venta.objects.create(fecha=date(2025, 5, 1), receta=self.rec, cantidad=2)
        self.assertEqual(v.precio_unitario, Decimal("95.00"))

    def test_ingreso(self):
        v = Venta.objects.create(fecha=date(2025, 5, 1), receta=self.rec, cantidad=2)
        self.assertEqual(v.ingreso, Decimal("190.00"))

    def test_precio_manual_se_respeta(self):
        v = Venta.objects.create(
            fecha=date(2025, 5, 1), receta=self.rec, cantidad=1,
            precio_unitario=Decimal("80.00"),
        )
        self.assertEqual(v.ingreso, Decimal("80.00"))


class AdminViewTests(TestCase):
    def setUp(self):
        from django.contrib.auth.models import User
        self.user = User.objects.create_superuser("admin", "a@a.com", "pass")
        self.client.force_login(self.user)
        self.ing = Ingrediente.objects.create(
            nombre="Leche", unidad_compra="litro", cantidad_por_unidad=1000,
            unidad_receta="ml", costo_unidad_compra=Decimal("30.00"),
        )
        self.rec = Receta.objects.create(
            nombre="Shake", precio_venta=Decimal("95.00")
        )
        RecetaIngrediente.objects.create(
            receta=self.rec, ingrediente=self.ing, cantidad=200
        )

    def _comprar(self, cantidad, costo_total):
        from django.urls import reverse
        self.client.post(reverse("inventario_compra_agregar"), {
            "ingrediente": self.ing.pk, "cantidad": cantidad,
            "costo_total": costo_total, "fecha": date(2026, 8, 12).isoformat(),
        })
        return Compra.objects.latest("id")

    def test_la_caja_guarda_el_monto_exacto_que_se_pago(self):
        # Antes se dividía entre la cantidad y se redondeaba: 47.33 / 1.5 daba
        # 31.55, y al multiplicar de vuelta salían 47.325. Dos centavos perdidos.
        compra = self._comprar("1.5", "47.33")
        self.assertEqual(compra.monto_total, Decimal("47.33"))
        self.assertEqual(compra.total, Decimal("47.33"))

    def test_una_compra_no_toca_el_costo_del_catalogo(self):
        # El costo del catálogo es un estimado para calcular márgenes; el costo
        # real de una venta sale de las compras por FIFO.
        antes = self.ing.costo_unidad_compra
        self._comprar("2", "500.00")
        self.ing.refresh_from_db()
        self.assertEqual(self.ing.costo_unidad_compra, antes)

    def test_admin_ingrediente_carga(self):
        resp = self.client.get("/admin/inventario/ingrediente/")
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Leche")

    def test_admin_receta_carga(self):
        resp = self.client.get("/admin/inventario/receta/")
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Shake")

    def test_admin_receta_detalle_con_inline(self):
        resp = self.client.get(f"/admin/inventario/receta/{self.rec.pk}/change/")
        self.assertEqual(resp.status_code, 200)

    def test_admin_compra_carga(self):
        resp = self.client.get("/admin/inventario/compra/")
        self.assertEqual(resp.status_code, 200)

    def test_admin_venta_carga(self):
        resp = self.client.get("/admin/inventario/venta/")
        self.assertEqual(resp.status_code, 200)


class SustitucionExtraTests(TestCase):
    def setUp(self):
        # Ingredientes
        self.avena = Ingrediente.objects.create(
            nombre="Leche avena", unidad_compra="litro", cantidad_por_unidad=1000,
            unidad_receta="ml", costo_unidad_compra=Decimal("30.00"),  # 0.03/ml
        )
        self.almendra = Ingrediente.objects.create(
            nombre="Leche almendra", unidad_compra="litro", cantidad_por_unidad=1000,
            unidad_receta="ml", costo_unidad_compra=Decimal("55.00"),  # 0.055/ml
        )
        self.espresso = Ingrediente.objects.create(
            nombre="Espresso", unidad_compra="litro", cantidad_por_unidad=1000,
            unidad_receta="ml", costo_unidad_compra=Decimal("80.00"),  # 0.08/ml
        )
        # Receta: 200ml avena -> costo 6.00
        self.rec = Receta.objects.create(nombre="Shake", precio_venta=Decimal("90.00"))
        RecetaIngrediente.objects.create(
            receta=self.rec, ingrediente=self.avena, cantidad=200
        )
        # Extra espresso: 30ml -> costo 2.40, cargo 12
        self.extra_esp = Extra.objects.create(
            nombre="Espresso", ingrediente=self.espresso,
            cantidad=30, cargo=Decimal("12.00"),
        )

    def test_costo_sin_personalizar(self):
        v = Venta.objects.create(fecha=date(2025, 5, 1), receta=self.rec, cantidad=1)
        self.assertEqual(v.costo_unitario_real, Decimal("6.00"))
        self.assertEqual(v.precio_efectivo, Decimal("90.00"))

    def test_sustitucion_recalcula_costo(self):
        # Cambiar avena (0.03) por almendra (0.055) en 200ml:
        # delta = 200*0.055 - 200*0.03 = 11 - 6 = +5
        v = Venta.objects.create(fecha=date(2025, 5, 1), receta=self.rec, cantidad=1)
        VentaSustitucion.objects.create(
            venta=v, ingrediente_original=self.avena, ingrediente_nuevo=self.almendra
        )
        self.assertEqual(v.costo_unitario_real, Decimal("11.00"))  # 6 + 5
        # El precio NO cambia por sustitución
        self.assertEqual(v.precio_efectivo, Decimal("90.00"))

    def test_extra_suma_costo_y_precio(self):
        v = Venta.objects.create(fecha=date(2025, 5, 1), receta=self.rec, cantidad=1)
        VentaExtra.objects.create(venta=v, extra=self.extra_esp, cantidad=1)
        # El add-on no entra en el costo/precio UNITARIO (es por línea):
        self.assertEqual(v.costo_unitario_real, Decimal("6.00"))
        self.assertEqual(v.precio_efectivo, Decimal("90.00"))
        # ...sino una vez en los totales: costo 6 + 2.40 = 8.40, ingreso 90 + 12 = 102
        self.assertEqual(v.costo_total, Decimal("8.40"))
        self.assertEqual(v.ingreso, Decimal("102.00"))

    def test_sustitucion_y_extra_juntos_con_cantidad(self):
        v = Venta.objects.create(fecha=date(2025, 5, 1), receta=self.rec, cantidad=2)
        VentaSustitucion.objects.create(
            venta=v, ingrediente_original=self.avena, ingrediente_nuevo=self.almendra
        )
        VentaExtra.objects.create(venta=v, extra=self.extra_esp, cantidad=1)
        # costo = 2 shakes x 11 (sub) + 2.40 (add-on una vez) = 24.40
        self.assertEqual(v.costo_total, Decimal("24.40"))
        # ingreso = 2 shakes x 90 + 12 (add-on una vez) = 192
        self.assertEqual(v.ingreso, Decimal("192.00"))

    def test_inventario_sustituido_no_consume_original(self):
        # Compramos avena y almendra
        Compra.objects.create(fecha=date(2025, 5, 1), ingrediente=self.avena,
                              cantidad=1, costo_unitario=Decimal("30"))  # 1000ml
        Compra.objects.create(fecha=date(2025, 5, 1), ingrediente=self.almendra,
                              cantidad=1, costo_unitario=Decimal("55"))  # 1000ml
        v = Venta.objects.create(fecha=date(2025, 5, 2), receta=self.rec, cantidad=1)
        VentaSustitucion.objects.create(
            venta=v, ingrediente_original=self.avena, ingrediente_nuevo=self.almendra
        )
        # La avena NO se consumió (fue sustituida)
        self.assertEqual(self.avena.total_consumido, Decimal("0"))
        # La almendra SÍ se consumió 200ml
        self.assertEqual(self.almendra.total_consumido, Decimal("200"))

    def test_inventario_extra_consume_ingrediente(self):
        Compra.objects.create(fecha=date(2025, 5, 1), ingrediente=self.espresso,
                              cantidad=1, costo_unitario=Decimal("80"))  # 1000ml
        v = Venta.objects.create(fecha=date(2025, 5, 2), receta=self.rec, cantidad=3)
        VentaExtra.objects.create(venta=v, extra=self.extra_esp, cantidad=1)
        # El add-on es por línea: 30ml por extra * 1 extra = 30ml (no x3 shakes)
        self.assertEqual(self.espresso.total_consumido, Decimal("30"))

    def test_extra_costo_property(self):
        # 30ml * 0.08 = 2.40
        self.assertEqual(self.extra_esp.costo, Decimal("2.40"))


class CicloCompletoCajaTests(TestCase):
    """El recorrido de punta a punta, por HTTP, como lo hace Andy.

    Es el escenario que se verificó a mano en el navegador: una venta sin
    respaldo queda fuera del Estado de Resultados, y en el instante en que se
    captura la compra que le faltaba —desde la caja, sin apretar nada más— se
    reconoce sola y el reporte la muestra con su costo.
    """

    def setUp(self):
        from django.contrib.auth.models import User
        from contabilidad import posting
        posting.crear_catalogo()
        self.user = User.objects.create_superuser("andy", "a@a.com", "pass")
        self.client.force_login(self.user)
        self.ing = Ingrediente.objects.create(
            nombre="Leche", unidad_compra="litro", cantidad_por_unidad=1000,
            unidad_receta="ml", costo_unidad_compra=Decimal("99.00"))
        self.rec = Receta.objects.create(
            nombre="Shake", precio_venta=Decimal("90.00"))
        RecetaIngrediente.objects.create(
            receta=self.rec, ingrediente=self.ing, cantidad=200)

    def _reportes(self, anio=2026, mes=8):
        from django.urls import reverse
        return self.client.get(
            f"{reverse('reportes_contables')}?anio={anio}&mes={mes}")

    def test_de_venta_diferida_a_reconocida_capturando_la_compra(self):
        from contabilidad import posting
        from contabilidad.models import Movimiento
        from django.urls import reverse

        venta = Venta.objects.create(
            fecha=date(2026, 8, 6), receta=self.rec, cantidad=3)   # 600 ml

        # 1. Sin compras: el dinero entró a caja, pero el reporte no la cuenta.
        self.assertEqual(
            posting.estado_resultados(2026, 8)["total_ingresos"], Decimal("0"))
        resp = self._reportes()
        self.assertContains(resp, "Falta costo")
        self.assertNotContains(resp, "✓ Reconocido")

        # 2. Se captura la compra desde la caja, fechada antes de la venta.
        self.client.post(reverse("inventario_compra_agregar"), {
            "ingrediente": self.ing.pk, "cantidad": "1.5",
            "costo_total": "47.33", "fecha": date(2026, 8, 1).isoformat(),
        })

        # 3. La venta se costeó y se reconoció sola.
        venta.refresh_from_db()
        self.assertFalse(venta.costo_incompleto)
        self.assertEqual(venta.costo_fifo, Decimal("18.93"))   # 600 × 47.33/1500
        self.assertIsNotNone(
            Movimiento.objects.get(venta=venta).asiento_reconocimiento)

        er = posting.estado_resultados(2026, 8)
        self.assertEqual(er["total_ingresos"], Decimal("270"))
        self.assertEqual(er["total_costo_ventas"], Decimal("18.93"))
        self.assertTrue(posting.balance_general(2026, 8)["cuadra"])
        self.assertTrue(posting.balanza_comprobacion(2026, 8)["cuadra"])

        # 4. Y el libro lo dice en pantalla.
        resp = self._reportes()
        self.assertContains(resp, "✓ Reconocido")
        self.assertNotContains(resp, "Falta costo")

    def test_una_compra_posterior_a_la_venta_no_la_cuesta(self):
        """FIFO respeta la fecha: no se surte un shake con hielo del año que viene."""
        from django.urls import reverse
        venta = Venta.objects.create(
            fecha=date(2026, 8, 6), receta=self.rec, cantidad=3)

        self.client.post(reverse("inventario_compra_agregar"), {
            "ingrediente": self.ing.pk, "cantidad": "1.5",
            "costo_total": "47.33", "fecha": date(2026, 8, 20).isoformat(),
        })

        venta.refresh_from_db()
        self.assertTrue(venta.costo_incompleto)
        self.assertContains(self._reportes(), "Falta costo")


class CortesiasEnLosAgregadosTests(TestCase):
    """Una cortesía se produjo, pero no se cobró. Cada número decide cuál
    de las dos cosas le importa, y ninguno puede quedarse con las dos."""

    def setUp(self):
        self.ing = Ingrediente.objects.create(
            nombre="Leche", unidad_compra="litro", cantidad_por_unidad=1000,
            unidad_receta="ml", costo_unidad_compra=Decimal("30.00"))
        self.rec = Receta.objects.create(
            nombre="Shake", precio_venta=Decimal("100.00"))
        RecetaIngrediente.objects.create(
            receta=self.rec, ingrediente=self.ing, cantidad=200)
        Compra.objects.create(
            fecha=date(2026, 8, 1), ingrediente=self.ing,
            cantidad=Decimal("1"), monto_total=Decimal("30.00"))
        Venta.objects.create(fecha=date(2026, 8, 5), receta=self.rec, cantidad=2)
        Venta.objects.create(fecha=date(2026, 8, 6), receta=self.rec,
                             cantidad=1, es_cortesia=True)

    def test_las_unidades_cuentan_lo_regalado_y_el_margen_no(self):
        from finanzas.calculos import margen_contribucion_promedio

        # Salieron tres shakes: los tres consumieron leche.
        self.assertEqual(self.rec.unidades_vendidas, 3)
        self.assertEqual(self.rec.unidades_regaladas, 1)
        self.assertEqual(self.rec.unidades_cobradas, 2)

        # El margen solo mira los dos que se cobraron. Si contara el regalado
        # —ingreso cero, costo real— el promedio se hundiría sin que el precio
        # ni el costo del producto se hubieran movido.
        _, unidades = margen_contribucion_promedio()
        self.assertEqual(unidades, 2)

    def test_el_costo_del_regalo_no_es_costo_variable_de_ventas(self):
        """Sale por mercadotecnia, en su propia columna, no del margen."""
        from finanzas.calculos import flujo_mensual

        fila = flujo_mensual()["filas"][0]

        self.assertEqual(fila["cortesias"], Decimal("6.00"))    # 200 ml × 0.03
        self.assertEqual(fila["costo_variable"], Decimal("12.00"))
        # Pero se sigue restando: el insumo se gastó.
        self.assertEqual(
            fila["ganancia_operativa"],
            fila["ingresos"] - fila["costo_variable"] - fila["cortesias"]
            - fila["costos_fijos"])

    def test_el_panel_separa_lo_cobrado_de_lo_regalado(self):
        from django.contrib.auth.models import User
        from django.urls import reverse
        User.objects.create_superuser("andy", "a@a.com", "pass")
        self.client.login(username="andy", password="pass")

        html = self.client.get(reverse("panel_inventario")).content.decode()

        self.assertIn("Cobrados", html)
        self.assertIn("Regalados", html)

    def test_el_admin_desglosa_la_cortesia(self):
        from inventario.admin import RecetaAdmin
        col = RecetaAdmin(Receta, None).vendidos_col(self.rec)
        self.assertEqual(col, "3 (1 de cortesía)")

    def test_sin_cortesias_el_admin_no_agrega_ruido(self):
        from inventario.admin import RecetaAdmin
        limpia = Receta.objects.create(
            nombre="Otro", precio_venta=Decimal("90.00"))
        self.assertEqual(RecetaAdmin(Receta, None).vendidos_col(limpia), 0)


class NotaPdfTests(TestCase):
    """La nota en PDF: pública como la nota, y sin depender de la de pantalla."""

    def setUp(self):
        self.ing = Ingrediente.objects.create(
            nombre="Leche", unidad_compra="litro", cantidad_por_unidad=1000,
            unidad_receta="ml", costo_unidad_compra=Decimal("30.00"))
        self.rec = Receta.objects.create(
            nombre="Shake", emoji="🍫", precio_venta=Decimal("100.00"))
        RecetaIngrediente.objects.create(
            receta=self.rec, ingrediente=self.ing, cantidad=200)
        self.nota = Nota.objects.create(
            fecha=date(2026, 8, 5), total=Decimal("200.00"),
            pago_con=Decimal("500.00"), cambio=Decimal("300.00"))
        Venta.objects.create(fecha=date(2026, 8, 5), receta=self.rec,
                             cantidad=2, nota=self.nota)

    def _pdf(self):
        from django.urls import reverse
        return self.client.get(reverse("nota_pdf", args=[self.nota.token]))

    def test_devuelve_un_pdf_de_verdad(self):
        resp = self._pdf()

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp["Content-Type"], "application/pdf")
        self.assertTrue(resp.content.startswith(b"%PDF-"))
        self.assertIn(self.nota.folio, resp["Content-Disposition"])

    def test_no_pide_sesion(self):
        """Quien tiene el token ya puede ver la nota; pedir contraseña aquí
        solo impediría que el cliente se lleve su comprobante."""
        self.client.logout()
        self.assertEqual(self._pdf().status_code, 200)

    def test_una_nota_que_no_existe_da_404(self):
        import uuid
        from django.urls import reverse
        resp = self.client.get(reverse("nota_pdf", args=[uuid.uuid4()]))
        self.assertEqual(resp.status_code, 404)

    def test_el_emoji_del_producto_no_rompe_el_dibujo(self):
        """Las fuentes base del PDF no tienen emoji: se quitan en vez de
        salir como cuadros negros."""
        from inventario.pdf import _limpio
        self.assertEqual(_limpio("🍫 Afterparty Shake"), "Afterparty Shake")
        self.assertTrue(self._pdf().content.startswith(b"%PDF-"))

    def test_los_simbolos_con_significado_se_traducen_en_vez_de_perderse(self):
        """Una sustitución sin su flecha queda como «Plátano  Fresa», que ya
        no dice cuál entró y cuál salió."""
        from inventario.pdf import _limpio
        self.assertEqual(_limpio("Plátano → Fresa"), "Plátano -> Fresa")
        self.assertEqual(_limpio("2× Creatina"), "2x Creatina")

    def test_la_nota_de_pantalla_ofrece_el_pdf(self):
        resp = self.client.get(self.nota.get_absolute_url())
        self.assertContains(resp, "Guardar PDF")


class CostoUltimaCompraTests(TestCase):
    """Junto al aproximado del catálogo, lo que costaría a precios reales."""

    def setUp(self):
        self.leche = Ingrediente.objects.create(
            nombre="Leche", unidad_compra="litro", cantidad_por_unidad=1000,
            unidad_receta="ml", costo_unidad_compra=Decimal("30.00"))
        self.cacao = Ingrediente.objects.create(
            nombre="Cacao", unidad_compra="kg", cantidad_por_unidad=1000,
            unidad_receta="g", costo_unidad_compra=Decimal("400.00"))
        self.rec = Receta.objects.create(
            nombre="Shake", precio_venta=Decimal("100.00"))
        RecetaIngrediente.objects.create(
            receta=self.rec, ingrediente=self.leche, cantidad=200)
        RecetaIngrediente.objects.create(
            receta=self.rec, ingrediente=self.cacao, cantidad=50)

    def test_usa_el_precio_de_la_compra_mas_reciente(self):
        Compra.objects.create(fecha=date(2026, 7, 1), ingrediente=self.leche,
                              cantidad=Decimal("1"), monto_total=Decimal("30"))
        Compra.objects.create(fecha=date(2026, 8, 1), ingrediente=self.leche,
                              cantidad=Decimal("1"), monto_total=Decimal("50"))
        Compra.objects.create(fecha=date(2026, 8, 1), ingrediente=self.cacao,
                              cantidad=Decimal("1"), monto_total=Decimal("600"))

        # 200 ml × 0.05 + 50 g × 0.60 = 10 + 30
        self.assertEqual(self.rec.costo_ultima_compra, Decimal("40.00"))
        # El catálogo sigue diciendo lo suyo: 200×0.03 + 50×0.40 = 26
        self.assertEqual(self.rec.costo_receta, Decimal("26.00"))

    def test_si_falta_una_compra_no_se_mezcla_con_el_catalogo(self):
        """Media receta a precios reales y la otra media a catálogo no es
        ninguna de las dos cosas."""
        Compra.objects.create(fecha=date(2026, 8, 1), ingrediente=self.leche,
                              cantidad=Decimal("1"), monto_total=Decimal("50"))

        self.assertIsNone(self.rec.costo_ultima_compra)

    def test_un_ingrediente_sin_compras_no_inventa_precio(self):
        self.assertIsNone(self.leche.costo_unidad_ultima_compra)


class RepartoDeVentasTests(TestCase):
    """La gráfica de qué se vende más reparte lo COBRADO."""

    def setUp(self):
        from django.contrib.auth.models import User
        self.rec_a = Receta.objects.create(
            nombre="Uno", precio_venta=Decimal("100.00"))
        self.rec_b = Receta.objects.create(
            nombre="Dos", precio_venta=Decimal("100.00"))
        Receta.objects.create(nombre="Nunca", precio_venta=Decimal("100.00"))
        User.objects.create_superuser("andy", "a@a.com", "pass")
        self.client.login(username="andy", password="pass")

    def _panel(self):
        from django.urls import reverse
        return self.client.get(reverse("panel_inventario"))

    def test_reparte_lo_cobrado_de_mayor_a_menor(self):
        Venta.objects.create(fecha=date(2026, 8, 1), receta=self.rec_a, cantidad=3)
        Venta.objects.create(fecha=date(2026, 8, 1), receta=self.rec_b, cantidad=1)

        reparto = self._panel().context["reparto"]

        self.assertEqual([r["nombre"] for r in reparto], ["Uno", "Dos"])
        self.assertEqual(reparto[0]["porcentaje"], Decimal("75"))
        self.assertEqual(reparto[1]["porcentaje"], Decimal("25"))

    def test_lo_regalado_no_hace_que_un_producto_se_venda_mas(self):
        Venta.objects.create(fecha=date(2026, 8, 1), receta=self.rec_a, cantidad=1)
        Venta.objects.create(fecha=date(2026, 8, 1), receta=self.rec_b, cantidad=1)
        Venta.objects.create(fecha=date(2026, 8, 2), receta=self.rec_b,
                             cantidad=8, es_cortesia=True)

        reparto = self._panel().context["reparto"]

        self.assertEqual(reparto[0]["porcentaje"], Decimal("50"))
        self.assertEqual(reparto[1]["porcentaje"], Decimal("50"))

    def test_un_producto_sin_ventas_no_aparece(self):
        Venta.objects.create(fecha=date(2026, 8, 1), receta=self.rec_a, cantidad=1)

        nombres = [r["nombre"] for r in self._panel().context["reparto"]]

        self.assertEqual(nombres, ["Uno"])

    def test_sin_ventas_cobradas_no_se_divide_entre_cero(self):
        Venta.objects.create(fecha=date(2026, 8, 1), receta=self.rec_a,
                             cantidad=2, es_cortesia=True)

        resp = self._panel()

        self.assertEqual(resp.context["reparto"], [])
        self.assertContains(resp, "Todavía no hay ventas cobradas que repartir")


class AlarmaMargenTests(TestCase):
    """La alarma avisa cuando el margen BAJA. Solo eso.

    El margen del mes sale del costo real de las ventas, así que la alarma se
    enciende cuando el insumo sube de precio, no cuando alguien edita el
    catálogo.
    """

    def setUp(self):
        self.ing = Ingrediente.objects.create(
            nombre="Leche", unidad_compra="litro", cantidad_por_unidad=1000,
            unidad_receta="ml", costo_unidad_compra=Decimal("100.00"))
        self.rec = Receta.objects.create(
            nombre="Shake", precio_venta=Decimal("100.00"))
        RecetaIngrediente.objects.create(
            receta=self.rec, ingrediente=self.ing, cantidad=200)

    def _capa(self, fecha, monto):
        """Una capa que alcanza justo para una venta: 200 ml."""
        return Compra.objects.create(
            fecha=fecha, ingrediente=self.ing,
            cantidad=Decimal("0.2"), monto_total=monto)

    def _dos_meses(self, monto_julio, monto_agosto):
        """Una venta en julio y otra en agosto, cada una con su propia capa."""
        self._capa(date(2026, 7, 1), monto_julio)
        self._capa(date(2026, 8, 1), monto_agosto)
        Venta.objects.create(fecha=date(2026, 7, 15), receta=self.rec, cantidad=1)
        Venta.objects.create(fecha=date(2026, 8, 5), receta=self.rec, cantidad=1)

    def test_avisa_cuando_el_margen_cae_mas_que_el_umbral(self):
        # Julio: costo 4 → margen 96%. Agosto: costo 20 → margen 80%.
        # Caída relativa = (96 − 80) / 96 = 16.67% ≥ 10%.
        self._dos_meses(Decimal("4.00"), Decimal("20.00"))

        res = alarmas_margen(hoy=date(2026, 8, 20))

        self.assertEqual(len(res["avisos"]), 1)
        a = res["avisos"][0]
        self.assertEqual(a["margen_anterior"], Decimal("96"))
        self.assertEqual(a["margen_actual"], Decimal("80"))
        self.assertAlmostEqual(float(a["caida"]), 16.67, places=2)
        self.assertFalse(a["estimado"])

    def test_una_caida_menor_al_umbral_no_es_alarma(self):
        # Costo de 4 a 4.40: el margen baja de 96% a 95.6%, menos del 10%.
        self._dos_meses(Decimal("4.00"), Decimal("4.40"))
        self.assertEqual(alarmas_margen(hoy=date(2026, 8, 20))["avisos"], [])

    def test_que_el_margen_suba_no_es_alarma(self):
        self._dos_meses(Decimal("20.00"), Decimal("4.00"))
        self.assertEqual(alarmas_margen(hoy=date(2026, 8, 20))["avisos"], [])

    def test_sin_ventas_el_mes_anterior_no_se_inventa_una_caida(self):
        self._capa(date(2026, 8, 1), Decimal("20.00"))
        Venta.objects.create(fecha=date(2026, 8, 5), receta=self.rec, cantidad=1)
        self.assertEqual(alarmas_margen(hoy=date(2026, 8, 20))["avisos"], [])

    def test_la_cortesia_no_cuenta(self):
        """Regalar un shake no es que el producto haya perdido margen."""
        self._capa(date(2026, 7, 1), Decimal("4.00"))
        self._capa(date(2026, 8, 1), Decimal("4.00"))
        Venta.objects.create(fecha=date(2026, 7, 15), receta=self.rec, cantidad=1)
        Venta.objects.create(fecha=date(2026, 8, 5), receta=self.rec,
                             cantidad=1, es_cortesia=True)
        self.assertEqual(alarmas_margen(hoy=date(2026, 8, 20))["avisos"], [])

    def test_leer_el_panel_no_escribe_en_la_base(self):
        """Mirar la alarma es una consulta de lectura, aunque nadie haya
        tocado los ajustes: los valores de fábrica no se guardan solos."""
        self._dos_meses(Decimal("4.00"), Decimal("20.00"))

        alarmas_margen(hoy=date(2026, 8, 20))

        self.assertEqual(ConfiguracionAlarmas.objects.count(), 0)
        self.assertEqual(ConfiguracionAlarmas.get().umbral_caida_margen, 10)

    def test_el_umbral_es_configurable(self):
        self._dos_meses(Decimal("4.00"), Decimal("20.00"))   # caída de 16.67%

        cfg = ConfiguracionAlarmas.get()
        cfg.umbral_caida_margen = 20
        cfg.save()

        self.assertEqual(alarmas_margen(hoy=date(2026, 8, 20))["avisos"], [])

    def test_enero_compara_contra_diciembre_del_ano_pasado(self):
        self._capa(date(2025, 12, 1), Decimal("4.00"))
        self._capa(date(2026, 1, 2), Decimal("20.00"))
        Venta.objects.create(fecha=date(2025, 12, 15), receta=self.rec, cantidad=1)
        Venta.objects.create(fecha=date(2026, 1, 5), receta=self.rec, cantidad=1)

        res = alarmas_margen(hoy=date(2026, 1, 20))

        self.assertEqual(len(res["avisos"]), 1)
        self.assertEqual(res["mes_anterior"], date(2025, 12, 1))

    def test_marca_el_aviso_que_se_apoya_en_el_estimado(self):
        """Sin la compra capturada, el margen sale del catálogo. Hay que decirlo."""
        self._capa(date(2026, 7, 1), Decimal("4.00"))
        Venta.objects.create(fecha=date(2026, 7, 15), receta=self.rec, cantidad=1)
        # Agosto sin capa: cae al catálogo (200 ml × 0.10 = 20 → margen .80).
        Venta.objects.create(fecha=date(2026, 8, 5), receta=self.rec, cantidad=1)

        avisos = alarmas_margen(hoy=date(2026, 8, 20))["avisos"]

        self.assertEqual(len(avisos), 1)
        self.assertTrue(avisos[0]["estimado"])

    def test_el_panel_muestra_la_alarma_solo_al_dueno(self):
        from django.contrib.auth.models import User
        from django.urls import reverse
        self._dos_meses(Decimal("4.00"), Decimal("20.00"))
        url = reverse("panel_inventario")

        User.objects.create_superuser("andy", "a@a.com", "pass")
        self.client.login(username="andy", password="pass")
        self.assertContains(self.client.get(url), "Alarma de margen")

        self.client.logout()
        User.objects.create_user("cajero", "c@c.com", "pass")
        self.client.login(username="cajero", password="pass")
        self.assertNotContains(self.client.get(url), "Alarma de margen")

    def test_el_panel_publica_los_porcentajes_de_la_alarma(self):
        """Lo que se verificó a mano, fijado: el recorrido completo hasta la
        pantalla, en porcentaje y con las unidades de la muestra a la vista."""
        from django.contrib.auth.models import User
        from django.urls import reverse
        self._dos_meses(Decimal("4.00"), Decimal("20.00"))
        User.objects.create_superuser("andy", "a@a.com", "pass")
        self.client.login(username="andy", password="pass")

        html = self.client.get(reverse("panel_inventario")).content.decode()

        self.assertIn("96.0%", html)      # julio
        self.assertIn("80.0%", html)      # agosto
        self.assertIn("−16.7%", html)     # la caída
        self.assertIn("1 → 1", html)      # sobre cuántas ventas se midió
        self.assertIn("1 con caída", html)
        self.assertNotIn("· estimado", html)   # ambas ventas están costeadas

    def test_el_panel_marca_el_aviso_apoyado_en_el_catalogo(self):
        from django.contrib.auth.models import User
        from django.urls import reverse
        self._capa(date(2026, 7, 1), Decimal("4.00"))
        Venta.objects.create(fecha=date(2026, 7, 15), receta=self.rec, cantidad=1)
        Venta.objects.create(fecha=date(2026, 8, 5), receta=self.rec, cantidad=1)
        User.objects.create_superuser("andy", "a@a.com", "pass")
        self.client.login(username="andy", password="pass")

        self.assertContains(
            self.client.get(reverse("panel_inventario")), "· estimado")

    def test_el_panel_dice_cuando_no_hay_caidas(self):
        from django.contrib.auth.models import User
        from django.urls import reverse
        self._dos_meses(Decimal("4.00"), Decimal("4.00"))
        User.objects.create_superuser("andy", "a@a.com", "pass")
        self.client.login(username="andy", password="pass")

        resp = self.client.get(reverse("panel_inventario"))

        self.assertContains(resp, "Sin caídas")
        self.assertNotContains(resp, "con caída")
