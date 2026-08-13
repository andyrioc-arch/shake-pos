"""Tanda 1 de P11: quitarle el NOT NULL a `facturado`, sin borrarla.

Se corre ANTES de desplegar, y sola.

`facturado` es NOT NULL y sin default en la base, así que ninguno de los dos
órdenes de despliegue funciona si se borra de un golpe:

- Migrando primero, el código viejo sigue escribiendo la columna en cada alta
  y truena con «column does not exist»: se cae la caja.
- Desplegando primero, el código nuevo ya no la manda en el INSERT y truena
  con «null value violates not-null constraint»: se cae la caja igual.

Dejarla nullable primero abre la ventana donde los dos conviven: el código
viejo la sigue llenando y el nuevo puede omitirla. La columna se borra en la
tanda 2, ya con el código nuevo arriba.
"""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('contabilidad', '0009_cuenta_padre'),
    ]

    operations = [
        migrations.AlterField(
            model_name='movimiento',
            name='facturado',
            field=models.BooleanField(default=False, editable=False, null=True,
                                      verbose_name='Facturado'),
        ),
    ]
