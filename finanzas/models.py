from decimal import Decimal

from presupuesto.models import ANIO_MIN, ANIO_MAX
from django.db import models
from django.core.validators import MinValueValidator, MaxValueValidator

MESES = [
    (1, "Enero"), (2, "Febrero"), (3, "Marzo"), (4, "Abril"),
    (5, "Mayo"), (6, "Junio"), (7, "Julio"), (8, "Agosto"),
    (9, "Septiembre"), (10, "Octubre"), (11, "Noviembre"), (12, "Diciembre"),
]


class PronosticoFlujoCuenta(models.Model):
    """Pronóstico de flujo por mes y por cuenta contable (mismo catálogo)."""

    anio = models.PositiveIntegerField(
        "Año",
        validators=[MinValueValidator(ANIO_MIN), MaxValueValidator(ANIO_MAX)]
    )
    mes = models.PositiveSmallIntegerField("Mes", choices=MESES)
    cuenta = models.ForeignKey(
        "contabilidad.Cuenta", on_delete=models.CASCADE,
        related_name="pronosticos_flujo",
    )
    monto = models.DecimalField(
        "Monto pronosticado ($)", max_digits=12, decimal_places=2,
        default=Decimal("0"), validators=[MinValueValidator(Decimal("0"))],
    )

    class Meta:
        verbose_name = "Pronóstico de flujo por cuenta"
        verbose_name_plural = "Pronóstico de flujo (por cuenta y mes)"
        ordering = ["-anio", "-mes", "cuenta"]
        unique_together = ("anio", "mes", "cuenta")

    def __str__(self):
        return f"{self.get_mes_display()} {self.anio} · {self.cuenta}: ${self.monto:,.2f}"


class CostoFijo(models.Model):
    """Gasto mensual recurrente que no depende de las ventas."""

    class Categoria(models.TextChoices):
        RENTA = "renta", "Renta"
        SUELDOS = "sueldos", "Sueldos"
        MERCADOTECNIA = "mercadotecnia", "Mercadotecnia"
        SERVICIOS = "servicios", "Servicios (luz, agua, internet)"
        OTRO = "otro", "Otro"

    concepto = models.CharField(max_length=120)
    categoria = models.CharField(
        max_length=20, choices=Categoria.choices, default=Categoria.OTRO
    )
    monto_mensual = models.DecimalField(
        "Monto mensual ($)", max_digits=12, decimal_places=2,
        validators=[MinValueValidator(Decimal("0"))],
    )
    activo = models.BooleanField(default=True)
    notas = models.CharField(max_length=200, blank=True)

    class Meta:
        verbose_name = "Costo fijo"
        verbose_name_plural = "Costos fijos (mensuales)"
        ordering = ["categoria", "concepto"]

    def __str__(self):
        return f"{self.concepto}: ${self.monto_mensual:,.2f}/mes"

    @classmethod
    def total_mensual(cls):
        agg = cls.objects.filter(activo=True).aggregate(
            t=models.Sum("monto_mensual")
        )
        return agg["t"] or Decimal("0")


class InversionInicial(models.Model):
    """Cada gasto hecho para arrancar el negocio (se busca recuperar)."""

    class Categoria(models.TextChoices):
        EQUIPO = "equipo", "Equipo (licuadora, congelador...)"
        MOBILIARIO = "mobiliario", "Mobiliario"
        DEPOSITO = "deposito", "Depósito / Garantía renta"
        PERMISOS = "permisos", "Permisos y licencias"
        INVENTARIO = "inventario", "Inventario inicial"
        ADECUACION = "adecuacion", "Adecuación del local"
        OTRO = "otro", "Otro"

    concepto = models.CharField(max_length=120)
    categoria = models.CharField(
        max_length=20, choices=Categoria.choices, default=Categoria.OTRO
    )
    monto = models.DecimalField(
        "Monto ($)", max_digits=12, decimal_places=2,
        validators=[MinValueValidator(Decimal("0"))],
    )
    fecha = models.DateField(blank=True, null=True)
    notas = models.CharField(max_length=200, blank=True)

    class Meta:
        verbose_name = "Inversión inicial"
        verbose_name_plural = "Inversión inicial"
        ordering = ["categoria", "concepto"]

    def __str__(self):
        return f"{self.concepto}: ${self.monto:,.2f}"

    @classmethod
    def total(cls):
        agg = cls.objects.aggregate(t=models.Sum("monto"))
        return agg["t"] or Decimal("0")


class MovimientoEfectivo(models.Model):
    """Entradas/salidas de dinero que no son ventas ni compras de inventario.
    Ej. inyección de capital, retiro, reparación, gasto extraordinario."""

    class Tipo(models.TextChoices):
        ENTRADA = "entrada", "Entrada (+)"
        SALIDA = "salida", "Salida (−)"

    fecha = models.DateField()
    tipo = models.CharField(max_length=10, choices=Tipo.choices)
    concepto = models.CharField(max_length=120)
    monto = models.DecimalField(
        "Monto ($)", max_digits=12, decimal_places=2,
        validators=[MinValueValidator(Decimal("0"))],
    )

    class Meta:
        verbose_name = "Movimiento de efectivo"
        verbose_name_plural = "Movimientos de efectivo (extra)"
        ordering = ["-fecha", "-id"]

    def __str__(self):
        signo = "+" if self.tipo == self.Tipo.ENTRADA else "−"
        return f"{self.fecha} {signo}${self.monto:,.2f} {self.concepto}"

    @property
    def monto_con_signo(self):
        if self.tipo == self.Tipo.SALIDA:
            return -self.monto
        return self.monto
