"""Las cortesías (506) se leen dentro de Mercadotecnia (504).

Regla de Andy: regalar un producto es gasto de mercadotecnia, y el reporte lo
tiene que decir así. Lo que NO cambia es el posteo: los asientos siguen yendo a
la 506. Solo se agrupa al presentar.

POR QUÉ UN CAMPO `padre` Y NO RENOMBRAR LA 506 A «504.01». `crear_catalogo()`
usa `update_or_create(codigo=...)`, así que renombrarla dejaría una 506 huérfana
más una 504.01 nueva. Los asientos sobrevivirían —`MovimientoContable.cuenta` es
FK, no código— pero el catálogo quedaría sucio y con dos cuentas para lo mismo.

La 504 recibe posteos propios (`registrar_gasto` con categoría mercadotecnia) Y
es padre: el reporte debe mostrar su saldo propio y el de sus hijas por separado,
y sumarlos.

No toca un solo asiento. Totalmente reversible.
"""
import django.db.models.deletion
from django.db import migrations, models


def emparentar_cortesias(apps, schema_editor):
    """Cuelga la 506 de la 504, si ambas existen.

    Defensivo a propósito: en una base recién creada el catálogo aún no está,
    y `crear_catalogo()` hace la misma asignación de forma idempotente cuando
    corra. Así la migración nunca falla por orden de arranque.
    """
    Cuenta = apps.get_model("contabilidad", "Cuenta")
    madre = Cuenta.objects.filter(codigo="504").first()
    hija = Cuenta.objects.filter(codigo="506").first()
    if madre and hija:
        hija.padre = madre
        hija.save(update_fields=["padre"])


def desemparentar(apps, schema_editor):
    Cuenta = apps.get_model("contabilidad", "Cuenta")
    Cuenta.objects.filter(codigo="506").update(padre=None)


class Migration(migrations.Migration):
    dependencies = [
        ("contabilidad", "0008_reconocimiento_automatico"),
    ]

    operations = [
        migrations.AddField(
            model_name="cuenta",
            name="padre",
            field=models.ForeignKey(
                blank=True, null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="subcuentas", to="contabilidad.cuenta",
                verbose_name="Cuenta padre",
                help_text="Solo para agrupar en los reportes. "
                          "No cambia dónde se postea."),
        ),
        migrations.RunPython(emparentar_cortesias, desemparentar),
    ]
