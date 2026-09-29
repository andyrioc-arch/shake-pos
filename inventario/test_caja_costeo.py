"""Registrar una venta deja el costeo igual que antes, con una fracción del trabajo.

La caja costeaba cada venta nueva una y otra vez: al crear la línea, al crear
cada extra y cada sustitución, y otra más al cerrar la nota, y cada una de
esas veces recosteaba el día entero una vez por ingrediente. El resultado era
correcto; el problema eran las decenas de miles de consultas.

Estas pruebas fijan que el resultado no cambió. `como_antes` reproduce paso a
paso la secuencia que corría la caja, y cada escenario se registra dos veces
sobre el mismo estado —una por cada camino, deshaciendo entre las dos— para
comparar todo lo que el costeo deja escrito: costo, bandera de incompleto,
cada `ConsumoCapa`, el saldo de cada capa, las mermas y los asientos.
"""
import json
from contextlib import contextmanager
from datetime import date
from decimal import Decimal
from unittest import mock

from django.contrib.auth.models import User
from django.db import connection, transaction
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from contabilidad import posting
from contabilidad.models import Asiento, Movimiento
from inventario import costeo
from inventario.models import (
    AjusteInventario, Compra, Extra, Ingrediente, Receta, RecetaIngrediente,
    Venta, VentaExtra, VentaSustitucion)


def _dia(n):
    return date(2026, 8, n)


def _n(valor):
    """Decimal comparable sin importar cuántos ceros traiga de la base."""
    return None if valor is None else format(Decimal(valor).normalize(), "f")


class _Deshacer(Exception):
    pass


def foto():
    """Todo lo que el costeo escribe, sin depender de los ids de lo nuevo."""
    def consumos(qs):
        return sorted((c.ingrediente_id, c.compra_id or 0, _n(c.cantidad_receta),
                       _n(c.costo_unitario), _n(c.importe)) for c in qs)

    def asientos(venta):
        mov = Movimiento.objects.filter(venta=venta).first()
        if not mov:
            return []
        base = f"Mov #{mov.pk}"
        return sorted(
            (a.referencia[len(base):], str(a.fecha),
             tuple(sorted((m.cuenta.codigo, _n(m.debe), _n(m.haber))
                          for m in a.movimientos.all())))
            for a in Asiento.objects.filter(
                referencia__in=[base, f"{base} flujo", f"{base} reconocimiento"]))

    ventas = [
        (str(v.fecha), v.receta_id, v.cantidad, v.es_cortesia, _n(v.costo_fifo),
         v.costo_incompleto, consumos(v.consumos.all()), asientos(v))
        for v in Venta.objects.order_by("fecha", "id")]
    capas = sorted((c.pk, _n(c.saldo_receta)) for c in Compra.objects.all())
    mermas = [
        (a.pk, _n(a.costo), a.costo_incompleto, consumos(a.consumos.all()),
         [(m.cuenta.codigo, _n(m.debe), _n(m.haber))
          for asiento in Asiento.objects.filter(referencia=f"Merma #{a.pk}")
          for m in asiento.movimientos.order_by("pk")])
        for a in AjusteInventario.objects.order_by("pk")]
    return json.loads(json.dumps(
        {"ventas": ventas, "capas": capas, "mermas": mermas}))


@contextmanager
def contar_costeos():
    with mock.patch.object(costeo, "costear_venta",
                           wraps=costeo.costear_venta) as espia:
        yield espia


class EscenarioCaja(TestCase):
    """Un catálogo con capas escasas y a precios distintos, y una historia.

    Las capas escasas son a propósito: si la leche o la avena alcanzaran para
    todo, cualquier orden de costeo daría el mismo número y la prueba no
    distinguiría nada.
    """

    def setUp(self):
        posting.crear_catalogo()
        User.objects.create_superuser("andy", "a@a.com", "pass")
        self.client.login(username="andy", password="pass")

        def ing(nombre, unidad="g"):
            return Ingrediente.objects.create(
                nombre=nombre, unidad_compra="kg", cantidad_por_unidad=1000,
                unidad_receta=unidad, costo_unidad_compra=Decimal("999"))

        self.leche = ing("Leche", "ml")
        self.proteina = ing("Proteína")
        self.platano = ing("Plátano")
        self.avena = ing("Avena")
        self.cacahuate = ing("Crema de cacahuate")
        self.almendra = ing("Almendra", "ml")
        self.cafe = ing("Café")

        self.shake = Receta.objects.create(nombre="Shake", precio_venta=100)
        for i, cantidad in ((self.leche, 200), (self.proteina, 30),
                            (self.platano, 100), (self.avena, 40),
                            (self.cacahuate, 15)):
            RecetaIngrediente.objects.create(
                receta=self.shake, ingrediente=i, cantidad=cantidad)
        self.americano = Receta.objects.create(nombre="Americano",
                                               precio_venta=45)
        RecetaIngrediente.objects.create(
            receta=self.americano, ingrediente=self.cafe, cantidad=18)
        self.agua = Receta.objects.create(nombre="Agua", precio_venta=20)
        self.shot = Extra.objects.create(nombre="Shot", ingrediente=self.cafe,
                                         cantidad=10, cargo=12)

        for dia, i, kilos, monto in (
                (1, self.leche, "2", "40"), (3, self.leche, "1", "22"),
                (6, self.leche, "1", "25"), (1, self.proteina, "1", "450"),
                (2, self.platano, "1", "30"), (1, self.avena, "0.5", "20"),
                (4, self.avena, "0.2", "11"), (1, self.cacahuate, "0.2", "30"),
                (2, self.almendra, "1", "60"), (1, self.cafe, "0.25", "100"),
                (5, self.cafe, "0.25", "120")):
            Compra.objects.create(fecha=_dia(dia), ingrediente=i,
                                  cantidad=Decimal(kilos),
                                  monto_total=Decimal(monto))

    def historia(self, por_dia=((2, 2), (4, 2), (5, 3), (7, 3), (9, 2),
                                (10, 8))):
        """Ventas previas, capturadas como las captura el admin: por el ORM."""
        n = 0
        for dia, cuantas in por_dia:
            for _ in range(cuantas):
                n += 1
                receta = self.americano if n % 4 == 0 else self.shake
                venta = Venta.objects.create(fecha=_dia(dia), receta=receta,
                                             cantidad=1 + n % 2)
                if n % 5 == 0:
                    VentaExtra.objects.create(venta=venta, extra=self.shot,
                                              cantidad=1)
                if n % 7 == 0 and receta == self.shake:
                    VentaSustitucion.objects.create(
                        venta=venta, ingrediente_original=self.leche,
                        ingrediente_nuevo=self.almendra)

    # ── Los dos caminos ─────────────────────────────────────────────────────
    def vender(self, productos, fecha, cortesia=False):
        datos = {
            "productos_json": json.dumps(productos),
            "fecha": fecha.isoformat(), "metodo_pago": "efectivo",
            "pago_con": "5000", "nombre_cliente": "Andrea"}
        if cortesia:
            datos.update(cortesia="1", motivo_cortesia="Activación")
        resp = self.client.post(reverse("inventario_venta_agregar"), datos)
        self.assertEqual(resp.status_code, 302)

    def como_antes(self, productos, fecha, cortesia=False):
        """La secuencia que corría la caja antes, paso por paso.

        Cada objeto que se guardaba disparaba su señal —costear la venta y
        recostear desde su fecha un ingrediente a la vez—, y al cerrar la nota
        la vista volvía a costear cada línea.
        """
        def al_guardar(venta):
            costeo.costear_venta(venta)
            ids = (set(venta.consumos.values_list("ingrediente_id", flat=True))
                   or set(venta.consumo_ingredientes()))
            for ingrediente_id in sorted(ids):
                costeo.recostear_desde(ingrediente_id, venta.fecha)

        creadas = []
        with costeo.diferido():
            for p in productos:
                venta = Venta.objects.create(
                    fecha=fecha, receta_id=p["receta"],
                    cantidad=p.get("cantidad", 1), metodo_pago="efectivo",
                    es_cortesia=cortesia, descuento_pct=Decimal("0"))
                al_guardar(venta)
                for orig, nuevo in p.get("subs", []):
                    VentaSustitucion.objects.create(
                        venta=venta, ingrediente_original_id=orig,
                        ingrediente_nuevo_id=nuevo)
                    al_guardar(Venta.objects.get(pk=venta.pk))
                for extra, cantidad in p.get("addons", []):
                    VentaExtra.objects.create(venta=venta, extra_id=extra,
                                              cantidad=cantidad)
                    al_guardar(Venta.objects.get(pk=venta.pk))
                creadas.append(venta)
        for venta in creadas:
            costeo.costear_venta(venta)

    def ensayo(self, registrar):
        """Corre `registrar`, toma la foto y deshace todo."""
        try:
            with transaction.atomic():
                registrar()
                raise _Deshacer(foto())
        except _Deshacer as e:
            return e.args[0]

    def assertIgualQueAntes(self, productos, fecha, cortesia=False):
        previa = foto()
        antes = self.ensayo(lambda: self.como_antes(productos, fecha, cortesia))
        ahora = self.ensayo(lambda: self.vender(productos, fecha, cortesia))
        self.assertEqual(foto(), previa)            # el ensayo no dejó rastro
        self.assertEqual(len(ahora["ventas"]),
                         len(previa["ventas"]) + len(productos))
        self.assertEqual(ahora, antes)
        return previa, ahora


class CajaIgualQueAntesTests(EscenarioCaja):
    def test_venta_de_hoy_sin_posteriores(self):
        self.historia()
        previa, ahora = self.assertIgualQueAntes(
            [{"receta": self.shake.pk, "cantidad": 1,
              "addons": [[self.shot.pk, 1]]}], _dia(10))
        # Nadie más cambió: solo se agregó la venta nueva al final.
        self.assertEqual(ahora["ventas"][:-1], previa["ventas"])
        self.assertIsNotNone(ahora["ventas"][-1][4])

    def test_venta_con_fecha_pasada_les_quita_capas_a_las_posteriores(self):
        self.historia()
        previa, ahora = self.assertIgualQueAntes(
            [{"receta": self.shake.pk, "cantidad": 3}], _dia(3))
        # El escenario de verdad mueve a las posteriores; si no, no prueba nada.
        posteriores = [v for v in previa["ventas"] if v[0] > "2026-08-03"]
        despues = [v for v in ahora["ventas"] if v[0] > "2026-08-03"]
        self.assertNotEqual(despues, posteriores)
        nueva = next(v for v in ahora["ventas"] if v[0] == "2026-08-03")
        self.assertFalse(nueva[5])                  # la nueva se surtió entera

    def test_extras_sustituciones_y_varias_lineas(self):
        self.historia()
        self.assertIgualQueAntes(
            [{"receta": self.shake.pk, "cantidad": 2,
              "subs": [[self.leche.pk, self.almendra.pk]],
              "addons": [[self.shot.pk, 2]]},
             {"receta": self.americano.pk, "cantidad": 2,
              "addons": [[self.shot.pk, 1]]},
             {"receta": self.agua.pk, "cantidad": 1}], _dia(5))

    def test_capas_insuficientes_dejan_la_venta_incompleta(self):
        self.historia()
        _, ahora = self.assertIgualQueAntes(
            [{"receta": self.shake.pk, "cantidad": 9}], _dia(10))
        self.assertTrue(ahora["ventas"][-1][5])

    def test_cortesia_con_fecha_pasada(self):
        self.historia()
        self.assertIgualQueAntes(
            [{"receta": self.shake.pk, "cantidad": 2}], _dia(6), cortesia=True)

    def test_sin_historia(self):
        self.assertIgualQueAntes(
            [{"receta": self.shake.pk, "cantidad": 1},
             {"receta": self.shake.pk, "cantidad": 1,
              "addons": [[self.shot.pk, 1]]}], _dia(4))

    def test_con_una_merma_posterior(self):
        self.historia()
        AjusteInventario.objects.create(
            fecha=_dia(8), ingrediente=self.leche,
            cantidad_calculada=Decimal("500"), cantidad_real=Decimal("100"))
        previa, _ = self.assertIgualQueAntes(
            [{"receta": self.shake.pk, "cantidad": 2}], _dia(4))
        self.assertIsNotNone(previa["mermas"][0][1])

    def test_una_venta_capturada_fuera_de_la_caja(self):
        """El admin y el shell no pasan por la caja: sus señales cuestan solas."""
        self.historia()
        previa = foto()

        def por_el_orm():
            venta = Venta.objects.create(fecha=_dia(4), receta=self.shake,
                                         cantidad=2)
            VentaSustitucion.objects.create(
                venta=venta, ingrediente_original=self.leche,
                ingrediente_nuevo=self.almendra)
            VentaExtra.objects.create(venta=venta, extra=self.shot, cantidad=1)

        def antes():
            def al_guardar(venta):
                costeo.costear_venta(venta)
                ids = (set(venta.consumos.values_list("ingrediente_id",
                                                      flat=True))
                       or set(venta.consumo_ingredientes()))
                for ingrediente_id in sorted(ids):
                    costeo.recostear_desde(ingrediente_id, venta.fecha)

            with costeo.diferido():
                venta = Venta.objects.create(fecha=_dia(4), receta=self.shake,
                                             cantidad=2)
                al_guardar(venta)
                VentaSustitucion.objects.create(
                    venta=venta, ingrediente_original=self.leche,
                    ingrediente_nuevo=self.almendra)
                al_guardar(Venta.objects.get(pk=venta.pk))
                VentaExtra.objects.create(venta=venta, extra=self.shot,
                                          cantidad=1)
                al_guardar(Venta.objects.get(pk=venta.pk))

        self.assertEqual(self.ensayo(por_el_orm), self.ensayo(antes))
        self.assertEqual(foto(), previa)


class CajaSinRecosteoRepetidoTests(EscenarioCaja):
    """La venta nueva se cuesta una vez; el día que ya lleva no se vuelve a tocar."""

    def _costeos_al_vender(self, productos, fecha):
        with contar_costeos() as espia:
            self.vender(productos, fecha)
        return espia.call_count

    def test_venta_de_hoy_se_cuesta_una_sola_vez(self):
        self.historia()
        veces = self._costeos_al_vender(
            [{"receta": self.shake.pk, "cantidad": 1,
              "addons": [[self.shot.pk, 1]]}], _dia(10))
        self.assertEqual(veces, 1)

    def test_una_linea_por_producto(self):
        self.historia()
        veces = self._costeos_al_vender(
            [{"receta": self.shake.pk}, {"receta": self.americano.pk},
             {"receta": self.agua.pk}], _dia(10))
        self.assertEqual(veces, 3)

    def test_no_crece_con_las_ventas_del_dia(self):
        producto = [{"receta": self.shake.pk, "addons": [[self.shot.pk, 1]]}]
        # Capas de sobra: si la venta encontrara la leche agotada con veinte
        # ventas y no con cinco, haría menos consultas por eso y no por el día.
        for i in (self.leche, self.proteina, self.platano, self.avena,
                  self.cacahuate, self.cafe):
            Compra.objects.create(fecha=_dia(1), ingrediente=i,
                                  cantidad=Decimal("100"),
                                  monto_total=Decimal("1000"))
        self.historia(por_dia=((10, 5),))
        self.vender(producto, _dia(10))     # la primera llena cachés del admin
        with CaptureQueriesContext(connection) as con_pocas:
            pocas = self._costeos_al_vender(producto, _dia(10))
        self.historia(por_dia=((10, 20),))
        with CaptureQueriesContext(connection) as con_muchas:
            muchas = self._costeos_al_vender(producto, _dia(10))
        self.assertEqual(pocas, muchas)
        self.assertEqual(len(con_pocas), len(con_muchas))

    def test_fecha_pasada_solo_recuesta_lo_que_viene_despues(self):
        self.historia()
        posteriores = Venta.objects.filter(fecha__gt=_dia(7),
                                           receta=self.shake).count()
        veces = self._costeos_al_vender([{"receta": self.shake.pk}], _dia(7))
        # La nueva y los shakes de días posteriores. Las del día 7 que ya
        # estaban tienen id menor y la nueva no les libera ninguna capa; los
        # americanos no comparten ingrediente con ella.
        self.assertEqual(veces, 1 + posteriores)


class DiferidoTests(TestCase):
    def test_se_restablece_al_salir_aunque_truene(self):
        self.assertFalse(costeo.esta_diferido())
        with self.assertRaises(ValueError):
            with costeo.diferido():
                self.assertTrue(costeo.esta_diferido())
                raise ValueError
        self.assertFalse(costeo.esta_diferido())

    def test_anidado(self):
        with costeo.diferido():
            with costeo.diferido():
                self.assertTrue(costeo.esta_diferido())
            self.assertTrue(costeo.esta_diferido())
        self.assertFalse(costeo.esta_diferido())
