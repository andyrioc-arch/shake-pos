"""Tarjeta en el wallet del celular.

Google Wallet funciona con un JWT firmado por una cuenta de servicio: el pase se
describe entero dentro del token, así que basta el botón "Guardar" para que la
tarjeta quede en el celular del cliente. Cuando cambian sus puntos, se
sincroniza el objeto por la API para que el pase se actualice solo.

Si no hay credenciales configuradas, todo el módulo se queda callado y la
tarjeta web sigue funcionando igual: nada se rompe por no tener cuenta.

Apple Wallet está cableado pero apagado: necesita el certificado Pass Type ID
del Apple Developer Program.
"""

import base64
import json
import logging
import time
from urllib import error as urlerror
from urllib import request as urlrequest

from django.conf import settings
from django.utils import timezone

from .models import ConfiguracionPrograma, PaseWallet

log = logging.getLogger(__name__)

API = "https://walletobjects.googleapis.com/walletobjects/v1"
SCOPE = "https://www.googleapis.com/auth/wallet_object.issuer"


# ══════════════════════════════════════════════════════════════════════════════
#  CREDENCIALES
# ══════════════════════════════════════════════════════════════════════════════

def _cuenta_servicio():
    """Lee el JSON de la cuenta de servicio de Google, o None si no está."""
    ruta = getattr(settings, "GOOGLE_WALLET_SERVICE_ACCOUNT", "")
    if not ruta:
        return None
    try:
        with open(ruta, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError) as e:
        log.warning("No se pudo leer la cuenta de servicio de Google: %s", e)
        return None


def configurado():
    return bool(getattr(settings, "GOOGLE_WALLET_ISSUER_ID", "")
                and _cuenta_servicio())


def _clase_id():
    issuer = getattr(settings, "GOOGLE_WALLET_ISSUER_ID", "")
    return f"{issuer}.lealtad"


def _objeto_id(cliente):
    issuer = getattr(settings, "GOOGLE_WALLET_ISSUER_ID", "")
    return f"{issuer}.{cliente.token.hex}"


# ══════════════════════════════════════════════════════════════════════════════
#  FIRMA
# ══════════════════════════════════════════════════════════════════════════════

def _b64url(datos):
    return base64.urlsafe_b64encode(datos).rstrip(b"=").decode()


def _firma_rs256(mensaje, llave_pem):
    """Firma con la llave privada de la cuenta de servicio (RS256)."""
    try:
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import padding
    except ImportError as e:   # pragma: no cover - depende del entorno
        raise RuntimeError(
            "Falta el paquete 'cryptography' para firmar los pases de "
            "Google Wallet (pip install -r requirements.txt).") from e

    llave = serialization.load_pem_private_key(llave_pem.encode(), password=None)
    return llave.sign(mensaje, padding.PKCS1v15(), hashes.SHA256())


def _jwt(payload, cuenta):
    cabecera = {"alg": "RS256", "typ": "JWT"}
    sin_firma = f"{_b64url(json.dumps(cabecera).encode())}." \
                f"{_b64url(json.dumps(payload).encode())}"
    firma = _firma_rs256(sin_firma.encode(), cuenta["private_key"])
    return f"{sin_firma}.{_b64url(firma)}"


# ══════════════════════════════════════════════════════════════════════════════
#  EL PASE
# ══════════════════════════════════════════════════════════════════════════════

def _clase(cfg):
    """Plantilla del pase, igual para todos los clientes."""
    return {
        "id": _clase_id(),
        "issuerName": cfg.nombre_negocio,
        "programName": f"Lealtad {cfg.nombre_negocio}",
        "reviewStatus": "UNDER_REVIEW",
        "hexBackgroundColor": "#000000",
    }


def _objeto(cliente, cfg, url_tarjeta):
    nivel = cliente.nivel
    premio = cliente.siguiente_premio
    faltan = max(0, premio.puntos_requeridos - cliente.puntos_saldo) if premio else 0

    return {
        "id": _objeto_id(cliente),
        "classId": _clase_id(),
        "state": "ACTIVE",
        "accountId": cliente.token.hex,
        "accountName": cliente.nombre or cliente.telefono_display,
        "hexBackgroundColor": nivel.color if nivel else "#fa5598",
        "loyaltyPoints": {
            "label": "Puntos",
            "balance": {"int": cliente.puntos_saldo},
        },
        "secondaryLoyaltyPoints": {
            "label": "Nivel",
            "balance": {"string": str(nivel) if nivel else "Inicio"},
        },
        "barcode": {
            "type": "QR_CODE",
            "value": f"CLIENTE:{cliente.codigo}",
            "alternateText": cliente.codigo,
        },
        "textModulesData": [
            {"header": "Siguiente premio",
             "body": (f"{premio.nombre}: te faltan {faltan} puntos"
                      if premio else "¡Ya puedes canjear!"),
             "id": "siguiente"},
        ],
        "linksModuleData": {
            "uris": [{"uri": url_tarjeta, "description": "Ver mi tarjeta",
                      "id": "tarjeta"}],
        },
    }


def link_google(cliente, request=None):
    """Link del botón «Guardar en Google Wallet», o None si no está configurado."""
    cuenta = _cuenta_servicio()
    if not (cuenta and getattr(settings, "GOOGLE_WALLET_ISSUER_ID", "")):
        return None

    cfg = ConfiguracionPrograma.get()
    ruta = cliente.get_absolute_url()
    url = (request.build_absolute_uri(ruta) if request else cfg.url_de(ruta))

    payload = {
        "iss": cuenta["client_email"],
        "aud": "google",
        "typ": "savetowallet",
        "iat": int(time.time()),
        "origins": [],
        "payload": {
            "loyaltyClasses": [_clase(cfg)],
            "loyaltyObjects": [_objeto(cliente, cfg, url)],
        },
    }
    try:
        token = _jwt(payload, cuenta)
    except Exception as e:  # noqa: BLE001 - la tarjeta web no debe caerse
        log.warning("No se pudo firmar el pase de Google Wallet: %s", e)
        return None
    return f"https://pay.google.com/gp/v/save/{token}"


# ══════════════════════════════════════════════════════════════════════════════
#  SINCRONIZACIÓN DE PUNTOS
# ══════════════════════════════════════════════════════════════════════════════

def _token_acceso(cuenta):
    """Cambia la cuenta de servicio por un token de acceso de Google (OAuth2)."""
    ahora = int(time.time())
    afirmacion = _jwt({
        "iss": cuenta["client_email"],
        "scope": SCOPE,
        "aud": "https://oauth2.googleapis.com/token",
        "iat": ahora,
        "exp": ahora + 3600,
    }, cuenta)

    from urllib.parse import urlencode
    datos = urlencode({
        "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
        "assertion": afirmacion,
    }).encode()
    peticion = urlrequest.Request(
        "https://oauth2.googleapis.com/token", data=datos, method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded"})
    with urlrequest.urlopen(peticion, timeout=20) as resp:
        return json.loads(resp.read().decode())["access_token"]


def sincronizar(cliente):
    """Actualiza los puntos del pase que el cliente ya tiene en su celular.

    Silencioso a propósito: si el wallet no está configurado o Google falla,
    la tarjeta web sigue siendo la fuente de verdad.
    """
    pase = PaseWallet.objects.filter(
        cliente=cliente, proveedor=PaseWallet.Proveedor.GOOGLE).first()
    if not pase or not configurado():
        return None

    cuenta = _cuenta_servicio()
    cfg = ConfiguracionPrograma.get()
    cuerpo = _objeto(cliente, cfg, cfg.url_de(cliente.get_absolute_url()))
    datos = json.dumps(cuerpo).encode()
    peticion = urlrequest.Request(
        f"{API}/loyaltyObject/{_objeto_id(cliente)}", data=datos, method="PATCH",
        headers={"Content-Type": "application/json"})
    try:
        peticion.add_header("Authorization", f"Bearer {_token_acceso(cuenta)}")
        with urlrequest.urlopen(peticion, timeout=20):
            pase.sincronizado = timezone.now()
            pase.error = ""
    except (urlerror.URLError, KeyError, ValueError, RuntimeError) as e:
        pase.error = str(e)[:200]
        log.warning("No se pudo sincronizar el pase de %s: %s", cliente, e)
    pase.save(update_fields=["sincronizado", "error"])
    return pase
