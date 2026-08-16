"""Rutas del módulo de lealtad: panel interno, tarjeta pública y API."""

from django.urls import path

from . import api, views

# ── Panel interno (requiere sesión) ──────────────────────────────────────────
panel_urls = [
    path("", views.panel, name="lealtad_panel"),
    path("metricas/", views.panel_metricas, name="lealtad_metricas"),
    path("marketing/", views.panel_marketing, name="lealtad_marketing"),

    path("clientes/", views.clientes, name="lealtad_clientes"),
    path("clientes/crear/", views.cliente_crear, name="lealtad_cliente_crear"),
    path("clientes/<int:pk>/", views.cliente_detalle, name="lealtad_cliente"),
    path("clientes/<int:pk>/editar/", views.cliente_editar,
         name="lealtad_cliente_editar"),
    path("clientes/<int:pk>/canjear/", views.cliente_canjear,
         name="lealtad_cliente_canjear"),
    path("clientes/<int:pk>/ajustar/", views.cliente_ajustar,
         name="lealtad_cliente_ajustar"),
    path("buscar/", views.buscar, name="lealtad_buscar"),

    path("canjes/<int:pk>/entregar/", views.canje_entregar,
         name="lealtad_canje_entregar"),
    path("canjes/<int:pk>/cancelar/", views.canje_cancelar,
         name="lealtad_canje_cancelar"),

    path("descuentos/", views.descuentos, name="lealtad_descuentos"),
    path("descuentos/guardar/", views.descuento_guardar,
         name="lealtad_descuento_guardar"),
    path("descuentos/<int:pk>/eliminar/", views.descuento_eliminar,
         name="lealtad_descuento_eliminar"),

    path("premios/", views.premios, name="lealtad_premios"),
    path("premios/guardar/", views.premio_guardar, name="lealtad_premio_guardar"),
    path("premios/<int:pk>/eliminar/", views.premio_eliminar,
         name="lealtad_premio_eliminar"),
    path("niveles/guardar/", views.nivel_guardar, name="lealtad_nivel_guardar"),
    path("niveles/<int:pk>/eliminar/", views.nivel_eliminar,
         name="lealtad_nivel_eliminar"),
    path("configuracion/guardar/", views.configuracion_guardar,
         name="lealtad_config_guardar"),
    path("promociones/guardar/", views.promocion_guardar,
         name="lealtad_promocion_guardar"),
    path("promociones/<int:pk>/eliminar/", views.promocion_eliminar,
         name="lealtad_promocion_eliminar"),

    path("mensajes/", views.mensajes, name="lealtad_mensajes"),
    path("mensajes/despachar/", views.bandeja_despachar,
         name="lealtad_despachar"),
    path("mensajes/<int:pk>/cancelar/", views.mensaje_cancelar,
         name="lealtad_mensaje_cancelar"),
    path("mensajes/<int:pk>/reintentar/", views.mensaje_reintentar,
         name="lealtad_mensaje_reintentar"),
    path("automatizaciones/guardar/", views.automatizacion_guardar,
         name="lealtad_automatizacion_guardar"),
    path("automatizaciones/<int:pk>/alternar/", views.automatizacion_alternar,
         name="lealtad_automatizacion_alternar"),
    path("automatizaciones/<int:pk>/eliminar/", views.automatizacion_eliminar,
         name="lealtad_automatizacion_eliminar"),
    path("plantillas/guardar/", views.plantilla_guardar,
         name="lealtad_plantilla_guardar"),
    path("campanas/guardar/", views.campana_guardar,
         name="lealtad_campana_guardar"),
    path("campanas/<int:pk>/enviar/", views.campana_enviar,
         name="lealtad_campana_enviar"),
]

# ── Público: lo que ve el cliente en su celular ──────────────────────────────
publicas = [
    path("unete/", views.unete, name="lealtad_unete"),
    path("t/<uuid:token>/", views.tarjeta, name="lealtad_tarjeta"),
    path("t/<uuid:token>/manifest.json", views.tarjeta_manifest,
         name="lealtad_tarjeta_manifest"),
]

# ── API para un ERP externo ──────────────────────────────────────────────────
api_urls = [
    path("customers", api.customers, name="lealtad_api_customers"),
    path("customers/<int:pk>/points", api.customer_points,
         name="lealtad_api_points"),
    path("purchases", api.purchases, name="lealtad_api_purchases"),
    path("rewards/redeem", api.rewards_redeem, name="lealtad_api_redeem"),
    path("campaigns/send", api.campaigns_send, name="lealtad_api_campaigns"),
    path("webhooks/whatsapp", api.webhook_whatsapp, name="lealtad_api_webhook"),

    # Latido del programa. En un servidor normal lo dispara el cron del sistema
    # (`manage.py lealtad_run`); en Vercel no hay cron de SO, así que se llama
    # por HTTP. Sin barra final, como el resto de la API: con ella, APPEND_SLASH
    # respondería un redirect y el cron quedaría sin ejecutarse en silencio.
    path("cron/run", api.cron_run, name="lealtad_api_cron"),
]
