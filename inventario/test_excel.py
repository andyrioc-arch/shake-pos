import io
import json
from datetime import date

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse
from openpyxl import load_workbook

from inventario import excel


def celda(t, span=1, th=False):
    return {"t": t, "span": span, "th": th}


class InterpretaTests(TestCase):
    def test_numeros_como_se_ven_en_pantalla(self):
        casos = {
            "$1,234.50": (1234.5, excel.MONEY),
            "-$80.00": (-80.0, excel.MONEY),
            "$-80.00": (-80.0, excel.MONEY),
            "−$12.00": (-12.0, excel.MONEY),
            "12.5%": (0.125, excel.PORCENTAJE),
            "1,200": (1200.0, excel.ENTERO),
            "3.75": (3.75, excel.DECIMAL),
            "31/08/2026": (date(2026, 8, 31), "dd/mm/yyyy"),
        }
        for texto, esperado in casos.items():
            with self.subTest(texto=texto):
                self.assertEqual(excel.interpreta(texto), esperado)

    def test_lo_que_perderia_informacion_se_queda_en_texto(self):
        for texto in ("0042", "5512345678901", "31/02/2026", "Shake fresa",
                      "✓ Reconocido"):
            with self.subTest(texto=texto):
                self.assertEqual(excel.interpreta(texto), (texto, None))

    def test_vacio(self):
        self.assertEqual(excel.interpreta("   "), (None, None))


class ConstruirTests(TestCase):
    def test_una_hoja_por_tabla_con_nombres_validos_y_unicos(self):
        wb = excel.construir([
            {"nombre": "Ventas: agosto/2026", "filas": [[celda("A")]]},
            {"nombre": "Ventas: agosto/2026", "filas": [[celda("B")]]},
            {"nombre": "", "filas": [[celda("C")]]},
        ])
        self.assertEqual(wb.sheetnames,
                         ["Ventas  agosto 2026", "Ventas  agosto 2026 (2)",
                          "Tabla"])

    def test_cabecera_colspan_y_numeros(self):
        wb = excel.construir([{"nombre": "Flujo", "filas": [
            [celda("Mes", th=True), celda("Totales", span=2, th=True)],
            [celda("Agosto"), celda("$1,000.00"), celda("10%")],
        ]}])
        ws = wb["Flujo"]
        self.assertTrue(ws["A1"].font.bold)
        self.assertIn("B1:C1", [str(r) for r in ws.merged_cells.ranges])
        self.assertEqual(ws["B2"].value, 1000.0)
        self.assertEqual(ws["B2"].number_format, excel.MONEY)
        self.assertEqual(ws["C2"].value, 0.1)

    def test_un_texto_con_igual_no_se_vuelve_formula(self):
        wb = excel.construir([{"nombre": "Clientes", "filas": [
            [celda('=HYPERLINK("http://x","clic")')]]}])
        self.assertEqual(wb["Clientes"]["A1"].data_type, "s")

    def test_un_span_que_no_es_numero_no_tumba_el_libro(self):
        wb = excel.construir([{"nombre": "T", "filas": [
            [celda("a", span="dos"), celda("b", span=None)]]}])
        self.assertEqual(wb["T"]["B1"].value, "b")


class ExportarTablasViewTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("caja", password="x")
        self.client.force_login(self.user)
        self.url = reverse("exportar_tablas")

    def _post(self, datos, cliente=None):
        return (cliente or self.client).post(
            self.url, data=json.dumps(datos), content_type="application/json")

    def test_devuelve_un_xlsx_con_lo_que_mando_la_pantalla(self):
        resp = self._post({"titulo": "Panel financiero", "tablas": [
            {"nombre": "Flujo", "filas": [[celda("Mes", th=True)],
                                          [celda("$50.00")]]}]})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp["Content-Type"], excel.XLSX_MIME)
        self.assertRegex(resp["Content-Disposition"],
                         r'^attachment; filename="shake_panel-financiero_'
                         r'\d{4}-\d{2}-\d{2}\.xlsx"$')
        wb = load_workbook(io.BytesIO(resp.content))
        self.assertEqual(wb["Flujo"]["A2"].value, 50)

    def test_requiere_sesion(self):
        resp = self._post({"tablas": [{"filas": []}]},
                          cliente=self.client_class())
        self.assertEqual(resp.status_code, 302)

    def test_solo_post(self):
        self.assertEqual(self.client.get(self.url).status_code, 405)

    def test_rechaza_lo_mal_formado(self):
        fila_larga = [celda("x")] * (excel.MAX_COLUMNAS + 1)
        for datos in ([], {}, {"tablas": []}, {"tablas": ["x"]},
                      {"tablas": [{"filas": "x"}]},
                      {"tablas": [{"filas": [["x"]]}]},
                      {"tablas": [{"filas": [fila_larga]}]}):
            with self.subTest(datos=str(datos)[:40]):
                self.assertEqual(self._post(datos).status_code, 400)
        resp = self.client.post(self.url, data="{no es json",
                                content_type="application/json")
        self.assertEqual(resp.status_code, 400)

    def test_rechaza_demasiadas_celdas_en_total(self):
        fila = [celda("x")] * excel.MAX_COLUMNAS
        tabla = {"filas": [fila] * excel.MAX_FILAS}
        self.assertEqual(self._post({"tablas": [tabla]}).status_code, 400)

    def test_las_paginas_traen_el_boton(self):
        resp = self.client.get(reverse("panel_inventario"))
        self.assertContains(resp, self.url)
