"""El costeo FIFO sobre capas persistidas."""
from datetime import date
from decimal import Decimal

from django.test import TestCase

from inventario import costeo
from inventario.models import (
    Compra, ConsumoCapa, Extra, Ingrediente, Receta, RecetaIngrediente,
    Venta, VentaExtra, VentaSustitucion)


class CosteoBase(TestCase):
    def setUp(self):
        self.leche = Ingrediente.objects.create(
            nombre="Leche", unidad_compra="litro",
            cantidad_por_unidad=Decimal("1000"), unidad_receta="ml",
            costo_unidad_compra=Decimal("999"))   # catálogo alto a propósito:
        self.receta = Receta.objects.create(      # si se colara, se notaría
            nombre="Shake", precio_venta=Decimal("100"))
        RecetaIngrediente.objects.create(
            receta=self.receta, ingrediente=self.leche, cantidad=Decimal("200"))

    def _compra(self, dia, litros, monto):
        return Compra.objects.create(
            fecha=date(2026, 8, dia), ingrediente=self.leche,
            cantidad=Decimal(str(litros)), monto_total=Decimal(monto))

    def _venta(self, dia, cantidad=1, **extra):
        return Venta.objects.create(
            fecha=date(2026, 8, dia), receta=self.receta,
            cantidad=cantidad, **extra)


class CapasTests(CosteoBase):
    def test_una_compra_nace_como_capa_llena(self):
        compra = self._compra(1, 2, "40")
        self.assertEqual(compra.saldo_receta, Decimal("2000"))

    def test_la_venta_consume_la_capa_y_baja_su_saldo(self):
        compra = self._compra(1, 2, "40")          # 2000 ml por $40 → 0.02/ml
        venta = self._venta(2, cantidad=3)         # 600 ml
        costeo.costear_venta(venta)

        compra.refresh_from_db()
        venta.refresh_from_db()
        self.assertEqual(venta.costo_fifo, Decimal("12.00"))
        self.assertFalse(venta.costo_incompleto)
        self.assertEqual(compra.saldo_receta, Decimal("1400"))

    def test_consume_primero_la_capa_mas_vieja(self):
        vieja = self._compra(1, 1, "20")           # 1000 ml a 0.02
        nueva = self._compra(2, 1, "50")           # 1000 ml a 0.05
        venta = self._venta(3, cantidad=6)         # 1200 ml: 1000 + 200

        costeo.costear_venta(venta)
        venta.refresh_from_db()
        # 1000×0.02 + 200×0.05 = 20 + 10
        self.assertEqual(venta.costo_fifo, Decimal("30.00"))
        vieja.refresh_from_db(); nueva.refresh_from_db()
        self.assertEqual(vieja.saldo_receta, Decimal("0"))
        self.assertEqual(nueva.saldo_receta, Decimal("800"))

    def test_no_usa_capas_posteriores_a_la_venta(self):
        self._compra(10, 1, "20")                  # llega después
        venta = self._venta(5)
        costeo.costear_venta(venta)
        venta.refresh_from_db()
        self.assertEqual(venta.costo_fifo, Decimal("0.00"))
        self.assertTrue(venta.costo_incompleto)


class SinCapasTests(CosteoBase):
    def test_sin_compras_no_se_inventa_costo(self):
        venta = self._venta(5)
        costeo.costear_venta(venta)
        venta.refresh_from_db()
        # El catálogo diría 200 × 0.999 = 199.80; no se usa.
        self.assertEqual(venta.costo_fifo, Decimal("0.00"))
        self.assertTrue(venta.costo_incompleto)

    def test_el_faltante_queda_registrado_sin_capa(self):
        self._compra(1, Decimal("0.1"), "2")       # solo 100 ml
        venta = self._venta(2)                     # pide 200 ml
        costeo.costear_venta(venta)

        sin_capa = ConsumoCapa.objects.filter(venta=venta, compra__isnull=True)
        self.assertEqual(sin_capa.count(), 1)
        self.assertEqual(sin_capa.first().cantidad_receta, Decimal("100"))
        self.assertEqual(sin_capa.first().importe, Decimal("0"))
        venta.refresh_from_db()
        self.assertEqual(venta.costo_fifo, Decimal("2.00"))   # solo lo que había
        self.assertTrue(venta.costo_incompleto)


class RecosteoTests(CosteoBase):
    def test_una_compra_retroactiva_recuesta_la_venta(self):
        """El caso del mostrador: se vende en la mañana, la factura llega después."""
        venta = self._venta(5)
        costeo.costear_venta(venta)
        self.assertTrue(Venta.objects.get(pk=venta.pk).costo_incompleto)

        self._compra(5, 1, "20")                   # misma fecha, capturada después
        costeo.recostear_desde(self.leche.pk, date(2026, 8, 5))

        venta.refresh_from_db()
        self.assertEqual(venta.costo_fifo, Decimal("4.00"))   # 200 ml × 0.02
        self.assertFalse(venta.costo_incompleto)

    def test_costear_dos_veces_no_duplica_el_consumo(self):
        compra = self._compra(1, 1, "20")
        venta = self._venta(2)

        costeo.costear_venta(venta)
        costeo.costear_venta(venta)

        compra.refresh_from_db()
        self.assertEqual(compra.saldo_receta, Decimal("800"))   # no 600
        self.assertEqual(ConsumoCapa.objects.filter(venta=venta).count(), 1)

    def test_descostear_devuelve_el_saldo_a_la_capa(self):
        compra = self._compra(1, 1, "20")
        venta = self._venta(2)
        costeo.costear_venta(venta)

        costeo.descostear_venta(venta)
        compra.refresh_from_db()
        venta.refresh_from_db()
        self.assertEqual(compra.saldo_receta, Decimal("1000"))  # entera otra vez
        self.assertIsNone(venta.costo_fifo)
        self.assertEqual(ConsumoCapa.objects.filter(venta=venta).count(), 0)

    def test_la_capa_liberada_surte_a_la_siguiente_venta(self):
        compra = self._compra(1, 1, "20")
        primera = self._venta(2, cantidad=5)       # 1000 ml: se lleva todo
        costeo.costear_venta(primera)
        segunda = self._venta(3)
        costeo.costear_venta(segunda)
        self.assertTrue(Venta.objects.get(pk=segunda.pk).costo_incompleto)

        costeo.descostear_venta(primera)
        costeo.costear_venta(segunda)

        segunda.refresh_from_db()
        self.assertEqual(segunda.costo_fifo, Decimal("4.00"))
        self.assertFalse(segunda.costo_incompleto)
        compra.refresh_from_db()
        self.assertEqual(compra.saldo_receta, Decimal("800"))


class ConsumoCompletoTests(CosteoBase):
    def test_cuenta_sustituciones_y_extras(self):
        cafe = Ingrediente.objects.create(
            nombre="Café", unidad_compra="kg", cantidad_por_unidad=Decimal("1000"),
            unidad_receta="g", costo_unidad_compra=Decimal("500"))
        almendra = Ingrediente.objects.create(
            nombre="Almendra", unidad_compra="litro",
            cantidad_por_unidad=Decimal("1000"), unidad_receta="ml",
            costo_unidad_compra=Decimal("500"))
        Compra.objects.create(fecha=date(2026, 8, 1), ingrediente=cafe,
                              cantidad=Decimal("1"), monto_total=Decimal("100"))
        Compra.objects.create(fecha=date(2026, 8, 1), ingrediente=almendra,
                              cantidad=Decimal("1"), monto_total=Decimal("30"))
        self._compra(1, 1, "20")

        shot = Extra.objects.create(nombre="Shot", ingrediente=cafe,
                                    cantidad=Decimal("10"), cargo=Decimal("12"))
        venta = self._venta(2)
        VentaSustitucion.objects.create(
            venta=venta, ingrediente_original=self.leche,
            ingrediente_nuevo=almendra)
        VentaExtra.objects.create(venta=venta, extra=shot, cantidad=1)

        costeo.costear_venta(venta)
        venta.refresh_from_db()
        # 200 ml de almendra a 0.03 = 6.00, más 10 g de café a 0.10 = 1.00.
        # La leche no entra: fue sustituida.
        self.assertEqual(venta.costo_fifo, Decimal("7.00"))
        self.assertFalse(venta.costo_incompleto)


class BorrarYEditarTests(CosteoBase):
    """Borrar o editar una venta deshace lo que esa venta movió, sin ayuda.

    Y no solo lo suyo: con FIFO, liberar una capa vieja cambia el costo de todas
    las ventas que vinieron después.
    """

    def test_borrar_una_venta_devuelve_el_inventario(self):
        compra = self._compra(1, 1, "20")
        venta = self._venta(2, cantidad=2)      # 400 ml
        self.assertEqual(Compra.objects.get(pk=compra.pk).saldo_receta,
                         Decimal("600"))

        venta.delete()

        self.assertEqual(Compra.objects.get(pk=compra.pk).saldo_receta,
                         Decimal("1000"))       # entera otra vez
        self.assertEqual(ConsumoCapa.objects.count(), 0)

    def test_borrar_una_venta_recuesta_las_posteriores(self):
        """El ejemplo de las tres leches: $20, $22 y $23."""
        barata = self._compra(1, 1, "20")
        media = self._compra(2, 1, "22")
        cara = self._compra(3, 1, "23")

        primera = self._venta(5, cantidad=5)    # 1000 ml: la leche de $20
        segunda = self._venta(6, cantidad=5)    # 1000 ml: la de $22
        tercera = self._venta(7, cantidad=5)    # 1000 ml: la de $23
        self.assertEqual(Venta.objects.get(pk=tercera.pk).costo_fifo,
                         Decimal("23.00"))

        segunda.delete()

        # La leche de $22 volvió a ser la más vieja disponible, así que le toca
        # a la tercera venta, que baja de $23 a $22.
        self.assertEqual(Venta.objects.get(pk=tercera.pk).costo_fifo,
                         Decimal("22.00"))
        self.assertEqual(Compra.objects.get(pk=media.pk).saldo_receta,
                         Decimal("0"))
        self.assertEqual(Compra.objects.get(pk=cara.pk).saldo_receta,
                         Decimal("1000"))       # la cara queda sin tocar
        self.assertEqual(Compra.objects.get(pk=barata.pk).saldo_receta,
                         Decimal("0"))

    def test_una_venta_creada_fuera_de_la_caja_queda_costeada(self):
        self._compra(1, 1, "20")
        venta = Venta.objects.create(
            fecha=date(2026, 8, 2), receta=self.receta, cantidad=1)
        # Nadie llamó a costear_venta: lo hizo la señal.
        self.assertEqual(Venta.objects.get(pk=venta.pk).costo_fifo,
                         Decimal("4.00"))

    def test_editar_la_cantidad_recuesta_la_venta(self):
        compra = self._compra(1, 1, "20")
        venta = self._venta(2, cantidad=1)      # 200 ml → $4
        self.assertEqual(Venta.objects.get(pk=venta.pk).costo_fifo,
                         Decimal("4.00"))

        venta.cantidad = 3                      # ahora 600 ml → $12
        venta.save()

        self.assertEqual(Venta.objects.get(pk=venta.pk).costo_fifo,
                         Decimal("12.00"))
        self.assertEqual(Compra.objects.get(pk=compra.pk).saldo_receta,
                         Decimal("400"))        # 1000 − 600, no 1000 − 800

    def test_borrar_una_compra_deja_incompletas_las_ventas_que_surtia(self):
        compra = self._compra(1, 1, "20")
        venta = self._venta(2, cantidad=1)
        self.assertFalse(Venta.objects.get(pk=venta.pk).costo_incompleto)

        compra.delete()

        recargada = Venta.objects.get(pk=venta.pk)
        self.assertEqual(recargada.costo_fifo, Decimal("0.00"))
        self.assertTrue(recargada.costo_incompleto)


class CostoDeVentasTests(CosteoBase):
    """La propiedad puente que leen los paneles comerciales."""

    def test_usa_el_costo_fifo_cuando_la_venta_esta_costeada(self):
        self._compra(1, 1, "20")               # 1000 ml a 0.02
        venta = self._venta(2, cantidad=1)     # 200 ml → $4
        recargada = Venta.objects.get(pk=venta.pk)

        self.assertEqual(recargada.costo_fifo, Decimal("4.00"))
        self.assertEqual(recargada.costo_de_ventas, Decimal("4.00"))
        # El catálogo dice 999/litro: si se colara, el costo sería 199.80.
        self.assertEqual(recargada.costo_total, Decimal("199.80"))

    def test_sin_capas_cae_al_estimado_del_catalogo(self):
        venta = self._venta(2, cantidad=1)     # ninguna compra que la surta
        recargada = Venta.objects.get(pk=venta.pk)

        self.assertTrue(recargada.costo_incompleto)
        self.assertEqual(recargada.costo_fifo, Decimal("0.00"))
        # Cero es «no se sabe», no «salió gratis»: el panel muestra el estimado.
        self.assertEqual(recargada.costo_de_ventas, Decimal("199.80"))

    def test_costo_parcial_tambien_cae_al_estimado(self):
        self._compra(1, Decimal("0.1"), "2")   # 100 ml: alcanza para la mitad
        venta = self._venta(2, cantidad=1)     # necesita 200 ml
        recargada = Venta.objects.get(pk=venta.pk)

        self.assertTrue(recargada.costo_incompleto)
        self.assertEqual(recargada.costo_fifo, Decimal("2.00"))
        # Publicar 2.00 como costo de la venta la haría ver el doble de rentable.
        self.assertEqual(recargada.costo_de_ventas, Decimal("199.80"))

    def test_la_ganancia_se_calcula_contra_el_costo_real(self):
        self._compra(1, 1, "20")
        venta = self._venta(2, cantidad=1)     # ingreso 100, costo real 4
        self.assertEqual(Venta.objects.get(pk=venta.pk).ganancia,
                         Decimal("96.00"))
