"""Tanda 2 de P11: se van las dos columnas del botón «Facturado».

Se corre DESPUÉS de desplegar, no antes: hasta que el código nuevo esté
arriba, el viejo sigue mandando `facturado` y `fecha_factura` en cada INSERT
del libro, y sin las columnas eso tumba la caja.

A partir de aquí, revertir el despliegue ya no basta para volver atrás: hay
que restaurar respaldo. Por eso el plan las dejó hasta el final.
"""
from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('contabilidad', '0010_facturado_deja_de_ser_obligatorio'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='movimiento',
            name='facturado',
        ),
        migrations.RemoveField(
            model_name='movimiento',
            name='fecha_factura',
        ),
    ]
