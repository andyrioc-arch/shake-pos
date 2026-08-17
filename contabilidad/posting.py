"""Reglas de generación automática de asientos (partida doble) y reportes.

El plan de cuentas que usan las reglas automáticas está en `CATALOGO`, unas
líneas abajo.

El IVA no se lleva en la contabilidad: los importes se registran completos, tal
como entran y salen de caja. El único lugar donde se desglosa es la nota que se
le entrega al cliente (ver `inventario.models`).
"""
import calendar
from datetime import date
from decimal import Decimal
from django.db import transaction
from django.db.models import Sum
from .models import (
    Cuenta, Asiento, MovimientoContable,
    CategoriaGasto, Movimiento,
)


# Catálogo básico: codigo -> (nombre, tipo)
CATALOGO = [
    ("101", "Caja y bancos", Cuenta.Tipo.ACTIVO),
    ("106", "Compras por facturar", Cuenta.Tipo.ACTIVO),
    ("115", "Inventario", Cuenta.Tipo.ACTIVO),
    ("116", "Gastos por comprobar", Cuenta.Tipo.ACTIVO),
    ("202", "Ventas por facturar", Cuenta.Tipo.PASIVO),
    ("301", "Capital", Cuenta.Tipo.CAPITAL),
    ("401", "Ventas", Cuenta.Tipo.INGRESO),
    ("501", "Costo de ventas", Cuenta.Tipo.GASTO),
    ("502", "Renta", Cuenta.Tipo.GASTO),
    ("503", "Servicios", Cuenta.Tipo.GASTO),
    ("504", "Mercadotecnia", Cuenta.Tipo.GASTO),
    ("505", "Sueldos", Cuenta.Tipo.GASTO),
    ("506", "Cortesías y promociones", Cuenta.Tipo.GASTO),
    ("507", "Merma de inventario", Cuenta.Tipo.GASTO),
    ("509", "Otros gastos", Cuenta.Tipo.GASTO),
]

# Agrupación en los REPORTES (hija → padre). No cambia dónde se postea: los
# asientos de cortesía siguen yendo a la 506; el reporte la lee dentro de 504.
JERARQUIA = {"506": "504"}

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
CTA_MERMA = "507"              # gasto: inventario perdido (caducado, tirado, derramado)

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
    """Crea (idempotente) el catálogo de cuentas básico y su jerarquía."""
    creadas = 0
    for codigo, nombre, tipo in CATALOGO:
        _, hecho = Cuenta.objects.update_or_create(
            codigo=codigo, defaults=dict(nombre=nombre, tipo=tipo)
        )
        creadas += 1 if hecho else 0

    # La jerarquía se repone aquí además de en la migración: `_cuenta_segura()`
    # puede recrear una cuenta borrada, y sin esto renacería suelta y
    # desaparecería de su grupo en el reporte sin que nadie se entere.
    por_codigo = {c.codigo: c for c in
                  Cuenta.objects.filter(codigo__in=[*JERARQUIA, *JERARQUIA.values()])}
    for hija_cod, padre_cod in JERARQUIA.items():
        hija, padre = por_codigo.get(hija_cod), por_codigo.get(padre_cod)
        if hija and padre and hija.padre_id != padre.id:
            hija.padre = padre
            hija.save(update_fields=["padre"])
    return creadas


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
def sincronizar_movimiento(mov: Movimiento):
    """(Re)genera los asientos de un movimiento según su estado actual.

    • FLUJO (siempre): efectivo contra cuenta puente. Afecta balance, flujo y
      balanza, pero NO el estado de resultados.
    • RECONOCIMIENTO (automático, IAS 2 / IFRS), en la fecha del movimiento:
        - Compra y gasto → siempre.
        - Venta  → solo si su costo está completo. Ingreso y costo entran
                   juntos o no entran: reconocer el ingreso con costo parcial
                   infla la utilidad bruta en silencio.
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
    fecha_rec = mov.fecha
    if mov.tipo == Movimiento.Tipo.VENTA:
        # El costo es un hecho guardado por inventario.costeo, no algo que se
        # recalcule aquí. Se difiere el reconocimiento ENTERO —ingreso
        # incluido— mientras el costo no esté completo (invariante I2).
        completo = bool(mov.venta_id) and mov.venta.costo_esta_completo
        costo = mov.venta.costo_fifo if completo else None
        if not completo:
            Asiento.objects.filter(referencia=ref_rec, automatico=True).delete()
        elif es_cortesia:
            # Regalo: solo el costo del inventario → gasto de cortesías.
            if costo:
                reconocimiento = _reemplaza_asiento(
                    ref_rec, fecha_rec, f"Cortesía: {mov.descripcion}",
                    [(CTA_CORTESIAS, costo, cero),
                     (CTA_INVENTARIO, cero, costo)])
            else:
                Asiento.objects.filter(referencia=ref_rec, automatico=True).delete()
        else:
            # Ingreso: DEBE Ventas por facturar · HABER Ventas (total)
            lineas = [(CTA_VENTAS_POR_FACTURAR, mov.monto, cero),
                      (CTA_VENTAS, cero, mov.monto)]
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

    Movimiento.objects.filter(pk=mov.pk).update(
        asiento_flujo=flujo, asiento_reconocimiento=reconocimiento)


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
def sincronizar_ajuste(ajuste):
    """El asiento de una merma: sale del inventario y se vuelve gasto.

    DEBE 507 Merma · HABER 115 Inventario. Sin asiento de flujo, como la
    cortesía: perder mercancía no mueve efectivo.

    No pasa por `Movimiento` a propósito. Ese modelo es el libro de lo que se
    cobró y se pagó —tiene `venta`, `compra` y una cuenta puente por cada uno—
    y una merma no es ninguna de las tres: no hubo contraparte ni dinero. Se
    postea directo, que es lo que ya hace el reconocimiento de la cortesía.
    """
    from inventario.models import AjusteInventario   # noqa: F401 (claridad)

    referencia = f"Merma #{ajuste.pk}"
    # Un sobrante no postea nada: dar de alta inventario que nadie compró
    # obligaría a inventarle un precio. Y un costo incompleto tampoco, por la
    # misma regla que difiere las ventas: no se reconoce lo que no se sabe.
    if not ajuste.es_merma or ajuste.costo_incompleto or not ajuste.costo:
        Asiento.objects.filter(referencia=referencia, automatico=True).delete()
        return None

    cero = Decimal("0")
    return _reemplaza_asiento(
        referencia, ajuste.fecha,
        f"Merma: {ajuste.ingrediente} × {ajuste.merma:,.2f} "
        f"{ajuste.ingrediente.unidad_receta}",
        [(CTA_MERMA, ajuste.costo, cero),
         (CTA_INVENTARIO, cero, ajuste.costo)])


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

    Se registra en el libro con su categoría y cuenta de gasto, y entra al
    Estado de Resultados en el acto: un gasto pagado es un gasto del periodo.
    """
    cuenta = _cuenta_segura(_codigo_cuenta_gasto(categoria))
    mov = Movimiento.objects.create(
        fecha=fecha, tipo=Movimiento.Tipo.GASTO, categoria=categoria,
        descripcion=descripcion or categoria, monto=monto, cuenta=cuenta,
    )
    sincronizar_movimiento(mov)
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


def salud_del_costeo(anio=None, mes=None):
    """Los síntomas de que el costeo se rompió, más el saldo de la 115.

    Quien sabe medir el costeo es el costeo: `inventario.costeo.diagnostico()`
    ya cuenta estas cifras y su docstring dice que la usan el comando y el
    panel. Reimplementarlas aquí dejaría dos definiciones de «sano» que hay
    que mantener iguales a mano, y la pantalla acabaría diciendo verde
    mientras `recostear --verificar` dice otra cosa.

    Ninguno de estos números se nota mirando los reportes: la balanza cuadra
    igual con ventas sin costear, y el balance se declara correcto con el
    inventario en negativo, porque solo verifica que las sumas coincidan y no
    que los signos tengan sentido.
    """
    from inventario import costeo

    _, fin = _rango_mes(anio, mes)
    diag = costeo.diagnostico()

    # El saldo de la 115 sí es cosa de contabilidad, y se pide directo en vez
    # de agrupar toda la tabla de movimientos para leer una sola cuenta.
    agg = (MovimientoContable.objects
           .filter(cuenta__codigo=CTA_INVENTARIO, asiento__fecha__lte=fin)
           .aggregate(d=Sum("debe"), h=Sum("haber")))
    saldo = (agg["d"] or Decimal("0")) - (agg["h"] or Decimal("0"))

    return {
        "sin_costear": diag["sin_costear"],
        "incompletas": diag["incompletas"],
        # Capas que nacieron sin saldo: el FIFO no las ve y quedarían
        # invisibles para siempre si nadie las reabre.
        "capas_sin_abrir": diag["capas_sin_abrir"],
        # Consumo que ninguna compra respaldó. Es la medida de cuánto del
        # costo se está dejando de reconocer.
        "faltante_sin_capa": diag["faltante_sin_capa"],
        # Se publica lo que sobra del lado acreedor, no el saldo: un inventario
        # sano da cero aquí, y cualquier otra cosa es el bug.
        "saldo_acreedor": -saldo if saldo < 0 else Decimal("0"),
        "saldo_inventario": saldo,
    }


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

    Solo considera lo reconocido EN el mes. Una venta cuyo costo aún no está
    completo no aparece aquí: ni su ingreso ni su costo.
    """
    desde, hasta = _rango_mes(anio, mes)
    agg = _agg(desde=desde, hasta=hasta)
    cuentas = {c.id: c for c in Cuenta.objects.select_related("padre")}
    ingresos, costo_ventas = [], []
    saldos_gasto = {}                       # cuenta_id -> saldo, para agrupar
    tot_ing = tot_cv = tot_gas = Decimal("0")
    for cid, (d, h) in agg.items():
        c = cuentas[cid]
        saldo = _saldo(c, d, h)
        if not saldo:
            continue
        if c.tipo == Cuenta.Tipo.INGRESO:
            ingresos.append({"nombre": c.nombre, "monto": saldo}); tot_ing += saldo
        elif c.tipo == Cuenta.Tipo.GASTO:
            if _es_costo_de_ventas(c):
                costo_ventas.append({"nombre": c.nombre, "monto": saldo}); tot_cv += saldo
            else:
                saldos_gasto[cid] = saldo
                tot_gas += saldo

    gastos = _agrupa_gastos(saldos_gasto, cuentas)
    ingresos.sort(key=lambda x: x["nombre"])
    costo_ventas.sort(key=lambda x: x["nombre"])
    utilidad_bruta = tot_ing - tot_cv
    return {"ingresos": ingresos, "costo_ventas": costo_ventas, "gastos": gastos,
            "total_ingresos": tot_ing, "total_costo_ventas": tot_cv,
            "utilidad_bruta": utilidad_bruta,
            "total_gastos": tot_gas, "utilidad": utilidad_bruta - tot_gas}


def _es_costo_de_ventas(cuenta):
    """¿Es la 501 o cuelga de ella?

    Generalizado a la descendencia para que agregar mañana una subcuenta de
    Costo de ventas no la mande sin aviso al bloque de gastos operativos, que
    la sacaría de la utilidad bruta.
    """
    actual = cuenta
    while actual is not None:
        if actual.codigo == CTA_COSTO_VENTAS:
            return True
        actual = actual.padre
    return False


def _agrupa_gastos(saldos, cuentas):
    """Gastos anidados por cuenta padre, ordenados por código.

    Una cuenta padre puede tener saldo PROPIO y además hijas: la 504 recibe
    posteos directos vía `registrar_gasto` y encima agrupa a la 506. Por eso el
    grupo lleva `propio` aparte de `subcuentas`, y el total es la suma de ambos.

    El orden es explícito: antes se iteraba el agregado tal cual y las filas
    salían en un orden que dependía del motor.
    """
    grupos = {}                              # cuenta_id del padre -> grupo
    for cid, saldo in saldos.items():
        c = cuentas[cid]
        cabeza = c.padre if c.padre_id else c
        g = grupos.setdefault(cabeza.id, {
            "nombre": cabeza.nombre, "codigo": cabeza.codigo,
            "propio": Decimal("0"), "subcuentas": [], "total": Decimal("0"),
        })
        if c.id == cabeza.id:
            g["propio"] += saldo
        else:
            g["subcuentas"].append({"nombre": c.nombre, "codigo": c.codigo,
                                    "monto": saldo})
        g["total"] += saldo

    for g in grupos.values():
        g["subcuentas"].sort(key=lambda x: x["codigo"])
    return sorted(grupos.values(), key=lambda g: g["codigo"])


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
