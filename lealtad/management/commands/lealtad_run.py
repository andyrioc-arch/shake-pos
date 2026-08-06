"""Latido del programa: caduca puntos, evalúa automatizaciones y manda mensajes.

Pensado para correr cada 10 minutos desde cron. Es idempotente: correrlo de más
no duplica mensajes (cada automatización respeta su 'no repetir en N días').

    */10 * * * * cd /ruta/al/proyecto && .venv/bin/python manage.py lealtad_run
"""

from django.core.management.base import BaseCommand
from django.utils import timezone

from lealtad import automatizaciones, mensajeria, servicios
from lealtad.models import Campana, Canje


class Command(BaseCommand):
    help = "Corre las automatizaciones y despacha la bandeja de mensajes."

    def add_arguments(self, parser):
        parser.add_argument("--solo-encolar", action="store_true",
                            help="Evalúa las reglas pero no envía nada.")
        parser.add_argument("--forzar-horario", action="store_true",
                            help="Envía aunque sea fuera del horario permitido.")
        parser.add_argument("--limite", type=int, default=None,
                            help="Cuántos mensajes despachar como máximo "
                                 "(por omisión, los que trae la bandeja).")

    def handle(self, *args, **opts):
        hoy = timezone.localdate()

        expirados = servicios.expirar_puntos(hoy)
        if expirados:
            self.stdout.write(f"⏳ Caducaron {expirados} puntos.")

        vencidos = Canje.objects.filter(estado=Canje.Estado.PENDIENTE,
                                        vence_el__lt=hoy).update(
            estado=Canje.Estado.EXPIRADO)
        if vencidos:
            self.stdout.write(f"🎟  {vencidos} canje(s) sin reclamar expiraron.")

        encolados = automatizaciones.correr_programadas(hoy)
        for nombre, cuantos in encolados.items():
            self.stdout.write(f"📨 {nombre}: {cuantos} mensaje(s) en cola.")

        pendientes = Campana.objects.filter(
            estado=Campana.Estado.PROGRAMADA,
            programada_para__lte=timezone.now())
        for campana in pendientes:
            cuantos = automatizaciones.enviar_campana(campana)
            self.stdout.write(f"📢 Campaña «{campana.nombre}»: {cuantos} mensaje(s).")

        if opts["solo_encolar"]:
            self.stdout.write(self.style.WARNING("Sin enviar (--solo-encolar)."))
            return

        despacho = {"forzar_horario": opts["forzar_horario"]}
        if opts["limite"] is not None:
            despacho["limite"] = opts["limite"]
        enviados, fallidos, pospuestos = mensajeria.despachar_pendientes(
            **despacho)
        ligados = mensajeria.atribuir_compras()

        resumen = f"✔ {enviados} enviado(s)"
        if fallidos:
            resumen += f", {fallidos} fallido(s)"
        if pospuestos:
            resumen += f", {pospuestos} para reintentar"
        if ligados:
            resumen += f" · {ligados} venta(s) atribuida(s)"
        self.stdout.write(self.style.SUCCESS(resumen))
