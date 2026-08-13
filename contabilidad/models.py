from decimal import Decimal, ROUND_HALF_UP
from django.db import models
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator

CENTAVO = Decimal("0.01")


def redondea(v):
    return Decimal(v).quantize(CENTAVO, rounding=ROUND_HALF_UP)


# ══════════════════════════════════════════════════════════════════════════════
#  CATÁLOGO DE CUENTAS  (plan contable de partida doble)
# ══════════════════════════════════════════════════════════════════════════════
class Cuenta(models.Model):
    """Una cuenta del catálogo. La naturaleza define si aumenta por DEBE o HABER."""

    class Tipo(models.TextChoices):
        ACTIVO = "activo", "Activo"
        PASIVO = "pasivo", "Pasivo"
        CAPITAL = "capital", "Capital"
        INGRESO = "ingreso", "Ingreso"
        GASTO = "gasto", "Gasto / Costo"

    # Activo y Gasto son naturaleza DEUDORA (aumentan por el debe);
    # Pasivo, Capital e Ingreso son naturaleza ACREEDORA (aumentan por el haber).
    NATURALEZA_DEUDORA = {Tipo.ACTIVO, Tipo.GASTO}

    codigo = models.CharField("Código", max_length=20, unique=True)
    nombre = models.CharField(max_length=120)
    tipo = models.CharField(max_length=12, choices=Tipo.choices)
    descripcion = models.CharField(max_length=200, blank=True)
    # Agrupa cuentas en el reporte SIN mover el posteo: los asientos siguen
    # yendo a la cuenta hija. Una cuenta puede recibir movimientos propios Y
    # tener hijas a la vez (504 Mercadotecnia es las dos cosas).
    padre = models.ForeignKey(
        "self", on_delete=models.PROTECT, null=True, blank=True,
        related_name="subcuentas", verbose_name="Cuenta padre",
        help_text="Solo para agrupar en los reportes. No cambia dónde se postea.",
    )

    class Meta:
        verbose_name = "Cuenta"
        verbose_name_plural = "Catálogo de cuentas"
        ordering = ["codigo"]

    def __str__(self):
        return f"{self.codigo} · {self.nombre}"

    @property
    def es_deudora(self):
        return self.tipo in self.NATURALEZA_DEUDORA

    def _movs(self):
        return MovimientoContable.objects.filter(cuenta=self)

    @property
    def total_debe(self):
        agg = self._movs().aggregate(t=models.Sum("debe"))
        return agg["t"] or Decimal("0")

    @property
    def total_haber(self):
        agg = self._movs().aggregate(t=models.Sum("haber"))
        return agg["t"] or Decimal("0")

    @property
    def saldo(self):
        """Saldo según la naturaleza de la cuenta (siempre positivo si normal)."""
        if self.es_deudora:
            return self.total_debe - self.total_haber
        return self.total_haber - self.total_debe


# ══════════════════════════════════════════════════════════════════════════════
#  CATEGORÍAS DE GASTO  (compartidas por presupuesto y facturas de gasto)
# ══════════════════════════════════════════════════════════════════════════════
class CategoriaGasto(models.Model):
    """Categoría de gasto. Fuente única de verdad para presupuesto y facturas."""

    clave = models.SlugField(
        "Clave", max_length=30, unique=True,
        help_text="Identificador corto (se genera del nombre), ej. 'logistica'.",
    )
    nombre = models.CharField("Nombre", max_length=60)
    cuenta_codigo = models.CharField(
        "Código de cuenta contable", max_length=20, blank=True,
        help_text="Cuenta del catálogo a la que se cargan estos gastos. "
                  "Si se deja vacío se usa 'Otros gastos' (509).",
    )
    activo = models.BooleanField(default=True)

    class Meta:
        verbose_name = "Categoría de gasto"
        verbose_name_plural = "Categorías de gasto"
        ordering = ["nombre"]

    def __str__(self):
        return self.nombre


def categorias_gasto_choices():
    """Choices dinámicas (categorías activas) para los campos de categoría/tipo."""
    return [(c.clave, c.nombre)
            for c in CategoriaGasto.objects.filter(activo=True)]


def etiqueta_categoria_gasto(clave):
    """Nombre legible de una categoría por su clave (incluye inactivas)."""
    c = CategoriaGasto.objects.filter(clave=clave).first()
    return c.nombre if c else clave


# ══════════════════════════════════════════════════════════════════════════════
#  ASIENTOS CONTABLES (Pólizas)
# ══════════════════════════════════════════════════════════════════════════════
class Asiento(models.Model):
    """Una póliza contable. La suma de DEBE debe ser igual a la de HABER."""

    fecha = models.DateField()
    concepto = models.CharField(max_length=200)
    referencia = models.CharField(
        max_length=80, blank=True,
        help_text="Origen del asiento (ej. Venta #12, Gasto #5).",
    )
    automatico = models.BooleanField(
        default=False,
        help_text="Generado automáticamente desde una venta o gasto.",
    )
    creado = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Asiento contable"
        verbose_name_plural = "Asientos contables (pólizas)"
        ordering = ["-fecha", "-id"]
        indexes = [models.Index(fields=["fecha"])]

    def __str__(self):
        return f"#{self.pk} {self.fecha} · {self.concepto}"

    @property
    def total_debe(self):
        return sum((m.debe for m in self.movimientos.all()), Decimal("0"))

    @property
    def total_haber(self):
        return sum((m.haber for m in self.movimientos.all()), Decimal("0"))

    @property
    def cuadrado(self):
        return redondea(self.total_debe) == redondea(self.total_haber)


class MovimientoContable(models.Model):
    """Una línea de un asiento: carga (debe) o abono (haber) a una cuenta."""

    asiento = models.ForeignKey(
        Asiento, on_delete=models.CASCADE, related_name="movimientos"
    )
    cuenta = models.ForeignKey(
        Cuenta, on_delete=models.PROTECT, related_name="movimientos"
    )
    debe = models.DecimalField(
        max_digits=14, decimal_places=2, default=Decimal("0"),
        validators=[MinValueValidator(Decimal("0"))],
    )
    haber = models.DecimalField(
        max_digits=14, decimal_places=2, default=Decimal("0"),
        validators=[MinValueValidator(Decimal("0"))],
    )

    class Meta:
        verbose_name = "Movimiento"
        verbose_name_plural = "Movimientos"

    def __str__(self):
        return f"{self.cuenta.codigo} D:{self.debe} H:{self.haber}"

    def clean(self):
        if self.debe and self.haber:
            raise ValidationError("Una línea no puede tener DEBE y HABER a la vez.")
        if not self.debe and not self.haber:
            raise ValidationError("La línea debe tener un monto en DEBE o en HABER.")


# ══════════════════════════════════════════════════════════════════════════════
#  LIBRO DE MOVIMIENTOS  (puente automático ventas/compras/gastos → contabilidad)
# ══════════════════════════════════════════════════════════════════════════════
class Movimiento(models.Model):
    """Registro único de cada venta/compra en el libro de movimientos.

    Genera automáticamente sus asientos de partida doble:
      • Asiento de FLUJO (siempre): mueve efectivo contra una cuenta puente,
        por lo que afecta balance, flujo y balanza pero NO el estado de
        resultados.
      • Asiento de RECONOCIMIENTO (solo si está 'Facturado'): reclasifica de la
        cuenta puente a ingreso/gasto, en la fecha de la factura, reconociendo
        el resultado (criterio IFRS de reconocimiento).
    """

    class Tipo(models.TextChoices):
        VENTA = "venta", "Venta"
        COMPRA = "compra", "Compra"
        GASTO = "gasto", "Gasto operativo"

    fecha = models.DateField("Fecha")
    tipo = models.CharField(max_length=10, choices=Tipo.choices)
    descripcion = models.CharField("Descripción", max_length=200)
    monto = models.DecimalField(max_digits=14, decimal_places=2)
    cuenta = models.ForeignKey(
        Cuenta, on_delete=models.PROTECT, related_name="movimientos_libro",
        help_text="Cuenta de resultados afectada al facturar (ingreso o gasto).",
    )
    # Para gastos operativos: categoría (clave de CategoriaGasto).
    categoria = models.CharField(max_length=30, blank=True)

    venta = models.ForeignKey(
        "inventario.Venta", on_delete=models.CASCADE,
        null=True, blank=True, related_name="movimiento",
    )
    compra = models.ForeignKey(
        "inventario.Compra", on_delete=models.CASCADE,
        null=True, blank=True, related_name="movimiento",
    )

    asiento_flujo = models.ForeignKey(
        Asiento, on_delete=models.SET_NULL, null=True, blank=True, related_name="+",
    )
    asiento_reconocimiento = models.ForeignKey(
        Asiento, on_delete=models.SET_NULL, null=True, blank=True, related_name="+",
    )
    creado = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Movimiento (libro)"
        verbose_name_plural = "Libro de movimientos"
        ordering = ["-fecha", "-id"]
        indexes = [
            models.Index(fields=["fecha"]),
            models.Index(fields=["tipo"]),
        ]

    def __str__(self):
        return f"{self.fecha} · {self.get_tipo_display()} · ${self.monto:,.2f}"
