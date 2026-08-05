from django import forms
from django.contrib import admin

from contabilidad.models import categorias_gasto_choices, etiqueta_categoria_gasto
from .models import PresupuestoVenta, PresupuestoGasto


@admin.register(PresupuestoVenta)
class PresupuestoVentaAdmin(admin.ModelAdmin):
    list_display = ("anio", "mes", "monto", "notas")
    list_filter = ("anio",)
    list_editable = ("monto",)
    ordering = ("-anio", "-mes")


@admin.register(PresupuestoGasto)
class PresupuestoGastoAdmin(admin.ModelAdmin):
    list_display = ("anio", "mes", "categoria_col", "monto", "notas")
    list_filter = ("anio", "categoria")
    list_editable = ("monto",)
    ordering = ("-anio", "-mes", "categoria")

    def formfield_for_dbfield(self, db_field, request, **kwargs):
        if db_field.name == "categoria":
            kwargs["widget"] = forms.Select(choices=categorias_gasto_choices())
        return super().formfield_for_dbfield(db_field, request, **kwargs)

    @admin.display(description="Categoría", ordering="categoria")
    def categoria_col(self, obj):
        return etiqueta_categoria_gasto(obj.categoria)
