from django.db.models.signals import post_save, post_delete
from django.dispatch import receiver

from inventario.models import Venta, Compra, VentaExtra, VentaSustitucion
from .models import Asiento, Movimiento
from . import posting


# ── Puente automático: ventas y compras de inventario → contabilidad ───────────
@receiver(post_save, sender=Venta)
def venta_inventario_guardada(sender, instance, **kwargs):
    posting.sincronizar_venta(instance)


@receiver(post_save, sender=Compra)
def compra_inventario_guardada(sender, instance, **kwargs):
    posting.sincronizar_compra(instance)


def _resync_venta(venta_id):
    """Re-sincroniza el movimiento de una venta si la venta aún existe."""
    venta = Venta.objects.filter(pk=venta_id).first()
    if venta:
        posting.sincronizar_venta(venta)


@receiver(post_save, sender=VentaExtra)
@receiver(post_delete, sender=VentaExtra)
def venta_extra_cambio(sender, instance, **kwargs):
    _resync_venta(instance.venta_id)


@receiver(post_save, sender=VentaSustitucion)
@receiver(post_delete, sender=VentaSustitucion)
def venta_sustitucion_cambio(sender, instance, **kwargs):
    _resync_venta(instance.venta_id)


@receiver(post_delete, sender=Movimiento)
def movimiento_borrado(sender, instance, **kwargs):
    Asiento.objects.filter(
        referencia__in=[f"Mov #{instance.pk} flujo",
                        f"Mov #{instance.pk} reconocimiento"],
        automatico=True,
    ).delete()
