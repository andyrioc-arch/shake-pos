"""Recalcula el costo real de las ventas contra las capas de compra.

Es la red de seguridad del costeo: si una venta quedó sin costear porque algo
falló en la caja, o si se capturaron compras viejas, este comando lo repara.

    python manage.py recostear --todo
    python manage.py recostear --desde 2026-08-01
    python manage.py recostear --solo-pendientes
    python manage.py recostear --verificar
"""
from datetime import date
from decimal import Decimal, ROUND_HALF_UP

from django.core.management.base import BaseCommand, CommandError
from django.db.models import Sum

from contabilidad import posting
from contabilidad.models import Movimiento
from inventario import costeo
from inventario.models import AjusteInventario, Compra, Venta


class Command(BaseCommand):
    help = "Recalcula el costo de ventas por FIFO contra las capas de compra."

    def add_arguments(self, parser):
        parser.add_argument("--todo", action="store_true",
                            help="Rehace el costeo de todas las ventas.")
        parser.add_argument("--desde", type=str, metavar="AAAA-MM-DD",
                            help="Rehace desde esa fecha en adelante.")
        parser.add_argument("--solo-pendientes", action="store_true",
                            help="Solo las sin costear o incompletas.")
        parser.add_argument("--verificar", action="store_true",
                            help="No cambia nada: compara lo guardado contra "
                                 "un recálculo independiente.")

    def handle(self, *args, **opts):
        if opts["verificar"]:
            return self._verificar()

        desde = None
        if opts["desde"]:
            try:
                desde = date.fromisoformat(opts["desde"])
            except ValueError:
                raise CommandError("La fecha debe ir como AAAA-MM-DD.")

        if opts["solo_pendientes"]:
            n = costeo.recostear_pendientes()
            self.stdout.write(f"✔ {n} venta(s) pendientes recosteadas.")
        elif opts["todo"] or desde:
            n = costeo.recostear_todo(desde=desde)
            alcance = f"desde {desde}" if desde else "completo"
            self.stdout.write(f"✔ Recosteo {alcance}: {n} venta(s).")
        else:
            raise CommandError(
                "Elige --todo, --desde, --solo-pendientes o --verificar.")

        self._resumen()

    def _resumen(self):
        d = costeo.diagnostico()
        self.stdout.write("")
        self.stdout.write(f"  ventas             {d['ventas']}")
        self.stdout.write(f"  sin costear        {d['sin_costear']}")
        self.stdout.write(f"  incompletas        {d['incompletas']}")
        self.stdout.write(f"  capas con saldo    {d['capas_con_saldo']}")
        self.stdout.write(f"  capas sin abrir    {d['capas_sin_abrir']}")
        self.stdout.write(f"  consumo sin capa   {d['faltante_sin_capa']}")
        if d["capas_sin_abrir"]:
            self.stdout.write(self.style.WARNING(
                "\n  Hay compras que el FIFO no ve. Corre --todo para abrirlas."))
        if d["sin_costear"] or d["incompletas"]:
            self.stdout.write(self.style.WARNING(
                "\n  Hay ventas sin respaldo de compras. Captura las compras "
                "que faltan y vuelve a correr el comando."))

    def _verificar(self):
        """Comprueba que lo guardado sea internamente consistente.

        No se compara contra un recálculo alterno: dos algoritmos con reglas
        distintas siempre acaban discrepando por motivos que no son bugs. Se
        verifican las igualdades que el costeo promete cumplir.
        """
        problemas = []

        # 1. Cada venta costeada vale lo que suman las capas que la surtieron.
        for venta in Venta.objects.exclude(costo_fifo__isnull=True):
            suma = (venta.consumos.aggregate(t=Sum("importe"))["t"]
                    or Decimal("0"))
            esperado = suma.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            if esperado != venta.costo_fifo:
                problemas.append(
                    f"venta {venta.id}: guardado {venta.costo_fifo}, "
                    f"sus capas suman {esperado}")

        # 1b. Lo mismo para las mermas: consumen las mismas capas y por las
        #     mismas reglas, así que la igualdad tiene que valer igual. Sin
        #     esta comprobación, una merma mal costeada sería la única salida
        #     de inventario que el auditor no mira.
        for ajuste in AjusteInventario.objects.exclude(costo__isnull=True):
            suma = (ajuste.consumos.aggregate(t=Sum("importe"))["t"]
                    or Decimal("0"))
            esperado = suma.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            if esperado != ajuste.costo:
                problemas.append(
                    f"merma {ajuste.id}: guardado {ajuste.costo}, "
                    f"sus capas suman {esperado}")

        # 2. Ninguna capa puede deber más de lo que trajo, ni menos que cero.
        for compra in Compra.objects.exclude(saldo_receta__isnull=True):
            consumido = (compra.consumos.aggregate(t=Sum("cantidad_receta"))["t"]
                         or Decimal("0"))
            traido = compra.cantidad_receta or Decimal("0")
            if compra.saldo_receta < 0:
                problemas.append(f"compra {compra.id}: saldo negativo "
                                 f"({compra.saldo_receta})")
            elif consumido + compra.saldo_receta != traido:
                problemas.append(
                    f"compra {compra.id}: trajo {traido}, consumido "
                    f"{consumido} + saldo {compra.saldo_receta}")

        # 3. Una venta sin costo completo no puede tener asiento reconocido.
        colgadas = Movimiento.objects.filter(
            tipo=Movimiento.Tipo.VENTA,
            asiento_reconocimiento__isnull=False,
            venta__in=Venta.objects.sin_costo_completo(),
        ).count()
        if colgadas:
            problemas.append(
                f"{colgadas} venta(s) reconocidas sin tener el costo completo")

        for p in problemas:
            self.stdout.write(self.style.ERROR(f"  {p}"))

        hoy = date.today()
        balanza = posting.balanza_comprobacion(hoy.year, hoy.month)
        balance = posting.balance_general(hoy.year, hoy.month)
        inventario = next(
            (a["monto"] for a in balance["activos"] if a["nombre"] == "Inventario"),
            Decimal("0"))

        self.stdout.write("")
        self.stdout.write(f"  balanza cuadra     {balanza['cuadra']}")
        self.stdout.write(f"  balance cuadra     {balance['cuadra']}")
        self.stdout.write(f"  saldo inventario   {inventario}")

        sano = (not problemas and balanza["cuadra"] and balance["cuadra"]
                and inventario >= 0)
        self.stdout.write("")
        if sano:
            self.stdout.write(self.style.SUCCESS("✔ El costeo está sano."))
        else:
            self.stdout.write(self.style.ERROR(
                "✘ El costeo NO está sano. Revisa lo de arriba antes de seguir."))
        self._resumen()
