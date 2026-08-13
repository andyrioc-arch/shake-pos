"""El reconocimiento deja de depender del botón «Facturado».

Antes, un movimiento solo llegaba al Estado de Resultados si alguien apretaba
ese botón. Nadie lo apretó nunca en producción: la cuenta 401 Ventas está en
cero y el reporte no dice nada. A partir de aquí compras y gastos se reconocen
siempre, y una venta se reconoce cuando su costo está completo (invariante I2).

NO TOCA UN SOLO DATO, A PROPÓSITO. El diseño original volteaba
`facturado=True` en todo el histórico y, para poder revertirlo, agregaba un
campo testigo con los pks que había volteado. Sobra: el código nuevo NO LEE la
columna en ningún camino, así que el estado del reconocimiento ya no depende de
ella. Dejándola congelada tal como la dejó el código viejo, revertir el
despliegue devuelve exactamente el Estado de Resultados que estaba publicado,
sin migración inversa que correr ni testigo que mantener. Voltear los datos
habría hecho lo contrario: al revertir, el código viejo vería todo facturado y
reconocería el histórico entero con su FIFO en memoria, que es el que inventa
costo con precios de catálogo.

La columna queda como reliquia inerte hasta que P11 la borre, un ciclo de
operación después.

ORDEN DE PUBLICACIÓN: es indistinto —esta migración no agrega nada que el
código nuevo necesite ni quita nada que el viejo use—, pero conviene correrla
junto con el despliegue para que el modelo y la base no queden desfasados.

EL REPORTE DE AGOSTO CAMBIA DE GOLPE con el despliegue: aparecen ingresos y
costos que nunca habían entrado. Es lo que se pidió. Tomar respaldo de Supabase
justo antes, de todos modos.
"""
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("contabilidad", "0007_quitar_cuentas_de_iva"),
    ]

    operations = [
        # `editable=False` no cambia una sola columna de la base; saca los dos
        # campos de los formularios del admin para que nadie los capture
        # creyendo que todavía mueven el reconocimiento.
        migrations.AlterField(
            model_name="movimiento",
            name="facturado",
            field=models.BooleanField("Facturado", default=False,
                                      editable=False),
        ),
        migrations.AlterField(
            model_name="movimiento",
            name="fecha_factura",
            field=models.DateField("Fecha de factura", null=True, blank=True,
                                   editable=False),
        ),
        # El índice sí toca la base: se rehace sin `facturado`, que ninguna
        # consulta filtra ya. Mantenerlo costaba escritura en cada alta a
        # cambio de nada. Es reversible y no toca datos.
        migrations.RemoveIndex(
            model_name="movimiento",
            name="contabilida_tipo_205a47_idx",
        ),
        migrations.AddIndex(
            model_name="movimiento",
            index=models.Index(fields=["tipo"],
                               name="contabilida_tipo_f2ad4b_idx"),
        ),
    ]
