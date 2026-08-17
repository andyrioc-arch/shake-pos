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
            cantidad=2, monto_total=Decimal("60.00"),  # 2 litros = 2000ml
        )
        self.assertEqual(self.leche.total_comprado, Decimal("2000"))

    def test_el_total_sale_del_monto_pagado(self):
        compra = Compra.objects.create(
            fecha=date(2025, 5, 1), ingrediente=self.leche,
            cantidad=Decimal("1.5"), monto_total=Decimal("47.33"),
        )
        # Ni se recalcula ni se redondea: es lo que se pagó.
        self.assertEqual(compra.total, Decimal("47.33"))

    def test_el_unitario_se_deriva_y_no_reconstruye_el_total(self):
        """Desde P11 el unitario es una propiedad, no una columna.

        Es para mostrar. Reconstruir el total multiplicándolo de vuelta da
        47.325 en vez de 47.33: la división pierde centavos y la
        multiplicación no los recupera. Ese era el bug que cerró P1, y
        guardar el derivado al lado del dato es como vuelve a entrar.
        """
        compra = Compra.objects.create(
            fecha=date(2025, 5, 1), ingrediente=self.leche,
            cantidad=Decimal("1.5"), monto_total=Decimal("47.33"),
        )

        self.assertEqual(compra.total, Decimal("47.33"))
        self.assertNotEqual(compra.cantidad * compra.costo_unitario.quantize(
            Decimal("0.01")), compra.total)

    def test_una_compra_sin_monto_ya_no_se_puede_guardar(self):
        """El hueco dejó de existir: la base lo rechaza."""
        from django.db import IntegrityError, transaction

        with self.assertRaises(IntegrityError), transaction.atomic():
            Compra.objects.create(
                fecha=date(2025, 5, 1), ingrediente=self.leche, cantidad=2)

    def test_consumo_y_stock(self):
        Compra.objects.create(
            fecha=date(2025, 5, 1), ingrediente=self.leche,
            cantidad=2, monto_total=Decimal("60.00"),  # 2000ml
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
                              cantidad=1, monto_total=Decimal("30.00"))  # 1000ml
        Compra.objects.create(fecha=date(2025, 5, 1), ingrediente=self.almendra,
                              cantidad=1, monto_total=Decimal("55.00"))  # 1000ml
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
                              cantidad=1, monto_total=Decimal("80.00"))  # 1000ml
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
        from django.contrib.auth.models import User
        User.objects.create_superuser("andy", "a@a.com", "pass")
        self.client.login(username="andy", password="pass")

        resp = self.client.get("/admin/inventario/receta/")

        self.assertContains(resp, "3 (1 de cortesía)")

    def test_sin_cortesias_el_admin_no_agrega_ruido(self):
        from django.contrib.auth.models import User
        Receta.objects.create(nombre="Otro", precio_venta=Decimal("90.00"))
        User.objects.create_superuser("andy", "a@a.com", "pass")
        self.client.login(username="andy", password="pass")

        resp = self.client.get("/admin/inventario/receta/")

        self.assertNotContains(resp, "0 (0 de cortesía)")


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

    def test_ninguna_plantilla_le_enseña_comentarios_al_cliente(self):
        """`{# #}` de Django es de UNA línea: si abarca dos, se imprime tal
        cual. Pasó en la nota, que es la única página que ve el cliente."""
        import re
        from pathlib import Path

        sueltos = []
        for plantilla in Path(".").rglob("*.html"):
            if ".venv" in str(plantilla):
                continue
            texto = plantilla.read_text()
            for marca in re.finditer(r"\{#", texto):
                resto = texto[marca.start():]
                cierre = resto.find("#}")
                if cierre == -1 or "\n" in resto[:cierre]:
                    sueltos.append(str(plantilla))

        self.assertEqual(sueltos, [], "Usa {% comment %} para varias líneas")


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
        self.assertEqual(self.rec.costo_ultima_compra(), Decimal("40.00"))
        # El catálogo sigue diciendo lo suyo: 200×0.03 + 50×0.40 = 26
        self.assertEqual(self.rec.costo_receta, Decimal("26.00"))

    def test_si_falta_una_compra_no_se_mezcla_con_el_catalogo(self):
        """Media receta a precios reales y la otra media a catálogo no es
        ninguna de las dos cosas."""
        Compra.objects.create(fecha=date(2026, 8, 1), ingrediente=self.leche,
                              cantidad=Decimal("1"), monto_total=Decimal("50"))

        self.assertIsNone(self.rec.costo_ultima_compra())

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


class PedidosPendientesTests(TestCase):
    """La barra: a nombre de quién va cada pedido y qué falta por entregar."""

    def setUp(self):
        from django.contrib.auth.models import User
        self.cajero = User.objects.create_user(
            "caja", "caja@x.mx", "x", is_staff=True)
        self.client.force_login(self.cajero)
        self.ing = Ingrediente.objects.create(
            nombre="Leche", unidad_compra="litro", cantidad_por_unidad=1000,
            unidad_receta="ml", costo_unidad_compra=Decimal("30.00"))
        self.rec = Receta.objects.create(
            nombre="Shake", emoji="🍓", precio_venta=Decimal("100.00"))
        RecetaIngrediente.objects.create(
            receta=self.rec, ingrediente=self.ing, cantidad=200)

    def _vender(self, nombre="Andrea", **extra):
        import json
        from django.urls import reverse
        datos = {
            "productos_json": json.dumps(
                [{"receta": self.rec.pk, "cantidad": 1}]),
            "metodo_pago": "efectivo", "pago_con": "200",
            "nombre_cliente": nombre,
        }
        datos.update(extra)
        return self.client.post(reverse("inventario_venta_agregar"), datos)

    def _pedidos(self):
        from django.urls import reverse
        return self.client.get(reverse("panel_pedidos"))

    def test_la_venta_guarda_a_nombre_de_quien_va(self):
        self._vender("Andrea")
        self.assertEqual(Nota.objects.get().nombre_cliente, "Andrea")

    def test_sin_nombre_no_se_registra_la_venta(self):
        """La validación tira toda la venta, no la deja a medias."""
        from django.urls import reverse
        resp = self._vender("")
        self.assertRedirects(resp, reverse("panel_inventario"))
        self.assertEqual(Nota.objects.count(), 0)
        self.assertEqual(Venta.objects.count(), 0)

    def test_solo_espacios_tampoco_cuenta_como_nombre(self):
        self._vender("   ")
        self.assertEqual(Nota.objects.count(), 0)

    def test_un_nombre_larguisimo_no_revienta(self):
        """`CharField(80)` en Postgres corta con error, no truncando."""
        self._vender("A" * 300)
        self.assertEqual(len(Nota.objects.get().nombre_cliente), 80)

    def test_una_venta_nueva_nace_pendiente(self):
        self._vender("Andrea")
        nota = Nota.objects.get()
        self.assertIsNone(nota.entregada_en)
        self.assertTrue(nota.pendiente)

    def test_la_lista_muestra_lo_pendiente_con_su_nombre(self):
        self._vender("Andrea")
        resp = self._pedidos()
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Andrea")
        self.assertContains(resp, "1× 🍓 Shake")

    def test_al_entregarlo_desaparece_de_la_lista(self):
        from django.urls import reverse
        self._vender("Andrea")
        nota = Nota.objects.get()
        resp = self.client.post(reverse("pedido_entregar", args=[nota.pk]))
        self.assertRedirects(resp, reverse("panel_pedidos"))
        nota.refresh_from_db()
        self.assertIsNotNone(nota.entregada_en)
        self.assertFalse(nota.pendiente)
        self.assertContains(self._pedidos(), "No hay nada pendiente")

    def test_entregar_dos_veces_no_reescribe_la_hora(self):
        """En la barra dos personas aprietan el mismo botón a la vez."""
        from django.urls import reverse
        self._vender("Andrea")
        nota = Nota.objects.get()
        url = reverse("pedido_entregar", args=[nota.pk])
        self.client.post(url)
        nota.refresh_from_db()
        primera = nota.entregada_en
        self.client.post(url)
        nota.refresh_from_db()
        self.assertEqual(nota.entregada_en, primera)

    def test_los_pedidos_salen_en_el_orden_en_que_entraron(self):
        """Se atiende por turno; el más viejo va arriba."""
        self._vender("Primero")
        self._vender("Segundo")
        cuerpo = self._pedidos().content.decode()
        self.assertLess(cuerpo.index("Primero"), cuerpo.index("Segundo"))

    def test_la_cortesia_tambien_se_entrega(self):
        """Un regalo también se prepara y se canta: no puede saltarse la lista."""
        self._vender("Andrea", cortesia="1", motivo_cortesia="Activación",
                     pago_con="")
        self.assertContains(self._pedidos(), "Cortesía")

    def test_el_cajero_no_ve_montos_en_la_lista(self):
        """Misma puerta que el resto: el staff ve unidades, no dinero."""
        self._vender("Andrea")
        self.assertNotContains(self._pedidos(), "$100.00")

    def test_la_lista_vacia_lo_dice(self):
        self.assertContains(self._pedidos(), "No hay nada pendiente")

    def test_pide_sesion(self):
        from django.urls import reverse
        self.client.logout()
        resp = self.client.get(reverse("panel_pedidos"))
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/login/", resp["Location"])


class DescuentoEnLaVentaTests(TestCase):
    """El descuento baja lo que se cobra. Nunca lo que costó."""

    def setUp(self):
        from django.contrib.auth.models import User
        from contabilidad import posting
        posting.crear_catalogo()
        self.andy = User.objects.create_superuser("andy", "a@x.mx", "x")
        self.client.force_login(self.andy)
        self.hoy = date.today()
        self.leche = Ingrediente.objects.create(
            nombre="Leche", unidad_compra="litro", cantidad_por_unidad=1000,
            unidad_receta="ml", costo_unidad_compra=Decimal("30.00"))
        self.rec = Receta.objects.create(
            nombre="Shake", emoji="🍓", precio_venta=Decimal("100.00"))
        RecetaIngrediente.objects.create(
            receta=self.rec, ingrediente=self.leche, cantidad=200)
        Compra.objects.create(fecha=self.hoy, ingrediente=self.leche,
                              cantidad=Decimal("2"), monto_total=Decimal("60"))

    def _vender(self, pct="", cantidad=1, **extra):
        import json
        from django.urls import reverse
        datos = {
            "productos_json": json.dumps(
                [{"receta": self.rec.pk, "cantidad": cantidad}]),
            "metodo_pago": "efectivo", "pago_con": "1000",
            "nombre_cliente": "Andrea", "descuento_pct": pct,
            "fecha": self.hoy.isoformat(),
        }
        datos.update(extra)
        return self.client.post(reverse("inventario_venta_agregar"), datos)

    # ── Lo que cobra ────────────────────────────────────────────────────────
    def test_sin_descuento_cobra_el_precio_de_lista(self):
        self._vender()
        v = Venta.objects.get()
        self.assertEqual(v.descuento_pct, Decimal("0"))
        self.assertEqual(v.ingreso, Decimal("100.00"))
        self.assertEqual(v.ingreso_lista, Decimal("100.00"))

    def test_con_descuento_baja_el_ingreso(self):
        self._vender("15")
        v = Venta.objects.get()
        self.assertEqual(v.ingreso_lista, Decimal("100.00"))
        self.assertEqual(v.descuento_monto, Decimal("15.00"))
        self.assertEqual(v.ingreso, Decimal("85.00"))

    def test_el_descuento_no_toca_el_costo(self):
        """La regla que sostiene todo: se rebaja lo cobrado, no lo que costó."""
        self._vender("50")
        v = Venta.objects.get()
        self.assertEqual(v.costo_fifo, Decimal("6.00"))   # 200 ml × 0.03
        self.assertEqual(v.ganancia, Decimal("44.00"))    # 50 − 6

    def test_del_100_por_ciento_cobra_cero_pero_no_es_cortesia(self):
        """Regalar y descontar al 100% no son lo mismo en los libros.

        La cortesía va contra 506; el descuento se queda en el ingreso.
        """
        self._vender("100")
        v = Venta.objects.get()
        self.assertEqual(v.ingreso, Decimal("0.00"))
        self.assertFalse(v.es_cortesia)
        self.assertEqual(v.costo_fifo, Decimal("6.00"))

    def test_se_redondea_una_sola_vez(self):
        """3 × 100 × 33.33% deja fracciones de centavo en cada parte."""
        self._vender("33.33", cantidad=3)
        v = Venta.objects.get()
        self.assertEqual(v.ingreso, Decimal("200.01"))
        self.assertEqual(Nota.objects.get().total, Decimal("200.01"))

    # ── Lo que rechaza ──────────────────────────────────────────────────────
    def test_un_descuento_imposible_se_ignora_y_no_tumba_la_venta(self):
        for malo in ("101", "-5", "abc", "NaN", "Infinity", "1e400"):
            with self.subTest(pct=malo):
                Venta.objects.all().delete()
                Nota.objects.all().delete()
                self._vender(malo)
                v = Venta.objects.get()
                self.assertEqual(v.descuento_pct, Decimal("0"))
                self.assertEqual(v.ingreso, Decimal("100.00"))

    def test_una_cortesia_ignora_el_descuento(self):
        self._vender("50", cortesia="1", motivo_cortesia="Activación",
                     pago_con="")
        v = Venta.objects.get()
        self.assertEqual(v.descuento_pct, Decimal("0"))
        self.assertEqual(v.ingreso, Decimal("0"))

    def test_el_efectivo_se_compara_contra_lo_rebajado(self):
        """Con 15% de descuento, $90 alcanzan para un shake de $100."""
        resp = self._vender("15", pago_con="90")
        self.assertEqual(Venta.objects.count(), 1)
        self.assertEqual(Nota.objects.get().cambio, Decimal("5.00"))

    # ── Lo que arrastra ─────────────────────────────────────────────────────
    def test_la_contabilidad_registra_lo_cobrado(self):
        from contabilidad import posting
        from contabilidad.models import Movimiento
        self._vender("15")
        v = Venta.objects.get()
        self.assertEqual(Movimiento.objects.get(venta=v).monto, Decimal("85.00"))
        er = posting.estado_resultados(self.hoy.year, self.hoy.month)
        self.assertEqual(er["total_ingresos"], Decimal("85"))
        self.assertEqual(er["total_costo_ventas"], Decimal("6"))

    def test_el_iva_se_calcula_sobre_lo_cobrado(self):
        self._vender("15")
        nota = Nota.objects.get()
        self.assertEqual(nota.total, Decimal("85.00"))
        self.assertEqual(nota.subtotal + nota.iva, nota.total)

    def test_los_puntos_se_dan_por_lo_que_pago(self):
        """No por el precio de lista: los puntos siguen al dinero."""
        from lealtad.models import Cliente
        self._vender("50", telefono_lealtad="9991234567")
        # $100 con 50% = $50 cobrados ÷ $10 por punto
        self.assertEqual(Cliente.objects.get().puntos_saldo, 5)

    def test_la_nota_guarda_de_cuanto_era_y_cuanto_se_rebajo(self):
        self._vender("15", cantidad=2)
        nota = Nota.objects.get()
        self.assertEqual(nota.total_lista, Decimal("200.00"))
        self.assertEqual(nota.descuento_monto, Decimal("30.00"))
        self.assertEqual(nota.descuento_pct, Decimal("15.00"))
        self.assertTrue(nota.tiene_descuento)

    def test_sin_descuento_la_nota_no_lo_menciona(self):
        self._vender()
        self.assertFalse(Nota.objects.get().tiene_descuento)

    def test_el_comprobante_lo_muestra(self):
        self._vender("15")
        nota = Nota.objects.get()
        resp = self.client.get(nota.get_absolute_url())
        self.assertContains(resp, "Descuento (15%)")
        self.assertContains(resp, "Precio de lista")

    def test_el_pdf_no_se_rompe_con_descuento(self):
        from django.urls import reverse
        self._vender("15")
        nota = Nota.objects.get()
        resp = self.client.get(reverse("nota_pdf", args=[nota.token]))
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.content.startswith(b"%PDF-"))


class ValoresImposiblesEnLaCajaTests(TestCase):
    """`Decimal` acepta NaN e Infinity y revienta al compararlos.

    Es un 500 con el cliente enfrente, y por `_to_decimal` pasan el efectivo
    recibido y la captura de compras, no solo el descuento.
    """

    def setUp(self):
        from django.contrib.auth.models import User
        self.andy = User.objects.create_superuser("andy", "a@x.mx", "x")
        self.client.force_login(self.andy)
        self.ing = Ingrediente.objects.create(
            nombre="Leche", unidad_compra="litro", cantidad_por_unidad=1000,
            unidad_receta="ml", costo_unidad_compra=Decimal("30.00"))
        self.rec = Receta.objects.create(
            nombre="Shake", precio_venta=Decimal("100.00"))
        RecetaIngrediente.objects.create(
            receta=self.rec, ingrediente=self.ing, cantidad=200)

    def test_pago_con_no_finito_no_tumba_la_caja(self):
        import json
        from django.urls import reverse
        for malo in ("NaN", "sNaN", "Infinity", "-Infinity"):
            with self.subTest(pago_con=malo):
                resp = self.client.post(reverse("inventario_venta_agregar"), {
                    "productos_json": json.dumps(
                        [{"receta": self.rec.pk, "cantidad": 1}]),
                    "metodo_pago": "efectivo", "pago_con": malo,
                    "nombre_cliente": "Andrea"})
                self.assertEqual(resp.status_code, 302)
                self.assertEqual(Venta.objects.count(), 0)

    def test_una_compra_con_cantidad_no_finita_no_tumba_la_pagina(self):
        from django.urls import reverse
        for campo in ("cantidad", "costo_total"):
            for malo in ("NaN", "Infinity"):
                with self.subTest(campo=campo, valor=malo):
                    datos = {"ingrediente": self.ing.pk, "cantidad": "1",
                             "costo_total": "10"}
                    datos[campo] = malo
                    resp = self.client.post(
                        reverse("inventario_compra_agregar"), datos)
                    self.assertEqual(resp.status_code, 302)
                    self.assertEqual(Compra.objects.count(), 0)


class LaNotaConDescuentoCuadraTests(TestCase):
    """El comprobante que ve el cliente tiene que sumar.

    `lista − descuento = total`, con el descuento redondeado como lo imprime la
    plantilla. Derivarlo de la resta y no de la suma de importes crudos es lo
    único que lo garantiza: el total se redondea sobre el complemento, así que
    dos redondeos independientes suben los dos y se gana un centavo.
    """

    def setUp(self):
        self.ing = Ingrediente.objects.create(
            nombre="Leche", unidad_compra="litro", cantidad_por_unidad=1000,
            unidad_receta="ml", costo_unidad_compra=Decimal("30.00"))

    def _nota(self, precio, pct, cantidad=1):
        rec = Receta.objects.create(
            nombre=f"Shake {precio}-{pct}", precio_venta=Decimal(precio))
        RecetaIngrediente.objects.create(
            receta=rec, ingrediente=self.ing, cantidad=200)
        nota = Nota.objects.create(fecha=date.today(), total=Decimal("0"))
        venta = Venta.objects.create(
            fecha=date.today(), receta=rec, cantidad=cantidad,
            descuento_pct=Decimal(pct), nota=nota)
        Nota.objects.filter(pk=nota.pk).update(total=venta.ingreso)
        nota.refresh_from_db()
        return nota

    def test_la_resta_del_comprobante_da_el_total(self):
        from django.template.defaultfilters import floatformat
        # 12.35% deja media milésima justo en la frontera del redondeo.
        for precio in ("10.00", "30.00", "50.00", "70.00", "95.00"):
            for pct in ("12.35", "33.33", "15", "7.77"):
                with self.subTest(precio=precio, pct=pct):
                    nota = self._nota(precio, pct)
                    mostrado = Decimal(floatformat(nota.descuento_monto, 2))
                    self.assertEqual(nota.total_lista - mostrado, nota.total)

    def test_una_cortesia_no_anuncia_un_descuento_que_nadie_dio(self):
        """`total` es 0 y el de lista no: sin guarda saldría «100% de descuento»."""
        rec = Receta.objects.create(nombre="Regalo", precio_venta=Decimal("50"))
        RecetaIngrediente.objects.create(
            receta=rec, ingrediente=self.ing, cantidad=200)
        nota = Nota.objects.create(
            fecha=date.today(), total=Decimal("0"), es_cortesia=True)
        Venta.objects.create(fecha=date.today(), receta=rec, cantidad=1,
                             es_cortesia=True, nota=nota)
        self.assertFalse(nota.tiene_descuento)
        self.assertEqual(nota.descuento_monto, Decimal("0"))

    def test_el_comprobante_no_repite_la_misma_consulta(self):
        """Es la página que abre el cliente desde el QR, contra el pooler."""
        nota = self._nota("30.00", "10")
        for _ in range(2):
            Venta.objects.create(fecha=date.today(), receta=nota.lineas.first().receta,
                                 cantidad=1, descuento_pct=Decimal("10"), nota=nota)
        with self.assertNumQueries(9):
            self.client.get(nota.get_absolute_url())


class CapturaDeComprasTests(TestCase):
    """La captura que dejó 24 compras 1360 veces más baratas de lo real.

    El error no se ve en el ticket del proveedor: se ve en el costo por unidad
    de receta, y solo comparándolo contra la compra anterior.
    """

    def setUp(self):
        from django.contrib.auth.models import User
        self.andy = User.objects.create_superuser("andy", "a@x.mx", "x")
        self.client.force_login(self.andy)
        self.hoy = date.today()
        # Una bolsa de 1360 g, como las blueberries de producción.
        self.ing = Ingrediente.objects.create(
            nombre="Blueberries", unidad_compra="bolsa",
            cantidad_por_unidad=1360, unidad_receta="g",
            costo_unidad_compra=Decimal("178.54"))

    def _comprar(self, cantidad, monto="178.54"):
        from django.urls import reverse
        return self.client.post(reverse("inventario_compra_agregar"), {
            "ingrediente": self.ing.pk, "cantidad": cantidad,
            "costo_total": monto, "fecha": self.hoy.isoformat(),
        }, follow=True)

    def _avisos(self, resp):
        return [str(m) for m in resp.context["messages"]]

    # ── La capa ─────────────────────────────────────────────────────────────
    def test_un_paquete_deja_la_capa_del_tamaño_del_paquete(self):
        self._comprar("1")
        compra = Compra.objects.get()
        self.assertEqual(compra.cantidad_receta, Decimal("1360.0000"))
        self.assertEqual(compra.saldo_receta, Decimal("1360.0000"))
        self.assertEqual(
            compra.costo_unitario_capa.quantize(Decimal("0.0001")),
            Decimal("0.1313"))

    def test_el_aviso_dice_en_qué_se_convirtió(self):
        """El mensaje trae el costo por gramo, no solo lo que se pagó."""
        resp = self._comprar("1")
        self.assertTrue(any("por g" in m and "1,360 g" in m
                            for m in self._avisos(resp)))

    # ── La red de atrás ─────────────────────────────────────────────────────
    def test_la_primera_compra_no_tiene_con_qué_compararse(self):
        resp = self._comprar("1")
        self.assertFalse(any("Ojo" in m for m in self._avisos(resp)))

    def test_capturar_gramos_en_vez_de_paquetes_dispara_el_aviso(self):
        """El error real de agosto: 1360 donde iba 1."""
        self._comprar("1")
        resp = self._comprar("1360")
        avisos = self._avisos(resp)
        self.assertTrue(any("Ojo" in m and "más barato" in m for m in avisos))
        self.assertTrue(any("número de paquetes" in m for m in avisos))

    def test_el_aviso_no_llama_anterior_a_una_compra_posterior(self):
        """Capturar la factura atrasada es un flujo normal aquí.

        La referencia sigue siendo el precio más reciente —es la mejor vara—
        pero llamarla «la anterior» sería mentir en el único mensaje que
        existe para cazar un error de captura.
        """
        from datetime import timedelta
        from django.urls import reverse
        self._comprar("1")                       # la de hoy
        resp = self.client.post(reverse("inventario_compra_agregar"), {
            "ingrediente": self.ing.pk, "cantidad": "1360",
            "costo_total": "178.54",
            "fecha": (self.hoy - timedelta(days=7)).isoformat(),
        }, follow=True)                          # la factura atrasada
        avisos = self._avisos(resp)
        self.assertTrue(any("Ojo" in m for m in avisos))
        self.assertFalse(any("compra anterior" in m for m in avisos))
        self.assertTrue(any("más reciente" in m and
                            self.hoy.strftime("%d/%m/%Y") in m
                            for m in avisos))

    def test_tambien_avisa_al_revés(self):
        """Capturar 1 donde iban 1360 deja la capa carísima."""
        self._comprar("1360")
        resp = self._comprar("1")
        self.assertTrue(any("Ojo" in m and "más caro" in m
                            for m in self._avisos(resp)))

    def test_una_subida_de_precio_normal_no_hace_ruido(self):
        """Un proveedor que sube 30% no puede generar un aviso.

        Si avisa por todo, se aprende a ignorarlo y deja de servir.
        """
        self._comprar("1", "178.54")
        resp = self._comprar("1", "232.10")
        self.assertFalse(any("Ojo" in m for m in self._avisos(resp)))

    def test_el_aviso_no_impide_registrar_la_compra(self):
        """Un precio puede subir de verdad; negarse sería peor."""
        self._comprar("1")
        self._comprar("1360")
        self.assertEqual(Compra.objects.count(), 2)

    def test_la_compra_anterior_es_la_mas_reciente_no_la_primera(self):
        """Se compara contra la última, no contra la primera del histórico."""
        from datetime import timedelta
        from django.urls import reverse
        self._comprar("1")
        self.client.post(reverse("inventario_compra_agregar"), {
            "ingrediente": self.ing.pk, "cantidad": "1",
            "costo_total": "180.00",
            "fecha": (self.hoy + timedelta(days=1)).isoformat()})
        resp = self._comprar("1", "179.00")
        self.assertFalse(any("Ojo" in m for m in self._avisos(resp)))

    # ── Lo que la pantalla necesita ────────────────────────────────────────
    def test_el_panel_publica_la_referencia_de_cada_ingrediente(self):
        from django.urls import reverse
        self._comprar("1")
        resp = self.client.get(reverse("panel_inventario"))
        self.assertIn(self.ing.pk, resp.context["referencias_compra"])
        self.assertAlmostEqual(
            resp.context["referencias_compra"][self.ing.pk], 0.13128, places=4)

    def test_el_cajero_no_recibe_las_referencias(self):
        """Son costos: misma puerta que el resto del dinero."""
        from django.contrib.auth.models import User
        from django.urls import reverse
        User.objects.create_user("caja", "c@x.mx", "x", is_staff=True)
        self.client.login(username="caja", password="x")
        resp = self.client.get(reverse("panel_inventario"))
        self.assertEqual(resp.context["referencias_compra"], {})


class MermaTests(TestCase):
    """El conteo físico: lo que falta sale del inventario y se vuelve gasto."""

    def setUp(self):
        from django.contrib.auth.models import User
        from contabilidad import posting
        posting.crear_catalogo()
        self.andy = User.objects.create_superuser("andy", "a@x.mx", "x")
        self.client.force_login(self.andy)
        self.hoy = date.today()
        self.ing = Ingrediente.objects.create(
            nombre="Fresas", unidad_compra="bolsa", cantidad_por_unidad=1000,
            unidad_receta="g", costo_unidad_compra=Decimal("100.00"))
        # 1000 g a $0.10 el gramo.
        Compra.objects.create(fecha=self.hoy, ingrediente=self.ing,
                              cantidad=Decimal("1"), monto_total=Decimal("100"))

    def _contar(self, real, **extra):
        from django.urls import reverse
        datos = {"ingrediente": self.ing.pk, "cantidad_real": str(real),
                 "fecha": self.hoy.isoformat(), "motivo": "Se echó a perder"}
        datos.update(extra)
        return self.client.post(
            reverse("inventario_merma_registrar"), datos, follow=True)

    def _avisos(self, resp):
        return [str(m) for m in resp.context["messages"]]

    def _ajuste(self):
        from inventario.models import AjusteInventario
        return AjusteInventario.objects.get()

    # ── Lo que sale del inventario ──────────────────────────────────────────
    def test_contar_de_menos_registra_la_merma_y_la_cuesta(self):
        self._contar("800")
        a = self._ajuste()
        self.assertEqual(a.cantidad_calculada, Decimal("1000.0000"))
        self.assertEqual(a.merma, Decimal("200.0000"))
        self.assertEqual(a.costo, Decimal("20.00"))    # 200 g × $0.10
        self.assertFalse(a.costo_incompleto)

    def test_la_merma_consume_la_capa(self):
        """No basta con anotar el gasto: el inventario tiene que bajar."""
        self._contar("800")
        capa = Compra.objects.get()
        self.assertEqual(capa.saldo_receta, Decimal("800.0000"))

    def test_despues_del_conteo_el_stock_es_lo_que_se_contó(self):
        self._contar("800")
        self.ing.refresh_from_db()
        self.assertEqual(self.ing.stock_disponible, Decimal("800"))

    def test_el_asiento_va_de_merma_contra_inventario(self):
        from contabilidad.models import Asiento
        self._contar("800")
        asiento = Asiento.objects.get(referencia=f"Merma #{self._ajuste().pk}")
        lineas = sorted((l.cuenta.codigo, l.debe, l.haber)
                        for l in asiento.movimientos.all())
        self.assertEqual(lineas, [
            ("115", Decimal("0.00"), Decimal("20.00")),
            ("507", Decimal("20.00"), Decimal("0.00")),
        ])

    def test_la_merma_no_mueve_efectivo(self):
        """Perder mercancía no saca dinero de la caja."""
        from contabilidad import posting
        self._contar("800")
        flujo = posting.flujo_efectivo(self.hoy.year, self.hoy.month)
        self.assertEqual(flujo["salidas"], Decimal("100"))   # solo la compra

    def test_la_merma_entra_al_estado_de_resultados_como_gasto(self):
        from contabilidad import posting
        self._contar("800")
        er = posting.estado_resultados(self.hoy.year, self.hoy.month)
        codigos = {g["codigo"] for g in er["gastos"]}
        self.assertIn("507", codigos)
        self.assertEqual(er["total_gastos"], Decimal("20"))

    def test_la_balanza_y_el_balance_siguen_cuadrando(self):
        from contabilidad import posting
        self._contar("800")
        self.assertTrue(
            posting.balanza_comprobacion(self.hoy.year, self.hoy.month)["cuadra"])
        self.assertTrue(
            posting.balance_general(self.hoy.year, self.hoy.month)["cuadra"])

    # ── Lo que NO hace ──────────────────────────────────────────────────────
    def test_contar_de_más_no_da_de_alta_inventario(self):
        """Sobrar significa que falta capturar una compra, no que apareció.

        Darle entrada obligaría a inventarle un precio, que es justo lo que el
        costeo entero existe para no hacer.
        """
        from contabilidad.models import Asiento
        antes = Asiento.objects.count()
        resp = self._contar("1200")
        a = self._ajuste()
        self.assertEqual(a.sobrante, Decimal("200.0000"))
        self.assertFalse(a.es_merma)
        self.assertIsNone(a.costo)
        self.assertEqual(Asiento.objects.count(), antes)
        self.ing.refresh_from_db()
        self.assertEqual(self.ing.stock_disponible, Decimal("1000"))
        self.assertTrue(any("falta capturar una compra" in m
                            for m in self._avisos(resp)))

    def test_un_conteo_que_cuadra_no_registra_nada(self):
        self._contar("1000")
        a = self._ajuste()
        self.assertFalse(a.es_merma)
        self.assertEqual(a.costo, None)

    def test_no_se_inventa_costo_si_faltan_capas(self):
        """Perder algo que ninguna capa de esa fecha respalda no se cuesta.

        Es el caso real: se cuenta el lunes y la única compra que respalda ese
        stock está fechada el miércoles. El FIFO solo mira capas anteriores al
        movimiento, así que no hay de dónde sacar el costo — y se deja
        constancia en vez de inventarlo con el catálogo.
        """
        from datetime import timedelta
        from contabilidad.models import Asiento
        ayer = self.hoy - timedelta(days=1)
        resp = self._contar("800", fecha=ayer.isoformat())
        a = self._ajuste()
        self.assertEqual(a.merma, Decimal("200.0000"))
        self.assertTrue(a.costo_incompleto)
        self.assertEqual(a.costo, Decimal("0.00"))
        self.assertFalse(
            Asiento.objects.filter(referencia=f"Merma #{a.pk}").exists())
        self.assertTrue(any("todavía no entra al gasto" in m
                            for m in self._avisos(resp)))

    # ── Deshacer y rehacer ──────────────────────────────────────────────────
    def test_borrar_la_merma_le_devuelve_el_saldo_a_la_capa(self):
        self._contar("800")
        self._ajuste().delete()
        self.assertEqual(Compra.objects.get().saldo_receta, Decimal("1000.0000"))
        self.ing.refresh_from_db()
        self.assertEqual(self.ing.stock_disponible, Decimal("1000"))

    def test_una_compra_atrasada_recupera_una_merma_incompleta(self):
        """La misma red que tienen las ventas: en cuanto llega, entra sola."""
        from datetime import timedelta
        ayer = self.hoy - timedelta(days=1)
        self._contar("800", fecha=ayer.isoformat())
        self.assertTrue(self._ajuste().costo_incompleto)

        # Aparece la factura del proveedor, fechada antes del conteo.
        Compra.objects.create(fecha=ayer - timedelta(days=1),
                              ingrediente=self.ing, cantidad=Decimal("1"),
                              monto_total=Decimal("60"))
        a = self._ajuste()
        self.assertFalse(a.costo_incompleto)
        self.assertEqual(a.costo, Decimal("12.00"))    # 200 g × $0.06

    def test_recostear_todo_no_cambia_una_merma_sana(self):
        from io import StringIO
        from django.core.management import call_command
        self._contar("800")
        antes = (self._ajuste().costo, Compra.objects.get().saldo_receta)
        call_command("recostear", "--todo", stdout=StringIO())
        self.assertEqual(
            (self._ajuste().costo, Compra.objects.get().saldo_receta), antes)

    def test_el_auditor_sigue_sano_con_mermas(self):
        """El invariante de las capas cuenta la merma junto con las ventas."""
        from io import StringIO
        from django.core.management import call_command
        self._contar("800")
        salida = StringIO()
        call_command("recostear", "--verificar", stdout=salida)
        self.assertIn("sano", salida.getvalue().lower())

    def test_el_cajero_no_puede_registrar_conteos(self):
        from django.contrib.auth.models import User
        from django.urls import reverse
        User.objects.create_user("caja", "c@x.mx", "x", is_staff=True)
        self.client.login(username="caja", password="x")
        url = reverse("inventario_merma_registrar")
        resp = self.client.post(url, {"ingrediente": self.ing.pk,
                                      "cantidad_real": "800"})
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp["Location"], f"/?next={url}")


class MermaSinConsultaPorIngredienteTests(TestCase):
    """El panel no puede pagar una consulta por ingrediente para las mermas.

    Es la pantalla que el cajero tiene abierta todo el día, y cada consulta es
    un viaje al pooler. Es la misma lección que dejó el costo de la última
    compra, que llevó el catálogo de 61 a 251 consultas.
    """

    def test_las_mermas_del_catalogo_salen_de_una_sola_consulta(self):
        from django.contrib.auth.models import User
        from django.db import connection
        from django.test.utils import CaptureQueriesContext
        from django.urls import reverse

        cajero = User.objects.create_user("caja", "c@x.mx", "x", is_staff=True)
        self.client.force_login(cajero)
        for n in range(20):
            Ingrediente.objects.create(
                nombre=f"Ing {n}", unidad_compra="kg", cantidad_por_unidad=1000,
                unidad_receta="g", costo_unidad_compra=Decimal("10"))

        with CaptureQueriesContext(connection) as ctx:
            self.client.get(reverse("panel_inventario"))
        consultas = [q["sql"] for q in ctx.captured_queries
                     if "inventario_ajusteinventario" in q["sql"]]
        self.assertEqual(len(consultas), 1, consultas)

    def test_el_mapa_y_la_propiedad_dicen_lo_mismo(self):
        """Las dos vías tienen que dar el número idéntico, o una miente."""
        from inventario.models import AjusteInventario
        ing = Ingrediente.objects.create(
            nombre="Fresas", unidad_compra="kg", cantidad_por_unidad=1000,
            unidad_receta="g", costo_unidad_compra=Decimal("100"))
        hoy = date.today()
        AjusteInventario.objects.create(
            fecha=hoy, ingrediente=ing, cantidad_calculada=Decimal("1000"),
            cantidad_real=Decimal("800"))                 # merma de 200
        AjusteInventario.objects.create(
            fecha=hoy, ingrediente=ing, cantidad_calculada=Decimal("800"),
            cantidad_real=Decimal("950"))                 # sobrante: no cuenta
        AjusteInventario.objects.create(
            fecha=hoy, ingrediente=ing, cantidad_calculada=Decimal("950"),
            cantidad_real=Decimal("900"))                 # merma de 50

        self.assertEqual(ing.merma_total, Decimal("250"))
        self.assertEqual(
            Ingrediente.mermas_por_ingrediente()[ing.pk], Decimal("250"))


class PresentacionEnLaCompraTests(TestCase):
    """El tamaño del paquete se captura en cada compra, no se va a editar aparte.

    Andy compra la misma crema de cacahuate de 790 g esta semana y de 1 kg la
    siguiente. Antes había que ir a editar el ingrediente, y como no se hacía,
    la capa quedaba con el tamaño equivocado.
    """

    def setUp(self):
        from django.contrib.auth.models import User
        User.objects.create_superuser("andy", "a@a.com", "pass")
        self.client.login(username="andy", password="pass")
        self.ing = Ingrediente.objects.create(
            nombre="Crema de cacahuate", unidad_compra="frasco",
            cantidad_por_unidad=Decimal("790"), unidad_receta="g",
            costo_unidad_compra=Decimal("100.00"))

    def _comprar(self, cantidad, costo, **extra):
        from django.urls import reverse
        datos = {"ingrediente": self.ing.pk, "cantidad": cantidad,
                 "costo_total": costo, "fecha": date(2026, 8, 16).isoformat()}
        datos.update(extra)
        resp = self.client.post(reverse("inventario_compra_agregar"), datos,
                                follow=True)
        return resp

    def test_el_contenido_capturado_manda_sobre_el_catalogo(self):
        self._comprar("1", "250", contenido_paquete="1000")
        compra = Compra.objects.latest("id")
        self.assertEqual(compra.cantidad_receta, Decimal("1000"))

    def test_el_catalogo_se_pone_al_dia(self):
        self._comprar("1", "250", contenido_paquete="1000")
        self.ing.refresh_from_db()
        self.assertEqual(self.ing.cantidad_por_unidad, Decimal("1000"))

    def test_el_costo_del_catalogo_sigue_sin_tocarse(self):
        """La regla de Andy: el catálogo es un aproximado y no entra a contabilidad.

        Se mueve el tamaño del paquete, que es un hecho de la compra. El costo
        no, aunque el costo por unidad de receta cambie como consecuencia.
        """
        self._comprar("1", "250", contenido_paquete="1000")
        self.ing.refresh_from_db()
        self.assertEqual(self.ing.costo_unidad_compra, Decimal("100.00"))

    def test_una_compra_vieja_no_se_mueve_al_cambiar_la_presentacion(self):
        """Cada capa congela lo suyo. Es la regla que sostiene el costeo FIFO."""
        self._comprar("1", "200")                       # 790 g, la de siempre
        vieja = Compra.objects.latest("id")
        self._comprar("1", "250", contenido_paquete="1000")

        vieja.refresh_from_db()
        self.assertEqual(vieja.cantidad_receta, Decimal("790"))
        # Y el stock total suma lo congelado, no los paquetes por el tamaño de
        # hoy: 790 + 1000, no 2 × 1000.
        self.assertEqual(self.ing.total_comprado, Decimal("1790"))

    def test_sin_contenido_se_usa_la_presentacion_de_siempre(self):
        """El admin, el seed y la API no mandan el campo; no pueden romperse."""
        self._comprar("2", "200")
        self.assertEqual(Compra.objects.latest("id").cantidad_receta,
                         Decimal("1580"))
        self.ing.refresh_from_db()
        self.assertEqual(self.ing.cantidad_por_unidad, Decimal("790"))

    def test_un_contenido_imposible_se_rechaza(self):
        for malo in ("0", "-5", "abc", "NaN", "Infinity"):
            with self.subTest(contenido=malo):
                antes = Compra.objects.count()
                resp = self._comprar("1", "200", contenido_paquete=malo)
                self.assertEqual(Compra.objects.count(), antes)
                self.assertEqual(resp.status_code, 200)

    def test_el_cambio_de_presentacion_se_avisa(self):
        resp = self._comprar("1", "250", contenido_paquete="1000")
        avisos = [str(m) for m in resp.context["messages"]]
        self.assertTrue(any("790" in a and "1,000" in a for a in avisos), avisos)

    def test_si_la_compra_falla_el_catalogo_no_se_queda_cambiado(self):
        """Juntas o ninguna: un catálogo movido sin su compra propone un tamaño
        que nadie compró."""
        from unittest.mock import patch
        with patch("inventario.views.Compra.objects.create",
                   side_effect=RuntimeError("truena al guardar")):
            with self.assertRaises(RuntimeError):
                self._comprar("1", "250", contenido_paquete="1000")

        self.ing.refresh_from_db()
        self.assertEqual(self.ing.cantidad_por_unidad, Decimal("790"))

    def test_los_campos_al_reves_no_envenenan_el_catalogo(self):
        """El error que NINGUNA comparación de costos puede ver.

        La previsualización mide costo ÷ (paquetes × contenido), y ese cociente
        no cambia al voltear los dos campos: la capa sale perfecta, la alarma
        calla y `recostear --verificar` sale sano. Lo único que se mueve es el
        tamaño del paquete, así que es lo que se vigila.
        """
        # 3 frascos de 790 g, capturados al revés: 790 frascos de 3 g.
        resp = self._comprar("790", "300", contenido_paquete="3")

        self.ing.refresh_from_db()
        self.assertEqual(self.ing.cantidad_por_unidad, Decimal("790"))

        avisos = [str(m) for m in resp.context["messages"]]
        self.assertTrue(any("al revés" in a for a in avisos), avisos)

    def test_la_capa_se_congela_con_lo_capturado_aunque_el_catalogo_no_cambie(self):
        """Rechazar la presentación no puede falsear lo que trajo la compra."""
        self._comprar("790", "300", contenido_paquete="3")
        self.assertEqual(Compra.objects.latest("id").cantidad_receta,
                         Decimal("2370"))          # 790 × 3, lo capturado

    def test_un_cambio_de_empaque_creible_si_pasa(self):
        """790 g a 1 kg es un empaque distinto; 790 g a 3 no lo es."""
        self._comprar("1", "250", contenido_paquete="1000")
        self.ing.refresh_from_db()
        self.assertEqual(self.ing.cantidad_por_unidad, Decimal("1000"))

    def test_un_contenido_que_se_redondea_a_cero_se_rechaza(self):
        """La columna guarda dos decimales: 0.004 pasaría el «mayor a 0».

        Y un catálogo en cero deja toda compra futura como capa vacía, así que
        ninguna venta de ese ingrediente se vuelve a poder costear.
        """
        antes = Compra.objects.count()
        self._comprar("1", "200", contenido_paquete="0.004")
        self.assertEqual(Compra.objects.count(), antes)
        self.ing.refresh_from_db()
        self.assertEqual(self.ing.cantidad_por_unidad, Decimal("790"))

    def test_el_aviso_no_redondea_una_presentacion_fraccionaria(self):
        self.ing.cantidad_por_unidad = Decimal("0.50")
        self.ing.save(update_fields=["cantidad_por_unidad"])
        resp = self._comprar("1", "10", contenido_paquete="1.5")
        avisos = [str(m) for m in resp.context["messages"]]
        self.assertTrue(any("0.50" in a and "1.50" in a for a in avisos), avisos)


class RecetaDesactivadaTests(TestCase):
    """Un producto que ya no está detiene la venta en vez de desaparecer.

    Antes la línea se caía del carrito sin decir nada: el cajero cobraba el
    total que la pantalla había mostrado y la nota salía con un producto menos,
    así que el cliente pagaba de menos y nadie se enteraba.
    """

    def setUp(self):
        from django.contrib.auth.models import User
        from contabilidad import posting
        posting.crear_catalogo()
        User.objects.create_superuser("andy", "a@a.com", "pass")
        self.client.login(username="andy", password="pass")
        ing = Ingrediente.objects.create(
            nombre="Leche", unidad_compra="litro", cantidad_por_unidad=1000,
            unidad_receta="ml", costo_unidad_compra=Decimal("30.00"))
        self.viva = Receta.objects.create(
            nombre="Shake", precio_venta=Decimal("130.00"))
        self.muerta = Receta.objects.create(
            nombre="Shake de temporada", precio_venta=Decimal("150.00"),
            activa=False)
        for r in (self.viva, self.muerta):
            RecetaIngrediente.objects.create(
                receta=r, ingrediente=ing, cantidad=200)

    def _vender(self, productos):
        import json
        from django.urls import reverse
        return self.client.post(reverse("inventario_venta_agregar"), {
            "productos_json": json.dumps(productos),
            "metodo_pago": "efectivo", "pago_con": "500",
            "nombre_cliente": "Andrea"}, follow=True)

    def test_la_venta_se_detiene_y_dice_cual(self):
        resp = self._vender([{"receta": self.viva.pk, "cantidad": 1},
                             {"receta": self.muerta.pk, "cantidad": 1}])
        self.assertEqual(Nota.objects.count(), 0)
        self.assertEqual(Venta.objects.count(), 0)
        avisos = [str(m) for m in resp.context["messages"]]
        self.assertTrue(any("Shake de temporada" in a for a in avisos), avisos)

    def test_no_se_cobra_de_menos_a_medias(self):
        """Detener entera es el punto: media venta es una venta mal cobrada."""
        self._vender([{"receta": self.viva.pk, "cantidad": 1},
                      {"receta": self.muerta.pk, "cantidad": 1}])
        self.assertFalse(Venta.objects.exists())

    def test_el_carrito_sano_se_cobra_igual(self):
        self._vender([{"receta": self.viva.pk, "cantidad": 2}])
        self.assertEqual(Nota.objects.get().total, Decimal("260.00"))


class MinimoSoloDeLoQueSeVendeTests(TestCase):
    """El semáforo de faltantes mide contra lo que de verdad se puede pedir."""

    def setUp(self):
        self.ing = Ingrediente.objects.create(
            nombre="Leche", unidad_compra="litro", cantidad_por_unidad=1000,
            unidad_receta="ml", costo_unidad_compra=Decimal("30.00"))
        self.viva = Receta.objects.create(
            nombre="Shake", precio_venta=Decimal("130.00"))
        RecetaIngrediente.objects.create(
            receta=self.viva, ingrediente=self.ing, cantidad=200)

    def test_una_receta_apagada_deja_de_exigir_stock(self):
        vieja = Receta.objects.create(
            nombre="De temporada", precio_venta=Decimal("150.00"))
        RecetaIngrediente.objects.create(
            receta=vieja, ingrediente=self.ing, cantidad=300)
        self.assertEqual(self.ing.minimo_para_cinco, Decimal("2500"))

        vieja.activa = False
        vieja.save(update_fields=["activa"])
        self.assertEqual(self.ing.minimo_para_cinco, Decimal("1000"))
