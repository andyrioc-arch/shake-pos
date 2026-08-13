from datetime import date
from django.utils.timezone import localdate

from django.contrib import messages
from django.contrib.auth.decorators import login_required, user_passes_test
from django.http import Http404, HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from decimal import Decimal, InvalidOperation

from presupuesto.models import MESES
from . import export, posting
from .models import Movimiento, CategoriaGasto

XLSX_MIME = ("application/vnd.openxmlformats-officedocument"
             ".spreadsheetml.sheet")

MES_NOMBRE = dict(MESES)
solo_super = user_passes_test(lambda u: u.is_superuser, login_url='/')


def _shift(anio, mes, delta):
    m, y = mes + delta, anio
    while m < 1:
        m += 12
        y -= 1
    while m > 12:
        m -= 12
        y += 1
    return y, m


def _periodo(request):
    try:
        return int(request.GET["anio"]), int(request.GET["mes"])
    except (KeyError, TypeError, ValueError):
        periodos = posting.periodos_disponibles()
        if periodos:
            return periodos[0]
        hoy = localdate()
        return hoy.year, hoy.month


@login_required
@solo_super
def reportes(request):
    anio, mes = _periodo(request)
    prev_a, prev_m = _shift(anio, mes, -1)
    next_a, next_m = _shift(anio, mes, +1)

    # `venta__nota` viene en el mismo golpe: cada fila de venta enlaza a su
    # nota, y pedirla por fila cuesta dos consultas por movimiento.
    movimientos = (Movimiento.objects
                   .filter(fecha__year=anio, fecha__month=mes)
                   .select_related("cuenta", "venta__nota")
                   .order_by("fecha", "id"))

    ctx = {
        "title": "Contabilidad",
        "active": "contabilidad",
        "anio": anio, "mes": mes, "mes_nombre": MES_NOMBRE[mes],
        "prev_a": prev_a, "prev_m": prev_m,
        "next_a": next_a, "next_m": next_m,
        "periodos": [
            {"anio": a, "mes": m, "label": f"{MES_NOMBRE[m]} {a}",
             "sel": (a == anio and m == mes)}
            for (a, m) in posting.periodos_disponibles()
        ],
        "movimientos": movimientos,
        "balanza": posting.balanza_comprobacion(anio, mes),
        "resultados": posting.estado_resultados(anio, mes),
        "balance": posting.balance_general(anio, mes),
        "flujo": posting.flujo_efectivo(anio, mes),
        "salud": posting.salud_del_costeo(anio, mes),
        "categorias_gasto": CategoriaGasto.objects.filter(activo=True),
        "hoy": localdate().isoformat(),
    }
    return render(request, "contabilidad/reportes.html", ctx)


@require_POST
@login_required
@solo_super
def gasto_registrar(request):
    """Registra un gasto operativo (sueldos, renta, marketing…) en el libro."""
    categoria = request.POST.get("categoria", "")
    descripcion = request.POST.get("descripcion", "").strip()
    try:
        monto = Decimal(str(request.POST.get("monto", "")).replace(",", "").strip())
    except (InvalidOperation, AttributeError):
        monto = None
    fecha = localdate()
    if request.POST.get("fecha"):
        try:
            fecha = date.fromisoformat(request.POST["fecha"])
        except ValueError:
            pass
    validas = set(CategoriaGasto.objects.filter(activo=True)
                  .values_list("clave", flat=True))
    if categoria not in validas:
        messages.error(request, "Categoría inválida.")
    elif monto is None or monto <= 0:
        messages.error(request, "El monto debe ser mayor a 0.")
    else:
        posting.registrar_gasto(fecha, categoria, monto, descripcion)
        messages.success(request, f"Gasto registrado: ${monto:,.2f} ({categoria}).")
    return redirect(f"{reverse('reportes_contables')}?anio={fecha.year}&mes={fecha.month}")


@login_required
@solo_super
def exportar(request, reporte):
    """Descarga un estado financiero del mes seleccionado en xlsx."""
    if reporte not in export.BUILDERS:
        raise Http404("Reporte no válido.")
    anio, mes = _periodo(request)
    periodo = f"{MES_NOMBRE[mes]} {anio}"
    wb, nombre = export.construir(reporte, anio, mes, periodo)
    resp = HttpResponse(content_type=XLSX_MIME)
    resp["Content-Disposition"] = (
        f'attachment; filename="{nombre}_{anio}-{mes:02d}.xlsx"')
    wb.save(resp)
    return resp


