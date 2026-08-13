"""Cálculos financieros: punto de equilibrio, flujo de efectivo y recuperación."""
from collections import defaultdict
from decimal import Decimal
from datetime import date

from django.db.models import Sum

from inventario.models import Venta, Compra, Receta
from contabilidad import posting as cont
from contabilidad.models import Cuenta, Movimiento
from .models import (
    CostoFijo, InversionInicial, MovimientoEfectivo, PronosticoFlujoCuenta, MESES,
)

MES_NOMBRE = dict(MESES)

# Cada categoría de costo fijo se refleja en su cuenta de gasto contable.
CATEGORIA_FIJO_A_CUENTA = {
    "renta": "502", "sueldos": "505", "mercadotecnia": "504",
    "servicios": "503", "otro": "509",
}


def cuentas_flujo():
    """Cuentas contables que representan flujo de efectivo (ingresos/gastos/capital)."""
    tipos = [Cuenta.Tipo.INGRESO, Cuenta.Tipo.GASTO, Cuenta.Tipo.CAPITAL]
    qs = Cuenta.objects.filter(tipo__in=tipos)
    if not qs.exists():
        cont.crear_catalogo()  # siembra el catálogo solo la primera vez
    return list(qs.order_by("codigo"))


def _real_por_cuenta(anio, mes):
    """{cuenta_id: monto real de efectivo del mes} por cuenta contable."""
    res = defaultdict(lambda: Decimal("0"))
    # Ventas (cobros) y compras (pagos) desde el libro de movimientos.
    movs = (Movimiento.objects
            .filter(fecha__year=anio, fecha__month=mes)
            .values("cuenta").annotate(t=Sum("monto")))
    for m in movs:
        res[m["cuenta"]] += m["t"] or Decimal("0")
    # Costos fijos mensuales, cargados a su cuenta de gasto.
    codigo_a_id = {c.codigo: c.id for c in Cuenta.objects.all()}
    for cf in CostoFijo.objects.filter(activo=True):
        cid = codigo_a_id.get(CATEGORIA_FIJO_A_CUENTA.get(cf.categoria, "509"))
        if cid:
            res[cid] += cf.monto_mensual
    return res


def flujo_desglose(anio, mes):
    """Flujo pronóstico vs. real desglosado por cuenta contable, para un mes."""
    cuentas = cuentas_flujo()
    real = _real_por_cuenta(anio, mes)
    pron = {p.cuenta_id: p.monto
            for p in PronosticoFlujoCuenta.objects.filter(anio=anio, mes=mes)}

    filas = []
    tot = {"pron_ent": Decimal("0"), "pron_sal": Decimal("0"),
           "real_ent": Decimal("0"), "real_sal": Decimal("0")}
    for c in cuentas:
        r = real.get(c.id, Decimal("0"))
        p = pron.get(c.id, Decimal("0"))
        if not r and not p:
            continue
        es_entrada = c.tipo in (Cuenta.Tipo.INGRESO, Cuenta.Tipo.CAPITAL)
        filas.append({
            "cuenta_id": c.id, "codigo": c.codigo, "nombre": c.nombre,
            "es_entrada": es_entrada,
            "tipo_txt": "Entrada" if es_entrada else "Salida",
            "pronostico": p, "real": r, "dif": r - p,
        })
        if es_entrada:
            tot["pron_ent"] += p; tot["real_ent"] += r
        else:
            tot["pron_sal"] += p; tot["real_sal"] += r

    return {
        "filas": filas,
        "pron_entradas": tot["pron_ent"], "pron_salidas": tot["pron_sal"],
        "pron_neto": tot["pron_ent"] - tot["pron_sal"],
        "real_entradas": tot["real_ent"], "real_salidas": tot["real_sal"],
        "real_neto": tot["real_ent"] - tot["real_sal"],
    }


def periodos_flujo():
    """(anio, mes) con actividad real o pronóstico, más reciente primero."""
    s = set(cont.periodos_disponibles())
    for p in PronosticoFlujoCuenta.objects.values_list("anio", "mes"):
        s.add(p)
    return sorted(s, reverse=True)


def inversion_desglose():
    """Inversión inicial agrupada por categoría, con subtotales y total."""
    labels = dict(InversionInicial.Categoria.choices)
    grupos = {}
    for it in InversionInicial.objects.all():
        g = grupos.setdefault(it.categoria, {
            "categoria": labels.get(it.categoria, it.categoria),
            "items": [], "subtotal": Decimal("0"),
        })
        g["items"].append(it)
        g["subtotal"] += it.monto
    grupos = sorted(grupos.values(), key=lambda x: x["categoria"])
    total = sum((g["subtotal"] for g in grupos), Decimal("0"))
    return {"grupos": grupos, "total": total}


def margen_contribucion_promedio():
    """Margen de contribución promedio por shake, ponderado por ventas.
    Margen de contribución = ingreso − costo real de la venta (FIFO).
    Las cortesías no entran: no tienen ingreso y hundirían el promedio.
    Si no hay ventas, promedia las recetas activas."""
    ventas = Venta.objects.comerciales()
    total_unidades = 0
    total_margen = Decimal("0")
    for v in ventas:
        # `ganancia` es ingreso menos costo de la LÍNEA completa (add-ons
        # incluidos); se reparte entre las unidades después, no antes.
        total_margen += v.ganancia
        total_unidades += v.cantidad
    if total_unidades:
        return total_margen / total_unidades, total_unidades
    # Sin ventas: promedio simple de recetas activas
    recetas = Receta.objects.filter(activa=True)
    if recetas:
        suma = sum((r.precio_venta - r.costo_receta) for r in recetas)
        return suma / len(recetas), 0
    return Decimal("0"), 0


def punto_equilibrio():
    """Cuántos shakes vender al mes para cubrir los costos fijos."""
    fijos = CostoFijo.total_mensual()
    mc, _ = margen_contribucion_promedio()
    if mc and mc > 0:
        unidades = fijos / mc
        return {
            "costos_fijos": fijos,
            "margen_contribucion": mc,
            "unidades_mes": unidades,
            "unidades_dia": unidades / Decimal("30"),
            "ingreso_necesario": None,  # se llena abajo
        }
    return {
        "costos_fijos": fijos, "margen_contribucion": mc,
        "unidades_mes": None, "unidades_dia": None, "ingreso_necesario": None,
    }


def _periodo(d):
    return (d.year, d.month)


def flujo_mensual():
    """Construye el flujo de efectivo mes a mes y la recuperación acumulada."""
    ingresos = defaultdict(Decimal)
    costo_var = defaultdict(Decimal)
    cortesias = defaultdict(Decimal)
    compras = defaultdict(Decimal)
    extras = defaultdict(Decimal)

    for v in Venta.objects.con_costeo():
        p = _periodo(v.fecha)
        ingresos[p] += v.ingreso
        # El costo de una cortesía es mercadotecnia, no costo variable de venta:
        # va aparte para no ensuciar el margen del producto. Pero se sigue
        # restando de la ganancia operativa, porque el insumo sí se gastó.
        if v.es_cortesia:
            cortesias[p] += v.costo_de_ventas
        else:
            costo_var[p] += v.costo_de_ventas

    for c in Compra.objects.all():
        compras[_periodo(c.fecha)] += c.total

    for m in MovimientoEfectivo.objects.all():
        extras[_periodo(m.fecha)] += m.monto_con_signo

    fijos = CostoFijo.total_mensual()
    inversion = InversionInicial.total()

    periodos = sorted(set(ingresos) | set(compras) | set(extras))
    filas = []
    acumulado = Decimal("0")
    recuperado_en = None

    for p in periodos:
        ing = ingresos[p]
        # Ganancia operativa del mes = ingresos − costos fijos − movimientos extra netos negativos
        # Flujo de efectivo real = ingresos − compras − fijos + extras
        flujo = ing - compras[p] - fijos + extras[p]
        # Para recuperación usamos la ganancia operativa (ingresos − costo variable − fijos)
        ganancia_op = ing - costo_var[p] - cortesias[p] - fijos
        acumulado += ganancia_op
        if recuperado_en is None and inversion > 0 and acumulado >= inversion:
            recuperado_en = p
        filas.append({
            "anio": p[0], "mes": p[1],
            "ingresos": ing,
            "costo_variable": costo_var[p],
            "cortesias": cortesias[p],
            "compras": compras[p],
            "costos_fijos": fijos,
            "extras": extras[p],
            "flujo_efectivo": flujo,
            "ganancia_operativa": ganancia_op,
            "ganancia_acumulada": acumulado,
        })

    return {
        "filas": filas,
        "inversion_inicial": inversion,
        "recuperado_en": recuperado_en,
        "ganancia_acumulada_final": acumulado,
    }


def resumen():
    """Junta todo para mostrar en el panel."""
    pe = punto_equilibrio()
    fm = flujo_mensual()
    mc, unidades_vendidas = margen_contribucion_promedio()
    return {
        "punto_equilibrio": pe,
        "flujo": fm,
        "margen_contribucion": mc,
        "unidades_vendidas": unidades_vendidas,
        "costos_fijos_mes": CostoFijo.total_mensual(),
        "inversion_total": InversionInicial.total(),
    }
