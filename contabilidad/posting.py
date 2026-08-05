"""Reglas de generación automática de asientos (partida doble) y reportes.

Catálogo básico usado por las reglas automáticas:
  101  Caja y bancos          (Activo)
  105  IVA acreditable        (Activo)  -> IVA que pagamos en gastos
  201  IVA por pagar          (Pasivo)  -> IVA que cobramos en ventas
  301  Capital                (Capital)
  401  Ventas                 (Ingreso)
  501  Costo / Insumos        (Gasto)
  502  Renta                  (Gasto)
  503  Servicios              (Gasto)
  504  Mercadotecnia          (Gasto)
  505  Sueldos                (Gasto)
  509  Otros gastos           (Gasto)
"""
import calendar
from datetime import date
from decimal import Decimal
from django.db import transaction
from django.db.models import Sum
from .models import (
    Cuenta, Asiento, MovimientoContable,
    CategoriaGasto, Movimiento, IVA_TASA, redondea,
)


def _desglose_iva(total):
    """Separa un total con IVA incluido en (subtotal, iva) del 16%."""
    iva = redondea(total * IVA_TASA / (Decimal("1") + IVA_TASA))
    return total - iva, iva


# ── COSTEO FIFO (IAS 2): valúa el Costo de Ventas por capas ────────────────────
def _orden_recon(mov):
    """Clave de orden por reconocimiento (fecha de factura, luego fecha)."""
    return (mov.fecha_factura or mov.fecha, mov.pk)


def fifo_cogs():
    """Costo de Ventas por FIFO de cada venta facturada.

    Reproduce, en orden de reconocimiento, las compras facturadas como capas de
    inventario y las ventas facturadas consumiéndolas (la más antigua primero).
    Devuelve {venta_id: costo_de_ventas}.
    """
    from collections import defaultdict, deque
    from inventario.models import Ingrediente

    ing = {i.id: i for i in Ingrediente.objects.all()}
    capas = defaultdict(deque)   # ingrediente_id -> deque[[cantidad, costo_unit]]

    compras = Movimiento.objects.filter(
        tipo=Movimiento.Tipo.COMPRA, facturado=True).select_related(
        "compra__ingrediente")
    for m in sorted(compras, key=_orden_recon):
        c = m.compra
        if not c or not c.ingrediente.cantidad_por_unidad:
            continue
        cant_receta = c.cantidad * c.ingrediente.cantidad_por_unidad
        costo_unit = c.costo_unitario / c.ingrediente.cantidad_por_unidad
        capas[c.ingrediente_id].append([cant_receta, costo_unit])

    cogs = {}
    ventas = Movimiento.objects.filter(
        tipo=Movimiento.Tipo.VENTA, facturado=True).select_related("venta__receta")
    for m in sorted(ventas, key=_orden_recon):
        v = m.venta
        if not v:
            continue
        total = Decimal("0")
        for ing_id, cantidad in v.consumo_ingredientes().items():
            falta = cantidad
            dq = capas[ing_id]
            while falta > 0 and dq:
                capa = dq[0]
                toma = min(falta, capa[0])
                total += toma * capa[1]
                capa[0] -= toma
                falta -= toma
                if capa[0] <= 0:
                    dq.popleft()
            if falta > 0:   # sin capas suficientes: usa el costo actual del ingrediente
                obj = ing.get(ing_id)
                total += falta * (obj.costo_unidad_receta if obj else Decimal("0"))
        cogs[v.id] = redondea(total)
    return cogs

# Catálogo básico: codigo -> (nombre, tipo)
CATALOGO = [
    ("101", "Caja y bancos", Cuenta.Tipo.ACTIVO),
    ("105", "IVA acreditable", Cuenta.Tipo.ACTIVO),
    ("106", "Compras por facturar", Cuenta.Tipo.ACTIVO),
    ("115", "Inventario", Cuenta.Tipo.ACTIVO),
    ("116", "Gastos por comprobar", Cuenta.Tipo.ACTIVO),
    ("201", "IVA por pagar", Cuenta.Tipo.PASIVO),
    ("202", "Ventas por facturar", Cuenta.Tipo.PASIVO),
    ("301", "Capital", Cuenta.Tipo.CAPITAL),
    ("401", "Ventas", Cuenta.Tipo.INGRESO),
    ("501", "Costo de ventas", Cuenta.Tipo.GASTO),
    ("502", "Renta", Cuenta.Tipo.GASTO),
    ("503", "Servicios", Cuenta.Tipo.GASTO),
    ("504", "Mercadotecnia", Cuenta.Tipo.GASTO),
    ("505", "Sueldos", Cuenta.Tipo.GASTO),
    ("506", "Cortesías y promociones", Cuenta.Tipo.GASTO),
    ("509", "Otros gastos", Cuenta.Tipo.GASTO),
]

# Cuentas puente para el criterio de reconocimiento (IFRS):
CTA_CAJA = "101"
CTA_COMPRAS_POR_FACTURAR = "106"   # activo: compra pagada, gasto aún no reconocido
CTA_VENTAS_POR_FACTURAR = "202"    # pasivo: venta cobrada, ingreso aún no reconocido
CTA_VENTAS = "401"
CTA_COSTO_INSUMOS = "501"      # gasto: Costo de ventas (COGS) reconocido al vender
CTA_COSTO_VENTAS = "501"
CTA_INVENTARIO = "115"         # activo: mercancía comprada aún no vendida
CTA_GASTOS_POR_COMPROBAR = "116"   # activo: gasto pagado aún no reconocido
CTA_CORTESIAS = "506"          # gasto: costo de productos regalados (activaciones)
CTA_IVA_POR_PAGAR = "201"      # pasivo: IVA cobrado que se debe a Hacienda

# Cuenta de gasto por categoría (claves base; las nuevas usan CategoriaGasto).
GASTO_A_CUENTA = {
    "insumos": "501", "renta": "502", "servicios": "503",
    "mercadotecnia": "504", "sueldos": "505", "otro": "509",
}


def _codigo_cuenta_gasto(tipo):
    """Cuenta contable para una categoría de gasto.

    Prioridad: la cuenta configurada en la categoría → el mapeo base →
    'Otros gastos' (509) como respaldo para categorías nuevas.
    """
    cat = CategoriaGasto.objects.filter(clave=tipo).first()
    if (cat and cat.cuenta_codigo
            and Cuenta.objects.filter(codigo=cat.cuenta_codigo).exists()):
        return cat.cuenta_codigo
    return GASTO_A_CUENTA.get(tipo, "509")


def crear_catalogo():
    """Crea (idempotente) el catálogo de cuentas básico."""
    creadas = 0
    for codigo, nombre, tipo in CATALOGO:
        _, hecho = Cuenta.objects.update_or_create(
            codigo=codigo, defaults=dict(nombre=nombre, tipo=tipo)
        )
        creadas += 1 if hecho else 0
    return creadas


def _cuenta(codigo):
    return Cuenta.objects.get(codigo=codigo)


# ══════════════════════════════════════════════════════════════════════════════
#  PUENTE AUTOMÁTICO: ventas/compras de inventario → libro de movimientos y asientos
# ══════════════════════════════════════════════════════════════════════════════
_CATALOGO_POR_CODIGO = {c[0]: c for c in CATALOGO}


def _cuenta_segura(codigo):
    """Devuelve la cuenta, creándola desde el catálogo si aún no existe.

    Hace el puente robusto: registrar una venta/compra nunca falla por catálogo
    ausente (p. ej. en pruebas o antes de correr crear_catalogo).
    """
    cta = Cuenta.objects.filter(codigo=codigo).first()
    if cta:
        return cta
    _codigo, nombre, tipo = _CATALOGO_POR_CODIGO.get(
        codigo, (codigo, f"Cuenta {codigo}", Cuenta.Tipo.GASTO))
    return Cuenta.objects.create(codigo=codigo, nombre=nombre, tipo=tipo)


def _reemplaza_asiento(referencia, fecha, concepto, lineas):
    """Crea (idempotente por referencia) un asiento con sus líneas debe/haber."""
    Asiento.objects.filter(referencia=referencia, automatico=True).delete()
    asiento = Asiento.objects.create(
        fecha=fecha, concepto=concepto, referencia=referencia, automatico=True)
    for cuenta_codigo, debe, haber in lineas:
        MovimientoContable.objects.create(
            asiento=asiento, cuenta=_cuenta_segura(cuenta_codigo),
            debe=debe, haber=haber,
        )
    return asiento


@transaction.atomic
def sincronizar_movimiento(mov: Movimiento, cogs_map=None):
    """(Re)genera los asientos de un movimiento según su estado actual.

    • FLUJO (siempre): efectivo contra cuenta puente. Afecta balance, flujo y
      balanza, pero NO el estado de resultados.
    • RECONOCIMIENTO (solo si facturado, IAS 2 / IFRS):
        - Compra → entra a Inventario (activo).
        - Venta  → reconoce el ingreso (neto de IVA) Y el Costo de Ventas por
                   FIFO (Costo de ventas contra Inventario), en la fecha factura.
    """
    cero = Decimal("0")
    es_cortesia = mov.tipo == Movimiento.Tipo.VENTA and \
        mov.venta_id and mov.venta.es_cortesia
    ref_flujo = f"Mov #{mov.pk} flujo"
    if es_cortesia or (mov.tipo == Movimiento.Tipo.VENTA and not mov.monto):
        # Cortesía (o venta $0): no mueve efectivo, no hay asiento de flujo.
        Asiento.objects.filter(referencia=ref_flujo, automatico=True).delete()
        flujo = None
    elif mov.tipo == Movimiento.Tipo.VENTA:
        # DEBE Caja · HABER Ventas por facturar
        flujo = _reemplaza_asiento(
            ref_flujo, mov.fecha, f"Cobro venta: {mov.descripcion}",
            [(CTA_CAJA, mov.monto, cero),
             (CTA_VENTAS_POR_FACTURAR, cero, mov.monto)])
    elif mov.tipo == Movimiento.Tipo.COMPRA:
        # DEBE Compras por facturar · HABER Caja
        flujo = _reemplaza_asiento(
            ref_flujo, mov.fecha, f"Pago compra: {mov.descripcion}",
            [(CTA_COMPRAS_POR_FACTURAR, mov.monto, cero),
             (CTA_CAJA, cero, mov.monto)])
    else:  # GASTO operativo
        # DEBE Gastos por comprobar · HABER Caja
        flujo = _reemplaza_asiento(
            ref_flujo, mov.fecha, f"Pago gasto: {mov.descripcion}",
            [(CTA_GASTOS_POR_COMPROBAR, mov.monto, cero),
             (CTA_CAJA, cero, mov.monto)])

    ref_rec = f"Mov #{mov.pk} reconocimiento"
    reconocimiento = None
    if mov.facturado:
        fecha_rec = mov.fecha_factura or mov.fecha
        if mov.tipo == Movimiento.Tipo.VENTA:
            if cogs_map is None:
                cogs_map = fifo_cogs()
            costo = cogs_map.get(mov.venta_id, Decimal("0"))
            if es_cortesia:
                # Regalo: solo el costo del inventario → gasto de cortesías.
                if costo:
                    reconocimiento = _reemplaza_asiento(
                        ref_rec, fecha_rec, f"Cortesía: {mov.descripcion}",
                        [(CTA_CORTESIAS, costo, cero),
                         (CTA_INVENTARIO, cero, costo)])
                else:
                    Asiento.objects.filter(referencia=ref_rec, automatico=True).delete()
            else:
                # Ingreso: DEBE Ventas por facturar · HABER Ventas (subtotal) · HABER IVA
                subtotal, iva = _desglose_iva(mov.monto)
                lineas = [(CTA_VENTAS_POR_FACTURAR, mov.monto, cero),
                          (CTA_VENTAS, cero, subtotal),
                          (CTA_IVA_POR_PAGAR, cero, iva)]
                # Costo de Ventas (FIFO): DEBE Costo de ventas · HABER Inventario
                if costo:
                    lineas += [(CTA_COSTO_VENTAS, costo, cero),
                               (CTA_INVENTARIO, cero, costo)]
                reconocimiento = _reemplaza_asiento(
                    ref_rec, fecha_rec, f"Reconoce venta: {mov.descripcion}", lineas)
        elif mov.tipo == Movimiento.Tipo.COMPRA:
            # Compra → Inventario: DEBE Inventario · HABER Compras por facturar
            reconocimiento = _reemplaza_asiento(
                ref_rec, fecha_rec, f"Compra a inventario: {mov.descripcion}",
                [(CTA_INVENTARIO, mov.monto, cero),
                 (CTA_COMPRAS_POR_FACTURAR, cero, mov.monto)])
        else:  # GASTO → se reconoce como gasto del periodo
            reconocimiento = _reemplaza_asiento(
                ref_rec, fecha_rec, f"Reconoce gasto: {mov.descripcion}",
                [(mov.cuenta.codigo, mov.monto, cero),
                 (CTA_GASTOS_POR_COMPROBAR, cero, mov.monto)])
    else:
        Asiento.objects.filter(referencia=ref_rec, automatico=True).delete()

    Movimiento.objects.filter(pk=mov.pk).update(
        asiento_flujo=flujo, asiento_reconocimiento=reconocimiento)


def resincronizar_cogs():
    """Recalcula el FIFO y regenera el reconocimiento de TODAS las ventas facturadas.

    Necesario porque al facturar una compra/venta cambian las capas y, por lo
    tanto, el Costo de Ventas de otras ventas.
    """
    cogs_map = fifo_cogs()
    ventas = Movimiento.objects.filter(
        tipo=Movimiento.Tipo.VENTA, facturado=True)
    for mov in ventas:
        sincronizar_movimiento(mov, cogs_map=cogs_map)


@transaction.atomic
def sincronizar_venta(venta):
    """Crea o actualiza el movimiento de una venta (idempotente por venta)."""
    monto = venta.ingreso
    descripcion = f"{venta.receta} ×{venta.cantidad}"
    mov, _ = Movimiento.objects.get_or_create(
        venta=venta,
        defaults=dict(
            fecha=venta.fecha, tipo=Movimiento.Tipo.VENTA,
            descripcion=descripcion, monto=monto,
            cuenta=_cuenta_segura(CTA_VENTAS),
        ),
    )
    # Mantén sincronizados fecha/monto/descripcion (p. ej. si se agregan extras).
    Movimiento.objects.filter(pk=mov.pk).update(
        fecha=venta.fecha, monto=monto, descripcion=descripcion)
    mov.refresh_from_db()
    sincronizar_movimiento(mov)
    return mov


@transaction.atomic
def sincronizar_compra(compra):
    """Crea o actualiza el movimiento de una compra (idempotente por compra)."""
    monto = compra.total
    descripcion = f"{compra.ingrediente} ×{compra.cantidad}"
    mov, _ = Movimiento.objects.get_or_create(
        compra=compra,
        defaults=dict(
            fecha=compra.fecha, tipo=Movimiento.Tipo.COMPRA,
            descripcion=descripcion, monto=monto,
            cuenta=_cuenta_segura(CTA_COSTO_INSUMOS),
        ),
    )
    Movimiento.objects.filter(pk=mov.pk).update(
        fecha=compra.fecha, monto=monto, descripcion=descripcion)
    mov.refresh_from_db()
    sincronizar_movimiento(mov)
    return mov


@transaction.atomic
def registrar_gasto(fecha, categoria, monto, descripcion=""):
    """Crea un movimiento de gasto operativo (sueldos, renta, marketing…).

    Se registra en el libro con su categoría y cuenta de gasto; el asiento de
    reconocimiento (que lo lleva al Estado de Resultados) se genera al facturar.
    """
    cuenta = _cuenta_segura(_codigo_cuenta_gasto(categoria))
    mov = Movimiento.objects.create(
        fecha=fecha, tipo=Movimiento.Tipo.GASTO, categoria=categoria,
        descripcion=descripcion or categoria, monto=monto, cuenta=cuenta,
    )
    sincronizar_movimiento(mov)
    return mov


def marcar_facturado(mov: Movimiento, facturado: bool, fecha_factura=None):
    """Cambia el estado de facturación y regenera el reconocimiento.

    Como el FIFO depende de qué compras/ventas están facturadas, al cambiar el
    estado se recalcula el Costo de Ventas de todas las ventas facturadas.
    """
    mov.facturado = facturado
    if facturado:
        mov.fecha_factura = fecha_factura or mov.fecha_factura or mov.fecha
    mov.save(update_fields=["facturado", "fecha_factura"])
    sincronizar_movimiento(mov)
    resincronizar_cogs()
    return mov


# ══════════════════════════════════════════════════════════════════════════════
#  REPORTES MENSUALES
# ══════════════════════════════════════════════════════════════════════════════
def _rango_mes(anio, mes):
    """(inicio, fin) del mes; (None, None) si no se especifica periodo."""
    if not anio or not mes:
        return None, None
    ini = date(anio, mes, 1)
    fin = date(anio, mes, calendar.monthrange(anio, mes)[1])
    return ini, fin


def _agg(desde=None, hasta=None):
    """{cuenta_id: (debe, haber)} sumando líneas por rango de fecha del asiento."""
    qs = MovimientoContable.objects.all()
    if desde:
        qs = qs.filter(asiento__fecha__gte=desde)
    if hasta:
        qs = qs.filter(asiento__fecha__lte=hasta)
    filas = qs.values("cuenta").annotate(d=Sum("debe"), h=Sum("haber"))
    return {f["cuenta"]: (f["d"] or Decimal("0"), f["h"] or Decimal("0"))
            for f in filas}


def _saldo(cuenta, debe, haber):
    return (debe - haber) if cuenta.es_deudora else (haber - debe)


def periodos_disponibles():
    """Lista de (anio, mes) con actividad contable, más reciente primero."""
    vistos = set()
    for f in Asiento.objects.values_list("fecha", flat=True):
        vistos.add((f.year, f.month))
    return sorted(vistos, reverse=True)


def balanza_comprobacion(anio=None, mes=None):
    """Balanza de comprobación acumulada al cierre del mes (débitos = créditos)."""
    _, fin = _rango_mes(anio, mes)
    agg = _agg(hasta=fin)
    cuentas = {c.id: c for c in Cuenta.objects.all()}
    filas = []
    tot_debe = tot_haber = Decimal("0")
    for cid, (d, h) in sorted(agg.items(), key=lambda kv: cuentas[kv[0]].codigo):
        if d == 0 and h == 0:
            continue
        c = cuentas[cid]
        tot_debe += d
        tot_haber += h
        filas.append({
            "codigo": c.codigo, "nombre": c.nombre, "tipo": c.get_tipo_display(),
            "debe": d, "haber": h, "saldo": _saldo(c, d, h),
            "es_deudora": c.es_deudora,
        })
    return {"filas": filas, "total_debe": tot_debe, "total_haber": tot_haber,
            "cuadra": tot_debe == tot_haber}


def estado_resultados(anio=None, mes=None):
    """Ingresos − Costo de Ventas = Utilidad bruta; − Gastos = Utilidad neta.

    Solo considera movimientos reconocidos (facturados) EN el mes.
    """
    desde, hasta = _rango_mes(anio, mes)
    agg = _agg(desde=desde, hasta=hasta)
    cuentas = {c.id: c for c in Cuenta.objects.all()}
    ingresos, costo_ventas, gastos = [], [], []
    tot_ing = tot_cv = tot_gas = Decimal("0")
    for cid, (d, h) in agg.items():
        c = cuentas[cid]
        saldo = _saldo(c, d, h)
        if not saldo:
            continue
        if c.tipo == Cuenta.Tipo.INGRESO:
            ingresos.append({"nombre": c.nombre, "monto": saldo}); tot_ing += saldo
        elif c.tipo == Cuenta.Tipo.GASTO:
            if c.codigo == CTA_COSTO_VENTAS:
                costo_ventas.append({"nombre": c.nombre, "monto": saldo}); tot_cv += saldo
            else:
                gastos.append({"nombre": c.nombre, "monto": saldo}); tot_gas += saldo
    utilidad_bruta = tot_ing - tot_cv
    return {"ingresos": ingresos, "costo_ventas": costo_ventas, "gastos": gastos,
            "total_ingresos": tot_ing, "total_costo_ventas": tot_cv,
            "utilidad_bruta": utilidad_bruta,
            "total_gastos": tot_gas, "utilidad": utilidad_bruta - tot_gas}


def _resultado_desde_agg(agg, cuentas):
    """Utilidad (ingresos − gastos) a partir de un agregado ya calculado."""
    total = Decimal("0")
    for cid, (d, h) in agg.items():
        c = cuentas[cid]
        if c.tipo == Cuenta.Tipo.INGRESO:
            total += _saldo(c, d, h)
        elif c.tipo == Cuenta.Tipo.GASTO:
            total -= _saldo(c, d, h)
    return total


def _resultado_acumulado(hasta):
    """Utilidad acumulada (ingresos − gastos) hasta la fecha dada."""
    cuentas = {c.id: c for c in Cuenta.objects.all()}
    return _resultado_desde_agg(_agg(hasta=hasta), cuentas)


def balance_general(anio=None, mes=None):
    """Estado de situación financiera acumulado al cierre del mes."""
    _, fin = _rango_mes(anio, mes)
    agg = _agg(hasta=fin)
    cuentas = {c.id: c for c in Cuenta.objects.all()}
    activos, pasivos, capital = [], [], []
    ta = tp = tc = Decimal("0")
    for cid, (d, h) in sorted(agg.items(), key=lambda kv: cuentas[kv[0]].codigo):
        c = cuentas[cid]
        saldo = _saldo(c, d, h)
        if not saldo:
            continue
        if c.tipo == Cuenta.Tipo.ACTIVO:
            activos.append({"nombre": c.nombre, "monto": saldo}); ta += saldo
        elif c.tipo == Cuenta.Tipo.PASIVO:
            pasivos.append({"nombre": c.nombre, "monto": saldo}); tp += saldo
        elif c.tipo == Cuenta.Tipo.CAPITAL:
            capital.append({"nombre": c.nombre, "monto": saldo}); tc += saldo
    # Reutiliza el agregado ya calculado (evita re-consultar la BD).
    utilidad = _resultado_desde_agg(agg, cuentas)
    capital.append({"nombre": "Resultado del ejercicio", "monto": utilidad})
    tc += utilidad
    return {"activos": activos, "pasivos": pasivos, "capital": capital,
            "total_activo": ta, "total_pasivo": tp, "total_capital": tc,
            "total_pasivo_capital": tp + tc, "cuadra": ta == (tp + tc)}


def flujo_efectivo(anio=None, mes=None):
    """Flujo de efectivo del mes: entradas, salidas y saldo de caja."""
    desde, hasta = _rango_mes(anio, mes)
    caja = Cuenta.objects.filter(codigo=CTA_CAJA).first()
    if not caja:
        return {"entradas": Decimal("0"), "salidas": Decimal("0"),
                "neto": Decimal("0"), "saldo_inicial": Decimal("0"),
                "saldo_final": Decimal("0"), "lineas": []}

    per = MovimientoContable.objects.filter(cuenta=caja)
    if desde:
        per = per.filter(asiento__fecha__gte=desde)
    if hasta:
        per = per.filter(asiento__fecha__lte=hasta)
    entradas = per.aggregate(t=Sum("debe"))["t"] or Decimal("0")
    salidas = per.aggregate(t=Sum("haber"))["t"] or Decimal("0")

    # Saldo inicial = caja acumulada hasta el día anterior al mes.
    saldo_inicial = Decimal("0")
    if desde:
        prev = MovimientoContable.objects.filter(
            cuenta=caja, asiento__fecha__lt=desde)
        saldo_inicial = ((prev.aggregate(t=Sum("debe"))["t"] or Decimal("0"))
                         - (prev.aggregate(t=Sum("haber"))["t"] or Decimal("0")))

    lineas = []
    for m in per.select_related("asiento").order_by("asiento__fecha", "id"):
        monto = m.debe if m.debe else -m.haber
        lineas.append({"fecha": m.asiento.fecha, "concepto": m.asiento.concepto,
                       "monto": monto})
    return {"entradas": entradas, "salidas": salidas,
            "neto": entradas - salidas,
            "saldo_inicial": saldo_inicial,
            "saldo_final": saldo_inicial + entradas - salidas,
            "lineas": lineas}
