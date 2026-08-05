from decimal import Decimal
from django.db import models
from django.core.validators import MinValueValidator, MaxValueValidator

from contabilidad.models import etiqueta_categoria_gasto

MESES = [
    (1, "Enero"), (2, "Febrero"), (3, "Marzo"), (4, "Abril"),
    (5, "Mayo"), (6, "Junio"), (7, "Julio"), (8, "Agosto"),
    (9, "Septiembre"), (10, "Octubre"), (11, "Noviembre"), (12, "Diciembre"),
]


class PresupuestoVenta(models.Model):
    """Meta de ventas (en $) para un mes."""

    anio = models.PositiveIntegerField(
        "Año", validators=[MinValueValidator(2020), MaxValueValidator(2100)]
    )
    mes = models.PositiveSmallIntegerField("Mes", choices=MESES)
    monto = models.DecimalField(
        "Meta de ventas ($)", max_digits=12, decimal_places=2,
        validators=[MinValueValidator(Decimal("0"))],
    )
    notas = models.CharField(max_length=200, blank=True)

    class Meta:
        verbose_name = "Presupuesto de ventas"
        verbose_name_plural = "Presupuesto de ventas (por mes)"
        ordering = ["-anio", "-mes"]
        unique_together = ("anio", "mes")

    def __str__(self):
        return f"{self.get_mes_display()} {self.anio}: ${self.monto:,.2f}"


class PresupuestoGasto(models.Model):
    """Meta de gasto (en $) para una categoría en un mes."""

    anio = models.PositiveIntegerField(
        "Año", validators=[MinValueValidator(2020), MaxValueValidator(2100)]
    )
    mes = models.PositiveSmallIntegerField("Mes", choices=MESES)
    categoria = models.CharField(max_length=30)
    monto = models.DecimalField(
        "Meta de gasto ($)", max_digits=12, decimal_places=2,
        validators=[MinValueValidator(Decimal("0"))],
    )
    notas = models.CharField(max_length=200, blank=True)

    class Meta:
        verbose_name = "Presupuesto de gasto"
        verbose_name_plural = "Presupuesto de gastos (por categoría y mes)"
        ordering = ["-anio", "-mes", "categoria"]
        unique_together = ("anio", "mes", "categoria")

    def __str__(self):
        return (f"{self.get_mes_display()} {self.anio} · "
                f"{etiqueta_categoria_gasto(self.categoria)}: ${self.monto:,.2f}")
