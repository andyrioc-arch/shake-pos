"""Compara el presupuesto contra lo real (ventas y compras de inventario, gastos)."""
from collections import defaultdict
from decimal import Decimal

from inventario.models import Venta, Compra
from contabilidad.models import Movimiento, CategoriaGasto
from .models import PresupuestoVenta, PresupuestoGasto, MESES

MES_NOMBRE = dict(MESES)


def _labels_categorias():
    """Mapa clave -> nombre de todas las categorías (incluye inactivas)."""
    return {c.clave: c.nombre for c in CategoriaGasto.objects.all()}


def _pct(real, meta):
    """% de cumplimiento de lo real respecto a la meta."""
    if meta and meta != 0:
        return (real / meta) * 100
    return None


def ventas_reales_por_periodo():
    reales = defaultdict(Decimal)
    for v in Venta.objects.select_related("receta"):
        reales[(v.fecha.year, v.fecha.month)] += v.ingreso
    return reales


def gastos_reales_por_periodo_categoria():
    """{(anio, mes, categoria): monto_real} de gastos operativos y compras de stock.

    Toma los gastos operativos del libro de movimientos y las compras de
    inventario (que cuentan como gasto real de la categoría "insumos"), para que
    el presupuesto refleje lo que realmente se gastó.
    """
    reales = defaultdict(Decimal)
    for g in Movimiento.objects.filter(tipo=Movimiento.Tipo.GASTO):
        reales[(g.fecha.year, g.fecha.month, g.categoria)] += g.monto
    for c in Compra.objects.select_related("ingrediente"):
        reales[(c.fecha.year, c.fecha.month, "insumos")] += c.total
    return reales


def comparativo_ventas():
    reales = ventas_reales_por_periodo()
    filas = []
    # Une periodos presupuestados y periodos con ventas reales
    periodos = set((p.anio, p.mes) for p in PresupuestoVenta.objects.all())
    periodos |= set(reales.keys())
    for (anio, mes) in sorted(periodos, reverse=True):
        pv = PresupuestoVenta.objects.filter(anio=anio, mes=mes).first()
        meta = pv.monto if pv else Decimal("0")
        real = reales.get((anio, mes), Decimal("0"))
        filas.append({
            "periodo": f"{MES_NOMBRE[mes]} {anio}",
            "meta": meta, "real": real,
            "diferencia": real - meta,
            "pct": _pct(real, meta),
        })
    return filas


def comparativo_gastos():
    reales = gastos_reales_por_periodo_categoria()
    cat_label = _labels_categorias()
    filas = []
    # Une (anio, mes, categoria) presupuestados con los reales
    claves = set(
        (p.anio, p.mes, p.categoria) for p in PresupuestoGasto.objects.all()
    )
    claves |= set(reales.keys())
    for (anio, mes, cat) in sorted(claves, reverse=True):
        pg = PresupuestoGasto.objects.filter(
            anio=anio, mes=mes, categoria=cat
        ).first()
        meta = pg.monto if pg else Decimal("0")
        real = reales.get((anio, mes, cat), Decimal("0"))
        # En gastos, gastar MENOS que la meta es bueno (diferencia favorable)
        filas.append({
            "periodo": f"{MES_NOMBRE[mes]} {anio}",
            "categoria": cat_label.get(cat, cat),
            "meta": meta, "real": real,
            "diferencia": real - meta,   # positivo = te pasaste
            "pct": _pct(real, meta),
        })
    return filas


def periodos_disponibles():
    """Lista de (anio, mes) con presupuesto o movimientos reales, más reciente primero."""
    s = set((p.anio, p.mes) for p in PresupuestoVenta.objects.all())
    s |= set((p.anio, p.mes) for p in PresupuestoGasto.objects.all())
    s |= set(ventas_reales_por_periodo().keys())
    s |= set((a, m) for (a, m, c) in gastos_reales_por_periodo_categoria())
    return sorted(s, reverse=True)


def detalle_mes(anio, mes):
    """Datos de un solo mes: meta de ventas (objeto) y líneas de gasto (con objeto)."""
    # ── Ventas (una sola meta por mes) ──
    pv = PresupuestoVenta.objects.filter(anio=anio, mes=mes).first()
    real_v = ventas_reales_por_periodo().get((anio, mes), Decimal("0"))
    meta_v = pv.monto if pv else Decimal("0")
    ventas = {
        "obj": pv, "meta": meta_v, "real": real_v,
        "diferencia": real_v - meta_v, "pct": _pct(real_v, meta_v),
    }

    # ── Gastos (varias líneas, una por categoría) ──
    reales_g = gastos_reales_por_periodo_categoria()
    cat_label = _labels_categorias()
    pgs = {p.categoria: p
           for p in PresupuestoGasto.objects.filter(anio=anio, mes=mes)}
    claves = set(pgs.keys())
    claves |= set(c for (a, m, c) in reales_g if a == anio and m == mes)
    gastos = []
    for c in sorted(claves):
        pg = pgs.get(c)
        meta = pg.monto if pg else Decimal("0")
        real = reales_g.get((anio, mes, c), Decimal("0"))
        gastos.append({
            "obj": pg, "categoria_key": c,
            "categoria": cat_label.get(c, c),
            "meta": meta, "real": real,
            "diferencia": real - meta, "pct": _pct(real, meta),
        })

    # Categorías que aún no tienen línea de presupuesto (para el formulario)
    categorias_libres = [
        (c.clave, c.nombre)
        for c in CategoriaGasto.objects.filter(activo=True)
        if c.clave not in pgs
    ]

    return {
        "ventas": ventas,
        "gastos": gastos,
        "tot_meta_gastos": sum((g["meta"] for g in gastos), Decimal("0")),
        "tot_real_gastos": sum((g["real"] for g in gastos), Decimal("0")),
        "categorias_libres": categorias_libres,
    }


def resumen():
    cv = comparativo_ventas()
    cg = comparativo_gastos()
    tot_meta_v = sum((f["meta"] for f in cv), Decimal("0"))
    tot_real_v = sum((f["real"] for f in cv), Decimal("0"))
    tot_meta_g = sum((f["meta"] for f in cg), Decimal("0"))
    tot_real_g = sum((f["real"] for f in cg), Decimal("0"))
    return {
        "ventas": cv, "gastos": cg,
        "tot_meta_ventas": tot_meta_v, "tot_real_ventas": tot_real_v,
        "tot_meta_gastos": tot_meta_g, "tot_real_gastos": tot_real_g,
    }
