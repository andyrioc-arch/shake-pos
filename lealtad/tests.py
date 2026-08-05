"""Pruebas del programa de lealtad.

Cubren lo que de verdad puede costar dinero o confianza: el cálculo de puntos,
la caducidad, los canjes, las automatizaciones y que una venta nunca se caiga
por culpa del programa.
"""

import json
from datetime import date, timedelta
from decimal import Decimal
from io import StringIO

from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from inventario.models import Ingrediente, Nota, Receta, RecetaIngrediente, Venta
from lealtad import automatizaciones, mensajeria, metricas, servicios
from lealtad.models import (
    Automatizacion, Campana, Canje, Cliente, Compra, ConfiguracionPrograma,
    Mensaje, Nivel, Plantilla, Premio, PromocionPuntos, TelefonoInvalido,
    normaliza_telefono, telefono_bonito,
)


def crea_receta(nombre="Smoothie", precio="130", costo_ingrediente="80"):
    ing = Ingrediente.objects.create(
        nombre=f"Insumo {nombre}", unidad_compra="kg", cantidad_por_unidad=1,
        unidad_receta="g", costo_unidad_compra=Decimal(costo_ingrediente))
    receta = Receta.objects.create(nombre=nombre, precio_venta=Decimal(precio))
    RecetaIngrediente.objects.create(receta=receta, ingrediente=ing, cantidad=1)
    return receta


class TelefonoTests(TestCase):
    def test_acepta_los_formatos_que_la_gente_teclea(self):
        for entrada in ["9991234567", "999 123 4567", "(999) 123-4567",
                        "+52 999 123 4567", "521 999 123 4567", "5219991234567"]:
            self.assertEqual(normaliza_telefono(entrada), "+529991234567", entrada)

    def test_rechaza_numeros_que_no_son_celulares(self):
        for malo in ["123", "", "abcdefghij", "9991234567891234"]:
            with self.assertRaises(TelefonoInvalido):
                normaliza_telefono(malo)

    def test_se_muestra_legible(self):
        self.assertEqual(telefono_bonito("+529991234567"), "99 9123 4567")

    def test_el_cliente_guarda_el_telefono_normalizado(self):
        cliente = Cliente.objects.create(telefono="999 123 4567")
        self.assertEqual(cliente.telefono, "+529991234567")

    def test_no_se_duplica_un_cliente_por_el_formato(self):
        servicios.alta_cliente("9991234567", nombre="Ana")
        cliente, creado = servicios.alta_cliente("+52 999 123 4567")
        self.assertFalse(creado)
        self.assertEqual(Cliente.objects.count(), 1)
        self.assertEqual(cliente.nombre, "Ana")


class PuntosTests(TestCase):
    def setUp(self):
        self.cfg = ConfiguracionPrograma.get()
        self.cliente, _ = servicios.alta_cliente("9991234567", nombre="Ana")

    def test_un_punto_por_cada_diez_pesos(self):
        casos = [("9991000001", 90, 9), ("9991000002", 130, 13),
                 ("9991000003", 260, 26), ("9991000004", 5, 0),
                 ("9991000005", 139, 13)]
        for telefono, monto, esperado in casos:
            cliente, _ = servicios.alta_cliente(telefono)
            compra = servicios.registrar_compra(cliente, Decimal(monto))
            self.assertEqual(compra.puntos_ganados, esperado, f"${monto}")

    def test_la_compra_actualiza_el_perfil_del_cliente(self):
        servicios.registrar_compra(self.cliente, Decimal("260"),
                                   fecha=date(2026, 6, 30))
        self.cliente.refresh_from_db()
        self.assertEqual(self.cliente.puntos_saldo, 26)
        self.assertEqual(self.cliente.puntos_historicos, 26)
        self.assertEqual(self.cliente.gasto_historico, Decimal("260"))
        self.assertEqual(self.cliente.visitas, 1)
        self.assertEqual(self.cliente.ultima_compra, date(2026, 6, 30))

    def test_una_promocion_multiplica_los_puntos(self):
        hoy = timezone.localdate()
        PromocionPuntos.objects.create(nombre="Dobles", multiplicador=Decimal("2"),
                                       desde=hoy, hasta=hoy)
        compra = servicios.registrar_compra(self.cliente, Decimal("130"))
        self.assertEqual(compra.puntos_ganados, 26)

    def test_el_mismo_ticket_no_da_puntos_dos_veces(self):
        primera = servicios.registrar_compra(self.cliente, Decimal("130"),
                                             ticket="A12345")
        repetida = servicios.registrar_compra(self.cliente, Decimal("130"),
                                              ticket="A12345")
        self.cliente.refresh_from_db()
        self.assertEqual(primera.pk, repetida.pk)
        self.assertEqual(self.cliente.puntos_saldo, 13)

    def test_el_programa_apagado_no_acumula(self):
        self.cfg.activo = False
        self.cfg.save()
        self.assertIsNone(servicios.registrar_compra(self.cliente, Decimal("130")))

    def test_un_monto_invalido_se_rechaza(self):
        with self.assertRaises(servicios.ErrorLealtad):
            servicios.registrar_compra(self.cliente, Decimal("0"))

    def test_el_ajuste_manual_suma_y_resta(self):
        servicios.ajustar_puntos(self.cliente, 50, "Cortesía")
        self.assertEqual(self.cliente.puntos_saldo, 50)
        servicios.ajustar_puntos(self.cliente, -20, "Corrección")
        self.assertEqual(self.cliente.puntos_saldo, 30)

    def test_no_se_puede_dejar_el_saldo_en_negativo(self):
        servicios.ajustar_puntos(self.cliente, 10, "Cortesía")
        with self.assertRaises(servicios.ErrorLealtad):
            servicios.ajustar_puntos(self.cliente, -50, "Error")


class CaducidadTests(TestCase):
    def setUp(self):
        self.cliente, _ = servicios.alta_cliente("9991234567")

    def test_los_puntos_caducan_a_los_doce_meses(self):
        compra = servicios.registrar_compra(self.cliente, Decimal("130"))
        lote = compra.movimientos.get()
        self.assertEqual(lote.expira_el,
                         servicios._suma_meses(timezone.localdate(), 12))

    def test_sin_vigencia_los_puntos_no_caducan(self):
        cfg = ConfiguracionPrograma.get()
        cfg.vigencia_puntos_meses = 0
        cfg.save()
        compra = servicios.registrar_compra(self.cliente, Decimal("130"))
        self.assertIsNone(compra.movimientos.get().expira_el)

    def test_expirar_puntos_descuenta_los_lotes_vencidos(self):
        compra = servicios.registrar_compra(self.cliente, Decimal("130"))
        lote = compra.movimientos.get()
        lote.expira_el = timezone.localdate() - timedelta(days=1)
        lote.save()

        perdidos = servicios.expirar_puntos()
        self.cliente.refresh_from_db()
        self.assertEqual(perdidos, 13)
        self.assertEqual(self.cliente.puntos_saldo, 0)
        # Los puntos históricos no bajan: el nivel ganado no se pierde.
        self.assertEqual(self.cliente.puntos_historicos, 13)

    def test_el_canje_consume_primero_lo_que_caduca_antes(self):
        viejo = servicios.registrar_compra(self.cliente, Decimal("1000"),
                                           ticket="viejo").movimientos.get()
        viejo.expira_el = timezone.localdate() + timedelta(days=5)
        viejo.save()
        nuevo = servicios.registrar_compra(self.cliente, Decimal("1000"),
                                           ticket="nuevo").movimientos.get()

        premio = Premio.objects.create(nombre="Latte", puntos_requeridos=100)
        self.cliente.refresh_from_db()
        servicios.canjear(self.cliente, premio)

        viejo.refresh_from_db()
        nuevo.refresh_from_db()
        self.assertEqual(viejo.saldo_lote, 0)
        self.assertEqual(nuevo.saldo_lote, 100)


class NivelesYPremiosTests(TestCase):
    def setUp(self):
        self.inicio = Nivel.objects.create(nombre="Inicio", puntos_requeridos=0)
        self.fan = Nivel.objects.create(nombre="Fan", puntos_requeridos=100)
        self.vip = Nivel.objects.create(nombre="VIP", puntos_requeridos=500)
        self.receta = crea_receta()
        self.premio = Premio.objects.create(
            nombre="Latte gratis", puntos_requeridos=100, receta=self.receta)
        self.cliente, _ = servicios.alta_cliente("9991234567", nombre="Ana")

    def test_el_nivel_sale_de_los_puntos_historicos(self):
        servicios.registrar_compra(self.cliente, Decimal("1200"))
        self.cliente.refresh_from_db()
        self.assertEqual(self.cliente.nivel, self.fan)
        self.assertEqual(self.cliente.siguiente_nivel, self.vip)

    def test_el_costo_del_premio_sale_de_la_receta(self):
        self.assertEqual(self.premio.valor_percibido, Decimal("130"))
        self.assertEqual(self.premio.costo_estimado, Decimal("80"))

    def test_un_descuento_por_porcentaje_vale_la_mitad(self):
        premio = Premio.objects.create(
            nombre="Mitad de precio", puntos_requeridos=200, receta=self.receta,
            tipo=Premio.Tipo.DESCUENTO_PCT, valor=Decimal("50"))
        self.assertEqual(premio.valor_percibido, Decimal("65"))
        self.assertEqual(premio.costo_estimado, Decimal("65"))

    def test_canjear_descuenta_los_puntos(self):
        servicios.registrar_compra(self.cliente, Decimal("1300"))
        self.cliente.refresh_from_db()
        canje = servicios.canjear(self.cliente, self.premio)
        self.cliente.refresh_from_db()
        self.assertEqual(self.cliente.puntos_saldo, 30)
        self.assertEqual(canje.puntos_usados, 100)
        self.assertEqual(canje.estado, Canje.Estado.PENDIENTE)
        self.assertEqual(len(canje.codigo), 6)

    def test_no_se_puede_canjear_sin_saldo(self):
        with self.assertRaises(servicios.ErrorLealtad):
            servicios.canjear(self.cliente, self.premio)

    def test_se_respeta_el_limite_por_cliente(self):
        self.premio.limite_por_cliente = 1
        self.premio.save()
        servicios.registrar_compra(self.cliente, Decimal("5000"))
        self.cliente.refresh_from_db()
        servicios.canjear(self.cliente, self.premio)
        self.cliente.refresh_from_db()
        with self.assertRaises(servicios.ErrorLealtad):
            servicios.canjear(self.cliente, self.premio)

    def test_se_respeta_el_nivel_minimo(self):
        self.premio.nivel_minimo = self.vip
        self.premio.save()
        servicios.registrar_compra(self.cliente, Decimal("2000"))   # 200 pts, Fan
        self.cliente.refresh_from_db()
        with self.assertRaises(servicios.ErrorLealtad):
            servicios.canjear(self.cliente, self.premio)

    def test_cancelar_un_canje_devuelve_los_puntos(self):
        servicios.registrar_compra(self.cliente, Decimal("1300"))
        self.cliente.refresh_from_db()
        canje = servicios.canjear(self.cliente, self.premio)
        servicios.cancelar_canje(canje)
        self.cliente.refresh_from_db()
        self.assertEqual(self.cliente.puntos_saldo, 130)
        self.assertEqual(canje.estado, Canje.Estado.CANCELADO)


class AutomatizacionesTests(TestCase):
    def setUp(self):
        self.plantilla = Plantilla.objects.create(
            clave="prueba", nombre="Prueba",
            cuerpo="Hola {nombre}, tienes {saldo} puntos.")
        self.cliente, _ = servicios.alta_cliente("9991234567", nombre="Ana Pérez")

    def _regla(self, disparador, **extra):
        return Automatizacion.objects.create(
            nombre=f"Regla {disparador}", disparador=disparador,
            plantilla=self.plantilla, **extra)

    def _compra(self, monto, **extra):
        """Compra que sí ejecuta los disparadores.

        Las automatizaciones se encolan con `transaction.on_commit` para no
        mandar mensajes de una venta que se revirtió; en los tests el commit
        nunca ocurre, así que hay que forzarlo.
        """
        with self.captureOnCommitCallbacks(execute=True):
            return servicios.registrar_compra(self.cliente, Decimal(monto), **extra)

    def test_la_bienvenida_se_encola_al_registrarse(self):
        self._regla(Automatizacion.Disparador.BIENVENIDA)
        cliente, _ = servicios.alta_cliente("9997654321", nombre="Luis")
        mensaje = Mensaje.objects.get(cliente=cliente)
        self.assertIn("Hola Luis", mensaje.cuerpo)
        self.assertEqual(mensaje.estado, Mensaje.Estado.PROGRAMADO)

    def test_la_compra_dispara_su_mensaje(self):
        self._regla(Automatizacion.Disparador.COMPRA, no_repetir_dias=0)
        self._compra("130")
        self.assertEqual(Mensaje.objects.filter(cliente=self.cliente).count(), 1)

    def test_no_se_repite_dentro_de_la_ventana(self):
        self._regla(Automatizacion.Disparador.COMPRA, no_repetir_dias=30)
        self._compra("130", ticket="a")
        self._compra("130", ticket="b")
        self.assertEqual(Mensaje.objects.filter(cliente=self.cliente).count(), 1)

    def test_se_respeta_el_maximo_por_cliente(self):
        self._regla(Automatizacion.Disparador.COMPRA, no_repetir_dias=0,
                    max_por_cliente=1)
        self._compra("130", ticket="a")
        self._compra("130", ticket="b")
        self.assertEqual(Mensaje.objects.filter(cliente=self.cliente).count(), 1)

    def test_no_se_le_manda_a_quien_pidio_baja(self):
        self._regla(Automatizacion.Disparador.COMPRA, no_repetir_dias=0)
        self.cliente.acepta_mensajes = False
        self.cliente.save()
        self._compra("130")
        self.assertFalse(Mensaje.objects.filter(cliente=self.cliente).exists())

    def test_inactividad_alcanza_a_quien_lleva_dias_sin_venir(self):
        regla = self._regla(Automatizacion.Disparador.INACTIVIDAD, dias=30)
        self._compra("130", fecha=timezone.localdate() - timedelta(days=45))
        Mensaje.objects.all().delete()

        automatizaciones.correr_programadas()
        self.assertTrue(Mensaje.objects.filter(automatizacion=regla).exists())

    def test_inactividad_no_molesta_a_quien_acaba_de_venir(self):
        self._regla(Automatizacion.Disparador.INACTIVIDAD, dias=30)
        self._compra("130")
        Mensaje.objects.all().delete()

        automatizaciones.correr_programadas()
        self.assertFalse(Mensaje.objects.exists())

    def test_cumpleanos_avisa_con_los_dias_configurados(self):
        objetivo = timezone.localdate() + timedelta(days=7)
        self.cliente.cumpleanos = date(1995, objetivo.month, objetivo.day)
        self.cliente.save()
        regla = self._regla(Automatizacion.Disparador.CUMPLEANOS, dias=7)

        automatizaciones.correr_programadas()
        self.assertTrue(Mensaje.objects.filter(automatizacion=regla).exists())

    def test_cerca_de_premio_solo_al_pasar_el_umbral(self):
        Premio.objects.create(nombre="Latte", puntos_requeridos=100)
        regla = self._regla(Automatizacion.Disparador.CERCA_PREMIO,
                            umbral_porcentaje=80)

        servicios.ajustar_puntos(self.cliente, 50, "prueba")
        automatizaciones.correr_programadas()
        self.assertFalse(Mensaje.objects.filter(automatizacion=regla).exists())

        servicios.ajustar_puntos(self.cliente, 35, "prueba")   # 85 pts = 85%
        automatizaciones.correr_programadas()
        self.assertTrue(Mensaje.objects.filter(automatizacion=regla).exists())

    def test_una_regla_apagada_no_manda_nada(self):
        self._regla(Automatizacion.Disparador.COMPRA, activa=False,
                    no_repetir_dias=0)
        self._compra("130")
        self.assertFalse(Mensaje.objects.exists())

    def test_las_variables_se_sustituyen(self):
        plantilla = Plantilla.objects.create(
            clave="v", nombre="V",
            cuerpo="{nombre} ganó {puntos} pts, saldo {saldo}, faltan {faltan}.")
        Premio.objects.create(nombre="Latte", puntos_requeridos=100)
        Automatizacion.objects.create(
            nombre="Compra", disparador=Automatizacion.Disparador.COMPRA,
            plantilla=plantilla, no_repetir_dias=0)

        self._compra("130")
        cuerpo = Mensaje.objects.get().cuerpo
        self.assertEqual(cuerpo, "Ana ganó 13 pts, saldo 13, faltan 87.")

    def test_una_plantilla_con_variable_inventada_no_truena(self):
        plantilla = Plantilla.objects.create(
            clave="mala", nombre="Mala", cuerpo="Hola {inexistente}")
        self.assertEqual(plantilla.render(nombre="Ana"), "Hola {inexistente}")


class MensajeriaTests(TestCase):
    def setUp(self):
        self.cliente, _ = servicios.alta_cliente("9991234567", nombre="Ana")
        self.plantilla = Plantilla.objects.create(
            clave="p", nombre="P", cuerpo="Hola {nombre}")

    def test_el_modo_simulado_marca_enviado_sin_salir(self):
        mensaje = mensajeria.encolar(self.cliente, self.plantilla)
        mensajeria.enviar(mensaje)
        self.assertEqual(mensaje.estado, Mensaje.Estado.ENVIADO)
        self.assertTrue(mensaje.proveedor_id.startswith("sim-"))

    def test_fuera_de_horario_no_se_despacha(self):
        cfg = ConfiguracionPrograma.get()
        hora = timezone.localtime().hour
        cfg.envio_hora_inicio = (hora + 2) % 24
        cfg.envio_hora_fin = (hora + 3) % 24
        cfg.save()
        mensajeria.encolar(self.cliente, self.plantilla)
        self.assertEqual(mensajeria.despachar_pendientes(), (0, 0, 0))

    def test_la_compra_posterior_se_atribuye_al_mensaje(self):
        mensaje = mensajeria.encolar(self.cliente, self.plantilla)
        mensajeria.enviar(mensaje)
        servicios.registrar_compra(self.cliente, Decimal("130"))

        self.assertEqual(mensajeria.atribuir_compras(), 1)
        mensaje.refresh_from_db()
        self.assertIsNotNone(mensaje.compra_atribuida)

    def test_una_compra_anterior_al_mensaje_no_se_atribuye(self):
        servicios.registrar_compra(self.cliente, Decimal("130"))
        mensaje = mensajeria.encolar(self.cliente, self.plantilla)
        mensajeria.enviar(mensaje)

        self.assertEqual(mensajeria.atribuir_compras(), 0)

    def test_la_campana_llega_a_su_segmento(self):
        servicios.alta_cliente("9997654321", nombre="Luis")
        campana = Campana.objects.create(
            nombre="Promo", segmento=Campana.Segmento.TODOS,
            cuerpo="Hola {nombre}, ven esta semana.")
        self.assertEqual(automatizaciones.enviar_campana(campana), 2)
        campana.refresh_from_db()
        self.assertEqual(campana.estado, Campana.Estado.ENVIADA)


class VentaConLealtadTests(TestCase):
    """La caja: el punto donde el programa toca las ventas reales."""

    def setUp(self):
        self.user = User.objects.create_superuser("caja", "c@x.mx", "x")
        self.client.force_login(self.user)
        self.receta = crea_receta()

    def _vender(self, telefono="", cortesia=False):
        datos = {
            "productos_json": json.dumps([{"receta": self.receta.pk, "cantidad": 2}]),
            "metodo_pago": "efectivo",
            "pago_con": "300",
            "fecha": timezone.localdate().isoformat(),
            "telefono_lealtad": telefono,
        }
        if cortesia:
            datos.update(cortesia="1", motivo_cortesia="Activación", pago_con="")
        return self.client.post(reverse("inventario_venta_agregar"), datos)

    def test_una_venta_con_telefono_da_de_alta_y_acumula(self):
        self._vender("9991234567")
        cliente = Cliente.objects.get()
        self.assertEqual(cliente.puntos_saldo, 26)     # $260 → 26 puntos
        self.assertEqual(cliente.visitas, 1)
        self.assertEqual(Compra.objects.get().nota, Nota.objects.get())

    def test_una_venta_sin_telefono_no_crea_clientes(self):
        self._vender()
        self.assertEqual(Cliente.objects.count(), 0)
        self.assertEqual(Nota.objects.count(), 1)

    def test_un_telefono_invalido_no_tumba_la_venta(self):
        respuesta = self._vender("123")
        self.assertEqual(Nota.objects.count(), 1)
        self.assertEqual(Venta.objects.count(), 1)
        self.assertEqual(Cliente.objects.count(), 0)
        self.assertEqual(respuesta.status_code, 302)

    def test_una_cortesia_no_acumula_puntos(self):
        self._vender("9991234567", cortesia=True)
        self.assertEqual(Cliente.objects.count(), 0)
        self.assertEqual(Compra.objects.count(), 0)

    def test_el_comprobante_muestra_los_puntos(self):
        self._vender("9991234567")
        nota = Nota.objects.get()
        html = self.client.get(nota.get_absolute_url()).content.decode()
        self.assertIn("ganaste 26 puntos", html)


class ApiTests(TestCase):
    def setUp(self):
        cfg = ConfiguracionPrograma.get()
        cfg.api_token = "secreto-de-prueba"
        cfg.save()
        self.cabeceras = {"HTTP_AUTHORIZATION": "Bearer secreto-de-prueba"}

    def _post(self, nombre, cuerpo, **extra):
        return self.client.post(reverse(nombre), data=json.dumps(cuerpo),
                                content_type="application/json",
                                **{**self.cabeceras, **extra})

    def test_sin_token_no_se_puede(self):
        r = self.client.post(reverse("lealtad_api_purchases"), data="{}",
                             content_type="application/json")
        self.assertEqual(r.status_code, 401)

    def test_con_token_equivocado_tampoco(self):
        r = self._post("lealtad_api_purchases", {},
                       HTTP_AUTHORIZATION="Bearer otro")
        self.assertEqual(r.status_code, 401)

    def test_registrar_una_compra_con_el_formato_del_documento(self):
        r = self._post("lealtad_api_purchases", {
            "phone_number": "9991234567",
            "ticket_number": "A12345",
            "purchase_amount": 260,
            "purchase_date": "2026-06-30",
            "items": [{"sku": "SMOOTHIE", "qty": 2, "price": 130}],
        })
        self.assertEqual(r.status_code, 201)
        datos = r.json()
        self.assertEqual(datos["purchase"]["points_earned"], 26)
        self.assertEqual(datos["customer"]["total_points"], 26)
        self.assertEqual(datos["customer"]["phone_number"], "+529991234567")

    def test_el_mismo_ticket_dos_veces_no_duplica_puntos(self):
        cuerpo = {"phone_number": "9991234567", "ticket_number": "A1",
                  "purchase_amount": 130, "purchase_date": "2026-06-30"}
        self._post("lealtad_api_purchases", cuerpo)
        r = self._post("lealtad_api_purchases", cuerpo)
        self.assertTrue(r.json()["duplicate"])
        self.assertEqual(Cliente.objects.get().puntos_saldo, 13)

    def test_un_monto_invalido_devuelve_error_claro(self):
        r = self._post("lealtad_api_purchases",
                       {"phone_number": "9991234567", "purchase_amount": 0})
        self.assertEqual(r.status_code, 400)
        self.assertIn("purchase_amount", r.json()["error"])

    def test_consultar_los_puntos(self):
        cliente, _ = servicios.alta_cliente("9991234567")
        servicios.registrar_compra(cliente, Decimal("260"))
        r = self.client.get(
            reverse("lealtad_api_points", args=[cliente.pk]), **self.cabeceras)
        self.assertEqual(r.json()["customer"]["total_points"], 26)

    def test_canjear_por_api(self):
        cliente, _ = servicios.alta_cliente("9991234567")
        servicios.registrar_compra(cliente, Decimal("1300"))
        premio = Premio.objects.create(nombre="Latte", puntos_requeridos=100)

        r = self._post("lealtad_api_redeem",
                       {"phone_number": "9991234567", "reward_id": premio.pk})
        self.assertEqual(r.status_code, 201)
        self.assertEqual(r.json()["customer"]["total_points"], 30)

    def test_canjear_sin_saldo_devuelve_conflicto(self):
        servicios.alta_cliente("9991234567")
        premio = Premio.objects.create(nombre="Latte", puntos_requeridos=100)
        r = self._post("lealtad_api_redeem",
                       {"phone_number": "9991234567", "reward_id": premio.pk})
        self.assertEqual(r.status_code, 409)

    def test_el_webhook_marca_el_mensaje_como_leido(self):
        cliente, _ = servicios.alta_cliente("9991234567")
        plantilla = Plantilla.objects.create(clave="p", nombre="P", cuerpo="Hola")
        mensaje = mensajeria.encolar(cliente, plantilla)
        mensajeria.enviar(mensaje)

        self.client.post(
            reverse("lealtad_api_webhook"), content_type="application/json",
            data=json.dumps({"entry": [{"changes": [{"value": {
                "statuses": [{"id": mensaje.proveedor_id, "status": "read"}]}}]}]}))
        mensaje.refresh_from_db()
        self.assertEqual(mensaje.estado, Mensaje.Estado.LEIDO)
        self.assertIsNotNone(mensaje.leido_en)

    def test_responder_baja_apaga_los_mensajes(self):
        cliente, _ = servicios.alta_cliente("9991234567")
        self.client.post(
            reverse("lealtad_api_webhook"), content_type="application/json",
            data=json.dumps({"entry": [{"changes": [{"value": {
                "messages": [{"from": "529991234567",
                              "text": {"body": "BAJA"}}]}}]}]}))
        cliente.refresh_from_db()
        self.assertFalse(cliente.acepta_mensajes)


class VistasTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_superuser("admin", "a@x.mx", "x")
        self.client.force_login(self.user)
        self.receta = crea_receta()
        self.premio = Premio.objects.create(
            nombre="Latte gratis", puntos_requeridos=100, receta=self.receta)
        self.cliente, _ = servicios.alta_cliente("9991234567", nombre="Ana")

    def test_los_paneles_cargan(self):
        for nombre in ["lealtad_panel", "lealtad_clientes", "lealtad_premios",
                       "lealtad_mensajes", "lealtad_marketing", "lealtad_metricas"]:
            with self.subTest(nombre):
                self.assertEqual(self.client.get(reverse(nombre)).status_code, 200)

    def test_la_ficha_del_cliente_carga(self):
        r = self.client.get(reverse("lealtad_cliente", args=[self.cliente.pk]))
        self.assertEqual(r.status_code, 200)

    def test_la_tarjeta_publica_no_pide_contrasena(self):
        self.client.logout()
        r = self.client.get(self.cliente.get_absolute_url())
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Ana")

    def test_el_alta_autoservicio_crea_al_cliente(self):
        self.client.logout()
        r = self.client.post(reverse("lealtad_unete"), {
            "telefono": "9997654321", "nombre": "Luis", "acepta": "1"})
        self.assertEqual(r.status_code, 302)
        self.assertTrue(Cliente.objects.filter(telefono="+529997654321").exists())

    def test_el_alta_sin_consentimiento_no_registra(self):
        self.client.logout()
        r = self.client.post(reverse("lealtad_unete"),
                             {"telefono": "9997654321", "nombre": "Luis"})
        self.assertEqual(r.status_code, 200)
        self.assertFalse(Cliente.objects.filter(telefono="+529997654321").exists())

    def test_el_alta_con_telefono_malo_muestra_el_error(self):
        self.client.logout()
        r = self.client.post(reverse("lealtad_unete"),
                             {"telefono": "123", "acepta": "1"})
        self.assertContains(r, "10 dígitos")

    def test_buscar_devuelve_al_cliente(self):
        datos = self.client.get(reverse("lealtad_buscar"),
                                {"q": "999 123 4567"}).json()
        self.assertTrue(datos["encontrado"])
        self.assertEqual(datos["nombre"], "Ana")

    def test_buscar_por_codigo_corto(self):
        datos = self.client.get(reverse("lealtad_buscar"),
                                {"q": self.cliente.codigo}).json()
        self.assertTrue(datos["encontrado"])

    def test_canjear_desde_el_panel(self):
        servicios.registrar_compra(self.cliente, Decimal("1300"))
        self.client.post(reverse("lealtad_cliente_canjear", args=[self.cliente.pk]),
                         {"premio": self.premio.pk})
        self.cliente.refresh_from_db()
        self.assertEqual(self.cliente.puntos_saldo, 30)
        self.assertEqual(Canje.objects.count(), 1)

    def test_guardar_un_premio_desde_el_panel(self):
        self.client.post(reverse("lealtad_premio_guardar"), {
            "nombre": "Bites gratis", "puntos_requeridos": "80",
            "tipo": Premio.Tipo.PRODUCTO, "cantidad": "1", "valor": "0",
            "vigencia_dias": "30", "activo": "1"})
        premio = Premio.objects.get(nombre="Bites gratis")
        self.assertEqual(premio.puntos_requeridos, 80)

    def test_guardar_una_automatizacion_desde_el_panel(self):
        plantilla = Plantilla.objects.create(clave="p", nombre="P", cuerpo="Hola")
        self.client.post(reverse("lealtad_automatizacion_guardar"), {
            "nombre": "Mi regla", "disparador": Automatizacion.Disparador.INACTIVIDAD,
            "plantilla": plantilla.pk, "dias": "45", "hora_envio": "10",
            "dias_semana": ["0", "1", "2"], "no_repetir_dias": "20", "activa": "1"})
        regla = Automatizacion.objects.get(nombre="Mi regla")
        self.assertEqual(regla.dias, 45)
        self.assertEqual(regla.dias_semana, "012")

    def test_un_premio_con_canjes_se_desactiva_en_vez_de_borrarse(self):
        servicios.registrar_compra(self.cliente, Decimal("1300"))
        self.cliente.refresh_from_db()
        servicios.canjear(self.cliente, self.premio)

        self.client.post(reverse("lealtad_premio_eliminar", args=[self.premio.pk]))
        self.premio.refresh_from_db()
        self.assertFalse(self.premio.activo)


class MetricasTests(TestCase):
    def setUp(self):
        self.receta = crea_receta()
        self.premio = Premio.objects.create(
            nombre="Latte", puntos_requeridos=100, receta=self.receta)

    def test_la_retencion_solo_cuenta_a_quien_tuvo_tiempo_de_volver(self):
        hoy = timezone.localdate()
        fiel, _ = servicios.alta_cliente("9991111111")
        servicios.registrar_compra(fiel, Decimal("130"), ticket="1",
                                   fecha=hoy - timedelta(days=60))
        servicios.registrar_compra(fiel, Decimal("130"), ticket="2",
                                   fecha=hoy - timedelta(days=50))

        perdido, _ = servicios.alta_cliente("9992222222")
        servicios.registrar_compra(perdido, Decimal("130"), ticket="3",
                                   fecha=hoy - timedelta(days=60))

        nuevo, _ = servicios.alta_cliente("9993333333")
        servicios.registrar_compra(nuevo, Decimal("130"), ticket="4", fecha=hoy)

        r = metricas.retencion(30)
        self.assertEqual(r["cohorte"], 2)      # el de hoy no cuenta todavía
        self.assertEqual(r["repitieron"], 1)
        self.assertEqual(r["pct"], 50)

    def test_el_costo_del_pasivo_usa_el_costo_real_de_los_premios(self):
        cliente, _ = servicios.alta_cliente("9991234567")
        servicios.registrar_compra(cliente, Decimal("1000"))   # 100 pts
        # El latte cuesta $80 y vale 100 puntos → $0.80 por punto.
        self.assertEqual(metricas.costo_por_punto_promedio(), Decimal("0.8"))
        self.assertEqual(metricas.costo_pasivo(), Decimal("80.0"))

    def test_el_resumen_de_clientes_separa_activos_de_inactivos(self):
        activo, _ = servicios.alta_cliente("9991111111")
        servicios.registrar_compra(activo, Decimal("130"), ticket="1")
        dormido, _ = servicios.alta_cliente("9992222222")
        servicios.registrar_compra(
            dormido, Decimal("130"), ticket="2",
            fecha=timezone.localdate() - timedelta(days=90))

        resumen = metricas.resumen_clientes()
        self.assertEqual(resumen["total"], 2)
        self.assertEqual(resumen["activos"], 1)
        self.assertEqual(resumen["inactivos"], 1)


class ComandosTests(TestCase):
    def test_el_seed_deja_el_programa_listo(self):
        call_command("seed_lealtad", "--reset", stdout=StringIO())
        self.assertEqual(Nivel.objects.count(), 5)
        self.assertEqual(Premio.objects.filter(activo=True).count(), 4)
        self.assertEqual(Automatizacion.objects.filter(activa=True).count(), 9)
        # Los umbrales del documento original.
        self.assertEqual(
            list(Nivel.objects.values_list("puntos_requeridos", flat=True)),
            [0, 100, 200, 350, 500])

    def test_el_seed_se_puede_correr_dos_veces(self):
        call_command("seed_lealtad", stdout=StringIO())
        call_command("seed_lealtad", stdout=StringIO())
        self.assertEqual(Nivel.objects.count(), 5)

    def test_el_runner_corre_sin_datos(self):
        call_command("lealtad_run", "--forzar-horario", stdout=StringIO())

    def test_el_runner_encola_y_despacha(self):
        call_command("seed_lealtad", stdout=StringIO())
        cliente, _ = servicios.alta_cliente("9991234567", nombre="Ana")
        servicios.registrar_compra(
            cliente, Decimal("130"),
            fecha=timezone.localdate() - timedelta(days=60))

        call_command("lealtad_run", "--forzar-horario", stdout=StringIO())
        self.assertTrue(
            Mensaje.objects.filter(estado=Mensaje.Estado.ENVIADO).exists())
