"""El costo de ventas deja de recalcularse y pasa a ser un hecho guardado.

Cada compra se vuelve una capa de inventario con su saldo, cada venta guarda el
costo real de las capas que la surtieron, y `ConsumoCapa` deja el rastro de qué
capa surtió a cuál —que es lo que permite deshacer el costeo cuando entra una
compra con fecha retroactiva, o cuando se borra una venta.

Las capas nacen llenas y las ventas nacen sin costear (`costo_fifo = NULL`).
Poblarlas es trabajo de `manage.py recostear --todo`, que se corre DESPUÉS de
desplegar: hacerlo aquí obligaría a duplicar toda la lógica de costeo contra
modelos históricos.

Todas las columnas nacen aceptando nulos o con `db_default`, porque Django
suelta el DEFAULT justo después del ADD COLUMN y el código viejo sigue
escribiendo en la ventana entre migrar y desplegar. Las compras que se capturen
en esa ventana nacerán sin saldo; `recostear --todo` las reabre y el comando
las reporta como «capas sin abrir» para que no pasen inadvertidas.
"""

import django.db.models.deletion
from decimal import Decimal
from django.db import migrations, models


def abre_las_capas(apps, schema_editor):
    """Cada compra existente arranca con toda su cantidad disponible."""
    Compra = apps.get_model("inventario", "Compra")
    por_guardar = []
    for compra in Compra.objects.select_related("ingrediente").iterator():
        cpu = compra.ingrediente.cantidad_por_unidad or Decimal("0")
        compra.cantidad_receta = (compra.cantidad or Decimal("0")) * cpu
        compra.saldo_receta = compra.cantidad_receta
        por_guardar.append(compra)
    Compra.objects.bulk_update(por_guardar, ["cantidad_receta", "saldo_receta"],
                               batch_size=500)


def cierra_las_capas(apps, schema_editor):
    apps.get_model("inventario", "Compra").objects.update(
        cantidad_receta=None, saldo_receta=None)


class Migration(migrations.Migration):

    dependencies = [
        ('inventario', '0008_compra_monto_total'),
    ]

    operations = [
        migrations.AddField(
            model_name='compra',
            name='cantidad_receta',
            field=models.DecimalField(blank=True, decimal_places=4, editable=False, help_text='Cuántas unidades de receta trajo esta compra, congeladas al momento de comprarla.', max_digits=14, null=True, verbose_name='Unidades de receta compradas'),
        ),
        migrations.AddField(
            model_name='compra',
            name='saldo_receta',
            field=models.DecimalField(blank=True, decimal_places=4, editable=False, help_text='Unidades de receta que le quedan sin consumir a esta compra. Lo lleva el costeo FIFO.', max_digits=14, null=True, verbose_name='Saldo de la capa'),
        ),
        migrations.AddField(
            model_name='venta',
            name='costeada_en',
            field=models.DateTimeField(blank=True, editable=False, null=True),
        ),
        migrations.AddField(
            model_name='venta',
            name='costo_fifo',
            field=models.DecimalField(blank=True, decimal_places=2, editable=False, help_text='Costo de las capas de compra que surtieron esta venta. Vacío significa «sin costear», nunca cero.', max_digits=12, null=True, verbose_name='Costo real (FIFO)'),
        ),
        migrations.AddField(
            model_name='venta',
            name='costo_incompleto',
            field=models.BooleanField(db_default=False, default=False, editable=False, help_text='Faltaron compras para surtir parte del consumo: el costo solo cubre lo que sí tenía capa.', verbose_name='Costo incompleto'),
        ),
        migrations.CreateModel(
            name='ConsumoCapa',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('cantidad_receta', models.DecimalField(decimal_places=4, max_digits=14)),
                ('costo_unitario', models.DecimalField(decimal_places=6, default=Decimal('0'), max_digits=14)),
                ('importe', models.DecimalField(decimal_places=4, default=Decimal('0'), max_digits=14)),
                ('compra', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name='consumos', to='inventario.compra')),
                ('ingrediente', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, to='inventario.ingrediente')),
                ('venta', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='consumos', to='inventario.venta')),
            ],
            options={
                'verbose_name': 'Consumo de capa',
                'verbose_name_plural': 'Consumos de capas',
                'indexes': [models.Index(fields=['ingrediente', 'venta'], name='inventario__ingredi_bd596e_idx'), models.Index(fields=['compra'], name='inventario__compra__af79d9_idx')],
            },
        ),
        migrations.RunPython(abre_las_capas, cierra_las_capas),
    ]
