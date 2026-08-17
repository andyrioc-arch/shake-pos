"""Paneles del programa de lealtad y las pantallas públicas del cliente."""

from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.contrib.admin.models import ADDITION, CHANGE, DELETION, LogEntry
from django.contrib.auth.decorators import login_required, user_passes_test
from django.core.cache import cache
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.db import IntegrityError
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.text import slugify
from django.views.decorators.http import require_POST

from inventario.models import Receta
from inventario.views import _qr_svg
from . import automatizaciones, mensajeria, metricas, servicios, wallet
from .models import (
    Automatizacion, Campana, Canje, Cliente, ConfiguracionPrograma,
    DescuentoCliente, Mensaje, Nivel, Plantilla, Premio, PromocionPuntos,
    TelefonoInvalido, resuelve_id,
)

solo_super = user_passes_test(lambda u: u.is_superuser, login_url='/')


def _log(request, obj, flag, mensaje):
    LogEntry.objects.log_actions(
        user_id=request.user.pk, queryset=[obj], action_flag=flag,
        change_message=mensaje, single_object=True,
    )


def _decimal(valor, defecto=None):
    try:
        return Decimal(str(valor).replace(",", "").strip())
    except (InvalidOperation, AttributeError, TypeError, ValueError):
        return defecto


def _entero(valor, defecto=0, minimo=None, maximo=None):
    """Entero del formulario, acotado. Los campos `Positive*` no aceptan
    negativos y guardarlos revienta con IntegrityError."""
    try:
        numero = int(valor)
    except (TypeError, ValueError):
        numero = defecto
    if minimo is not None:
        numero = max(minimo, numero)
    if maximo is not None:
        numero = min(maximo, numero)
    return numero


def _fecha(valor):
    try:
        return date.fromisoformat(valor)
    except (TypeError, ValueError):
        return None


def _destino(request, defecto):
    """A dónde volver tras un POST, sin permitir salir a otro dominio."""
    volver = request.POST.get("volver") or ""
    if volver and url_has_allowed_host_and_scheme(
            volver, allowed_hosts={request.get_host()},
            require_https=request.is_secure()):
        return volver
    return defecto


def _opcion_valida(valor, choices, defecto):
    """Un valor de `<select>` que de verdad exista en las opciones del modelo.

    Sin esto se puede guardar un disparador inventado: la automatización se ve
    activa en el panel y no dispara nunca, en silencio.
    """
    return valor if valor in {v for v, _ in choices} else defecto


# ══════════════════════════════════════════════════════════════════════════════
#  PANEL GENERAL
# ══════════════════════════════════════════════════════════════════════════════

@login_required
def panel(request):
    cfg = ConfiguracionPrograma.get()
    es_super = request.user.is_superuser
    ctx = {
        "active": "lealtad",
        "seccion": "panel",
        "cfg": cfg,
        "clientes": metricas.resumen_clientes(cfg),
        "puntos": metricas.resumen_puntos(),
        # Margen y mensajería solo se pintan para el dueño, así que para el
        # cajero ni se calculan: la plantilla tiraba el resultado. No es
        # trabajo barato — `resumen_retorno` recorre los canjes uno por uno
        # pidiéndole el costo a cada quien. Mismo patrón que la alarma de
        # margen en `inventario/views.py`.
        "retorno": metricas.resumen_retorno() if es_super else None,
        "marketing": metricas.resumen_marketing() if es_super else None,
        "top": metricas.top_clientes(),
        "cerca": metricas.cerca_de_premio(),
        "canjes_pendientes": (Canje.objects
                              .filter(estado=Canje.Estado.PENDIENTE)
                              .select_related("cliente", "premio")[:10]),
        "promo": PromocionPuntos.vigente(),
        "es_super": es_super,
    }
    return render(request, "lealtad/panel.html", ctx)


@login_required
@solo_super
def panel_metricas(request):
    """Solo el dueño: LTV, margen de miembros y utilidad neta.

    Aquí sí se cierra la puerta, no se esconde el contenido: la pantalla entera
    es dinero y el cajero no tiene nada que hacer en ella. El menú ya la
    escondía, pero esconder no es proteger — la URL respondía 200.
    """
    comportamiento = metricas.resumen_comportamiento()
    ctx = {
        "active": "lealtad",
        "seccion": "metricas",
        "retenciones": [
            ("A 30 días", comportamiento["retencion_30"]),
            ("A 60 días", comportamiento["retencion_60"]),
            ("A 90 días", comportamiento["retencion_90"]),
        ],
        "comportamiento": comportamiento,
        "retorno": metricas.resumen_retorno(),
        "puntos": metricas.resumen_puntos(),
        "clientes": metricas.resumen_clientes(),
        "costo_punto": metricas.costo_por_punto_promedio(),
    }
    return render(request, "lealtad/metricas.html", ctx)


@login_required
@solo_super
def panel_marketing(request):
    """Solo el dueño: ventas atribuidas por campaña, en pesos."""
    # Acotado a 10 años: un timedelta gigante revienta con OverflowError.
    dias = _entero(request.GET.get("dias"), 30, minimo=0, maximo=3650)
    desde = timezone.localdate() - timedelta(days=dias) if dias else None
    ctx = {
        "active": "lealtad",
        "seccion": "marketing",
        "dias": dias,
        "resumen": metricas.resumen_marketing(desde),
        "desglose": metricas.desglose_marketing(desde),
        "recientes": (Mensaje.objects.select_related(
            "cliente", "automatizacion", "campana")[:40]),
    }
    return render(request, "lealtad/marketing.html", ctx)


# ══════════════════════════════════════════════════════════════════════════════
#  CLIENTES
# ══════════════════════════════════════════════════════════════════════════════

@login_required
def clientes(request):
    busqueda = (request.GET.get("q") or "").strip()
    filtro = request.GET.get("estado") or ""

    qs = Cliente.objects.all()
    if busqueda:
        encontrado = servicios.buscar_cliente(busqueda)
        if encontrado:
            return redirect("lealtad_cliente", pk=encontrado.pk)
        qs = qs.filter(nombre__icontains=busqueda)
    if filtro:
        qs = qs.filter(estado=filtro)

    ctx = {
        "active": "lealtad",
        "seccion": "clientes",
        "clientes": qs.order_by("-ultima_compra", "-alta")[:200],
        "total": qs.count(),
        "q": busqueda,
        "estado": filtro,
        "estados": Cliente.Estado.choices,
        "es_super": request.user.is_superuser,
    }
    return render(request, "lealtad/clientes.html", ctx)


@login_required
def cliente_detalle(request, pk):
    cliente = get_object_or_404(Cliente, pk=pk)
    catalogo = [{"premio": p, "puede": p.puede_canjearlo(cliente),
                 "faltan": max(0, p.puntos_requeridos - cliente.puntos_saldo)}
                for p in Premio.objects.filter(activo=True)
                .select_related("receta", "nivel_minimo")]
    siguiente = cliente.siguiente_nivel

    ctx = {
        "active": "lealtad",
        "seccion": "clientes",
        "cliente": cliente,
        "nivel": cliente.nivel,
        "siguiente": siguiente,
        "falta_nivel": (siguiente.puntos_requeridos - cliente.puntos_historicos)
                       if siguiente else 0,
        "catalogo": catalogo,
        "compras": cliente.compras.select_related("nota")[:30],
        "movimientos": cliente.movimientos.all()[:40],
        "canjes": cliente.canjes.select_related("premio")[:20],
        "mensajes": cliente.mensajes.select_related("automatizacion")[:20],
        "url_tarjeta": request.build_absolute_uri(cliente.get_absolute_url()),
        "es_super": request.user.is_superuser,
    }
    return render(request, "lealtad/cliente.html", ctx)


@require_POST
@login_required
def cliente_crear(request):
    try:
        cliente, creado = servicios.alta_cliente(
            request.POST.get("telefono"),
            nombre=request.POST.get("nombre", ""),
            cumpleanos=_fecha(request.POST.get("cumpleanos")),
            origen=Cliente.Origen.MANUAL,
        )
    except TelefonoInvalido as e:
        messages.error(request, str(e))
        return redirect("lealtad_clientes")

    if creado:
        _log(request, cliente, ADDITION, "Dio de alta un cliente de lealtad")
        messages.success(request, f"Cliente {cliente.telefono_display} registrado.")
    else:
        messages.info(request, "Ese número ya estaba registrado.")
    return redirect("lealtad_cliente", pk=cliente.pk)


@require_POST
@login_required
def cliente_editar(request, pk):
    cliente = get_object_or_404(Cliente, pk=pk)
    cliente.nombre = request.POST.get("nombre", "").strip()
    cliente.cumpleanos = _fecha(request.POST.get("cumpleanos"))
    cliente.acepta_mensajes = bool(request.POST.get("acepta_mensajes"))
    cliente.notas = request.POST.get("notas", "").strip()
    cliente.save(update_fields=["nombre", "cumpleanos", "acepta_mensajes", "notas"])
    _log(request, cliente, CHANGE, "Editó los datos del cliente")
    messages.success(request, "Datos del cliente actualizados.")
    return redirect("lealtad_cliente", pk=pk)


@require_POST
@login_required
def cliente_canjear(request, pk):
    cliente = get_object_or_404(Cliente, pk=pk)
    premio = resuelve_id(Premio, request.POST.get("premio"))
    if not premio:
        messages.error(request, "Elige el premio que quieres canjear.")
        return redirect("lealtad_cliente", pk=pk)
    try:
        canje = servicios.canjear(cliente, premio, usuario=request.user)
    except servicios.ErrorLealtad as e:
        messages.error(request, str(e))
        return redirect("lealtad_cliente", pk=pk)

    cliente.refresh_from_db()
    _log(request, canje, ADDITION, f"Canjeó «{premio.nombre}»")
    messages.success(
        request,
        f"Canje {canje.codigo}: {premio.nombre}. Se descontaron "
        f"{canje.puntos_usados} puntos, le quedan {cliente.puntos_saldo}.")
    return redirect("lealtad_cliente", pk=pk)


@require_POST
@login_required
def canje_entregar(request, pk):
    canje = get_object_or_404(Canje, pk=pk)
    try:
        servicios.entregar_canje(canje, usuario=request.user)
    except servicios.ErrorLealtad as e:
        messages.error(request, str(e))
    else:
        _log(request, canje, CHANGE, "Entregó el premio")
        messages.success(request, f"Premio «{canje.premio.nombre}» entregado.")
    return redirect(_destino(request, "lealtad_panel"))


@require_POST
@login_required
def canje_cancelar(request, pk):
    canje = get_object_or_404(Canje, pk=pk)
    try:
        servicios.cancelar_canje(canje)
    except servicios.ErrorLealtad as e:
        messages.error(request, str(e))
    else:
        _log(request, canje, CHANGE, "Canceló el canje y devolvió los puntos")
        messages.success(
            request, f"Canje cancelado. Se devolvieron {canje.puntos_usados} puntos.")
    return redirect(_destino(request, "lealtad_panel"))


@require_POST
@login_required
@solo_super
def cliente_ajustar(request, pk):
    cliente = get_object_or_404(Cliente, pk=pk)
    puntos = _entero(request.POST.get("puntos"))
    motivo = request.POST.get("motivo", "").strip() or "Ajuste manual"
    try:
        # Devuelve el cliente recargado: `ajustar_puntos` bloquea la fila y
        # trabaja sobre su propia copia, así que la de aquí queda vieja.
        cliente = servicios.ajustar_puntos(cliente, puntos, motivo,
                                           usuario=request.user)
    except servicios.ErrorLealtad as e:
        messages.error(request, str(e))
    else:
        _log(request, cliente, CHANGE, f"Ajustó {puntos:+d} puntos: {motivo}")
        messages.success(request,
                         f"Ajuste aplicado. Saldo: {cliente.puntos_saldo} puntos.")
    return redirect("lealtad_cliente", pk=pk)


@login_required
def clientes_sugerencias(request):
    """Sugiere clientes por nombre, para elegir de una lista.

    Devuelve IDENTIDAD y nada más: nombre, celular y código. Ni puntos ni
    descuento ni gasto — para eso está `buscar`, que resuelve a un cliente ya
    elegido. Separarlos evita que teclear tres letras devuelva un listado del
    padrón con el dinero de cada quien.

    El celular viaja para distinguir a dos clientes con el mismo nombre, que es
    el problema entero: sin él, elegir «Juan» no significa nada.
    """
    clientes = servicios.sugerir_clientes(request.GET.get("q"))
    return JsonResponse({"clientes": [
        {"id": c.pk,
         "nombre": c.nombre or "Sin nombre",
         "telefono": c.telefono_display,
         "codigo": c.codigo}
        for c in clientes]})


@login_required
def buscar(request):
    """Busca un cliente por teléfono o código. Lo usa el formulario de venta.

    Excepción consciente a la regla de que el descuento es del dueño: el panel
    que lo administra sí exige superusuario, pero aquí el cajero necesita el
    porcentaje para cobrar bien. Cerrar esta puerta rompe el cobro, así que se
    queda abierta y se deja anotado por qué.
    """
    # Por `id` cuando la caja lo eligió de la lista, por `q` cuando se tecleó
    # el celular o el código. Las dos vías desembocan en el mismo cliente y en
    # la misma respuesta, así que la pantalla solo tiene una cosa que pintar.
    cliente = (resuelve_id(Cliente, request.GET.get("id"))
               or servicios.buscar_cliente(request.GET.get("q")))
    if not cliente:
        return JsonResponse({"encontrado": False, "necesita_nombre": True})
    nivel = cliente.nivel
    descuento = cliente.descuento_vigente
    return JsonResponse({
        "encontrado": True,
        "id": cliente.pk,
        "nombre": cliente.nombre or "",
        "necesita_nombre": not cliente.nombre,
        "telefono": cliente.telefono_display,
        "puntos": cliente.puntos_saldo,
        "nivel": str(nivel) if nivel else "",
        "premios": [p.nombre for p in cliente.premios_disponibles()],
        # El descuento pactado, para que el mostrador no dependa de que alguien
        # se acuerde. Va como número y como texto: el número lo aplica la caja,
        # el texto lo lee el cajero.
        "descuento": (str(descuento.porcentaje) if descuento else None),
        "descuento_motivo": (descuento.descripcion if descuento else ""),
        "url": cliente.get_absolute_url(),
    })


# ══════════════════════════════════════════════════════════════════════════════
#  DESCUENTOS POR CLIENTE
# ══════════════════════════════════════════════════════════════════════════════

@login_required
@solo_super
def descuentos(request):
    """Los descuentos pactados, con su vigencia. Solo el dueño: es dinero."""
    filas = (DescuentoCliente.objects.select_related("cliente")
             .order_by("-activo", "-desde", "-id"))
    return render(request, "lealtad/descuentos.html", {
        "active": "lealtad",
        "seccion": "descuentos",
        "descuentos": filas,
        "vigentes": sum(1 for d in filas if d.estado == "vigente"),
    })


@require_POST
@login_required
@solo_super
def descuento_guardar(request):
    pk = request.POST.get("pk")
    descuento = resuelve_id(DescuentoCliente, pk) if pk else DescuentoCliente()
    if pk and not descuento:
        messages.error(request, "Ese descuento ya no existe.")
        return redirect("lealtad_descuentos")

    # El cliente se ELIGE de una lista, no se teclea. Se escribe el nombre, que
    # es lo que uno recuerda, pero lo que viaja es el cliente concreto: el
    # nombre no identifica a nadie —hay tres Juanes— y el celular sí, solo que
    # nadie se lo sabe de memoria. Escribir por nombre y guardar por id resuelve
    # las dos cosas a la vez.
    #
    # Se sigue aceptando el celular o el código tecleados, que es como se hacía
    # antes y como llega desde cualquier otro sitio.
    if not descuento.pk:
        cliente = (resuelve_id(Cliente, request.POST.get("cliente_id"))
                   or servicios.buscar_cliente(request.POST.get("cliente")))
        if not cliente:
            messages.error(
                request,
                "No encontré a ese cliente. Escribe su nombre y elígelo de la "
                "lista; si es nuevo, dalo de alta en Clientes.")
            return redirect("lealtad_descuentos")
        descuento.cliente = cliente

    # `is_finite()` antes de comparar: `Decimal("NaN")` se construye sin
    # quejarse y revienta con InvalidOperation en cuanto se compara con `<`,
    # que sería un 500 en vez de este mensaje. Misma guarda que `api._monto`.
    porcentaje = _decimal(request.POST.get("porcentaje"))
    if porcentaje is not None and not porcentaje.is_finite():
        porcentaje = None
    if porcentaje is None or not (Decimal("0") < porcentaje <= Decimal("100")):
        messages.error(request, "El descuento debe estar entre 0 y 100%.")
        return redirect("lealtad_descuentos")

    desde = _fecha(request.POST.get("desde")) or timezone.localdate()
    hasta = _fecha(request.POST.get("hasta"))
    # Una vigencia al revés no se guarda: el descuento se vería en la lista y
    # no aplicaría nunca, que es la peor de las dos formas de fallar.
    if hasta and hasta < desde:
        messages.error(request, "La fecha de fin no puede ser antes de la de inicio.")
        return redirect("lealtad_descuentos")

    descuento.porcentaje = porcentaje
    descuento.descripcion = request.POST.get("descripcion", "").strip()[:200]
    descuento.desde = desde
    descuento.hasta = hasta
    descuento.activo = bool(request.POST.get("activo"))
    descuento.save()

    _log(request, descuento, CHANGE if pk else ADDITION,
         "Editó un descuento" if pk else "Creó un descuento")
    messages.success(
        request,
        f"Descuento de {descuento.porcentaje}% guardado para "
        f"{descuento.cliente.nombre_corto}.")
    return redirect("lealtad_descuentos")


@require_POST
@login_required
@solo_super
def descuento_eliminar(request, pk):
    descuento = get_object_or_404(DescuentoCliente, pk=pk)
    _log(request, descuento, DELETION, "Eliminó un descuento")
    nombre = descuento.cliente.nombre_corto
    descuento.delete()
    messages.success(request, f"Descuento de {nombre} eliminado.")
    return redirect("lealtad_descuentos")


# ══════════════════════════════════════════════════════════════════════════════
#  PREMIOS Y NIVELES
# ══════════════════════════════════════════════════════════════════════════════

@login_required
@solo_super
def premios(request):
    ctx = {
        "active": "lealtad",
        "seccion": "premios",
        "premios": (Premio.objects.select_related("receta", "nivel_minimo")
                    .order_by("orden", "puntos_requeridos")),
        "niveles": Nivel.objects.all(),
        "recetas": Receta.objects.filter(activa=True).order_by("nombre"),
        "tipos": Premio.Tipo.choices,
        "cfg": ConfiguracionPrograma.get(),
        "promociones": PromocionPuntos.objects.all()[:10],
        "costo_punto": metricas.costo_por_punto_promedio(),
    }
    return render(request, "lealtad/premios.html", ctx)


@require_POST
@login_required
@solo_super
def premio_guardar(request):
    pk = request.POST.get("pk")
    premio = resuelve_id(Premio, pk) if pk else Premio()
    if pk and not premio:
        messages.error(request, "Ese premio ya no existe.")
        return redirect("lealtad_premios")

    nombre = request.POST.get("nombre", "").strip()
    if not nombre:
        messages.error(request, "El premio necesita un nombre.")
        return redirect("lealtad_premios")

    premio.nombre = nombre
    premio.emoji = request.POST.get("emoji", "").strip()
    premio.descripcion = request.POST.get("descripcion", "").strip()
    premio.puntos_requeridos = _entero(
        request.POST.get("puntos_requeridos"), minimo=0)
    premio.tipo = _opcion_valida(request.POST.get("tipo"), Premio.Tipo.choices,
                                 Premio.Tipo.PRODUCTO)
    premio.receta = resuelve_id(Receta, request.POST.get("receta"))
    premio.cantidad = _entero(request.POST.get("cantidad"), 1, minimo=1)
    premio.valor = _decimal(request.POST.get("valor"), Decimal("0"))
    premio.nivel_minimo = resuelve_id(Nivel, request.POST.get("nivel_minimo"))
    premio.limite_por_cliente = _entero(
        request.POST.get("limite_por_cliente"), minimo=0)
    premio.vigencia_dias = _entero(request.POST.get("vigencia_dias"), 30, minimo=0)
    premio.activo = bool(request.POST.get("activo"))

    # Un premio de producto sin receta no se puede entregar: no hay de dónde
    # descontar el inventario. Se atrapa al configurarlo y no en el mostrador
    # con el cliente enfrente.
    if premio.consume_inventario and not premio.receta_id:
        messages.error(
            request,
            f"«{premio.nombre}» entrega un producto, así que necesita una "
            "receta ligada; si no, no se puede descontar del inventario.")
        return redirect("lealtad_premios")

    try:
        premio.save()
    except IntegrityError:
        messages.error(request, f"Ya existe un premio llamado «{nombre}».")
        return redirect("lealtad_premios")

    _log(request, premio, CHANGE if pk else ADDITION,
         "Editó un premio" if pk else "Creó un premio")
    messages.success(request, f"Premio «{premio.nombre}» guardado.")
    return redirect("lealtad_premios")


@require_POST
@login_required
@solo_super
def premio_eliminar(request, pk):
    premio = get_object_or_404(Premio, pk=pk)
    if premio.canjes.exists():
        premio.activo = False
        premio.save(update_fields=["activo"])
        messages.info(request,
                      f"«{premio.nombre}» ya tiene canjes, así que se desactivó "
                      f"en vez de borrarse (para no perder el historial).")
    else:
        _log(request, premio, DELETION, "Eliminó un premio")
        premio.delete()
        messages.success(request, "Premio eliminado.")
    return redirect("lealtad_premios")


@require_POST
@login_required
@solo_super
def nivel_guardar(request):
    pk = request.POST.get("pk")
    nivel = resuelve_id(Nivel, pk) if pk else Nivel()
    nombre = request.POST.get("nombre", "").strip()
    if not nombre:
        messages.error(request, "El nivel necesita un nombre.")
        return redirect("lealtad_premios")

    nivel.nombre = nombre
    nivel.emoji = request.POST.get("emoji", "").strip()
    nivel.puntos_requeridos = _entero(
        request.POST.get("puntos_requeridos"), minimo=0)
    nivel.beneficios = request.POST.get("beneficios", "").strip()
    nivel.color = request.POST.get("color") or "#fa5598"
    nivel.activo = bool(request.POST.get("activo"))
    try:
        nivel.save()
    except IntegrityError:
        messages.error(request, f"Ya existe un nivel llamado «{nombre}».")
        return redirect("lealtad_premios")

    _log(request, nivel, CHANGE if pk else ADDITION,
         "Editó un nivel" if pk else "Creó un nivel")
    messages.success(request, f"Nivel «{nivel.nombre}» guardado.")
    return redirect("lealtad_premios")


@require_POST
@login_required
@solo_super
def nivel_eliminar(request, pk):
    nivel = get_object_or_404(Nivel, pk=pk)
    _log(request, nivel, DELETION, "Eliminó un nivel")
    nivel.delete()
    messages.success(request, "Nivel eliminado.")
    return redirect("lealtad_premios")


@require_POST
@login_required
@solo_super
def configuracion_guardar(request):
    cfg = ConfiguracionPrograma.get()
    cfg.nombre_negocio = request.POST.get("nombre_negocio") or cfg.nombre_negocio
    cfg.pesos_por_punto = _decimal(request.POST.get("pesos_por_punto"),
                                   cfg.pesos_por_punto)
    cfg.vigencia_puntos_meses = _entero(request.POST.get("vigencia_puntos_meses"),
                                        cfg.vigencia_puntos_meses, minimo=0)
    cfg.dias_inactividad = _entero(request.POST.get("dias_inactividad"),
                                   cfg.dias_inactividad, minimo=1)
    cfg.activo = bool(request.POST.get("activo"))
    if cfg.pesos_por_punto is None or not cfg.pesos_por_punto.is_finite() \
            or cfg.pesos_por_punto <= 0:
        messages.error(request, "Los pesos por punto deben ser mayores a cero.")
        return redirect(_destino(request, "lealtad_premios"))
    cfg.save()
    messages.success(request, "Configuración del programa guardada.")
    return redirect(_destino(request, "lealtad_premios"))


@require_POST
@login_required
@solo_super
def promocion_guardar(request):
    desde = _fecha(request.POST.get("desde"))
    hasta = _fecha(request.POST.get("hasta"))
    nombre = request.POST.get("nombre", "").strip()
    if not (nombre and desde and hasta):
        messages.error(request, "La promoción necesita nombre y fechas.")
    elif hasta < desde:
        messages.error(request, "La fecha final no puede ser antes de la inicial.")
    else:
        promo = PromocionPuntos.objects.create(
            nombre=nombre, desde=desde, hasta=hasta,
            multiplicador=_decimal(request.POST.get("multiplicador"), Decimal("2")),
            activa=True)
        _log(request, promo, ADDITION, "Creó una promoción de puntos")
        messages.success(request, f"Promoción «{nombre}» activada.")
    return redirect("lealtad_premios")


@require_POST
@login_required
@solo_super
def promocion_eliminar(request, pk):
    promo = get_object_or_404(PromocionPuntos, pk=pk)
    _log(request, promo, DELETION, "Eliminó una promoción de puntos")
    promo.delete()
    messages.success(request, "Promoción eliminada.")
    return redirect("lealtad_premios")


# ══════════════════════════════════════════════════════════════════════════════
#  MENSAJES Y AUTOMATIZACIONES
# ══════════════════════════════════════════════════════════════════════════════

@login_required
@solo_super
def mensajes(request):
    ctx = {
        "active": "lealtad",
        "seccion": "mensajes",
        "cfg": ConfiguracionPrograma.get(),
        "automatizaciones": (Automatizacion.objects
                             .select_related("plantilla", "nivel_objetivo")),
        "plantillas": Plantilla.objects.all(),
        "campanas": Campana.objects.all()[:15],
        "bandeja": (Mensaje.objects
                    .select_related("cliente", "automatizacion", "campana")[:50]),
        "pendientes": Mensaje.objects.filter(
            estado=Mensaje.Estado.PROGRAMADO).count(),
        "disparadores": Automatizacion.Disparador.choices,
        "segmentos": Campana.Segmento.choices,
        "niveles": Nivel.objects.all(),
        "dias_semana": [("0", "Lun"), ("1", "Mar"), ("2", "Mié"), ("3", "Jue"),
                        ("4", "Vie"), ("5", "Sáb"), ("6", "Dom")],
        "en_horario": mensajeria.en_horario(),
    }
    return render(request, "lealtad/mensajes.html", ctx)


@require_POST
@login_required
@solo_super
def automatizacion_guardar(request):
    pk = request.POST.get("pk")
    auto = resuelve_id(Automatizacion, pk) if pk else Automatizacion()
    plantilla = resuelve_id(Plantilla, request.POST.get("plantilla"))
    nombre = request.POST.get("nombre", "").strip()
    if not (nombre and plantilla):
        messages.error(request, "La automatización necesita nombre y plantilla.")
        return redirect("lealtad_mensajes")

    disparador = _opcion_valida(
        request.POST.get("disparador"), Automatizacion.Disparador.choices, None)
    if not disparador:
        messages.error(request, "Elige cuándo se dispara la automatización.")
        return redirect("lealtad_mensajes")

    auto.nombre = nombre
    auto.disparador = disparador
    auto.plantilla = plantilla
    auto.umbral_porcentaje = _entero(request.POST.get("umbral_porcentaje"), 80,
                                     minimo=1, maximo=100)
    auto.dias = _entero(request.POST.get("dias"), 30, minimo=0)
    auto.nivel_objetivo = resuelve_id(Nivel, request.POST.get("nivel_objetivo"))
    auto.hora_envio = _entero(request.POST.get("hora_envio"), 11,
                              minimo=0, maximo=23)
    # Solo días de la semana reales: un valor inventado nunca coincidiría con
    # `weekday()` y la regla no se dispararía jamás.
    dias_validos = [d for d in request.POST.getlist("dias_semana") if d in "0123456"]
    auto.dias_semana = "".join(sorted(set(dias_validos))) or "0123456"
    auto.no_repetir_dias = _entero(request.POST.get("no_repetir_dias"), minimo=0)
    auto.max_por_cliente = _entero(request.POST.get("max_por_cliente"), minimo=0)
    auto.activa = bool(request.POST.get("activa"))
    auto.save()

    _log(request, auto, CHANGE if pk else ADDITION,
         "Editó una automatización" if pk else "Creó una automatización")
    messages.success(request, f"Automatización «{auto.nombre}» guardada.")
    return redirect("lealtad_mensajes")


@require_POST
@login_required
@solo_super
def automatizacion_alternar(request, pk):
    auto = get_object_or_404(Automatizacion, pk=pk)
    auto.activa = not auto.activa
    auto.save(update_fields=["activa"])
    _log(request, auto, CHANGE,
         "Activó la automatización" if auto.activa else "Apagó la automatización")
    messages.success(
        request, f"«{auto.nombre}» {'activada' if auto.activa else 'apagada'}.")
    return redirect("lealtad_mensajes")


@require_POST
@login_required
@solo_super
def automatizacion_eliminar(request, pk):
    auto = get_object_or_404(Automatizacion, pk=pk)
    _log(request, auto, DELETION, "Eliminó una automatización")
    auto.delete()
    messages.success(request, "Automatización eliminada.")
    return redirect("lealtad_mensajes")


@require_POST
@login_required
@solo_super
def plantilla_guardar(request):
    pk = request.POST.get("pk")
    plantilla = resuelve_id(Plantilla, pk) if pk else Plantilla()
    nombre = request.POST.get("nombre", "").strip()
    cuerpo = request.POST.get("cuerpo", "").strip()
    if not (nombre and cuerpo):
        messages.error(request, "La plantilla necesita nombre y texto.")
        return redirect("lealtad_mensajes")

    plantilla.nombre = nombre
    plantilla.cuerpo = cuerpo
    plantilla.canal = request.POST.get("canal") or Plantilla.Canal.WHATSAPP
    plantilla.plantilla_meta = request.POST.get("plantilla_meta", "").strip()
    plantilla.activa = bool(request.POST.get("activa"))
    if not plantilla.clave:
        base = slugify(nombre)[:45] or "plantilla"
        clave, n = base, 2
        while Plantilla.objects.filter(clave=clave).exists():
            clave, n = f"{base}-{n}", n + 1
        plantilla.clave = clave
    plantilla.save()

    _log(request, plantilla, CHANGE if pk else ADDITION,
         "Editó una plantilla" if pk else "Creó una plantilla")
    messages.success(request, f"Plantilla «{plantilla.nombre}» guardada.")
    return redirect("lealtad_mensajes")


@require_POST
@login_required
@solo_super
def campana_guardar(request):
    nombre = request.POST.get("nombre", "").strip()
    cuerpo = request.POST.get("cuerpo", "").strip()
    plantilla = resuelve_id(Plantilla, request.POST.get("plantilla"))
    if not nombre or not (cuerpo or plantilla):
        messages.error(request, "La campaña necesita nombre y un mensaje.")
        return redirect("lealtad_mensajes")

    cuando = request.POST.get("programada_para")
    programada = None
    if cuando:
        try:
            programada = timezone.make_aware(datetime.fromisoformat(cuando))
        except (ValueError, TypeError):
            programada = None

    campana = Campana.objects.create(
        nombre=nombre, cuerpo=cuerpo, plantilla=plantilla,
        segmento=request.POST.get("segmento") or Campana.Segmento.TODOS,
        nivel=resuelve_id(Nivel, request.POST.get("nivel")),
        programada_para=programada,
        estado=(Campana.Estado.PROGRAMADA if programada
                else Campana.Estado.BORRADOR),
    )
    _log(request, campana, ADDITION, "Creó una campaña")
    cuantos = automatizaciones.destinatarios(campana).count()
    messages.success(request,
                     f"Campaña «{nombre}» guardada. Alcanza a {cuantos} cliente(s).")
    return redirect("lealtad_mensajes")


@require_POST
@login_required
@solo_super
def campana_enviar(request, pk):
    campana = get_object_or_404(Campana, pk=pk)
    cuantos = automatizaciones.enviar_campana(campana)
    _log(request, campana, CHANGE, "Envió una campaña")
    messages.success(request,
                     f"Campaña «{campana.nombre}»: {cuantos} mensaje(s) en cola.")
    return redirect("lealtad_mensajes")


@require_POST
@login_required
@solo_super
def bandeja_despachar(request):
    enviados, fallidos, pospuestos = mensajeria.despachar_pendientes(
        forzar_horario=True)
    cfg = ConfiguracionPrograma.get()
    if cfg.proveedor_mensajes == ConfiguracionPrograma.Proveedor.SIMULADO:
        messages.info(
            request,
            f"Modo simulado: {enviados} mensaje(s) se marcaron como enviados "
            f"sin salir de verdad. Cambia el proveedor en la configuración "
            f"para mandarlos por WhatsApp.")
    else:
        texto = f"{enviados} mensaje(s) enviados."
        if fallidos:
            texto += f" {fallidos} fallaron."
        if pospuestos:
            texto += f" {pospuestos} se reintentarán."
        messages.success(request, texto)
    return redirect("lealtad_mensajes")


@require_POST
@login_required
@solo_super
def mensaje_cancelar(request, pk):
    mensaje = get_object_or_404(Mensaje, pk=pk)
    mensaje.estado = Mensaje.Estado.CANCELADO
    mensaje.save(update_fields=["estado"])
    messages.success(request, "Mensaje cancelado.")
    return redirect("lealtad_mensajes")


@require_POST
@login_required
@solo_super
def mensaje_reintentar(request, pk):
    mensaje = get_object_or_404(Mensaje, pk=pk)
    mensaje.estado = Mensaje.Estado.PROGRAMADO
    mensaje.intentos = 0
    mensaje.programado_para = timezone.now()
    mensaje.save(update_fields=["estado", "intentos", "programado_para"])
    messages.success(request, "Mensaje puesto de nuevo en la cola.")
    return redirect("lealtad_mensajes")


# ══════════════════════════════════════════════════════════════════════════════
#  PÚBLICO: ALTA Y TARJETA DIGITAL
# ══════════════════════════════════════════════════════════════════════════════

#: Altas por IP antes de empezar a rechazar, y en cuántos segundos.
ALTAS_MAX = 5
ALTAS_VENTANA = 3600


def _ip(request):
    reenviada = request.META.get("HTTP_X_FORWARDED_FOR", "")
    if reenviada:
        return reenviada.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR", "")


def unete(request):
    """Alta autoservicio: el cliente escanea el QR del mostrador y se registra.

    Ojo con la privacidad: aquí no se verifica que el número sea de quien lo
    teclea (no hay código por SMS). Por eso un número YA registrado nunca
    devuelve la tarjeta — si lo hiciera, cualquiera podría ver los puntos, el
    nombre y el historial de otra persona con solo saberse su celular.
    """
    cfg = ConfiguracionPrograma.get()
    base = {"cfg": cfg, "niveles": Nivel.objects.filter(activo=True),
            "premios": Premio.objects.filter(activo=True)[:4]}
    if request.method != "POST":
        return render(request, "lealtad/unete.html", base)

    if not request.POST.get("acepta"):
        return render(request, "lealtad/unete.html", {
            **base, "datos": request.POST,
            "error": "Necesitamos tu autorización para mandarte tus puntos "
                     "y promociones."})

    llave = f"lealtad:altas:{_ip(request)}"
    if cache.get(llave, 0) >= ALTAS_MAX:
        return render(request, "lealtad/unete.html", {
            **base, "datos": request.POST,
            "error": "Demasiados registros desde este dispositivo. "
                     "Inténtalo más tarde o pídenos ayuda en el mostrador."})

    try:
        cliente, creado = servicios.alta_cliente(
            request.POST.get("telefono"),
            nombre=request.POST.get("nombre", ""),
            cumpleanos=_fecha(request.POST.get("cumpleanos")),
            origen=Cliente.Origen.AUTOSERVICIO,
        )
    except TelefonoInvalido as e:
        return render(request, "lealtad/unete.html",
                      {**base, "datos": request.POST, "error": str(e)})

    cache.set(llave, cache.get(llave, 0) + 1, ALTAS_VENTANA)

    if not creado:
        # Ese número ya estaba registrado y no podemos comprobar que sea suyo.
        return render(request, "lealtad/unete_ya_registrado.html", base)
    return redirect("lealtad_tarjeta", token=cliente.token)


def tarjeta(request, token):
    """La tarjeta digital del cliente. Pública, sin contraseña, por link o QR."""
    cliente = get_object_or_404(Cliente, token=token)
    nivel = cliente.nivel
    siguiente = cliente.siguiente_nivel

    catalogo = []
    for premio in (Premio.objects.filter(activo=True)
                   .select_related("receta", "nivel_minimo")):
        catalogo.append({
            "premio": premio,
            "puede": premio.puede_canjearlo(cliente),
            "faltan": max(0, premio.puntos_requeridos - cliente.puntos_saldo),
            "avance": min(100, int(cliente.puntos_saldo * 100 /
                                   premio.puntos_requeridos))
            if premio.puntos_requeridos else 100,
        })

    # Avance dentro del nivel actual: de su umbral al del siguiente.
    progreso_nivel = 100
    if siguiente:
        piso = nivel.puntos_requeridos if nivel else 0
        tramo = siguiente.puntos_requeridos - piso
        progreso_nivel = (int((cliente.puntos_historicos - piso) * 100 / tramo)
                          if tramo > 0 else 0)

    ctx = {
        "cliente": cliente,
        "cfg": ConfiguracionPrograma.get(),
        "nivel": nivel,
        "siguiente_nivel": siguiente,
        "progreso_nivel": progreso_nivel,
        "falta_nivel": (siguiente.puntos_requeridos - cliente.puntos_historicos)
                       if siguiente else 0,
        "catalogo": catalogo,
        "pendientes": cliente.canjes.filter(
            estado=Canje.Estado.PENDIENTE).select_related("premio"),
        "movimientos": cliente.movimientos.all()[:10],
        "qr_svg": _qr_svg(f"CLIENTE:{cliente.codigo}"),
        "color": nivel.color if nivel else "#fa5598",
        "wallet_url": wallet.link_google(cliente, request),
    }
    return render(request, "lealtad/tarjeta.html", ctx)


def tarjeta_manifest(request, token):
    """Manifiesto para que la tarjeta se instale en la pantalla de inicio."""
    cliente = get_object_or_404(Cliente, token=token)
    cfg = ConfiguracionPrograma.get()
    nivel = cliente.nivel
    return JsonResponse({
        "name": f"{cfg.nombre_negocio} · Mi tarjeta",
        "short_name": cfg.nombre_negocio,
        "start_url": cliente.get_absolute_url(),
        "scope": cliente.get_absolute_url(),
        "display": "standalone",
        "background_color": "#000000",
        "theme_color": nivel.color if nivel else "#fa5598",
    })
