"""API REST del programa, para que un ERP externo registre compras.

JSON plano sobre las vistas de Django: sin dependencias nuevas y con el mismo
contrato del documento original. La autenticación es un token fijo que se
configura en el panel:

    Authorization: Bearer <token>
"""

import hmac
import json
import logging
from datetime import date
from decimal import Decimal, InvalidOperation
from functools import wraps

from django.conf import settings
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST

from . import automatizaciones, servicios
from .models import (
    Campana, Cliente, Compra, ConfiguracionPrograma, Mensaje, Premio,
    TelefonoInvalido,
)

log = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════════════════
#  PLOMERÍA
# ══════════════════════════════════════════════════════════════════════════════

def _error(mensaje, codigo=400, **extra):
    return JsonResponse({"ok": False, "error": mensaje, **extra}, status=codigo)


def requiere_token(vista):
    """Exige el token del programa en el encabezado Authorization."""
    @wraps(vista)
    def envoltura(request, *args, **kwargs):
        esperado = ConfiguracionPrograma.get().api_token
        if not esperado:
            return _error("La API está apagada: configura el token del "
                          "programa en el panel de lealtad.", 503)
        cabecera = request.headers.get("Authorization", "")
        recibido = cabecera[7:] if cabecera.startswith("Bearer ") else ""
        if not hmac.compare_digest(recibido, esperado):
            return _error("Token inválido.", 401)
        return vista(request, *args, **kwargs)
    return envoltura


def _cuerpo(request):
    try:
        return json.loads(request.body or b"{}")
    except ValueError:
        return None


def _monto(valor):
    try:
        return Decimal(str(valor))
    except (InvalidOperation, TypeError, ValueError):
        return None


def _fecha(valor):
    if not valor:
        return timezone.localdate()
    try:
        return date.fromisoformat(str(valor)[:10])
    except ValueError:
        return None


def _cliente_json(cliente):
    nivel = cliente.nivel
    siguiente = cliente.siguiente_premio
    return {
        "id": cliente.pk,
        "phone_number": cliente.telefono,
        "first_name": cliente.nombre,
        "birthday": cliente.cumpleanos.isoformat() if cliente.cumpleanos else None,
        "total_points": cliente.puntos_saldo,
        "lifetime_points": cliente.puntos_historicos,
        "lifetime_spend": str(cliente.gasto_historico),
        "visits": cliente.visitas,
        "status": cliente.estado,
        "tier": str(nivel) if nivel else None,
        "next_reward": ({"name": siguiente.nombre,
                         "points_required": siguiente.puntos_requeridos,
                         "points_missing": max(
                             0, siguiente.puntos_requeridos - cliente.puntos_saldo)}
                        if siguiente else None),
        "card_url": ConfiguracionPrograma.get().url_de(cliente.get_absolute_url()),
        "created_at": cliente.alta.isoformat(),
        "last_purchase_at": (cliente.ultima_compra.isoformat()
                             if cliente.ultima_compra else None),
    }


# ══════════════════════════════════════════════════════════════════════════════
#  CLIENTES
# ══════════════════════════════════════════════════════════════════════════════

@csrf_exempt
@require_POST
@requiere_token
def customers(request):
    """POST /api/lealtad/customers — registra o completa un cliente."""
    datos = _cuerpo(request)
    if datos is None:
        return _error("El cuerpo debe ser JSON válido.")
    try:
        cliente, creado = servicios.alta_cliente(
            datos.get("phone_number"),
            nombre=datos.get("first_name", ""),
            cumpleanos=_fecha(datos.get("birthday")) if datos.get("birthday") else None,
            origen=Cliente.Origen.API,
        )
    except TelefonoInvalido as e:
        return _error(str(e))
    return JsonResponse({"ok": True, "created": creado,
                         "customer": _cliente_json(cliente)},
                        status=201 if creado else 200)


@require_GET
@requiere_token
def customer_points(request, pk):
    """GET /api/lealtad/customers/<id>/points — saldo y premios alcanzables."""
    cliente = get_object_or_404(Cliente, pk=pk)
    return JsonResponse({
        "ok": True,
        "customer": _cliente_json(cliente),
        "available_rewards": [
            {"id": p.pk, "name": p.nombre, "points_required": p.puntos_requeridos}
            for p in cliente.premios_disponibles()
        ],
    })


# ══════════════════════════════════════════════════════════════════════════════
#  COMPRAS
# ══════════════════════════════════════════════════════════════════════════════

@csrf_exempt
@require_POST
@requiere_token
def purchases(request):
    """POST /api/lealtad/purchases — registra una venta y acumula sus puntos.

    Idempotente por `ticket_number`: si el ERP reintenta, no se duplican puntos.
    """
    datos = _cuerpo(request)
    if datos is None:
        return _error("El cuerpo debe ser JSON válido.")

    monto = _monto(datos.get("purchase_amount"))
    if monto is None or monto <= 0:
        return _error("`purchase_amount` debe ser un número mayor a cero.")
    fecha = _fecha(datos.get("purchase_date"))
    if fecha is None:
        return _error("`purchase_date` debe venir como AAAA-MM-DD.")

    try:
        cliente, creado = servicios.alta_cliente(
            datos.get("phone_number"), origen=Cliente.Origen.API)
    except TelefonoInvalido as e:
        return _error(str(e))

    ticket = str(datos.get("ticket_number") or "")[:40]
    ya_existia = bool(ticket) and cliente.compras.filter(ticket=ticket).exists()
    try:
        compra = servicios.registrar_compra(
            cliente, monto, ticket=ticket, fecha=fecha,
            origen=Compra.Origen.API)
    except servicios.ErrorLealtad as e:
        return _error(str(e))

    if compra is None:
        return _error("El programa de lealtad está apagado.", 503)

    cliente.refresh_from_db()
    return JsonResponse({
        "ok": True,
        "duplicate": ya_existia,
        "customer_created": creado,
        "purchase": {
            "id": compra.pk,
            "ticket_number": compra.ticket,
            "amount": str(compra.monto),
            "points_earned": compra.puntos_ganados,
            "multiplier": str(compra.multiplicador),
            "date": compra.fecha.isoformat(),
        },
        "customer": _cliente_json(cliente),
    }, status=200 if ya_existia else 201)


# ══════════════════════════════════════════════════════════════════════════════
#  PREMIOS
# ══════════════════════════════════════════════════════════════════════════════

@csrf_exempt
@require_POST
@requiere_token
def rewards_redeem(request):
    """POST /api/lealtad/rewards/redeem — canjea un premio."""
    datos = _cuerpo(request)
    if datos is None:
        return _error("El cuerpo debe ser JSON válido.")

    cliente = None
    if datos.get("customer_id"):
        cliente = Cliente.objects.filter(pk=datos["customer_id"]).first()
    elif datos.get("phone_number"):
        cliente = servicios.buscar_cliente(datos["phone_number"])
    if not cliente:
        return _error("No encontré a ese cliente.", 404)

    premio = Premio.objects.filter(pk=datos.get("reward_id"), activo=True).first()
    if not premio:
        return _error("No encontré ese premio (o está desactivado).", 404)

    try:
        canje = servicios.canjear(cliente, premio)
    except servicios.ErrorLealtad as e:
        return _error(str(e), 409)

    cliente.refresh_from_db()
    return JsonResponse({
        "ok": True,
        "redemption": {
            "id": canje.pk, "code": canje.codigo,
            "reward": premio.nombre, "points_used": canje.puntos_usados,
            "status": canje.estado,
            "expires_at": canje.vence_el.isoformat() if canje.vence_el else None,
        },
        "customer": _cliente_json(cliente),
    }, status=201)


# ══════════════════════════════════════════════════════════════════════════════
#  CAMPAÑAS
# ══════════════════════════════════════════════════════════════════════════════

@csrf_exempt
@require_POST
@requiere_token
def campaigns_send(request):
    """POST /api/lealtad/campaigns/send — crea y encola una campaña."""
    datos = _cuerpo(request)
    if datos is None:
        return _error("El cuerpo debe ser JSON válido.")
    cuerpo = (datos.get("message") or "").strip()
    if not cuerpo:
        return _error("Falta `message`.")

    segmento = datos.get("segment") or Campana.Segmento.TODOS
    if segmento not in Campana.Segmento.values:
        return _error(f"`segment` debe ser uno de: "
                      f"{', '.join(Campana.Segmento.values)}.")

    campana = Campana.objects.create(
        nombre=datos.get("name") or f"Campaña API {timezone.localdate()}",
        cuerpo=cuerpo, segmento=segmento,
    )
    encolados = automatizaciones.enviar_campana(campana)
    return JsonResponse({"ok": True, "campaign_id": campana.pk,
                         "queued": encolados}, status=201)


# ══════════════════════════════════════════════════════════════════════════════
#  WEBHOOK DE WHATSAPP
# ══════════════════════════════════════════════════════════════════════════════

ESTADOS_META = {
    "sent": Mensaje.Estado.ENVIADO,
    "delivered": Mensaje.Estado.ENTREGADO,
    "read": Mensaje.Estado.LEIDO,
    "failed": Mensaje.Estado.FALLIDO,
}

PALABRAS_BAJA = {"baja", "stop", "cancelar", "no molestar", "unsubscribe"}


@csrf_exempt
def webhook_whatsapp(request):
    """Recibe de Meta los estados de entrega y las respuestas del cliente."""
    if request.method == "GET":
        # Verificación inicial del webhook.
        esperado = getattr(settings, "WHATSAPP_VERIFY_TOKEN", "")
        if (request.GET.get("hub.mode") == "subscribe"
                and esperado
                and hmac.compare_digest(request.GET.get("hub.verify_token", ""),
                                        esperado)):
            return HttpResponse(request.GET.get("hub.challenge", ""))
        return HttpResponse("Token de verificación inválido.", status=403)

    datos = _cuerpo(request)
    if datos is None:
        return _error("El cuerpo debe ser JSON válido.")

    for entrada in datos.get("entry", []):
        for cambio in entrada.get("changes", []):
            valor = cambio.get("value", {})
            _procesa_estados(valor.get("statuses", []))
            _procesa_respuestas(valor.get("messages", []))
    return JsonResponse({"ok": True})


def _procesa_estados(estados):
    for estado in estados:
        nuevo = ESTADOS_META.get(estado.get("status"))
        mensaje = Mensaje.objects.filter(
            proveedor_id=estado.get("id", "")).first()
        if not (nuevo and mensaje):
            continue
        # Meta manda los estados en orden, pero llegan por red: no retrocedas.
        orden = [Mensaje.Estado.ENVIADO, Mensaje.Estado.ENTREGADO,
                 Mensaje.Estado.LEIDO]
        if (mensaje.estado in orden and nuevo in orden
                and orden.index(nuevo) < orden.index(mensaje.estado)):
            continue
        mensaje.estado = nuevo
        campos = ["estado"]
        ahora = timezone.now()
        if nuevo == Mensaje.Estado.ENTREGADO and not mensaje.entregado_en:
            mensaje.entregado_en = ahora
            campos.append("entregado_en")
        elif nuevo == Mensaje.Estado.LEIDO and not mensaje.leido_en:
            mensaje.leido_en = ahora
            campos.append("leido_en")
        elif nuevo == Mensaje.Estado.FALLIDO:
            errores = estado.get("errors") or [{}]
            mensaje.error = str(errores[0].get("title", ""))[:200]
            campos.append("error")
        mensaje.save(update_fields=campos)


def _procesa_respuestas(mensajes):
    """Si el cliente pide su baja, se respeta de inmediato."""
    for entrante in mensajes:
        texto = (entrante.get("text", {}).get("body") or "").strip().lower()
        if texto not in PALABRAS_BAJA:
            continue
        cliente = servicios.buscar_cliente(entrante.get("from"))
        if cliente:
            cliente.acepta_mensajes = False
            cliente.save(update_fields=["acepta_mensajes"])
            log.info("Baja de mensajes solicitada por %s", cliente.telefono)
