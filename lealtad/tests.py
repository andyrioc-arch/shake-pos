"""Pruebas del programa de lealtad.

Cubren lo que de verdad puede costar dinero o confianza: el cálculo de puntos,
la caducidad, los canjes, las automatizaciones y que una venta nunca se caiga
por culpa del programa.
"""

import hashlib
import hmac
import json
from datetime import date, timedelta
from decimal import Decimal
from io import StringIO

from django.contrib.auth.models import User
from django.core.cache import cache
from django.core.management import call_command
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from inventario.models import Ingrediente, Nota, Receta, RecetaIngrediente, Venta
from lealtad import automatizaciones, mensajeria, metricas, servicios, views
from lealtad.models import (
    LARGO_NOMBRE, Automatizacion, Campana, Canje, Cliente, Compra,
    ConfiguracionPrograma, Mensaje, MovimientoPuntos, Nivel, Plantilla, Premio,
    PromocionPuntos, TelefonoInvalido, normaliza_nombre, normaliza_telefono,
    telefono_bonito,
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


class NormalizaNombreTests(SimpleTestCase):
    """Postgres rechaza lo que no cabe en la columna; SQLite lo acepta callado,
    así que sin este tope el error solo aparecería en producción."""

    def test_recorta_al_largo_de_la_columna(self):
        self.assertEqual(len(normaliza_nombre("A" * 200)), LARGO_NOMBRE)

    def test_al_truncar_no_deja_un_espacio_al_final(self):
        cortado = normaliza_nombre("A" * (LARGO_NOMBRE - 1) + " Beltrán")
        self.assertEqual(cortado, cortado.rstrip())

    def test_colapsa_los_espacios_de_sobra(self):
        self.assertEqual(normaliza_nombre("  Ana   María  "), "Ana María")

    def test_un_nombre_ausente_queda_vacio(self):
        self.assertEqual(normaliza_nombre(None), "")


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
        cliente = servicios.ajustar_puntos(self.cliente, 50, "Cortesía")
        self.assertEqual(cliente.puntos_saldo, 50)
        cliente = servicios.ajustar_puntos(cliente, -20, "Corrección")
        self.assertEqual(cliente.puntos_saldo, 30)

    def test_no_se_puede_dejar_el_saldo_en_negativo(self):
        servicios.ajustar_puntos(self.cliente, 10, "Cortesía")
        with self.assertRaises(servicios.ErrorLealtad):
            servicios.ajustar_puntos(self.cliente, -50, "Error")

    def test_los_puntos_de_cortesia_si_se_pueden_gastar(self):
        """Los ajustes también son lotes: el canje debe poder consumirlos."""
        cliente = servicios.ajustar_puntos(self.cliente, 100, "Cortesía")
        premio = Premio.objects.create(nombre="Latte", puntos_requeridos=60)
        servicios.canjear(cliente, premio)

        cliente.refresh_from_db()
        self.assertEqual(cliente.puntos_saldo, 40)
        # El lote de cortesía quedó consumido, no colgado en 100.
        lote = cliente.movimientos.get(tipo=MovimientoPuntos.Tipo.AJUSTE)
        self.assertEqual(lote.saldo_lote, 40)

    def test_los_puntos_de_cortesia_tambien_caducan(self):
        cliente = servicios.ajustar_puntos(self.cliente, 100, "Cortesía")
        lote = cliente.movimientos.get(tipo=MovimientoPuntos.Tipo.AJUSTE)
        self.assertIsNotNone(lote.expira_el)
        lote.expira_el = timezone.localdate() - timedelta(days=1)
        lote.save()

        self.assertEqual(servicios.expirar_puntos(), 100)
        cliente.refresh_from_db()
        self.assertEqual(cliente.puntos_saldo, 0)

    def test_el_saldo_siempre_cuadra_con_los_lotes(self):
        """Invariante del libro: saldo == suma de los lotes vivos."""
        servicios.registrar_compra(self.cliente, Decimal("1300"), ticket="a")
        self.cliente.refresh_from_db()
        servicios.ajustar_puntos(self.cliente, 40, "Cortesía")
        premio = Premio.objects.create(nombre="Latte", puntos_requeridos=100)
        self.cliente.refresh_from_db()
        canje = servicios.canjear(self.cliente, premio)
        servicios.cancelar_canje(canje)

        self.cliente.refresh_from_db()
        lotes = sum(m.saldo_lote for m in servicios.lotes_vivos(self.cliente))
        self.assertEqual(self.cliente.puntos_saldo, lotes)


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

    def test_canjear_y_cancelar_no_regala_puntos_de_por_vida(self):
        """Si la devolución contara como ganancia, se subiría de nivel gratis."""
        servicios.registrar_compra(self.cliente, Decimal("1300"))
        self.cliente.refresh_from_db()
        self.assertEqual(self.cliente.puntos_historicos, 130)

        for _ in range(5):
            self.cliente.refresh_from_db()
            servicios.cancelar_canje(servicios.canjear(self.cliente, self.premio))

        self.cliente.refresh_from_db()
        self.assertEqual(self.cliente.puntos_saldo, 130)
        self.assertEqual(self.cliente.puntos_historicos, 130)
        self.assertEqual(self.cliente.nivel, self.fan)

    def test_cancelar_no_le_estrena_vigencia_a_los_puntos(self):
        """Cancelar un canje no debe revivir puntos a punto de caducar."""
        servicios.registrar_compra(self.cliente, Decimal("1300"))
        lote = self.cliente.movimientos.get()
        lote.expira_el = timezone.localdate() + timedelta(days=3)
        lote.save()

        self.cliente.refresh_from_db()
        canje = servicios.canjear(self.cliente, self.premio)
        servicios.cancelar_canje(canje)

        devuelto = self.cliente.movimientos.get(
            tipo=MovimientoPuntos.Tipo.DEVOLUCION)
        self.assertEqual(devuelto.expira_el, lote.expira_el)


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

    def _vender(self, telefono="", cortesia=False, nombre=""):
        datos = {
            "productos_json": json.dumps([{"receta": self.receta.pk, "cantidad": 2}]),
            "metodo_pago": "efectivo",
            "pago_con": "300",
            "fecha": timezone.localdate().isoformat(),
            "telefono_lealtad": telefono,
            # Es el mismo campo para las dos cosas: el nombre con el que se
            # canta el pedido es el que se le pone al cliente en lealtad.
            "nombre_cliente": nombre or "Mostrador",
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

    def test_el_nombre_capturado_en_caja_se_guarda(self):
        self._vender("9991234567", nombre="Andrea")
        self.assertEqual(Cliente.objects.get().nombre, "Andrea")

    def test_el_nombre_completa_a_un_cliente_que_no_lo_tenia(self):
        servicios.alta_cliente("9991234567")
        self._vender("9991234567", nombre="Andrea")
        self.assertEqual(Cliente.objects.get().nombre, "Andrea")

    def test_el_nombre_en_caja_no_pisa_el_que_ya_tenia(self):
        servicios.alta_cliente("9991234567", nombre="Andrea")
        self._vender("9991234567", nombre="Equivocado")
        self.assertEqual(Cliente.objects.get().nombre, "Andrea")

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


@override_settings(WHATSAPP_APP_SECRET="secreto-de-la-app")
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

    def _webhook(self, valor):
        """POST al webhook firmado como lo haría Meta."""
        cuerpo = json.dumps({"entry": [{"changes": [{"value": valor}]}]})
        firma = hmac.new(b"secreto-de-la-app", cuerpo.encode(),
                         hashlib.sha256).hexdigest()
        return self.client.post(
            reverse("lealtad_api_webhook"), data=cuerpo,
            content_type="application/json",
            HTTP_X_HUB_SIGNATURE_256=f"sha256={firma}")

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
        for malo in [0, -5, "abc", None, "NaN", "Infinity"]:
            with self.subTest(malo=malo):
                r = self._post("lealtad_api_purchases",
                               {"phone_number": "9991234567",
                                "purchase_amount": malo})
                self.assertEqual(r.status_code, 400)
                self.assertIn("purchase_amount", r.json()["error"])

    def test_un_id_que_no_es_numero_no_revienta(self):
        servicios.alta_cliente("9991234567")
        for cuerpo in [{"customer_id": "abc", "reward_id": 1},
                       {"customer_id": None, "reward_id": "x"},
                       {"phone_number": "9991234567", "reward_id": "  "}]:
            with self.subTest(cuerpo=cuerpo):
                r = self._post("lealtad_api_redeem", cuerpo)
                self.assertEqual(r.status_code, 404)

    def test_un_token_con_acentos_no_revienta(self):
        r = self._post("lealtad_api_purchases", {},
                       HTTP_AUTHORIZATION="Bearer café")
        self.assertEqual(r.status_code, 401)

    def test_un_ticket_de_otro_cliente_se_rechaza(self):
        """El folio es único global: acreditárselo a otro sería regalar puntos."""
        self._post("lealtad_api_purchases",
                   {"phone_number": "9991111111", "ticket_number": "A1",
                    "purchase_amount": 130})
        r = self._post("lealtad_api_purchases",
                       {"phone_number": "9992222222", "ticket_number": "A1",
                        "purchase_amount": 130})
        self.assertEqual(r.status_code, 409)
        self.assertEqual(
            Cliente.objects.get(telefono="+529992222222").puntos_saldo, 0)

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

        self._webhook({"statuses": [{"id": mensaje.proveedor_id, "status": "read"}]})
        mensaje.refresh_from_db()
        self.assertEqual(mensaje.estado, Mensaje.Estado.LEIDO)
        self.assertIsNotNone(mensaje.leido_en)

    def test_responder_baja_apaga_los_mensajes(self):
        cliente, _ = servicios.alta_cliente("9991234567")
        self._webhook({"messages": [{"from": "529991234567",
                                     "text": {"body": "BAJA"}}]})
        cliente.refresh_from_db()
        self.assertFalse(cliente.acepta_mensajes)

    def test_un_webhook_sin_firma_se_rechaza(self):
        """Sin esto, cualquiera da de baja clientes desde internet."""
        cliente, _ = servicios.alta_cliente("9991234567")
        r = self.client.post(
            reverse("lealtad_api_webhook"), content_type="application/json",
            data=json.dumps({"entry": [{"changes": [{"value": {
                "messages": [{"from": "529991234567",
                              "text": {"body": "BAJA"}}]}}]}]}))
        cliente.refresh_from_db()
        self.assertEqual(r.status_code, 403)
        self.assertTrue(cliente.acepta_mensajes)

    def test_un_webhook_con_firma_equivocada_se_rechaza(self):
        r = self.client.post(
            reverse("lealtad_api_webhook"), content_type="application/json",
            data=json.dumps({"entry": []}),
            HTTP_X_HUB_SIGNATURE_256="sha256=" + "0" * 64)
        self.assertEqual(r.status_code, 403)

    def test_un_status_sin_id_no_toca_los_mensajes_en_cola(self):
        """Buscar por id vacío coincidía con todo lo que aún no se despacha."""
        cliente, _ = servicios.alta_cliente("9991234567")
        plantilla = Plantilla.objects.create(clave="p", nombre="P", cuerpo="Hola")
        encolado = mensajeria.encolar(cliente, plantilla)

        self._webhook({"statuses": [{"status": "failed",
                                     "errors": [{"title": "boom"}]}]})
        encolado.refresh_from_db()
        self.assertEqual(encolado.estado, Mensaje.Estado.PROGRAMADO)

    def test_un_fallo_tardio_no_borra_un_leido(self):
        cliente, _ = servicios.alta_cliente("9991234567")
        plantilla = Plantilla.objects.create(clave="p", nombre="P", cuerpo="Hola")
        mensaje = mensajeria.enviar(mensajeria.encolar(cliente, plantilla))

        self._webhook({"statuses": [{"id": mensaje.proveedor_id, "status": "read"}]})
        self._webhook({"statuses": [{"id": mensaje.proveedor_id,
                                     "status": "failed"}]})
        mensaje.refresh_from_db()
        self.assertEqual(mensaje.estado, Mensaje.Estado.LEIDO)


class VistasTests(TestCase):
    def setUp(self):
        # El límite de altas por IP vive en la caché del proceso, que sobrevive
        # entre tests: sin limpiarla, un test le gasta los intentos al siguiente.
        cache.clear()
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

    def test_un_numero_ajeno_no_revela_la_tarjeta_de_su_dueno(self):
        """Nadie verifica el número: no puede servir para espiar a otro."""
        servicios.registrar_compra(self.cliente, Decimal("1300"))
        self.client.logout()

        r = self.client.post(reverse("lealtad_unete"), {
            "telefono": "9991234567", "nombre": "Impostor", "acepta": "1"})

        self.assertEqual(r.status_code, 200)     # no redirige a la tarjeta
        cuerpo = r.content.decode()
        self.assertNotIn("Ana", cuerpo)
        self.assertNotIn(str(self.cliente.token), cuerpo)
        self.assertNotIn("130", cuerpo)

    def test_demasiadas_altas_desde_la_misma_ip_se_frenan(self):
        cache.clear()
        self.client.logout()
        for i in range(views.ALTAS_MAX):
            self.client.post(reverse("lealtad_unete"), {
                "telefono": f"55100000{i:02d}", "acepta": "1"})
        antes = Cliente.objects.count()

        self.client.post(reverse("lealtad_unete"),
                         {"telefono": "5510009999", "acepta": "1"})
        self.assertEqual(Cliente.objects.count(), antes)

    def test_cada_cliente_tiene_un_codigo_unico(self):
        codigos = {servicios.alta_cliente(f"55200000{i:02d}")[0].codigo
                   for i in range(30)}
        self.assertEqual(len(codigos), 30)
        self.assertTrue(all(len(c) == 6 for c in codigos))

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

    def test_buscar_dice_si_hay_que_pedir_el_nombre(self):
        # Es el servidor quien decide, no la caja: la regla de "a quién le
        # falta nombre" vive junto a la que lo escribe.
        con_nombre = self.client.get(reverse("lealtad_buscar"),
                                     {"q": "999 123 4567"}).json()
        self.assertFalse(con_nombre["necesita_nombre"])

        servicios.alta_cliente("9998887766")
        sin_nombre = self.client.get(reverse("lealtad_buscar"),
                                     {"q": "999 888 7766"}).json()
        self.assertTrue(sin_nombre["necesita_nombre"])

        nuevo = self.client.get(reverse("lealtad_buscar"),
                                {"q": "999 000 0000"}).json()
        self.assertFalse(nuevo["encontrado"])
        self.assertTrue(nuevo["necesita_nombre"])

    def test_editar_un_cliente_no_desborda_las_columnas(self):
        # El panel de edición escribe nombre y notas directo, sin pasar por
        # alta_cliente. En Postgres un valor más largo que la columna es un 500;
        # por eso el recorte vive en Cliente.save() y no en cada vista.
        self.client.post(reverse("lealtad_cliente_editar", args=[self.cliente.pk]),
                         {"nombre": "N" * 200, "notas": "X" * 500,
                          "acepta_mensajes": "1"})
        self.cliente.refresh_from_db()
        self.assertEqual(len(self.cliente.nombre), LARGO_NOMBRE)
        self.assertEqual(len(self.cliente.notas), 200)

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
            "tipo": Premio.Tipo.PRODUCTO, "receta": self.receta.pk,
            "cantidad": "1", "valor": "0",
            "vigencia_dias": "30", "activo": "1"})
        premio = Premio.objects.get(nombre="Bites gratis")
        self.assertEqual(premio.puntos_requeridos, 80)

    def test_un_premio_de_producto_sin_receta_no_se_guarda(self):
        """Se atrapa al configurarlo, no en el mostrador con el cliente
        enfrente: sin receta no hay de dónde descontar el inventario."""
        resp = self.client.post(reverse("lealtad_premio_guardar"), {
            "nombre": "Sin receta", "puntos_requeridos": "80",
            "tipo": Premio.Tipo.PRODUCTO, "cantidad": "1", "valor": "0",
            "vigencia_dias": "30", "activo": "1"}, follow=True)

        self.assertFalse(Premio.objects.filter(nombre="Sin receta").exists())
        self.assertContains(resp, "necesita una receta ligada")

    def test_un_descuento_no_necesita_receta(self):
        self.client.post(reverse("lealtad_premio_guardar"), {
            "nombre": "10% off", "puntos_requeridos": "50",
            "tipo": Premio.Tipo.DESCUENTO_PCT, "cantidad": "1", "valor": "10",
            "vigencia_dias": "30", "activo": "1"})

        self.assertTrue(Premio.objects.filter(nombre="10% off").exists())

    def test_guardar_un_premio_ligando_el_producto(self):
        self.client.post(reverse("lealtad_premio_guardar"), {
            "pk": self.premio.pk, "nombre": "Latte gratis",
            "puntos_requeridos": "100", "tipo": Premio.Tipo.PRODUCTO,
            "receta": self.receta.pk, "cantidad": "1", "valor": "0",
            "vigencia_dias": "30", "activo": "1"})
        self.premio.refresh_from_db()
        self.assertEqual(self.premio.receta, self.receta)
        self.assertEqual(self.premio.costo_estimado, Decimal("80"))

    def test_los_selects_vacios_significan_ninguno(self):
        """El formulario manda "" en los campos opcionales, no los omite."""
        self.client.post(reverse("lealtad_premio_guardar"), {
            "nombre": "Sin producto", "puntos_requeridos": "50",
            "tipo": Premio.Tipo.LIBRE, "receta": "", "nivel_minimo": "",
            "cantidad": "1", "valor": "0", "vigencia_dias": "30", "activo": "1"})
        premio = Premio.objects.get(nombre="Sin producto")
        self.assertIsNone(premio.receta)
        self.assertIsNone(premio.nivel_minimo)

    def test_un_disparador_inventado_se_rechaza(self):
        """Si se guardara, la regla se vería activa y no dispararía nunca."""
        plantilla = Plantilla.objects.create(clave="z", nombre="Z", cuerpo="Hola")
        self.client.post(reverse("lealtad_automatizacion_guardar"), {
            "nombre": "Rota", "disparador": "compras", "plantilla": plantilla.pk,
            "dias": "30", "hora_envio": "11", "activa": "1"})
        self.assertFalse(Automatizacion.objects.filter(nombre="Rota").exists())

    def test_los_valores_fuera_de_rango_se_acotan(self):
        plantilla = Plantilla.objects.create(clave="y", nombre="Y", cuerpo="Hola")
        self.client.post(reverse("lealtad_automatizacion_guardar"), {
            "nombre": "Acotada", "disparador": Automatizacion.Disparador.COMPRA,
            "plantilla": plantilla.pk, "dias": "-5", "hora_envio": "99",
            "umbral_porcentaje": "500", "no_repetir_dias": "-1", "activa": "1"})
        regla = Automatizacion.objects.get(nombre="Acotada")
        self.assertEqual(regla.dias, 0)
        self.assertEqual(regla.hora_envio, 23)
        self.assertEqual(regla.umbral_porcentaje, 100)
        self.assertEqual(regla.no_repetir_dias, 0)

    def test_un_nombre_de_premio_repetido_avisa_en_vez_de_reventar(self):
        r = self.client.post(reverse("lealtad_premio_guardar"), {
            "nombre": self.premio.nombre, "puntos_requeridos": "50",
            "tipo": Premio.Tipo.PRODUCTO, "cantidad": "1", "valor": "0",
            "vigencia_dias": "30", "activo": "1"})
        self.assertEqual(r.status_code, 302)
        self.assertEqual(Premio.objects.filter(nombre=self.premio.nombre).count(), 1)

    def test_no_se_puede_redirigir_a_otro_dominio(self):
        servicios.registrar_compra(self.cliente, Decimal("1300"))
        self.cliente.refresh_from_db()
        canje = servicios.canjear(self.cliente, self.premio)
        r = self.client.post(reverse("lealtad_canje_entregar", args=[canje.pk]),
                             {"volver": "https://sitio-malicioso.mx/robar"})
        self.assertNotIn("sitio-malicioso", r.url)

    def test_el_filtro_de_dias_no_desborda(self):
        r = self.client.get(reverse("lealtad_marketing"), {"dias": "99999999999"})
        self.assertEqual(r.status_code, 200)

    def test_una_automatizacion_sin_nivel_objetivo_se_guarda(self):
        plantilla = Plantilla.objects.create(clave="q", nombre="Q", cuerpo="Hola")
        self.client.post(reverse("lealtad_automatizacion_guardar"), {
            "nombre": "Sin nivel", "disparador": Automatizacion.Disparador.COMPRA,
            "plantilla": plantilla.pk, "nivel_objetivo": "", "dias": "30",
            "hora_envio": "11", "no_repetir_dias": "0", "activa": "1"})
        self.assertIsNone(
            Automatizacion.objects.get(nombre="Sin nivel").nivel_objetivo)

    def test_una_campana_sin_nivel_ni_plantilla_se_guarda(self):
        self.client.post(reverse("lealtad_campana_guardar"), {
            "nombre": "Promo libre", "cuerpo": "Hola {nombre}",
            "segmento": Campana.Segmento.TODOS, "nivel": "", "plantilla": ""})
        self.assertTrue(Campana.objects.filter(nombre="Promo libre").exists())

    def test_canjear_sin_elegir_premio_avisa_en_vez_de_reventar(self):
        r = self.client.post(
            reverse("lealtad_cliente_canjear", args=[self.cliente.pk]),
            {"premio": ""})
        self.assertEqual(r.status_code, 302)
        self.assertEqual(Canje.objects.count(), 0)

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


class CanjeConsumeInventarioTests(TestCase):
    """Entregar un shake gratis gasta la misma leche que venderlo.

    Antes el canje solo movía puntos: el insumo desaparecía del almacén sin
    que ningún número lo registrara, el inventario contaba de más y el costo
    de la promoción no aparecía en ninguna parte.
    """

    def setUp(self):
        cache.clear()
        # Ojo: `Compra` a secas es la del programa de lealtad. La de insumos
        # es otra cosa y vive en inventario.
        from contabilidad import posting
        from inventario.models import Compra as CompraDeInsumo
        posting.crear_catalogo()
        self.ing = Ingrediente.objects.create(
            nombre="Leche", unidad_compra="litro", cantidad_por_unidad=1000,
            unidad_receta="ml", costo_unidad_compra=Decimal("30.00"))
        self.receta = Receta.objects.create(
            nombre="Latte", precio_venta=Decimal("100.00"))
        RecetaIngrediente.objects.create(
            receta=self.receta, ingrediente=self.ing, cantidad=200)
        CompraDeInsumo.objects.create(
            fecha=date(2026, 8, 1), ingrediente=self.ing,
            cantidad=Decimal("1"), monto_total=Decimal("30"))
        self.cliente, _ = servicios.alta_cliente("9991234567", nombre="Ana")
        servicios.ajustar_puntos(self.cliente, 500, "Semilla")

    def _canjear(self, premio):
        return servicios.canjear(self.cliente, premio)

    def test_entregar_un_producto_descuenta_inventario_y_guarda_su_costo(self):
        premio = Premio.objects.create(
            nombre="Latte gratis", puntos_requeridos=100,
            tipo=Premio.Tipo.PRODUCTO, receta=self.receta, cantidad=1)
        antes = self.ing.stock_disponible

        canje = servicios.entregar_canje(self._canjear(premio))

        self.assertIsNotNone(canje.venta)
        self.assertTrue(canje.venta.es_cortesia)
        self.assertEqual(canje.costo, Decimal("6.00"))    # 200 ml × 0.03
        self.assertEqual(self.ing.stock_disponible, antes - 200)

    def test_el_costo_del_premio_entra_a_mercadotecnia_y_no_a_costo_de_ventas(self):
        from contabilidad import posting
        premio = Premio.objects.create(
            nombre="Latte gratis", puntos_requeridos=100,
            tipo=Premio.Tipo.PRODUCTO, receta=self.receta, cantidad=1)

        servicios.entregar_canje(self._canjear(premio))

        hoy = timezone.localdate()
        er = posting.estado_resultados(hoy.year, hoy.month)
        self.assertEqual(er["total_costo_ventas"], Decimal("0"))
        self.assertTrue(posting.balanza_comprobacion(hoy.year, hoy.month)["cuadra"])

    def test_un_premio_sin_receta_avisa_en_vez_de_reventar(self):
        """`Venta.receta` es obligatoria: sin este mensaje el cajero vería un
        500 en la cara del cliente."""
        premio = Premio.objects.create(
            nombre="Algo gratis", puntos_requeridos=100,
            tipo=Premio.Tipo.PRODUCTO, receta=None, cantidad=1)
        canje = self._canjear(premio)

        with self.assertRaises(servicios.ErrorLealtad) as ctx:
            servicios.entregar_canje(canje)

        self.assertIn("no tiene producto ligado", str(ctx.exception))
        canje.refresh_from_db()
        self.assertEqual(canje.estado, Canje.Estado.PENDIENTE)

    def test_un_descuento_no_toca_el_inventario(self):
        premio = Premio.objects.create(
            nombre="10% off", puntos_requeridos=100,
            tipo=Premio.Tipo.DESCUENTO_PCT, valor=Decimal("10"))
        antes = self.ing.stock_disponible

        canje = servicios.entregar_canje(self._canjear(premio))

        self.assertIsNone(canje.venta)
        self.assertEqual(self.ing.stock_disponible, antes)
        # Su costo sigue siendo ingreso no percibido, no insumo.
        self.assertEqual(canje.costo, premio.costo_estimado)

    def test_sin_compras_capturadas_no_se_publica_que_el_premio_salio_gratis(self):
        """Cero no es un valor, es una ausencia.

        Con capas faltantes el FIFO deja `costo_fifo = 0.00`. Guardarlo como
        costo real diría que regalar shakes no cuesta nada, justo cuando menos
        se sabe. Se deja en NULL y el estimado del catálogo responde.
        """
        otro = Ingrediente.objects.create(
            nombre="Cacao", unidad_compra="kg", cantidad_por_unidad=1000,
            unidad_receta="g", costo_unidad_compra=Decimal("400.00"))
        receta = Receta.objects.create(
            nombre="Sin respaldo", precio_venta=Decimal("100.00"))
        RecetaIngrediente.objects.create(
            receta=receta, ingrediente=otro, cantidad=50)     # nunca comprado
        premio = Premio.objects.create(
            nombre="Cacao gratis", puntos_requeridos=100,
            tipo=Premio.Tipo.PRODUCTO, receta=receta, cantidad=1)

        canje = servicios.entregar_canje(self._canjear(premio))

        # `costo_de_ventas` de la cortesía cae al estimado del catálogo cuando
        # el costo no está completo: 50 g × 0.40. Nunca al cero del FIFO.
        self.assertTrue(canje.venta.costo_incompleto)
        self.assertEqual(canje.costo, Decimal("20.00"))

    def test_las_metricas_leen_el_costo_real_cuando_existe(self):
        premio = Premio.objects.create(
            nombre="Latte gratis", puntos_requeridos=100,
            tipo=Premio.Tipo.PRODUCTO, receta=self.receta, cantidad=1)
        servicios.entregar_canje(self._canjear(premio))

        canje = Canje.objects.get()
        # Se lee de la cortesía, no de una copia: cuando el costeo rehaga el
        # FIFO —al capturar una compra vieja— este número se mueve con él.
        self.assertEqual(canje.costo, canje.venta.costo_de_ventas)


class NotaAnunciaHitosTests(TestCase):
    """La nota es lo único que el cliente ve; ahí se le dice qué ganó."""

    def setUp(self):
        cache.clear()
        self.receta = crea_receta()
        self.oro = Nivel.objects.create(
            nombre="Oro", puntos_requeridos=10, beneficios="10% en todo")
        self.premio = Premio.objects.create(
            nombre="Latte gratis", puntos_requeridos=5, receta=self.receta)
        self.cliente, _ = servicios.alta_cliente("9991234567", nombre="Ana")

    def _nota(self, total="130"):
        nota = Nota.objects.create(fecha=date(2026, 8, 5), total=Decimal(total))
        servicios.registrar_compra(self.cliente, Decimal(total), nota=nota)
        return nota

    def test_la_nota_anuncia_el_nivel_que_esta_compra_desbloqueo(self):
        nota = self._nota()      # $130 = 13 puntos, cruza los 10 de Oro

        resp = self.client.get(nota.get_absolute_url())

        self.assertContains(resp, "Subiste a Oro")
        self.assertContains(resp, "10% en todo")

    def test_la_siguiente_compra_ya_no_lo_vuelve_a_anunciar(self):
        self._nota()
        segunda = self._nota()

        self.assertContains(self.client.get(segunda.get_absolute_url()),
                            "puntos disponibles")
        self.assertNotContains(self.client.get(segunda.get_absolute_url()),
                               "Subiste a")

    def test_una_nota_vieja_no_felicita_por_un_nivel_posterior(self):
        """El hito se guarda al registrar la compra, así que cada nota dice lo
        que pasó en ELLA. La primera no cruzó a Oro; la segunda sí."""
        primera = self._nota("30")     # 3 puntos, todavía sin nivel
        segunda = self._nota("130")    # aquí sí cruza a Oro

        self.assertNotContains(self.client.get(primera.get_absolute_url()),
                               "Subiste a")
        self.assertContains(self.client.get(segunda.get_absolute_url()),
                            "Subiste a Oro")

    def test_el_hito_sigue_ahi_cuando_el_cliente_vuelve_a_comprar(self):
        """La nota es un documento: lo que anunció el día que se entregó no
        puede desaparecer después, y menos ahora que el cliente se la guarda
        en PDF."""
        nota = self._nota()            # cruza a Oro
        self._nota()
        self._nota()

        self.assertContains(self.client.get(nota.get_absolute_url()),
                            "Subiste a Oro")

    def test_un_ajuste_de_puntos_no_se_le_acredita_a_una_compra(self):
        """`puntos_historicos` también sube con `ajustar_puntos`, que no crea
        una compra. Deducir el nivel restando los puntos de la compra le
        colgaba a esa nota un ascenso que desbloqueó el ajuste."""
        nota = self._nota("30")        # 3 puntos: no alcanza los 10 de Oro
        servicios.ajustar_puntos(self.cliente, 8, "Cortesía del mostrador")
        self.cliente.refresh_from_db()

        self.assertEqual(self.cliente.nivel, self.oro)
        self.assertNotContains(self.client.get(nota.get_absolute_url()),
                               "Subiste a")

    def test_anuncia_el_premio_ganado_aunque_falte_para_el_siguiente(self):
        Premio.objects.create(nombre="Shake gratis", puntos_requeridos=500)
        nota = self._nota()            # 13 puntos: alcanza el de 5, no el de 500

        resp = self.client.get(nota.get_absolute_url())

        self.assertContains(resp, "Ya puedes canjear Latte gratis")
        self.assertContains(resp, "Te faltan")

    def test_sin_puntos_no_hay_nada_que_anunciar(self):
        nota = self._nota("5")         # $5 no llega ni a un punto

        resp = self.client.get(nota.get_absolute_url())

        self.assertNotContains(resp, "Subiste a")


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

    def test_baseline_del_indicador_del_5_por_ciento(self):
        """Congela el número que Andy mira: cuánto del margen se come el programa.

        Hoy `margen_de_miembros` usa la ganancia con el costo ESTIMADO del
        catálogo. Cuando el costo pase a ser el FIFO real, este número se mueve
        —y debe moverse a propósito, no por accidente—: el test obliga a que
        quien lo cambie actualice el valor a mano.
        """
        cliente, _ = servicios.alta_cliente("9991234567")
        nota = Nota.objects.create(fecha=timezone.localdate(),
                                   metodo_pago="efectivo", total=Decimal("130"))
        Venta.objects.create(fecha=timezone.localdate(), receta=self.receta,
                             cantidad=1, nota=nota)
        servicios.registrar_compra(cliente, Decimal("130"), nota=nota)

        # Precio 130 − costo de catálogo 80 = 50 de margen.
        self.assertEqual(metricas.margen_de_miembros(), Decimal("50"))

        r = metricas.resumen_retorno()
        self.assertEqual(r["margen_miembros"], Decimal("50"))
        self.assertEqual(r["costo_premios"], Decimal("0"))   # sin canjes aún
        self.assertEqual(r["pct_margen"], 0)
        self.assertTrue(r["dentro_objetivo"])

        servicios.registrar_compra(cliente, Decimal("1000"), ticket="x")
        servicios.canjear(cliente, self.premio)
        r = metricas.resumen_retorno()
        # El latte cuesta 80 contra 50 de margen: 160%, muy fuera del 5%.
        self.assertEqual(r["costo_premios"], Decimal("80"))
        self.assertEqual(r["pct_margen"], 160)
        self.assertFalse(r["dentro_objetivo"])

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
