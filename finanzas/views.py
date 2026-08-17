from datetime import date
from django.utils.timezone import localdate
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.contrib.admin.models import ADDITION, DELETION, LogEntry
from django.contrib.auth.decorators import login_required, user_passes_test
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from presupuesto.models import periodo_capturable, periodo_consultable

from . import calculos
from .models import InversionInicial, PronosticoFlujoCuenta

MESES = ["", "Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio",
         "Julio", "Agosto", "Septiembre", "Octubre", "Noviembre", "Diciembre"]

solo_super = user_passes_test(lambda u: u.is_superuser, login_url='/')


def _shift(anio, mes, delta):
    m, y = mes + delta, anio
    while m < 1:
        m += 12; y -= 1
    while m > 12:
        m -= 12; y += 1
    return y, m


def _periodo_flujo(request):
    # Ojo con MESES de aquí arriba: es una LISTA, así que un mes negativo no
    # truena, devuelve el mes contado desde el final. Acotar el rango es lo
    # único que evita que el panel diga «Diciembre» sobre datos de la nada.
    try:
        anio, mes = int(request.GET["fanio"]), int(request.GET["fmes"])
    except (KeyError, TypeError, ValueError):
        pass
    else:
        if periodo_consultable(anio, mes):
            return anio, mes
    periodos = calculos.periodos_flujo()
    if periodos:
        return periodos[0]
    hoy = localdate()
    return hoy.year, hoy.month


def _dec(valor):
    try:
        d = Decimal(str(valor).replace(",", "").strip())
        # `NaN` queda atrapado por el except —comparar lo hace estallar— pero
        # `Infinity` sí se compara y pasa, y muere al escribir en la columna.
        # `is_finite()` cierra los dos de frente. Misma guarda que
        # `lealtad.api._monto` e `inventario.views._to_decimal`.
        return d if d.is_finite() and d >= 0 else None
    except (InvalidOperation, AttributeError):
        return None


def _log(request, obj, flag, mensaje):
    LogEntry.objects.log_actions(
        user_id=request.user.pk, queryset=[obj],
        action_flag=flag, change_message=mensaje, single_object=True,
    )


@login_required
@solo_super
def panel_financiero(request):
    data = calculos.resumen()
    flujo = data["flujo"]

    recuperado = flujo["recuperado_en"]
    recuperado_txt = (
        f"{MESES[recuperado[1]]} {recuperado[0]}" if recuperado else None
    )

    filas = []
    for f in flujo["filas"]:
        f = dict(f)
        f["mes_nombre"] = f"{MESES[f['mes']]} {f['anio']}"
        filas.append(f)

    pe = data["punto_equilibrio"]
    hoy = localdate()
    ctx = {
        "title": "Panel financiero",
        "active": "finanzas",
        "costos_fijos": data["costos_fijos_mes"],
        "inversion": data["inversion_total"],
        "margen_contribucion": data["margen_contribucion"],
        "unidades_vendidas": data["unidades_vendidas"],
        "pe_unidades_mes": pe["unidades_mes"],
        "pe_unidades_dia": pe["unidades_dia"],
        "filas": filas,
        "recuperado_txt": recuperado_txt,
        "ganancia_acumulada": flujo["ganancia_acumulada_final"],
        "inversion_pendiente": (
            data["inversion_total"] - flujo["ganancia_acumulada_final"]
        ),
        # Desglose de inversión inicial
        "desglose": calculos.inversion_desglose(),
        "categorias_inv": InversionInicial.Categoria.choices,
    }

    # ── Flujo pronóstico vs. real, desglosado por cuenta (mes seleccionado) ──
    f_anio, f_mes = _periodo_flujo(request)
    flujo = calculos.flujo_desglose(f_anio, f_mes)
    pron_actual = {
        p.cuenta_id: p.monto
        for p in PronosticoFlujoCuenta.objects.filter(anio=f_anio, mes=f_mes)
    }
    cuentas_form = [
        {"id": c.id, "codigo": c.codigo, "nombre": c.nombre,
         "es_entrada": c.tipo in ("ingreso", "capital"),
         "valor": pron_actual.get(c.id)}
        for c in calculos.cuentas_flujo()
    ]
    prev_a, prev_m = _shift(f_anio, f_mes, -1)
    next_a, next_m = _shift(f_anio, f_mes, +1)
    ctx.update({
        "flujo": flujo,
        "cuentas_form": cuentas_form,
        "f_anio": f_anio, "f_mes": f_mes, "f_mes_nombre": MESES[f_mes],
        "f_prev_a": prev_a, "f_prev_m": prev_m,
        "f_next_a": next_a, "f_next_m": next_m,
        "f_periodos": [
            {"anio": a, "mes": m, "label": f"{MESES[m]} {a}",
             "sel": (a == f_anio and m == f_mes)}
            for (a, m) in calculos.periodos_flujo()
        ],
    })
    return render(request, "finanzas/panel_financiero.html", ctx)


@require_POST
@login_required
@solo_super
def pronostico_guardar(request):
    # Lo que pase de aquí se escribe. Una fila con el mes fuera de rango deja
    # el panel en 500 para todos, con querystring o sin ella, hasta que alguien
    # la borre a mano de la base.
    try:
        anio, mes = int(request.POST["anio"]), int(request.POST["mes"])
        valido = periodo_capturable(anio, mes)
    except (KeyError, TypeError, ValueError):
        valido = False
    if not valido:
        messages.error(request, "Periodo inválido.")
        return redirect("panel_financiero")

    guardadas = 0
    for c in calculos.cuentas_flujo():
        monto = _dec(request.POST.get(f"cuenta_{c.id}"))
        if monto and monto > 0:
            PronosticoFlujoCuenta.objects.update_or_create(
                anio=anio, mes=mes, cuenta=c, defaults={"monto": monto})
            guardadas += 1
        else:
            PronosticoFlujoCuenta.objects.filter(
                anio=anio, mes=mes, cuenta=c).delete()

    LogEntry.objects.log_actions(
        user_id=request.user.pk,
        queryset=list(PronosticoFlujoCuenta.objects.filter(anio=anio, mes=mes)[:1]) or [request.user],
        action_flag=ADDITION, change_message="Guardó pronóstico de flujo por cuenta",
    )
    messages.success(
        request,
        f"Pronóstico de {MESES[mes]} {anio} guardado ({guardadas} cuenta(s)).")
    return redirect(f"{reverse('panel_financiero')}?fanio={anio}&fmes={mes}")


@require_POST
@login_required
@solo_super
def inversion_agregar(request):
    concepto = request.POST.get("concepto", "").strip()
    categoria = request.POST.get("categoria", "")
    monto = _dec(request.POST.get("monto"))
    validas = {k for k, _ in InversionInicial.Categoria.choices}
    if not concepto:
        messages.error(request, "El concepto es obligatorio.")
    elif categoria not in validas:
        messages.error(request, "Categoría inválida.")
    elif monto is None or monto == 0:
        messages.error(request, "El monto debe ser mayor a 0.")
    else:
        fecha = None
        if request.POST.get("fecha"):
            try:
                fecha = date.fromisoformat(request.POST["fecha"])
            except ValueError:
                fecha = None
        obj = InversionInicial.objects.create(
            concepto=concepto, categoria=categoria, monto=monto, fecha=fecha)
        _log(request, obj, ADDITION, "Agregó partida de inversión inicial")
        messages.success(request, f"Inversión agregada: {concepto} (${monto:,.2f}).")
    return redirect("panel_financiero")


@require_POST
@login_required
@solo_super
def inversion_eliminar(request, pk):
    obj = get_object_or_404(InversionInicial, pk=pk)
    _log(request, obj, DELETION, "Eliminó partida de inversión inicial")
    obj.delete()
    messages.success(request, "Partida de inversión eliminada.")
    return redirect("panel_financiero")
