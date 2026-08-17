from decimal import Decimal
from datetime import date
from django.test import TestCase
from contabilidad.models import Cuenta, Asiento, MovimientoContable
from contabilidad import posting


class FifoCogsTests(TestCase):
    """Costo de Ventas por FIFO (IAS 2): consume capas de la más antigua primero."""

    def setUp(self):
        from inventario.models import Ingrediente, Receta, RecetaIngrediente
        from contabilidad.models import Movimiento
        posting.crear_catalogo()
        self.Movimiento = Movimiento
        i = Ingrediente.objects.create(
            nombre="Leche", categoria="liquido", unidad_compra="litro",
            cantidad_por_unidad=Decimal("1000"), unidad_receta="ml",
            costo_unidad_compra=Decimal("20"))
        self.r = Receta.objects.create(
            nombre="Shake", perfil="x", precio_venta=Decimal("100"))
        RecetaIngrediente.objects.create(receta=self.r, ingrediente=i, cantidad=Decimal("200"))
        self.i = i

    def _compra(self, dia, costo):
        from inventario.models import Compra
        c = Compra.objects.create(fecha=date(2026, 7, dia), ingrediente=self.i,
                                  cantidad=Decimal("1"), monto_total=Decimal(costo))
        return c

    def _venta(self, dia, cant):
        from inventario.models import Venta
        from inventario import costeo
        v = Venta.objects.create(fecha=date(2026, 7, dia), receta=self.r, cantidad=cant)
        costeo.costear_venta(v)
        return v

    def test_fifo_cruza_capas_y_cuadra(self):
        self._compra(1, "20")   # capa 1: 1000ml @ 0.02
        self._compra(2, "30")   # capa 2: 1000ml @ 0.03
        vA = self._venta(5, 4)  # 800ml -> 16.00 (toda capa 1)
        vB = self._venta(6, 2)  # 400ml -> 200*0.02 + 200*0.03 = 10.00 (cruza)

        vA.refresh_from_db(); vB.refresh_from_db()
        self.assertEqual(vA.costo_fifo, Decimal("16.00"))
        self.assertEqual(vB.costo_fifo, Decimal("10.00"))

        er = posting.estado_resultados(2026, 7)
        self.assertEqual(er["total_ingresos"], Decimal("600.00"))     # 6 × $100
        self.assertEqual(er["total_costo_ventas"], Decimal("26.00"))
        self.assertEqual(er["utilidad_bruta"], Decimal("574.00"))

        bg = posting.balance_general(2026, 7)
        self.assertTrue(bg["cuadra"])
        inv = [a for a in bg["activos"] if a["nombre"] == "Inventario"]
        self.assertEqual(inv[0]["monto"], Decimal("24.00"))           # 50 comprado - 26 vendido
        self.assertTrue(posting.balanza_comprobacion(2026, 7)["cuadra"])

    def test_la_forma_del_asiento_de_venta(self):
        """El ingreso entra completo y en una sola línea: el IVA no se contabiliza.

        Fija la forma entera del asiento, no solo los totales, para que se vea
        de un vistazo qué cuentas toca una venta.
        """
        self._compra(1, "20")
        vA = self._venta(5, 4)   # 4 × $100, consume 800ml @ 0.02 = 16.00
        asiento = self.Movimiento.objects.get(venta=vA).asiento_reconocimiento
        cero = Decimal("0")
        self.assertEqual(
            [(m.cuenta.codigo, m.debe, m.haber)
             for m in asiento.movimientos.order_by("cuenta__codigo")],
            [("115", cero, Decimal("16.00")),      # sale del inventario
             ("202", Decimal("400.00"), cero),     # se salda el puente
             ("401", cero, Decimal("400.00")),     # ingreso completo
             ("501", Decimal("16.00"), cero)])     # costo de ventas


class VentaSinComprasTests(TestCase):
    """Vender un insumo que nunca se compró NO puede inventar costo.

    Antes el FIFO completaba el faltante con el precio del catálogo y abonaba
    Inventario por unidades que nunca entraron: el asiento cuadraba, pero el
    balance publicaba un ACTIVO NEGATIVO y se declaraba correcto igual, porque
    solo verifica que las sumas coincidan y no que los signos tengan sentido.

    No era teórico: al 12 de agosto de 2026, diez de los ingredientes usados no
    tenían ni una compra capturada y el respaldo inventaba el 72% del costo.
    """

    def setUp(self):
        from inventario.models import Ingrediente, Receta, RecetaIngrediente
        from contabilidad.models import Movimiento
        posting.crear_catalogo()
        self.Movimiento = Movimiento
        self.ing = Ingrediente.objects.create(
            nombre="Cacao", categoria="polvo", unidad_compra="kg",
            cantidad_por_unidad=Decimal("1000"), unidad_receta="g",
            costo_unidad_compra=Decimal("400"))     # 0.40 por gramo
        self.receta = Receta.objects.create(
            nombre="Shake", perfil="x", precio_venta=Decimal("100"))
        RecetaIngrediente.objects.create(
            receta=self.receta, ingrediente=self.ing, cantidad=Decimal("50"))

    def test_no_se_inventa_costo_ni_se_hunde_el_inventario(self):
        from inventario.models import Venta
        from inventario import costeo
        v = Venta.objects.create(fecha=date(2026, 8, 5), receta=self.receta,
                                 cantidad=2)        # querría 100 g, no hay ni uno
        costeo.costear_venta(v)

        v.refresh_from_db()
        self.assertEqual(v.costo_fifo, Decimal("0.00"))
        self.assertTrue(v.costo_incompleto)

        bg = posting.balance_general(2026, 8)
        # Sin costo no hay movimiento de inventario: la cuenta ni aparece, en
        # vez de aparecer en negativo como antes.
        self.assertEqual(
            [a for a in bg["activos"] if a["nombre"] == "Inventario"], [])
        self.assertTrue(bg["cuadra"])
        self.assertTrue(posting.balanza_comprobacion(2026, 8)["cuadra"])

    def test_al_capturar_la_compra_tardia_la_venta_se_cuesta_sola(self):
        # El caso real del mostrador: se vende en la mañana y la factura del
        # proveedor se captura en la tarde.
        from inventario.models import Venta, Compra
        from inventario import costeo
        v = Venta.objects.create(fecha=date(2026, 8, 5), receta=self.receta,
                                 cantidad=2)
        costeo.costear_venta(v)
        self.assertTrue(Venta.objects.get(pk=v.pk).costo_incompleto)

        Compra.objects.create(fecha=date(2026, 8, 5), ingrediente=self.ing,
                              cantidad=Decimal("1"), monto_total=Decimal("400"))
        costeo.recostear_desde(self.ing.pk, date(2026, 8, 5))

        v.refresh_from_db()
        self.assertEqual(v.costo_fifo, Decimal("40.00"))   # 100 g × 0.40
        self.assertFalse(v.costo_incompleto)


class CortesiaTests(TestCase):
    """Una cortesía es gratis pero consume inventario; su costo va a Cortesías."""

    def test_cortesia_gratis_consume_inventario_y_gasto_promocion(self):
        from inventario.models import (
            Ingrediente, Receta, RecetaIngrediente, Venta, Compra)
        from contabilidad.models import Movimiento
        posting.crear_catalogo()
        i = Ingrediente.objects.create(
            nombre="Leche", categoria="liquido", unidad_compra="litro",
            cantidad_por_unidad=Decimal("1000"), unidad_receta="ml",
            costo_unidad_compra=Decimal("20"))
        r = Receta.objects.create(nombre="Shake", precio_venta=Decimal("100"))
        RecetaIngrediente.objects.create(receta=r, ingrediente=i, cantidad=Decimal("200"))
        c = Compra.objects.create(fecha=date(2026, 8, 1), ingrediente=i,
                                  cantidad=Decimal("1"), monto_total=Decimal("20.00"))

        v = Venta.objects.create(fecha=date(2026, 8, 5), receta=r,
                                 cantidad=2, es_cortesia=True)
        from inventario import costeo
        costeo.costear_venta(v)
        self.assertEqual(v.ingreso, Decimal("0"))            # gratis
        i.refresh_from_db()
        self.assertEqual(i.stock_disponible, Decimal("600"))  # 1000 - 400ml consumidos

        er = posting.estado_resultados(2026, 8)
        self.assertEqual(er["total_ingresos"], Decimal("0"))
        self.assertEqual(er["total_costo_ventas"], Decimal("0"))
        # Desde P6 la cortesía se lee DENTRO de Mercadotecnia, sin dejar de ser
        # su propia cuenta: el asiento sigue yendo a la 506.
        grupos = {g["codigo"]: g for g in er["gastos"]}
        mercadotecnia = grupos["504"]
        self.assertEqual(mercadotecnia["propio"], Decimal("0"))   # sin gasto directo
        self.assertEqual(mercadotecnia["total"], Decimal("8"))    # 400ml * 0.02
        sub = {s["codigo"]: s for s in mercadotecnia["subcuentas"]}
        self.assertEqual(sub["506"]["nombre"], "Cortesías y promociones")
        self.assertEqual(sub["506"]["monto"], Decimal("8"))
        self.assertEqual(er["total_gastos"], Decimal("8"))        # no cambió
        self.assertTrue(posting.balance_general(2026, 8)["cuadra"])


class CatalogoTests(TestCase):
    def test_crear_catalogo_idempotente(self):
        posting.crear_catalogo()
        n1 = Cuenta.objects.count()
        posting.crear_catalogo()  # otra vez
        n2 = Cuenta.objects.count()
        self.assertEqual(n1, n2)
        self.assertEqual(n1, len(posting.CATALOGO))

    def test_naturaleza_cuentas(self):
        posting.crear_catalogo()
        self.assertTrue(Cuenta.objects.get(codigo="101").es_deudora)  # Activo
        self.assertTrue(Cuenta.objects.get(codigo="501").es_deudora)  # Gasto
        self.assertFalse(Cuenta.objects.get(codigo="401").es_deudora) # Ingreso
        self.assertFalse(Cuenta.objects.get(codigo="202").es_deudora) # Pasivo

    def test_el_catalogo_no_tiene_cuentas_de_iva(self):
        # El IVA no se lleva en la contabilidad: los importes van completos y
        # el desglose vive solo en la nota al cliente.
        posting.crear_catalogo()
        self.assertFalse(Cuenta.objects.filter(codigo__in=["105", "201"]).exists())


class MigracionQuitarIvaTests(TestCase):
    """Los dos seguros de la migración que borra las cuentas de IVA.

    Se le pasa el registro de modelos vivo en vez del histórico que Django usa
    en una migración real; vale mientras esos modelos no cambien de forma.
    """

    def setUp(self):
        import importlib
        from django.apps import apps
        self.migracion = importlib.import_module(
            "contabilidad.migrations.0007_quitar_cuentas_de_iva")
        self.apps = apps
        self.por_pagar = Cuenta.objects.create(
            codigo="201", nombre="IVA por pagar", tipo=Cuenta.Tipo.PASIVO)

    def test_se_detiene_si_alguna_tiene_movimientos(self):
        # PROTECT ya impide el borrado; lo que se prueba es que el error se
        # traduzca a algo que diga qué hacer.
        asiento = Asiento.objects.create(fecha=date(2026, 8, 1), concepto="x")
        MovimientoContable.objects.create(
            asiento=asiento, cuenta=self.por_pagar,
            debe=Decimal("0"), haber=Decimal("10"))

        with self.assertRaises(RuntimeError) as e:
            self.migracion.borrar_cuentas_de_iva(self.apps, None)
        self.assertIn("reclasifica", str(e.exception).lower())
        self.assertTrue(Cuenta.objects.filter(codigo="201").exists())

    def test_se_detiene_si_hay_pronosticos_de_flujo(self):
        # Éste es el peligroso: PronosticoFlujoCuenta es CASCADE, así que sin
        # el seguro se borraría en silencio junto con la cuenta.
        from finanzas.models import PronosticoFlujoCuenta
        PronosticoFlujoCuenta.objects.create(
            cuenta=self.por_pagar, anio=2026, mes=8, monto=Decimal("100"))

        with self.assertRaises(RuntimeError) as e:
            self.migracion.borrar_cuentas_de_iva(self.apps, None)
        self.assertIn("cascada", str(e.exception).lower())
        self.assertTrue(Cuenta.objects.filter(codigo="201").exists())
        self.assertEqual(PronosticoFlujoCuenta.objects.count(), 1)


class GastoOperativoTests(TestCase):
    """Gastos operativos (sueldos, renta…) por el libro de movimientos."""

    def setUp(self):
        posting.crear_catalogo()

    def test_el_gasto_entra_a_resultados_en_cuanto_se_registra(self):
        """P5: ya no hay botón que apretar. Un gasto pagado es gasto del mes."""
        posting.registrar_gasto(date(2025, 5, 1), "renta", Decimal("4500"))
        r = posting.estado_resultados(2025, 5)
        self.assertEqual(r["total_gastos"], Decimal("4500"))
        self.assertTrue(posting.balanza_comprobacion(2025, 5)["cuadra"])

    def test_el_gasto_cae_en_su_propia_cuenta(self):
        mov = posting.registrar_gasto(date(2025, 5, 1), "renta", Decimal("4500"), "Local")
        mov.refresh_from_db()
        r = posting.estado_resultados(2025, 5)
        self.assertEqual(r["total_gastos"], Decimal("4500"))  # gasto operativo
        self.assertEqual(r["total_costo_ventas"], Decimal("0"))
        cuentas = [m.cuenta.codigo for m in mov.asiento_reconocimiento.movimientos.all()]
        self.assertIn("502", cuentas)   # Renta
        self.assertTrue(posting.balance_general(2025, 5)["cuadra"])


class ReportesTests(TestCase):
    def setUp(self):
        posting.crear_catalogo()
        posting.registrar_gasto(date(2025, 5, 1), "sueldos", Decimal("400.00"))

    def test_balanza_cuadra(self):
        b = posting.balanza_comprobacion()
        self.assertTrue(b["cuadra"])
        self.assertEqual(b["total_debe"], b["total_haber"])

    def test_estado_resultados(self):
        r = posting.estado_resultados()
        self.assertEqual(r["total_gastos"], Decimal("400.00"))   # gasto operativo
        self.assertEqual(r["utilidad"], Decimal("-400.00"))

    def test_balance_cuadra(self):
        bg = posting.balance_general()
        self.assertTrue(bg["cuadra"])
        self.assertEqual(bg["total_activo"], bg["total_pasivo_capital"])


class ValidacionAsientoTests(TestCase):
    """Verifica la detección de asientos descuadrados e inválidos."""

    def setUp(self):
        posting.crear_catalogo()
        self.caja = Cuenta.objects.get(codigo="101")

    def test_movimiento_debe_y_haber_se_rechaza(self):
        from django.core.exceptions import ValidationError
        a = Asiento.objects.create(fecha=date(2025, 5, 1), concepto="X")
        m = MovimientoContable(
            asiento=a, cuenta=self.caja,
            debe=Decimal("100"), haber=Decimal("50"),
        )
        with self.assertRaises(ValidationError):
            m.full_clean()

    def test_movimiento_sin_monto_se_rechaza(self):
        from django.core.exceptions import ValidationError
        a = Asiento.objects.create(fecha=date(2025, 5, 1), concepto="X")
        m = MovimientoContable(asiento=a, cuenta=self.caja)
        with self.assertRaises(ValidationError):
            m.full_clean()

    def test_asiento_descuadrado_se_detecta(self):
        a = Asiento.objects.create(fecha=date(2025, 5, 1), concepto="Malo")
        MovimientoContable.objects.create(
            asiento=a, cuenta=self.caja, debe=Decimal("100")
        )
        self.assertFalse(a.cuadrado)

    def test_asiento_cuadrado_se_detecta(self):
        ventas = Cuenta.objects.get(codigo="401")
        a = Asiento.objects.create(fecha=date(2025, 5, 1), concepto="Bueno")
        MovimientoContable.objects.create(
            asiento=a, cuenta=self.caja, debe=Decimal("100")
        )
        MovimientoContable.objects.create(
            asiento=a, cuenta=ventas, haber=Decimal("100")
        )
        self.assertTrue(a.cuadrado)


class ReportesViewTests(TestCase):
    def setUp(self):
        from django.contrib.auth.models import User
        posting.crear_catalogo()
        self.user = User.objects.create_superuser("admin", "a@a.com", "pass")
        self.client.force_login(self.user)
        posting.registrar_gasto(date(2025, 5, 1), "renta", Decimal("4500"))

    def test_reportes_carga(self):
        resp = self.client.get("/contabilidad/")
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Balanza de comprobación")
        self.assertContains(resp, "Estado de resultados")
        self.assertContains(resp, "Balance general")

    def test_reportes_requiere_login(self):
        self.client.logout()
        resp = self.client.get("/contabilidad/")
        self.assertEqual(resp.status_code, 302)

    def test_admin_movimiento_carga(self):
        resp = self.client.get("/admin/contabilidad/movimiento/")
        self.assertEqual(resp.status_code, 200)

    def test_admin_asiento_carga(self):
        resp = self.client.get("/admin/contabilidad/asiento/")
        self.assertEqual(resp.status_code, 200)


class _PanelBase(TestCase):
    """Un shake de 200 ml a $100, con Andy dentro. Lo comparten las pruebas
    que miran la pantalla de contabilidad."""

    def setUp(self):
        from django.contrib.auth.models import User
        from inventario.models import Ingrediente, Receta, RecetaIngrediente
        posting.crear_catalogo()
        User.objects.create_superuser("andy", "a@a.com", "pass")
        self.client.login(username="andy", password="pass")
        self.ing = Ingrediente.objects.create(
            nombre="Leche", unidad_compra="litro", cantidad_por_unidad=1000,
            unidad_receta="ml", costo_unidad_compra=Decimal("30.00"))
        self.rec = Receta.objects.create(
            nombre="Shake", precio_venta=Decimal("100.00"))
        RecetaIngrediente.objects.create(
            receta=self.rec, ingrediente=self.ing, cantidad=200)


class SaludDelCosteoTests(_PanelBase):
    """Tres cifras que valen cero cuando el costeo está sano.

    Ninguna se nota mirando los reportes: la balanza cuadra igual con ventas
    sin costear, y el balance se declara correcto con el inventario en
    negativo, porque solo verifica que las sumas coincidan.
    """

    def test_un_ciclo_normal_sale_en_cero(self):
        from inventario.models import Compra, Venta
        Compra.objects.create(fecha=date(2026, 8, 1), ingrediente=self.ing,
                              cantidad=Decimal("1"), monto_total=Decimal("30"))
        Venta.objects.create(fecha=date(2026, 8, 5), receta=self.rec, cantidad=2)

        salud = posting.salud_del_costeo(2026, 8)

        self.assertEqual(salud["sin_costear"], 0)
        self.assertEqual(salud["incompletas"], 0)
        self.assertEqual(salud["saldo_acreedor"], Decimal("0"))
        self.assertGreater(salud["saldo_inventario"], 0)

    def test_una_venta_sin_su_compra_sale_como_incompleta(self):
        from inventario.models import Venta
        Venta.objects.create(fecha=date(2026, 8, 5), receta=self.rec, cantidad=2)

        salud = posting.salud_del_costeo(2026, 8)

        self.assertEqual(salud["incompletas"], 1)
        # Y sin embargo el balance se declara correcto: por eso hace falta
        # este panel y no basta con mirar los reportes.
        self.assertTrue(posting.balance_general(2026, 8)["cuadra"])

    def test_el_panel_lo_publica(self):
        from inventario.models import Venta
        Venta.objects.create(fecha=date(2026, 8, 5), receta=self.rec, cantidad=2)

        resp = self.client.get("/contabilidad/?anio=2026&mes=8")

        self.assertContains(resp, "Salud del costeo")
        self.assertContains(resp, "Revisar")

    def test_sin_movimientos_no_truena_ni_inventa_problemas(self):
        salud = posting.salud_del_costeo(2026, 8)

        self.assertEqual(salud["sin_costear"], 0)
        self.assertEqual(salud["incompletas"], 0)
        self.assertEqual(salud["saldo_acreedor"], Decimal("0"))
        self.assertContains(
            self.client.get("/contabilidad/?anio=2026&mes=8"), "Sano")


class LibroEnlazaALaNotaTests(_PanelBase):
    """El libro dice cuánto entró; la nota dice qué se llevó el cliente."""

    def test_la_venta_con_nota_enlaza_a_su_nota(self):
        from inventario.models import Nota, Venta
        nota = Nota.objects.create(fecha=date(2026, 8, 5), total=Decimal("100"))
        Venta.objects.create(fecha=date(2026, 8, 5), receta=self.rec,
                             cantidad=1, nota=nota)

        resp = self.client.get("/contabilidad/?anio=2026&mes=8")

        self.assertContains(resp, nota.get_absolute_url())

    def test_un_gasto_no_inventa_un_enlace(self):
        posting.registrar_gasto(date(2026, 8, 3), "renta", Decimal("4500"))

        resp = self.client.get("/contabilidad/?anio=2026&mes=8")

        self.assertNotContains(resp, 'class="ver-nota"')

    def test_una_venta_sin_nota_no_rompe_la_fila(self):
        from inventario.models import Venta
        Venta.objects.create(fecha=date(2026, 8, 5), receta=self.rec,
                             cantidad=1)

        resp = self.client.get("/contabilidad/?anio=2026&mes=8")

        self.assertEqual(resp.status_code, 200)
        self.assertNotContains(resp, 'class="ver-nota"')


class _ReconocimientoBase(TestCase):
    """Fixture compartido: un shake de 200 ml de leche, a $100.

    El costo de catálogo se pone absurdo (99/litro) a propósito: si alguna vez
    se colara a la contabilidad, los números no se parecerían en nada.
    """

    def setUp(self):
        from inventario.models import Ingrediente, Receta, RecetaIngrediente
        from contabilidad.models import Movimiento
        posting.crear_catalogo()
        self.Movimiento = Movimiento
        self.ing = Ingrediente.objects.create(
            nombre="Leche", unidad_compra="litro",
            cantidad_por_unidad=Decimal("1000"), unidad_receta="ml",
            costo_unidad_compra=Decimal("99"))
        self.receta = Receta.objects.create(
            nombre="Shake", precio_venta=Decimal("100"))
        RecetaIngrediente.objects.create(
            receta=self.receta, ingrediente=self.ing, cantidad=Decimal("200"))

    def _comprar(self, dia, litros="1", monto="20"):
        from inventario.models import Compra
        return Compra.objects.create(
            fecha=date(2026, 8, dia), ingrediente=self.ing,
            cantidad=Decimal(litros), monto_total=Decimal(monto))

    def _vender(self, dia=5, cantidad=5):
        from inventario.models import Venta
        return Venta.objects.create(
            fecha=date(2026, 8, dia), receta=self.receta, cantidad=cantidad)


class ReconocimientoDiferidoTests(_ReconocimientoBase):
    """Una venta sin costo completo no entra al Estado de Resultados.

    Reconocer el ingreso con un costo parcial infla la utilidad bruta sin que
    nada se queje: es peor que no reportar la venta todavía. Se difieren los
    dos lados hasta que se capture la compra que falta.
    """


    def test_sin_compras_no_se_reconoce_el_ingreso(self):
        v = self._vender()
        er = posting.estado_resultados(2026, 8)
        self.assertEqual(er["total_ingresos"], Decimal("0"))
        self.assertEqual(er["total_costo_ventas"], Decimal("0"))
        self.assertIsNone(
            self.Movimiento.objects.get(venta=v).asiento_reconocimiento)

    def test_con_costo_parcial_tampoco(self):
        from inventario.models import Compra
        Compra.objects.create(fecha=date(2026, 8, 1), ingrediente=self.ing,
                              cantidad=Decimal("0.1"), monto_total=Decimal("2"))
        v = self._vender()          # pide 1000 ml, solo hay 100
        v.refresh_from_db()
        self.assertTrue(v.costo_incompleto)
        self.assertEqual(posting.estado_resultados(2026, 8)["total_ingresos"],
                         Decimal("0"))

    def test_al_completar_la_compra_la_venta_entra_al_reporte(self):
        from inventario.models import Compra
        v = self._vender()
        self.assertEqual(posting.estado_resultados(2026, 8)["total_ingresos"],
                         Decimal("0"))

        Compra.objects.create(fecha=date(2026, 8, 1), ingrediente=self.ing,
                              cantidad=Decimal("1"), monto_total=Decimal("20"))

        v.refresh_from_db()
        self.assertFalse(v.costo_incompleto)
        self.assertEqual(v.costo_fifo, Decimal("20.00"))
        er = posting.estado_resultados(2026, 8)
        self.assertEqual(er["total_ingresos"], Decimal("500"))
        self.assertEqual(er["total_costo_ventas"], Decimal("20.00"))


class ReconocimientoAutomaticoTests(_ReconocimientoBase):
    """P5: sin botón «Facturado». Lo capturado se reconoce solo.

    El caso contrario —lo que NO se reconoce— vive en
    `ReconocimientoDiferidoTests`, que comparte este mismo fixture.
    """

    def test_la_compra_entra_a_inventario_sin_que_nadie_la_facture(self):
        c = self._comprar(1)
        self.assertIsNotNone(
            self.Movimiento.objects.get(compra=c).asiento_reconocimiento)
        bg = posting.balance_general(2026, 8)
        activos = {a["nombre"]: a["monto"] for a in bg["activos"]}
        self.assertEqual(activos["Inventario"], Decimal("20"))

    def test_la_venta_costeada_llega_al_estado_de_resultados_sola(self):
        self._comprar(1)                       # 1000 ml a 0.02
        v = self._vender(2, cantidad=2)        # 400 ml → 8.00

        er = posting.estado_resultados(2026, 8)
        self.assertEqual(er["total_ingresos"], Decimal("200"))
        self.assertEqual(er["total_costo_ventas"], Decimal("8"))
        self.assertEqual(er["utilidad_bruta"], Decimal("192"))
        self.assertIsNotNone(
            self.Movimiento.objects.get(venta=v).asiento_reconocimiento)
        self.assertTrue(posting.balance_general(2026, 8)["cuadra"])
        self.assertTrue(posting.balanza_comprobacion(2026, 8)["cuadra"])

    def test_la_compra_del_dia_surte_a_la_venta_del_mismo_dia(self):
        self._comprar(3)
        v = self._vender(3, cantidad=2)
        v.refresh_from_db()
        self.assertFalse(v.costo_incompleto)
        self.assertEqual(v.costo_fifo, Decimal("8.00"))

    def test_el_reconocimiento_usa_la_fecha_del_movimiento(self):
        self._comprar(1)
        self._vender(2, cantidad=2)
        # Nada cae en julio: ya no hay fecha de factura que lo mueva de mes.
        self.assertEqual(
            posting.estado_resultados(2026, 7)["total_ingresos"], Decimal("0"))
        self.assertEqual(
            posting.estado_resultados(2026, 8)["total_ingresos"], Decimal("200"))

    def test_la_venta_sin_costo_igual_cobra_en_caja(self):
        """El flujo no depende del costeo: el dinero sí entró."""
        v = self._vender(2)                    # sin compras que la surtan
        self.assertIsNotNone(self.Movimiento.objects.get(venta=v).asiento_flujo)
        self.assertTrue(posting.balanza_comprobacion(2026, 8)["cuadra"])


class ReconocimientoSeRetiraTests(_ReconocimientoBase):
    """La dirección peligrosa: lo reconocido tiene que poder DES-reconocerse.

    Si se borra la compra que respaldaba una venta, su costo deja de estar
    completo. Si el asiento se quedara, el Estado de Resultados mostraría el
    ingreso sin su costo —justo lo que el invariante I2 prohíbe— y encima con
    un abono a Inventario que ya no respalda nada.
    """

    def test_borrar_la_compra_retira_el_asiento_de_la_venta(self):
        compra = self._comprar(1)
        v = self._vender(2, cantidad=2)
        mov = self.Movimiento.objects.get(venta=v)
        self.assertIsNotNone(mov.asiento_reconocimiento)
        self.assertEqual(
            posting.estado_resultados(2026, 8)["total_ingresos"], Decimal("200"))

        compra.delete()

        mov.refresh_from_db()
        self.assertIsNone(mov.asiento_reconocimiento)
        er = posting.estado_resultados(2026, 8)
        self.assertEqual(er["total_ingresos"], Decimal("0"))
        self.assertEqual(er["total_costo_ventas"], Decimal("0"))
        self.assertTrue(posting.balanza_comprobacion(2026, 8)["cuadra"])

    def test_el_predicado_y_su_queryset_dicen_lo_mismo(self):
        """`costo_esta_completo` y `sin_costo_completo()` son complementos.

        Son la misma regla escrita dos veces, una en Python y otra en el ORM.
        Si se separan, el panel y el libro discrepan sobre la misma venta sin
        que nada se queje.
        """
        from inventario.models import Venta
        self._comprar(1, litros="0.1", monto="2")   # alcanza para media venta
        completa = self._vender(2, cantidad=1)      # 200 ml, hay 100 → parcial
        self._comprar(1)                            # ahora sí alcanza
        self._vender(3, cantidad=50)                # 10 000 ml: no alcanza

        incompletas = set(Venta.objects.sin_costo_completo()
                          .values_list("id", flat=True))
        por_propiedad = {v.id for v in Venta.objects.all()
                         if not v.costo_esta_completo}
        self.assertEqual(incompletas, por_propiedad)
        self.assertIn(completa.id, {v.id for v in Venta.objects.all()
                                    if v.costo_esta_completo})


class JerarquiaDeCuentasTests(TestCase):
    """P6: la 506 se lee dentro de la 504 sin que el posteo cambie."""

    def setUp(self):
        posting.crear_catalogo()

    def test_la_506_cuelga_de_la_504(self):
        c506 = Cuenta.objects.get(codigo="506")
        self.assertEqual(c506.padre.codigo, "504")

    def test_crear_catalogo_repone_la_jerarquia_si_alguien_la_rompe(self):
        """`_cuenta_segura()` puede recrear una cuenta borrada.

        Si renaciera suelta, las cortesías desaparecerían del grupo de
        mercadotecnia en el reporte sin que nadie se entere.
        """
        Cuenta.objects.filter(codigo="506").update(padre=None)
        posting.crear_catalogo()
        self.assertEqual(Cuenta.objects.get(codigo="506").padre.codigo, "504")

    def test_el_grupo_suma_el_gasto_propio_del_padre_y_el_de_la_hija(self):
        """La 504 es cuenta de movimiento Y padre a la vez."""
        posting.registrar_gasto(date(2026, 8, 1), "mercadotecnia",
                                Decimal("300"), "Volantes")
        # Un gasto directo a cortesías, como el que hará el canje en P9.
        cortesias = Cuenta.objects.get(codigo="506")
        posting._reemplaza_asiento(
            "prueba cortesía", date(2026, 8, 2), "Regalo",
            [(cortesias.codigo, Decimal("50"), Decimal("0")),
             ("115", Decimal("0"), Decimal("50"))])

        er = posting.estado_resultados(2026, 8)
        grupo = {g["codigo"]: g for g in er["gastos"]}["504"]
        self.assertEqual(grupo["propio"], Decimal("300"))
        self.assertEqual(grupo["subcuentas"][0]["codigo"], "506")
        self.assertEqual(grupo["subcuentas"][0]["monto"], Decimal("50"))
        self.assertEqual(grupo["total"], Decimal("350"))
        self.assertEqual(er["total_gastos"], Decimal("350"))

    def test_los_gastos_salen_ordenados_por_codigo(self):
        """Antes se iteraba el agregado tal cual: el orden dependía del motor."""
        for categoria in ("sueldos", "renta", "mercadotecnia", "servicios"):
            posting.registrar_gasto(date(2026, 8, 1), categoria, Decimal("100"))
        codigos = [g["codigo"] for g in
                   posting.estado_resultados(2026, 8)["gastos"]]
        self.assertEqual(codigos, sorted(codigos))

    def test_el_xlsx_dice_lo_mismo_que_la_pantalla(self):
        from contabilidad import export
        from openpyxl import Workbook
        posting.registrar_gasto(date(2026, 8, 1), "mercadotecnia",
                                Decimal("300"), "Volantes")
        cortesias = Cuenta.objects.get(codigo="506")
        posting._reemplaza_asiento(
            "prueba cortesía", date(2026, 8, 2), "Regalo",
            [(cortesias.codigo, Decimal("50"), Decimal("0")),
             ("115", Decimal("0"), Decimal("50"))])

        ws = Workbook().active
        export.resultados(ws, 2026, 8, "Agosto 2026")
        filas = {str(f[0].value).strip(): f[1].value
                 for f in ws.iter_rows(min_col=1, max_col=2) if f[0].value}
        self.assertEqual(filas["Mercadotecnia (directo)"], 300)
        self.assertEqual(filas["↳ Cortesías y promociones"], 50)
        self.assertEqual(filas["Total mercadotecnia"], 350)
        self.assertEqual(filas["Total gastos operativos"], 350)


class PeriodoFueraDeRangoTests(TestCase):
    """Un periodo imposible en la URL no puede tumbar la pantalla.

    El mes viajaba entero hasta el diccionario que le pone nombre (KeyError) y
    el año hasta la fecha con que se consulta el periodo (ValueError). Un mes
    de diez cifras ni siquiera llegaba tan lejos: `_shift` normaliza de doce en
    doce, así que quemaba la petición contando antes de reventar.
    """

    MALOS = [
        ("mes cero", 2026, 0),
        ("mes trece", 2026, 13),
        ("mes negativo", 2026, -1),
        ("anio cero", 0, 6),
        ("anio de cinco cifras", 99999, 6),
        ("anio de veinte cifras", 10 ** 20, 6),
        ("mes de diez cifras", 2026, 10 ** 9),
    ]

    def setUp(self):
        from django.contrib.auth.models import User
        posting.crear_catalogo()
        User.objects.create_superuser("andy", "a@a.com", "pass")
        self.client.login(username="andy", password="pass")

    def test_la_pantalla_aguanta(self):
        for caso, anio, mes in self.MALOS:
            with self.subTest(caso=caso):
                resp = self.client.get(f"/contabilidad/?anio={anio}&mes={mes}")
                self.assertEqual(resp.status_code, 200)

    def test_las_cuatro_descargas_aguantan(self):
        from django.urls import reverse
        for reporte in ("resultados", "balance", "flujo", "balanza"):
            for caso, anio, mes in self.MALOS:
                with self.subTest(reporte=reporte, caso=caso):
                    url = reverse("exportar_estado", args=[reporte])
                    resp = self.client.get(f"{url}?anio={anio}&mes={mes}")
                    self.assertEqual(resp.status_code, 200)

    def test_un_periodo_bueno_si_se_respeta(self):
        resp = self.client.get("/contabilidad/?anio=2026&mes=8")
        self.assertContains(resp, "Agosto 2026")

    def test_un_periodo_viejo_del_desplegable_si_se_honra(self):
        """El desplegable no puede ofrecer un mes que la pantalla luego cambie.

        La lista de periodos sale de las fechas de los asientos, y `gasto_registrar`
        acepta cualquier fecha. Si la lectura se acotara al rango del negocio,
        elegir «Mayo 2019» devolvería agosto sin decir por qué.
        """
        for fecha in ("2019-05-10", "2026-08-10"):
            self.client.post("/contabilidad/gasto/registrar/", {
                "fecha": fecha, "categoria": "renta",
                "descripcion": "Renta", "monto": "100"})

        resp = self.client.get("/contabilidad/?anio=2019&mes=5")
        self.assertIn((2019, 5),
                      [(p["anio"], p["mes"]) for p in resp.context["periodos"]])
        self.assertEqual((resp.context["anio"], resp.context["mes"]), (2019, 5))


class MontosImposiblesTests(TestCase):
    """Un monto que no es un número no puede tumbar la pantalla.

    `Decimal` acepta «NaN» e «Infinity» sin quejarse y revienta al COMPARARLOS
    o al escribirlos. Es la trampa que ya documentaba el proyecto, y este
    lector no había copiado la guarda.
    """

    def setUp(self):
        from django.contrib.auth.models import User
        posting.crear_catalogo()
        User.objects.create_superuser("andy", "a@a.com", "pass")
        self.client.login(username="andy", password="pass")

    def test_ningun_monto_imposible_registra_ni_truena(self):
        from django.urls import reverse
        from contabilidad.models import Movimiento
        for malo in ("NaN", "Infinity", "-Infinity", "nan", "inf", "abc", ""):
            with self.subTest(monto=malo):
                antes = Movimiento.objects.count()
                resp = self.client.post(reverse("gasto_registrar"), {
                    "categoria": "renta", "descripcion": "Renta", "monto": malo})
                self.assertEqual(resp.status_code, 302)
                self.assertEqual(Movimiento.objects.count(), antes)

    def test_un_monto_bueno_si_entra(self):
        from django.urls import reverse
        from contabilidad.models import Movimiento
        self.client.post(reverse("gasto_registrar"), {
            "categoria": "renta", "descripcion": "Renta", "monto": "4500"})
        self.assertEqual(Movimiento.objects.count(), 1)
