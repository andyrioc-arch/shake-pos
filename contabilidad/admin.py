from django.contrib import admin
from django.utils.html import format_html
from django.utils.safestring import mark_safe
from .models import (
    Cuenta, Asiento, MovimientoContable, CategoriaGasto, Movimiento,
)


@admin.register(Movimiento)
class MovimientoAdmin(admin.ModelAdmin):
    list_display = ("fecha", "tipo", "descripcion", "monto", "cuenta",
                    "asiento_reconocimiento")
    # `asiento_reconocimiento` es FK nullable, y el select_related automático
    # del admin no sigue nullables: sin esto, una consulta por fila.
    list_select_related = ("cuenta", "asiento_reconocimiento")
    list_filter = ("tipo", "fecha")
    search_fields = ("descripcion",)
    date_hierarchy = "fecha"
    readonly_fields = ("tipo", "venta", "compra", "asiento_flujo",
                       "asiento_reconocimiento", "creado")


def money(v):
    try:
        return f"${v:,.2f}"
    except (TypeError, ValueError):
        return "-"


@admin.register(CategoriaGasto)
class CategoriaGastoAdmin(admin.ModelAdmin):
    list_display = ("nombre", "clave", "cuenta_codigo", "activo")
    list_editable = ("activo",)
    search_fields = ("nombre", "clave")
    prepopulated_fields = {"clave": ("nombre",)}


@admin.register(Cuenta)
class CuentaAdmin(admin.ModelAdmin):
    list_display = ("codigo", "nombre", "tipo", "saldo_col")
    list_filter = ("tipo",)
    search_fields = ("codigo", "nombre")
    ordering = ("codigo",)

    @admin.display(description="Saldo")
    def saldo_col(self, obj):
        return money(obj.saldo)


class MovimientoInline(admin.TabularInline):
    model = MovimientoContable
    extra = 0
    autocomplete_fields = ("cuenta",)
    fields = ("cuenta", "debe", "haber")


@admin.register(Asiento)
class AsientoAdmin(admin.ModelAdmin):
    inlines = [MovimientoInline]
    list_display = ("id", "fecha", "concepto", "referencia",
                    "debe_col", "haber_col", "estado_col", "automatico")
    list_filter = ("automatico", "fecha")
    search_fields = ("concepto", "referencia")
    date_hierarchy = "fecha"

    @admin.display(description="Debe")
    def debe_col(self, obj):
        return money(obj.total_debe)

    @admin.display(description="Haber")
    def haber_col(self, obj):
        return money(obj.total_haber)

    @admin.display(description="Cuadra")
    def estado_col(self, obj):
        if obj.cuadrado:
            return mark_safe('<b style="color:#fff;background:#27AE60;'
                             'padding:2px 8px;border-radius:4px;">✅</b>')
        return mark_safe('<b style="color:#fff;background:#E74C3C;'
                         'padding:2px 8px;border-radius:4px;">❌</b>')


