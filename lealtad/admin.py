from django.contrib import admin
from django.utils.html import format_html
from django.utils.safestring import mark_safe

from .models import (
    Automatizacion, Campana, Canje, Cliente, Compra, ConfiguracionPrograma,
    DescuentoCliente, Mensaje, MovimientoPuntos, Nivel, PaseWallet, Plantilla,
    Premio, PromocionPuntos,
)


@admin.register(ConfiguracionPrograma)
class ConfiguracionProgramaAdmin(admin.ModelAdmin):
    fieldsets = (
        ("Negocio", {"fields": ("nombre_negocio", "activo", "site_url")}),
        ("Puntos", {"fields": ("pesos_por_punto", "vigencia_puntos_meses",
                               "aviso_expiracion_dias")}),
        ("Clientes", {"fields": ("dias_inactividad", "ventana_atribucion_dias")}),
        ("Mensajería", {"fields": ("proveedor_mensajes", "envio_hora_inicio",
                                   "envio_hora_fin")}),
        ("API", {"fields": ("api_token",)}),
    )

    def has_add_permission(self, request):
        return not ConfiguracionPrograma.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Nivel)
class NivelAdmin(admin.ModelAdmin):
    list_display = ("nombre", "emoji", "puntos_requeridos", "clientes_col", "activo")
    list_editable = ("puntos_requeridos", "activo")
    ordering = ("puntos_requeridos",)

    @admin.display(description="Clientes")
    def clientes_col(self, obj):
        siguiente = (Nivel.objects.filter(activo=True,
                                          puntos_requeridos__gt=obj.puntos_requeridos)
                     .order_by("puntos_requeridos").first())
        qs = Cliente.objects.filter(puntos_historicos__gte=obj.puntos_requeridos)
        if siguiente:
            qs = qs.filter(puntos_historicos__lt=siguiente.puntos_requeridos)
        return qs.count()


@admin.register(Premio)
class PremioAdmin(admin.ModelAdmin):
    list_display = ("nombre", "puntos_requeridos", "tipo", "receta",
                    "valor_col", "costo_col", "canjes_col", "activo")
    list_editable = ("puntos_requeridos", "activo")
    list_filter = ("activo", "tipo")
    search_fields = ("nombre", "descripcion")
    ordering = ("orden", "puntos_requeridos")

    @admin.display(description="Valor percibido")
    def valor_col(self, obj):
        return f"${obj.valor_percibido:,.2f}"

    @admin.display(description="Te cuesta")
    def costo_col(self, obj):
        return f"${obj.costo_estimado:,.2f}"

    @admin.display(description="Canjeado")
    def canjes_col(self, obj):
        return obj.canjes.exclude(estado=Canje.Estado.CANCELADO).count()


class MovimientoInline(admin.TabularInline):
    model = MovimientoPuntos
    extra = 0
    can_delete = False
    fields = ("creado", "tipo", "puntos", "saldo_lote", "expira_el", "descripcion")
    readonly_fields = fields
    ordering = ("-creado",)

    def has_add_permission(self, request, obj=None):
        return False


class CompraInline(admin.TabularInline):
    model = Compra
    extra = 0
    fields = ("fecha", "monto", "puntos_ganados", "multiplicador", "origen")
    readonly_fields = fields
    ordering = ("-fecha",)

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(Cliente)
class ClienteAdmin(admin.ModelAdmin):
    list_display = ("telefono_col", "nombre", "nivel_col", "puntos_saldo",
                    "puntos_historicos", "visitas", "gasto_col",
                    "ultima_compra", "estado")
    list_filter = ("estado", "origen", "acepta_mensajes")
    search_fields = ("telefono", "nombre")
    readonly_fields = ("token", "alta", "puntos_saldo", "puntos_historicos",
                       "gasto_historico", "visitas", "ultima_compra",
                       "tarjeta_col")
    inlines = [CompraInline, MovimientoInline]
    ordering = ("-alta",)

    @admin.display(description="Celular", ordering="telefono")
    def telefono_col(self, obj):
        return obj.telefono_display

    @admin.display(description="Nivel")
    def nivel_col(self, obj):
        nivel = obj.nivel
        return str(nivel) if nivel else "—"

    @admin.display(description="Gasto", ordering="gasto_historico")
    def gasto_col(self, obj):
        return f"${obj.gasto_historico:,.2f}"

    @admin.display(description="Tarjeta digital")
    def tarjeta_col(self, obj):
        if not obj.pk:
            return "—"
        url = obj.get_absolute_url()
        return format_html('<a href="{}" target="_blank">{}</a> · código {}',
                           url, url, obj.codigo)


@admin.register(Compra)
class CompraAdmin(admin.ModelAdmin):
    list_display = ("fecha", "cliente", "monto", "puntos_ganados",
                    "multiplicador", "origen", "ticket")
    list_filter = ("origen", "fecha")
    search_fields = ("cliente__telefono", "cliente__nombre", "ticket")
    autocomplete_fields = ("cliente",)
    date_hierarchy = "fecha"


@admin.register(MovimientoPuntos)
class MovimientoPuntosAdmin(admin.ModelAdmin):
    list_display = ("creado", "cliente", "tipo", "puntos", "saldo_lote",
                    "expira_el", "descripcion")
    list_filter = ("tipo",)
    search_fields = ("cliente__telefono", "cliente__nombre")
    autocomplete_fields = ("cliente",)


@admin.register(Canje)
class CanjeAdmin(admin.ModelAdmin):
    list_display = ("codigo", "creado", "cliente", "premio", "puntos_usados",
                    "estado", "vence_el", "autorizado_por")
    list_filter = ("estado", "premio")
    search_fields = ("codigo", "cliente__telefono", "cliente__nombre")
    autocomplete_fields = ("cliente", "premio")
    readonly_fields = ("codigo", "creado", "puntos_usados")


@admin.register(DescuentoCliente)
class DescuentoClienteAdmin(admin.ModelAdmin):
    list_display = ("cliente", "porcentaje", "descripcion", "desde", "hasta",
                    "activo")
    list_filter = ("activo", "desde")
    search_fields = ("cliente__nombre", "cliente__telefono", "descripcion")
    autocomplete_fields = ("cliente",)
    list_editable = ("activo",)


@admin.register(PromocionPuntos)
class PromocionPuntosAdmin(admin.ModelAdmin):
    list_display = ("nombre", "multiplicador", "desde", "hasta", "activa")
    list_editable = ("activa",)


@admin.register(Plantilla)
class PlantillaAdmin(admin.ModelAdmin):
    list_display = ("nombre", "clave", "canal", "plantilla_meta", "activa")
    list_editable = ("activa",)
    prepopulated_fields = {"clave": ("nombre",)}
    fieldsets = (
        (None, {"fields": ("nombre", "clave", "canal", "activa")}),
        ("Texto", {
            "fields": ("cuerpo",),
            "description": mark_safe(
                "Variables disponibles: <code>{nombre}</code> "
                "<code>{saldo}</code> <code>{puntos}</code> "
                "<code>{premio}</code> <code>{faltan}</code> "
                "<code>{nivel}</code> <code>{negocio}</code> "
                "<code>{link}</code> <code>{monto}</code> <code>{dias}</code>"),
        }),
        ("WhatsApp Business", {
            "fields": ("plantilla_meta", "idioma_meta"),
            "classes": ("collapse",),
        }),
    )


@admin.register(Automatizacion)
class AutomatizacionAdmin(admin.ModelAdmin):
    list_display = ("nombre", "disparador", "plantilla", "condicion_col",
                    "hora_envio", "no_repetir_dias", "enviados_col", "activa")
    list_editable = ("activa",)
    list_filter = ("activa", "disparador")
    fieldsets = (
        (None, {"fields": ("nombre", "disparador", "plantilla", "activa")}),
        ("Condición", {
            "fields": ("umbral_porcentaje", "dias", "nivel_objetivo"),
            "description": "Cada disparador usa el campo que le toca: "
                           "% para «cerca de un premio», días para "
                           "inactividad / cumpleaños / caducidad, y nivel "
                           "para «al alcanzar un nivel».",
        }),
        ("Cuándo se envía", {"fields": ("hora_envio", "dias_semana",
                                        "no_repetir_dias", "max_por_cliente")}),
    )

    @admin.display(description="Condición")
    def condicion_col(self, obj):
        D = Automatizacion.Disparador
        if obj.disparador == D.CERCA_PREMIO:
            return f"≥ {obj.umbral_porcentaje}% del premio"
        if obj.disparador == D.INACTIVIDAD:
            return f"{obj.dias} días sin comprar"
        if obj.disparador == D.CUMPLEANOS:
            return f"{obj.dias} días antes"
        if obj.disparador == D.POR_EXPIRAR:
            return f"caducan en {obj.dias} días"
        if obj.disparador == D.NIVEL:
            return str(obj.nivel_objetivo) if obj.nivel_objetivo else "cualquier nivel"
        return "—"

    @admin.display(description="Enviados")
    def enviados_col(self, obj):
        return obj.mensajes.filter(estado__in=Mensaje.ESTADOS_ENVIADOS).count()


@admin.register(Campana)
class CampanaAdmin(admin.ModelAdmin):
    list_display = ("nombre", "segmento", "estado", "programada_para",
                    "enviada_en", "mensajes_col")
    list_filter = ("estado", "segmento")

    @admin.display(description="Mensajes")
    def mensajes_col(self, obj):
        return obj.mensajes.count()


@admin.register(Mensaje)
class MensajeAdmin(admin.ModelAdmin):
    list_display = ("creado", "telefono", "estado", "origen_col",
                    "resumen_col", "atribuida_col")
    list_filter = ("estado", "canal", "automatizacion", "campana")
    search_fields = ("telefono", "cuerpo", "cliente__nombre")
    readonly_fields = ("creado", "enviado_en", "entregado_en", "leido_en",
                       "proveedor_id", "intentos", "error", "enlace_col")

    @admin.display(description="Origen")
    def origen_col(self, obj):
        return obj.automatizacion or obj.campana or "Manual"

    @admin.display(description="Mensaje")
    def resumen_col(self, obj):
        return obj.cuerpo[:70] + ("…" if len(obj.cuerpo) > 70 else "")

    @admin.display(description="Venta atribuida", boolean=True)
    def atribuida_col(self, obj):
        return obj.compra_atribuida_id is not None

    @admin.display(description="Mandar a mano")
    def enlace_col(self, obj):
        if not obj.pk:
            return "—"
        return format_html('<a href="{}" target="_blank">Abrir en WhatsApp</a>',
                           obj.enlace_wa)


@admin.register(PaseWallet)
class PaseWalletAdmin(admin.ModelAdmin):
    list_display = ("cliente", "proveedor", "object_id", "sincronizado", "error")
    list_filter = ("proveedor",)
