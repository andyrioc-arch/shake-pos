"""Tanda 1 de P11: la compra deja de admitir huecos.

Se corre ANTES de desplegar, junto con `contabilidad.0010`.

Es seguro en ese orden porque el código que hoy está en producción ya llena
las tres: `monto_total` lo captura la caja desde P1, y `cantidad_receta` y
`saldo_receta` las escribe `Compra.save()` en cada alta, venga de donde venga.
Medido contra producción antes de escribir esto: 10 compras, ninguna con
alguno de los tres en nulo.

Lo que compra el cambio: `saldo_receta` nulo era una capa que el FIFO no veía
—se gastaba sin gastarse— y era uno de los síntomas que reporta el panel de
salud. Con el NOT NULL deja de poder existir, en vez de contarse.
"""
import django.core.validators
from decimal import Decimal
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('inventario', '0010_configuracionalarmas'),
    ]

    operations = [
        migrations.AlterField(
            model_name='compra',
            name='monto_total',
            field=models.DecimalField(
                decimal_places=2, max_digits=12,
                help_text='Lo que de verdad se pagó por esta compra.',
                validators=[django.core.validators.MinValueValidator(Decimal('0'))],
                verbose_name='Monto total pagado ($)'),
        ),
        migrations.AlterField(
            model_name='compra',
            name='cantidad_receta',
            field=models.DecimalField(
                decimal_places=4, editable=False, max_digits=14,
                help_text='Cuántas unidades de receta trajo esta compra, '
                          'congeladas al momento de comprarla.',
                verbose_name='Unidades de receta compradas'),
        ),
        migrations.AlterField(
            model_name='compra',
            name='saldo_receta',
            field=models.DecimalField(
                decimal_places=4, editable=False, max_digits=14,
                help_text='Unidades de receta que le quedan sin consumir a '
                          'esta compra. Lo lleva el costeo FIFO.',
                verbose_name='Saldo de la capa'),
        ),
    ]
