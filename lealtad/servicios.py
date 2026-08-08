"""Motor del programa: altas, puntos, caducidad FIFO y canjes.

Todas las escrituras de puntos pasan por aquí. El saldo del cliente siempre es
la suma de sus movimientos: si algo se descuadra, `recalcular` lo reconstruye.
"""

import secrets
from datetime import timedelta
from decimal import Decimal

from django.db import transaction
from django.db.models import Count, F, Max, Q, Sum
from django.utils import timezone

from .models import (
    ALFABETO_CODIGO, Canje, Cliente, Compra, ConfiguracionPrograma,
    MovimientoPuntos, PromocionPuntos, TelefonoInvalido, normaliza_telefono,
)


class ErrorLealtad(Exception):
    """Algo impidió completar la operación (saldo, límites, duplicados)."""


#: Centinela para distinguir «no me pasaron vigencia» de «vigencia None».
_SIN_DATO = object()


def _suma_meses(fecha, meses):
    """Misma fecha N meses después, ajustando fin de mes (31 ene + 1 = 28 feb)."""
    mes = fecha.month - 1 + meses
    anio = fecha.year + mes // 12
    mes = mes % 12 + 1
    dia = min(fecha.day, [31, 29 if (anio % 4 == 0 and (anio % 100 != 0 or anio % 400 == 0))
                          else 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31][mes - 1])
    return fecha.replace(year=anio, month=mes, day=dia)


# ══════════════════════════════════════════════════════════════════════════════
#  CLIENTES
# ══════════════════════════════════════════════════════════════════════════════

def buscar_cliente(texto):
    """Encuentra un cliente por teléfono (en cualquier formato) o por su código."""
    texto = (texto or "").strip()
    if not texto:
        return None
    try:
        return Cliente.objects.filter(telefono=normaliza_telefono(texto)).first()
    except TelefonoInvalido:
        pass
    codigo = texto.upper().lstrip("#")
    if len(codigo) == 6:
        return Cliente.objects.filter(codigo=codigo).first()
    return None


@transaction.atomic
def alta_cliente(telefono, nombre="", cumpleanos=None,
                 origen=Cliente.Origen.CAJA, acepta_mensajes=True):
    """Da de alta un cliente (o completa los datos si ya existía).

    Devuelve `(cliente, creado)`. Nunca duplica un teléfono.
    """
    telefono = normaliza_telefono(telefono)
    cliente = Cliente.objects.filter(telefono=telefono).first()
    if cliente:
        cambios = []
        if nombre and not cliente.nombre:
            cliente.nombre = nombre.strip()
            cambios.append("nombre")
        if cumpleanos and not cliente.cumpleanos:
            cliente.cumpleanos = cumpleanos
            cambios.append("cumpleanos")
        if cambios:
            cliente.save(update_fields=cambios)
        return cliente, False

    cliente = Cliente.objects.create(
        telefono=telefono, nombre=(nombre or "").strip(),
        cumpleanos=cumpleanos, origen=origen,
        acepta_mensajes=acepta_mensajes,
    )
    from . import automatizaciones
    automatizaciones.disparar_evento("bienvenida", cliente)
    return cliente, True


# ══════════════════════════════════════════════════════════════════════════════
#  PUNTOS
# ══════════════════════════════════════════════════════════════════════════════

def lotes_vivos(cliente=None):
    """Movimientos con puntos sin gastar, del que caduca antes al que después.

    Un lote es cualquier movimiento que sumó puntos y todavía tiene saldo: da
    igual si vino de una compra, de una cortesía o de un canje cancelado.
    """
    qs = MovimientoPuntos.objects.filter(saldo_lote__gt=0)
    if cliente is not None:
        qs = qs.filter(cliente=cliente)
    return qs.order_by(F("expira_el").asc(nulls_last=True), "creado", "id")


def _vencimiento(cfg=None):
    """Hasta cuándo valen los puntos que se ganan hoy. None = no caducan."""
    cfg = cfg or ConfiguracionPrograma.get()
    if not cfg.vigencia_puntos_meses:
        return None
    return _suma_meses(timezone.localdate(), cfg.vigencia_puntos_meses)


def _otorgar_puntos(cliente, puntos, *, tipo=MovimientoPuntos.Tipo.GANA,
                    compra=None, descripcion="", cfg=None, expira=_SIN_DATO):
    """Crea un lote de puntos con su fecha de caducidad."""
    if puntos <= 0:
        return None
    if expira is _SIN_DATO:
        expira = _vencimiento(cfg)
    return MovimientoPuntos.objects.create(
        cliente=cliente, tipo=tipo, puntos=puntos, saldo_lote=puntos,
        expira_el=expira, compra=compra, descripcion=descripcion,
    )


def _consumir_lotes(cliente, puntos, tipo, descripcion="", canje=None):
    """Descuenta puntos de los lotes vivos, empezando por el que caduca antes.

    Devuelve `(movimiento, expira_el)`: el movimiento negativo que registra el
    consumo y la vigencia más temprana entre los lotes que se gastaron, para
    poder devolverlos con su fecha original si el canje se cancela.

    Si los lotes no alcanzan es que el saldo del cliente estaba descuadrado, y
    seguir restaría puntos que no existen; mejor abortar y que se note.
    """
    restante = puntos
    primera_vigencia = None
    for lote in lotes_vivos(cliente).select_for_update():
        if restante <= 0:
            break
        if primera_vigencia is None:
            primera_vigencia = lote.expira_el
        toma = min(lote.saldo_lote, restante)
        lote.saldo_lote -= toma
        lote.save(update_fields=["saldo_lote"])
        restante -= toma

    if restante > 0:
        raise ErrorLealtad(
            f"El saldo de {cliente.nombre_corto} no cuadra: faltan {restante} "
            f"puntos por respaldar. Revisa sus movimientos antes de continuar.")

    movimiento = MovimientoPuntos.objects.create(
        cliente=cliente, tipo=tipo, puntos=-puntos, canje=canje,
        descripcion=descripcion,
    )
    return movimiento, primera_vigencia


#: Movimientos que suman a los puntos históricos (los que definen el nivel).
#: La devolución de un canje queda fuera a propósito: esos puntos ya contaron
#: cuando se ganaron, y volver a sumarlos regalaría niveles.
Q_ACUMULA = (Q(tipo=MovimientoPuntos.Tipo.GANA)
             | Q(tipo=MovimientoPuntos.Tipo.AJUSTE, puntos__gt=0))


def recalcular(cliente):
    """Reconstruye saldo, históricos, visitas y estado desde los movimientos."""
    agg = cliente.movimientos.aggregate(
        saldo=Sum("puntos"),
        ganados=Sum("puntos", filter=Q_ACUMULA),
    )
    compras = cliente.compras.aggregate(
        gasto=Sum("monto"), visitas=Count("id"), ultima=Max("fecha"))

    cliente.puntos_saldo = agg["saldo"] or 0
    cliente.puntos_historicos = agg["ganados"] or 0
    cliente.gasto_historico = compras["gasto"] or Decimal("0")
    cliente.visitas = compras["visitas"] or 0
    cliente.ultima_compra = compras["ultima"]

    cfg = ConfiguracionPrograma.get()
    if cliente.estado != Cliente.Estado.BAJA:
        dias = cliente.dias_sin_comprar
        cliente.estado = (Cliente.Estado.INACTIVO
                          if dias is not None and dias > cfg.dias_inactividad
                          else Cliente.Estado.ACTIVO)
    cliente.save(update_fields=[
        "puntos_saldo", "puntos_historicos", "gasto_historico", "visitas",
        "ultima_compra", "estado"])
    return cliente


# ══════════════════════════════════════════════════════════════════════════════
#  COMPRAS
# ══════════════════════════════════════════════════════════════════════════════

@transaction.atomic
def registrar_compra(cliente, monto, *, nota=None, ticket="", fecha=None,
                     origen=Compra.Origen.CAJA):
    """Acumula los puntos de una compra y dispara las automatizaciones.

    Es idempotente: si esa nota o ese ticket ya se registraron, devuelve la
    compra existente sin volver a dar puntos.
    """
    cfg = ConfiguracionPrograma.get()
    if not cfg.activo:
        return None

    ya = None
    if nota is not None:
        ya = Compra.objects.filter(nota=nota).first()
    elif ticket:
        ya = Compra.objects.filter(ticket=ticket).first()
    if ya:
        return ya

    monto = Decimal(monto or 0)
    if monto <= 0:
        raise ErrorLealtad("El monto de la compra debe ser mayor a cero.")

    fecha = fecha or timezone.localdate()
    promo = PromocionPuntos.vigente(fecha)
    multiplicador = promo.multiplicador if promo else Decimal("1")
    puntos = int(cfg.puntos_de(monto) * multiplicador)

    era_primera = not cliente.compras.exists()
    nivel_antes = cliente.nivel

    compra = Compra.objects.create(
        cliente=cliente, nota=nota, ticket=ticket, monto=monto,
        puntos_ganados=puntos, multiplicador=multiplicador, fecha=fecha,
        origen=origen,
    )
    detalle = f"Compra de ${monto:,.2f}"
    if promo:
        detalle += f" · {promo.nombre} ({multiplicador}×)"
    _otorgar_puntos(cliente, puntos, compra=compra, descripcion=detalle, cfg=cfg)
    recalcular(cliente)

    from . import automatizaciones
    transaction.on_commit(lambda: automatizaciones.tras_compra(
        cliente, compra, era_primera=era_primera, nivel_antes=nivel_antes))
    return compra


def registrar_compra_desde_nota(nota, telefono, nombre=""):
    """Punto de entrada desde la caja. Nunca lanza: una venta no puede fallar
    porque el programa de lealtad tenga un problema."""
    if not telefono:
        return None
    try:
        cliente, _ = alta_cliente(telefono, nombre=nombre,
                                  origen=Cliente.Origen.CAJA)
        return registrar_compra(cliente, nota.total, nota=nota,
                                fecha=nota.fecha, origen=Compra.Origen.CAJA)
    except (TelefonoInvalido, ErrorLealtad):
        raise
    except Exception:  # noqa: BLE001 - la venta manda; el punto es no romperla
        import logging
        logging.getLogger(__name__).exception(
            "No se pudieron acumular puntos de la nota %s", nota.pk)
        return None


@transaction.atomic
def ajustar_puntos(cliente, puntos, descripcion, usuario=None):
    """Suma o resta puntos a mano (correcciones, cortesías)."""
    cliente = Cliente.objects.select_for_update().get(pk=cliente.pk)
    puntos = int(puntos)
    if puntos == 0:
        raise ErrorLealtad("El ajuste no puede ser de cero puntos.")
    if puntos < 0 and cliente.puntos_saldo + puntos < 0:
        raise ErrorLealtad(
            f"{cliente.nombre_corto} solo tiene {cliente.puntos_saldo} puntos.")

    if puntos > 0:
        _otorgar_puntos(cliente, puntos, tipo=MovimientoPuntos.Tipo.AJUSTE,
                        descripcion=descripcion)
    else:
        _consumir_lotes(cliente, -puntos, MovimientoPuntos.Tipo.AJUSTE,
                        descripcion=descripcion)
    return recalcular(cliente)


# ══════════════════════════════════════════════════════════════════════════════
#  CANJES
# ══════════════════════════════════════════════════════════════════════════════

def _codigo_canje():
    while True:
        codigo = "".join(secrets.choice(ALFABETO_CODIGO) for _ in range(6))
        if not Canje.objects.filter(codigo=codigo).exists():
            return codigo


@transaction.atomic
def canjear(cliente, premio, usuario=None, nota=None):
    """Canjea un premio: descuenta los puntos y deja el canje listo para entregar."""
    cliente = Cliente.objects.select_for_update().get(pk=cliente.pk)
    if not premio.activo:
        raise ErrorLealtad(f"El premio «{premio.nombre}» está desactivado.")
    if cliente.puntos_saldo < premio.puntos_requeridos:
        faltan = premio.puntos_requeridos - cliente.puntos_saldo
        raise ErrorLealtad(
            f"Le faltan {faltan} puntos para «{premio.nombre}» "
            f"(tiene {cliente.puntos_saldo} de {premio.puntos_requeridos}).")
    if not premio.puede_canjearlo(cliente):
        raise ErrorLealtad(
            f"«{premio.nombre}» no está disponible para este cliente "
            f"(revisa el nivel mínimo o el límite por cliente).")

    vence = None
    if premio.vigencia_dias:
        vence = timezone.localdate() + timedelta(days=premio.vigencia_dias)

    canje = Canje.objects.create(
        cliente=cliente, premio=premio,
        puntos_usados=premio.puntos_requeridos, codigo=_codigo_canje(),
        vence_el=vence, autorizado_por=usuario, nota=nota,
    )
    _, expira_puntos = _consumir_lotes(
        cliente, premio.puntos_requeridos, MovimientoPuntos.Tipo.CANJE,
        descripcion=f"Canje de {premio.nombre}", canje=canje)
    canje.expira_puntos = expira_puntos
    canje.save(update_fields=["expira_puntos"])
    recalcular(cliente)
    return canje


@transaction.atomic
def entregar_canje(canje, usuario=None, nota=None):
    if canje.estado != Canje.Estado.PENDIENTE:
        raise ErrorLealtad(f"Ese canje ya está {canje.get_estado_display().lower()}.")
    canje.estado = Canje.Estado.ENTREGADO
    canje.entregado_en = timezone.now()
    if usuario:
        canje.autorizado_por = usuario
    if nota:
        canje.nota = nota
    canje.save(update_fields=["estado", "entregado_en", "autorizado_por", "nota"])
    return canje


@transaction.atomic
def cancelar_canje(canje, motivo="Canje cancelado"):
    """Cancela un canje y le devuelve los puntos al cliente.

    Los puntos vuelven con la vigencia que traían, no con 12 meses nuevos: un
    canje cancelado no es una forma de revivir puntos a punto de caducar. Y se
    devuelven como DEVOLUCION, que no cuenta para los puntos de por vida,
    porque ya contaron cuando se ganaron.
    """
    if canje.estado not in (Canje.Estado.PENDIENTE, Canje.Estado.EXPIRADO):
        raise ErrorLealtad("Solo se pueden cancelar canjes pendientes.")
    canje.estado = Canje.Estado.CANCELADO
    canje.save(update_fields=["estado"])
    _otorgar_puntos(canje.cliente, canje.puntos_usados,
                    tipo=MovimientoPuntos.Tipo.DEVOLUCION,
                    expira=canje.expira_puntos,
                    descripcion=f"{motivo}: {canje.premio.nombre}")
    recalcular(canje.cliente)
    return canje


# ══════════════════════════════════════════════════════════════════════════════
#  CADUCIDAD
# ══════════════════════════════════════════════════════════════════════════════

@transaction.atomic
def expirar_puntos(hoy=None):
    """Caduca los lotes vencidos. Devuelve cuántos puntos se perdieron."""
    hoy = hoy or timezone.localdate()
    vencidos = (lotes_vivos().filter(expira_el__lt=hoy)
                .select_for_update().select_related("cliente"))
    total, tocados = 0, {}
    for lote in vencidos:
        puntos = lote.saldo_lote
        lote.saldo_lote = 0
        lote.save(update_fields=["saldo_lote"])
        MovimientoPuntos.objects.create(
            cliente=lote.cliente, tipo=MovimientoPuntos.Tipo.EXPIRA,
            puntos=-puntos,
            descripcion=f"Caducaron {puntos} puntos del {lote.creado:%d/%m/%Y}",
        )
        total += puntos
        tocados[lote.cliente_id] = lote.cliente
    for cliente in tocados.values():
        recalcular(cliente)
    return total


def puntos_por_expirar(dias):
    """Lotes que caducan dentro de los próximos `dias` días."""
    hoy = timezone.localdate()
    return (lotes_vivos()
            .filter(expira_el__gte=hoy, expira_el__lte=hoy + timedelta(days=dias))
            .select_related("cliente"))
