"""Tanda 2 de P11: se va `Compra.costo_unitario`.

Se corre DESPUÉS de desplegar. El código viejo todavía manda la columna en
cada alta de compra, y además la lee como respaldo de `total` para filas sin
`monto_total` —que ya no existen, pero el camino sigue en el código—.

Con el código nuevo arriba, el unitario pasa a ser una propiedad derivada:
`monto_total / cantidad`, solo para mostrar. Nunca para reconstruir el total,
porque la división pierde centavos y la multiplicación no los recupera. Era
exactamente el bug que P1 vino a cerrar, y guardar el derivado al lado del
dato es cómo vuelve a entrar.
"""
from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('inventario', '0011_la_compra_sin_huecos'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='compra',
            name='costo_unitario',
        ),
    ]
