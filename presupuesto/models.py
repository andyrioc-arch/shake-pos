from decimal import Decimal
from django.db import models
from django.core.validators import MinValueValidator, MaxValueValidator

from contabilidad.models import etiqueta_categoria_gasto

MESES = [
    (1, "Enero"), (2, "Febrero"), (3, "Marzo"), (4, "Abril"),
    (5, "Mayo"), (6, "Junio"), (7, "Julio"), (8, "Agosto"),
    (9, "Septiembre"), (10, "Octubre"), (11, "Noviembre"), (12, "Diciembre"),
]

# Dos rangos, y no son el mismo. Confundirlos cuesta en las dos direcciones.
#
# Vive junto a MESES porque las tres apps que leen un periodo de la petición
# comparten esta misma tabla de meses.
ANIO_MIN, ANIO_MAX = 2020, 2100

# Lo que `date()` sabe representar. Un año fuera de aquí revienta al construir
# el rango con que se consulta el mes, y un mes fuera de 1..12 revienta al
# buscar su nombre.
ANIO_TOPE_FECHA = 9999


def periodo_capturable(anio, mes):
    """¿Es un periodo que el negocio acepte GUARDAR?

    Es el rango que ya declaran los validadores de los modelos de abajo. Esos
    validadores solo corren en full_clean(), y las vistas escriben con
    update_or_create, así que sin esta comprobación el rango está declarado
    pero no se cumple: el mes se guarda tal cual y después revienta la portada.
    """
    return ANIO_MIN <= anio <= ANIO_MAX and 1 <= mes <= 12


def periodo_consultable(anio, mes):
    """¿Es un periodo que el sistema sepa nombrar y consultar?

    Más ancho que el capturable, a propósito. Al LEER, el periodo casi nunca
    viene de un formulario: sale de la fecha de una venta o de un asiento, y
    esa fecha ya es real. Acotar la lectura al rango del negocio haría
    desaparecer movimientos ciertos de los totales, que es peor que enseñar un
    año raro.
    """
    return 1 <= anio <= ANIO_TOPE_FECHA and 1 <= mes <= 12


class PresupuestoVenta(models.Model):
    """Meta de ventas (en $) para un mes."""

    anio = models.PositiveIntegerField(
        "Año",
        validators=[MinValueValidator(ANIO_MIN), MaxValueValidator(ANIO_MAX)]
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
        "Año",
        validators=[MinValueValidator(ANIO_MIN), MaxValueValidator(ANIO_MAX)]
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
