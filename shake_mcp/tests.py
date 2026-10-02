"""El servidor MCP: periodos, solo lectura, cada consulta y el protocolo."""
import asyncio
import json
import unittest
from contextlib import ExitStack
from datetime import date
from decimal import Decimal
from unittest import mock

from django.db import OperationalError
from django.test import TestCase, TransactionTestCase

from contabilidad import posting
from inventario.models import (
    Compra, Ingrediente, Nota, Receta, RecetaIngrediente, Venta)
from lealtad.models import Cliente, ConfiguracionPrograma, Nivel
from presupuesto.models import PresupuestoVenta
from shake_mcp import consultas, periodos
from shake_mcp.lectura import solo_lectura, verificar_rol
from shake_mcp.periodos import ErrorDeConsulta

try:
    import mcp  # noqa: F401
    HAY_MCP = True
except ImportError:
    HAY_MCP = False

HOY = date(2026, 8, 20)      # jueves
TELEFONO = "5512345678"


def con_hoy(hoy=HOY):
    """Fija «hoy» en todos los módulos que lo preguntan."""
    pila = ExitStack()
    for modulo in ("shake_mcp.periodos", "shake_mcp.consultas",
                   "inventario.alarmas"):
        pila.enter_context(mock.patch(f"{modulo}.localdate", return_value=hoy))
    return pila


class PeriodosTests(TestCase):
    def test_periodos_relativos(self):
        casos = {
            "hoy": (date(2026, 8, 20), date(2026, 8, 20)),
            "ayer": (date(2026, 8, 19), date(2026, 8, 19)),
            "esta_semana": (date(2026, 8, 17), date(2026, 8, 20)),
            "semana_pasada": (date(2026, 8, 10), date(2026, 8, 16)),
            "este_mes": (date(2026, 8, 1), date(2026, 8, 20)),
            "mes_pasado": (date(2026, 7, 1), date(2026, 7, 31)),
            "ultimos_7_dias": (date(2026, 8, 14), date(2026, 8, 20)),
            "ultimos_30_dias": (date(2026, 7, 22), date(2026, 8, 20)),
        }
        for periodo, esperado in casos.items():
            with self.subTest(periodo):
                d, h, _ = periodos.resolver(periodo, hoy=HOY)
                self.assertEqual((d, h), esperado)

    def test_mes_pasado_en_enero_cruza_el_anio(self):
        d, h, _ = periodos.resolver("mes_pasado", hoy=date(2027, 1, 10))
        self.assertEqual((d, h), (date(2026, 12, 1), date(2026, 12, 31)))

    def test_fechas_explicitas_ganan_al_periodo(self):
        d, h, _ = periodos.resolver("hoy", "2026-08-01", "2026-08-05", hoy=HOY)
        self.assertEqual((d, h), (date(2026, 8, 1), date(2026, 8, 5)))

    def test_solo_desde_llega_hasta_hoy(self):
        d, h, _ = periodos.resolver(desde="2026-08-15", hoy=HOY)
        self.assertEqual((d, h), (date(2026, 8, 15), HOY))

    def test_argumentos_imposibles_explican_el_error(self):
        malos = [
            {"desde": "2026-13-01"},
            {"desde": "ayer"},
            {"desde": "2026-08-10", "hasta": "2026-08-01"},
            {"desde": "2024-01-01", "hasta": "2026-01-01"},
            {"periodo": "trimestre"},
        ]
        for kwargs in malos:
            with self.subTest(kwargs), self.assertRaises(ErrorDeConsulta):
                periodos.resolver(hoy=HOY, **kwargs)

    def test_anterior_de_un_tramo_de_mes_es_el_mismo_tramo(self):
        self.assertEqual(
            periodos.anterior(date(2026, 10, 1), date(2026, 10, 2)),
            (date(2026, 9, 1), date(2026, 9, 2)))
        # Marzo completo contra febrero completo, sin pasarse al 3 de marzo.
        self.assertEqual(
            periodos.anterior(date(2026, 3, 1), date(2026, 3, 31)),
            (date(2026, 2, 1), date(2026, 2, 28)))

    def test_anterior_de_una_semana_es_la_semana_previa(self):
        self.assertEqual(
            periodos.anterior(date(2026, 8, 17), date(2026, 8, 20)),
            (date(2026, 8, 13), date(2026, 8, 16)))

    def test_mes_valida(self):
        self.assertEqual(periodos.mes(hoy=HOY), (2026, 8))
        for anio, mes in ((2026, 13), (2026, 0), (1999, 5), (2030, 1)):
            with self.subTest((anio, mes)), self.assertRaises(ErrorDeConsulta):
                periodos.mes(anio, mes, hoy=HOY)


class SoloLecturaTests(TestCase):
    def test_escribir_dentro_truena_y_no_deja_rastro(self):
        with self.assertRaises(OperationalError):
            with solo_lectura():
                Receta.objects.create(nombre="Intrusa", precio_venta=1)
        self.assertFalse(Receta.objects.filter(nombre="Intrusa").exists())

    def test_al_salir_se_vuelve_a_poder_escribir(self):
        with solo_lectura():
            Receta.objects.count()
        Receta.objects.create(nombre="Después", precio_venta=1)
        self.assertTrue(Receta.objects.filter(nombre="Después").exists())

    def test_verificar_rol_no_aplica_a_sqlite(self):
        verificar_rol()


def sembrar():
    """Un negocio chico con todos los casos que las consultas distinguen."""
    posting.crear_catalogo()
    leche = Ingrediente.objects.create(
        nombre="Leche", unidad_compra="litro", cantidad_por_unidad=1000,
        unidad_receta="ml", costo_unidad_compra=Decimal("20"))
    proteina = Ingrediente.objects.create(
        nombre="Proteína", categoria=Ingrediente.Categoria.PROTEINA,
        unidad_compra="bolsa", cantidad_por_unidad=1000, unidad_receta="g",
        costo_unidad_compra=Decimal("500"))
    vaso = Ingrediente.objects.create(
        nombre="Vaso", categoria=Ingrediente.Categoria.EMPAQUE,
        unidad_compra="paquete", cantidad_por_unidad=50, unidad_receta="pieza",
        costo_unidad_compra=Decimal("50"))
    fresa = Ingrediente.objects.create(          # nunca se compra
        nombre="Fresa", categoria=Ingrediente.Categoria.FRUTA,
        unidad_compra="kg", cantidad_por_unidad=1000, unidad_receta="g",
        costo_unidad_compra=Decimal("80"))

    chocolate = Receta.objects.create(nombre="Chocolate", precio_venta=90)
    de_fresa = Receta.objects.create(nombre="Fresa", precio_venta=100)
    vieja = Receta.objects.create(nombre="Vieja", precio_venta=70, activa=False)
    for receta, lineas in (
            (chocolate, ((leche, 250), (proteina, 30), (vaso, 1))),
            (de_fresa, ((leche, 200), (proteina, 30), (fresa, 50), (vaso, 1))),
            (vieja, ((leche, 100), (vaso, 1)))):
        for ingrediente, cantidad in lineas:
            RecetaIngrediente.objects.create(
                receta=receta, ingrediente=ingrediente, cantidad=cantidad)

    for dia in (date(2026, 7, 1), date(2026, 8, 1)):
        Compra.objects.create(fecha=dia, ingrediente=leche, cantidad=5,
                              monto_total=Decimal("100"), proveedor="Costco")
        Compra.objects.create(fecha=dia, ingrediente=proteina, cantidad=1,
                              monto_total=Decimal("450"))
        Compra.objects.create(fecha=dia, ingrediente=vaso, cantidad=1,
                              monto_total=Decimal("50"))

    def venta(dia, receta, cantidad=1, metodo="efectivo", nota=True, **extra):
        n = Nota.objects.create(fecha=dia, metodo_pago=metodo) if nota else None
        return Venta.objects.create(fecha=dia, receta=receta, cantidad=cantidad,
                                    metodo_pago=metodo, nota=n, **extra)

    venta(date(2026, 7, 15), chocolate)                          # mes anterior
    venta(date(2026, 8, 11), chocolate, nota=False)              # semana pasada
    venta(date(2026, 8, 18), chocolate, 2, descuento_pct=50)     # baja el margen
    venta(date(2026, 8, 19), de_fresa, metodo="tarjeta")         # costo incompleto
    venta(date(2026, 8, 19), chocolate, es_cortesia=True)

    posting.registrar_gasto(date(2026, 8, 5), "servicios", Decimal("200"), "Luz")
    PresupuestoVenta.objects.create(anio=2026, mes=8, monto=Decimal("1000"))

    Nivel.objects.create(nombre="Fan", puntos_requeridos=0)
    Nivel.objects.create(nombre="Pro", puntos_requeridos=100)
    Cliente.objects.create(
        telefono=TELEFONO, nombre="Ana López", visitas=3,
        gasto_historico=Decimal("300"), puntos_saldo=30, puntos_historicos=150,
        ultima_compra=date(2026, 8, 10))
    Cliente.objects.create(
        telefono="5587654321", nombre="Beto", visitas=9,
        gasto_historico=Decimal("900"), estado=Cliente.Estado.BAJA)


class ConsultasTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        sembrar()

    def consulta(self, fn, **kwargs):
        """Corre la consulta como la corre el servidor, y valida la forma."""
        with con_hoy(), solo_lectura():
            r = fn(**kwargs)
        texto = json.dumps(r, ensure_ascii=False)
        self.assertNotIn(TELEFONO, texto)
        self.assertIsInstance(r["avisos"], list)
        return r

    def test_ventas_de_la_semana(self):
        r = self.consulta(consultas.ventas_del_periodo, periodo="esta_semana")
        self.assertEqual(r["periodo"]["desde"], "2026-08-17")
        self.assertEqual(r["periodo"]["hasta"], "2026-08-20")
        # 2 chocolates al 50% (90) + una fresa (100); la cortesía no cobra.
        self.assertEqual(r["total_cobrado_mxn"], 190.0)
        self.assertEqual(r["tickets"], 2)
        self.assertEqual(r["ticket_promedio_mxn"], 95.0)
        self.assertEqual(r["unidades_cobradas"], 3)
        self.assertEqual(r["unidades_regaladas"], 1)
        self.assertEqual(r["descuentos_mxn"], 90.0)
        self.assertEqual({m["metodo"]: m["total_mxn"] for m in r["por_metodo_pago"]},
                         {"Tarjeta": 100.0, "Efectivo": 90.0})
        self.assertEqual(len(r["por_dia"]), 2)
        # La semana anterior equivalente (13 al 16) no tuvo ventas.
        self.assertEqual(r["periodo_anterior"]["desde"], "2026-08-13")
        self.assertIsNone(r["periodo_anterior"]["variacion_pct"])
        self.assertTrue(any("costo" in a for a in r["avisos"]))

    def test_ventas_contra_el_periodo_anterior(self):
        r = self.consulta(consultas.ventas_del_periodo,
                          desde="2026-08-11", hasta="2026-08-18")
        self.assertEqual(r["total_cobrado_mxn"], 180.0)
        self.assertEqual(r["tickets"], 2)          # una sin nota cuenta sola

    def test_periodo_sin_ventas_lo_dice(self):
        r = self.consulta(consultas.ventas_del_periodo, periodo="hoy")
        self.assertEqual(r["total_cobrado_mxn"], 0.0)
        self.assertIsNone(r["ticket_promedio_mxn"])
        self.assertIn("No hay ventas", r["avisos"][0])

    def test_productos_mas_vendidos(self):
        r = self.consulta(consultas.productos_mas_vendidos, periodo="este_mes")
        primero = r["productos"][0]
        self.assertEqual(primero["producto"], "Chocolate")
        self.assertEqual((primero["unidades"], primero["cobradas"],
                          primero["regaladas"]), (4, 3, 1))
        r = self.consulta(consultas.productos_mas_vendidos, periodo="este_mes",
                          ordenar_por="ingreso", limite=1)
        self.assertEqual(r["productos"][0]["producto"], "Chocolate")
        self.assertEqual(r["productos"][0]["ingreso_mxn"], 180.0)
        self.assertTrue(r["truncado"])
        self.assertEqual(r["total_filas"], 2)

    def test_criterio_desconocido_se_rechaza(self):
        with self.assertRaises(ErrorDeConsulta):
            consultas.productos_mas_vendidos(ordenar_por="color")

    def test_insumos_por_surtir(self):
        r = self.consulta(consultas.insumos_por_surtir)
        nombres = [i["insumo"] for i in r["insumos"]]
        self.assertIn("Fresa", nombres)
        fresa = next(i for i in r["insumos"] if i["insumo"] == "Fresa")
        self.assertTrue(fresa["stock_negativo"])
        self.assertIsNone(fresa["ultima_compra"])
        self.assertTrue(any("negativo" in a for a in r["avisos"]))
        self.assertTrue(all(i["faltante"] > 0 for i in r["insumos"]))

    def test_insumos_todos(self):
        r = self.consulta(consultas.insumos_por_surtir, solo_faltantes=False)
        # Las migraciones siembran los insumos de los add-ons.
        self.assertEqual(r["total_insumos"], Ingrediente.objects.count())
        self.assertEqual(r["total_filas"], r["total_insumos"])
        leche = next(i for i in r["insumos"] if i["insumo"] == "Leche")
        self.assertEqual(leche["ultima_compra"], "2026-08-01")

    def test_margen_por_receta(self):
        r = self.consulta(consultas.margen_por_receta)
        self.assertNotIn("Vieja", [x["producto"] for x in r["recetas"]])
        fresa = next(x for x in r["recetas"] if x["producto"] == "Fresa")
        self.assertIsNone(fresa["costo_ultima_compra_mxn"])
        self.assertTrue(any("última compra" in a for a in r["avisos"]))
        chocolate = next(x for x in r["recetas"] if x["producto"] == "Chocolate")
        # Catálogo: 250 ml × 0.02 + 30 g × 0.5 + 1 × 1 = 21; 69/90 = 76.7 %.
        self.assertEqual(chocolate["costo_catalogo_mxn"], 21.0)
        self.assertEqual(chocolate["margen_catalogo_pct"], 76.7)
        # Última compra: 250 × 0.02 + 30 × 0.45 + 1 × 1 = 19.50.
        self.assertEqual(chocolate["costo_ultima_compra_mxn"], 19.5)
        self.assertNotIn("vendido_en_periodo", chocolate)

    def test_margen_con_periodo_e_inactivas(self):
        r = self.consulta(consultas.margen_por_receta, periodo="este_mes",
                          incluir_inactivas=True, ordenar_por="nombre")
        self.assertEqual([x["producto"] for x in r["recetas"]],
                         ["Chocolate", "Fresa", "Vieja"])
        chocolate = r["recetas"][0]["vendido_en_periodo"]
        self.assertEqual(chocolate["unidades"], 3)
        self.assertEqual(chocolate["ingreso_mxn"], 180.0)
        self.assertFalse(chocolate["estimado"])
        self.assertTrue(r["recetas"][1]["vendido_en_periodo"]["estimado"])
        self.assertIsNone(r["recetas"][2]["vendido_en_periodo"])

    def test_alertas_de_margen(self):
        r = self.consulta(consultas.alertas_de_margen)
        self.assertEqual((r["mes_actual"], r["mes_anterior"]), ("2026-08", "2026-07"))
        self.assertEqual([a["producto"] for a in r["alertas"]], ["Chocolate"])
        self.assertTrue(any("pocas unidades" in a for a in r["avisos"]))

    def test_compras_recientes(self):
        r = self.consulta(consultas.compras_recientes)
        self.assertEqual(r["periodo"]["desde"], "2026-07-22")
        self.assertEqual(r["total_filas"], 3)          # solo las del 1 de agosto
        self.assertEqual(r["total_mxn"], 600.0)
        r = self.consulta(consultas.compras_recientes, periodo="ultimos_30_dias",
                          insumo=" lech ")
        self.assertEqual([c["insumo"] for c in r["compras"]], ["Leche"])
        self.assertEqual(r["compras"][0]["proveedor"], "Costco")
        self.assertEqual(r["compras"][0]["costo_por_unidad_mxn"], 20.0)

    def test_compras_sin_resultados_lo_dicen(self):
        r = self.consulta(consultas.compras_recientes, insumo="mango")
        self.assertIn("mango", r["avisos"][0])

    def test_estado_de_resultados(self):
        r = self.consulta(consultas.estado_de_resultados, anio=2026, mes=8)
        # Reconocidas: las dos ventas de chocolate con costo completo.
        self.assertEqual(r["ingresos_mxn"], 180.0)
        self.assertEqual(r["ventas_sin_reconocer"],
                         {"ventas": 1, "cobrado_mxn": 100.0})
        self.assertTrue(any("no aparecen" in a for a in r["avisos"]))
        self.assertIn("El mes todavía no termina.", r["avisos"])
        self.assertIn("Servicios", [g["grupo"] for g in r["gastos"]])
        self.assertEqual(r["utilidad_neta_mxn"],
                         round(r["utilidad_bruta_mxn"] - r["gastos_mxn"], 2))

    def test_estado_de_resultados_por_omision_es_el_mes_en_curso(self):
        r = self.consulta(consultas.estado_de_resultados)
        self.assertEqual((r["periodo"]["anio"], r["periodo"]["mes"]), (2026, 8))

    def test_flujo_de_caja(self):
        r = self.consulta(consultas.flujo_de_caja, anio=2026, mes=8)
        self.assertEqual(r["neto_mxn"],
                         round(r["entradas_mxn"] - r["salidas_mxn"], 2))
        self.assertEqual(r["saldo_final_mxn"],
                         round(r["saldo_inicial_mxn"] + r["neto_mxn"], 2))
        self.assertTrue(any("caja contable" in a for a in r["avisos"]))

    def test_presupuesto_contra_real(self):
        r = self.consulta(consultas.presupuesto_contra_real, anio=2026, mes=8)
        self.assertEqual(r["ventas"]["meta_mxn"], 1000.0)
        self.assertEqual(r["ventas"]["real_mxn"], 280.0)
        self.assertEqual(r["ventas"]["cumplimiento_pct"], 28.0)
        reales = {g["categoria"]: g["real_mxn"] for g in r["gastos"]}
        self.assertEqual(reales["Servicios"], 200.0)
        self.assertEqual(r["avisos"], [])

    def test_mes_sin_presupuesto_lo_dice(self):
        r = self.consulta(consultas.presupuesto_contra_real, anio=2026, mes=6)
        self.assertIn("No hay presupuesto", r["avisos"][0])

    def test_resumen_lealtad_no_crea_la_configuracion(self):
        r = self.consulta(consultas.resumen_lealtad)
        self.assertEqual(r["clientes"]["total"], 1)        # Beto está de baja
        self.assertEqual(r["puntos"]["por_canjear"], 30)
        self.assertFalse(ConfiguracionPrograma.objects.exists())

    def test_clientes_frecuentes_sin_datos_personales(self):
        r = self.consulta(consultas.clientes_frecuentes)
        self.assertEqual(r["total_filas"], 1)
        ana = r["clientes"][0]
        self.assertEqual(ana["nombre"], "Ana")
        self.assertEqual(ana["nivel"], "Pro")
        self.assertEqual(ana["ticket_promedio_mxn"], 100.0)
        self.assertEqual(ana["dias_sin_comprar"], 10)
        self.assertNotIn("López", json.dumps(r, ensure_ascii=False))

    def test_salud_de_los_datos(self):
        r = self.consulta(consultas.salud_de_los_datos)
        self.assertEqual(r["ventas"]["total"], 5)
        self.assertEqual(r["ventas"]["con_costo_incompleto"], 1)
        self.assertEqual(r["insumos_de_recetas_activas_sin_ninguna_compra"],
                         {"total": 1, "nombres": ["Fresa"]})
        self.assertGreaterEqual(r["inventario_contable_mxn"], 0)
        self.assertTrue(any("incompleto" in a for a in r["avisos"]))

    def test_base_vacia_no_truena(self):
        Venta.objects.all().delete()
        Compra.objects.all().delete()
        for fn in (consultas.ventas_del_periodo, consultas.insumos_por_surtir,
                   consultas.margen_por_receta, consultas.alertas_de_margen,
                   consultas.estado_de_resultados, consultas.salud_de_los_datos):
            with self.subTest(fn.__name__):
                self.consulta(fn)


@unittest.skipUnless(HAY_MCP, "falta el paquete mcp (requirements-mcp.txt)")
class ServidorTests(TransactionTestCase):
    """El protocolo de punta a punta, con un cliente MCP en el mismo proceso.

    TransactionTestCase porque el SDK corre cada herramienta en otro hilo, y
    ese hilo abre su propia conexión: no vería datos sin confirmar.
    """
    NOMBRES = {
        "ventas_del_periodo", "productos_mas_vendidos", "insumos_por_surtir",
        "margen_por_receta", "alertas_de_margen", "compras_recientes",
        "estado_de_resultados", "flujo_de_caja", "presupuesto_contra_real",
        "resumen_lealtad", "clientes_frecuentes", "salud_de_los_datos",
    }

    def setUp(self):
        sembrar()

    def conversa(self, pasos):
        from mcp import Client
        from shake_mcp.server import construir_servidor

        async def correr():
            async with Client(construir_servidor()) as cliente:
                return [await paso(cliente) for paso in pasos]
        with con_hoy():
            return asyncio.run(correr())

    def test_lista_las_doce_herramientas_de_solo_lectura(self):
        [lista] = self.conversa([lambda c: c.list_tools()])
        self.assertEqual({t.name for t in lista.tools}, self.NOMBRES)
        for t in lista.tools:
            with self.subTest(t.name):
                self.assertTrue(t.annotations.read_only_hint)
                self.assertFalse(t.annotations.destructive_hint)
                self.assertGreater(len(t.description), 60)

    def test_llama_herramientas(self):
        ventas, insumos, lealtad = self.conversa([
            lambda c: c.call_tool("ventas_del_periodo", {"periodo": "esta_semana"}),
            lambda c: c.call_tool("insumos_por_surtir", {"limite": 2}),
            lambda c: c.call_tool("resumen_lealtad", {}),
        ])
        for r in (ventas, insumos, lealtad):
            self.assertFalse(r.is_error, r.content)
        datos = json.loads(ventas.content[0].text)
        self.assertEqual(datos["total_cobrado_mxn"], 190.0)
        self.assertLessEqual(len(json.loads(insumos.content[0].text)["insumos"]), 2)
        self.assertFalse(ConfiguracionPrograma.objects.exists())

    def test_un_argumento_malo_regresa_un_error_legible(self):
        fecha, periodo = self.conversa([
            lambda c: c.call_tool("ventas_del_periodo", {"desde": "2026-02-30"}),
            lambda c: c.call_tool("ventas_del_periodo", {"periodo": "trimestre"}),
        ])
        self.assertTrue(fecha.is_error)
        self.assertIn("AAAA-MM-DD", fecha.content[0].text)
        self.assertTrue(periodo.is_error)
