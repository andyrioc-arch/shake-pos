from decimal import Decimal
from django.contrib import admin
from django.utils.html import format_html
from django.utils.safestring import mark_safe
from reversion.admin import VersionAdmin
from .models import (
    ConfiguracionAlarmas, Ingrediente, Receta, RecetaIngrediente, Compra, Venta,
    Extra, VentaSustitucion, VentaExtra, Nota,
)


@admin.register(Nota)
class NotaAdmin(admin.ModelAdmin):
    list_display = ("folio", "creada", "metodo_pago", "total", "cambio")
    list_filter = ("metodo_pago", "fecha")
    readonly_fields = ("token", "creada")
    date_hierarchy = "creada"

admin.site.site_header = "🥤 SHAKE — Control de Inventario"
admin.site.site_title = "SHAKE Inventario"
admin.site.index_title = "Panel de control"


def money(value):
    try:
        return f"${value:,.2f}"
    except (TypeError, ValueError):
        return "-"


def pct(value):
    try:
        return f"{value * 100:.1f}%"
    except (TypeError, ValueError):
        return "-"


# ── Ingredientes ──────────────────────────────────────────────────────────────
@admin.register(Ingrediente)
class IngredienteAdmin(admin.ModelAdmin):
    list_display = (
        "nombre", "categoria", "unidad_compra", "cantidad_por_unidad",
        "unidad_receta", "costo_unidad_compra", "costo_receta_col",
        "stock_col", "estado_col",
    )
    list_filter = ("categoria", "unidad_receta")
    search_fields = ("nombre",)
    # El costo se ajusta con las compras y el catálogo; no se edita en línea
    # aquí (así la columna, oculta para el staff, no entra en list_editable).

    # Columnas con costo que solo ve un superusuario.
    _cols_costo = (
        "costo_unidad_compra", "costo_receta_col", "stock_col", "estado_col",
    )

    def get_list_display(self, request):
        if request.user.is_superuser:
            return self.list_display
        return tuple(c for c in self.list_display if c not in self._cols_costo)

    fieldsets = (
        (None, {
            "fields": ("nombre", "categoria"),
        }),
        ("Unidades y costo", {
            "fields": (
                "unidad_compra", "cantidad_por_unidad",
                "unidad_receta", "costo_unidad_compra",
            ),
            "description": "El costo por unidad de receta se calcula solo "
                           "(costo de compra ÷ cantidad por unidad).",
        }),
    )

    @admin.display(description="Costo/u. receta")
    def costo_receta_col(self, obj):
        return f"${obj.costo_unidad_receta:,.4f}"

    @admin.display(description="Stock disp.")
    def stock_col(self, obj):
        return f"{obj.stock_disponible:,.1f} {obj.unidad_receta}"

    @admin.display(description="Estado")
    def estado_col(self, obj):
        if obj.minimo_para_cinco <= 0:
            return mark_safe('<span style="color:#888;">—</span>')
        if obj.hay_faltante:
            return format_html(
                '<b style="color:#fff;background:#E74C3C;padding:2px 8px;'
                'border-radius:4px;">🔴 FALTA {}</b>',
                f"{obj.faltante:,.1f}",
            )
        return mark_safe(
            '<b style="color:#fff;background:#27AE60;padding:2px 8px;'
            'border-radius:4px;">✅ OK</b>'
        )


# ── Recetas (con ingredientes editables en línea) ─────────────────────────────
class RecetaIngredienteInline(admin.TabularInline):
    model = RecetaIngrediente
    extra = 1
    autocomplete_fields = ("ingrediente",)
    fields = ("ingrediente", "cantidad", "costo_linea_col")
    readonly_fields = ("costo_linea_col",)

    @admin.display(description="Costo línea")
    def costo_linea_col(self, obj):
        if obj.pk:
            return money(obj.costo_linea)
        return "-"


@admin.register(Receta)
class RecetaAdmin(admin.ModelAdmin):
    inlines = [RecetaIngredienteInline]
    list_display = (
        "nombre_col", "perfil", "precio_venta",
        "costo_col", "ganancia_col", "margen_col",
        "vendidos_col", "activa",
    )
    list_filter = ("activa",)
    # precio_venta/activa siempre se muestran; Django ya impide que el staff
    # (solo lectura) los edite, así que list_editable es seguro sin mutaciones.
    list_editable = ("precio_venta", "activa")
    search_fields = ("nombre", "perfil")
    fields = ("nombre", "emoji", "perfil", "tamano", "precio_venta", "activa")

    # Columnas con costo/ganancia/volumen que solo ve un superusuario.
    _cols_costo = ("costo_col", "ganancia_col", "margen_col", "vendidos_col")

    def get_list_display(self, request):
        if request.user.is_superuser:
            return self.list_display
        return tuple(c for c in self.list_display if c not in self._cols_costo)

    @admin.display(description="Shake", ordering="nombre")
    def nombre_col(self, obj):
        return f"{obj.emoji} {obj.nombre}".strip()

    @admin.display(description="Costo receta")
    def costo_col(self, obj):
        return money(obj.costo_receta)

    @admin.display(description="Ganancia/u.")
    def ganancia_col(self, obj):
        return money(obj.ganancia_unitaria)

    @admin.display(description="Margen")
    def margen_col(self, obj):
        return pct(obj.margen)

    @admin.display(description="Vendidos")
    def vendidos_col(self, obj):
        """Todo lo que salió, con lo regalado desglosado si lo hubo."""
        regaladas = obj.unidades_regaladas
        if not regaladas:
            return obj.unidades_vendidas
        return f"{obj.unidades_vendidas} ({regaladas} de cortesía)"


# ── Compras ───────────────────────────────────────────────────────────────────
@admin.register(Compra)
class CompraAdmin(admin.ModelAdmin):
    list_display = (
        "fecha", "ingrediente", "cantidad", "unidad_col",
        "total_col", "proveedor",
    )
    # El unitario ya no se captura: se paga un monto por una cantidad.
    fields = ("fecha", "ingrediente", "cantidad", "monto_total",
              "proveedor", "notas")
    list_filter = ("fecha", "proveedor", "ingrediente__categoria")
    search_fields = ("ingrediente__nombre", "proveedor")
    autocomplete_fields = ("ingrediente",)
    date_hierarchy = "fecha"

    @admin.display(description="Unidad")
    def unidad_col(self, obj):
        return obj.ingrediente.unidad_compra

    @admin.display(description="Total")
    def total_col(self, obj):
        return money(obj.total)


# ── Ventas ────────────────────────────────────────────────────────────────────
class VentaSustitucionInline(admin.TabularInline):
    model = VentaSustitucion
    extra = 0
    autocomplete_fields = ("ingrediente_original", "ingrediente_nuevo")
    verbose_name = "Sustitución (cambiar un ingrediente por otro)"
    verbose_name_plural = "Sustituciones (cambiar un ingrediente por otro)"


class VentaExtraInline(admin.TabularInline):
    model = VentaExtra
    extra = 0
    autocomplete_fields = ("extra",)
    verbose_name = "Extra (espresso, creatina...)"
    verbose_name_plural = "Extras (espresso, creatina...)"


@admin.register(Venta)
class VentaAdmin(VersionAdmin):
    inlines = [VentaSustitucionInline, VentaExtraInline]
    list_display = (
        "fecha", "receta", "cantidad", "metodo_pago", "personalizada_col",
        "precio_col", "ingreso_col", "costo_col", "ganancia_col",
    )
    list_filter = ("fecha", "metodo_pago", "receta")
    search_fields = ("receta__nombre",)
    autocomplete_fields = ("receta",)
    date_hierarchy = "fecha"

    # Ingreso/costo/ganancia solo para superusuario; staff registra ventas
    # pero no ve márgenes ni totales financieros.
    _cols_costo = ("ingreso_col", "costo_col", "ganancia_col")

    def get_list_display(self, request):
        if request.user.is_superuser:
            return self.list_display
        return tuple(c for c in self.list_display if c not in self._cols_costo)

    @admin.display(description="Personalizada")
    def personalizada_col(self, obj):
        marcas = []
        if obj.sustituciones.exists():
            marcas.append("🔄")
        if obj.extras.exists():
            marcas.append("➕")
        return " ".join(marcas) if marcas else "—"

    @admin.display(description="Precio u.")
    def precio_col(self, obj):
        return money(obj.precio_efectivo)

    @admin.display(description="Ingreso")
    def ingreso_col(self, obj):
        return money(obj.ingreso)

    @admin.display(description="Costo total")
    def costo_col(self, obj):
        return money(obj.costo_total)

    @admin.display(description="Ganancia")
    def ganancia_col(self, obj):
        return format_html(
            '<b style="color:#27AE60;">{}</b>', money(obj.ganancia)
        )



@admin.register(Extra)
class ExtraAdmin(admin.ModelAdmin):
    list_display = ("nombre", "ingrediente", "cantidad", "cargo", "costo_col", "activo")
    list_editable = ("cargo", "activo")
    search_fields = ("nombre",)
    autocomplete_fields = ("ingrediente",)

    @admin.display(description="Costo insumo")
    def costo_col(self, obj):
        return money(obj.costo)


@admin.register(ConfiguracionAlarmas)
class ConfiguracionAlarmasAdmin(admin.ModelAdmin):
    list_display = ("__str__",)

    def has_add_permission(self, request):
        # Es un singleton: se edita el registro que ya existe.
        return not ConfiguracionAlarmas.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False
