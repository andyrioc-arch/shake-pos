"""Costeo real de las ventas por FIFO, sobre capas de compra persistidas.

Antes el costo de ventas se recalculaba entero en memoria cada vez que alguien
tocaba un botón: no quedaba guardado en ningún lado y cambiaba solo cuando
entraba una compra vieja. Aquí pasa a ser un hecho: cada compra es una capa con
su saldo, cada venta guarda el costo de las capas que la surtieron, y
`ConsumoCapa` registra cuál surtió a cuál.

Dos reglas que sostienen el módulo:

1. **No se inventa costo.** Si faltan capas para parte del consumo, esa parte se
   registra con `compra=None` e importe cero y la venta queda marcada como
   incompleta. Costearla con el precio del catálogo —lo que se hacía antes—
   abona inventario que nunca entró y deja el activo en negativo.

2. **Una compra retroactiva recuesta lo que le toca.** Vender en la mañana y
   capturar la factura del proveedor en la tarde es lo normal en el mostrador,
   así que registrar una compra devuelve las capas de las ventas posteriores y
   las vuelve a consumir en orden.

Vive en `inventario` y no en `contabilidad` porque consume inventario físico;
contabilidad solo lee el resultado. Pero el costeo sí dispara el re-posteo: si
no, el costo cambia y el asiento se queda con el viejo.
"""
from decimal import Decimal, ROUND_HALF_UP

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from .models import Compra, ConsumoCapa, Venta

CERO = Decimal("0")


def _redondea(valor):
    return valor.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _orden_capas(qs):
    """Las capas se consumen de la más antigua a la más nueva.

    El orden es también el orden en que se toman los bloqueos, para que dos
    costeos simultáneos no se bloqueen en cruz.
    """
    return qs.order_by("fecha", "id")


@transaction.atomic
def descostear_venta(venta):
    """Deshace el costeo de una venta y le devuelve a cada capa lo que le tomó.

    Es la mitad que faltaba: sin devolver el saldo, borrar o recostear una venta
    quemaría inventario en silencio y las ventas siguientes se costearían con
    capas más caras.
    """
    devoluciones = {}
    for compra_id, cantidad in (ConsumoCapa.objects
                                .filter(venta=venta, compra__isnull=False)
                                .values_list("compra_id", "cantidad_receta")):
        devoluciones[compra_id] = devoluciones.get(compra_id, CERO) + cantidad

    if devoluciones:
        # Se bloquean y se escriben en el orden canónico, el mismo que usa
        # `costear_venta`, para que dos operaciones sobre las mismas capas
        # nunca se esperen en cruz.
        capas = list(_orden_capas(
            Compra.objects.select_for_update().filter(pk__in=devoluciones)))
        for capa in capas:
            capa.saldo_receta = (capa.saldo_receta or CERO) + devoluciones[capa.pk]
        Compra.objects.bulk_update(capas, ["saldo_receta"])

    ConsumoCapa.objects.filter(venta=venta).delete()
    Venta.objects.filter(pk=venta.pk).update(
        costo_fifo=None, costo_incompleto=False, costeada_en=None)
    venta.costo_fifo = None
    venta.costo_incompleto = False
    venta.costeada_en = None


@transaction.atomic
def costear_venta(venta, sincronizar=True):
    """Consume capas por FIFO y guarda el costo real de la venta.

    Idempotente: primero deshace lo que hubiera y luego vuelve a consumir, así
    que llamarla dos veces deja el mismo resultado.
    """
    descostear_venta(venta)

    total = CERO
    incompleto = False
    consumos = []

    for ingrediente_id, cantidad in sorted(venta.consumo_ingredientes().items()):
        falta = cantidad
        capas = list(_orden_capas(
            Compra.objects.select_for_update()
            .filter(ingrediente_id=ingrediente_id, saldo_receta__gt=0,
                    fecha__lte=venta.fecha)))
        tocadas = []
        for capa in capas:
            if falta <= 0:
                break
            toma = min(falta, capa.saldo_receta)
            if toma <= 0:
                continue
            # El precio de la capa está congelado desde que se compró: no se
            # deriva del catálogo vivo, que puede cambiar.
            unitario = capa.costo_unitario_capa
            importe = toma * unitario
            consumos.append(ConsumoCapa(
                venta=venta, compra=capa, ingrediente_id=ingrediente_id,
                cantidad_receta=toma, costo_unitario=unitario, importe=importe))
            capa.saldo_receta -= toma
            tocadas.append(capa)
            total += importe
            falta -= toma
        if tocadas:
            Compra.objects.bulk_update(tocadas, ["saldo_receta"])

        if falta > 0:
            # Sin capa que lo respalde: se deja constancia con importe cero en
            # vez de costearlo con el catálogo.
            incompleto = True
            consumos.append(ConsumoCapa(
                venta=venta, compra=None, ingrediente_id=ingrediente_id,
                cantidad_receta=falta, costo_unitario=CERO, importe=CERO))

    ConsumoCapa.objects.bulk_create(consumos)

    venta.costo_fifo = _redondea(total)
    venta.costo_incompleto = incompleto
    venta.costeada_en = timezone.now()
    venta.save(update_fields=["costo_fifo", "costo_incompleto", "costeada_en"])

    if sincronizar:
        _resincroniza(venta)
    return venta.costo_fifo


def _resincroniza(venta):
    """Regenera el asiento de la venta con el costo que acaba de quedar.

    Sin esto el costo cambia y el asiento conserva el anterior, que es justo el
    desajuste que este módulo viene a eliminar.
    """
    from contabilidad import posting
    posting.sincronizar_venta(venta)


# ══════════════════════════════════════════════════════════════════════════════
#  MERMA
# ══════════════════════════════════════════════════════════════════════════════

@transaction.atomic
def descostear_ajuste(ajuste):
    """Deshace una merma y le devuelve a cada capa lo que le tomó.

    Es la misma mitad que `descostear_venta` y por la misma razón: sin
    devolver el saldo, borrar o recostear una merma quema inventario en
    silencio.
    """
    devoluciones = {}
    for compra_id, cantidad in (ConsumoCapa.objects
                                .filter(ajuste=ajuste, compra__isnull=False)
                                .values_list("compra_id", "cantidad_receta")):
        devoluciones[compra_id] = devoluciones.get(compra_id, CERO) + cantidad

    if devoluciones:
        capas = list(_orden_capas(
            Compra.objects.select_for_update().filter(pk__in=devoluciones)))
        for capa in capas:
            capa.saldo_receta = (capa.saldo_receta or CERO) + devoluciones[capa.pk]
        Compra.objects.bulk_update(capas, ["saldo_receta"])

    ConsumoCapa.objects.filter(ajuste=ajuste).delete()
    type(ajuste).objects.filter(pk=ajuste.pk).update(
        costo=None, costo_incompleto=False)
    ajuste.costo = None
    ajuste.costo_incompleto = False


@transaction.atomic
def costear_ajuste(ajuste, sincronizar=True):
    """Saca la merma del inventario consumiendo capas por FIFO.

    Se cuesta con las mismas reglas que una venta, y no por casualidad: lo que
    se perdió costó lo que costó comprarlo. Las tres reglas valen igual —no se
    inventa costo si faltan capas, y se redondea una sola vez al escribir—.

    Idempotente: descuesta antes de volver a consumir.
    """
    descostear_ajuste(ajuste)

    falta = ajuste.merma
    if falta <= 0:
        # Cuadró o sobró: no sale nada del inventario. Un sobrante NO se da de
        # alta —habría que inventarle un precio a mercancía que nunca se
        # compró—; se queda como constancia de que falta capturar una compra.
        if sincronizar:
            _resincroniza_ajuste(ajuste)
        return CERO

    total = CERO
    incompleto = False
    consumos = []
    capas = list(_orden_capas(
        Compra.objects.select_for_update()
        .filter(ingrediente_id=ajuste.ingrediente_id, saldo_receta__gt=0,
                fecha__lte=ajuste.fecha)))
    tocadas = []
    for capa in capas:
        if falta <= 0:
            break
        toma = min(falta, capa.saldo_receta)
        if toma <= 0:
            continue
        unitario = capa.costo_unitario_capa
        consumos.append(ConsumoCapa(
            ajuste=ajuste, compra=capa, ingrediente_id=ajuste.ingrediente_id,
            cantidad_receta=toma, costo_unitario=unitario,
            importe=toma * unitario))
        capa.saldo_receta -= toma
        tocadas.append(capa)
        total += toma * unitario
        falta -= toma
    if tocadas:
        Compra.objects.bulk_update(tocadas, ["saldo_receta"])

    if falta > 0:
        # Se perdió más de lo que ninguna compra respalda. Pasa cuando faltan
        # facturas por capturar, y se deja constancia con importe cero en vez
        # de costearlo con el catálogo.
        incompleto = True
        consumos.append(ConsumoCapa(
            ajuste=ajuste, compra=None, ingrediente_id=ajuste.ingrediente_id,
            cantidad_receta=falta, costo_unitario=CERO, importe=CERO))

    ConsumoCapa.objects.bulk_create(consumos)

    ajuste.costo = _redondea(total)
    ajuste.costo_incompleto = incompleto
    ajuste.save(update_fields=["costo", "costo_incompleto"])

    if sincronizar:
        _resincroniza_ajuste(ajuste)
    return ajuste.costo


def _resincroniza_ajuste(ajuste):
    from contabilidad import posting
    posting.sincronizar_ajuste(ajuste)


def ventas_a_recostear(ingrediente_id, desde):
    """Ventas que hay que rehacer cuando entra una capa nueva con fecha `desde`.

    Son las de esa fecha en adelante que involucran ese ingrediente, por
    cualquiera de las cuatro vías. Incluye las que aún no tienen costo: una
    venta que nació en el admin, o cuyo costeo falló, tiene que poder
    recuperarse cuando llegue la compra que le faltaba.

    Las cuatro vías se enumeran a propósito en vez de traer «todas las
    incompletas»: eso arrastraría cada venta sin respaldo de toda la historia
    en cada compra, tenga o no que ver con este ingrediente.
    """
    return (Venta.objects
            .filter(fecha__gte=desde)
            .filter(Q(consumos__ingrediente_id=ingrediente_id) |
                    Q(receta__ingredientes__ingrediente_id=ingrediente_id) |
                    Q(sustituciones__ingrediente_nuevo_id=ingrediente_id) |
                    Q(extras__extra__ingrediente_id=ingrediente_id))
            .distinct()
            .order_by("fecha", "id"))


@transaction.atomic
def _replay(ventas):
    """Descuesta y vuelve a costear, en una sola transacción.

    Todo junto y en orden: si se corta a la mitad, no puede quedar inventario
    devuelto con su costo todavía contabilizado.
    """
    ventas = list(ventas)
    for venta in ventas:
        descostear_venta(venta)
    for venta in ventas:
        costear_venta(venta)
    return len(ventas)


def mermas_a_recostear(ingrediente_id, desde):
    """Mermas de ese ingrediente que una capa nueva con fecha `desde` altera."""
    from .models import AjusteInventario
    return (AjusteInventario.objects
            .filter(ingrediente_id=ingrediente_id, fecha__gte=desde)
            .order_by("fecha", "id"))


@transaction.atomic
def _replay_mermas(ajustes):
    ajustes = list(ajustes)
    for ajuste in ajustes:
        descostear_ajuste(ajuste)
    for ajuste in ajustes:
        costear_ajuste(ajuste)
    return len(ajustes)


def recostear_desde(ingrediente_id, desde):
    """Rehace el costeo de lo que una capa nueva (o retirada) puede alterar.

    Las mermas van primero y por la misma razón que existen las dos mitades del
    costeo: una merma incompleta —se perdió mercancía que ninguna compra
    respaldaba— tiene que poder recuperarse cuando llegue la factura que
    faltaba, igual que una venta. Si solo se recostearan las ventas, esa merma
    se quedaría para siempre sin costo y su gasto nunca entraría al libro.
    """
    _replay_mermas(mermas_a_recostear(ingrediente_id, desde))
    return _replay(ventas_a_recostear(ingrediente_id, desde))


def abrir_capas_huerfanas():
    """Reabre compras que nacieron sin saldo, para que el FIFO las vea.

    Pasa en la ventana entre migrar y desplegar: el código viejo inserta
    compras sin saldo y el filtro `saldo_receta__gt=0` las volvería invisibles
    para siempre.
    """
    huerfanas = list(Compra.objects.filter(saldo_receta__isnull=True)
                     .select_related("ingrediente"))
    for compra in huerfanas:
        if compra.cantidad_receta is None:
            compra.cantidad_receta = (
                (compra.cantidad or CERO) * compra.ingrediente.cantidad_por_unidad)
        compra.saldo_receta = compra.cantidad_receta
    if huerfanas:
        Compra.objects.bulk_update(huerfanas, ["cantidad_receta", "saldo_receta"])
    return len(huerfanas)


def recostear_todo(desde=None):
    """Rehace el costeo completo, en orden canónico. Para el comando y las pruebas."""
    from .models import AjusteInventario

    abrir_capas_huerfanas()
    # Las mermas primero: consumen las mismas capas y su fecha manda igual que
    # la de una venta. Dejarlas fuera haría que `recostear --todo` devolviera
    # su saldo a las capas y no lo volviera a tomar, inflando el inventario.
    mermas = AjusteInventario.objects.all()
    ventas = Venta.objects.all()
    if desde:
        mermas = mermas.filter(fecha__gte=desde)
        ventas = ventas.filter(fecha__gte=desde)
    _replay_mermas(mermas.order_by("fecha", "id"))
    return _replay(ventas.order_by("fecha", "id"))


def recostear_pendientes():
    """Solo las que quedaron sin costear o incompletas."""
    abrir_capas_huerfanas()
    return _replay(Venta.objects.sin_costo_completo().order_by("fecha", "id"))


def diagnostico():
    """Qué tan sano está el costeo. Lo usa el comando y el panel.

    Tres consultas y no seis: cada cifra por separado eran seis viajes al
    pooler cada vez que el dueño abre el panel de contabilidad, y todas las
    de una tabla caben en un agregado.
    """
    from django.db.models import Count, Q, Sum

    ventas = Venta.objects.aggregate(
        total=Count("pk"),
        sin_costear=Count("pk", filter=Q(costo_fifo__isnull=True)),
        incompletas=Count("pk", filter=Q(costo_incompleto=True)))
    capas = Compra.objects.aggregate(
        con_saldo=Count("pk", filter=Q(saldo_receta__gt=0)),
        # Capas que el FIFO no puede ver: nacieron sin saldo y quedarían
        # invisibles para siempre si nadie las reabre. Desde P11 la columna es
        # NOT NULL, así que esto ya no puede pasar por el ORM; se sigue
        # contando porque es la única red bajo un `UPDATE` a mano o una
        # migración que lo deshaga, y cuesta una línea del mismo agregado.
        sin_abrir=Count("pk", filter=Q(saldo_receta__isnull=True)))
    faltante = (ConsumoCapa.objects.filter(compra__isnull=True)
                .aggregate(t=Sum("cantidad_receta"))["t"] or CERO)

    return {
        "ventas": ventas["total"],
        "sin_costear": ventas["sin_costear"],
        "incompletas": ventas["incompletas"],
        "capas_con_saldo": capas["con_saldo"],
        "capas_sin_abrir": capas["sin_abrir"],
        "faltante_sin_capa": faltante,
    }
