"""La compra guarda el monto total pagado, no solo el costo unitario.

Andy registra lo que pagó por el bulto; el unitario era una división que hacía
la caja y que pierde centavos. `monto_total` pasa a ser la fuente de verdad y
`costo_unitario` queda como columna derivada, por compatibilidad.

Para las filas que ya existen, `monto_total = cantidad × costo_unitario` es
idéntico a lo que `Compra.total` devolvía antes, así que ningún asiento se
mueve. Se verificó contra producción (12 ago 2026): ninguna de las 10 compras
tiene cantidad fraccionaria ni un producto de más de dos decimales, o sea que
el delta de redondeo es cero. Ver BASELINE-COSTEO.md.

Ambas columnas quedan nullable a propósito: Django suelta el DEFAULT justo
después del ADD COLUMN, así que una columna NOT NULL reventaría la caja en la
ventana entre migrar y desplegar.
"""

import django.core.validators
from decimal import Decimal, ROUND_HALF_UP
from django.db import migrations, models


def rellena_monto_total(apps, schema_editor):
    Compra = apps.get_model("inventario", "Compra")
    centavo = Decimal("0.01")
    for compra in Compra.objects.filter(monto_total__isnull=True):
        if compra.cantidad is None or compra.costo_unitario is None:
            continue
        compra.monto_total = (compra.cantidad * compra.costo_unitario).quantize(
            centavo, rounding=ROUND_HALF_UP)
        compra.save(update_fields=["monto_total"])


def vacia_monto_total(apps, schema_editor):
    """Al revertir, el unitario sigue intacto y `Compra.total` vuelve a usarlo."""
    apps.get_model("inventario", "Compra").objects.update(monto_total=None)


class Migration(migrations.Migration):

    dependencies = [
        ('inventario', '0007_compra_inventario__fecha_ecf680_idx_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='compra',
            name='monto_total',
            field=models.DecimalField(blank=True, decimal_places=2, help_text='Lo que de verdad se pagó por esta compra.', max_digits=12, null=True, validators=[django.core.validators.MinValueValidator(Decimal('0'))], verbose_name='Monto total pagado ($)'),
        ),
        migrations.AlterField(
            model_name='compra',
            name='costo_unitario',
            field=models.DecimalField(blank=True, decimal_places=2, help_text='Derivado del monto total. Se conserva por compatibilidad.', max_digits=12, null=True, validators=[django.core.validators.MinValueValidator(Decimal('0'))], verbose_name='Costo unitario ($)'),
        ),
        migrations.RunPython(rellena_monto_total, vacia_monto_total),
    ]
