"""Un día de uso, de punta a punta y por HTTP.

Los 294 tests del repo prueban cada pieza por separado y lo hacen bien. Lo que
no existía era la prueba de la SECUENCIA: abrir la caja, cobrar sin tener las
compras capturadas, capturar la factura del proveedor a media tarde, cobrar un
carrito con sustitución y add-on, regalar una cortesía, acumular puntos, canjear
un premio, registrar la renta y cerrar mirando los reportes. Cada paso deja el
sistema en el estado del que parte el siguiente, y varias de las reglas del
proyecto —el ingreso y su costo entran juntos, el inventario nunca queda en
negativo— solo se pueden afirmar sobre esa secuencia completa.

Por eso es UN método largo y no diez cortos: partirlo en métodos con `setUp`
compartido vuelve a probar piezas aisladas, que es justo lo que ya está hecho.

Todo entra por la URL que usa el mostrador, no por el ORM. Una venta creada con
`Venta.objects.create()` prueba el modelo; lo que mañana se va a apretar es el
formulario.
"""
from datetime import timedelta
from decimal import ROUND_HALF_UP, Decimal
from io import StringIO

from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from contabilidad import posting
from contabilidad.models import Asiento, Movimiento, MovimientoContable
from contabilidad.views import XLSX_MIME
from inventario.models import (
    Compra, ConsumoCapa, Extra, Ingrediente, Nota, Receta, RecetaIngrediente,
    Venta,
)
from lealtad import servicios
from lealtad.models import Canje, Cliente, Nivel, Premio

import json


def _mensajes(respuesta):
    """Los mensajes de ESTA petición (pide `follow=True` al hacerla).

    Sin seguir el redirect, los mensajes se acumulan de una petición a otra: en
    un test que hace veinte, cualquier comparación exacta sería basura.
    """
    return [str(m) for m in respuesta.context["messages"]]


class DiaDeUsoTests(TestCase):
    """El recorrido completo de un día de mostrador."""

    def setUp(self):
        # El catálogo contable tiene que existir ANTES del primer asiento. Si no,
        # `_cuenta_segura()` crea la 506 suelta, sin padre, y el desglose de
        # gastos la reporta como grupo propio en vez de dentro de la 504: el test
        # fallaría por el montaje y parecería un bug de contabilidad.
        posting.crear_catalogo()

        # La fecha es HOY y no una fija a propósito: `entregar_canje` fecha la
        # cortesía del premio con `timezone.localdate()`, así que con una fecha
        # inventada el costo del premio caería en otro mes y desaparecería del
        # estado de resultados que este test consulta.
        self.hoy = timezone.localdate()

        self.andy = User.objects.create_superuser(
            "andy", "andy@shake.mx", "clave-de-prueba")
        # El cajero es staff pero NO superusuario: es la cuenta que de verdad
        # va a operar la caja.
        self.cajero = User.objects.create_user(
            "caja", "caja@shake.mx", "clave-de-prueba", is_staff=True)

        self.c_andy = self.client_class()
        self.c_andy.force_login(self.andy)
        self.c_caja = self.client_class()
        self.c_caja.force_login(self.cajero)
        self.c_publico = self.client_class()

        self.leche = Ingrediente.objects.create(
            nombre="Leche", categoria="lacteo", unidad_compra="litro",
            cantidad_por_unidad=1000, unidad_receta="ml",
            costo_unidad_compra=Decimal("30"))
        self.fresa = Ingrediente.objects.create(
            nombre="Fresa", categoria="fruta", unidad_compra="kg",
            cantidad_por_unidad=1000, unidad_receta="g",
            costo_unidad_compra=Decimal("80"))
        self.almendra = Ingrediente.objects.create(
            nombre="Leche de almendra", categoria="lacteo",
            unidad_compra="litro", cantidad_por_unidad=1000,
            unidad_receta="ml", costo_unidad_compra=Decimal("60"))

        self.shake = Receta.objects.create(
            nombre="Shake fresa", emoji="🍓", precio_venta=Decimal("130"))
        RecetaIngrediente.objects.create(
            receta=self.shake, ingrediente=self.leche, cantidad=Decimal("200"))
        RecetaIngrediente.objects.create(
            receta=self.shake, ingrediente=self.fresa, cantidad=Decimal("100"))

        self.latte = Receta.objects.create(
            nombre="Latte", emoji="☕", precio_venta=Decimal("50"))
        RecetaIngrediente.objects.create(
            receta=self.latte, ingrediente=self.leche, cantidad=Decimal("150"))

        # Un producto desactivado: Andy lo saca del menú a media mañana.
        self.viejo = Receta.objects.create(
            nombre="Descontinuado", precio_venta=Decimal("70"), activa=False)
        RecetaIngrediente.objects.create(
            receta=self.viejo, ingrediente=self.leche, cantidad=Decimal("100"))

        # El add-on NO se crea: la migración 0003 ya lo trae, con su propio
        # ingrediente en «porciones». Se usa el de producción a propósito.
        self.espresso = Extra.objects.get(nombre="Shot de espresso")
        self.ing_espresso = self.espresso.ingrediente

        self.oro = Nivel.objects.create(nombre="Oro", puntos_requeridos=20)
        # El premio con receta y el premio sin ella. El segundo es el caso real
        # de producción: "Latte gratis" no tiene producto ligado.
        self.premio_latte = Premio.objects.create(
            nombre="Latte gratis", puntos_requeridos=10,
            tipo=Premio.Tipo.PRODUCTO, receta=self.latte, cantidad=1,
            vigencia_dias=30)
        self.premio_huerfano = Premio.objects.create(
            nombre="Postre gratis", puntos_requeridos=5,
            tipo=Premio.Tipo.PRODUCTO, receta=None, vigencia_dias=30)

    # ── Utilidades del guion ────────────────────────────────────────────────
    def _vender(self, productos, cliente=None, **extra):
        """POST al formulario de la caja, tal como lo manda el navegador."""
        datos = {
            "productos_json": json.dumps(productos),
            "fecha": self.hoy.isoformat(),
            "metodo_pago": "efectivo",
            "nombre_cliente": "Andrea",
        }
        datos.update(extra)
        return (cliente or self.c_caja).post(
            reverse("inventario_venta_agregar"), datos)

    def _comprar(self, ingrediente, cantidad, monto):
        return self.c_andy.post(reverse("inventario_compra_agregar"), {
            "ingrediente": ingrediente.pk, "cantidad": cantidad,
            "costo_total": monto, "fecha": self.hoy.isoformat(),
            "proveedor": "Central de abastos",
        })

    def _reportes(self):
        return posting.estado_resultados(self.hoy.year, self.hoy.month)

    # ══════════════════════════════════════════════════════════════════════
    def test_el_dia_completo(self):
        # ── 1 · El cajero abre la caja ──────────────────────────────────────
        # Nadie había probado nunca que la cuenta que va a operar mañana pueda
        # entrar y no vea los números del dueño.
        self.assertEqual(self.c_caja.get(reverse("home")).status_code, 200)
        panel = self.c_caja.get(reverse("panel_inventario"))
        self.assertEqual(panel.status_code, 200)
        self.assertIsNone(panel.context["alarma_margen"])
        self.assertFalse(panel.context["es_super"])

        # ── 2 · Primera venta, todavía sin capturar ninguna compra ──────────
        # Es el orden real del mostrador y el estado exacto de producción hoy.
        resp = self._vender([{"receta": self.shake.pk, "cantidad": 1}],
                            pago_con="200")
        n1 = Nota.objects.get()
        self.assertRedirects(
            resp, reverse("nota_ver", kwargs={"token": n1.token}))
        self.assertEqual(n1.total, Decimal("130.00"))
        self.assertEqual(n1.cambio, Decimal("70.00"))
        self.assertEqual(n1.folio, n1.token.hex[:8].upper())

        v1 = Venta.objects.get()
        # 0.00 con `costo_incompleto` NO es «salió gratis»: es «no se sabe».
        # Las tres se afirman juntas porque el número solo por su cuenta miente.
        self.assertEqual(v1.costo_fifo, Decimal("0.00"))
        self.assertTrue(v1.costo_incompleto)
        self.assertFalse(v1.costo_esta_completo)
        self.assertTrue(v1.consumos.filter(compra__isnull=True).exists())

        # El invariante que sostiene todo: sin costo completo no hay ingreso.
        mov1 = Movimiento.objects.get(venta=v1)
        self.assertIsNotNone(mov1.asiento_flujo_id)
        self.assertIsNone(mov1.asiento_reconocimiento_id)
        self.assertEqual(self._reportes()["total_ingresos"], Decimal("0"))

        salud = posting.salud_del_costeo(self.hoy.year, self.hoy.month)
        self.assertEqual(salud["incompletas"], 1)
        self.assertEqual(salud["sin_costear"], 0)
        # 200 ml de leche + 100 g de fresa que ninguna compra respalda.
        self.assertEqual(salud["faltante_sin_capa"], Decimal("300"))
        self.assertEqual(salud["saldo_acreedor"], Decimal("0"))

        libro = self.c_andy.get(
            f"{reverse('reportes_contables')}?anio={self.hoy.year}"
            f"&mes={self.hoy.month}")
        self.assertContains(libro, "⏳ Falta costo")

        # ── 3 · El cliente se lleva su comprobante ──────────────────────────
        # La nota y su PDF son públicos a propósito: quien tiene el token ya
        # puede verla, y pedir sesión solo estorbaría al cliente.
        self.assertEqual(
            self.c_publico.get(n1.get_absolute_url()).status_code, 200)
        pdf = self.c_publico.get(reverse("nota_pdf", args=[n1.token]))
        self.assertEqual(pdf["Content-Type"], "application/pdf")
        self.assertEqual(pdf.content[:5], b"%PDF-")
        self.assertEqual(pdf["Content-Disposition"],
                         f'inline; filename="nota-{n1.folio}.pdf"')

        # ── 3b · La barra entrega el pedido ─────────────────────────────────
        pedidos = self.c_caja.get(reverse("panel_pedidos"))
        self.assertContains(pedidos, "Andrea")
        self.assertTrue(n1.pendiente)
        self.c_caja.post(reverse("pedido_entregar", args=[n1.pk]))
        n1.refresh_from_db()
        self.assertIsNotNone(n1.entregada_en)
        # Se afirma el estado vacío y no la ausencia del nombre: el aviso de
        # «Pedido de Andrea entregado» viaja en la siguiente petición y lleva
        # el nombre dentro, así que buscarlo daría un falso negativo.
        self.assertContains(self.c_caja.get(reverse("panel_pedidos")),
                            "No hay nada pendiente")

        # ── 4 · Llega la factura del proveedor ──────────────────────────────
        self._comprar(self.leche, "2", "60.00")
        self._comprar(self.fresa, "1", "80.00")
        self._comprar(self.almendra, "1", "60.00")
        self._comprar(self.ing_espresso, "10", "40.00")

        capa_leche = Compra.objects.get(ingrediente=self.leche)
        self.assertEqual(capa_leche.cantidad_receta, Decimal("2000.0000"))
        # 200 ml ya se los llevó la venta de la mañana, retroactivamente.
        self.assertEqual(capa_leche.saldo_receta, Decimal("1800.0000"))
        self.assertEqual(capa_leche.costo_unitario_capa, Decimal("0.03"))
        # `total` es lo que se pagó, no una reconstrucción de la división.
        self.assertEqual(capa_leche.total, capa_leche.monto_total)

        # La venta de la mañana se costeó y se reconoció SOLA. Hay que releerla:
        # la instancia en memoria sigue diciendo que está incompleta.
        v1.refresh_from_db()
        mov1.refresh_from_db()
        self.assertEqual(v1.costo_fifo, Decimal("14.00"))  # 200×0.03 + 100×0.08
        self.assertFalse(v1.costo_incompleto)
        self.assertIsNotNone(mov1.asiento_reconocimiento_id)
        self.assertEqual(self._reportes()["total_ingresos"], Decimal("130"))
        self.assertEqual(self._reportes()["total_costo_ventas"], Decimal("14"))

        # ── 5 · Carrito de dos productos, con sustitución y add-on ──────────
        # `subs` y `addons` son listas de PARES, no diccionarios.
        resp = self._vender([
            {"receta": self.shake.pk, "cantidad": 2,
             "subs": [[self.leche.pk, self.almendra.pk]],
             "addons": [[self.espresso.pk, 1]]},
            {"receta": self.latte.pk, "cantidad": 1},
        ], pago_con="500")
        n2 = Nota.objects.exclude(pk=n1.pk).get()
        self.assertEqual(n2.lineas.count(), 2)
        self.assertEqual(n2.total, Decimal("320.00"))
        self.assertEqual(n2.cambio, Decimal("180.00"))
        # El IVA solo se desglosa aquí, en lo que ve el cliente.
        self.assertEqual(n2.subtotal + n2.iva, n2.total)

        linea_shake = n2.lineas.get(receta=self.shake)
        linea_latte = n2.lineas.get(receta=self.latte)
        # 2×130 + 10: el add-on se cobra una vez por línea, no por shake.
        self.assertEqual(linea_shake.ingreso, Decimal("270.00"))
        # almendra 400×0.06 + fresa 200×0.08 + 1 porción de espresso ×4.00
        self.assertEqual(linea_shake.costo_fifo, Decimal("44.00"))
        self.assertFalse(linea_shake.costo_incompleto)
        # Lo sustituido no se consume: es el bug que la sustitución vino a evitar.
        self.assertFalse(
            linea_shake.consumos.filter(ingrediente=self.leche).exists())
        self.assertEqual(linea_latte.costo_fifo, Decimal("4.50"))

        # Se redondea UNA sola vez, al escribir `costo_fifo`. Cuantizar capa por
        # capa deja un centavo atorado en Inventario en cada venta.
        suma_capas = sum(
            (c.importe for c in linea_shake.consumos.all()), Decimal("0"))
        self.assertEqual(
            suma_capas.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP),
            linea_shake.costo_fifo)

        # ── 6 · Venta con tarjeta ───────────────────────────────────────────
        self._vender([{"receta": self.latte.pk, "cantidad": 1}],
                     metodo_pago="tarjeta")
        n3 = Nota.objects.exclude(pk__in=[n1.pk, n2.pk]).get()
        self.assertEqual(n3.metodo_pago, "tarjeta")
        self.assertIsNone(n3.pago_con)
        self.assertIsNone(n3.cambio)
        self.assertEqual(n3.total, Decimal("50.00"))

        # ── 7 · Los errores de captura no deben dejar rastro ────────────────
        ventas, notas = Venta.objects.count(), Nota.objects.count()

        # (a) el efectivo no alcanza
        resp = self._vender([{"receta": self.shake.pk, "cantidad": 1}],
                            pago_con="10", **{})
        self.assertRedirects(resp, reverse("panel_inventario"))
        self.assertEqual(Venta.objects.count(), ventas)
        self.assertEqual(Nota.objects.count(), notas)

        # (b) cortesía sin motivo
        resp = self._vender([{"receta": self.shake.pk, "cantidad": 1}],
                            cortesia="1")
        self.assertRedirects(resp, reverse("panel_inventario"))
        self.assertEqual(Venta.objects.count(), ventas)
        self.assertEqual(Nota.objects.count(), notas)
        # La venta se creó y se costeó dentro de la transacción antes de que la
        # validación dijera que no: lo que se está probando es que el rollback
        # también deshizo los asientos que las señales alcanzaron a escribir.
        self.assertEqual(
            Movimiento.objects.filter(tipo=Movimiento.Tipo.VENTA).count(),
            ventas)

        # (c) un producto desactivado dentro del carrito.
        # HALLAZGO, no diseño: la línea desaparece del ticket EN SILENCIO, sin
        # ningún mensaje, y el cliente pagaría de menos. Se fija como está para
        # que el día que se arregle, este test avise.
        resp = self._vender([{"receta": self.shake.pk, "cantidad": 1},
                             {"receta": self.viejo.pk, "cantidad": 1}],
                            pago_con="200")
        n4 = Nota.objects.exclude(pk__in=[n1.pk, n2.pk, n3.pk]).get()
        self.assertEqual(n4.lineas.count(), 1)
        self.assertEqual(n4.total, Decimal("130.00"))

        # ── 8 · Venta con el celular del cliente ────────────────────────────
        self._vender([{"receta": self.shake.pk, "cantidad": 2}],
                     pago_con="300", telefono_lealtad="9991234567")
        cliente = Cliente.objects.get()
        self.assertEqual(cliente.telefono, "+529991234567")
        self.assertEqual(cliente.nombre, "Andrea")
        self.assertEqual(len(cliente.codigo), 6)
        # 260 pesos ÷ 10 pesos por punto.
        self.assertEqual(cliente.puntos_saldo, 26)
        self.assertEqual(cliente.puntos_historicos, 26)
        self.assertEqual(cliente.visitas, 1)
        self.assertEqual(cliente.nivel, self.oro)

        n5 = cliente.compras.get().nota
        comprobante = self.c_publico.get(n5.get_absolute_url())
        self.assertContains(comprobante, "ganaste 26 puntos")
        self.assertContains(comprobante, "¡Subiste a Oro!")
        self.assertContains(comprobante, "¡Ya puedes canjear")

        # ── 9 · Cortesía de activación ──────────────────────────────────────
        self._vender([{"receta": self.shake.pk, "cantidad": 1}],
                     cortesia="1", motivo_cortesia="Activación de la sucursal",
                     telefono_lealtad="9991234567")
        cortesia = Venta.objects.filter(es_cortesia=True).get()
        self.assertEqual(cortesia.ingreso, Decimal("0"))
        self.assertEqual(cortesia.costo_fifo, Decimal("14.00"))

        mov_cortesia = Movimiento.objects.get(venta=cortesia)
        # No movió efectivo: no hay asiento de flujo.
        self.assertIsNone(mov_cortesia.asiento_flujo_id)
        lineas = sorted(
            (l.cuenta.codigo, l.debe, l.haber)
            for l in mov_cortesia.asiento_reconocimiento.movimientos.all())
        # 506 Cortesías contra 115 Inventario: nunca 401 ni 501.
        self.assertEqual(lineas, [
            ("115", Decimal("0.00"), Decimal("14.00")),
            ("506", Decimal("14.00"), Decimal("0.00")),
        ])
        # La cortesía NO acumula puntos, aunque el teléfono venga en el POST.
        cliente.refresh_from_db()
        self.assertEqual(cliente.puntos_saldo, 26)
        self.assertEqual(cliente.compras.count(), 1)

        # ── 10 · Canje de un premio ─────────────────────────────────────────
        # (a) el cajero busca al cliente desde el formulario de venta
        busca = self.c_caja.get(f"{reverse('lealtad_buscar')}?q=9991234567")
        datos = busca.json()
        self.assertTrue(datos["encontrado"])
        self.assertEqual(datos["puntos"], 26)
        self.assertEqual(datos["nivel"], "Oro")
        self.assertEqual(datos["premios"], ["Postre gratis", "Latte gratis"])

        # (b) canjea. Lo hace el CAJERO: canjear no exige superusuario.
        self.c_caja.post(
            reverse("lealtad_cliente_canjear", args=[cliente.pk]),
            {"premio": self.premio_latte.pk})
        canje = Canje.objects.get()
        self.assertEqual(canje.estado, "pendiente")
        self.assertEqual(len(canje.codigo), 6)
        self.assertEqual(canje.puntos_usados, 10)
        self.assertEqual(canje.vence_el, self.hoy + timedelta(days=30))
        cliente.refresh_from_db()
        self.assertEqual(cliente.puntos_saldo, 16)
        # Canjear baja el saldo, NUNCA el nivel: los históricos no se tocan.
        self.assertEqual(cliente.puntos_historicos, 26)
        self.assertEqual(cliente.nivel, self.oro)

        # (c) se entrega el premio: sale del inventario como cortesía
        destino = reverse("lealtad_cliente", args=[cliente.pk])
        resp = self.c_caja.post(
            reverse("lealtad_canje_entregar", args=[canje.pk]),
            {"volver": destino})
        self.assertRedirects(resp, destino)
        canje.refresh_from_db()
        self.assertEqual(canje.estado, "entregado")
        self.assertTrue(canje.venta.es_cortesia)
        self.assertIsNone(canje.venta.nota_id)
        self.assertEqual(canje.costo, Decimal("4.50"))
        mov_premio = Movimiento.objects.get(venta=canje.venta)
        self.assertIsNone(mov_premio.asiento_flujo_id)

        # (d) el premio sin receta: el bloqueo real de producción.
        # Los puntos YA se descontaron y el canje se queda pendiente.
        self.c_andy.post(
            reverse("lealtad_cliente_ajustar", args=[cliente.pk]),
            {"puntos": "10", "motivo": "Corrección de prueba"})
        self.c_caja.post(
            reverse("lealtad_cliente_canjear", args=[cliente.pk]),
            {"premio": self.premio_huerfano.pk})
        huerfano = Canje.objects.exclude(pk=canje.pk).get()
        resp = self.c_caja.post(
            reverse("lealtad_canje_entregar", args=[huerfano.pk]),
            {"volver": destino}, follow=True)
        huerfano.refresh_from_db()
        self.assertEqual(huerfano.estado, "pendiente")
        self.assertTrue(any("no tiene producto ligado" in m
                            for m in _mensajes(resp)))

        # (e) cancelar devuelve los puntos sin regalar nivel.
        # El canje que no se pudo entregar dejó al cliente pagando: 26 − 5.
        cliente.refresh_from_db()
        self.assertEqual(cliente.puntos_saldo, 21)
        historicos = cliente.puntos_historicos
        self.c_caja.post(reverse("lealtad_canje_cancelar", args=[huerfano.pk]))
        cliente.refresh_from_db()
        self.assertEqual(cliente.puntos_saldo, 26)
        self.assertEqual(cliente.puntos_historicos, historicos)

        # ── 11 · El gasto del mes ───────────────────────────────────────────
        resp = self.c_andy.post(reverse("gasto_registrar"), {
            "categoria": "renta", "monto": "1,500.00",
            "descripcion": "Renta de agosto", "fecha": self.hoy.isoformat()})
        self.assertRedirects(
            resp,
            f"{reverse('reportes_contables')}?anio={self.hoy.year}"
            f"&mes={self.hoy.month}")
        gasto = Movimiento.objects.get(tipo=Movimiento.Tipo.GASTO)
        # La coma de miles se limpia; el monto no se queda en 1.00.
        self.assertEqual(gasto.monto, Decimal("1500.00"))
        self.assertEqual(gasto.cuenta.codigo, "502")

        # ── 12 · Cierre: lo que abre el dueño al final del día ──────────────
        er = self._reportes()
        # 130 + 270 + 50 + 50 + 130 + 260
        self.assertEqual(er["total_ingresos"], Decimal("890"))
        # 14 + 44 + 4.50 + 4.50 + 14 + 28 — sin las dos cortesías
        self.assertEqual(er["total_costo_ventas"], Decimal("109"))
        self.assertEqual(er["utilidad_bruta"], Decimal("781"))

        gastos = {g["codigo"]: g for g in er["gastos"]}
        self.assertEqual(gastos["502"]["total"], Decimal("1500"))
        # Las cortesías (14.00 + 4.50) se presentan DENTRO de la 504 y fuera
        # del costo de ventas, que es lo que decidió P6.
        self.assertEqual(gastos["504"]["propio"], Decimal("0"))
        self.assertEqual(
            [(s["codigo"], s["monto"]) for s in gastos["504"]["subcuentas"]],
            [("506", Decimal("18.50"))])
        self.assertEqual(er["total_gastos"], Decimal("1518.50"))

        balanza = posting.balanza_comprobacion(self.hoy.year, self.hoy.month)
        self.assertTrue(balanza["cuadra"])
        self.assertEqual(balanza["total_debe"], balanza["total_haber"])

        balance = posting.balance_general(self.hoy.year, self.hoy.month)
        self.assertTrue(balance["cuadra"])
        self.assertEqual(balance["total_activo"],
                         balance["total_pasivo_capital"])

        salud = posting.salud_del_costeo(self.hoy.year, self.hoy.month)
        self.assertEqual(salud["sin_costear"], 0)
        self.assertEqual(salud["incompletas"], 0)
        self.assertEqual(salud["capas_sin_abrir"], 0)
        self.assertEqual(salud["faltante_sin_capa"], Decimal("0"))
        # La única aserción que caza el bug que las tres reglas del costeo
        # vienen a evitar: el balance cuadra igual con el activo en negativo.
        # 240 comprados − 127.50 consumidos.
        self.assertEqual(salud["saldo_inventario"], Decimal("112.50"))
        self.assertGreaterEqual(salud["saldo_inventario"], 0)

        flujo = posting.flujo_efectivo(self.hoy.year, self.hoy.month)
        self.assertEqual(flujo["entradas"], Decimal("890"))
        self.assertEqual(flujo["salidas"], Decimal("1740"))  # 240 + 1500
        self.assertEqual(flujo["saldo_final"],
                         flujo["saldo_inicial"] + flujo["entradas"]
                         - flujo["salidas"])

        # La pantalla dice lo mismo que los cálculos.
        libro = self.c_andy.get(
            f"{reverse('reportes_contables')}?anio={self.hoy.year}"
            f"&mes={self.hoy.month}")
        self.assertContains(libro, "Sano")
        self.assertContains(libro, "✓ Reconocido")
        self.assertNotContains(libro, "⏳ Falta costo")
        self.assertContains(libro, 'class="ver-nota"')

        # Los cuatro botones de descarga, que nunca se habían pedido por HTTP.
        for reporte, archivo in (("resultados", "estado_resultados"),
                                 ("balance", "balance_general"),
                                 ("flujo", "flujo_efectivo"),
                                 ("balanza", "balanza_comprobacion")):
            with self.subTest(reporte=reporte):
                xls = self.c_andy.get(
                    f"{reverse('exportar_estado', args=[reporte])}"
                    f"?anio={self.hoy.year}&mes={self.hoy.month}")
                self.assertEqual(xls.status_code, 200)
                self.assertEqual(xls["Content-Type"], XLSX_MIME)
                self.assertEqual(xls.content[:2], b"PK")
                self.assertEqual(
                    xls["Content-Disposition"],
                    f'attachment; filename="{archivo}_{self.hoy.year}'
                    f'-{self.hoy.month:02d}.xlsx"')
        self.assertEqual(
            self.c_andy.get(
                reverse("exportar_estado", args=["pdf"])).status_code, 404)

        # ── 13 · Las tres igualdades de `recostear --verificar` ─────────────
        # Como aserciones y no como texto en pantalla: el comando imprime que
        # el costeo NO está sano y devuelve 0 igual, así que su código de
        # salida no sirve de guardia.
        for venta in Venta.objects.filter(costo_fifo__isnull=False):
            with self.subTest(venta=venta.pk):
                suma = sum((c.importe for c in venta.consumos.all()),
                           Decimal("0"))
                self.assertEqual(
                    suma.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP),
                    venta.costo_fifo)
        for capa in Compra.objects.all():
            with self.subTest(capa=capa.pk):
                consumido = sum(
                    (c.cantidad_receta for c in capa.consumos.all()),
                    Decimal("0"))
                self.assertEqual(consumido + capa.saldo_receta,
                                 capa.cantidad_receta)
                self.assertGreaterEqual(capa.saldo_receta, 0)
        self.assertEqual(
            Movimiento.objects.filter(
                tipo=Movimiento.Tipo.VENTA,
                asiento_reconocimiento__isnull=False,
                venta__in=Venta.objects.sin_costo_completo()).count(), 0)

        # ── 14 · El runbook de después de desplegar no cambia nada ──────────
        # Es la prueba de que `sincronizar_contabilidad` y `recostear --todo`
        # son idempotentes: correrlos sobre un sistema sano tiene que dejarlo
        # idéntico, o no se pueden correr con confianza en producción.
        antes = {
            "er": self._reportes(),
            "asientos": Asiento.objects.count(),
            "lineas": MovimientoContable.objects.count(),
            "costos": sorted(Venta.objects.values_list("id", "costo_fifo")),
            "saldos": sorted(Compra.objects.values_list("id", "saldo_receta")),
        }
        call_command("sincronizar_contabilidad", stdout=StringIO())
        call_command("recostear", "--todo", stdout=StringIO())
        self.assertEqual(self._reportes(), antes["er"])
        self.assertEqual(Asiento.objects.count(), antes["asientos"])
        self.assertEqual(MovimientoContable.objects.count(), antes["lineas"])
        self.assertEqual(
            sorted(Venta.objects.values_list("id", "costo_fifo")),
            antes["costos"])
        self.assertEqual(
            sorted(Compra.objects.values_list("id", "saldo_receta")),
            antes["saldos"])

        salida = StringIO()
        call_command("recostear", "--verificar", stdout=salida)
        self.assertIn("sano", salida.getvalue().lower())

    # ══════════════════════════════════════════════════════════════════════
    def test_la_frontera_de_permisos(self):
        """El cajero no puede entrar a lo que expone dinero."""
        for nombre in ("inventario_compra_agregar", "reportes_contables",
                       "panel_financiero", "panel_presupuesto",
                       "panel_catalogo", "panel_actividad",
                       "gasto_registrar"):
            with self.subTest(vista=nombre):
                url = reverse(nombre)
                resp = self.c_caja.post(url) if nombre in (
                    "inventario_compra_agregar",
                    "gasto_registrar") else self.c_caja.get(url)
                self.assertEqual(resp.status_code, 302)
                self.assertEqual(resp["Location"], f"/?next={url}")

        # El anónimo recibe 405 y no un redirect al login en las vistas
        # POST-only: `@require_POST` es el decorador más externo.
        self.assertEqual(
            self.c_publico.get(
                reverse("inventario_venta_agregar")).status_code, 405)
        self.assertEqual(
            self.c_publico.post(
                reverse("inventario_venta_agregar")).status_code, 302)

    def test_lo_que_el_cajero_ve_del_programa(self):
        """El hallazgo de antes, ya cerrado y del derecho.

        Este test fijaba lo contrario: el subnav escondía métricas y marketing,
        pero las URLs respondían 200 a cualquier sesión y publicaban margen de
        miembros, costo de premios y utilidad neta. Esconder no es proteger.

        Ahora esas dos exigen dueño, porque son pantallas de puro dinero. El
        panel general se queda abierto —de ahí el cajero busca clientes y
        entrega canjes— con el dinero escondido dentro.
        """
        for nombre in ("lealtad_metricas", "lealtad_marketing"):
            with self.subTest(vista=nombre):
                url = reverse(nombre)
                resp = self.c_caja.get(url)
                self.assertEqual(resp.status_code, 302)
                self.assertEqual(resp["Location"], f"/?next={url}")

        panel = self.c_caja.get(reverse("lealtad_panel"))
        self.assertEqual(panel.status_code, 200)
        self.assertNotContains(panel, "Utilidad neta después de premios")

    def test_el_cron_de_lealtad(self):
        """Sin él no caducan puntos, no expiran canjes y no sale un mensaje.

        Es la única vía por la que el programa late en producción, y ningún
        test lo tocaba. La URL va literal y sin barra final: con ella
        `APPEND_SLASH` respondería un redirect y el cron quedaría sin
        ejecutarse en silencio.
        """
        url = "/api/lealtad/cron/run"
        # Sin secreto configurado, el latido está apagado.
        self.assertEqual(self.c_publico.get(url).status_code, 503)

        with override_settings(CRON_SECRET="secreto-de-prueba"):
            self.assertEqual(
                self.c_publico.get(
                    url, headers={"authorization": "Bearer nope"}).status_code,
                401)
            resp = self.c_publico.get(
                url,
                headers={"authorization": "Bearer secreto-de-prueba"})
            self.assertEqual(resp.status_code, 200)
            self.assertTrue(resp.json()["ok"])
