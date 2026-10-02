"""Convierte «esta semana» en fechas absolutas, con el día de hoy en CDMX.

El modelo que llama las herramientas no sabe qué día es; el servidor sí. Por
eso los periodos relativos se resuelven aquí y cada respuesta devuelve las
fechas que de verdad se usaron.
"""
import calendar
from datetime import date, timedelta

from django.utils.timezone import localdate

PERIODOS = (
    "hoy", "ayer", "esta_semana", "semana_pasada", "este_mes", "mes_pasado",
    "ultimos_7_dias", "ultimos_30_dias",
)
NOMBRES = {
    "hoy": "hoy", "ayer": "ayer",
    "esta_semana": "esta semana (lunes a hoy)",
    "semana_pasada": "semana pasada (lunes a domingo)",
    "este_mes": "este mes (día 1 a hoy)",
    "mes_pasado": "mes pasado (completo)",
    "ultimos_7_dias": "últimos 7 días", "ultimos_30_dias": "últimos 30 días",
}
MAX_DIAS = 366


class ErrorDeConsulta(ValueError):
    """Un argumento que no se puede responder. El mensaje lo lee el modelo."""


def _fin_de_mes(d):
    return d.replace(day=calendar.monthrange(d.year, d.month)[1])


def _mes_anterior(d):
    return (d.replace(day=1) - timedelta(days=1)).replace(day=1)


def _fecha(valor, campo):
    try:
        return date.fromisoformat(valor)
    except (TypeError, ValueError):
        raise ErrorDeConsulta(
            f"«{campo}» debe ser una fecha AAAA-MM-DD; llegó {valor!r}.")


def resolver(periodo=None, desde=None, hasta=None, hoy=None,
             por_omision="este_mes"):
    """(desde, hasta, nombre). `desde`/`hasta` explícitos ganan a `periodo`."""
    hoy = hoy or localdate()
    if desde or hasta:
        d = _fecha(desde, "desde") if desde else None
        h = _fecha(hasta, "hasta") if hasta else hoy
        d = d or h
        if d > h:
            raise ErrorDeConsulta("«desde» no puede ser posterior a «hasta».")
        if (h - d).days + 1 > MAX_DIAS:
            raise ErrorDeConsulta(f"El rango máximo es de {MAX_DIAS} días.")
        return d, h, f"del {d.isoformat()} al {h.isoformat()}"

    periodo = periodo or por_omision
    if periodo not in PERIODOS:
        raise ErrorDeConsulta(
            f"Periodo desconocido {periodo!r}. Usa uno de: {', '.join(PERIODOS)}.")
    lunes = hoy - timedelta(days=hoy.weekday())
    rangos = {
        "hoy": (hoy, hoy),
        "ayer": (hoy - timedelta(days=1), hoy - timedelta(days=1)),
        "esta_semana": (lunes, hoy),
        "semana_pasada": (lunes - timedelta(days=7), lunes - timedelta(days=1)),
        "este_mes": (hoy.replace(day=1), hoy),
        "mes_pasado": (_mes_anterior(hoy), _fin_de_mes(_mes_anterior(hoy))),
        "ultimos_7_dias": (hoy - timedelta(days=6), hoy),
        "ultimos_30_dias": (hoy - timedelta(days=29), hoy),
    }
    d, h = rangos[periodo]
    return d, h, NOMBRES[periodo]


def anterior(desde, hasta):
    """El tramo equivalente justo antes, para comparar.

    Un rango que arranca el día 1 y no sale del mes se compara contra el mismo
    tramo del mes anterior («del 1 al 2 de septiembre» contra «del 1 al 2 de
    octubre»): comparar dos días contra los dos últimos del mes pasado
    mezclaría quincenas. Lo demás se corre hacia atrás su propia longitud.
    """
    if desde.day == 1 and desde.month == hasta.month and desde.year == hasta.year:
        previo = _mes_anterior(desde)
        fin = min(previo + (hasta - desde), _fin_de_mes(previo))
        return previo, fin
    largo = hasta - desde + timedelta(days=1)
    return desde - largo, hasta - largo


def rango_mes(anio, mes_):
    inicio = date(anio, mes_, 1)
    return inicio, _fin_de_mes(inicio)


def mes(anio=None, mes_=None, hoy=None):
    """(anio, mes) validados; el mes en curso si no se dan."""
    hoy = hoy or localdate()
    anio = hoy.year if anio is None else anio
    mes_ = hoy.month if mes_ is None else mes_
    if not 1 <= mes_ <= 12:
        raise ErrorDeConsulta("«mes» va de 1 a 12.")
    if not 2020 <= anio <= hoy.year + 1:
        raise ErrorDeConsulta(f"«anio» debe estar entre 2020 y {hoy.year + 1}.")
    return anio, mes_
