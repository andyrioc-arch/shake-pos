"""Alarma de margen: avisa cuando el margen de un producto BAJA.

Solo la caída. Que un producto mejore su margen no es una alarma, y mezclar
las dos direcciones convierte el aviso en ruido que se aprende a ignorar.

Todo se mide en PORCENTAJE de punta a punta: así se guarda el umbral, así se
compara y así sale en el panel. Un ratio a medio camino es una unidad más que
traducir cada vez que alguien lea esto.
"""
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal

from django.utils.timezone import localdate

from .models import ConfiguracionAlarmas, Venta

CIEN = Decimal("100")


def _mes_siguiente(primero):
    if primero.month == 12:
        return date(primero.year + 1, 1, 1)
    return date(primero.year, primero.month + 1, 1)


def _margenes_por_mes(desde, hasta):
    """{(anio, mes): {receta_id: {margen, unidades, nombre, estimado}}}.

    Una sola pasada por el rango en vez de una consulta por mes: además de
    ahorrarse la precarga repetida, un rango de fechas usa el índice de
    `fecha`, mientras que filtrar por `fecha__month` obliga a barrer el año.
    """
    acum = defaultdict(lambda: defaultdict(lambda: {
        "ingreso": Decimal("0"), "ganancia": Decimal("0"),
        "unidades": 0, "estimado": False, "nombre": "",
    }))

    ventas = (Venta.objects.comerciales()
              .filter(fecha__gte=desde, fecha__lt=hasta)
              .order_by())          # el orden del Meta no sirve para agregar
    for v in ventas:
        fila = acum[(v.fecha.year, v.fecha.month)][v.receta_id]
        fila["ingreso"] += v.ingreso
        # `ganancia` es ingreso menos costo de la línea completa. La definición
        # de margen vive en el modelo; repetirla aquí la deja desfasada el día
        # que cambie, sin que nada falle.
        fila["ganancia"] += v.ganancia
        fila["unidades"] += v.cantidad
        # Una venta sin costo completo cae al estimado del catálogo. El número
        # sirve, pero quien lo lea tiene que saber sobre qué está parado.
        if not v.costo_esta_completo:
            fila["estimado"] = True
        fila["nombre"] = str(v.receta)

    return {
        periodo: {rid: {**fila, "margen": fila["ganancia"] * CIEN / fila["ingreso"]}
                  for rid, fila in recetas.items() if fila["ingreso"] > 0}
        for periodo, recetas in acum.items()
    }


def alarmas_margen(hoy=None):
    """Productos cuyo margen cayó respecto al mes anterior más que el umbral.

    Compara el mes en curso contra el mes calendario anterior. Un producto que
    no se vendió en alguno de los dos no aparece: sin base de comparación no se
    inventa una caída, igual que el costeo no inventa un costo.
    """
    hoy = hoy or localdate()
    umbral = ConfiguracionAlarmas.get().umbral_caida_margen

    mes_actual = hoy.replace(day=1)
    mes_anterior = (mes_actual - timedelta(days=1)).replace(day=1)
    por_mes = _margenes_por_mes(mes_anterior, _mes_siguiente(mes_actual))
    actual = por_mes.get((mes_actual.year, mes_actual.month), {})
    previo = por_mes.get((mes_anterior.year, mes_anterior.month), {})

    avisos = []
    for rid, a in actual.items():
        p = previo.get(rid)
        # Con margen anterior de cero o negativo la caída relativa no significa
        # nada: dividir entre él da infinito o invierte el signo.
        if not p or p["margen"] <= 0:
            continue
        caida = (p["margen"] - a["margen"]) * CIEN / p["margen"]
        if caida < umbral:
            continue
        avisos.append({
            "nombre": a["nombre"],
            "margen_anterior": p["margen"],
            "margen_actual": a["margen"],
            "caida": caida,
            "unidades_anterior": p["unidades"],
            "unidades_actual": a["unidades"],
            # Si cualquiera de los dos lados se apoyó en el catálogo, la
            # comparación es de estimados y hay que decirlo.
            "estimado": p["estimado"] or a["estimado"],
        })

    avisos.sort(key=lambda x: x["caida"], reverse=True)
    return {
        "avisos": avisos,
        "umbral": umbral,
        "mes_actual": mes_actual,
        "mes_anterior": mes_anterior,
    }
