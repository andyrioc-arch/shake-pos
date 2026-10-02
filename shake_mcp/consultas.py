"""Lo que el servidor MCP sabe responder. Solo lee, y devuelve JSON.

Cada consulta delega en la lógica que ya usan los paneles —el costeo, el
posteo, el presupuesto, las métricas de lealtad— para que el asistente y la
pantalla no puedan dar dos números distintos para la misma pregunta.

La forma es igual en todas:
- montos en MXN, IVA incluido, con dos decimales y sufijo `_mxn`
- porcentajes con un decimal y sufijo `_pct`
- fechas AAAA-MM-DD
- listas con tope (`limite`, 50 como máximo) y `total_filas` / `truncado`
- `avisos`: lo que quien lea el número tiene que saber para no malinterpretarlo
"""
from collections import defaultdict
from decimal import ROUND_HALF_UP, Decimal

from django.db.models import Count, Max, Min
from django.utils.timezone import localdate

from contabilidad import posting
from inventario.alarmas import alarmas_margen
from inventario.models import Compra, Ingrediente, Receta, Venta
from lealtad import metricas
from lealtad.models import Cliente, ConfiguracionPrograma, Nivel
from presupuesto import comparativo

from . import periodos
from .periodos import ErrorDeConsulta

CERO = Decimal("0")
CIEN = Decimal("100")
LIMITE_MAXIMO = 50
# Con menos unidades que esto en el mes, una venta atípica mueve el margen.
POCAS_UNIDADES = 5


# ── Formato ──────────────────────────────────────────────────────────────────
def _mxn(valor):
    return float(Decimal(valor or 0).quantize(Decimal("0.01"), ROUND_HALF_UP))


def _cant(valor):
    return float(Decimal(valor or 0).quantize(Decimal("0.01"), ROUND_HALF_UP))


def _pct(valor):
    if valor is None:
        return None
    return float(Decimal(valor).quantize(Decimal("0.1"), ROUND_HALF_UP))


def _fecha(valor):
    return valor.isoformat() if valor else None


def _limite(limite, por_omision):
    if limite is None:
        return por_omision
    return max(1, min(int(limite), LIMITE_MAXIMO))


def _recorta(clave, filas, limite):
    return {clave: filas[:limite], "total_filas": len(filas),
            "truncado": len(filas) > limite}


def _periodo(desde, hasta, nombre):
    return {"desde": desde.isoformat(), "hasta": hasta.isoformat(),
            "nombre": nombre, "hoy": localdate().isoformat()}


def _periodo_mes(anio, mes):
    desde, hasta = periodos.rango_mes(anio, mes)
    return {"anio": anio, "mes": mes, "desde": desde.isoformat(),
            "hasta": hasta.isoformat(), "hoy": localdate().isoformat()}


def _valida(valor, opciones, campo):
    if valor not in opciones:
        raise ErrorDeConsulta(
            f"«{campo}» debe ser uno de: {', '.join(opciones)}; llegó {valor!r}.")


def _ventas(desde, hasta):
    return list(Venta.objects.filter(fecha__gte=desde, fecha__lte=hasta)
                .con_costeo().order_by())


def _sin_reconocer(desde, hasta):
    """Ventas cobradas que la contabilidad todavía no reconoce, y por cuánto."""
    pendientes = list(Venta.objects.filter(
        fecha__gte=desde, fecha__lte=hasta, es_cortesia=False)
        .sin_costo_completo().con_costeo().order_by())
    return len(pendientes), sum((v.ingreso for v in pendientes), CERO)


def _aviso_sin_reconocer(n, monto, donde):
    return (f"{n} ventas del mes por {_mxn(monto):,.2f} MXN no aparecen en "
            f"{donde}: les falta costo completo porque faltan compras "
            "capturadas, y el sistema reconoce ingreso y costo juntos o "
            "ninguno. Lo cobrado está en ventas_del_periodo.")


# ── Ventas ───────────────────────────────────────────────────────────────────
def ventas_del_periodo(periodo=None, desde=None, hasta=None):
    d, h, nombre = periodos.resolver(periodo, desde, hasta)
    ventas = _ventas(d, h)
    comerciales = [v for v in ventas if not v.es_cortesia]

    total = sum((v.ingreso for v in comerciales), CERO)
    con_nota = {v.nota_id for v in comerciales if v.nota_id}
    tickets = len(con_nota) + sum(1 for v in comerciales if not v.nota_id)
    por_metodo = defaultdict(Decimal)
    por_dia = defaultdict(lambda: {"total": CERO, "unidades": 0})
    for v in comerciales:
        por_metodo[v.get_metodo_pago_display()] += v.ingreso
        por_dia[v.fecha]["total"] += v.ingreso
        por_dia[v.fecha]["unidades"] += v.cantidad
    estimadas = sum(1 for v in comerciales if not v.costo_esta_completo)

    pd, ph = periodos.anterior(d, h)
    previo = sum((v.ingreso for v in Venta.objects.comerciales()
                  .filter(fecha__gte=pd, fecha__lte=ph).order_by()), CERO)

    resultado = {
        "periodo": _periodo(d, h, nombre),
        "total_cobrado_mxn": _mxn(total),
        "tickets": tickets,
        "ticket_promedio_mxn": _mxn(total / tickets) if tickets else None,
        "unidades_cobradas": sum(v.cantidad for v in comerciales),
        "unidades_regaladas": sum(v.cantidad for v in ventas if v.es_cortesia),
        "descuentos_mxn": _mxn(sum((v.descuento_monto for v in comerciales), CERO)),
        "ganancia_bruta_mxn": _mxn(sum((v.ganancia for v in comerciales), CERO)),
        "por_metodo_pago": [
            {"metodo": m, "total_mxn": _mxn(t)}
            for m, t in sorted(por_metodo.items(), key=lambda x: -x[1])],
        "periodo_anterior": {
            "desde": pd.isoformat(), "hasta": ph.isoformat(),
            "total_cobrado_mxn": _mxn(previo),
            "variacion_pct": _pct((total - previo) * CIEN / previo) if previo else None,
        },
        "avisos": [],
    }
    if (h - d).days < 31:
        resultado["por_dia"] = [
            {"fecha": f.isoformat(), "total_mxn": _mxn(x["total"]),
             "unidades": x["unidades"]}
            for f, x in sorted(por_dia.items())]
    if not ventas:
        resultado["avisos"].append(
            f"No hay ventas capturadas del {d.isoformat()} al {h.isoformat()}.")
    if estimadas:
        resultado["avisos"].append(
            f"{estimadas} de {len(comerciales)} ventas no tienen su costo "
            "completo (faltan compras capturadas): para ellas la ganancia "
            "bruta usa el costo del catálogo de hoy, así que es estimada.")
    return resultado


def productos_mas_vendidos(periodo=None, desde=None, hasta=None,
                           ordenar_por="unidades", limite=None):
    _valida(ordenar_por, ("unidades", "ingreso"), "ordenar_por")
    limite = _limite(limite, 10)
    d, h, nombre = periodos.resolver(periodo, desde, hasta)

    por_receta = {}
    for v in _ventas(d, h):
        f = por_receta.setdefault(v.receta_id, {
            "producto": v.receta.nombre, "unidades": 0, "cobradas": 0,
            "regaladas": 0, "ingreso": CERO})
        f["unidades"] += v.cantidad
        if v.es_cortesia:
            f["regaladas"] += v.cantidad
        else:
            f["cobradas"] += v.cantidad
            f["ingreso"] += v.ingreso
    total_ingreso = sum((f["ingreso"] for f in por_receta.values()), CERO)

    clave = "unidades" if ordenar_por == "unidades" else "ingreso"
    filas = sorted(por_receta.values(), key=lambda f: (-f[clave], f["producto"]))
    filas = [{
        "producto": f["producto"],
        "unidades": f["unidades"],
        "cobradas": f["cobradas"],
        "regaladas": f["regaladas"],
        "ingreso_mxn": _mxn(f["ingreso"]),
        "participacion_ingreso_pct": (
            _pct(f["ingreso"] * CIEN / total_ingreso) if total_ingreso else None),
    } for f in filas]
    resultado = {"periodo": _periodo(d, h, nombre), "ordenado_por": ordenar_por,
                 **_recorta("productos", filas, limite), "avisos": []}
    if not filas:
        resultado["avisos"].append(
            f"No hay ventas capturadas del {d.isoformat()} al {h.isoformat()}.")
    return resultado


# ── Inventario y costos ──────────────────────────────────────────────────────
def insumos_por_surtir(solo_faltantes=True, limite=None):
    limite = _limite(limite, 20)
    filas = Ingrediente.stock_del_catalogo()
    ultimas = dict(Compra.objects.order_by().values("ingrediente_id")
                   .annotate(u=Max("fecha")).values_list("ingrediente_id", "u"))

    def cobertura(f):
        # Las unidades no se comparan entre insumos (g contra piezas): se
        # ordena por qué tanto del mínimo cubre cada uno.
        return f["stock"] / f["minimo"] if f["minimo"] > 0 else Decimal("Infinity")

    elegidas = [f for f in filas if f["falta"]] if solo_faltantes else list(filas)
    elegidas.sort(key=lambda f: (cobertura(f), f["nombre"]))
    salida = [{
        "insumo": f["nombre"],
        "categoria": f["categoria"],
        "unidad": f["unidad"],
        "stock": _cant(f["stock"]),
        "minimo_para_5_shakes": _cant(f["minimo"]),
        "faltante": _cant(f["faltante"]),
        "ultima_compra": _fecha(ultimas.get(f["pk"])),
        "stock_negativo": f["stock"] < 0,
    } for f in elegidas]

    resultado = {
        "fecha": localdate().isoformat(),
        "total_insumos": len(filas),
        "con_faltante": sum(1 for f in filas if f["falta"]),
        **_recorta("insumos", salida, limite),
        "avisos": [],
    }
    negativos = [f["nombre"] for f in filas if f["stock"] < 0]
    if negativos:
        resultado["avisos"].append(
            f"{len(negativos)} insumos tienen stock negativo "
            f"({', '.join(negativos[:5])}{'…' if len(negativos) > 5 else ''}): "
            "se ha vendido más de lo que suman sus compras capturadas, así que "
            "su stock real no se conoce hasta capturar las compras que faltan.")
    return resultado


def margen_por_receta(ordenar_por="margen", incluir_inactivas=False,
                      periodo=None, desde=None, hasta=None, limite=None):
    _valida(ordenar_por, ("margen", "ganancia", "nombre"), "ordenar_por")
    limite = _limite(limite, 20)
    unitarios = Ingrediente.costos_ultima_compra()
    recetas = Receta.objects.prefetch_related("ingredientes__ingrediente")
    if not incluir_inactivas:
        recetas = recetas.filter(activa=True)

    real, rango = None, None
    if periodo or desde or hasta:
        d, h, nombre = periodos.resolver(periodo, desde, hasta)
        rango = _periodo(d, h, nombre)
        real = {}
        for v in (Venta.objects.comerciales()
                  .filter(fecha__gte=d, fecha__lte=h).order_by()):
            r = real.setdefault(v.receta_id, {
                "ingreso": CERO, "ganancia": CERO, "unidades": 0,
                "estimado": False})
            r["ingreso"] += v.ingreso
            r["ganancia"] += v.ganancia
            r["unidades"] += v.cantidad
            r["estimado"] |= not v.costo_esta_completo

    filas = []
    for r in recetas:
        ultima = r.costo_ultima_compra(unitarios)
        fila = {
            "producto": r.nombre,
            "activa": r.activa,
            "precio_mxn": _mxn(r.precio_venta),
            "costo_catalogo_mxn": _mxn(r.costo_receta),
            "ganancia_unitaria_mxn": _mxn(r.ganancia_unitaria),
            "margen_catalogo_pct": _pct(r.margen * CIEN),
            "costo_ultima_compra_mxn": _mxn(ultima) if ultima is not None else None,
            "margen_ultima_compra_pct": (
                _pct((r.precio_venta - ultima) * CIEN / r.precio_venta)
                if ultima is not None and r.precio_venta else None),
            "_orden": (r.margen, r.ganancia_unitaria),
        }
        if real is not None:
            v = real.get(r.pk)
            fila["vendido_en_periodo"] = {
                "unidades": v["unidades"],
                "ingreso_mxn": _mxn(v["ingreso"]),
                "ganancia_mxn": _mxn(v["ganancia"]),
                "margen_real_pct": _pct(v["ganancia"] * CIEN / v["ingreso"]),
                "estimado": v["estimado"],
            } if v and v["ingreso"] else None
        filas.append(fila)

    if ordenar_por == "nombre":
        filas.sort(key=lambda f: f["producto"].casefold())
    else:
        i = 0 if ordenar_por == "margen" else 1
        filas.sort(key=lambda f: (-f["_orden"][i], f["producto"].casefold()))
    for f in filas:
        del f["_orden"]

    resultado = {"ordenado_por": ordenar_por,
                 **_recorta("recetas", filas, limite), "avisos": []}
    if rango:
        resultado["periodo"] = rango
    sin_ultima = sum(1 for f in filas if f["costo_ultima_compra_mxn"] is None)
    if sin_ultima:
        resultado["avisos"].append(
            f"{sin_ultima} recetas no tienen costo a última compra: a algún "
            "insumo suyo le falta su primera compra capturada.")
    if real is not None and any(
            f["vendido_en_periodo"] and f["vendido_en_periodo"]["estimado"]
            for f in filas):
        resultado["avisos"].append(
            "Los márgenes reales marcados «estimado» incluyen ventas sin costo "
            "completo, valuadas con el catálogo de hoy.")
    return resultado


def alertas_de_margen():
    r = alarmas_margen()
    alertas = [{
        "producto": a["nombre"],
        "margen_anterior_pct": _pct(a["margen_anterior"]),
        "margen_actual_pct": _pct(a["margen_actual"]),
        "caida_pct": _pct(a["caida"]),
        "unidades_mes_anterior": a["unidades_anterior"],
        "unidades_mes_actual": a["unidades_actual"],
        "estimado": a["estimado"],
    } for a in r["avisos"]]
    resultado = {
        "mes_actual": r["mes_actual"].strftime("%Y-%m"),
        "mes_anterior": r["mes_anterior"].strftime("%Y-%m"),
        "umbral_caida_pct": _pct(r["umbral"]),
        "alertas": alertas,
        "avisos": [],
    }
    if not alertas:
        resultado["avisos"].append(
            "Ningún producto vendido en los dos meses bajó su margen más que "
            "el umbral. Un producto que no se vendió en alguno de los dos no "
            "se compara.")
    if r["hay_estimados"]:
        resultado["avisos"].append(
            "Las alertas «estimado» se apoyan en ventas sin costo completo, "
            "valuadas con el catálogo de hoy.")
    if any(a["unidades_mes_actual"] < POCAS_UNIDADES for a in alertas):
        resultado["avisos"].append(
            "Algunas alertas comparan pocas unidades del mes en curso contra "
            "un mes completo: una sola venta atípica puede encenderlas.")
    return resultado


def compras_recientes(periodo=None, desde=None, hasta=None, insumo=None,
                      limite=None):
    limite = _limite(limite, 20)
    d, h, nombre = periodos.resolver(periodo, desde, hasta,
                                     por_omision="ultimos_30_dias")
    qs = (Compra.objects.filter(fecha__gte=d, fecha__lte=h)
          .select_related("ingrediente").order_by("-fecha", "-id"))
    if insumo and insumo.strip():
        qs = qs.filter(ingrediente__nombre__icontains=insumo.strip())
    compras = list(qs)
    filas = [{
        "fecha": c.fecha.isoformat(),
        "insumo": c.ingrediente.nombre,
        "cantidad": _cant(c.cantidad),
        "unidad_compra": c.ingrediente.unidad_compra,
        "monto_mxn": _mxn(c.monto_total),
        "costo_por_unidad_mxn": _mxn(c.costo_unitario),
        "proveedor": c.proveedor or None,
    } for c in compras]
    resultado = {
        "periodo": _periodo(d, h, nombre),
        "total_mxn": _mxn(sum((c.monto_total for c in compras), CERO)),
        **_recorta("compras", filas, limite),
        "avisos": [],
    }
    if not compras:
        filtro = f" de «{insumo.strip()}»" if insumo and insumo.strip() else ""
        resultado["avisos"].append(
            f"No hay compras{filtro} capturadas del {d.isoformat()} al "
            f"{h.isoformat()}.")
    return resultado


# ── Contabilidad y presupuesto ───────────────────────────────────────────────
def estado_de_resultados(anio=None, mes=None):
    anio, mes = periodos.mes(anio, mes)
    er = posting.estado_resultados(anio, mes)
    desde, hasta = periodos.rango_mes(anio, mes)

    gastos = []
    for g in er["gastos"]:
        desglose = [{"concepto": s["nombre"], "monto_mxn": _mxn(s["monto"])}
                    for s in g["subcuentas"]]
        desglose += [{"concepto": x["nombre"], "monto_mxn": _mxn(x["monto"])}
                     for x in g["detalles"]]
        gastos.append({"grupo": g["nombre"], "total_mxn": _mxn(g["total"]),
                       "desglose": desglose[:10]})

    resultado = {
        "periodo": _periodo_mes(anio, mes),
        "ingresos_mxn": _mxn(er["total_ingresos"]),
        "ingresos": [{"cuenta": i["nombre"], "monto_mxn": _mxn(i["monto"])}
                     for i in er["ingresos"]],
        "costo_de_ventas_mxn": _mxn(er["total_costo_ventas"]),
        "costo_comercial_mxn": _mxn(er["costo_comercial"]),
        "costo_cortesias_mxn": _mxn(er["costo_cortesias"]),
        "utilidad_bruta_mxn": _mxn(er["utilidad_bruta"]),
        "gastos_mxn": _mxn(er["total_gastos"]),
        "gastos": gastos,
        "utilidad_neta_mxn": _mxn(er["utilidad"]),
        "avisos": [],
    }
    n, monto = _sin_reconocer(desde, hasta)
    resultado["ventas_sin_reconocer"] = {"ventas": n, "cobrado_mxn": _mxn(monto)}
    if n:
        resultado["avisos"].append(
            _aviso_sin_reconocer(n, monto, "este estado de resultados"))
    if hasta >= localdate():
        resultado["avisos"].append("El mes todavía no termina.")
    return resultado


def flujo_de_caja(anio=None, mes=None):
    anio, mes = periodos.mes(anio, mes)
    f = posting.flujo_efectivo(anio, mes)
    desde, hasta = periodos.rango_mes(anio, mes)
    resultado = {
        "periodo": _periodo_mes(anio, mes),
        "saldo_inicial_mxn": _mxn(f["saldo_inicial"]),
        "entradas_mxn": _mxn(f["entradas"]),
        "salidas_mxn": _mxn(f["salidas"]),
        "neto_mxn": _mxn(f["neto"]),
        "saldo_final_mxn": _mxn(f["saldo_final"]),
        "movimientos": len(f["lineas"]),
        "avisos": [],
    }
    n, monto = _sin_reconocer(desde, hasta)
    if n:
        resultado["avisos"].append(
            _aviso_sin_reconocer(n, monto, "la caja contable"))
    return resultado


def presupuesto_contra_real(anio=None, mes=None):
    anio, mes = periodos.mes(anio, mes)
    det = comparativo.detalle_mes(anio, mes)
    v = det["ventas"]
    gastos = [{
        "categoria": g["categoria"],
        "meta_mxn": _mxn(g["meta"]),
        "real_mxn": _mxn(g["real"]),
        "diferencia_mxn": _mxn(g["diferencia"]),
        "cumplimiento_pct": _pct(g["pct"]),
    } for g in det["gastos"]]
    resultado = {
        "periodo": _periodo_mes(anio, mes),
        "ventas": {
            "meta_mxn": _mxn(v["meta"]),
            "real_mxn": _mxn(v["real"]),
            "diferencia_mxn": _mxn(v["diferencia"]),
            "cumplimiento_pct": _pct(v["pct"]),
        },
        "gastos": gastos,
        "gastos_meta_total_mxn": _mxn(det["tot_meta_gastos"]),
        "gastos_real_total_mxn": _mxn(det["tot_real_gastos"]),
        "avisos": [],
    }
    if not v["meta"] and not det["tot_meta_gastos"]:
        resultado["avisos"].append("No hay presupuesto capturado para este mes.")
    return resultado


# ── Lealtad ──────────────────────────────────────────────────────────────────
def _configuracion_lealtad():
    """La configuración guardada sin crearla: `ConfiguracionPrograma.get()`
    hace get_or_create, y aquí no se escribe."""
    return ConfiguracionPrograma.objects.filter(pk=1).first() or ConfiguracionPrograma()


def resumen_lealtad():
    cfg = _configuracion_lealtad()
    c = metricas.resumen_clientes(cfg)
    p = metricas.resumen_puntos()
    return {
        "fecha": localdate().isoformat(),
        "clientes": {
            "total": c["total"],
            "activos": c["activos"],
            "inactivos": c["inactivos"],
            "nuevos_ultimos_30_dias": c["nuevos"],
            "registrados_sin_comprar": c["sin_comprar"],
            "activos_pct": _pct(c["pct_activos"]),
            "gasto_promedio_mxn": _mxn(c["gasto_promedio"]),
            "visitas_promedio": round(float(c["visitas_promedio"]), 1),
            "dias_sin_comprar_para_inactivo": cfg.dias_inactividad,
        },
        "puntos": {
            "emitidos": p["emitidos"],
            "redimidos": p["redimidos"],
            "caducados": p["caducados"],
            "por_canjear": p["pasivo"],
            "tasa_redencion_pct": _pct(p["tasa_redencion"]),
        },
        "canjes": {
            "total": p["canjes"],
            "pendientes_de_entregar": p["canjes_pendientes"],
            "costo_entregado_mxn": _mxn(p["costo_entregado"]),
        },
        "costo_si_canjearan_todos_sus_puntos_mxn": _mxn(p["costo_pasivo"]),
        "avisos": [],
    }


def clientes_frecuentes(ordenar_por="gasto", limite=None):
    _valida(ordenar_por, ("gasto", "visitas", "puntos"), "ordenar_por")
    limite = _limite(limite, 10)
    orden = {"gasto": "-gasto_historico", "visitas": "-visitas",
             "puntos": "-puntos_saldo"}[ordenar_por]
    # `.only()` deja fuera teléfono, cumpleaños y notas desde la consulta.
    qs = (Cliente.objects.exclude(estado=Cliente.Estado.BAJA)
          .filter(visitas__gt=0)
          .only("nombre", "visitas", "gasto_historico", "puntos_saldo",
                "puntos_historicos", "ultima_compra")
          .order_by(orden, "id"))
    total = qs.count()
    niveles = list(Nivel.objects.filter(activo=True).order_by("puntos_requeridos"))
    hoy = localdate()

    def nivel_de(cliente):
        alcanzados = [n for n in niveles
                      if n.puntos_requeridos <= cliente.puntos_historicos]
        return alcanzados[-1].nombre if alcanzados else None

    filas = [{
        "nombre": c.nombre_corto,
        "visitas": c.visitas,
        "gasto_total_mxn": _mxn(c.gasto_historico),
        "ticket_promedio_mxn": _mxn(c.gasto_historico / c.visitas),
        "puntos_disponibles": c.puntos_saldo,
        "nivel": nivel_de(c),
        "ultima_compra": _fecha(c.ultima_compra),
        "dias_sin_comprar": (hoy - c.ultima_compra).days if c.ultima_compra else None,
    } for c in qs[:limite]]
    return {"ordenado_por": ordenar_por, "clientes": filas,
            "total_filas": total, "truncado": total > limite, "avisos": []}


# ── Salud de los datos ───────────────────────────────────────────────────────
def salud_de_los_datos():
    hoy = localdate()
    s = posting.salud_del_costeo(hoy.year, hoy.month)
    ventas = Venta.objects.aggregate(n=Count("id"), primera=Min("fecha"),
                                     ultima=Max("fecha"))
    compras = Compra.objects.aggregate(n=Count("id"), primera=Min("fecha"),
                                       ultima=Max("fecha"))
    sin_compra = list(
        Ingrediente.objects.filter(usos__receta__activa=True, compras__isnull=True)
        .distinct().order_by("nombre").values_list("nombre", flat=True))

    resultado = {
        "fecha": hoy.isoformat(),
        "ventas": {
            "total": ventas["n"],
            "primera": _fecha(ventas["primera"]),
            "ultima": _fecha(ventas["ultima"]),
            "sin_costear": s["sin_costear"],
            "con_costo_incompleto": s["incompletas"],
        },
        "compras": {
            "total": compras["n"],
            "primera": _fecha(compras["primera"]),
            "ultima": _fecha(compras["ultima"]),
        },
        "inventario_contable_mxn": _mxn(s["saldo_inventario"]),
        "consumo_sin_compra_que_lo_respalde": _cant(s["faltante_sin_capa"]),
        "insumos_de_recetas_activas_sin_ninguna_compra": {
            "total": len(sin_compra), "nombres": sin_compra[:20]},
        "avisos": [],
    }
    if s["incompletas"]:
        resultado["avisos"].append(
            f"{s['incompletas']} ventas tienen costo incompleto: consumieron "
            "insumos sin compra capturada. No entran al estado de resultados "
            "hasta capturar esas compras.")
    if s["sin_costear"]:
        resultado["avisos"].append(
            f"{s['sin_costear']} ventas no están costeadas. Hay que correr "
            "`recostear --solo-pendientes`.")
    if s["saldo_acreedor"]:
        resultado["avisos"].append(
            "El inventario contable está en negativo: es una falla del costeo, "
            "no un dato del negocio. Hay que avisar a quien mantiene el sistema.")
    return resultado
