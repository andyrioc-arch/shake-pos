"""Mantiene el costeo al día pase lo que pase con las ventas y las compras.

La regla es simple: si algo cambia lo que una venta consumió, esa venta se
recuesta, y también las que vinieron después.

El «y las que vinieron después» es la parte que se olvida. Con FIFO, borrar una
venta libera las capas que se había llevado, y esas capas eran las más viejas:
la venta siguiente debía haberlas consumido a ellas y no a las que le tocaron.
Sin recostear hacia adelante, el costo de todas las posteriores queda mal.
"""
from django.db.models.signals import post_delete, post_save, pre_delete
from django.dispatch import receiver

from . import costeo
from .models import AjusteInventario, Compra, Venta, VentaExtra, VentaSustitucion

# Campos que escribe el propio costeo. Guardarlos no debe volver a disparar el
# costeo: sin esta guarda, cada venta se costearía en bucle.
CAMPOS_DE_COSTEO = {"costo_fifo", "costo_incompleto", "costeada_en"}

# Lo que la venta consumía justo antes de borrarse, para saber qué recostear
# después. No se puede leer en post_delete: para entonces ya no existe.
_pendiente = {}


def _ingredientes_y_fecha(venta):
    ids = set(venta.consumos.values_list("ingrediente_id", flat=True))
    return ids or set(venta.consumo_ingredientes().keys()), venta.fecha


def _recostear_posteriores(ingrediente_ids, fecha):
    for ingrediente_id in sorted(ingrediente_ids):
        costeo.recostear_desde(ingrediente_id, fecha)


def _es_guardado_del_costeo(update_fields):
    return update_fields is not None and set(update_fields) <= CAMPOS_DE_COSTEO


# ── Ventas ────────────────────────────────────────────────────────────────────
@receiver(pre_delete, sender=Venta)
def venta_por_borrarse(sender, instance, **kwargs):
    """Devuelve el inventario ANTES de que la cascada borre el rastro.

    Tiene que ser `pre_delete`: `ConsumoCapa` cuelga de la venta en cascada, así
    que en `post_delete` ya no habría forma de saber a qué compras devolverle su
    saldo.
    """
    _pendiente[instance.pk] = _ingredientes_y_fecha(instance)
    costeo.descostear_venta(instance)


@receiver(post_delete, sender=Venta)
def venta_borrada(sender, instance, **kwargs):
    ingredientes, fecha = _pendiente.pop(instance.pk, (set(), instance.fecha))
    _recostear_posteriores(ingredientes, fecha)


@receiver(post_save, sender=Venta)
def venta_guardada(sender, instance, created, update_fields=None, **kwargs):
    """Una venta nueva o editada se cuesta sola.

    Cubre el admin, el shell y cualquier importación: la caja ya lo hace por su
    cuenta, pero no puede ser el único camino que deje el costo bien.
    """
    if _es_guardado_del_costeo(update_fields):
        return
    costeo.costear_venta(instance)
    ingredientes, fecha = _ingredientes_y_fecha(instance)
    _recostear_posteriores(ingredientes, fecha)


# ── Modificaciones de una venta (extras y sustituciones) ──────────────────────
def _recostear_venta_de(objeto):
    # Si la venta entera se está borrando, no hay nada que recostear: sus
    # propias señales ya devolvieron el inventario.
    #
    # Preguntar si la venta EXISTE no sirve como guarda, y ese fue el bug.
    # Django manda todos los `pre_delete` primero, luego borra, y hasta el
    # final los `post_delete`; los hijos se borran antes que el padre, así que
    # cuando llega el `post_delete` de un extra la venta todavía está en la
    # base. La guarda dejaba pasar, se volvía a costear, y los `ConsumoCapa`
    # recién insertados quedaban colgando de una venta que desaparecía un
    # instante después. En Postgres las llaves foráneas son DEFERRABLE, así que
    # el error salía hasta el COMMIT y la pantalla solo decía «error 500».
    #
    # `_pendiente` sí lo sabe: lo llenó el `pre_delete` de la venta, que corre
    # antes que cualquier `post_delete`.
    if objeto.venta_id in _pendiente:
        return
    venta = Venta.objects.filter(pk=objeto.venta_id).first()
    if not venta:          # ya no está: nada que recostear
        return
    costeo.costear_venta(venta)
    ingredientes, fecha = _ingredientes_y_fecha(venta)
    _recostear_posteriores(ingredientes, fecha)


@receiver(post_save, sender=VentaExtra)
@receiver(post_save, sender=VentaSustitucion)
def modificacion_guardada(sender, instance, **kwargs):
    _recostear_venta_de(instance)


@receiver(post_delete, sender=VentaExtra)
@receiver(post_delete, sender=VentaSustitucion)
def modificacion_borrada(sender, instance, **kwargs):
    _recostear_venta_de(instance)


# ── Compras ───────────────────────────────────────────────────────────────────
@receiver(post_save, sender=Compra)
def compra_guardada(sender, instance, update_fields=None, **kwargs):
    """Una capa nueva puede surtir a ventas que se quedaron sin costo.

    Es el caso normal del mostrador: se vende en la mañana y la factura del
    proveedor se captura en la tarde.
    """
    if update_fields is not None and set(update_fields) <= {"saldo_receta"}:
        return          # lo escribió el propio costeo; no reentrar
    costeo.recostear_desde(instance.ingrediente_id, instance.fecha)


@receiver(pre_delete, sender=Compra)
def compra_por_borrarse(sender, instance, **kwargs):
    """Anota a quién surtía esta compra, antes de que la cascada borre el rastro.

    En `post_delete` los `ConsumoCapa` ya no existen, así que las ventas que la
    usaban serían imposibles de encontrar: se quedarían con un costo que ya no
    respalda nada.
    """
    _pendiente[("compra", instance.pk)] = list(
        instance.consumos.values_list("venta_id", flat=True))


@receiver(post_delete, sender=Compra)
def compra_borrada(sender, instance, **kwargs):
    """Borrar una compra deja sin respaldo lo que había surtido.

    Las ventas que la usaban se recuestan: unas encontrarán otra capa y otras
    quedarán incompletas, que es la verdad.
    """
    afectadas = _pendiente.pop(("compra", instance.pk), [])
    for venta in Venta.objects.filter(pk__in=afectadas).order_by("fecha", "id"):
        costeo.costear_venta(venta)
    costeo.recostear_desde(instance.ingrediente_id, instance.fecha)


# ── Mermas ────────────────────────────────────────────────────────────────────
# Campos que escribe el propio costeo de la merma. Igual que con las ventas:
# guardarlos no debe volver a disparar el costeo.
CAMPOS_DE_COSTEO_MERMA = {"costo", "costo_incompleto"}


@receiver(post_save, sender=AjusteInventario)
def ajuste_guardado(sender, instance, update_fields=None, **kwargs):
    """Una merma nueva o editada sale del inventario sola.

    Vale desde el panel, desde el admin y desde el shell, como el costeo de
    las ventas: la pantalla no puede ser el único camino que deje el
    inventario bien.
    """
    if update_fields is not None and set(update_fields) <= CAMPOS_DE_COSTEO_MERMA:
        return
    costeo.costear_ajuste(instance)


@receiver(pre_delete, sender=AjusteInventario)
def ajuste_por_borrarse(sender, instance, **kwargs):
    """Devuelve el inventario ANTES de que la cascada borre el rastro.

    Tiene que ser `pre_delete` por lo mismo que en las ventas: `ConsumoCapa`
    cuelga en cascada, y en `post_delete` ya no habría a qué capa devolverle
    su saldo.
    """
    costeo.descostear_ajuste(instance)
    _pendiente[("ajuste", instance.pk)] = (instance.ingrediente_id, instance.fecha)


@receiver(post_delete, sender=AjusteInventario)
def ajuste_borrado(sender, instance, **kwargs):
    """Al liberar capas, las ventas posteriores pueden costar distinto."""
    ingrediente_id, fecha = _pendiente.pop(
        ("ajuste", instance.pk), (instance.ingrediente_id, instance.fecha))
    costeo.recostear_desde(ingrediente_id, fecha)
