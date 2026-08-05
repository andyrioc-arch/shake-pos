from datetime import date
from django.utils.timezone import localdate
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.contrib.admin.models import ADDITION, CHANGE, DELETION, LogEntry
from django.contrib.auth.decorators import login_required, user_passes_test
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.text import slugify
from django.views.decorators.http import require_POST

from contabilidad.models import CategoriaGasto, Cuenta
from . import comparativo
from .models import MESES, PresupuestoGasto, PresupuestoVenta

MES_NOMBRE = dict(MESES)

solo_super = user_passes_test(lambda u: u.is_superuser, login_url='/')


def _log(request, obj, flag, mensaje):
    """Registra la acción en el log de actividad (LogEntry)."""
    LogEntry.objects.log_actions(
        user_id=request.user.pk,
        queryset=[obj],
        action_flag=flag,
        change_message=mensaje,
        single_object=True,
    )


def _periodo_post(request):
    """(anio, mes) del POST, o (None, None) si vienen ausentes/inválidos."""
    try:
        return int(request.POST["anio"]), int(request.POST["mes"])
    except (KeyError, TypeError, ValueError):
        return None, None


def _shift(anio, mes, delta):
    m = mes + delta
    y = anio
    while m < 1:
        m += 12
        y -= 1
    while m > 12:
        m -= 12
        y += 1
    return y, m


def _periodo_de(request):
    """Mes/año seleccionado (GET); por defecto, el más reciente con datos o el actual."""
    try:
        return int(request.GET["anio"]), int(request.GET["mes"])
    except (KeyError, TypeError, ValueError):
        periodos = comparativo.periodos_disponibles()
        if periodos:
            return periodos[0]
        hoy = localdate()
        return hoy.year, hoy.month


def _volver(anio, mes):
    return redirect(f"{reverse('panel_presupuesto')}?anio={anio}&mes={mes}")


def _to_decimal(valor):
    try:
        d = Decimal(str(valor).replace(",", "").strip())
        return d if d >= 0 else None
    except (InvalidOperation, AttributeError):
        return None


@login_required
@solo_super
def panel_presupuesto(request):
    anio, mes = _periodo_de(request)
    data = comparativo.detalle_mes(anio, mes)

    prev_a, prev_m = _shift(anio, mes, -1)
    next_a, next_m = _shift(anio, mes, +1)

    periodos = [
        {"anio": a, "mes": m, "label": f"{MES_NOMBRE[m]} {a}",
         "sel": (a == anio and m == mes)}
        for (a, m) in comparativo.periodos_disponibles()
    ]

    ctx = {
        "title": "Presupuesto vs. real",
        "active": "presupuesto",
        "anio": anio, "mes": mes,
        "mes_nombre": MES_NOMBRE[mes],
        "prev_a": prev_a, "prev_m": prev_m,
        "next_a": next_a, "next_m": next_m,
        "periodos": periodos,
        "categorias_libres": data["categorias_libres"],
        **data,
    }
    return render(request, "presupuesto/panel.html", ctx)


@require_POST
@login_required
@solo_super
def venta_guardar(request):
    anio, mes = _periodo_post(request)
    if anio is None:
        messages.error(request, "Periodo inválido.")
        return redirect("panel_presupuesto")
    monto = _to_decimal(request.POST.get("monto"))
    if monto is None:
        messages.error(request, "Monto inválido para la meta de ventas.")
    else:
        obj, creado = PresupuestoVenta.objects.update_or_create(
            anio=anio, mes=mes,
            defaults={"monto": monto, "notas": request.POST.get("notas", "")},
        )
        _log(request, obj, ADDITION if creado else CHANGE,
             "Agregó meta de ventas" if creado else "Actualizó meta de ventas")
        messages.success(request, "Meta de ventas guardada.")
    return _volver(anio, mes)


@require_POST
@login_required
@solo_super
def venta_eliminar(request):
    anio, mes = _periodo_post(request)
    if anio is None:
        messages.error(request, "Periodo inválido.")
        return redirect("panel_presupuesto")
    obj = PresupuestoVenta.objects.filter(anio=anio, mes=mes).first()
    if obj:
        _log(request, obj, DELETION, "Eliminó meta de ventas")
        obj.delete()
    messages.success(request, "Meta de ventas eliminada.")
    return _volver(anio, mes)


@require_POST
@login_required
@solo_super
def gasto_guardar(request):
    anio, mes = _periodo_post(request)
    if anio is None:
        messages.error(request, "Periodo inválido.")
        return redirect("panel_presupuesto")
    categoria = request.POST.get("categoria", "")
    monto = _to_decimal(request.POST.get("monto"))
    validas = set(
        CategoriaGasto.objects.filter(activo=True).values_list("clave", flat=True)
    )
    if categoria not in validas:
        messages.error(request, "Categoría inválida.")
    elif monto is None:
        messages.error(request, "Monto inválido para la línea de gasto.")
    else:
        obj, creado = PresupuestoGasto.objects.update_or_create(
            anio=anio, mes=mes, categoria=categoria,
            defaults={"monto": monto, "notas": request.POST.get("notas", "")},
        )
        _log(request, obj, ADDITION if creado else CHANGE,
             "Agregó línea de gasto" if creado else "Actualizó línea de gasto")
        messages.success(request, "Línea de gasto guardada.")
    return _volver(anio, mes)


@require_POST
@login_required
@solo_super
def gasto_eliminar(request):
    anio, mes = _periodo_post(request)
    if anio is None:
        messages.error(request, "Periodo inválido.")
        return redirect("panel_presupuesto")
    pk = request.POST.get("pk")
    obj = PresupuestoGasto.objects.filter(pk=pk).first()
    if obj:
        _log(request, obj, DELETION, "Eliminó línea de gasto")
        obj.delete()
    messages.success(request, "Línea de gasto eliminada.")
    return _volver(anio, mes)


@require_POST
@login_required
@solo_super
def categoria_crear(request):
    """Crea una nueva categoría de gasto (compartida con facturas reales)."""
    anio, mes = _periodo_post(request)
    if anio is None:
        messages.error(request, "Periodo inválido.")
        return redirect("panel_presupuesto")
    nombre = request.POST.get("nombre", "").strip()
    cuenta = request.POST.get("cuenta_codigo", "").strip()
    if not nombre:
        messages.error(request, "El nombre de la categoría es obligatorio.")
        return _volver(anio, mes)

    clave = slugify(nombre)[:30] or "categoria"
    # Evita choque de claves si ya existe una con el mismo slug.
    base, n = clave, 2
    while CategoriaGasto.objects.filter(clave=clave).exists():
        clave = f"{base[:27]}-{n}"
        n += 1

    # Si la cuenta indicada no existe en el catálogo, no la asignamos:
    # los gastos de esta categoría caerán en 'Otros gastos' (509).
    aviso = ""
    if cuenta and not Cuenta.objects.filter(codigo=cuenta).exists():
        aviso = (f" La cuenta «{cuenta}» no existe en el catálogo, así que "
                 f"quedó sin cuenta asignada (usará 'Otros gastos').")
        cuenta = ""

    obj = CategoriaGasto.objects.create(
        clave=clave, nombre=nombre, cuenta_codigo=cuenta, activo=True,
    )
    _log(request, obj, ADDITION, "Creó categoría de gasto")
    messages.success(request, f"Categoría «{nombre}» creada.{aviso}")
    return _volver(anio, mes)
