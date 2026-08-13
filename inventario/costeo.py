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


def recostear_desde(ingrediente_id, desde):
    """Rehace el costeo de lo que una capa nueva (o retirada) puede alterar."""
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
    abrir_capas_huerfanas()
    ventas = Venta.objects.all()
    if desde:
        ventas = ventas.filter(fecha__gte=desde)
    return _replay(ventas.order_by("fecha", "id"))


def recostear_pendientes():
    """Solo las que quedaron sin costear o incompletas."""
    abrir_capas_huerfanas()
    return _replay(Venta.objects.sin_costo_completo().order_by("fecha", "id"))


def diagnostico():
    """Qué tan sano está el costeo. Lo usa el comando y el panel."""
    from django.db.models import Sum
    return {
        "ventas": Venta.objects.count(),
        "sin_costear": Venta.objects.filter(costo_fifo__isnull=True).count(),
        "incompletas": Venta.objects.filter(costo_incompleto=True).count(),
        "capas_con_saldo": Compra.objects.filter(saldo_receta__gt=0).count(),
        # Capas que el FIFO no puede ver: nacieron sin saldo y quedarían
        # invisibles para siempre si nadie las reabre.
        "capas_sin_abrir": Compra.objects.filter(saldo_receta__isnull=True).count(),
        "faltante_sin_capa": (
            ConsumoCapa.objects.filter(compra__isnull=True)
            .aggregate(t=Sum("cantidad_receta"))["t"] or CERO),
    }
