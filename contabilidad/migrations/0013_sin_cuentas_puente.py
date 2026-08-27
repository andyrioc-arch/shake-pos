"""Quita las cuentas puente 106, 116 y 202.

Andy: esos montos deben ir a ventas/gastos/inventario como el resto, y las
cuentas ya no existir. Una venta incompleta no postea efectivo hasta costear
(decisión del 27 ago 2026).

ORDEN DE PUBLICACIÓN: desplegar el código PRIMERO y correr esta migración
después. Al revés, el código viejo sigue posteando a los puentes y
`_cuenta_segura()` los recrea fuera del catálogo.
"""
from django.db import migrations
from django.db.models import ProtectedError

PUENTES = ["106", "116", "202"]


def quitar_puentes(apps, schema_editor):
    """Reescribe asientos al esquema sin puentes y borra las tres cuentas."""
    # Importa el módulo vivo: la lógica de posteo es la del código desplegado.
    from contabilidad import posting
    from contabilidad.models import Movimiento, Asiento, Cuenta
    from inventario.models import Venta, Compra
    from finanzas.models import PronosticoFlujoCuenta

    posting.crear_catalogo()

    for v in Venta.objects.all().iterator():
        posting.sincronizar_venta(v)
    for c in Compra.objects.all().iterator():
        posting.sincronizar_compra(c)
    for m in Movimiento.objects.filter(tipo=Movimiento.Tipo.GASTO).iterator():
        posting.sincronizar_movimiento(m)

    # Asientos huérfanos del esquema viejo (por si quedó alguno sin Movimiento).
    Asiento.objects.filter(
        automatico=True,
        referencia__endswith=" flujo",
    ).delete()

    pronosticos = PronosticoFlujoCuenta.objects.filter(cuenta__codigo__in=PUENTES)
    if pronosticos.exists():
        raise RuntimeError(
            f"Hay {pronosticos.count()} pronóstico(s) de flujo colgados de "
            f"las cuentas puente {PUENTES}. Reasígnalos antes de migrar.")

    try:
        Cuenta.objects.filter(codigo__in=PUENTES).delete()
    except ProtectedError as e:
        raise RuntimeError(
            f"Las cuentas {PUENTES} aún tienen movimientos. Corre "
            "`sincronizar_contabilidad` y vuelve a intentar.") from e


class Migration(migrations.Migration):
    dependencies = [
        ("contabilidad", "0012_cortesias_a_costo_de_ventas"),
        ("finanzas", "0003_pronosticoflujocuenta_delete_pronosticoflujo"),
        # El RunPython importa el posteo vivo, que lee Venta/Compra con todas
        # sus columnas (descuento_pct, capas, etc.).
        ("inventario", "0015_merma_de_inventario"),
    ]

    operations = [
        migrations.RunPython(quitar_puentes, migrations.RunPython.noop),
    ]
