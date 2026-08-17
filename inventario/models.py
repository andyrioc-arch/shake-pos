import uuid
from decimal import Decimal, ROUND_HALF_UP
from django.db import models
from django.core.validators import MaxValueValidator, MinValueValidator
from django.urls import reverse
from django.utils import timezone

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
        """Costo de UNA unidad de receta (g, ml o pieza), según el catálogo.

        Es un aproximado: lo que alguien tecleó como precio de referencia. Lo
        que de verdad se pagó vive en las compras, y `costo_unidad_ultima_compra`
        lo dice.
        """
        if self.cantidad_por_unidad:
            return self.costo_unidad_compra / self.cantidad_por_unidad
        return Decimal("0")

    @property
    def costo_unidad_ultima_compra(self):
        """Lo que costó una unidad de receta la última vez que se compró.

        `None` si nunca se ha comprado: no se inventa un precio, igual que el
        costeo no inventa un costo cuando le faltan capas.
        """
        ultima = self.compras.order_by("-fecha", "-id").first()
        return ultima.costo_unitario_capa if ultima else None

    @property
    def total_comprado(self):
        """Total comprado en unidades de receta.

        Suma lo que cada compra CONGELÓ, no el número de paquetes por el tamaño
        de hoy. Es la misma razón por la que existe `Compra.cantidad_receta`:
        corregir la presentación de un ingrediente no puede cambiar lo que
        trajeron las compras de antes. Recalculándolo, cambiar el catálogo de
        790 a 1000 g reescribía el stock de todo el histórico hacia atrás,
        mientras cada capa seguía diciendo 790.

        No hace falta prever capas sin congelar: `cantidad_receta` es NOT NULL
        desde `0011_la_compra_sin_huecos`, así que toda compra trae su dato.
        """
        agg = self.compras.aggregate(total=models.Sum("cantidad_receta"))
        return agg["total"] or Decimal("0")

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

        # 4) Lo que se perdió. Sin esto el conteo físico no sirve de nada: la
        #    merma bajaría la cuenta 115 pero el stock en pantalla seguiría
        #    contando mercancía que ya se tiró, y el siguiente conteo volvería
        #    a reportar el mismo faltante.
        #
        #    `_merma_precargada` la inyecta quien recorre el catálogo entero
        #    para no pagar una consulta por ingrediente; sin ella se resuelve
        #    sola, que es lo correcto para un llamador suelto.
        perdido = getattr(self, "_merma_precargada", None)
        total += self.merma_total if perdido is None else perdido

        return total

    @property
    def merma_total(self):
        """Unidades de receta perdidas y registradas en conteos físicos.

        Quien recorra varios ingredientes debe usar `mermas_por_ingrediente()`
        y no esta propiedad: aquí cuesta una consulta por ingrediente, y el
        panel las pide todas.
        """
        return self.mermas_por_ingrediente(
            [self.pk]).get(self.pk, Decimal("0"))

    @staticmethod
    def mermas_por_ingrediente(ids=None):
        """{ingrediente_id: unidades perdidas} para todos, en UNA consulta.

        La resta se hace en la base y no en Python porque el llamador que
        importa es el panel, que recorre el catálogo entero: pedirlo ingrediente
        por ingrediente son treinta viajes al pooler cada vez que el cajero
        abre su pantalla. Es la misma lección que dejó el costo de la última
        compra en el catálogo.
        """
        from django.db.models.functions import Greatest
        qs = AjusteInventario.objects.all()
        if ids is not None:
            qs = qs.filter(ingrediente_id__in=ids)
        filas = qs.values("ingrediente_id").annotate(
            perdido=models.Sum(Greatest(
                models.F("cantidad_calculada") - models.F("cantidad_real"),
                models.Value(Decimal("0")))))
        return {f["ingrediente_id"]: f["perdido"] or Decimal("0")
                for f in filas}

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
        """Costo total de los ingredientes de la receta.

        Sirve a dos clases de llamador y no puede castigar a ninguno:

        - Si quien llama YA precargó `ingredientes__ingrediente` (los paneles,
          vía `Venta.objects.con_costeo()`), hay que usar ese caché. Un
          `.select_related()` aquí abriría un queryset NUEVO, tiraría el caché
          y volvería a consultar una vez por receta.
        - Si NO precargó (el catálogo, el admin, los premios), `.all()` a secas
          deja una consulta por ingrediente al leer `costo_linea`.

        Por eso se mira el caché de prefetch antes de decidir. La alternativa
        —obligar a precargar en cada sitio— reparte la misma decisión entre
        seis llamadores, y olvidarla en uno no rompe ningún número: solo lo
        vuelve lento, que es lo que nadie nota.
        """
        return sum((item.costo_linea for item in self._items()), Decimal("0"))

    def _items(self):
        """Las líneas de la receta, respetando el caché de prefetch si lo hay.

        Vive aparte porque la decisión que explica `costo_receta` la necesitan
        todos los que recorren ingredientes, y copiarla es cómo se pierde.
        """
        items = self.ingredientes.all()
        if "ingredientes" not in getattr(self, "_prefetched_objects_cache", {}):
            items = items.select_related("ingrediente")
        return items

    def costo_ultima_compra(self, unitarios=None):
        """Lo que costaría esta receta a los precios realmente pagados.

        `None` si a algún ingrediente le falta su primera compra: media receta
        valuada con precios reales y la otra media con el catálogo es un número
        que no es ninguna de las dos cosas.

        `unitarios` es `{ingrediente_id: costo por unidad de receta}` ya
        resuelto para todo el catálogo. Es un parámetro y no un detalle interno
        porque preguntarle a cada ingrediente por su última compra cuesta una
        consulta por ingrediente por receta, y el catálogo tiene diecinueve.
        """
        total = Decimal("0")
        for item in self._items():
            if unitarios is not None:
                unitario = unitarios.get(item.ingrediente_id)
            else:
                unitario = item.ingrediente.costo_unidad_ultima_compra
            if unitario is None:
                return None
            total += item.cantidad * unitario
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
        """Todo lo que salió del mostrador, cobrado o regalado.

        Las cortesías cuentan aquí a propósito: se produjeron y consumieron
        insumo igual que las demás. Quien quiera solo lo que entró a caja
        tiene `unidades_cobradas`; el margen ya las excluye por su lado.
        """
        agg = self.ventas.aggregate(total=models.Sum("cantidad"))
        return agg["total"] or 0

    @property
    def unidades_regaladas(self):
        agg = self.ventas.filter(es_cortesia=True).aggregate(
            total=models.Sum("cantidad"))
        return agg["total"] or 0

    @property
    def unidades_cobradas(self):
        return self.unidades_vendidas - self.unidades_regaladas


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
    monto_total = models.DecimalField(
        "Monto total pagado ($)", max_digits=12, decimal_places=2,
        validators=[MinValueValidator(Decimal("0"))],
        help_text="Lo que de verdad se pagó por esta compra.",
    )
    cantidad_receta = models.DecimalField(
        "Unidades de receta compradas", max_digits=14, decimal_places=4,
        editable=False,
        help_text="Cuántas unidades de receta trajo esta compra, congeladas al "
                  "momento de comprarla.",
    )
    saldo_receta = models.DecimalField(
        "Saldo de la capa", max_digits=14, decimal_places=4,
        editable=False,
        help_text="Unidades de receta que le quedan sin consumir a esta compra. "
                  "Lo lleva el costeo FIFO.",
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

    def save(self, *args, **kwargs):
        # Una compra nueva es una capa llena. Se hace aquí y no en la vista para
        # que valga por todos los caminos: caja, admin, seed, API y pruebas.
        #
        # El tamaño de la capa se congela: si mañana alguien corrige la
        # presentación del ingrediente (que un costal traía 5 kg y no 1), esta
        # compra tiene que seguir valiendo lo que costó.
        nuevos = []
        if self.cantidad_receta is None and self.cantidad is not None:
            self.cantidad_receta = (
                self.cantidad * self.ingrediente.cantidad_por_unidad)
            nuevos.append("cantidad_receta")
        if self.saldo_receta is None and self.cantidad_receta is not None:
            self.saldo_receta = self.cantidad_receta
            nuevos.append("saldo_receta")
        if nuevos and kwargs.get("update_fields") is not None:
            kwargs["update_fields"] = [*kwargs["update_fields"], *nuevos]
        super().save(*args, **kwargs)

    @property
    def costo_unitario_capa(self):
        """Lo que costó una unidad de receta en ESTA compra."""
        if self.cantidad_receta:
            return self.total / self.cantidad_receta
        return Decimal("0")

    @property
    def total(self):
        """Lo que se pagó por la compra. Es el dato, no un cálculo."""
        return self.monto_total

    @property
    def costo_unitario(self):
        """Cuánto salió cada unidad de compra. SOLO para mostrar.

        Nunca para reconstruir el total: la división pierde centavos y la
        multiplicación no los recupera. Por eso no se cuantiza —quien lo
        imprima decide con cuántos decimales— y por eso `total` no la usa.
        Era una columna hasta P11; se derivaba de `monto_total` y se guardaba
        al lado, que es una copia esperando desfasarse.
        """
        if self.cantidad:
            return self.monto_total / self.cantidad
        return Decimal("0")


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
    nombre_cliente = models.CharField(
        "A nombre de", max_length=80, blank=True,
        help_text="Para cantar el pedido cuando esté listo.")
    entregada_en = models.DateTimeField(
        "Entregada", null=True, blank=True,
        help_text="Cuándo salió el pedido del mostrador. Vacío = pendiente.")

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
    def pendiente(self):
        """¿Sigue en la barra sin entregar?"""
        return self.entregada_en is None

    # ── Descuento ─────────────────────────────────────────────────────────
    # Se derivan de las líneas y no se guardan: `total` ya viene rebajado, y
    # una columna al lado sería una segunda versión del mismo hecho esperando
    # a desfasarse el día que alguien edite una línea desde el admin.
    #
    # Las tres cifras salen de UN solo recorrido y se guardan en caché: cada
    # una abriendo su propio `lineas.all()` convertía el comprobante —lo que
    # abre el cliente al escanear el QR— en veinte consultas.
    def _resumen_descuento(self):
        if not hasattr(self, "_desc_cache"):
            lineas = list(self.lineas.all())
            lista = sum((l.ingreso_lista for l in lineas), Decimal("0"))
            # El monto se DERIVA de la resta y no se suma aparte. Sumar los
            # importes crudos y redondearlos para enseñarlos deja el
            # comprobante sin cuadrar: el total se redondea sobre el
            # complemento, los dos redondeos suben, y el cliente lee
            # «30.00 − 3.71 = 26.30».
            monto = Decimal("0") if self.es_cortesia else lista - self.total
            self._desc_cache = {
                "lista": lista,
                "monto": monto if monto > 0 else Decimal("0"),
                # El mayor de las líneas: hoy la caja pone el mismo en todas, y
                # promediarlos daría un número que no es el de ninguna.
                "pct": max((l.descuento_pct for l in lineas),
                           default=Decimal("0")),
            }
        return self._desc_cache

    @property
    def total_lista(self):
        """Lo que habría costado sin descuento."""
        return self._resumen_descuento()["lista"]

    @property
    def descuento_monto(self):
        """Lo que se rebajó. Cuadra con el total por construcción."""
        return self._resumen_descuento()["monto"]

    @property
    def descuento_pct(self):
        """El porcentaje aplicado, para enseñarlo en la nota."""
        return self._resumen_descuento()["pct"]

    @property
    def tiene_descuento(self):
        return self.descuento_monto > 0

    @property
    def subtotal(self):
        return desglosa_iva(self.total)[0]

    @property
    def iva(self):
        return desglosa_iva(self.total)[1]

    def get_absolute_url(self):
        return reverse("nota_ver", args=[self.token])


class VentaQuerySet(models.QuerySet):
    """Formas de traer ventas que el costeo obliga a repetir siempre igual."""

    def con_costeo(self):
        """Precarga TODO lo que recorre `costo_de_ventas` en su respaldo.

        Son las mismas cuatro vías que enumera `consumo_ingredientes()`. Van
        juntas y en un solo lugar porque olvidar una no rompe ningún número:
        solo agrega una consulta por venta, que es la clase de regresión que
        nadie nota hasta que el panel tarda en producción.
        """
        return (self.select_related("receta")
                .prefetch_related("receta__ingredientes__ingrediente",
                                  "extras__extra__ingrediente",
                                  "sustituciones__ingrediente_original",
                                  "sustituciones__ingrediente_nuevo"))

    def sin_costo_completo(self):
        """Ventas que no se pueden reconocer: sin costear o costeadas a medias."""
        return self.filter(models.Q(costo_fifo__isnull=True) |
                           models.Q(costo_incompleto=True))

    def comerciales(self):
        """Las que tienen margen: una cortesía no lo tiene, porque no cobra.

        Meterlas hunde el margen del producto sin que su precio ni su costo
        hayan cambiado. Va aquí, junto a `con_costeo()`, por la misma razón:
        es una regla que varios paneles tienen que repetir igual, y olvidarla
        en uno no rompe nada —solo publica otro número.
        """
        return self.filter(es_cortesia=False).con_costeo()


class Venta(models.Model):
    """Registro de una venta de shakes."""

    objects = VentaQuerySet.as_manager()

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
    descuento_pct = models.DecimalField(
        "Descuento (%)", max_digits=5, decimal_places=2,
        default=Decimal("0"), db_default=Decimal("0"),
        validators=[MinValueValidator(Decimal("0")),
                    MaxValueValidator(Decimal("100"))],
        help_text="Rebaja sobre el precio de lista. No cambia el costo: solo "
                  "baja lo que se cobró.",
    )
    es_cortesia = models.BooleanField(
        "Cortesía", default=False,
        help_text="Producto regalado (activación): sin ingreso, pero consume inventario.",
    )

    # ── Costeo real (FIFO). Lo escribe inventario.costeo, nadie más. ──────────
    costo_fifo = models.DecimalField(
        "Costo real (FIFO)", max_digits=12, decimal_places=2,
        null=True, blank=True, editable=False,
        help_text="Costo de las capas de compra que surtieron esta venta. "
                  "Vacío significa «sin costear», nunca cero.",
    )
    costo_incompleto = models.BooleanField(
        "Costo incompleto", default=False, db_default=False, editable=False,
        help_text="Faltaron compras para surtir parte del consumo: el costo "
                  "solo cubre lo que sí tenía capa.",
    )
    costeada_en = models.DateTimeField(null=True, blank=True, editable=False)

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
    def ingreso_lista(self):
        """Lo que costaría sin descuento: precio de lista por lo que se llevó.

        Se separa de `ingreso` para que la nota pueda enseñar de cuánto era
        antes y cuánto se le rebajó. Sin este dato, un descuento del 100% y una
        cortesía se ven idénticos en los reportes, y no son lo mismo: la
        cortesía va contra 506 y el descuento contra el ingreso.
        """
        # Shakes a precio base + add-ons cobrados una vez por la línea.
        # El precio ya incluye IVA (16%); ver subtotal/iva para el desglose.
        return self.cantidad * self.precio_efectivo + self.cargo_extras

    @property
    def descuento_monto(self):
        if self.es_cortesia or not self.descuento_pct:
            return Decimal("0")
        return self.ingreso_lista * self.descuento_pct / Decimal("100")

    @property
    def ingreso(self):
        # Una cortesía es gratis: no genera ingreso (pero sí consume inventario).
        if self.es_cortesia:
            return Decimal("0")
        # Se redondea UNA sola vez, aquí, y no en cada parte: el descuento sobre
        # un total impar deja fracciones de centavo, y cuadrarlas por separado
        # deja al total de la nota sin coincidir con la suma de sus líneas.
        return (self.ingreso_lista - self.descuento_monto).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP)

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
    def costo_esta_completo(self):
        """¿El costo guardado cubre todo lo que la venta consumió?

        Es el invariante I2 en una sola línea: sin esto, ni se reconoce el
        asiento ni el panel puede confiar en el número. `costo_fifo = 0.00` con
        `costo_incompleto` no es «salió gratis», es «no se sabe».
        """
        return self.costo_fifo is not None and not self.costo_incompleto

    @property
    def costo_de_ventas(self):
        """Costo de esta venta para paneles comerciales.

        El costo real (`costo_fifo`) si está completo; si no, el estimado del
        catálogo, que sirve para no dejar el panel en blanco. Publicar un costo
        a medias infla el margen justo donde menos se sabe.

        Los ASIENTOS NO usan esta propiedad: la contabilidad lee `costo_fifo` y
        nada más, porque un estimado en el libro mayor es un costo inventado.
        """
        return self.costo_fifo if self.costo_esta_completo else self.costo_total

    @property
    def ganancia(self):
        return self.ingreso - self.costo_de_ventas


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
        """Cantidad que la receta usa del ingrediente original (la que se sustituye).

        Se busca en `.all()` y se filtra en Python: un `.filter()` abriría un
        queryset nuevo y consultaría una vez por sustitución, aunque quien nos
        llame ya haya precargado la receta con `con_costeo()`. Una receta trae
        un puñado de ingredientes, así que recorrerlos no cuesta nada.
        """
        for item in self.venta.receta.ingredientes.all():
            if item.ingrediente_id == self.ingrediente_original_id:
                return item.cantidad
        return Decimal("0")

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


class AjusteInventario(models.Model):
    """Un conteo físico: lo que el sistema calculaba contra lo que de verdad hay.

    La diferencia hacia abajo es merma —se echó a perder, se tiró, se derramó—
    y sale del inventario consumiendo capas por FIFO, igual que una venta. No
    basta con registrar un gasto: si el inventario contable no baja, la cuenta
    115 se queda inflada y el faltante reaparece en el siguiente conteo.

    La diferencia hacia ARRIBA no se postea. Que aparezca mercancía significa
    que falta capturar una compra, no que el negocio ganó inventario; darle
    entrada obligaría a inventarle un precio, que es exactamente lo que el
    costeo entero existe para no hacer. Se guarda el conteo como constancia y
    se avisa qué falta.
    """

    fecha = models.DateField(default=timezone.localdate)
    ingrediente = models.ForeignKey(
        Ingrediente, on_delete=models.PROTECT, related_name="ajustes")
    cantidad_calculada = models.DecimalField(
        "Lo que el sistema calculaba", max_digits=14, decimal_places=4,
        editable=False,
        help_text="Congelado al momento del conteo: el stock de mañana ya no "
                  "explica la merma de hoy.")
    cantidad_real = models.DecimalField(
        "Lo que hay de verdad", max_digits=14, decimal_places=4,
        validators=[MinValueValidator(Decimal("0"))])
    costo = models.DecimalField(
        "Costo de la merma ($)", max_digits=12, decimal_places=2,
        null=True, blank=True, editable=False,
        help_text="Lo que valía lo que se perdió, según las capas que lo "
                  "surtieron. Vacío mientras no se ha costeado.")
    costo_incompleto = models.BooleanField(
        default=False, editable=False,
        help_text="Faltaban capas para respaldar parte de lo perdido.")
    motivo = models.CharField(max_length=200, blank=True)
    creado = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Ajuste de inventario (merma)"
        verbose_name_plural = "Ajustes de inventario (mermas)"
        ordering = ["-fecha", "-id"]
        indexes = [models.Index(fields=["fecha"]),
                   models.Index(fields=["ingrediente", "fecha"])]

    def __str__(self):
        return f"{self.fecha} · {self.ingrediente} · {self.diferencia:+}"

    @property
    def diferencia(self):
        """Real − calculado. Negativa es merma; positiva, una compra sin capturar."""
        return self.cantidad_real - self.cantidad_calculada

    @property
    def merma(self):
        """Cuánto se perdió, en unidades de receta. Cero si sobró o cuadró."""
        falta = self.cantidad_calculada - self.cantidad_real
        return falta if falta > 0 else Decimal("0")

    @property
    def es_merma(self):
        return self.merma > 0

    @property
    def sobrante(self):
        dif = self.diferencia
        return dif if dif > 0 else Decimal("0")


class ConsumoCapa(models.Model):
    """Qué capa de compra surtió a qué salida de inventario, y por cuánto.

    Es el rastro que hace auditable el costo de una venta y, sobre todo, lo que
    permite deshacerlo: para recostear hay que devolverle a cada compra las
    unidades que esta venta le tomó, y sin este registro no hay forma de saber
    cuáles fueron.

    `compra` en blanco significa que faltaban capas para esa parte del consumo:
    se deja constancia con importe cero en vez de inventar el costo con el
    precio del catálogo, que abonaría inventario que nunca entró.

    Una fila cuelga de una venta O de un ajuste de inventario, nunca de las dos
    ni de ninguna. La merma vive aquí y no en su propia tabla a propósito: el
    invariante que audita `recostear --verificar` es «lo consumido más el saldo
    da lo que trajo la capa», y lo consumido lo cuenta esta tabla. Una tabla
    aparte dejaría ese invariante roto en cada capa que tocara una merma, y ese
    auditor es lo único que avisa cuando el costeo se descompone.
    """

    venta = models.ForeignKey(
        "Venta", on_delete=models.CASCADE, related_name="consumos",
        null=True, blank=True)
    ajuste = models.ForeignKey(
        "AjusteInventario", on_delete=models.CASCADE, related_name="consumos",
        null=True, blank=True,
        help_text="La merma que se llevó estas unidades, si no fue una venta.")
    compra = models.ForeignKey(
        "Compra", on_delete=models.CASCADE, null=True, blank=True,
        related_name="consumos")
    ingrediente = models.ForeignKey(Ingrediente, on_delete=models.PROTECT)
    # Los decimales de más son a propósito: el redondeo a centavos ocurre una
    # sola vez, al escribir Venta.costo_fifo. Redondear por capa dejaría un
    # residuo atrapado en Inventario que crece con el número de capas.
    cantidad_receta = models.DecimalField(max_digits=14, decimal_places=4)
    costo_unitario = models.DecimalField(max_digits=14, decimal_places=6,
                                         default=Decimal("0"))
    importe = models.DecimalField(max_digits=14, decimal_places=4,
                                  default=Decimal("0"))

    class Meta:
        verbose_name = "Consumo de capa"
        verbose_name_plural = "Consumos de capas"
        indexes = [
            models.Index(fields=["ingrediente", "venta"]),
            models.Index(fields=["compra"]),
            models.Index(fields=["ajuste"]),
        ]
        constraints = [
            # Una salida de inventario tiene exactamente un dueño. Sin esto,
            # una fila huérfana consumiría saldo que nadie puede devolver, y
            # una con los dos lo devolvería dos veces al deshacerse.
            models.CheckConstraint(
                condition=(models.Q(venta__isnull=False, ajuste__isnull=True) |
                           models.Q(venta__isnull=True, ajuste__isnull=False)),
                name="consumo_de_una_venta_o_de_un_ajuste",
            ),
        ]

    def __str__(self):
        origen = self.compra_id or "sin capa"
        dueño = f"venta {self.venta_id}" if self.venta_id else f"merma {self.ajuste_id}"
        return f"{dueño} ← compra {origen}: {self.cantidad_receta}"


class ConfiguracionAlarmas(models.Model):
    """Ajustes de los avisos del panel. Solo existe un registro (id=1).

    Es de alarmas, no del costeo: las tres reglas del costeo no llevan
    perilla, y una clase que se llamara «configuración del costeo» invitaría
    a ponerles una.
    """

    umbral_caida_margen = models.PositiveSmallIntegerField(
        "Avisar si el margen baja (%)", default=10,
        validators=[MinValueValidator(1)],
        help_text="Caída respecto al mes anterior a partir de la cual se "
                  "enciende la alarma. 10 = de 60% a 54% ya avisa.")

    class Meta:
        verbose_name = "Configuración de alarmas"
        verbose_name_plural = "Configuración de alarmas"

    def __str__(self):
        return f"Alarma de margen: baja de {self.umbral_caida_margen}%"

    def save(self, *args, **kwargs):
        self.pk = 1
        super().save(*args, **kwargs)

    @classmethod
    def get(cls):
        """Los ajustes guardados, o los de fábrica si nadie los ha tocado.

        Sin `get_or_create`: leer el panel es una consulta de solo lectura y
        no tiene por qué escribir en la base para responderla.
        """
        return cls.objects.filter(pk=1).first() or cls()
