"""Carga el programa de lealtad con los niveles, premios y mensajes base.

Todo lo que deja este comando es editable después desde el panel. Solo es un
punto de partida sensato para no arrancar con pantallas vacías.
"""

from decimal import Decimal

from django.core.management.base import BaseCommand

from inventario.models import Receta
from lealtad.models import (
    Automatizacion, Nivel, Plantilla, Premio, ConfiguracionPrograma,
)

NIVELES = [
    ("Inicio", "🌱", 0, "Acceso al programa y promociones exclusivas.", "#1e9aff"),
    ("Fan", "💛", 100, "Un latte gratis al llegar a 100 puntos.", "#ffd900"),
    ("Power", "🧡", 200, "Smoothie al 50% y promociones anticipadas.", "#ff7300"),
    ("Elite", "💗", 350, "Un smoothie gratis.", "#fa5598"),
    ("VIP", "💜", 500, "Premio a elegir y acceso anticipado a lanzamientos.", "#8700ff"),
]

# (nombre, emoji, puntos, tipo, descripción, pista para encontrar la receta,
#  cantidad, valor)
PREMIOS = [
    ("Latte gratis", "☕", 100, Premio.Tipo.PRODUCTO,
     "Un latte de la casa, por cuenta nuestra.", "latte", 1, "0"),
    ("Smoothie al 50%", "🥤", 200, Premio.Tipo.DESCUENTO_PCT,
     "Mitad de precio en el smoothie de proteína que elijas.", "smoothie", 1, "50"),
    ("Smoothie gratis", "🎉", 350, Premio.Tipo.PRODUCTO,
     "Un smoothie de proteína completamente gratis.", "smoothie", 1, "0"),
    ("Premio VIP a elegir", "👑", 500, Premio.Tipo.COMBO,
     "Elige: 1 smoothie gratis, 2 lattes gratis o el combo del mes.",
     "smoothie", 1, "0"),
]

PLANTILLAS = [
    ("bienvenida", "Bienvenida",
     "¡Bienvenido a {negocio}! 🎉 Ya formas parte de nuestro programa de "
     "lealtad. Acumula puntos en cada compra y obtén recompensas exclusivas.\n"
     "Tu tarjeta: {link}"),
    ("compra", "Compra registrada",
     "Tu compra de ${monto} quedó registrada. Ganaste {puntos} puntos ⭐ "
     "Tu saldo actual es {saldo} puntos.\n{link}"),
    ("primera-compra", "Primera compra",
     "¡Gracias por tu primera compra, {nombre}! Ganaste {puntos} puntos. "
     "Sigue acumulando para obtener recompensas 🥤"),
    ("premio-disponible", "Premio disponible",
     "¡Felicidades {nombre}! 🎁 Ya puedes canjear: {premio}. "
     "Pídelo en tu próxima visita."),
    ("cerca-premio", "Cerca de un premio",
     "{nombre}, te faltan solo {faltan} puntos para obtener {premio}. "
     "¡Ya casi! 💪"),
    ("inactividad", "Te extrañamos",
     "Te extrañamos, {nombre} 💗 Regresa esta semana y recibe puntos dobles "
     "en tu compra."),
    ("cumpleanos", "Cumpleaños",
     "¡Tu mes de cumpleaños llegó, {nombre}! 🎂 Pasa por una bebida especial "
     "de nuestra parte."),
    ("nivel-vip", "Cliente VIP",
     "Ahora eres cliente VIP 👑 Tendrás acceso anticipado a promociones "
     "especiales. ¡Gracias por tu preferencia!"),
    ("puntos-por-expirar", "Puntos por caducar",
     "{nombre}, tienes {puntos} puntos que caducan en {dias} días. "
     "Aprovéchalos en tu próxima visita ⏳"),
]

# (nombre, disparador, clave de plantilla, parámetros)
AUTOMATIZACIONES = [
    ("Bienvenida al registrarse", Automatizacion.Disparador.BIENVENIDA,
     "bienvenida", dict(no_repetir_dias=0, max_por_cliente=1)),
    ("Aviso de compra y puntos", Automatizacion.Disparador.COMPRA,
     "compra", dict(no_repetir_dias=0)),
    ("Gracias por tu primera compra", Automatizacion.Disparador.PRIMERA_COMPRA,
     "primera-compra", dict(no_repetir_dias=0, max_por_cliente=1)),
    ("Ya puedes canjear un premio", Automatizacion.Disparador.PREMIO_DISPONIBLE,
     "premio-disponible", dict(no_repetir_dias=15)),
    ("Cerca de tu premio (80%)", Automatizacion.Disparador.CERCA_PREMIO,
     "cerca-premio", dict(umbral_porcentaje=80, hora_envio=12,
                          no_repetir_dias=15)),
    ("Recuperar clientes inactivos", Automatizacion.Disparador.INACTIVIDAD,
     "inactividad", dict(dias=30, hora_envio=11, no_repetir_dias=30)),
    ("Felicitación de cumpleaños", Automatizacion.Disparador.CUMPLEANOS,
     "cumpleanos", dict(dias=7, hora_envio=10, no_repetir_dias=300)),
    ("Bienvenida a VIP", Automatizacion.Disparador.NIVEL,
     "nivel-vip", dict(no_repetir_dias=0, max_por_cliente=1)),
    ("Tus puntos están por caducar", Automatizacion.Disparador.POR_EXPIRAR,
     "puntos-por-expirar", dict(dias=15, hora_envio=11, no_repetir_dias=30)),
]


def _busca_receta(pista):
    """Encuentra la receta cuyo nombre se parezca a la pista (latte, smoothie)."""
    receta = Receta.objects.filter(nombre__icontains=pista).first()
    if receta or pista != "smoothie":
        return receta
    # El catálogo de este negocio llama "shakes" a los smoothies de proteína.
    return Receta.objects.filter(activa=True).order_by("-precio_venta").first()


class Command(BaseCommand):
    help = "Carga niveles, premios, plantillas y automatizaciones del programa."

    def add_arguments(self, parser):
        parser.add_argument("--reset", action="store_true",
                            help="Borra la configuración previa del programa "
                                 "(no toca clientes ni puntos).")

    def handle(self, *args, **opts):
        if opts["reset"]:
            Automatizacion.objects.all().delete()
            Plantilla.objects.all().delete()
            Premio.objects.all().delete()
            Nivel.objects.all().delete()
            self.stdout.write("Configuración previa del programa borrada.")

        cfg = ConfiguracionPrograma.get()

        niveles = {}
        for nombre, emoji, puntos, beneficios, color in NIVELES:
            nivel, _ = Nivel.objects.update_or_create(
                nombre=nombre,
                defaults=dict(emoji=emoji, puntos_requeridos=puntos,
                              beneficios=beneficios, color=color, activo=True),
            )
            niveles[nombre] = nivel

        sin_receta = []
        for nombre, emoji, puntos, tipo, desc, pista, cant, valor in PREMIOS:
            receta = _busca_receta(pista)
            if not receta:
                sin_receta.append(nombre)
            Premio.objects.update_or_create(
                nombre=nombre,
                defaults=dict(emoji=emoji, puntos_requeridos=puntos, tipo=tipo,
                              descripcion=desc, receta=receta, cantidad=cant,
                              valor=Decimal(valor), activo=True,
                              orden=puntos // 100),
            )

        plantillas = {}
        for clave, nombre, cuerpo in PLANTILLAS:
            plantilla, _ = Plantilla.objects.update_or_create(
                clave=clave,
                defaults=dict(nombre=nombre, cuerpo=cuerpo, activa=True),
            )
            plantillas[clave] = plantilla

        for nombre, disparador, clave, extra in AUTOMATIZACIONES:
            if disparador == Automatizacion.Disparador.NIVEL:
                extra = {**extra, "nivel_objetivo": niveles.get("VIP")}
            Automatizacion.objects.update_or_create(
                nombre=nombre,
                defaults=dict(disparador=disparador,
                              plantilla=plantillas[clave], activa=True, **extra),
            )

        self.stdout.write(self.style.SUCCESS(
            f"✔ Programa cargado: {len(NIVELES)} niveles, {len(PREMIOS)} premios, "
            f"{len(PLANTILLAS)} plantillas y {len(AUTOMATIZACIONES)} "
            f"automatizaciones activas."))
        self.stdout.write(
            f"  Regla de puntos: $1 punto por cada ${cfg.pesos_por_punto:,.0f} · "
            f"vigencia {cfg.vigencia_puntos_meses} meses · "
            f"envío en modo {cfg.get_proveedor_mensajes_display()}.")
        if sin_receta:
            self.stdout.write(self.style.WARNING(
                "  ⚠ Sin producto ligado (liga la receta en el panel de premios "
                "para ver costo y margen): " + ", ".join(sin_receta)))
