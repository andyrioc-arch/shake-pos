"""Los números del programa: clientes, puntos, marketing y retorno.

Todo se calcula en vivo desde los movimientos y las ventas reales del módulo
`inventario`, así que nunca hay cifras que se queden viejas.
"""

from collections import defaultdict
from datetime import timedelta
from decimal import Decimal

from django.db.models import Avg, Count, Max, Min, Q, Sum
from django.utils import timezone

from inventario.models import Venta
from .models import (
    Canje, Cliente, Compra, ConfiguracionPrograma, Mensaje, MovimientoPuntos,
    Premio,
)

CERO = Decimal("0")


def _pct(parte, total):
    if not total:
        return None
    return float(parte) * 100 / float(total)


# ══════════════════════════════════════════════════════════════════════════════
#  CLIENTES
# ══════════════════════════════════════════════════════════════════════════════

def resumen_clientes(cfg=None):
    cfg = cfg or ConfiguracionPrograma.get()
    hoy = timezone.localdate()
    limite = hoy - timedelta(days=cfg.dias_inactividad)

    qs = Cliente.objects.exclude(estado=Cliente.Estado.BAJA)
    total = qs.count()
    activos = qs.filter(ultima_compra__gte=limite).count()
    nuevos = qs.filter(alta__gte=timezone.now() - timedelta(days=30)).count()
    agg = qs.aggregate(gasto_prom=Avg("gasto_historico"),
                       visitas_prom=Avg("visitas"),
                       compradores=Count("id", filter=Q(visitas__gt=0)))

    return {
        "total": total,
        "activos": activos,
        "inactivos": total - activos,
        "nuevos": nuevos,
        "sin_comprar": total - (agg["compradores"] or 0),
        "gasto_promedio": agg["gasto_prom"] or CERO,
        "visitas_promedio": agg["visitas_prom"] or 0,
        "pct_activos": _pct(activos, total),
    }


def top_clientes(limite=10):
    return (Cliente.objects.exclude(estado=Cliente.Estado.BAJA)
            .filter(visitas__gt=0)
            .order_by("-gasto_historico")[:limite])


def cerca_de_premio(umbral=80, limite=15):
    """Clientes que van al X% o más de su siguiente premio."""
    premios = list(Premio.objects.filter(activo=True).order_by("puntos_requeridos"))
    if not premios:
        return []
    tope = premios[-1].puntos_requeridos
    cerca = []
    for cliente in (Cliente.objects
                    .filter(puntos_saldo__gt=0, puntos_saldo__lt=tope)
                    .exclude(estado=Cliente.Estado.BAJA)):
        premio = next((p for p in premios
                       if p.puntos_requeridos > cliente.puntos_saldo), None)
        if not premio:
            continue
        avance = cliente.puntos_saldo * 100 / premio.puntos_requeridos
        if avance >= umbral:
            cerca.append({
                "cliente": cliente, "premio": premio, "avance": avance,
                "faltan": premio.puntos_requeridos - cliente.puntos_saldo,
            })
    cerca.sort(key=lambda c: -c["avance"])
    return cerca[:limite]


# ══════════════════════════════════════════════════════════════════════════════
#  PUNTOS
# ══════════════════════════════════════════════════════════════════════════════

def resumen_puntos():
    agg = MovimientoPuntos.objects.aggregate(
        emitidos=Sum("puntos", filter=Q(tipo=MovimientoPuntos.Tipo.GANA)),
        redimidos=Sum("puntos", filter=Q(tipo=MovimientoPuntos.Tipo.CANJE)),
        caducados=Sum("puntos", filter=Q(tipo=MovimientoPuntos.Tipo.EXPIRA)),
    )
    emitidos = agg["emitidos"] or 0
    redimidos = abs(agg["redimidos"] or 0)
    caducados = abs(agg["caducados"] or 0)

    pasivo = (Cliente.objects.aggregate(s=Sum("puntos_saldo"))["s"] or 0)
    canjes = Canje.objects.exclude(estado=Canje.Estado.CANCELADO)
    # `costo` y no `costo_estimado`: desde P9 un premio de producto guarda lo
    # que de verdad costó entregarlo. El estimado se queda para los descuentos,
    # que no consumen insumo y cuestan ingreso no percibido.
    costo_entregado = sum((c.costo for c in
                           canjes.select_related("premio", "premio__receta")), CERO)

    return {
        "emitidos": emitidos,
        "redimidos": redimidos,
        "caducados": caducados,
        "pasivo": pasivo,
        "tasa_redencion": _pct(redimidos, emitidos),
        "canjes": canjes.count(),
        "canjes_pendientes": Canje.objects.filter(
            estado=Canje.Estado.PENDIENTE).count(),
        "costo_entregado": costo_entregado,
        "costo_pasivo": costo_pasivo(pasivo),
    }


def costo_por_punto_promedio():
    """Cuánto te cuesta en promedio un punto, según tus premios activos."""
    premios = [p for p in Premio.objects.filter(activo=True)
               .select_related("receta") if p.puntos_requeridos]
    if not premios:
        return CERO
    total = sum((p.costo_por_punto for p in premios), CERO)
    return total / len(premios)


def costo_pasivo(pasivo=None):
    """Cuánto costaría que todos canjearan sus puntos hoy."""
    if pasivo is None:
        pasivo = Cliente.objects.aggregate(s=Sum("puntos_saldo"))["s"] or 0
    return costo_por_punto_promedio() * pasivo


# ══════════════════════════════════════════════════════════════════════════════
#  MARGEN Y RETORNO
# ══════════════════════════════════════════════════════════════════════════════

def margen_de_miembros(desde=None):
    """Margen bruto real de las ventas hechas por clientes del programa.

    Con el costo FIFO de cada venta, no con el estimado del catálogo. Las
    cortesías quedan fuera: son costo de mercadotecnia, no venta con margen.
    """
    compras = Compra.objects.filter(nota__isnull=False)
    if desde:
        compras = compras.filter(fecha__gte=desde)
    notas = list(compras.values_list("nota_id", flat=True))
    if not notas:
        return CERO
    lineas = (Venta.objects
              .filter(nota_id__in=notas, es_cortesia=False)
              .con_costeo())
    return sum((v.ganancia for v in lineas), CERO)


def resumen_retorno(desde=None):
    """El número que importa: ¿el programa cuesta menos del 5% del margen?"""
    margen = margen_de_miembros(desde)
    canjes = Canje.objects.exclude(estado=Canje.Estado.CANCELADO)
    if desde:
        canjes = canjes.filter(creado__date__gte=desde)
    costo = sum((c.costo for c in
                 canjes.select_related("premio", "premio__receta")), CERO)

    compras = Compra.objects.all()
    if desde:
        compras = compras.filter(fecha__gte=desde)
    ventas = compras.aggregate(t=Sum("monto"))["t"] or CERO

    return {
        "ventas_miembros": ventas,
        "margen_miembros": margen,
        "costo_premios": costo,
        "pct_margen": _pct(costo, margen),
        "objetivo_pct": 5,
        "dentro_objetivo": (_pct(costo, margen) or 0) <= 5,
        "utilidad_neta": margen - costo,
    }


# ══════════════════════════════════════════════════════════════════════════════
#  COMPORTAMIENTO
# ══════════════════════════════════════════════════════════════════════════════

def retencion(dias):
    """% de clientes que volvieron a comprar dentro de N días de su primera compra.

    Solo cuenta a los que ya tuvieron tiempo de volver (primera compra hace más
    de N días), para no castigar a los clientes recién registrados.
    """
    corte = timezone.localdate() - timedelta(days=dias)
    cohorte = (Compra.objects.values("cliente_id")
               .annotate(primera=Min("fecha"), ultima=Max("fecha"),
                         compras=Count("id"))
               .filter(primera__lte=corte))
    total = repitieron = 0
    for fila in cohorte:
        total += 1
        if fila["compras"] > 1 and (fila["ultima"] - fila["primera"]).days <= dias:
            repitieron += 1
    return {"cohorte": total, "repitieron": repitieron,
            "pct": _pct(repitieron, total)}


def resumen_comportamiento():
    compradores = Cliente.objects.filter(visitas__gt=0)
    agg = compradores.aggregate(
        gasto_total=Sum("gasto_historico"), visitas_total=Sum("visitas"),
        n=Count("id"))
    total_visitas = agg["visitas_total"] or 0
    total_gasto = agg["gasto_total"] or CERO
    n = agg["n"] or 0

    # Frecuencia: visitas por cliente y por mes de vida en el programa.
    dias_vida = []
    hoy = timezone.localdate()
    for c in compradores.only("alta", "visitas"):
        dias = max(1, (hoy - timezone.localtime(c.alta).date()).days)
        dias_vida.append(c.visitas * 30 / dias)
    frecuencia = sum(dias_vida) / len(dias_vida) if dias_vida else 0

    margen = margen_de_miembros()
    return {
        "clientes": n,
        "ticket_promedio": (total_gasto / total_visitas) if total_visitas else CERO,
        "visitas_promedio": (total_visitas / n) if n else 0,
        "frecuencia_mensual": frecuencia,
        "ltv": (total_gasto / n) if n else CERO,
        "ltv_margen": (margen / n) if n else CERO,
        "retencion_30": retencion(30),
        "retencion_60": retencion(60),
        "retencion_90": retencion(90),
    }


# ══════════════════════════════════════════════════════════════════════════════
#  MARKETING
# ══════════════════════════════════════════════════════════════════════════════

def resumen_marketing(desde=None):
    mensajes = Mensaje.objects.all()
    if desde:
        mensajes = mensajes.filter(creado__date__gte=desde)

    enviados = mensajes.filter(estado__in=Mensaje.ESTADOS_ENVIADOS).count()
    entregados = mensajes.filter(
        estado__in=(Mensaje.Estado.ENTREGADO, Mensaje.Estado.LEIDO)).count()
    leidos = mensajes.filter(estado=Mensaje.Estado.LEIDO).count()
    convertidos = mensajes.filter(compra_atribuida__isnull=False)
    ventas = convertidos.aggregate(
        t=Sum("compra_atribuida__monto"))["t"] or CERO

    return {
        "encolados": mensajes.filter(estado=Mensaje.Estado.PROGRAMADO).count(),
        "enviados": enviados,
        "entregados": entregados,
        "leidos": leidos,
        "fallidos": mensajes.filter(estado=Mensaje.Estado.FALLIDO).count(),
        "conversiones": convertidos.count(),
        "ventas_atribuidas": ventas,
        "tasa_entrega": _pct(entregados, enviados),
        "tasa_lectura": _pct(leidos, enviados),
        "tasa_conversion": _pct(convertidos.count(), enviados),
    }


def desglose_marketing(desde=None):
    """Resultado de cada automatización y campaña, una fila por origen."""
    mensajes = Mensaje.objects.select_related("automatizacion", "campana",
                                              "compra_atribuida")
    if desde:
        mensajes = mensajes.filter(creado__date__gte=desde)

    filas = defaultdict(lambda: {"enviados": 0, "leidos": 0, "conversiones": 0,
                                 "ventas": CERO, "encolados": 0})
    for m in mensajes:
        origen = (m.automatizacion.nombre if m.automatizacion
                  else m.campana.nombre if m.campana else "Manual")
        fila = filas[origen]
        if m.estado == Mensaje.Estado.PROGRAMADO:
            fila["encolados"] += 1
        if m.estado in Mensaje.ESTADOS_ENVIADOS:
            fila["enviados"] += 1
        if m.estado == Mensaje.Estado.LEIDO:
            fila["leidos"] += 1
        if m.compra_atribuida_id:
            fila["conversiones"] += 1
            fila["ventas"] += m.compra_atribuida.monto

    resultado = []
    for origen, fila in filas.items():
        fila["origen"] = origen
        fila["tasa"] = _pct(fila["conversiones"], fila["enviados"])
        resultado.append(fila)
    resultado.sort(key=lambda f: -f["enviados"])
    return resultado
