"""La nota guarda a nombre de quién va el pedido y cuándo se entregó.

Las dos columnas nacen aceptando nulos, como manda la regla del proyecto:
Django suelta el DEFAULT justo después del ADD COLUMN, y en la ventana entre
migrar y desplegar el código viejo sigue insertando notas sin ellas.

Las notas que ya existen se dan por entregadas. Sin ese relleno, la lista de
pendientes abriría con todo el histórico dentro —hoy son las 8 ventas de
prueba, mañana serían las de ayer— y lo primero que haría el mostrador es
aprender a ignorarla. Se usa `creada` y no la hora de correr la migración
para que ninguna nota quede entregada antes de existir.
"""

from django.db import migrations, models


def dar_por_entregadas(apps, schema_editor):
    Nota = apps.get_model("inventario", "Nota")
    Nota.objects.filter(entregada_en__isnull=True).update(
        entregada_en=models.F("creada"))


def volver_a_pendientes(apps, schema_editor):
    """Al revertir, la columna se va con la tabla; esto es solo simetría."""
    apps.get_model("inventario", "Nota").objects.update(entregada_en=None)


class Migration(migrations.Migration):

    dependencies = [
        ('inventario', '0012_borrar_costo_unitario'),
    ]

    operations = [
        migrations.AddField(
            model_name='nota',
            name='entregada_en',
            field=models.DateTimeField(blank=True, help_text='Cuándo salió el pedido del mostrador. Vacío = pendiente.', null=True, verbose_name='Entregada'),
        ),
        migrations.AddField(
            model_name='nota',
            name='nombre_cliente',
            field=models.CharField(blank=True, help_text='Para cantar el pedido cuando esté listo.', max_length=80, verbose_name='A nombre de'),
        ),
        migrations.RunPython(dar_por_entregadas, volver_a_pendientes),
    ]
