"""Código de cliente único, y devoluciones de canje como tipo propio.

El código corto se derivaba del token (`token.hex[:6]`), sin ninguna garantía
de unicidad: con unos miles de clientes las colisiones son casi seguras y el
cajero podía acreditarle los puntos a la persona equivocada. Ahora es una
columna única de verdad.
"""

import secrets

from django.db import migrations, models

ALFABETO = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


def genera_codigos(apps, schema_editor):
    """Le da un código único a cada cliente que ya existía."""
    Cliente = apps.get_model("lealtad", "Cliente")
    usados = set(Cliente.objects.exclude(codigo="").exclude(codigo=None)
                 .values_list("codigo", flat=True))
    for cliente in Cliente.objects.filter(models.Q(codigo="") | models.Q(codigo=None)):
        while True:
            codigo = "".join(secrets.choice(ALFABETO) for _ in range(6))
            if codigo not in usados:
                break
        usados.add(codigo)
        cliente.codigo = codigo
        cliente.save(update_fields=["codigo"])


def sin_reversa(apps, schema_editor):
    """Al revertir no hay nada que deshacer: la columna se elimina completa."""


class Migration(migrations.Migration):

    dependencies = [("lealtad", "0001_initial")]

    operations = [
        # 1. La columna nace opcional para poder rellenarla.
        migrations.AddField(
            model_name="cliente",
            name="codigo",
            field=models.CharField(
                max_length=6, null=True, editable=False, verbose_name="Código",
                help_text="Código corto que el staff teclea para encontrarlo "
                          "en la caja."),
        ),
        # 2. Se le asigna uno a cada cliente existente.
        migrations.RunPython(genera_codigos, sin_reversa),
        # 3. Ya con todos poblados, se vuelve única y obligatoria.
        migrations.AlterField(
            model_name="cliente",
            name="codigo",
            field=models.CharField(
                max_length=6, unique=True, editable=False, verbose_name="Código",
                help_text="Código corto que el staff teclea para encontrarlo "
                          "en la caja."),
        ),
        # Devolución de canje: suma al saldo pero no a los puntos de por vida.
        migrations.AlterField(
            model_name="movimientopuntos",
            name="tipo",
            field=models.CharField(
                max_length=12,
                choices=[("gana", "Ganó puntos"), ("canje", "Canjeó un premio"),
                         ("ajuste", "Ajuste manual"), ("expira", "Puntos caducados"),
                         ("devolucion", "Devolución de un canje")]),
        ),
        migrations.AlterField(
            model_name="movimientopuntos",
            name="saldo_lote",
            field=models.IntegerField(
                default=0,
                help_text="Puntos que quedan sin consumir en este lote."),
        ),
        # Para devolver los puntos de un canje con su vigencia original.
        migrations.AddField(
            model_name="canje",
            name="expira_puntos",
            field=models.DateField(
                null=True, blank=True,
                help_text="Vigencia que tenían los puntos gastados aquí. Si el "
                          "canje se cancela, se devuelven con esa misma fecha "
                          "en vez de estrenar 12 meses."),
        ),
    ]
