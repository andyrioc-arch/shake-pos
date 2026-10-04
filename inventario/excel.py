"""Descarga a xlsx de cualquier tabla del sitio.

El navegador manda el texto de las celdas tal como se ven en pantalla y aquí se
arma el libro. Así cada panel exporta exactamente lo que muestra, sin que haya
que escribir un exportador por tabla y mantenerlo igual a su plantilla a mano.
No consulta la base: solo convierte lo que llega, así que no puede enseñar nada
que el usuario no tuviera ya en pantalla.
"""
import json
import re
from datetime import date

from django.contrib.auth.decorators import login_required
from django.http import HttpResponse, HttpResponseBadRequest
from django.utils.text import slugify
from django.utils.timezone import localdate
from django.views.decorators.http import require_POST
from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

XLSX_MIME = ("application/vnd.openxmlformats-officedocument"
             ".spreadsheetml.sheet")
MONEY = '"$"#,##0.00'
PORCENTAJE = "0.0%"
ENTERO = "#,##0"
DECIMAL = "#,##0.00"

MAX_TABLAS = 50
MAX_FILAS = 5000
MAX_COLUMNAS = 60
MAX_CELDAS = 200_000

_CABECERA_FONT = Font(bold=True, color="FFFFFF")
_CABECERA_FILL = PatternFill("solid", fgColor="000000")

_NUMERO = re.compile(
    r"^(?P<signo>[-+−])?\s*(?P<pesos>\$)?\s*(?P<signo2>[-+−])?\s*"
    r"(?P<num>\d{1,3}(?:,\d{3})+|\d+)(?P<dec>\.\d+)?\s*(?P<pct>%)?$")
_FECHA = re.compile(r"^(\d{1,2})/(\d{1,2})/(\d{4})$")
_PROHIBIDOS_HOJA = re.compile(r"[\[\]:*?/\\]")


def interpreta(texto):
    """(valor, formato) de una celda: número, fecha o el texto tal cual.

    Se queda en texto lo que pierda información al volverse número: un folio
    con cero a la izquierda o un teléfono largo dejarían de ser lo que eran.
    """
    texto = (texto or "").strip()
    if not texto:
        return None, None

    m = _FECHA.match(texto)
    if m:
        dia, mes, anio = (int(g) for g in m.groups())
        try:
            return date(anio, mes, dia), "dd/mm/yyyy"
        except ValueError:
            return texto, None

    m = _NUMERO.match(texto)
    if not m:
        return texto, None
    entero = m["num"].replace(",", "")
    if not m["pesos"] and not m["pct"] and not m["dec"]:
        if (len(entero) > 1 and entero.startswith("0")) or len(entero) > 10:
            return texto, None
    valor = float(entero + (m["dec"] or ""))
    signos = (m["signo"] or "") + (m["signo2"] or "")
    if "-" in signos or "−" in signos:
        valor = -valor
    if m["pct"]:
        return valor / 100, PORCENTAJE
    if m["pesos"]:
        return valor, MONEY
    return valor, (DECIMAL if m["dec"] else ENTERO)


def _nombre_hoja(nombre, usados):
    base = _PROHIBIDOS_HOJA.sub(" ", nombre or "").strip()[:31] or "Tabla"
    candidato, n = base, 2
    while candidato.lower() in usados:
        sufijo = f" ({n})"
        candidato = base[:31 - len(sufijo)] + sufijo
        n += 1
    usados.add(candidato.lower())
    return candidato


def _span(valor):
    try:
        return max(1, min(int(valor or 1), MAX_COLUMNAS))
    except (TypeError, ValueError):
        return 1


def construir(tablas):
    """Workbook con una hoja por tabla.

    Cada tabla llega como {"nombre": str, "filas": [[{"t", "span", "th"}]]}.
    """
    wb = Workbook()
    wb.remove(wb.active)
    usados = set()
    for tabla in tablas:
        ws = wb.create_sheet(_nombre_hoja(tabla.get("nombre"), usados))
        anchos = {}
        for r, fila in enumerate(tabla.get("filas") or [], 1):
            col = 1
            for celda in fila:
                span = _span(celda.get("span"))
                texto = str(celda.get("t") or "")
                valor, formato = interpreta(texto)
                c = ws.cell(row=r, column=col)
                c.value = valor
                # openpyxl toma como fórmula cualquier texto que empiece con
                # «=», y aquí llegan nombres de clientes que teclea cualquiera.
                if isinstance(valor, str) and valor.startswith("="):
                    c.data_type = "s"
                if formato:
                    c.number_format = formato
                if celda.get("th"):
                    c.font = _CABECERA_FONT
                    c.fill = _CABECERA_FILL
                    c.alignment = Alignment(vertical="center")
                if span > 1:
                    ws.merge_cells(start_row=r, start_column=col,
                                   end_row=r, end_column=col + span - 1)
                else:
                    anchos[col] = max(anchos.get(col, 0), len(texto))
                col += span
        for col, ancho in anchos.items():
            ws.column_dimensions[get_column_letter(col)].width = min(
                max(ancho + 2, 8), 60)
    return wb


def _valida(datos):
    if not isinstance(datos, dict):
        return None
    tablas = datos.get("tablas")
    if not isinstance(tablas, list) or not 0 < len(tablas) <= MAX_TABLAS:
        return None
    celdas = 0
    for tabla in tablas:
        if not isinstance(tabla, dict):
            return None
        filas = tabla.get("filas")
        if not isinstance(filas, list) or len(filas) > MAX_FILAS:
            return None
        for fila in filas:
            if not isinstance(fila, list) or len(fila) > MAX_COLUMNAS:
                return None
            if not all(isinstance(c, dict) for c in fila):
                return None
            celdas += len(fila)
    if celdas > MAX_CELDAS:
        return None
    return tablas


@require_POST
@login_required
def exportar_tablas(request):
    try:
        datos = json.loads(request.body)
    except (ValueError, UnicodeDecodeError):
        return HttpResponseBadRequest("JSON inválido.")
    tablas = _valida(datos)
    if tablas is None:
        return HttpResponseBadRequest("Tablas inválidas.")

    wb = construir(tablas)
    titulo = slugify(str(datos.get("titulo") or "")[:60]) or "tablas"
    resp = HttpResponse(content_type=XLSX_MIME)
    resp["Content-Disposition"] = (
        f'attachment; filename="shake_{titulo}_{localdate().isoformat()}.xlsx"')
    wb.save(resp)
    return resp
