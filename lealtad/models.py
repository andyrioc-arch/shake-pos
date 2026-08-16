"""Modelos del programa de lealtad.

Todo lo que define el programa —niveles, premios, reglas de puntos, plantillas
de mensaje y cuándo se envían— vive en la base de datos y es editable desde el
panel o el admin. El código no trae reglas de negocio "quemadas".
"""

import re
import secrets
import uuid
from decimal import Decimal

from django.core.validators import MinValueValidator, MaxValueValidator
from django.db import models
from django.urls import reverse
from django.utils import timezone

#: Alfabeto sin I, O, 0 ni 1: se dictan y se teclean en la caja sin confundirse.
ALFABETO_CODIGO = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


def genera_codigo_cliente():
    """Código corto y único para identificar a un cliente en el mostrador."""
    for _ in range(20):
        codigo = "".join(secrets.choice(ALFABETO_CODIGO) for _ in range(6))
        if not Cliente.objects.filter(codigo=codigo).exists():
            return codigo
    raise RuntimeError(
        "No se pudo generar un código de cliente libre. "
        "Considera alargar el código si el padrón creció mucho.")


def resuelve_id(modelo, valor):
    """Objeto por su id, o None si el valor viene vacío o no es un número.

    Los `<select>` opcionales de un formulario mandan cadena vacía y un ERP
    externo puede mandar cualquier cosa; pasarle eso a un filtro por id lanza
    ValueError. Aquí se traduce a «ninguno».
    """
    try:
        return modelo.objects.filter(pk=int(valor)).first()
    except (TypeError, ValueError):
        return None

# ══════════════════════════════════════════════════════════════════════════════
#  TELÉFONOS
# ══════════════════════════════════════════════════════════════════════════════

LADA_PAIS = "52"          # México
LARGO_NOMBRE = 80         # fuente de verdad: Cliente.nombre lo declara con esto


class TelefonoInvalido(ValueError):
    """El número capturado no es un celular mexicano válido."""


def normaliza_telefono(valor):
    """Devuelve el número en formato E.164 (+521234567890).

    Acepta lo que la gente teclea de verdad: con espacios, guiones, paréntesis,
    con o sin lada de país, y el viejo '1' que México usaba para celulares.
    """
    if valor is None:
        raise TelefonoInvalido("Falta el número de teléfono.")
    digitos = re.sub(r"\D", "", str(valor))
    if digitos.startswith("00"):
        digitos = digitos[2:]
    if len(digitos) == 10:
        digitos = LADA_PAIS + digitos
    elif len(digitos) == 13 and digitos.startswith(LADA_PAIS + "1"):
        # +52 1 55 1234 5678 → el "1" ya no se usa.
        digitos = LADA_PAIS + digitos[3:]
    if len(digitos) != 12 or not digitos.startswith(LADA_PAIS):
        raise TelefonoInvalido(
            f"'{valor}' no parece un celular de 10 dígitos.")
    return "+" + digitos


def normaliza_texto(valor, largo):
    """Deja un texto capturado a mano listo para guardar: sin espacios de sobra
    y sin pasarse del largo de su columna.

    Postgres rechaza un valor más largo que la columna; SQLite lo acepta, así
    que sin este tope el problema solo aparecería en producción. Se trunca en
    vez de rechazar: un nombre largo no debe costarle los puntos al cliente.

    El `rstrip` final es por el corte: `split()` ya quitó el espacio de la
    izquierda, pero truncar puede dejar uno a la derecha.
    """
    return " ".join((valor or "").split())[:largo].rstrip()


def normaliza_nombre(valor):
    return normaliza_texto(valor, LARGO_NOMBRE)


def telefono_bonito(e164):
    """+525512345678 → 55 1234 5678 (para mostrar en pantalla)."""
    d = re.sub(r"\D", "", e164 or "")
    if len(d) == 12:
        d = d[2:]
    if len(d) != 10:
        return e164 or ""
    return f"{d[:2]} {d[2:6]} {d[6:]}"


# ══════════════════════════════════════════════════════════════════════════════
#  CONFIGURACIÓN DEL PROGRAMA
# ══════════════════════════════════════════════════════════════════════════════

class ConfiguracionPrograma(models.Model):
    """Ajustes generales del programa. Solo existe un registro (id=1)."""

    class Proveedor(models.TextChoices):
        SIMULADO = "simulado", "Simulado (no envía, solo registra)"
        WHATSAPP = "whatsapp", "WhatsApp Business API (Meta)"
        TWILIO = "twilio", "Twilio (SMS)"

    nombre_negocio = models.CharField(max_length=80, default="Habits Shakes")
    activo = models.BooleanField(
        "Programa activo", default=True,
        help_text="Si lo apagas, las ventas dejan de acumular puntos.")

    pesos_por_punto = models.DecimalField(
        "Pesos por punto", max_digits=8, decimal_places=2,
        default=Decimal("10"),
        validators=[MinValueValidator(Decimal("0.01"))],
        help_text="$10 = 1 punto. Una compra de $130 otorga 13 puntos.")
    vigencia_puntos_meses = models.PositiveSmallIntegerField(
        "Vigencia de los puntos (meses)", default=12,
        help_text="0 = los puntos nunca caducan.")
    aviso_expiracion_dias = models.PositiveSmallIntegerField(
        "Avisar antes de que caduquen (días)", default=15)

    dias_inactividad = models.PositiveSmallIntegerField(
        "Días sin comprar para considerar inactivo", default=30)
    ventana_atribucion_dias = models.PositiveSmallIntegerField(
        "Ventana de atribución (días)", default=7,
        help_text="Compras dentro de este plazo tras un mensaje cuentan como "
                  "venta atribuida a ese mensaje.")

    site_url = models.URLField(
        "Dirección pública del sitio", blank=True,
        help_text="Ej. https://lealtad.tunegocio.mx — se usa en los enlaces "
                  "que se le mandan al cliente. Vacío = se deduce del request.")

    proveedor_mensajes = models.CharField(
        "Enviar mensajes por", max_length=20,
        choices=Proveedor.choices, default=Proveedor.SIMULADO)
    envio_hora_inicio = models.PositiveSmallIntegerField(
        "No enviar antes de (hora)", default=9,
        validators=[MaxValueValidator(23)])
    envio_hora_fin = models.PositiveSmallIntegerField(
        "No enviar después de (hora)", default=21,
        validators=[MaxValueValidator(23)])

    api_token = models.CharField(
        "Token de la API", max_length=64, blank=True,
        help_text="Requerido en el encabezado Authorization: Bearer <token> "
                  "para que un ERP externo registre compras.")

    class Meta:
        verbose_name = "Configuración del programa"
        verbose_name_plural = "Configuración del programa"

    def __str__(self):
        return f"Programa de lealtad · {self.nombre_negocio}"

    def save(self, *args, **kwargs):
        self.pk = 1
        super().save(*args, **kwargs)

    @classmethod
    def get(cls):
        cfg, _ = cls.objects.get_or_create(pk=1)
        return cfg

    def puntos_de(self, monto):
        """Puntos base que otorga un monto, sin multiplicadores."""
        if not monto or self.pesos_por_punto <= 0:
            return 0
        return int(Decimal(monto) / self.pesos_por_punto)

    def url_de(self, ruta):
        base = (self.site_url or "").rstrip("/")
        return f"{base}{ruta}" if base else ruta


# ══════════════════════════════════════════════════════════════════════════════
#  NIVELES
# ══════════════════════════════════════════════════════════════════════════════

class Nivel(models.Model):
    """Escalón del programa. Se alcanza con los puntos históricos (no bajan)."""

    nombre = models.CharField(max_length=40, unique=True)
    emoji = models.CharField(max_length=8, blank=True)
    puntos_requeridos = models.PositiveIntegerField(
        "Puntos históricos requeridos", default=0)
    beneficios = models.TextField(
        blank=True, help_text="Qué obtiene el cliente en este nivel.")
    color = models.CharField(
        max_length=9, default="#fa5598",
        help_text="Color de la tarjeta digital en este nivel.")
    activo = models.BooleanField(default=True)

    class Meta:
        verbose_name = "Nivel"
        verbose_name_plural = "Niveles"
        ordering = ["puntos_requeridos"]

    def __str__(self):
        return f"{self.emoji} {self.nombre}".strip()


# ══════════════════════════════════════════════════════════════════════════════
#  CLIENTES
# ══════════════════════════════════════════════════════════════════════════════

class Cliente(models.Model):
    """Un cliente del programa, identificado por su celular."""

    class Estado(models.TextChoices):
        ACTIVO = "activo", "Activo"
        INACTIVO = "inactivo", "Inactivo"
        BAJA = "baja", "Dado de baja"

    class Origen(models.TextChoices):
        CAJA = "caja", "Registrado en caja"
        AUTOSERVICIO = "autoservicio", "Se registró él mismo (QR)"
        API = "api", "Alta por API"
        MANUAL = "manual", "Alta manual"

    telefono = models.CharField(
        "Celular", max_length=20, unique=True,
        help_text="Se guarda en formato internacional (+52...).")
    nombre = models.CharField("Nombre", max_length=LARGO_NOMBRE, blank=True)
    cumpleanos = models.DateField("Cumpleaños", null=True, blank=True)

    token = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    codigo = models.CharField(
        "Código", max_length=6, unique=True, editable=False,
        help_text="Código corto que el staff teclea para encontrarlo en la caja.")

    puntos_saldo = models.IntegerField("Puntos disponibles", default=0)
    puntos_historicos = models.PositiveIntegerField(
        "Puntos acumulados de por vida", default=0)
    gasto_historico = models.DecimalField(
        "Gasto acumulado ($)", max_digits=12, decimal_places=2,
        default=Decimal("0"))
    visitas = models.PositiveIntegerField(default=0)

    alta = models.DateTimeField(auto_now_add=True)
    ultima_compra = models.DateField(null=True, blank=True)
    estado = models.CharField(
        max_length=10, choices=Estado.choices, default=Estado.ACTIVO)
    acepta_mensajes = models.BooleanField(
        "Acepta recibir mensajes", default=True)
    origen = models.CharField(
        max_length=15, choices=Origen.choices, default=Origen.CAJA)
    notas = models.CharField(max_length=200, blank=True)

    class Meta:
        verbose_name = "Cliente"
        verbose_name_plural = "Clientes"
        ordering = ["-alta"]
        indexes = [
            models.Index(fields=["ultima_compra"]),
            models.Index(fields=["puntos_historicos"]),
        ]

    def __str__(self):
        return f"{self.nombre or 'Cliente'} · {self.telefono_display}"

    def save(self, *args, **kwargs):
        if self.telefono:
            self.telefono = normaliza_telefono(self.telefono)
        # Igual que el teléfono: se normaliza aquí y no en cada vista, para que
        # ningún camino de escritura pueda desbordar la columna en Postgres.
        for campo in ("nombre", "notas"):
            largo = self._meta.get_field(campo).max_length
            setattr(self, campo, normaliza_texto(getattr(self, campo), largo))
        if not self.codigo:
            self.codigo = genera_codigo_cliente()
            if kwargs.get("update_fields") is not None:
                kwargs["update_fields"] = [*kwargs["update_fields"], "codigo"]
        super().save(*args, **kwargs)

    # ── Presentación ──────────────────────────────────────────────────────
    @property
    def telefono_display(self):
        return telefono_bonito(self.telefono)

    @property
    def nombre_corto(self):
        return (self.nombre or "").split(" ")[0] or "Cliente"

    def get_absolute_url(self):
        return reverse("lealtad_tarjeta", args=[self.token])

    # ── Nivel y progreso ──────────────────────────────────────────────────
    @property
    def nivel(self):
        return (Nivel.objects.filter(
            activo=True, puntos_requeridos__lte=self.puntos_historicos)
            .order_by("-puntos_requeridos").first())

    @property
    def siguiente_nivel(self):
        return (Nivel.objects.filter(
            activo=True, puntos_requeridos__gt=self.puntos_historicos)
            .order_by("puntos_requeridos").first())

    @property
    def siguiente_premio(self):
        """El premio alcanzable más cercano al saldo actual."""
        return (Premio.objects.filter(
            activo=True, puntos_requeridos__gt=self.puntos_saldo)
            .order_by("puntos_requeridos").first())

    @property
    def progreso_pct(self):
        """% de avance hacia el siguiente premio (0-100)."""
        premio = self.siguiente_premio
        if not premio or premio.puntos_requeridos <= 0:
            return 100
        return min(100, int(self.puntos_saldo * 100 / premio.puntos_requeridos))

    def premios_disponibles(self):
        """Premios que hoy puede canjear con su saldo y su nivel."""
        return [p for p in Premio.objects.filter(activo=True)
                .select_related("nivel_minimo", "receta")
                if p.puede_canjearlo(self)]

    @property
    def dias_sin_comprar(self):
        if not self.ultima_compra:
            return None
        return (timezone.localdate() - self.ultima_compra).days

    @property
    def ticket_promedio(self):
        if not self.visitas:
            return Decimal("0")
        return self.gasto_historico / self.visitas

    # ── Descuentos ────────────────────────────────────────────────────────
    @property
    def descuento_vigente(self):
        """El descuento que hoy le toca, o None.

        Si tiene varios encimados gana el de mayor porcentaje. Tiene que haber
        una regla y no un «el que salga»: dos descuentos vigentes a la vez son
        normales —uno de convenio y uno de temporada— y sin criterio el mismo
        cliente pagaría distinto según el orden en que se dieron de alta. Se
        elige el mayor porque es el que el cliente va a reclamar.
        """
        return self.descuentos.vigentes().order_by("-porcentaje", "-id").first()


# ══════════════════════════════════════════════════════════════════════════════
#  DESCUENTOS POR CLIENTE
# ══════════════════════════════════════════════════════════════════════════════

class DescuentoClienteQuerySet(models.QuerySet):
    def vigentes(self, fecha=None):
        """Los que hoy aplican: activos, ya empezados y sin vencer.

        `hasta` vacío es «sin vencimiento», no «vencido»: un convenio suele no
        tener fecha de fin, y tratarlo como caducado dejaría al cliente pagando
        completo sin que nadie tocara nada.
        """
        fecha = fecha or timezone.localdate()
        return self.filter(activo=True, desde__lte=fecha).filter(
            models.Q(hasta__isnull=True) | models.Q(hasta__gte=fecha))


class DescuentoCliente(models.Model):
    """Un descuento pactado con un cliente concreto, con su vigencia.

    Cuelga de `Cliente` y no de un nombre suelto porque el mostrador identifica
    al cliente por su celular, que es lo único que se puede teclear sin
    equivocarse. Un descuento a nombre de «Juan» no se puede aplicar: hay tres
    Juanes y el cajero no sabe cuál.
    """

    cliente = models.ForeignKey(
        Cliente, on_delete=models.CASCADE, related_name="descuentos")
    porcentaje = models.DecimalField(
        "Descuento (%)", max_digits=5, decimal_places=2,
        validators=[MinValueValidator(Decimal("0.01")),
                    MaxValueValidator(Decimal("100"))],
        help_text="15 = 15% de descuento sobre el total.")
    descripcion = models.CharField(
        "Por qué", max_length=200, blank=True,
        help_text="Convenio, empleado, cortesía de temporada…")
    desde = models.DateField("Desde", default=timezone.localdate)
    hasta = models.DateField(
        "Hasta", null=True, blank=True,
        help_text="Vacío = sin vencimiento.")
    activo = models.BooleanField(default=True)
    creado = models.DateTimeField(auto_now_add=True)

    objects = DescuentoClienteQuerySet.as_manager()

    class Meta:
        verbose_name = "Descuento de cliente"
        verbose_name_plural = "Descuentos de clientes"
        ordering = ["-desde", "-id"]
        indexes = [models.Index(fields=["activo", "desde", "hasta"])]

    def __str__(self):
        return f"{self.cliente.nombre_corto} · {self.porcentaje}%"

    def esta_vigente(self, fecha=None):
        fecha = fecha or timezone.localdate()
        return (self.activo and self.desde <= fecha
                and (self.hasta is None or self.hasta >= fecha))

    @property
    def estado(self):
        """Para la pantalla: por qué no aplica, cuando no aplica.

        Quién decide si aplica es `esta_vigente()`, y esto solo explica el «no».
        Repetir aquí las tres comparaciones dejaría dos definiciones de vigente
        que hay que mantener iguales a mano, y el día que una cambie la
        pantalla diría «vigente» sobre un descuento que la caja ya no aplica.
        """
        if self.esta_vigente():
            return "vigente"
        hoy = timezone.localdate()
        if not self.activo:
            return "desactivado"
        if self.desde > hoy:
            return "programado"
        return "vencido"


# ══════════════════════════════════════════════════════════════════════════════
#  PREMIOS
# ══════════════════════════════════════════════════════════════════════════════

class Premio(models.Model):
    """Recompensa canjeable con puntos. Totalmente editable por el admin."""

    class Tipo(models.TextChoices):
        PRODUCTO = "producto", "Producto gratis"
        DESCUENTO_PCT = "descuento_pct", "Descuento en %"
        DESCUENTO_MONTO = "descuento_monto", "Descuento en $"
        COMBO = "combo", "Combo / varios productos"
        LIBRE = "libre", "Otro (se describe en el texto)"

    nombre = models.CharField(max_length=80, unique=True)
    emoji = models.CharField(max_length=8, blank=True)
    descripcion = models.CharField(max_length=200, blank=True)
    puntos_requeridos = models.PositiveIntegerField("Cuesta (puntos)")
    tipo = models.CharField(max_length=20, choices=Tipo.choices,
                            default=Tipo.PRODUCTO)
    receta = models.ForeignKey(
        "inventario.Receta", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="premios",
        help_text="Producto que se regala o descuenta. Sirve para calcular "
                  "solo el costo y el valor del premio.")
    cantidad = models.PositiveSmallIntegerField(
        default=1, help_text="Cuántas unidades del producto incluye.")
    valor = models.DecimalField(
        "Valor / porcentaje", max_digits=10, decimal_places=2,
        default=Decimal("0"),
        help_text="Para descuento en %: 50 = mitad de precio. "
                  "Para descuento en $: el monto. Para producto gratis: se ignora.")

    nivel_minimo = models.ForeignKey(
        Nivel, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="premios", verbose_name="Nivel mínimo")
    limite_por_cliente = models.PositiveSmallIntegerField(
        "Límite por cliente", default=0, help_text="0 = sin límite.")
    vigencia_dias = models.PositiveSmallIntegerField(
        "Vigencia del canje (días)", default=30,
        help_text="Días que tiene el cliente para reclamarlo. 0 = sin límite.")
    orden = models.PositiveSmallIntegerField(default=0)
    activo = models.BooleanField(default=True)

    class Meta:
        verbose_name = "Premio"
        verbose_name_plural = "Premios"
        ordering = ["orden", "puntos_requeridos"]

    def __str__(self):
        return f"{self.emoji} {self.nombre}".strip()

    # ── Economía del premio (se calcula desde la receta) ───────────────────
    @property
    def valor_percibido(self):
        """Lo que el cliente siente que recibe (precio de lista)."""
        if self.receta:
            precio = self.receta.precio_venta * self.cantidad
            if self.tipo == self.Tipo.DESCUENTO_PCT:
                return precio * self.valor / Decimal("100")
            if self.tipo == self.Tipo.DESCUENTO_MONTO:
                return min(self.valor, precio)
            return precio
        if self.tipo == self.Tipo.DESCUENTO_MONTO:
            return self.valor
        return Decimal("0")

    @property
    def consume_inventario(self):
        """¿Entregarlo saca algo del almacén?

        Un descuento no: cuesta ingreso no percibido. Un producto o un combo
        sí, y por eso al entregarse crean una cortesía. La clasificación vive
        aquí, en una sola línea, porque antes estaba escrita a mano en el
        servicio y en la vista de premios, y las dos listas ya no coincidían.
        """
        return self.tipo in (self.Tipo.PRODUCTO, self.Tipo.COMBO)

    @property
    def costo_estimado(self):
        """Lo que de verdad te cuesta entregarlo.

        Ojo con unificar esta condición con `consume_inventario`: no son la
        misma pregunta. Un premio LIBRE no saca nada del almacén, pero aquí
        cae al ramo de la receta, y cambiarlo movería un número publicado.
        """
        if self.tipo in (self.Tipo.DESCUENTO_PCT, self.Tipo.DESCUENTO_MONTO):
            # Un descuento no consume insumos extra: cuesta ingreso no percibido.
            return self.valor_percibido
        if self.receta:
            return self.receta.costo_receta * self.cantidad
        return Decimal("0")

    @property
    def costo_por_punto(self):
        """Cuánto te cuesta cada punto en este premio. Para cuidar el margen."""
        if not self.puntos_requeridos:
            return Decimal("0")
        return self.costo_estimado / self.puntos_requeridos

    def puede_canjearlo(self, cliente):
        if not self.activo or cliente.puntos_saldo < self.puntos_requeridos:
            return False
        if self.nivel_minimo_id:
            nivel = cliente.nivel
            if not nivel or nivel.puntos_requeridos < self.nivel_minimo.puntos_requeridos:
                return False
        if self.limite_por_cliente:
            usados = self.canjes.filter(cliente=cliente).exclude(
                estado=Canje.Estado.CANCELADO).count()
            if usados >= self.limite_por_cliente:
                return False
        return True


# ══════════════════════════════════════════════════════════════════════════════
#  COMPRAS Y PUNTOS
# ══════════════════════════════════════════════════════════════════════════════

class Compra(models.Model):
    """Une una venta con el cliente que la hizo y los puntos que le dio.

    Cuando la venta se registra en la caja apunta a una `inventario.Nota`.
    Cuando llega de un ERP externo por la API, guarda su número de ticket.
    """

    class Origen(models.TextChoices):
        CAJA = "caja", "Caja"
        API = "api", "API / ERP externo"
        MANUAL = "manual", "Captura manual"

    cliente = models.ForeignKey(
        Cliente, on_delete=models.CASCADE, related_name="compras")
    nota = models.OneToOneField(
        "inventario.Nota", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="compra_lealtad")
    ticket = models.CharField(
        "Número de ticket", max_length=40, blank=True,
        help_text="Folio del ERP externo. Evita registrar dos veces la misma venta.")
    monto = models.DecimalField(max_digits=12, decimal_places=2)
    puntos_ganados = models.PositiveIntegerField(default=0)
    multiplicador = models.DecimalField(
        max_digits=4, decimal_places=2, default=Decimal("1"))
    fecha = models.DateField()
    origen = models.CharField(max_length=10, choices=Origen.choices,
                              default=Origen.CAJA)
    creada = models.DateTimeField(auto_now_add=True)
    nivel_alcanzado = models.ForeignKey(
        "Nivel", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="compras_que_lo_desbloquearon",
        help_text="El nivel que esta compra desbloqueó, si desbloqueó alguno. "
                  "Se escribe al registrarla, cuando el dato se conoce.")

    class Meta:
        verbose_name = "Compra del programa"
        verbose_name_plural = "Compras del programa"
        ordering = ["-fecha", "-id"]
        constraints = [
            models.UniqueConstraint(
                fields=["ticket"], condition=~models.Q(ticket=""),
                name="lealtad_ticket_unico"),
        ]

    def __str__(self):
        return f"{self.fecha} · {self.cliente.telefono_display} · ${self.monto:,.2f}"


class MovimientoPuntos(models.Model):
    """Libro mayor de puntos: todo movimiento queda aquí, nada se borra.

    Todo movimiento que SUMA puntos es un «lote»: lleva su propio `saldo_lote`
    (lo que queda sin gastar) y su `expira_el`. Los canjes y la caducidad los
    consumen en orden FIFO, primero el que vence antes. Da igual si el lote
    nació de una compra, de una cortesía o de la devolución de un canje: lo que
    define a un lote es tener saldo vivo, no su tipo.
    """

    class Tipo(models.TextChoices):
        GANA = "gana", "Ganó puntos"
        CANJE = "canje", "Canjeó un premio"
        AJUSTE = "ajuste", "Ajuste manual"
        EXPIRA = "expira", "Puntos caducados"
        DEVOLUCION = "devolucion", "Devolución de un canje"

    #: Tipos que cuentan para los puntos de por vida (los que dan el nivel).
    #: La devolución NO cuenta: esos puntos ya se habían acreditado al ganarlos.
    ACUMULAN = (Tipo.GANA, Tipo.AJUSTE)

    cliente = models.ForeignKey(
        Cliente, on_delete=models.CASCADE, related_name="movimientos")
    tipo = models.CharField(max_length=12, choices=Tipo.choices)
    puntos = models.IntegerField(help_text="Positivo suma, negativo resta.")
    saldo_lote = models.IntegerField(
        default=0,
        help_text="Puntos que quedan sin consumir en este lote.")
    expira_el = models.DateField(null=True, blank=True)
    compra = models.ForeignKey(
        Compra, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="movimientos")
    canje = models.ForeignKey(
        "Canje", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="movimientos")
    descripcion = models.CharField(max_length=160, blank=True)
    creado = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Movimiento de puntos"
        verbose_name_plural = "Movimientos de puntos"
        ordering = ["-creado", "-id"]
        indexes = [models.Index(fields=["cliente", "tipo"]),
                   models.Index(fields=["expira_el"])]

    def __str__(self):
        signo = "+" if self.puntos >= 0 else ""
        return f"{self.cliente.telefono_display}: {signo}{self.puntos} pts"


class Canje(models.Model):
    """Un premio reclamado por un cliente. Lo autoriza el staff."""

    class Estado(models.TextChoices):
        PENDIENTE = "pendiente", "Pendiente de entregar"
        ENTREGADO = "entregado", "Entregado"
        CANCELADO = "cancelado", "Cancelado (puntos devueltos)"
        EXPIRADO = "expirado", "Expirado"

    cliente = models.ForeignKey(
        Cliente, on_delete=models.CASCADE, related_name="canjes")
    premio = models.ForeignKey(
        Premio, on_delete=models.PROTECT, related_name="canjes")
    puntos_usados = models.PositiveIntegerField()
    codigo = models.CharField(max_length=8, unique=True)
    estado = models.CharField(max_length=10, choices=Estado.choices,
                              default=Estado.PENDIENTE)
    vence_el = models.DateField(null=True, blank=True)
    expira_puntos = models.DateField(
        null=True, blank=True,
        help_text="Vigencia que tenían los puntos gastados aquí. Si el canje "
                  "se cancela, se devuelven con esa misma fecha en vez de "
                  "estrenar 12 meses.")
    creado = models.DateTimeField(auto_now_add=True)
    entregado_en = models.DateTimeField(null=True, blank=True)
    autorizado_por = models.ForeignKey(
        "auth.User", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="canjes_autorizados")
    nota = models.ForeignKey(
        "inventario.Nota", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="canjes")
    venta = models.OneToOneField(
        "inventario.Venta", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="canje",
        help_text="La cortesía que sacó el premio del inventario. Solo para "
                  "premios de producto o combo.")

    class Meta:
        verbose_name = "Canje de premio"
        verbose_name_plural = "Canjes de premios"
        ordering = ["-creado"]

    def __str__(self):
        return f"{self.codigo} · {self.premio} · {self.cliente.telefono_display}"

    @property
    def costo_estimado(self):
        return self.premio.costo_estimado

    @property
    def costo(self):
        """Lo que costó entregarlo.

        Se lee de la cortesía, no se copia: `costo_fifo` no es un valor
        congelado —el costeo lo rehace cada vez que se captura una compra
        vieja— así que una copia aquí se quedaría con el número de la primera
        vez y nadie la repararía. `Venta.costo_de_ventas` ya decide entre el
        real y el estimado con la misma regla que usan los paneles.

        Un descuento no tiene cortesía: no consume insumos, cuesta ingreso no
        percibido, y ahí el estimado no es un respaldo sino la respuesta.
        """
        if self.venta_id:
            return self.venta.costo_de_ventas
        return self.costo_estimado


# ══════════════════════════════════════════════════════════════════════════════
#  PROMOCIONES DE PUNTOS
# ══════════════════════════════════════════════════════════════════════════════

class PromocionPuntos(models.Model):
    """Temporada de puntos multiplicados (ej. puntos dobles esta semana)."""

    nombre = models.CharField(max_length=80)
    multiplicador = models.DecimalField(
        max_digits=4, decimal_places=2, default=Decimal("2"),
        validators=[MinValueValidator(Decimal("1"))])
    desde = models.DateField()
    hasta = models.DateField()
    activa = models.BooleanField(default=True)

    class Meta:
        verbose_name = "Promoción de puntos"
        verbose_name_plural = "Promociones de puntos"
        ordering = ["-desde"]

    def __str__(self):
        return f"{self.nombre} ({self.multiplicador}×)"

    @classmethod
    def vigente(cls, fecha=None):
        fecha = fecha or timezone.localdate()
        return (cls.objects.filter(activa=True, desde__lte=fecha, hasta__gte=fecha)
                .order_by("-multiplicador").first())


# ══════════════════════════════════════════════════════════════════════════════
#  MENSAJERÍA
# ══════════════════════════════════════════════════════════════════════════════

class Plantilla(models.Model):
    """Texto de un mensaje, editable, con variables entre llaves.

    Variables disponibles: {nombre} {saldo} {puntos} {premio} {faltan}
    {nivel} {negocio} {link} {monto} {dias}
    """

    class Canal(models.TextChoices):
        WHATSAPP = "whatsapp", "WhatsApp"
        SMS = "sms", "SMS"

    clave = models.SlugField(
        max_length=50, unique=True,
        help_text="Identificador interno, ej. 'bienvenida'.")
    nombre = models.CharField(max_length=80)
    canal = models.CharField(max_length=10, choices=Canal.choices,
                             default=Canal.WHATSAPP)
    cuerpo = models.TextField(
        help_text="Ej. ¡Hola {nombre}! Tu saldo es de {saldo} puntos.")
    plantilla_meta = models.CharField(
        "Nombre de la plantilla en Meta", max_length=80, blank=True,
        help_text="Solo para WhatsApp fuera de la ventana de 24 h. "
                  "Debe existir aprobada en tu WhatsApp Business.")
    idioma_meta = models.CharField(max_length=10, default="es_MX")
    activa = models.BooleanField(default=True)

    class Meta:
        verbose_name = "Plantilla de mensaje"
        verbose_name_plural = "Plantillas de mensaje"
        ordering = ["nombre"]

    def __str__(self):
        return self.nombre

    def render(self, **variables):
        datos = {
            "nombre": "", "saldo": "", "puntos": "", "premio": "",
            "faltan": "", "nivel": "", "negocio": "", "link": "",
            "monto": "", "dias": "",
        }
        datos.update({k: v for k, v in variables.items() if v is not None})
        try:
            return self.cuerpo.format(**datos)
        except (KeyError, IndexError, ValueError):
            # Una variable mal escrita no debe tumbar el envío.
            return self.cuerpo


DIAS_SEMANA = [("0", "Lun"), ("1", "Mar"), ("2", "Mié"), ("3", "Jue"),
               ("4", "Vie"), ("5", "Sáb"), ("6", "Dom")]


class Automatizacion(models.Model):
    """Regla de "cuándo mandar qué". Se crea y se edita desde el panel."""

    class Disparador(models.TextChoices):
        # Por evento: se encolan en el momento en que ocurren.
        BIENVENIDA = "bienvenida", "Al registrarse"
        PRIMERA_COMPRA = "primera_compra", "Al hacer su primera compra"
        COMPRA = "compra", "Cada vez que compra"
        PREMIO_DISPONIBLE = "premio_disponible", "Cuando ya puede canjear un premio"
        NIVEL = "nivel", "Al alcanzar un nivel"
        # Programados: los evalúa el runner una vez al día.
        CERCA_PREMIO = "cerca_premio", "Cuando está cerca de un premio"
        INACTIVIDAD = "inactividad", "Cuando lleva días sin comprar"
        CUMPLEANOS = "cumpleanos", "Antes de su cumpleaños"
        POR_EXPIRAR = "por_expirar", "Cuando sus puntos están por caducar"

    POR_EVENTO = {Disparador.BIENVENIDA, Disparador.PRIMERA_COMPRA,
                  Disparador.COMPRA, Disparador.PREMIO_DISPONIBLE,
                  Disparador.NIVEL}

    nombre = models.CharField(max_length=80)
    disparador = models.CharField(max_length=20, choices=Disparador.choices)
    plantilla = models.ForeignKey(
        Plantilla, on_delete=models.PROTECT, related_name="automatizaciones")
    activa = models.BooleanField(default=True)

    # Parámetros (cada disparador usa el que le toca)
    umbral_porcentaje = models.PositiveSmallIntegerField(
        "% de avance al premio", default=80,
        validators=[MaxValueValidator(100)],
        help_text="Solo para 'está cerca de un premio'.")
    dias = models.PositiveSmallIntegerField(
        "Días", default=30,
        help_text="Días sin comprar, o días antes del cumpleaños, "
                  "o días antes de que caduquen los puntos.")
    nivel_objetivo = models.ForeignKey(
        Nivel, on_delete=models.CASCADE, null=True, blank=True,
        related_name="automatizaciones",
        help_text="Solo para 'al alcanzar un nivel'. Vacío = cualquier nivel.")

    # Horario
    hora_envio = models.PositiveSmallIntegerField(
        "Hora de envío", default=11, validators=[MaxValueValidator(23)],
        help_text="Solo aplica a los disparadores programados.")
    dias_semana = models.CharField(
        "Días permitidos", max_length=7, default="0123456",
        help_text="Días de la semana en los que se permite enviar.")
    no_repetir_dias = models.PositiveSmallIntegerField(
        "No repetir al mismo cliente en (días)", default=30,
        help_text="0 = se puede repetir siempre.")
    max_por_cliente = models.PositiveSmallIntegerField(
        "Máximo de veces por cliente", default=0, help_text="0 = sin límite.")

    creada = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Automatización"
        verbose_name_plural = "Automatizaciones"
        ordering = ["disparador", "nombre"]

    def __str__(self):
        return self.nombre

    @property
    def es_por_evento(self):
        return self.disparador in self.POR_EVENTO

    def permite_hoy(self, fecha=None):
        fecha = fecha or timezone.localdate()
        return str(fecha.weekday()) in (self.dias_semana or "0123456")


class Campana(models.Model):
    """Envío masivo puntual a un segmento de clientes."""

    class Segmento(models.TextChoices):
        TODOS = "todos", "Todos los clientes"
        ACTIVOS = "activos", "Clientes activos"
        INACTIVOS = "inactivos", "Clientes inactivos"
        NIVEL = "nivel", "De un nivel en específico"
        CERCA_PREMIO = "cerca_premio", "Cerca de un premio"
        CUMPLEANEROS = "cumpleaneros", "Cumpleañeros del mes"
        NUEVOS = "nuevos", "Nuevos (últimos 30 días)"

    class Estado(models.TextChoices):
        BORRADOR = "borrador", "Borrador"
        PROGRAMADA = "programada", "Programada"
        ENVIADA = "enviada", "Enviada"
        CANCELADA = "cancelada", "Cancelada"

    nombre = models.CharField(max_length=80)
    segmento = models.CharField(max_length=15, choices=Segmento.choices,
                                default=Segmento.TODOS)
    nivel = models.ForeignKey(
        Nivel, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="campanas")
    plantilla = models.ForeignKey(
        Plantilla, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="campanas")
    cuerpo = models.TextField(
        blank=True, help_text="Si lo llenas, se usa en vez de la plantilla.")
    programada_para = models.DateTimeField(null=True, blank=True)
    estado = models.CharField(max_length=12, choices=Estado.choices,
                              default=Estado.BORRADOR)
    enviada_en = models.DateTimeField(null=True, blank=True)
    creada = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Campaña"
        verbose_name_plural = "Campañas"
        ordering = ["-creada"]

    def __str__(self):
        return self.nombre


class Mensaje(models.Model):
    """Un mensaje para un cliente: encolado, enviado y con su resultado."""

    class Estado(models.TextChoices):
        PROGRAMADO = "programado", "Programado"
        ENVIADO = "enviado", "Enviado"
        ENTREGADO = "entregado", "Entregado"
        LEIDO = "leido", "Leído"
        FALLIDO = "fallido", "Falló"
        CANCELADO = "cancelado", "Cancelado"

    ESTADOS_ENVIADOS = ("enviado", "entregado", "leido")

    cliente = models.ForeignKey(
        Cliente, on_delete=models.CASCADE, related_name="mensajes")
    automatizacion = models.ForeignKey(
        Automatizacion, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="mensajes")
    campana = models.ForeignKey(
        Campana, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="mensajes")
    plantilla = models.ForeignKey(
        Plantilla, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="mensajes")

    canal = models.CharField(max_length=10, choices=Plantilla.Canal.choices,
                             default=Plantilla.Canal.WHATSAPP)
    telefono = models.CharField(max_length=20)
    cuerpo = models.TextField()
    estado = models.CharField(max_length=12, choices=Estado.choices,
                              default=Estado.PROGRAMADO)

    programado_para = models.DateTimeField(default=timezone.now)
    enviado_en = models.DateTimeField(null=True, blank=True)
    entregado_en = models.DateTimeField(null=True, blank=True)
    leido_en = models.DateTimeField(null=True, blank=True)

    proveedor_id = models.CharField(
        "ID del proveedor", max_length=80, blank=True)
    error = models.CharField(max_length=200, blank=True)
    intentos = models.PositiveSmallIntegerField(default=0)

    compra_atribuida = models.ForeignKey(
        Compra, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="mensajes_atribuidos",
        help_text="Primera compra del cliente dentro de la ventana tras "
                  "recibir el mensaje.")
    creado = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Mensaje"
        verbose_name_plural = "Bandeja de mensajes"
        ordering = ["-creado"]
        indexes = [
            models.Index(fields=["estado", "programado_para"]),
            models.Index(fields=["cliente", "automatizacion"]),
        ]

    def __str__(self):
        return f"{self.get_estado_display()} · {self.telefono}"

    @property
    def enlace_wa(self):
        """Link wa.me para mandarlo a mano desde tu propio WhatsApp."""
        from urllib.parse import quote
        return f"https://wa.me/{self.telefono.lstrip('+')}?text={quote(self.cuerpo)}"


# ══════════════════════════════════════════════════════════════════════════════
#  WALLET
# ══════════════════════════════════════════════════════════════════════════════

class PaseWallet(models.Model):
    """Pase de la tarjeta en el wallet del celular del cliente."""

    class Proveedor(models.TextChoices):
        GOOGLE = "google", "Google Wallet"
        APPLE = "apple", "Apple Wallet"

    cliente = models.ForeignKey(
        Cliente, on_delete=models.CASCADE, related_name="pases")
    proveedor = models.CharField(max_length=10, choices=Proveedor.choices)
    object_id = models.CharField(max_length=120, blank=True)
    url = models.URLField(max_length=2000, blank=True)
    creado = models.DateTimeField(auto_now_add=True)
    sincronizado = models.DateTimeField(null=True, blank=True)
    error = models.CharField(max_length=200, blank=True)

    class Meta:
        verbose_name = "Pase de wallet"
        verbose_name_plural = "Pases de wallet"
        unique_together = ("cliente", "proveedor")

    def __str__(self):
        return f"{self.get_proveedor_display()} · {self.cliente}"
