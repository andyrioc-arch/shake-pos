"""Crea la cuenta 210 Compras por capturar.

Una venta sin compra que respalde todo lo que consumió ya se reconoce: la parte
sin capa se valúa al último costo conocido del insumo y se abona aquí en vez de
a Inventario, que nunca la recibió. Se vacía sola cuando se captura la compra y
el recosteo mueve ese consumo a una capa real.

No se usa la 201: fue «IVA por pagar» y la 0007 la borró a propósito.

ORDEN DE PUBLICACIÓN: migrar PRIMERO y desplegar después. Agrega algo que el
código nuevo necesita; el viejo no la mira. Después del despliegue hay que
correr `recostear --todo` para que las ventas incompletas que ya existen se
revalúen y generen su asiento.
"""
from django.db import migrations


def crear_cuenta(apps, schema_editor):
    Cuenta = apps.get_model("contabilidad", "Cuenta")
    Cuenta.objects.update_or_create(
        codigo="210",
        defaults={"nombre": "Compras por capturar", "tipo": "pasivo"})


def quitar_cuenta(apps, schema_editor):
    Cuenta = apps.get_model("contabilidad", "Cuenta")
    Cuenta.objects.filter(codigo="210", movimientos__isnull=True).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("contabilidad", "0013_sin_cuentas_puente"),
    ]

    operations = [
        migrations.RunPython(crear_cuenta, quitar_cuenta),
    ]
