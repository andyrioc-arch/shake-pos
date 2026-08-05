"""Carga los datos iniciales (ingredientes, empaque y las 6 recetas Habits)."""
from datetime import date
from decimal import Decimal
from django.core.management.base import BaseCommand
from inventario.models import (
    Ingrediente, Receta, RecetaIngrediente, Compra, Venta, Extra,
)

C = Ingrediente.Categoria

# nombre -> (categoria, unidad_compra, cant_por_unidad, unidad_receta, costo_compra)
INGREDIENTES = {
    "Leche de avena":         (C.BASE_LIQUIDA, "litro", 1000, "ml", "30.00"),
    "Leche de almendra":      (C.BASE_LIQUIDA, "litro", 1000, "ml", "55.00"),
    "Leche de coco":          (C.BASE_LIQUIDA, "litro", 1000, "ml", "65.00"),
    "Plátano congelado":      (C.FRUTA, "kg", 1000, "g", "28.00"),
    "Habits Cacao":           (C.PROTEINA, "kg", 1000, "g", "450.00"),
    "Habits Macchiatto":      (C.PROTEINA, "kg", 1000, "g", "450.00"),
    "Habits Vainilla":        (C.PROTEINA, "kg", 1000, "g", "450.00"),
    "Habits Maca Cacao":      (C.PROTEINA, "kg", 1000, "g", "450.00"),
    "Habits Matcha Vainilla": (C.PROTEINA, "kg", 1000, "g", "450.00"),
    "Cacao puro":             (C.SUPERFOOD, "kg", 1000, "g", "80.00"),
    "Canela":                 (C.SUPERFOOD, "kg", 1000, "g", "120.00"),
    "Sal marina":             (C.OTRO, "kg", 1000, "g", "20.00"),
    "Spread avellana cacao":  (C.SPREAD, "kg", 1000, "g", "280.00"),
    "Hielo":                  (C.OTRO, "kg", 1000, "g", "5.00"),
    "Blueberries":            (C.FRUTA, "kg", 1000, "g", "120.00"),
    "Espresso":               (C.BASE_LIQUIDA, "litro", 1000, "ml", "80.00"),
    "Creatina":               (C.SUPERFOOD, "kg", 1000, "g", "600.00"),
    "Dátil":                  (C.FRUTA, "kg", 1000, "g", "90.00"),
    "Crema de almendra":      (C.SPREAD, "kg", 1000, "g", "260.00"),
    "Piña congelada":         (C.FRUTA, "kg", 1000, "g", "35.00"),
    "Mango congelado":        (C.FRUTA, "kg", 1000, "g", "40.00"),
    "Jengibre fresco":        (C.SUPERFOOD, "kg", 1000, "g", "50.00"),
    "Cúrcuma":                (C.SUPERFOOD, "kg", 1000, "g", "150.00"),
    "Pimienta negra":         (C.SUPERFOOD, "kg", 1000, "g", "180.00"),
    "Spread matcha":          (C.SPREAD, "kg", 1000, "g", "350.00"),
    "Crema de maní":          (C.SPREAD, "kg", 1000, "g", "180.00"),
    "Avena":                  (C.OTRO, "kg", 1000, "g", "30.00"),
    "Fresa congelada":        (C.FRUTA, "kg", 1000, "g", "55.00"),
    "Kéfir":                  (C.BASE_LIQUIDA, "litro", 1000, "ml", "60.00"),
    "Linaza molida":          (C.SUPERFOOD, "kg", 1000, "g", "70.00"),
    "Papaya congelada":       (C.FRUTA, "kg", 1000, "g", "30.00"),
    "Chía":                   (C.SUPERFOOD, "kg", 1000, "g", "110.00"),
    # Empaque
    "Vaso 16 oz":             (C.EMPAQUE, "paquete", 50, "pieza", "175.00"),
    "Tapa":                   (C.EMPAQUE, "paquete", 50, "pieza", "90.00"),
    "Popote":                 (C.EMPAQUE, "paquete", 100, "pieza", "60.00"),
    "Sticker":                (C.EMPAQUE, "rollo", 100, "pieza", "120.00"),
}

EMPAQUE = [("Vaso 16 oz", 1), ("Tapa", 1), ("Popote", 1), ("Sticker", 1)]

# receta -> (emoji, perfil, precio, [(ingrediente, cantidad)])
RECETAS = {
    "Afterparty Shake": ("🍫", "Recovery · Chocolate oscuro", "95.00", [
        ("Leche de avena", 180), ("Plátano congelado", 90),
        ("Habits Cacao", 27), ("Cacao puro", 5), ("Canela", 1),
        ("Sal marina", 1), ("Spread avellana cacao", 15), ("Hielo", 90),
    ]),
    "Boost Shake": ("☕", "Focus · Mocha berry", "95.00", [
        ("Leche de almendra", 180), ("Habits Macchiatto", 27),
        ("Blueberries", 80), ("Espresso", 60), ("Dátil", 6),
        ("Crema de almendra", 15), ("Canela", 1), ("Hielo", 90),
    ]),
    "Chill Shake": ("🥭", "Anti-inflammatory · Tropical dorado", "90.00", [
        ("Leche de coco", 180), ("Habits Vainilla", 27),
        ("Piña congelada", 80), ("Mango congelado", 80),
        ("Jengibre fresco", 2), ("Cúrcuma", 1), ("Pimienta negra", 1),
        ("Spread matcha", 12), ("Hielo", 90),
    ]),
    "Disco Shake": ("🥜", "Sustained Energy · Peanut butter", "90.00", [
        ("Leche de avena", 180), ("Habits Maca Cacao", 27),
        ("Plátano congelado", 60), ("Avena", 20),
        ("Crema de maní", 17), ("Canela", 1), ("Hielo", 126),
    ]),
    "Girls Shake": ("🍓", "Balance · Strawberry pink", "95.00", [
        ("Leche de almendra", 180), ("Habits Maca Cacao", 27),
        ("Fresa congelada", 100), ("Kéfir", 60), ("Linaza molida", 8),
        ("Crema de almendra", 12), ("Canela", 1), ("Hielo", 90),
    ]),
    "Gud Shake": ("🥬", "Gut Health · Matcha green", "90.00", [
        ("Leche de almendra", 180), ("Habits Matcha Vainilla", 27),
        ("Papaya congelada", 100), ("Kéfir", 60), ("Chía", 9),
        ("Jengibre fresco", 2), ("Spread matcha", 10), ("Hielo", 90),
    ]),
}

COMPRAS = [
    (date(2025, 5, 1), "Leche de avena", 10, "30.00", "Bodega"),
    (date(2025, 5, 1), "Leche de almendra", 8, "55.00", "Supermercado"),
    (date(2025, 5, 1), "Leche de coco", 5, "65.00", "Supermercado"),
    (date(2025, 5, 2), "Plátano congelado", 5, "28.00", "Congelados"),
    (date(2025, 5, 2), "Habits Cacao", 2, "450.00", "Habits Official"),
    (date(2025, 5, 2), "Habits Macchiatto", 2, "450.00", "Habits Official"),
    (date(2025, 5, 2), "Habits Vainilla", 2, "450.00", "Habits Official"),
    (date(2025, 5, 2), "Habits Maca Cacao", 2, "450.00", "Habits Official"),
    (date(2025, 5, 2), "Habits Matcha Vainilla", 2, "450.00", "Habits Official"),
    (date(2025, 5, 3), "Fresa congelada", 4, "55.00", "Congelados"),
    (date(2025, 5, 3), "Mango congelado", 4, "40.00", "Congelados"),
    (date(2025, 5, 3), "Piña congelada", 4, "35.00", "Congelados"),
    (date(2025, 5, 3), "Papaya congelada", 4, "30.00", "Congelados"),
    (date(2025, 5, 4), "Kéfir", 4, "60.00", "Lácteos García"),
    (date(2025, 5, 4), "Vaso 16 oz", 5, "175.00", "Desechables MX"),
    (date(2025, 5, 4), "Tapa", 5, "90.00", "Desechables MX"),
    (date(2025, 5, 4), "Popote", 3, "60.00", "Desechables MX"),
    (date(2025, 5, 4), "Sticker", 2, "120.00", "Imprenta"),
]

VENTAS = [
    (date(2025, 5, 5), "Afterparty Shake", 5),
    (date(2025, 5, 5), "Boost Shake", 4),
    (date(2025, 5, 6), "Chill Shake", 6),
    (date(2025, 5, 6), "Disco Shake", 3),
    (date(2025, 5, 7), "Girls Shake", 7),
    (date(2025, 5, 7), "Gud Shake", 5),
]



class Command(BaseCommand):
    help = "Carga ingredientes, empaque, recetas, compras y ventas de ejemplo."

    def add_arguments(self, parser):
        parser.add_argument(
            "--reset", action="store_true",
            help="Borra los datos existentes antes de cargar.",
        )

    def handle(self, *args, **opts):
        if opts["reset"]:
            Venta.objects.all().delete()  # arrastra sus extras/sustituciones
            Compra.objects.all().delete()
            RecetaIngrediente.objects.all().delete()
            Receta.objects.all().delete()
            Extra.objects.all().delete()  # liberan los ingredientes que protegen
            Ingrediente.objects.all().delete()
            self.stdout.write("Datos previos borrados.")

        ing_obj = {}
        for nombre, (cat, uc, cpu, ur, costo) in INGREDIENTES.items():
            obj, _ = Ingrediente.objects.update_or_create(
                nombre=nombre,
                defaults=dict(
                    categoria=cat, unidad_compra=uc,
                    cantidad_por_unidad=Decimal(str(cpu)),
                    unidad_receta=ur, costo_unidad_compra=Decimal(costo),
                ),
            )
            ing_obj[nombre] = obj
        self.stdout.write(f"✔ {len(ing_obj)} ingredientes (incluye empaque).")

        for nombre, (emoji, perfil, precio, items) in RECETAS.items():
            rec, _ = Receta.objects.update_or_create(
                nombre=nombre,
                defaults=dict(emoji=emoji, perfil=perfil,
                              precio_venta=Decimal(precio)),
            )
            full = list(items) + EMPAQUE
            for ing_nombre, cant in full:
                RecetaIngrediente.objects.update_or_create(
                    receta=rec, ingrediente=ing_obj[ing_nombre],
                    defaults=dict(cantidad=Decimal(str(cant))),
                )
        self.stdout.write(f"✔ {len(RECETAS)} recetas (con empaque incluido).")

        for fecha, ing_nombre, cant, costo, prov in COMPRAS:
            Compra.objects.get_or_create(
                fecha=fecha, ingrediente=ing_obj[ing_nombre],
                cantidad=Decimal(str(cant)), costo_unitario=Decimal(costo),
                defaults=dict(proveedor=prov),
            )
        self.stdout.write(f"✔ {len(COMPRAS)} compras de ejemplo.")

        for fecha, rec_nombre, cant in VENTAS:
            rec = Receta.objects.get(nombre=rec_nombre)
            Venta.objects.get_or_create(
                fecha=fecha, receta=rec, cantidad=cant,
            )
        self.stdout.write(f"✔ {len(VENTAS)} ventas de ejemplo.")

        extras_def = [
            ("Shot de espresso", "Espresso", 30, "12.00"),
            ("Creatina", "Creatina", 5, "15.00"),
        ]
        for nombre, ing_nombre, cant, cargo in extras_def:
            Extra.objects.update_or_create(
                nombre=nombre,
                defaults=dict(
                    ingrediente=ing_obj[ing_nombre],
                    cantidad=Decimal(str(cant)), cargo=Decimal(cargo),
                ),
            )
        self.stdout.write(f"✔ {len(extras_def)} extras de catálogo (espresso, creatina).")

        self.stdout.write(self.style.SUCCESS("Datos cargados correctamente."))
