from decimal import Decimal
from datetime import date
from django.test import TestCase
from inventario.models import (
    Ingrediente, Receta, RecetaIngrediente, Compra, Venta,
    Extra, VentaSustitucion, VentaExtra,
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
