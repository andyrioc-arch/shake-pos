from django.db import migrations

# (clave, nombre, cuenta_contable) de las categorías base existentes.
BASE = [
    ("insumos", "Insumos / Mercancía", "501"),
    ("renta", "Renta", "502"),
    ("servicios", "Servicios", "503"),
    ("mercadotecnia", "Mercadotecnia", "504"),
    ("sueldos", "Sueldos", "505"),
    ("otro", "Otro gasto", "509"),
]


def crear(apps, schema_editor):
    CategoriaGasto = apps.get_model("contabilidad", "CategoriaGasto")
    for clave, nombre, cuenta in BASE:
        CategoriaGasto.objects.update_or_create(
            clave=clave,
            defaults={"nombre": nombre, "cuenta_codigo": cuenta, "activo": True},
        )


def borrar(apps, schema_editor):
    CategoriaGasto = apps.get_model("contabilidad", "CategoriaGasto")
    CategoriaGasto.objects.filter(clave__in=[c[0] for c in BASE]).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("contabilidad", "0002_categoriagasto_alter_facturagasto_tipo"),
    ]
    operations = [migrations.RunPython(crear, borrar)]
