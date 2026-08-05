"""Las reglas de "cuándo mandar qué".

Ninguna regla está escrita aquí: este módulo solo lee las `Automatizacion` que
tú configuraste en el panel y decide a quién le toca hoy. Agregar una regla
nueva es crear un registro, no tocar código.
"""

import logging
from datetime import datetime, timedelta

from django.utils import timezone

from . import mensajeria
from .models import Automatizacion, Campana, Cliente, Mensaje, Nivel, Premio

log = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════════════════
#  CONTROL DE REPETICIÓN
# ══════════════════════════════════════════════════════════════════════════════

def puede_recibir(automatizacion, cliente, ahora=None):
    """Respeta el 'no repetir en N días' y el máximo por cliente."""
    previos = Mensaje.objects.filter(cliente=cliente,
                                     automatizacion=automatizacion)
    if automatizacion.max_por_cliente:
        if previos.count() >= automatizacion.max_por_cliente:
            return False
    if automatizacion.no_repetir_dias:
        desde = (ahora or timezone.now()) - timedelta(
            days=automatizacion.no_repetir_dias)
        if previos.filter(creado__gte=desde).exists():
            return False
    return True


def _activas(disparador):
    return (Automatizacion.objects
            .filter(activa=True, disparador=disparador)
            .select_related("plantilla", "nivel_objetivo"))


def _encolar(automatizacion, cliente, cuando=None, **variables):
    if not puede_recibir(automatizacion, cliente):
        return None
    return mensajeria.encolar(cliente, automatizacion.plantilla,
                              automatizacion=automatizacion, cuando=cuando,
                              **variables)


def _hora_de_hoy(automatizacion, fecha=None):
    """Momento del día en que esta automatización quiere salir."""
    fecha = fecha or timezone.localdate()
    naive = datetime(fecha.year, fecha.month, fecha.day,
                     automatizacion.hora_envio, 0)
    momento = timezone.make_aware(naive, timezone.get_current_timezone())
    return max(momento, timezone.now())


# ══════════════════════════════════════════════════════════════════════════════
#  DISPARADORES POR EVENTO
# ══════════════════════════════════════════════════════════════════════════════

def disparar_evento(disparador, cliente, **variables):
    """Encola los mensajes de las automatizaciones de ese evento."""
    creados = []
    for auto in _activas(disparador):
        if not auto.permite_hoy():
            continue
        if disparador == Automatizacion.Disparador.NIVEL and auto.nivel_objetivo_id:
            if variables.get("nivel_id") != auto.nivel_objetivo_id:
                continue
        mensaje = _encolar(auto, cliente,
                           **{k: v for k, v in variables.items()
                              if k != "nivel_id"})
        if mensaje:
            creados.append(mensaje)
    return creados


def tras_compra(cliente, compra, era_primera=False, nivel_antes=None):
    """Todo lo que se dispara al momento de registrar una compra."""
    cliente.refresh_from_db()
    variables = {
        "puntos": compra.puntos_ganados,
        "monto": f"{compra.monto:,.2f}",
    }
    try:
        if era_primera:
            disparar_evento(Automatizacion.Disparador.PRIMERA_COMPRA,
                            cliente, **variables)
        disparar_evento(Automatizacion.Disparador.COMPRA, cliente, **variables)

        nivel = cliente.nivel
        if nivel and (not nivel_antes or nivel.pk != nivel_antes.pk):
            disparar_evento(Automatizacion.Disparador.NIVEL, cliente,
                            nivel_id=nivel.pk, nivel=nivel.nombre)

        # ¿Con esta compra ya alcanzó algún premio?
        disponibles = cliente.premios_disponibles()
        if disponibles:
            mejor = max(disponibles, key=lambda p: p.puntos_requeridos)
            disparar_evento(Automatizacion.Disparador.PREMIO_DISPONIBLE,
                            cliente, premio=mejor.nombre, **variables)
    except Exception:  # noqa: BLE001 - una venta jamás falla por un mensaje
        log.exception("Fallaron las automatizaciones de la compra %s", compra.pk)


# ══════════════════════════════════════════════════════════════════════════════
#  DISPARADORES PROGRAMADOS
# ══════════════════════════════════════════════════════════════════════════════

def _cerca_de_premio(auto, hoy):
    """Clientes que van al X% o más de su siguiente premio."""
    creados = []
    premios = list(Premio.objects.filter(activo=True).order_by("puntos_requeridos"))
    if not premios:
        return creados
    tope = premios[-1].puntos_requeridos

    candidatos = Cliente.objects.filter(
        acepta_mensajes=True, puntos_saldo__gt=0, puntos_saldo__lt=tope,
    ).exclude(estado=Cliente.Estado.BAJA)

    for cliente in candidatos:
        premio = next((p for p in premios
                       if p.puntos_requeridos > cliente.puntos_saldo), None)
        if not premio:
            continue
        avance = cliente.puntos_saldo * 100 / premio.puntos_requeridos
        if avance < auto.umbral_porcentaje:
            continue
        mensaje = _encolar(auto, cliente, cuando=_hora_de_hoy(auto, hoy),
                           premio=premio.nombre,
                           faltan=premio.puntos_requeridos - cliente.puntos_saldo)
        if mensaje:
            creados.append(mensaje)
    return creados


def _inactivos(auto, hoy):
    """Clientes que llevan N días sin comprar."""
    limite = hoy - timedelta(days=auto.dias)
    candidatos = (Cliente.objects
                  .filter(acepta_mensajes=True, ultima_compra__lte=limite)
                  .exclude(estado=Cliente.Estado.BAJA))
    creados = []
    for cliente in candidatos:
        mensaje = _encolar(auto, cliente, cuando=_hora_de_hoy(auto, hoy),
                           dias=cliente.dias_sin_comprar)
        if mensaje:
            creados.append(mensaje)
    return creados


def _cumpleaneros(auto, hoy):
    """Clientes cuyo cumpleaños cae dentro de N días."""
    objetivo = hoy + timedelta(days=auto.dias)
    candidatos = (Cliente.objects
                  .filter(acepta_mensajes=True,
                          cumpleanos__month=objetivo.month,
                          cumpleanos__day=objetivo.day)
                  .exclude(estado=Cliente.Estado.BAJA))
    creados = []
    for cliente in candidatos:
        mensaje = _encolar(auto, cliente, cuando=_hora_de_hoy(auto, hoy),
                           dias=auto.dias)
        if mensaje:
            creados.append(mensaje)
    return creados


def _por_expirar(auto, hoy):
    """Clientes con puntos a punto de caducar."""
    from . import servicios

    creados = []
    por_cliente = {}
    for lote in servicios.puntos_por_expirar(auto.dias):
        acumulado = por_cliente.setdefault(lote.cliente_id, [lote.cliente, 0, None])
        acumulado[1] += lote.saldo_lote
        if acumulado[2] is None or lote.expira_el < acumulado[2]:
            acumulado[2] = lote.expira_el

    for cliente, puntos, primera_fecha in por_cliente.values():
        if not cliente.acepta_mensajes or cliente.estado == Cliente.Estado.BAJA:
            continue
        mensaje = _encolar(auto, cliente, cuando=_hora_de_hoy(auto, hoy),
                           puntos=puntos,
                           dias=(primera_fecha - hoy).days)
        if mensaje:
            creados.append(mensaje)
    return creados


PROGRAMADAS = {
    Automatizacion.Disparador.CERCA_PREMIO: _cerca_de_premio,
    Automatizacion.Disparador.INACTIVIDAD: _inactivos,
    Automatizacion.Disparador.CUMPLEANOS: _cumpleaneros,
    Automatizacion.Disparador.POR_EXPIRAR: _por_expirar,
}


def correr_programadas(hoy=None):
    """Evalúa las automatizaciones con horario. Devuelve {nombre: encolados}."""
    hoy = hoy or timezone.localdate()
    resultado = {}
    for disparador, evaluar in PROGRAMADAS.items():
        for auto in _activas(disparador):
            if not auto.permite_hoy(hoy):
                continue
            try:
                creados = evaluar(auto, hoy)
            except Exception:  # noqa: BLE001 - una regla rota no frena las demás
                log.exception("Falló la automatización «%s»", auto.nombre)
                continue
            if creados:
                resultado[auto.nombre] = len(creados)
    return resultado


# ══════════════════════════════════════════════════════════════════════════════
#  CAMPAÑAS MANUALES
# ══════════════════════════════════════════════════════════════════════════════

def destinatarios(campana, hoy=None):
    """Clientes a los que le tocaría esta campaña."""
    hoy = hoy or timezone.localdate()
    base = Cliente.objects.filter(acepta_mensajes=True).exclude(
        estado=Cliente.Estado.BAJA)

    if campana.segmento == Campana.Segmento.ACTIVOS:
        return base.filter(estado=Cliente.Estado.ACTIVO)
    if campana.segmento == Campana.Segmento.INACTIVOS:
        return base.filter(estado=Cliente.Estado.INACTIVO)
    if campana.segmento == Campana.Segmento.NIVEL and campana.nivel_id:
        siguiente = (Nivel.objects
                     .filter(activo=True,
                             puntos_requeridos__gt=campana.nivel.puntos_requeridos)
                     .order_by("puntos_requeridos").first())
        base = base.filter(puntos_historicos__gte=campana.nivel.puntos_requeridos)
        if siguiente:
            base = base.filter(puntos_historicos__lt=siguiente.puntos_requeridos)
        return base
    if campana.segmento == Campana.Segmento.CUMPLEANEROS:
        return base.filter(cumpleanos__month=hoy.month)
    if campana.segmento == Campana.Segmento.NUEVOS:
        return base.filter(alta__gte=timezone.now() - timedelta(days=30))
    if campana.segmento == Campana.Segmento.CERCA_PREMIO:
        premio = Premio.objects.filter(activo=True).order_by(
            "puntos_requeridos").first()
        if not premio:
            return base.none()
        minimo = int(premio.puntos_requeridos * 0.8)
        return base.filter(puntos_saldo__gte=minimo,
                           puntos_saldo__lt=premio.puntos_requeridos)
    return base


def enviar_campana(campana):
    """Encola la campaña a todo su segmento. Devuelve cuántos mensajes creó."""
    if campana.estado == Campana.Estado.ENVIADA:
        return 0
    creados = 0
    for cliente in destinatarios(campana):
        cuerpo = None
        if campana.cuerpo:
            variables = mensajeria.variables_de(cliente)
            try:
                cuerpo = campana.cuerpo.format(**variables)
            except (KeyError, IndexError, ValueError):
                cuerpo = campana.cuerpo
        if not cuerpo and not campana.plantilla:
            continue
        if mensajeria.encolar(cliente, campana.plantilla, campana=campana,
                              cuerpo=cuerpo,
                              cuando=campana.programada_para):
            creados += 1
    campana.estado = Campana.Estado.ENVIADA
    campana.enviada_en = timezone.now()
    campana.save(update_fields=["estado", "enviada_en"])
    return creados
