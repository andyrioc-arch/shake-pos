from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import include, path

from inventario import views as inventario_views
from inventario.views import home, panel_inventario, panel_actividad
from finanzas import views as finanzas_views
from finanzas.views import panel_financiero
from contabilidad import views as contabilidad_views
from contabilidad.views import reportes as reportes_contables
from presupuesto import views as presupuesto_views
from presupuesto.views import panel_presupuesto
from lealtad import urls as lealtad_urls

urlpatterns = [
    # Autenticación del sitio (fuera del admin)
    path("login/", auth_views.LoginView.as_view(
        template_name="site/login.html", redirect_authenticated_user=True,
    ), name="login"),
    path("logout/", auth_views.LogoutView.as_view(), name="logout"),

    # Sitio con diseño propio
    path("", home, name="home"),
    path("inventario/", panel_inventario, name="panel_inventario"),
    path("inventario/venta/agregar/", inventario_views.venta_agregar, name="inventario_venta_agregar"),
    path("n/<uuid:token>/", inventario_views.nota_ver, name="nota_ver"),
    path("inventario/compra/agregar/", inventario_views.compra_agregar, name="inventario_compra_agregar"),
    path("actividad/", panel_actividad, name="panel_actividad"),

    # Catálogo de productos (solo superusuario)
    path("catalogo/", inventario_views.panel_catalogo, name="panel_catalogo"),
    path("catalogo/ingrediente/agregar/", inventario_views.ingrediente_agregar, name="ingrediente_agregar"),
    path("catalogo/ingrediente/<int:pk>/eliminar/", inventario_views.ingrediente_eliminar, name="ingrediente_eliminar"),
    path("catalogo/producto/agregar/", inventario_views.producto_agregar, name="producto_agregar"),
    path("catalogo/producto/<int:pk>/editar/", inventario_views.producto_editar, name="producto_editar"),
    path("catalogo/producto/<int:pk>/eliminar/", inventario_views.producto_eliminar, name="producto_eliminar"),
    path("catalogo/producto/<int:pk>/linea/agregar/", inventario_views.receta_linea_agregar, name="receta_linea_agregar"),
    path("catalogo/linea/<int:pk>/eliminar/", inventario_views.receta_linea_eliminar, name="receta_linea_eliminar"),
    path("finanzas/", panel_financiero, name="panel_financiero"),
    path("finanzas/pronostico/guardar/", finanzas_views.pronostico_guardar, name="pronostico_guardar"),
    path("finanzas/inversion/agregar/", finanzas_views.inversion_agregar, name="inversion_agregar"),
    path("finanzas/inversion/<int:pk>/eliminar/", finanzas_views.inversion_eliminar, name="inversion_eliminar"),
    path("contabilidad/", reportes_contables, name="reportes_contables"),
    path("contabilidad/exportar/<str:reporte>/", contabilidad_views.exportar, name="exportar_estado"),
    path("contabilidad/gasto/registrar/", contabilidad_views.gasto_registrar, name="gasto_registrar"),
    path("contabilidad/movimiento/<int:pk>/facturar/", contabilidad_views.movimiento_facturar, name="movimiento_facturar"),
    path("presupuesto/", panel_presupuesto, name="panel_presupuesto"),
    path("presupuesto/venta/guardar/", presupuesto_views.venta_guardar, name="presupuesto_venta_guardar"),
    path("presupuesto/venta/eliminar/", presupuesto_views.venta_eliminar, name="presupuesto_venta_eliminar"),
    path("presupuesto/gasto/guardar/", presupuesto_views.gasto_guardar, name="presupuesto_gasto_guardar"),
    path("presupuesto/gasto/eliminar/", presupuesto_views.gasto_eliminar, name="presupuesto_gasto_eliminar"),
    path("presupuesto/categoria/crear/", presupuesto_views.categoria_crear, name="presupuesto_categoria_crear"),

    # Lealtad: panel interno, tarjeta pública del cliente y API para el ERP
    path("lealtad/", include(lealtad_urls.panel_urls)),
    path("", include(lealtad_urls.publicas)),
    path("api/lealtad/", include(lealtad_urls.api_urls)),

    # Admin estándar de Django (gestión de datos / registro de ventas)
    path("admin/", admin.site.urls),
]
