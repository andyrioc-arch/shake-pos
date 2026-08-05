import uuid
from decimal import Decimal, ROUND_HALF_UP
from django.db import models
from django.core.validators import MinValueValidator
from django.urls import reverse

IVA_TASA = Decimal("0.16")


def desglosa_iva(total):
    """Separa un total con IVA incluido (16%) en (subtotal, iva)."""
    iva = (total * IVA_TASA / (Decimal("1") + IVA_TASA)).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP)
    return total - iva, iva


class Ingrediente(models.Model):
    """Catálogo de ingredientes e insumos (incluye empaque)."""

    class Categoria(models.TextChoices):
        BASE_LIQUIDA = "liquido", "Base líquida"
        PROTEINA = "proteina", "Proteína Habits"
        FRUTA = "fruta", "Fruta"
        SPREAD = "spread", "Spread / Crema"
        SUPERFOOD = "superfood", "Superfood / Especia"
        OTRO = "otro", "Otro"
        EMPAQUE = "empaque", "Empaque"

    nombre = models.CharField("Ingrediente", max_length=120, unique=True)
    categoria = models.CharField(
        max_length=20, choices=Categoria.choices, default=Categoria.OTRO
    )
    unidad_compra = models.CharField(
        "Unidad de compra", max_length=30,
        help_text="Ej. kg, litro, paquete, rollo",
    )
    cantidad_por_unidad = models.DecimalField(
        "Cantidad por unidad de compra",
        max_digits=12, decimal_places=2,
        validators=[MinValueValidator(Decimal("0.0001"))],
        help_text="Cuántas unidades de receta trae una unidad de compra. "
                  "Ej. 1 kg = 1000 g; 1 paquete = 50 piezas.",
    )
    unidad_receta = models.CharField(
        "Unidad de receta", max_length=20,
        help_text="Ej. g, ml, pieza",
    )
    costo_unidad_compra = models.DecimalField(
        "Costo por unidad de compra ($)",
        max_digits=12, decimal_places=2,
        validators=[MinValueValidator(Decimal("0"))],
    )

    class Meta:
        verbose_name = "Ingrediente"
        verbose_name_plural = "Ingredientes"
        ordering = ["categoria", "nombre"]

    def __str__(self):
        return self.nombre

    @property
    def costo_unidad_receta(self):
        """Costo de UNA unidad de receta (g, ml o pieza)."""
        if self.cantidad_por_unidad:
            return self.costo_unidad_compra / self.cantidad_por_unidad
        return Decimal("0")

    @property
    def total_comprado(self):
        """Total comprado en unidades de receta."""
        agg = self.compras.aggregate(total=models.Sum("cantidad"))
        comprado = agg["total"] or Decimal("0")
        return comprado * self.cantidad_por_unidad

    @property
    def total_consumido(self):
        """Unidades de receta consumidas, considerando sustituciones y extras.

        Para cada venta:
          + lo que la receta usa de este ingrediente × cantidad vendida
          − lo que se sustituyó (si este ingrediente fue reemplazado en la venta)
          + lo que entró como sustituto (si este ingrediente reemplazó a otro)
          + lo que se usó como extra
        """
        total = Decimal("0")

        # 1) Consumo base por recetas que usan este ingrediente
        for item in self.usos.select_related("receta"):
            for venta in item.receta.ventas.all():
                # ¿En esta venta se sustituyó este ingrediente por otro? -> no se usó
                sustituido = venta.sustituciones.filter(
                    ingrediente_original=self
                ).exists()
                if not sustituido:
                    total += item.cantidad * venta.cantidad

        # 2) Consumo como sustituto (este ingrediente entró en lugar de otro)
        for sus in self.sustituto_en.select_related("venta"):
            total += sus.cantidad_receta * sus.venta.cantidad

        # 3) Consumo como add-on: una vez por la línea (no por cada shake)
        for ext in self.extras.all():  # Extra objects que usan este ingrediente
            for ve in ext.ventaextra_set.select_related("venta"):
                total += ext.cantidad * ve.cantidad

        return total

    @property
    def stock_disponible(self):
        return self.total_comprado - self.total_consumido

    @property
    def minimo_para_cinco(self):
        """Mínimo necesario para 5 shakes de cada receta que lo use."""
        total = Decimal("0")
        for item in self.usos.all():
            total += item.cantidad * 5
        return total

    @property
    def faltante(self):
        falta = self.minimo_para_cinco - self.stock_disponible
        return falta if falta > 0 else Decimal("0")

    @property
    def hay_faltante(self):
        return self.minimo_para_cinco > 0 and self.stock_disponible < self.minimo_para_cinco

    @property
    def valor_stock(self):
        return self.stock_disponible * self.costo_unidad_receta


class Receta(models.Model):
    """Una receta de shake. Sus ingredientes se editan en línea."""

    nombre = models.CharField("Shake", max_length=120, unique=True)
    emoji = models.CharField(max_length=8, blank=True)
    perfil = models.CharField(
        "Perfil / descripción", max_length=200, blank=True
    )
    tamano = models.CharField(
        "Tamaño", max_length=40, default="16 oz / 473 ml"
    )
    precio_venta = models.DecimalField(
        "Precio de venta ($)", max_digits=10, decimal_places=2,
        validators=[MinValueValidator(Decimal("0"))],
    )
    activa = models.BooleanField(default=True)

    class Meta:
        verbose_name = "Receta"
        verbose_name_plural = "Recetas"
        ordering = ["nombre"]

    def __str__(self):
        return f"{self.emoji} {self.nombre}".strip()

    @property
    def costo_receta(self):
        """Costo total de los ingredientes de la receta."""
        total = Decimal("0")
        for item in self.ingredientes.select_related("ingrediente"):
            total += item.costo_linea
        return total

    @property
    def ganancia_unitaria(self):
        return self.precio_venta - self.costo_receta

    @property
    def margen(self):
        if self.precio_venta:
            return self.ganancia_unitaria / self.precio_venta
        return Decimal("0")

    @property
    def unidades_vendidas(self):
        agg = self.ventas.aggregate(total=models.Sum("cantidad"))
        return agg["total"] or 0


class RecetaIngrediente(models.Model):
    """Cantidad de un ingrediente dentro de una receta (editable en línea)."""

    receta = models.ForeignKey(
        Receta, on_delete=models.CASCADE, related_name="ingredientes"
    )
    ingrediente = models.ForeignKey(
        Ingrediente, on_delete=models.PROTECT, related_name="usos"
    )
    cantidad = models.DecimalField(
        "Cantidad (en unidad de receta)",
        max_digits=12, decimal_places=2,
        validators=[MinValueValidator(Decimal("0"))],
    )

    class Meta:
        verbose_name = "Ingrediente de receta"
        verbose_name_plural = "Ingredientes de la receta"
        unique_together = ("receta", "ingrediente")

    def __str__(self):
        return f"{self.cantidad} {self.ingrediente.unidad_receta} de {self.ingrediente}"

    @property
    def costo_linea(self):
        return self.cantidad * self.ingrediente.costo_unidad_receta


class Compra(models.Model):
    """Registro de una compra de inventario."""

    fecha = models.DateField()
    ingrediente = models.ForeignKey(
        Ingrediente, on_delete=models.PROTECT, related_name="compras"
    )
    cantidad = models.DecimalField(
        "Cantidad comprada (en unidad de compra)",
        max_digits=12, decimal_places=2,
        validators=[MinValueValidator(Decimal("0"))],
    )
    costo_unitario = models.DecimalField(
        "Costo unitario ($)", max_digits=12, decimal_places=2,
        validators=[MinValueValidator(Decimal("0"))],
    )
    proveedor = models.CharField(max_length=120, blank=True)
    notas = models.CharField(max_length=200, blank=True)

    class Meta:
        verbose_name = "Compra"
        verbose_name_plural = "Compras"
        ordering = ["-fecha", "-id"]
        indexes = [models.Index(fields=["fecha"])]

    def __str__(self):
        return f"{self.fecha} · {self.ingrediente} x{self.cantidad}"

    @property
    def total(self):
        return self.cantidad * self.costo_unitario


class Nota(models.Model):
    """Comprobante (ticket) de una venta: agrupa sus líneas y guarda el pago.

    Tiene un token público para compartir por enlace/QR con el cliente.
    """

    token = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    fecha = models.DateField()
    creada = models.DateTimeField(auto_now_add=True)
    metodo_pago = models.CharField(
        "Método de pago", max_length=10,
        choices=[("efectivo", "Efectivo"), ("tarjeta", "Tarjeta")],
        default="efectivo",
    )
    pago_con = models.DecimalField(
        "Paga con ($)", max_digits=10, decimal_places=2, null=True, blank=True,
    )
    cambio = models.DecimalField(
        "Cambio ($)", max_digits=10, decimal_places=2, null=True, blank=True,
    )
    total = models.DecimalField(
        "Total ($)", max_digits=12, decimal_places=2, default=Decimal("0"),
    )
    es_cortesia = models.BooleanField("Cortesía", default=False)
    motivo_cortesia = models.CharField(
        "Motivo de la cortesía", max_length=200, blank=True)

    class Meta:
        verbose_name = "Nota / comprobante"
        verbose_name_plural = "Notas / comprobantes"
        ordering = ["-creada"]

    def __str__(self):
        return f"Nota {self.folio} · ${self.total:,.2f}"

    @property
    def folio(self):
        """Folio corto y legible a partir del token."""
        return self.token.hex[:8].upper()

    @property
    def subtotal(self):
        return desglosa_iva(self.total)[0]

    @property
    def iva(self):
        return desglosa_iva(self.total)[1]

    def get_absolute_url(self):
        return reverse("nota_ver", args=[self.token])


class Venta(models.Model):
    """Registro de una venta de shakes."""

    class MetodoPago(models.TextChoices):
        EFECTIVO = "efectivo", "Efectivo"
        TARJETA = "tarjeta", "Tarjeta"

    nota = models.ForeignKey(
        Nota, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="lineas",
    )

    fecha = models.DateField()
    receta = models.ForeignKey(
        Receta, on_delete=models.PROTECT, related_name="ventas"
    )
    cantidad = models.PositiveIntegerField(default=1)
    precio_unitario = models.DecimalField(
        "Precio unitario ($)", max_digits=10, decimal_places=2,
        validators=[MinValueValidator(Decimal("0"))],
        blank=True, null=True,
        help_text="Si lo dejas vacío se usa el precio de la receta.",
    )
    metodo_pago = models.CharField(
        "Método de pago", max_length=10,
        choices=MetodoPago.choices, default=MetodoPago.EFECTIVO,
    )
    es_cortesia = models.BooleanField(
        "Cortesía", default=False,
        help_text="Producto regalado (activación): sin ingreso, pero consume inventario.",
    )

    class Meta:
        verbose_name = "Venta"
        verbose_name_plural = "Ventas"
        ordering = ["-fecha", "-id"]
        indexes = [models.Index(fields=["fecha"])]

    def __str__(self):
        return f"{self.fecha} · {self.receta} x{self.cantidad}"

    def save(self, *args, **kwargs):
        if self.precio_unitario is None and self.receta_id:
            self.precio_unitario = self.receta.precio_venta
        super().save(*args, **kwargs)

    @property
    def cargo_extras(self):
        """Cargo de los add-ons: UNA vez por la línea, no por cada shake."""
        return sum((e.cargo_total for e in self.extras.all()), Decimal("0"))

    @property
    def precio_efectivo(self):
        """Precio base por shake (los add-ons se cobran por línea, ver cargo_extras)."""
        return self.precio_unitario if self.precio_unitario is not None \
            else self.receta.precio_venta

    @property
    def costo_unitario_real(self):
        """Costo de un shake: receta base + sustituciones (sin add-ons)."""
        costo = self.receta.costo_receta
        for s in self.sustituciones.all():
            costo += s.delta_costo
        return costo

    @property
    def costo_extras(self):
        """Costo de inventario de los add-ons: una vez por la línea."""
        return sum((e.costo_total for e in self.extras.all()), Decimal("0"))

    @property
    def ingreso(self):
        # Una cortesía es gratis: no genera ingreso (pero sí consume inventario).
        if self.es_cortesia:
            return Decimal("0")
        # Shakes a precio base + add-ons cobrados una vez por la línea.
        # El precio ya incluye IVA (16%); ver subtotal/iva para el desglose.
        return self.cantidad * self.precio_efectivo + self.cargo_extras

    @property
    def subtotal(self):
        """Ingreso sin IVA (base gravable)."""
        return desglosa_iva(self.ingreso)[0]

    @property
    def iva(self):
        """IVA (16%) contenido en el ingreso."""
        return desglosa_iva(self.ingreso)[1]

    @property
    def costo_total(self):
        return self.cantidad * self.costo_unitario_real + self.costo_extras

    def consumo_ingredientes(self):
        """{ingrediente_id: cantidad (unidad de receta)} que consume esta venta.

        Considera la receta base, sustituciones y add-ons. Es la base para
        valuar el Costo de Ventas por FIFO.
        """
        from collections import defaultdict
        cons = defaultdict(Decimal)
        sustituidos = set()
        for s in self.sustituciones.all():
            sustituidos.add(s.ingrediente_original_id)
            cons[s.ingrediente_nuevo_id] += s.cantidad_receta * self.cantidad
        for ri in self.receta.ingredientes.all():
            if ri.ingrediente_id in sustituidos:
                continue
            cons[ri.ingrediente_id] += ri.cantidad * self.cantidad
        for ve in self.extras.all():   # add-ons: una vez por la línea
            cons[ve.extra.ingrediente_id] += ve.extra.cantidad * ve.cantidad
        return dict(cons)

    @property
    def ganancia(self):
        return self.ingreso - self.costo_total


# ══════════════════════════════════════════════════════════════════════════════
#  EXTRAS Y PERSONALIZACIÓN DE VENTAS
# ══════════════════════════════════════════════════════════════════════════════
class Extra(models.Model):
    """Catálogo editable de extras que se cobran aparte (espresso, creatina...)."""

    nombre = models.CharField(max_length=80, unique=True)
    ingrediente = models.ForeignKey(
        Ingrediente, on_delete=models.PROTECT, related_name="extras",
        help_text="Ingrediente que consume del inventario.",
    )
    cantidad = models.DecimalField(
        "Cantidad por porción (en unidad de receta)",
        max_digits=10, decimal_places=2,
        validators=[MinValueValidator(Decimal("0"))],
        help_text="Cuánto se usa de ese ingrediente por cada extra agregado.",
    )
    cargo = models.DecimalField(
        "Cargo extra al precio ($)", max_digits=10, decimal_places=2,
        validators=[MinValueValidator(Decimal("0"))],
        help_text="Cuánto se suma al precio de venta por este extra.",
    )
    activo = models.BooleanField(default=True)

    class Meta:
        verbose_name = "Extra"
        verbose_name_plural = "Extras (catálogo)"
        ordering = ["nombre"]

    def __str__(self):
        return f"{self.nombre} (+${self.cargo:,.2f})"

    @property
    def costo(self):
        """Costo de insumo de un extra."""
        return self.cantidad * self.ingrediente.costo_unidad_receta


class VentaSustitucion(models.Model):
    """Cambia un ingrediente de la receta por otro en una venta (misma cantidad)."""

    venta = models.ForeignKey(
        "Venta", on_delete=models.CASCADE, related_name="sustituciones"
    )
    ingrediente_original = models.ForeignKey(
        Ingrediente, on_delete=models.PROTECT, related_name="sustituido_en",
        help_text="Ingrediente de la receta que se quita.",
    )
    ingrediente_nuevo = models.ForeignKey(
        Ingrediente, on_delete=models.PROTECT, related_name="sustituto_en",
        help_text="Ingrediente que entra en su lugar.",
    )

    class Meta:
        verbose_name = "Sustitución"
        verbose_name_plural = "Sustituciones"

    def __str__(self):
        return f"{self.ingrediente_original} → {self.ingrediente_nuevo}"

    @property
    def cantidad_receta(self):
        """Cantidad que la receta usa del ingrediente original (la que se sustituye)."""
        item = self.venta.receta.ingredientes.filter(
            ingrediente=self.ingrediente_original
        ).first()
        return item.cantidad if item else Decimal("0")

    @property
    def delta_costo(self):
        """Diferencia de costo por una porción: nuevo − original."""
        cant = self.cantidad_receta
        costo_orig = cant * self.ingrediente_original.costo_unidad_receta
        costo_nuevo = cant * self.ingrediente_nuevo.costo_unidad_receta
        return costo_nuevo - costo_orig


class VentaExtra(models.Model):
    """Un extra agregado a una venta (espresso, creatina, etc.)."""

    venta = models.ForeignKey(
        "Venta", on_delete=models.CASCADE, related_name="extras"
    )
    extra = models.ForeignKey(Extra, on_delete=models.PROTECT)
    cantidad = models.PositiveIntegerField(
        default=1, help_text="Cuántas porciones de este extra."
    )

    class Meta:
        verbose_name = "Extra de venta"
        verbose_name_plural = "Extras de la venta"

    def __str__(self):
        return f"{self.cantidad}× {self.extra.nombre}"

    @property
    def cargo_total(self):
        return self.cantidad * self.extra.cargo

    @property
    def costo_total(self):
        return self.cantidad * self.extra.costo
