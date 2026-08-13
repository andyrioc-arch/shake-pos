"""Alarma de margen: avisa cuando el margen de un producto BAJA.

Solo la caída. Que un producto mejore su margen no es una alarma, y mezclar
las dos direcciones convierte el aviso en ruido que se aprende a ignorar.
"""
from collections import defaultdict
from datetime import date
from decimal import Decimal

from django.utils.timezone import localdate

from .models import ConfiguracionCosteo, Venta


def _mes_anterior(anio, mes):
    return (anio - 1, 12) if mes == 1 else (anio, mes - 1)


def _margenes_del_mes(anio, mes):
    """{receta_id: {margen, unidades, nombre, estimado}} de ese mes.

    Las cortesías quedan fuera: no tienen ingreso y hundirían el margen del
    producto sin que su precio ni su costo hayan cambiado. Es el mismo criterio
    que usa `finanzas.calculos.margen_contribucion_promedio`.
    """
    ingreso = defaultdict(Decimal)
    costo = defaultdict(Decimal)
    unidades = defaultdict(int)
    estimado = defaultdict(bool)
    nombre = {}

    ventas = (Venta.objects
              .filter(fecha__year=anio, fecha__month=mes, es_cortesia=False)
              .con_costeo())
    for v in ventas:
        ingreso[v.receta_id] += v.ingreso
        costo[v.receta_id] += v.costo_de_ventas
        unidades[v.receta_id] += v.cantidad
        # Una venta sin costo completo cae al estimado del catálogo. El número
        # sirve, pero quien lo lea tiene que saber sobre qué está parado.
        if not v.costo_esta_completo:
            estimado[v.receta_id] = True
        nombre[v.receta_id] = str(v.receta)

    res = {}
    for rid, ing in ingreso.items():
        if ing <= 0:
            continue  # sin ingreso no hay margen que comparar
        res[rid] = {
            "margen": (ing - costo[rid]) / ing,
            "unidades": unidades[rid],
            "nombre": nombre[rid],
            "estimado": estimado[rid],
        }
    return res


def alarmas_margen(hoy=None):
    """Productos cuyo margen cayó respecto al mes anterior más que el umbral.

    Compara el mes en curso contra el mes calendario anterior. Un producto que
    no se vendió en alguno de los dos no aparece: sin base de comparación no se
    inventa una caída, igual que el costeo no inventa un costo.
    """
    hoy = hoy or localdate()
    umbral_pct = ConfiguracionCosteo.get().umbral_caida_margen
    umbral = Decimal(umbral_pct) / Decimal("100")

    actual = _margenes_del_mes(hoy.year, hoy.month)
    anio_ant, mes_ant = _mes_anterior(hoy.year, hoy.month)
    previo = _margenes_del_mes(anio_ant, mes_ant)

    avisos = []
    for rid, a in actual.items():
        p = previo.get(rid)
        # Con margen anterior de cero o negativo la caída relativa no significa
        # nada: dividir entre él da infinito o invierte el signo.
        if not p or p["margen"] <= 0:
            continue
        caida = (p["margen"] - a["margen"]) / p["margen"]
        if caida < umbral:
            continue
        avisos.append({
            "receta_id": rid,
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
        "umbral_pct": umbral_pct,
        "mes_actual": date(hoy.year, hoy.month, 1),
        "mes_anterior": date(anio_ant, mes_ant, 1),
    }
