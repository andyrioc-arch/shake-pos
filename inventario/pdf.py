"""La nota en PDF: lo mismo que ve el cliente en pantalla, para guardar.

Se dibuja a mano en vez de convertir el HTML porque el ticket es angosto y de
alto variable, y porque un conversor de HTML a PDF es una dependencia grande
—con navegador embebido— para producir una hoja de sesenta líneas.
"""
import io
from decimal import Decimal

import qrcode
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas as rl_canvas

ANCHO = 80 * mm                 # ticket de mostrador
MARGEN = 7 * mm

# Alturas de cada bloque. Se suman antes de dibujar porque la hoja se crea con
# su tamaño final: un PDF de alto fijo deja media página en blanco o corta.
#
# Cada constante se arma de los pasos que el dibujo va a dar, no de un número
# redondo aparte: reserva y dibujo tienen que coincidir, y dos listas de
# medidas mantenidas a mano divergen a la primera columna nueva.
ALTO_CABEZA = 26 * mm
ALTO_META = 9 * mm
ALTO_LINEA = 6 * mm
ALTO_DETALLE = 4 * mm
ALTO_PAGO = 7 * mm
ALTO_HITO = 6 * mm

PASO_SUBTOTAL = 5 * mm
PASO_IVA = 4 * mm
PASO_TOTAL = 6 * mm
ALTO_TOTALES = 2 * mm + PASO_SUBTOTAL + PASO_IVA + PASO_TOTAL + 5 * mm

ALTO_LEALTAD = 2 * mm + 5 * mm + 11 * mm

LADO_QR = 32 * mm
ALTO_QR = LADO_QR + 8 * mm
ALTO_PIE = 12 * mm


#: Símbolos que sí significan algo y hay que traducir antes de tirarlos. Una
#: sustitución que pierde su flecha queda como «Plátano  Fresa», que no dice
#: cuál entró y cuál salió.
TRADUCE = {"→": "->", "←": "<-", "×": "x", "·": "-", "≥": ">=", "≤": "<="}


def _limpio(texto):
    """Deja solo lo que las fuentes base de PDF saben dibujar.

    Los nombres de producto traen emoji («🍫 Afterparty Shake») y Helvetica no
    los tiene: sin esto salen como cuadros negros. Se quitan en vez de sustituir
    la fuente, porque empacar una tipografía con emoji para un ticket pesa más
    que todo lo demás junto. Los acentos sí sobreviven: latin-1 los cubre.
    """
    texto = texto or ""
    for simbolo, reemplazo in TRADUCE.items():
        texto = texto.replace(simbolo, reemplazo)
    limpio = texto.encode("latin-1", "ignore").decode("latin-1")
    return " ".join(limpio.split())        # el hueco que deja un emoji sobra


def _dinero(valor):
    return f"${Decimal(valor or 0):,.2f}"


def _alto_total(lineas, pago, lealtad):
    alto = ALTO_CABEZA + ALTO_META + ALTO_TOTALES + ALTO_QR + ALTO_PIE
    for linea in lineas:
        alto += ALTO_LINEA
        if linea.get("extras") or linea.get("subs"):
            alto += ALTO_DETALLE
    if pago:
        alto += ALTO_PAGO
    if lealtad:
        alto += ALTO_LEALTAD
        alto += ALTO_HITO * len(lealtad.get("hitos", []))
    return alto


def _dibuja_qr(c, url, centro_x, tope_y, lado):
    """Dibuja el QR como cuadros negros, sin pasar por una imagen.

    `qrcode` sabe entregar la matriz; pintarla con rectángulos evita cargar
    Pillow solo para rasterizar algo que ya es geometría.
    """
    matriz = qrcode.QRCode(border=2)
    matriz.add_data(url)
    matriz.make(fit=True)
    filas = matriz.get_matrix()
    modulo = lado / len(filas)
    x0 = centro_x - lado / 2
    y0 = tope_y - lado
    c.setFillColorRGB(0, 0, 0)
    for i, fila in enumerate(filas):
        for j, prendido in enumerate(fila):
            if prendido:
                c.rect(x0 + j * modulo, y0 + (len(filas) - 1 - i) * modulo,
                       modulo, modulo, stroke=0, fill=1)


def nota_pdf(nota, lineas, url, lealtad=None):
    """Devuelve los bytes del PDF de una nota."""
    pago = nota.pago_con is not None and nota.metodo_pago == "efectivo"
    alto = _alto_total(lineas, pago, lealtad)

    buffer = io.BytesIO()
    c = rl_canvas.Canvas(buffer, pagesize=(ANCHO, alto))
    c.setTitle(f"Nota {nota.folio} - SHAKE")

    y = alto - MARGEN

    # ── Cabeza ────────────────────────────────────────────────────────────
    c.setFont("Helvetica-Bold", 22)
    c.drawCentredString(ANCHO / 2, y - 16, "SHAKE.")
    c.setFont("Helvetica", 8)
    c.drawCentredString(ANCHO / 2, y - 28, "Gracias por tu compra")
    y -= ALTO_CABEZA

    # ── Folio y fecha ─────────────────────────────────────────────────────
    c.setFont("Helvetica-Bold", 8)
    c.drawString(MARGEN, y, f"Nota {nota.folio}")
    c.setFont("Helvetica", 8)
    c.drawRightString(ANCHO - MARGEN, y, nota.creada.strftime("%d/%m/%Y %H:%M"))
    c.line(MARGEN, y - 4 * mm, ANCHO - MARGEN, y - 4 * mm)
    y -= ALTO_META

    # ── Productos ─────────────────────────────────────────────────────────
    for linea in lineas:
        c.setFont("Helvetica-Bold", 9)
        c.drawString(MARGEN, y,
                     _limpio(f"{linea['cantidad']}x {linea['nombre']}"))
        c.drawRightString(ANCHO - MARGEN, y, _dinero(linea["importe"]))
        y -= ALTO_LINEA
        detalles = list(linea.get("extras") or []) + list(linea.get("subs") or [])
        if detalles:
            c.setFont("Helvetica", 7)
            c.drawString(MARGEN + 2 * mm, y + 2 * mm,
                         _limpio(" - ".join(detalles))[:60])
            y -= ALTO_DETALLE

    # ── Totales ───────────────────────────────────────────────────────────
    y -= 2 * mm
    c.line(MARGEN, y, ANCHO - MARGEN, y)
    y -= PASO_SUBTOTAL
    c.setFont("Helvetica", 8)
    c.drawString(MARGEN, y, "Subtotal")
    c.drawRightString(ANCHO - MARGEN, y, _dinero(nota.subtotal))
    y -= PASO_IVA
    c.drawString(MARGEN, y, "IVA (16%)")
    c.drawRightString(ANCHO - MARGEN, y, _dinero(nota.iva))
    y -= PASO_TOTAL
    c.setFont("Helvetica-Bold", 13)
    c.drawString(MARGEN, y, "Total")
    c.drawRightString(ANCHO - MARGEN, y, _dinero(nota.total))
    y -= 5 * mm

    if pago:
        c.setFont("Helvetica", 8)
        c.drawString(MARGEN, y, f"Pagó con {_dinero(nota.pago_con)}")
        c.drawRightString(ANCHO - MARGEN, y, f"Cambio {_dinero(nota.cambio)}")
        y -= ALTO_PAGO

    # ── Lealtad ───────────────────────────────────────────────────────────
    if lealtad:
        y -= 2 * mm
        c.setFont("Helvetica-Bold", 9)
        c.drawCentredString(ANCHO / 2, y, _limpio(lealtad["titulo"]))
        y -= 5 * mm
        c.setFont("Helvetica", 8)
        c.drawCentredString(ANCHO / 2, y, _limpio(lealtad["saldo"]))
        y -= 11 * mm
        for hito in lealtad.get("hitos", []):
            c.setFont("Helvetica-Bold", 8)
            c.drawCentredString(ANCHO / 2, y, _limpio(hito))
            y -= ALTO_HITO

    # ── QR ────────────────────────────────────────────────────────────────
    _dibuja_qr(c, url, ANCHO / 2, y, LADO_QR)
    y -= LADO_QR + 4 * mm
    c.setFont("Helvetica", 7)
    c.drawCentredString(ANCHO / 2, y, "Escanea para ver tu nota en linea")

    c.showPage()
    c.save()
    return buffer.getvalue()
