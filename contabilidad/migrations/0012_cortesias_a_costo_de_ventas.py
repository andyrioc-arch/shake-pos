"""Las cortesías dejan de tener cuenta propia y pasan a Costo de ventas (501).

Andy: regalar un producto es costo de lo que se produjo, no mercadotecnia.
El desglose «cuánto del COGS fue cortesía» vive en el Estado de resultados,
no en una cuenta 506.

ORDEN DE PUBLICACIÓN: desplegar el código PRIMERO y correr esta migración
después. Al revés, el código viejo sigue posteando a 506 y `_cuenta_segura()`
la recrea fuera del catálogo.
"""
from django.db import migrations
from django.db.models import ProtectedError


def mover_cortesias_a_cogs(apps, schema_editor):
    Cuenta = apps.get_model("contabilidad", "Cuenta")
    MovimientoContable = apps.get_model("contabilidad", "MovimientoContable")
    PronosticoFlujoCuenta = apps.get_model("finanzas", "PronosticoFlujoCuenta")

    c506 = Cuenta.objects.filter(codigo="506").first()
    if c506 is None:
        return

    c501 = Cuenta.objects.filter(codigo="501").first()
    if c501 is None:
        raise RuntimeError(
            "No existe la cuenta 501 Costo de ventas. Créala con "
            "`crear_catalogo()` antes de correr esta migración.")

    MovimientoContable.objects.filter(cuenta=c506).update(cuenta=c501)

    # Sin padre, o PROTECT frena el borrado si 504 sigue apuntándola.
    if c506.padre_id is not None:
        c506.padre_id = None
        c506.save(update_fields=["padre_id"])

    pronosticos = PronosticoFlujoCuenta.objects.filter(cuenta=c506)
    if pronosticos.exists():
        raise RuntimeError(
            f"Hay {pronosticos.count()} pronóstico(s) de flujo colgados de la "
            "506. Reasígnalos a otra cuenta antes de correr esta migración.")

    try:
        c506.delete()
    except ProtectedError as e:
        raise RuntimeError(
            "La 506 todavía tiene referencias. Reclasifica lo que quede "
            "antes de correr esta migración.") from e


class Migration(migrations.Migration):
    dependencies = [
        ("contabilidad", "0011_borrar_las_reliquias_del_boton"),
        ("finanzas", "0003_pronosticoflujocuenta_delete_pronosticoflujo"),
    ]

    operations = [
        # Al revertir no se recrea: el código viejo la trae en su CATALOGO y
        # `crear_catalogo()` la repone solo.
        migrations.RunPython(mover_cortesias_a_cogs, migrations.RunPython.noop),
    ]
