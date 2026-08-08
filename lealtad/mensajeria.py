"""Envío de mensajes al cliente: encolado, proveedores y despacho.

Nada se envía directo: todo mensaje se encola en `Mensaje` y luego se despacha.
Así siempre hay bitácora, se puede reintentar y el modo simulado deja probar el
programa completo sin contratar nada.
"""

import json
import logging
from datetime import timedelta
from urllib import error as urlerror
from urllib import request as urlrequest

from django.conf import settings
from django.utils import timezone

from .models import Cliente, ConfiguracionPrograma, Mensaje, Plantilla

log = logging.getLogger(__name__)

MAX_INTENTOS = 3


# ══════════════════════════════════════════════════════════════════════════════
#  ENCOLADO
# ══════════════════════════════════════════════════════════════════════════════

def variables_de(cliente, **extra):
    """Valores que puede usar cualquier plantilla."""
    cfg = ConfiguracionPrograma.get()
    nivel = cliente.nivel
    premio = cliente.siguiente_premio
    datos = {
        "nombre": cliente.nombre_corto,
        "negocio": cfg.nombre_negocio,
        "saldo": cliente.puntos_saldo,
        "nivel": nivel.nombre if nivel else "",
        "premio": premio.nombre if premio else "",
        "faltan": max(0, premio.puntos_requeridos - cliente.puntos_saldo) if premio else 0,
        "link": cfg.url_de(cliente.get_absolute_url()),
    }
    datos.update(extra)
    return datos


def encolar(cliente, plantilla, *, automatizacion=None, campana=None,
            cuando=None, cuerpo=None, **variables):
    """Deja un mensaje listo para salir. Devuelve el `Mensaje` o None."""
    if cliente.estado == Cliente.Estado.BAJA or not cliente.acepta_mensajes:
        return None
    if plantilla and not plantilla.activa:
        return None

    texto = cuerpo or plantilla.render(**variables_de(cliente, **variables))
    if not texto.strip():
        return None

    return Mensaje.objects.create(
        cliente=cliente, automatizacion=automatizacion, campana=campana,
        plantilla=plantilla,
        canal=plantilla.canal if plantilla else Plantilla.Canal.WHATSAPP,
        telefono=cliente.telefono, cuerpo=texto,
        programado_para=cuando or timezone.now(),
    )


# ══════════════════════════════════════════════════════════════════════════════
#  PROVEEDORES
# ══════════════════════════════════════════════════════════════════════════════

class ErrorEnvio(Exception):
    """El proveedor rechazó el mensaje."""


def _post_json(url, payload, headers):
    datos = json.dumps(payload).encode()
    peticion = urlrequest.Request(url, data=datos, method="POST",
                                  headers={"Content-Type": "application/json",
                                           **headers})
    try:
        with urlrequest.urlopen(peticion, timeout=20) as resp:
            return json.loads(resp.read().decode() or "{}")
    except urlerror.HTTPError as e:
        detalle = e.read().decode(errors="replace")[:300]
        raise ErrorEnvio(f"HTTP {e.code}: {detalle}") from e
    except urlerror.URLError as e:
        raise ErrorEnvio(f"Sin conexión con el proveedor: {e.reason}") from e


def _enviar_simulado(mensaje):
    """No manda nada: solo lo marca como enviado para poder probar el flujo."""
    log.info("[lealtad·simulado] %s → %s", mensaje.telefono, mensaje.cuerpo)
    return f"sim-{mensaje.pk}"


def _enviar_whatsapp(mensaje):
    """WhatsApp Business API (Meta Cloud API).

    Dentro de la ventana de 24 h manda texto libre. Si la plantilla tiene un
    nombre aprobado en Meta, manda la plantilla (necesario para iniciar
    conversación).
    """
    token = getattr(settings, "WHATSAPP_TOKEN", "")
    phone_id = getattr(settings, "WHATSAPP_PHONE_NUMBER_ID", "")
    version = getattr(settings, "WHATSAPP_API_VERSION", "v21.0")
    if not token or not phone_id:
        raise ErrorEnvio(
            "Falta configurar WHATSAPP_TOKEN y WHATSAPP_PHONE_NUMBER_ID.")

    destino = mensaje.telefono.lstrip("+")
    plantilla = mensaje.plantilla
    if plantilla and plantilla.plantilla_meta:
        cuerpo = {
            "messaging_product": "whatsapp",
            "to": destino,
            "type": "template",
            "template": {
                "name": plantilla.plantilla_meta,
                "language": {"code": plantilla.idioma_meta or "es_MX"},
                "components": [{
                    "type": "body",
                    "parameters": [{"type": "text", "text": mensaje.cuerpo}],
                }],
            },
        }
    else:
        cuerpo = {
            "messaging_product": "whatsapp",
            "to": destino,
            "type": "text",
            "text": {"preview_url": True, "body": mensaje.cuerpo},
        }

    respuesta = _post_json(
        f"https://graph.facebook.com/{version}/{phone_id}/messages",
        cuerpo, {"Authorization": f"Bearer {token}"})
    try:
        return respuesta["messages"][0]["id"]
    except (KeyError, IndexError):
        raise ErrorEnvio(f"Respuesta inesperada de Meta: {respuesta}")


def _enviar_twilio(mensaje):
    """SMS de respaldo por Twilio."""
    import base64
    from urllib.parse import urlencode

    sid = getattr(settings, "TWILIO_ACCOUNT_SID", "")
    auth = getattr(settings, "TWILIO_AUTH_TOKEN", "")
    origen = getattr(settings, "TWILIO_FROM", "")
    if not (sid and auth and origen):
        raise ErrorEnvio(
            "Falta configurar TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN y TWILIO_FROM.")

    datos = urlencode({"To": mensaje.telefono, "From": origen,
                       "Body": mensaje.cuerpo}).encode()
    credenciales = base64.b64encode(f"{sid}:{auth}".encode()).decode()
    peticion = urlrequest.Request(
        f"https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages.json",
        data=datos, method="POST",
        headers={"Authorization": f"Basic {credenciales}",
                 "Content-Type": "application/x-www-form-urlencoded"})
    try:
        with urlrequest.urlopen(peticion, timeout=20) as resp:
            return json.loads(resp.read().decode()).get("sid", "")
    except urlerror.HTTPError as e:
        raise ErrorEnvio(f"HTTP {e.code}: {e.read().decode(errors='replace')[:300]}") from e
    except urlerror.URLError as e:
        raise ErrorEnvio(f"Sin conexión con Twilio: {e.reason}") from e


PROVEEDORES = {
    ConfiguracionPrograma.Proveedor.SIMULADO: _enviar_simulado,
    ConfiguracionPrograma.Proveedor.WHATSAPP: _enviar_whatsapp,
    ConfiguracionPrograma.Proveedor.TWILIO: _enviar_twilio,
}


# ══════════════════════════════════════════════════════════════════════════════
#  DESPACHO
# ══════════════════════════════════════════════════════════════════════════════

def en_horario(cfg=None, ahora=None):
    """¿Es hora decente para mandarle un mensaje a alguien?"""
    cfg = cfg or ConfiguracionPrograma.get()
    hora = (ahora or timezone.localtime()).hour
    if cfg.envio_hora_inicio <= cfg.envio_hora_fin:
        return cfg.envio_hora_inicio <= hora <= cfg.envio_hora_fin
    return hora >= cfg.envio_hora_inicio or hora <= cfg.envio_hora_fin


def enviar(mensaje, cfg=None):
    """Manda un mensaje ya encolado y guarda el resultado."""
    cfg = cfg or ConfiguracionPrograma.get()
    enviar_con = PROVEEDORES.get(cfg.proveedor_mensajes, _enviar_simulado)
    mensaje.intentos += 1
    try:
        mensaje.proveedor_id = enviar_con(mensaje) or ""
        mensaje.estado = Mensaje.Estado.ENVIADO
        mensaje.enviado_en = timezone.now()
        mensaje.error = ""
    except ErrorEnvio as e:
        mensaje.error = str(e)[:200]
        if mensaje.intentos >= MAX_INTENTOS:
            mensaje.estado = Mensaje.Estado.FALLIDO
        else:
            # Reintento con espera creciente: 5, 25 minutos.
            mensaje.programado_para = timezone.now() + timedelta(
                minutes=5 ** mensaje.intentos)
    mensaje.save(update_fields=["estado", "enviado_en", "proveedor_id",
                                "error", "intentos", "programado_para"])
    return mensaje


# Tope de mensajes por corrida cuando nadie pide otra cosa.
LIMITE_DESPACHO = 100


def despachar_pendientes(limite=LIMITE_DESPACHO, forzar_horario=False):
    """Manda lo que ya toca. Devuelve (enviados, fallidos, pospuestos)."""
    cfg = ConfiguracionPrograma.get()
    if not forzar_horario and not en_horario(cfg):
        return 0, 0, 0

    pendientes = (Mensaje.objects
                  .filter(estado=Mensaje.Estado.PROGRAMADO,
                          programado_para__lte=timezone.now())
                  .select_related("cliente", "plantilla")[:limite])
    enviados = fallidos = pospuestos = 0
    for mensaje in pendientes:
        enviar(mensaje, cfg)
        if mensaje.estado == Mensaje.Estado.ENVIADO:
            enviados += 1
        elif mensaje.estado == Mensaje.Estado.FALLIDO:
            fallidos += 1
        else:
            pospuestos += 1
    return enviados, fallidos, pospuestos


# ══════════════════════════════════════════════════════════════════════════════
#  ATRIBUCIÓN
# ══════════════════════════════════════════════════════════════════════════════

def atribuir_compras():
    """Liga cada mensaje enviado con la primera compra que vino después.

    Es la base del tablero de marketing: cuántas ventas trajo cada mensaje.
    """
    from .models import Compra

    cfg = ConfiguracionPrograma.get()
    ventana = timedelta(days=cfg.ventana_atribucion_dias)
    sin_atribuir = (Mensaje.objects
                    .filter(estado__in=Mensaje.ESTADOS_ENVIADOS,
                            compra_atribuida__isnull=True,
                            enviado_en__isnull=False)
                    .filter(enviado_en__gte=timezone.now() - ventana * 3))
    ligados = 0
    for mensaje in sin_atribuir:
        compra = (Compra.objects
                  .filter(cliente_id=mensaje.cliente_id,
                          creada__gte=mensaje.enviado_en,
                          creada__lte=mensaje.enviado_en + ventana)
                  .order_by("creada").first())
        if compra:
            mensaje.compra_atribuida = compra
            mensaje.save(update_fields=["compra_atribuida"])
            ligados += 1
    return ligados
