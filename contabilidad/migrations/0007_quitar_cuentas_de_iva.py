"""Saca el IVA de la contabilidad.

Los importes se registran completos, tal como entran y salen de caja. El único
lugar donde se desglosa el IVA es la nota que se le entrega al cliente, que lo
calcula por su cuenta en `inventario.models`.

Se verificó contra producción antes de escribir esto: las dos cuentas tenían
cero movimientos, así que no hay historia que reescribir.

ORDEN DE PUBLICACIÓN: desplegar el código PRIMERO y correr esta migración
después. Al revés, el código viejo sigue posteando IVA en la ventana entre
ambos pasos y `_cuenta_segura()` recrea la cuenta 201 sin avisar, dejándola
como pasivo huérfano fuera del catálogo.
"""
from django.db import migrations
from django.db.models import ProtectedError

CUENTAS_IVA = ["105", "201"]


def borrar_cuentas_de_iva(apps, schema_editor):
    Cuenta = apps.get_model("contabilidad", "Cuenta")
    PronosticoFlujoCuenta = apps.get_model("finanzas", "PronosticoFlujoCuenta")

    # Los asientos y el libro apuntan a Cuenta con PROTECT, así que Django
    # frena solo; solo se traduce el error a algo accionable. Los pronósticos
    # de flujo son CASCADE y se borrarían callados: ésos hay que revisarlos a
    # mano.
    pronosticos = PronosticoFlujoCuenta.objects.filter(
        cuenta__codigo__in=CUENTAS_IVA)
    if pronosticos.exists():
        raise RuntimeError(
            f"Hay {pronosticos.count()} pronóstico(s) de flujo colgados de las "
            f"cuentas de IVA {CUENTAS_IVA}. Se borrarían en cascada sin dejar "
            "rastro. Reasígnalos a otra cuenta antes de correr esta migración.")

    try:
        Cuenta.objects.filter(codigo__in=CUENTAS_IVA).delete()
    except ProtectedError as e:
        raise RuntimeError(
            f"Las cuentas de IVA {CUENTAS_IVA} tienen movimientos. Borrarlas "
            "descuadraría los asientos que las usan: reclasifica esos "
            "movimientos antes de correr esta migración.") from e


class Migration(migrations.Migration):
    dependencies = [
        ("contabilidad", "0006_asiento_contabilida_fecha_3506db_idx_and_more"),
        ("finanzas", "0003_pronosticoflujocuenta_delete_pronosticoflujo"),
    ]

    operations = [
        # Al revertir no se recrean: el código viejo las trae en su CATALOGO y
        # `crear_catalogo()` las repone solo.
        migrations.RunPython(borrar_cuentas_de_iva, migrations.RunPython.noop),
    ]
