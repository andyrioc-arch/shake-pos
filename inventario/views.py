import json
from datetime import date
from django.utils.timezone import localdate
from decimal import Decimal, InvalidOperation

import reversion
from django.contrib import messages
from django.contrib.admin.models import ADDITION, CHANGE, DELETION, LogEntry
from django.contrib.auth.decorators import login_required, user_passes_test
from django.db import transaction
from django.db.models import ProtectedError
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from .alarmas import alarmas_margen
from .models import (
    Compra, Extra, Ingrediente, Nota, Receta, RecetaIngrediente,
    Venta, VentaExtra, VentaSustitucion,
)
from presupuesto import comparativo

# Cuántas filas de sustitución ofrece el constructor de producto.
MAX_SUB = 3

ACCION = {ADDITION: "Agregó", CHANGE: "Modificó", DELETION: "Eliminó"}
MODULOS = {
    "inventario": "Inventario", "finanzas": "Finanzas",
    "contabilidad": "Contabilidad", "presupuesto": "Presupuesto",
    "auth": "Usuarios", "reversion": "Historial", "admin": "Administración",
}


@login_required
def home(request):
    """Página de inicio: accesos a los paneles + cumplimiento de presupuesto.

    El cumplimiento de ventas se muestra a todos; el staff ve solo el % de
    cumplimiento (sin metas ni montos en $), el superusuario ve los montos.
    """
    cumplimiento = []
    for f in comparativo.comparativo_ventas():
        cumplimiento.append({
            "periodo": f["periodo"],
            "pct": f["pct"],
            "meta": f["meta"],              # solo superusuario
            "real": f["real"],              # solo superusuario
            "diferencia": f["diferencia"],  # solo superusuario
        })
    ctx = {
        "active": "home",
        "es_super": request.user.is_superuser,
        "cumplimiento": cumplimiento,
    }
    return render(request, "site/home.html", ctx)


@login_required
@user_passes_test(lambda u: u.is_superuser, login_url='/')
def panel_actividad(request):
    """Bitácora de movimientos (solo superusuario): acción, módulo, fecha y cuenta."""
    entradas = (LogEntry.objects
                .select_related("user", "content_type")
                .order_by("-action_time")[:300])
    filas = []
    for e in entradas:
        ct = e.content_type
        modulo = MODULOS.get(ct.app_label, ct.app_label.title()) if ct else "—"
        filas.append({
            "fecha": e.action_time,
            "usuario": e.user.get_username() if e.user else "—",
            "es_super": bool(e.user and e.user.is_superuser),
            "accion": ACCION.get(e.action_flag, "—"),
            "modulo": modulo,
            "objeto": e.object_repr,
            "detalle": e.get_change_message(),
        })
    return render(request, "site/actividad.html",
                  {"active": "actividad", "filas": filas})


@login_required
def panel_inventario(request):
    """Panel operativo de inventario.

    Accesible para todo el personal (staff). Las columnas con dinero
    (costos, márgenes, ingresos, metas en $) solo se muestran al
    superusuario; el staff ve unidades y porcentajes, nunca montos.
    """
    es_super = request.user.is_superuser

    # ── Stock y faltantes ────────────────────────────────────────────────
    ingredientes = []
    faltantes = 0
    for ing in Ingrediente.objects.all():
        falta = ing.hay_faltante
        if falta:
            faltantes += 1
        ingredientes.append({
            "nombre": ing.nombre,
            "categoria": ing.get_categoria_display(),
            "unidad": ing.unidad_receta,
            "stock": ing.stock_disponible,
            "minimo": ing.minimo_para_cinco,
            "falta": falta,
            "faltante": ing.faltante if falta else Decimal("0"),
        })

    # ── Catálogo de recetas ──────────────────────────────────────────────
    recetas = []
    total_unidades = 0
    total_ingreso = Decimal("0")
    for r in Receta.objects.all():
        unidades = r.unidades_vendidas
        ingreso = sum((v.ingreso for v in r.ventas.all()), Decimal("0"))
        total_unidades += unidades
        total_ingreso += ingreso
        recetas.append({
            "nombre": f"{r.emoji} {r.nombre}".strip(),
            "perfil": r.perfil,
            "precio": r.precio_venta,
            "activa": r.activa,
            "unidades": unidades,
            "ingreso": ingreso,               # solo superusuario
            "costo": r.costo_receta,          # solo superusuario
            "ganancia": r.ganancia_unitaria,  # solo superusuario
            "margen": r.margen,               # solo superusuario
        })

    ctx = {
        "title": "Panel de inventario",
        "active": "inventario",
        "es_super": es_super,
        # La alarma expone costo y margen, así que va detrás de la misma
        # puerta que las columnas de costo: solo el dueño la ve.
        "alarma_margen": alarmas_margen() if es_super else None,
        "ingredientes": ingredientes,
        "num_faltantes": faltantes,
        "recetas": recetas,
        "total_unidades": total_unidades,
        "total_ingreso": total_ingreso,
        # Para el formulario de registro (carrito de productos)
        "recetas_activas": Receta.objects.filter(activa=True),
        "ingredientes_lista": Ingrediente.objects.all(),
        "addons": Extra.objects.filter(activo=True),
        "hoy": localdate().isoformat(),
        "rango_sub": range(MAX_SUB),
        # Mapas para el carrito (JS). Se pasan como objetos y se serializan con
        # el tag {{ ...|json_script }}, que escapa de forma segura (evita XSS
        # si un nombre contiene </script>). NO usar |safe con json.dumps.
        "productos_map": {
            r.pk: {
                "nombre": f"{r.emoji} {r.nombre}".strip(),
                "precio": float(r.precio_venta),
                "ings": [[ri.ingrediente.pk, ri.ingrediente.nombre]
                         for ri in r.ingredientes.all()],
            }
            for r in Receta.objects.filter(activa=True)
                                   .prefetch_related("ingredientes__ingrediente")
        },
        "addons_map": {
            e.pk: {"nombre": e.nombre, "cargo": float(e.cargo)}
            for e in Extra.objects.filter(activo=True)
        },
    }
    return render(request, "inventario/panel.html", ctx)


def _to_decimal(valor, permite_cero=True):
    try:
        d = Decimal(str(valor).replace(",", "").strip())
    except (InvalidOperation, AttributeError):
        return None
    if d < 0 or (not permite_cero and d == 0):
        return None
    return d


def _log(request, obj, flag, mensaje):
    LogEntry.objects.log_actions(
        user_id=request.user.pk, queryset=[obj],
        action_flag=flag, change_message=mensaje, single_object=True,
    )


def _fecha(valor):
    try:
        return date.fromisoformat(valor)
    except (TypeError, ValueError):
        return localdate()


class _VentaError(Exception):
    """Cancela el registro de la venta y muestra un mensaje al usuario."""


@require_POST
@login_required
def venta_agregar(request):
    """Registra una venta con varios productos (carrito).

    El frontend envía 'productos_json': lista de productos, cada uno con su
    receta, cantidad, sustituciones y add-ons. Se crea una línea de Venta por
    producto, todo en una sola revisión (deshacer) y con el total combinado.
    Incluye el método de pago (efectivo/tarjeta) y, si es efectivo, calcula
    el cambio a partir de con cuánto paga el cliente.
    """
    try:
        productos = json.loads(request.POST.get("productos_json") or "[]")
    except json.JSONDecodeError:
        productos = []
    if not isinstance(productos, list) or not productos:
        messages.error(request, "Agrega al menos un producto a la venta.")
        return redirect("panel_inventario")

    fecha = _fecha(request.POST.get("fecha"))
    es_cortesia = request.POST.get("cortesia") == "1"
    motivo = request.POST.get("motivo_cortesia", "").strip()
    metodo = request.POST.get("metodo_pago", "efectivo")
    if metodo not in {c.value for c in Venta.MetodoPago}:
        metodo = "efectivo"
    pago_con = (_to_decimal(request.POST.get("pago_con"))
                if metodo == "efectivo" and not es_cortesia else None)

    creadas, total, cambio, nota = [], Decimal("0"), None, None
    try:
        with transaction.atomic():
            with reversion.create_revision():
                for p in productos:
                    venta = _crear_producto(p, fecha, metodo, es_cortesia)
                    if venta:
                        creadas.append(venta)
                        total += venta.ingreso
                reversion.set_user(request.user)
                reversion.set_comment(
                    f"Registró {'cortesía' if es_cortesia else 'venta'} "
                    f"con {len(creadas)} producto(s)")
            if not creadas:
                raise _VentaError("No se pudo registrar la venta (productos inválidos).")
            if es_cortesia and not motivo:
                raise _VentaError("Escribe el motivo de la cortesía.")
            if metodo == "efectivo" and not es_cortesia:
                if pago_con is None:
                    raise _VentaError("Indica con cuánto paga el cliente (efectivo).")
                if pago_con < total:
                    raise _VentaError(
                        f"El efectivo recibido (${pago_con:,.2f}) no alcanza "
                        f"el total (${total:,.2f}).")
                cambio = pago_con - total
            # Genera la nota/comprobante y enlaza las líneas de venta.
            nota = Nota.objects.create(
                fecha=fecha, metodo_pago=metodo, total=total,
                pago_con=pago_con, cambio=cambio,
                es_cortesia=es_cortesia, motivo_cortesia=motivo,
            )
            Venta.objects.filter(pk__in=[v.pk for v in creadas]).update(nota=nota)
    except _VentaError as e:
        messages.error(request, str(e))
        return redirect("panel_inventario")

    # Costea la venta contra las capas de compra. Va fuera de la transacción y
    # con red: una venta jamás se cae por el costeo. Si algo falla queda sin
    # costear y `manage.py recostear --solo-pendientes` la recupera.
    from inventario import costeo
    try:
        for linea in creadas:
            costeo.costear_venta(linea)
    except Exception:                                   # noqa: BLE001
        import logging
        logging.getLogger(__name__).exception(
            "No se pudo costear la nota %s", nota.pk)
        messages.warning(
            request, "Venta registrada, pero su costo quedó pendiente de calcular.")

    # Lealtad: si capturaron el celular del cliente, acumula sus puntos.
    # Va fuera de la transacción de la venta a propósito: la venta ya quedó
    # registrada y un problema del programa de lealtad no debe deshacerla.
    # Las cortesías no acumulan (no se cobraron).
    telefono = request.POST.get("telefono_lealtad", "").strip()
    nombre_cliente = request.POST.get("nombre_lealtad", "").strip()
    if telefono and not es_cortesia:
        from lealtad import servicios as lealtad
        try:
            compra = lealtad.registrar_compra_desde_nota(
                nota, telefono, nombre=nombre_cliente)
        except lealtad.TelefonoInvalido as e:
            messages.warning(request, f"Venta registrada, pero {e}")
        except lealtad.ErrorLealtad as e:
            messages.warning(request, f"Venta registrada, pero {e}")
        else:
            if compra:
                messages.success(
                    request,
                    f"{compra.cliente.nombre_corto} ganó {compra.puntos_ganados} "
                    f"puntos · saldo {compra.cliente.puntos_saldo}.")

    etiqueta = "cortesía" if es_cortesia else metodo
    LogEntry.objects.log_actions(
        user_id=request.user.pk, queryset=creadas, action_flag=ADDITION,
        change_message=f"Registró venta ({etiqueta}) · nota {nota.folio}",
    )
    return redirect("nota_ver", token=nota.token)


def _qr_svg(url):
    """Devuelve el SVG (inline) de un código QR que apunta a la URL dada."""
    import io
    import qrcode
    import qrcode.image.svg
    img = qrcode.make(url, image_factory=qrcode.image.svg.SvgPathImage,
                      box_size=11, border=2)
    buf = io.BytesIO()
    img.save(buf)
    return buf.getvalue().decode()


def nota_ver(request, token):
    """Comprobante público de una venta (para el cliente: enlace / QR)."""
    nota = get_object_or_404(Nota, token=token)
    lineas = []
    for v in nota.lineas.select_related("receta").prefetch_related(
            "extras__extra", "sustituciones__ingrediente_original",
            "sustituciones__ingrediente_nuevo"):
        lineas.append({
            "nombre": f"{v.receta.emoji} {v.receta.nombre}".strip(),
            "cantidad": v.cantidad,
            "importe": v.ingreso,
            "extras": [f"{e.cantidad}× {e.extra.nombre}" for e in v.extras.all()],
            "subs": [f"{s.ingrediente_original} → {s.ingrediente_nuevo}"
                     for s in v.sustituciones.all()],
        })
    url = request.build_absolute_uri(nota.get_absolute_url())
    ctx = {
        "nota": nota,
        "lineas": lineas,
        "url": url,
        "qr_svg": _qr_svg(url),
        "es_staff": request.user.is_authenticated,
        "lealtad": _lealtad_de_la_nota(nota, request),
    }
    return render(request, "inventario/nota.html", ctx)


def _lealtad_de_la_nota(nota, request):
    """Datos del programa de lealtad para mostrarlos en el comprobante."""
    compra = getattr(nota, "compra_lealtad", None)
    if compra is None:
        return None
    cliente = compra.cliente
    premio = cliente.siguiente_premio
    disponibles = cliente.premios_disponibles()
    return {
        "compra": compra,
        "cliente": cliente,
        "premio": premio,
        "falta": (premio.puntos_requeridos - cliente.puntos_saldo) if premio else 0,
        "premio_listo": max(disponibles, key=lambda p: p.puntos_requeridos,
                            default=None),
        "qr": _qr_svg(request.build_absolute_uri(cliente.get_absolute_url())),
    }


def _crear_producto(p, fecha, metodo="efectivo", es_cortesia=False):
    """Crea una línea de Venta (un producto) con sus sustituciones y add-ons."""
    if not isinstance(p, dict):
        return None
    receta = Receta.objects.filter(pk=p.get("receta"), activa=True).first()
    if not receta:
        return None
    try:
        cantidad = max(1, int(p.get("cantidad") or 1))
    except (TypeError, ValueError):
        cantidad = 1

    venta = Venta.objects.create(
        fecha=fecha, receta=receta, cantidad=cantidad, metodo_pago=metodo,
        es_cortesia=es_cortesia)

    # Sustituciones: cambiar un ingrediente de la receta por otro.
    for par in p.get("subs", []):
        try:
            orig_id, nuevo_id = par
        except (ValueError, TypeError):
            continue
        if not orig_id or not nuevo_id or str(orig_id) == str(nuevo_id):
            continue
        orig = Ingrediente.objects.filter(pk=orig_id).first()
        nuevo = Ingrediente.objects.filter(pk=nuevo_id).first()
        if orig and nuevo:
            VentaSustitucion.objects.create(
                venta=venta, ingrediente_original=orig, ingrediente_nuevo=nuevo)

    # Add-ons: extras de $10 (espresso, creatina, colágeno...).
    for par in p.get("addons", []):
        try:
            extra_id, qty = par
        except (ValueError, TypeError):
            continue
        extra = Extra.objects.filter(pk=extra_id, activo=True).first()
        if not extra:
            continue
        try:
            qty = max(1, int(qty))
        except (TypeError, ValueError):
            qty = 1
        VentaExtra.objects.create(venta=venta, extra=extra, cantidad=qty)

    return venta


@require_POST
@login_required
@user_passes_test(lambda u: u.is_superuser, login_url='/')
def compra_agregar(request):
    """Registra una compra de stock. Solo superusuario (expone costos).

    Se guarda tal cual lo que se pagó: la unidad comprada y el monto total. El
    costo unitario ya no se deriva ni se guarda, y la compra NO toca el costo
    del catálogo: ése es un estimado para calcular márgenes aproximados, y el
    costo real de cada venta sale de las compras por FIFO.
    """
    ingrediente = Ingrediente.objects.filter(pk=request.POST.get("ingrediente")).first()
    cantidad = _to_decimal(request.POST.get("cantidad"), permite_cero=False)
    costo_total = _to_decimal(request.POST.get("costo_total"))
    fecha = _fecha(request.POST.get("fecha"))

    if not ingrediente:
        messages.error(request, "Selecciona un ingrediente válido.")
    elif cantidad is None:
        messages.error(request, "La cantidad comprada debe ser mayor a 0.")
    elif costo_total is None:
        messages.error(request, "El costo total ($) es inválido.")
    else:
        compra = Compra.objects.create(
            fecha=fecha, ingrediente=ingrediente, cantidad=cantidad,
            monto_total=costo_total,
            proveedor=request.POST.get("proveedor", "").strip(),
        )
        # El recosteo de las ventas que esta capa puede surtir lo dispara la
        # señal de Compra, para que valga igual desde el admin o el shell.
        _log(request, compra, ADDITION, "Registró compra de stock")
        messages.success(
            request,
            f"Compra registrada: {cantidad} {ingrediente.unidad_compra} de "
            f"{ingrediente} por ${costo_total:,.2f}.")
    return redirect("panel_inventario")


# ══════════════════════════════════════════════════════════════════════════════
#  CATÁLOGO DE PRODUCTOS  (solo superusuario): ingredientes, productos y recetas
# ══════════════════════════════════════════════════════════════════════════════
solo_super = user_passes_test(lambda u: u.is_superuser, login_url='/')


def _volver_catalogo(producto_pk=None):
    url = reverse("panel_catalogo")
    if producto_pk:
        url += f"?producto={producto_pk}"
    return redirect(url)


@login_required
@solo_super
def panel_catalogo(request):
    """Gestión del catálogo: ingredientes, productos y sus recetas."""
    ingredientes = Ingrediente.objects.all()
    productos = Receta.objects.all()

    # Producto seleccionado para editar su receta (?producto=<pk>).
    producto_sel = None
    receta_lineas = []
    ingredientes_libres = []
    try:
        pk = int(request.GET.get("producto", ""))
        producto_sel = Receta.objects.filter(pk=pk).first()
    except (TypeError, ValueError):
        producto_sel = None
    if producto_sel:
        usados = set()
        for ri in producto_sel.ingredientes.select_related("ingrediente"):
            usados.add(ri.ingrediente_id)
            receta_lineas.append({
                "id": ri.id, "nombre": ri.ingrediente.nombre,
                "unidad": ri.ingrediente.unidad_receta,
                "cantidad": ri.cantidad, "costo_linea": ri.costo_linea,
            })
        ingredientes_libres = [i for i in ingredientes if i.id not in usados]

    ctx = {
        "title": "Catálogo de productos",
        "active": "catalogo",
        "ingredientes": ingredientes,
        "categorias_ing": Ingrediente.Categoria.choices,
        "productos": productos,
        "producto_sel": producto_sel,
        "receta_lineas": receta_lineas,
        "ingredientes_libres": ingredientes_libres,
    }
    return render(request, "inventario/catalogo.html", ctx)


# ── Ingredientes ───────────────────────────────────────────────────────────────
@require_POST
@login_required
@solo_super
def ingrediente_agregar(request):
    nombre = request.POST.get("nombre", "").strip()
    categoria = request.POST.get("categoria", "otro")
    unidad_compra = request.POST.get("unidad_compra", "").strip()
    unidad_receta = request.POST.get("unidad_receta", "").strip()
    cpu = _to_decimal(request.POST.get("cantidad_por_unidad"), permite_cero=False)
    costo = _to_decimal(request.POST.get("costo_unidad_compra")) or Decimal("0")
    validas = {k for k, _ in Ingrediente.Categoria.choices}

    if not nombre:
        messages.error(request, "El nombre del ingrediente es obligatorio.")
    elif Ingrediente.objects.filter(nombre__iexact=nombre).exists():
        messages.error(request, f"Ya existe un ingrediente llamado «{nombre}».")
    elif categoria not in validas:
        messages.error(request, "Categoría inválida.")
    elif not unidad_compra or not unidad_receta:
        messages.error(request, "Indica la unidad de compra y la de receta.")
    elif cpu is None:
        messages.error(request, "La cantidad por unidad de compra debe ser mayor a 0.")
    else:
        obj = Ingrediente.objects.create(
            nombre=nombre, categoria=categoria, unidad_compra=unidad_compra,
            cantidad_por_unidad=cpu, unidad_receta=unidad_receta,
            costo_unidad_compra=costo,
        )
        _log(request, obj, ADDITION, "Agregó ingrediente")
        messages.success(request, f"Ingrediente «{nombre}» registrado.")
    return _volver_catalogo()


@require_POST
@login_required
@solo_super
def ingrediente_eliminar(request, pk):
    obj = get_object_or_404(Ingrediente, pk=pk)
    nombre = obj.nombre
    try:
        with transaction.atomic():
            _log(request, obj, DELETION, "Eliminó ingrediente")
            obj.delete()
        messages.success(request, f"Ingrediente «{nombre}» eliminado.")
    except ProtectedError:
        messages.error(
            request,
            f"No se puede eliminar «{nombre}»: está en uso en recetas, "
            f"compras o extras.")
    return _volver_catalogo()


# ── Productos (recetas) ─────────────────────────────────────────────────────────
@require_POST
@login_required
@solo_super
def producto_agregar(request):
    nombre = request.POST.get("nombre", "").strip()
    precio = _to_decimal(request.POST.get("precio_venta"))
    if not nombre:
        messages.error(request, "El nombre del producto es obligatorio.")
    elif Receta.objects.filter(nombre__iexact=nombre).exists():
        messages.error(request, f"Ya existe un producto llamado «{nombre}».")
    elif precio is None:
        messages.error(request, "El precio de venta es inválido.")
    else:
        obj = Receta.objects.create(
            nombre=nombre, emoji=request.POST.get("emoji", "").strip(),
            perfil=request.POST.get("perfil", "").strip(),
            precio_venta=precio,
            tamano=request.POST.get("tamano", "").strip() or "16 oz / 473 ml",
        )
        _log(request, obj, ADDITION, "Creó producto")
        messages.success(request, f"Producto «{nombre}» creado. Agrégale su receta.")
        return _volver_catalogo(obj.pk)
    return _volver_catalogo()


@require_POST
@login_required
@solo_super
def producto_editar(request, pk):
    obj = get_object_or_404(Receta, pk=pk)
    precio = _to_decimal(request.POST.get("precio_venta"))
    if precio is None:
        messages.error(request, "Precio de venta inválido.")
        return _volver_catalogo(pk)
    nombre = request.POST.get("nombre", obj.nombre).strip() or obj.nombre
    if Receta.objects.filter(nombre__iexact=nombre).exclude(pk=obj.pk).exists():
        messages.error(request, f"Ya existe otro producto llamado «{nombre}».")
        return _volver_catalogo(pk)
    obj.nombre = nombre
    obj.emoji = request.POST.get("emoji", "").strip()
    obj.perfil = request.POST.get("perfil", "").strip()
    obj.tamano = request.POST.get("tamano", "").strip() or obj.tamano
    obj.precio_venta = precio
    obj.activa = request.POST.get("activa") == "1"
    obj.save()
    _log(request, obj, CHANGE, "Editó producto")
    messages.success(request, "Producto actualizado.")
    return _volver_catalogo(pk)


@require_POST
@login_required
@solo_super
def producto_eliminar(request, pk):
    obj = get_object_or_404(Receta, pk=pk)
    nombre = obj.nombre
    try:
        with transaction.atomic():
            _log(request, obj, DELETION, "Eliminó producto")
            obj.delete()
        messages.success(request, f"Producto «{nombre}» eliminado.")
    except ProtectedError:
        messages.error(
            request,
            f"No se puede eliminar «{nombre}»: tiene ventas registradas. "
            f"Puedes desactivarlo en su lugar.")
    return _volver_catalogo()


# ── Receta: líneas de ingrediente ───────────────────────────────────────────────
@require_POST
@login_required
@solo_super
def receta_linea_agregar(request, pk):
    receta = get_object_or_404(Receta, pk=pk)
    ingrediente = Ingrediente.objects.filter(pk=request.POST.get("ingrediente")).first()
    cantidad = _to_decimal(request.POST.get("cantidad"), permite_cero=False)
    if not ingrediente:
        messages.error(request, "Selecciona un ingrediente.")
    elif cantidad is None:
        messages.error(request, "La cantidad debe ser mayor a 0.")
    else:
        RecetaIngrediente.objects.update_or_create(
            receta=receta, ingrediente=ingrediente,
            defaults={"cantidad": cantidad})
        _log(request, receta, CHANGE, f"Agregó {ingrediente} a la receta")
        messages.success(
            request,
            f"{ingrediente} × {cantidad} {ingrediente.unidad_receta} agregado.")
    return _volver_catalogo(pk)


@require_POST
@login_required
@solo_super
def receta_linea_eliminar(request, pk):
    linea = get_object_or_404(RecetaIngrediente, pk=pk)
    receta_pk = linea.receta_id
    linea.delete()
    _log(request, linea.receta, CHANGE, "Quitó un ingrediente de la receta")
    messages.success(request, "Ingrediente quitado de la receta.")
    return _volver_catalogo(receta_pk)
