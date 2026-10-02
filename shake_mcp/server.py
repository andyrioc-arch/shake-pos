"""Servidor MCP de shake-pos, solo lectura, por stdio.

    python -m shake_mcp.server            # producción: SHAKE_MCP_DATABASE_URL
    python -m shake_mcp.server --local    # desarrollo: el SQLite del repo

Lee `SHAKE_MCP_DATABASE_URL` y no `DATABASE_URL` para no heredar por accidente
la cadena de administrador que alguien tenga exportada en su terminal. Sin
esa variable y sin `--local`, no arranca.

Por stdio, stdout es el canal del protocolo: nada aquí puede imprimir en él.
"""
import argparse
import logging
import os
import sys
from pathlib import Path
from typing import Annotated, Literal

RAIZ = Path(__file__).resolve().parent.parent
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))

log = logging.getLogger("shake_mcp")

INSTRUCCIONES = """\
Herramientas de SOLO LECTURA sobre el punto de venta de SHAKE (shakes de proteína).
- Montos en pesos mexicanos (MXN), con IVA incluido. Fechas AAAA-MM-DD.
- Di siempre las fechas exactas del periodo que devolvió la herramienta (campo «periodo»).
- Si la respuesta trae «avisos», compártelos en una frase: explican por qué un número
  puede ser estimado o estar incompleto.
- «Vendido» (ventas_del_periodo) es lo cobrado en caja. «Reconocido»
  (estado_de_resultados) es contable y puede ser menor: una venta solo entra cuando su
  costo está completo.
- No inventes cifras: si ninguna herramienta trae el dato, dilo.
"""

Periodo = Literal["hoy", "ayer", "esta_semana", "semana_pasada", "este_mes",
                  "mes_pasado", "ultimos_7_dias", "ultimos_30_dias"]


def _campos():
    from pydantic import Field
    return {
        "periodo": Field(
            description="Periodo relativo, calculado con la fecha de hoy en "
                        "Ciudad de México. Se ignora si se da desde/hasta."),
        "desde": Field(
            description="Fecha inicial AAAA-MM-DD. Opcional; gana sobre «periodo»."),
        "hasta": Field(
            description="Fecha final AAAA-MM-DD. Opcional; por omisión, hoy."),
        "anio": Field(
            description="Año, p. ej. 2026. Por omisión, el año en curso."),
        "mes": Field(
            ge=1, le=12,
            description="Mes del 1 al 12. Por omisión, el mes en curso."),
        "limite": Field(
            ge=1, le=50, description="Máximo de filas a devolver (1 a 50)."),
    }


def construir_servidor():
    """El servidor con sus herramientas. Requiere Django ya configurado."""
    from django.db import connections
    from mcp.server.mcpserver import MCPServer
    from mcp.server.mcpserver.exceptions import ToolError
    from mcp.types import ToolAnnotations
    from pydantic import Field

    from shake_mcp import consultas
    from shake_mcp.lectura import solo_lectura
    from shake_mcp.periodos import ErrorDeConsulta

    servidor = MCPServer(name="shake", instructions=INSTRUCCIONES)
    solo_lee = ToolAnnotations(read_only_hint=True, destructive_hint=False,
                               idempotent_hint=True, open_world_hint=False)
    f = _campos()

    def ejecuta(consulta, **kwargs):
        # Corre en un hilo del pool del SDK; la conexión de Django es por
        # hilo, así que se cierra aquí mismo y no se queda colgada del pooler.
        try:
            with solo_lectura():
                return consulta(**kwargs)
        except ErrorDeConsulta as e:
            raise ToolError(str(e)) from e
        except Exception:
            log.exception("Falló %s", consulta.__name__)
            raise
        finally:
            connections.close_all()

    def herramienta(descripcion):
        return servidor.tool(description=descripcion, annotations=solo_lee)

    @herramienta(
        "Cuánto se vendió (lo COBRADO en caja) en un periodo: total en MXN, "
        "tickets, ticket promedio, unidades cobradas y regaladas, descuentos, "
        "ganancia bruta, desglose por método de pago y por día, y comparación "
        "contra el periodo anterior equivalente. Úsala para «¿cuánto vendí "
        "hoy / esta semana / este mes?». No es la utilidad contable: para eso "
        "está estado_de_resultados.")
    def ventas_del_periodo(
        periodo: Annotated[Periodo | None, f["periodo"]] = None,
        desde: Annotated[str | None, f["desde"]] = None,
        hasta: Annotated[str | None, f["hasta"]] = None,
    ) -> dict:
        return ejecuta(consultas.ventas_del_periodo,
                       periodo=periodo, desde=desde, hasta=hasta)

    @herramienta(
        "Ranking de productos (shakes) vendidos en un periodo, por unidades o "
        "por ingreso, con cuántos se cobraron y cuántos se regalaron "
        "(cortesías). Úsala para «¿qué se vende más?» o «¿cuántos X vendí?».")
    def productos_mas_vendidos(
        periodo: Annotated[Periodo | None, f["periodo"]] = None,
        desde: Annotated[str | None, f["desde"]] = None,
        hasta: Annotated[str | None, f["hasta"]] = None,
        ordenar_por: Annotated[Literal["unidades", "ingreso"], Field(
            description="Criterio del ranking.")] = "unidades",
        limite: Annotated[int | None, f["limite"]] = None,
    ) -> dict:
        return ejecuta(consultas.productos_mas_vendidos, periodo=periodo,
                       desde=desde, hasta=hasta, ordenar_por=ordenar_por,
                       limite=limite)

    @herramienta(
        "Insumos (ingredientes y empaque) con poco stock. Compara el stock "
        "calculado (compras capturadas menos lo consumido por ventas y mermas) "
        "contra el mínimo para preparar 5 shakes de cada receta activa que lo "
        "usa. Por omisión, solo los que están debajo del mínimo. Úsala para "
        "«¿qué insumos me faltan?» o «¿qué tengo que comprar?».")
    def insumos_por_surtir(
        solo_faltantes: Annotated[bool, Field(
            description="true = solo los que están debajo del mínimo; "
                        "false = todo el inventario.")] = True,
        limite: Annotated[int | None, f["limite"]] = None,
    ) -> dict:
        return ejecuta(consultas.insumos_por_surtir,
                       solo_faltantes=solo_faltantes, limite=limite)

    @herramienta(
        "Margen de cada receta: precio, costo según el catálogo, costo a "
        "precios de la última compra real, ganancia por shake y margen %. Si "
        "se da un periodo, agrega el margen real de lo vendido en ese periodo. "
        "Úsala para «¿qué receta deja más margen?» o «¿cuánto me cuesta hacer "
        "un X?».")
    def margen_por_receta(
        ordenar_por: Annotated[Literal["margen", "ganancia", "nombre"], Field(
            description="margen = % sobre el precio; ganancia = pesos por "
                        "shake; nombre = alfabético.")] = "margen",
        incluir_inactivas: Annotated[bool, Field(
            description="Incluir recetas que ya no se venden.")] = False,
        periodo: Annotated[Periodo | None, f["periodo"]] = None,
        desde: Annotated[str | None, f["desde"]] = None,
        hasta: Annotated[str | None, f["hasta"]] = None,
        limite: Annotated[int | None, f["limite"]] = None,
    ) -> dict:
        return ejecuta(consultas.margen_por_receta, ordenar_por=ordenar_por,
                       incluir_inactivas=incluir_inactivas, periodo=periodo,
                       desde=desde, hasta=hasta, limite=limite)

    @herramienta(
        "Productos cuyo margen BAJÓ en el mes en curso contra el mes anterior "
        "más que el umbral configurado. Úsala para «¿algún producto me está "
        "dejando menos?» o «¿subieron mis costos?».")
    def alertas_de_margen() -> dict:
        return ejecuta(consultas.alertas_de_margen)

    @herramienta(
        "Compras de insumos capturadas en un periodo (por omisión, los últimos "
        "30 días): fecha, insumo, cantidad, monto pagado, costo por unidad y "
        "proveedor, con el total gastado. Se puede filtrar por nombre de "
        "insumo. Úsala para «¿cuánto gasté en insumos?» o «¿cuándo compré "
        "fresa y a cuánto?».")
    def compras_recientes(
        periodo: Annotated[Periodo | None, f["periodo"]] = None,
        desde: Annotated[str | None, f["desde"]] = None,
        hasta: Annotated[str | None, f["hasta"]] = None,
        insumo: Annotated[str | None, Field(
            description="Parte del nombre del insumo, p. ej. «fresa». "
                        "Opcional.")] = None,
        limite: Annotated[int | None, f["limite"]] = None,
    ) -> dict:
        return ejecuta(consultas.compras_recientes, periodo=periodo,
                       desde=desde, hasta=hasta, insumo=insumo, limite=limite)

    @herramienta(
        "Estado de resultados CONTABLE de un mes: ingresos reconocidos, costo "
        "de ventas (comercial y cortesías), utilidad bruta, gastos de "
        "operación por grupo y utilidad neta. Solo incluye ventas con costo "
        "completo; las demás se reportan en «ventas_sin_reconocer». Úsala "
        "para «¿cuánto gané este mes?» o «¿en qué se me va el dinero?».")
    def estado_de_resultados(
        anio: Annotated[int | None, f["anio"]] = None,
        mes: Annotated[int | None, f["mes"]] = None,
    ) -> dict:
        return ejecuta(consultas.estado_de_resultados, anio=anio, mes=mes)

    @herramienta(
        "Efectivo de un mes según la contabilidad (cuenta Caja): saldo "
        "inicial, entradas, salidas y saldo final. Úsala para «¿cuánto dinero "
        "entró y salió este mes?».")
    def flujo_de_caja(
        anio: Annotated[int | None, f["anio"]] = None,
        mes: Annotated[int | None, f["mes"]] = None,
    ) -> dict:
        return ejecuta(consultas.flujo_de_caja, anio=anio, mes=mes)

    @herramienta(
        "Presupuesto contra lo real en un mes: meta de ventas contra lo "
        "cobrado, y meta de gasto por categoría contra lo gastado (las compras "
        "de insumos cuentan en la categoría de insumos). En gastos, una "
        "diferencia positiva significa que se gastó de más. Úsala para «¿voy "
        "bien con el presupuesto?».")
    def presupuesto_contra_real(
        anio: Annotated[int | None, f["anio"]] = None,
        mes: Annotated[int | None, f["mes"]] = None,
    ) -> dict:
        return ejecuta(consultas.presupuesto_contra_real, anio=anio, mes=mes)

    @herramienta(
        "Resumen agregado del programa de lealtad: clientes totales, activos y "
        "nuevos, puntos emitidos, redimidos y por canjear, canjes pendientes "
        "de entregar y cuánto costaría que todos canjearan sus puntos. Sin "
        "datos personales. Úsala para «¿cómo va el programa de puntos?».")
    def resumen_lealtad() -> dict:
        return ejecuta(consultas.resumen_lealtad)

    @herramienta(
        "Mejores clientes del programa de lealtad por gasto, visitas o puntos: "
        "nombre de pila, visitas, gasto total, ticket promedio, puntos, nivel "
        "y fecha de su última compra. Nunca devuelve teléfono ni correo. Úsala "
        "para «¿quiénes son mis mejores clientes?».")
    def clientes_frecuentes(
        ordenar_por: Annotated[Literal["gasto", "visitas", "puntos"], Field(
            description="Criterio del ranking.")] = "gasto",
        limite: Annotated[int | None, f["limite"]] = None,
    ) -> dict:
        return ejecuta(consultas.clientes_frecuentes,
                       ordenar_por=ordenar_por, limite=limite)

    @herramienta(
        "Qué tan completos están los datos: cuántas ventas y compras hay y de "
        "qué fechas, cuántas ventas no tienen costo completo, qué insumos de "
        "recetas activas no tienen ninguna compra capturada y el valor "
        "contable del inventario. Úsala cuando un número se vea raro o para "
        "explicar por qué el estado de resultados sale bajo o en cero.")
    def salud_de_los_datos() -> dict:
        return ejecuta(consultas.salud_de_los_datos)

    return servidor


def _configura_entorno(local):
    if local:
        os.environ.pop("DATABASE_URL", None)
    else:
        url = os.environ.get("SHAKE_MCP_DATABASE_URL", "").strip()
        if not url:
            sys.exit("shake_mcp: falta SHAKE_MCP_DATABASE_URL (la cadena del "
                     "rol de solo lectura). Para desarrollo usa --local.")
        os.environ["DATABASE_URL"] = url
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "habits_inventory.settings")


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="shake_mcp", description="Servidor MCP de shake-pos (solo lectura).")
    parser.add_argument("--local", action="store_true",
                        help="Usar el SQLite del repo en vez de Postgres.")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, stream=sys.stderr,
                        format="%(asctime)s %(name)s %(levelname)s %(message)s")

    _configura_entorno(args.local)
    import django
    django.setup()

    from django.db import connections
    from shake_mcp.lectura import RolConEscritura, verificar_rol
    try:
        verificar_rol()
    except RolConEscritura as e:
        sys.exit(f"shake_mcp: {e}")
    finally:
        connections.close_all()

    construir_servidor().run("stdio")


if __name__ == "__main__":
    main()
