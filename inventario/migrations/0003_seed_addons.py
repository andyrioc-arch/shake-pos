from decimal import Decimal

from django.db import migrations

# Add-ons fijos: cada uno suma $10 a la venta.
ADDONS = ["Shot de espresso", "Creatina", "Colágeno"]


def crear(apps, schema_editor):
    Ingrediente = apps.get_model("inventario", "Ingrediente")
    Extra = apps.get_model("inventario", "Extra")
    for nombre in ADDONS:
        ing, _ = Ingrediente.objects.get_or_create(
            nombre=nombre,
            defaults=dict(
                categoria="otro", unidad_compra="porción",
                cantidad_por_unidad=Decimal("1"), unidad_receta="porción",
                costo_unidad_compra=Decimal("0"),
            ),
        )
        Extra.objects.get_or_create(
            nombre=nombre,
            defaults=dict(
                ingrediente=ing, cantidad=Decimal("1"),
                cargo=Decimal("10"), activo=True,
            ),
        )


def borrar(apps, schema_editor):
    Extra = apps.get_model("inventario", "Extra")
    Ingrediente = apps.get_model("inventario", "Ingrediente")
    Extra.objects.filter(nombre__in=ADDONS).delete()
    Ingrediente.objects.filter(nombre__in=ADDONS).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("inventario", "0002_extra_ventaextra_ventasustitucion"),
    ]
    operations = [migrations.RunPython(crear, borrar)]
