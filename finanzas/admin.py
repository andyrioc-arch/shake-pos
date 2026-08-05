from django.contrib import admin
from .models import (
    CostoFijo, InversionInicial, MovimientoEfectivo, PronosticoFlujoCuenta,
)


@admin.register(PronosticoFlujoCuenta)
class PronosticoFlujoCuentaAdmin(admin.ModelAdmin):
    list_display = ("anio", "mes", "cuenta", "monto")
    list_filter = ("anio", "cuenta")
    ordering = ("-anio", "-mes", "cuenta")


@admin.register(CostoFijo)
class CostoFijoAdmin(admin.ModelAdmin):
    list_display = ("concepto", "categoria", "monto_mensual", "activo")
    list_filter = ("categoria", "activo")
    list_editable = ("monto_mensual", "activo")
    search_fields = ("concepto",)

    def changelist_view(self, request, extra_context=None):
        total = CostoFijo.total_mensual()
        extra_context = extra_context or {}
        extra_context["title"] = f"Costos fijos — Total mensual: ${total:,.2f}"
        return super().changelist_view(request, extra_context)


@admin.register(InversionInicial)
class InversionInicialAdmin(admin.ModelAdmin):
    list_display = ("concepto", "categoria", "monto", "fecha")
    list_filter = ("categoria",)
    list_editable = ("monto",)
    search_fields = ("concepto",)

    def changelist_view(self, request, extra_context=None):
        total = InversionInicial.total()
        extra_context = extra_context or {}
        extra_context["title"] = f"Inversión inicial — Total: ${total:,.2f}"
        return super().changelist_view(request, extra_context)


@admin.register(MovimientoEfectivo)
class MovimientoEfectivoAdmin(admin.ModelAdmin):
    list_display = ("fecha", "tipo", "concepto", "monto")
    list_filter = ("tipo", "fecha")
    search_fields = ("concepto",)
    date_hierarchy = "fecha"
