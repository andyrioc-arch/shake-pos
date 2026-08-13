# Baseline del costeo — P0

Foto de producción tomada el **12 de agosto de 2026**, en solo lectura, antes de
tocar nada del costeo.

> **Los datos transaccionales de producción son de prueba** (confirmado por
> Rubén el 12 de agosto). Las 10 compras, 8 ventas, 2 clientes y 1 canje no
> representan operación real; lo único real es el catálogo (19 recetas, 30
> ingredientes) y los 3 costos fijos.
>
> Por eso estos números **no son un contrato que P3 deba reproducir**. Se
> conservan por dos razones: P1 los usó para demostrar que su migración no
> movía nada, y el análisis de abajo destapó un defecto de código real que sí
> hay que arreglar antes de operar con dinero de verdad.

Ver [DISENO-COSTEO.md](DISENO-COSTEO.md) para el plan completo (P0 a P11).

## Censo de datos

| Qué | Cuántos |
|---|---|
| Compras | 10 |
| Compras con cantidad 0 | **0** |
| Compras con cantidad fraccionaria | **0** |
| Compras cuyo `cantidad × costo_unitario` pasa de 2 decimales | **0** |
| Ingredientes con `cantidad_por_unidad = 0` | **0** |
| Ventas | 8 |
| Movimientos facturados | **0** |

**Lo que esto decide:** P1 introduce `monto_total = cantidad × costo_unitario`.
El riesgo era que el producto tuviera más de dos decimales y el redondeo moviera
el costo de ventas. Con cero filas fraccionarias, **el delta de P1 es cero** y
la promesa de «ningún asiento se mueve» es verificable, no un deseo.

## Baseline del FIFO

`fifo_cogs()` filtra por `facturado=True` y en producción no hay ni un
movimiento facturado, así que **hoy devuelve `{}`**. Cualquier verificación
contra esa función se cumpliría sola. Estos números son la misma lógica **sin
ese filtro**: es lo que P3 debe reproducir cuando el costo se persista.

| Venta | Costo |
|---|---|
| 7 | 2.68 |
| 8 | 40.58 |
| 9 | 40.44 |
| 10 | 40.44 |
| 11 | 40.58 |
| 12 | 91.54 |
| 13 | 81.16 |
| 14 | 81.16 |
| **Total** | **418.58** |

## El hallazgo que cambia las prioridades

**El 72% de ese costo no respalda inventario real.** Diez de los ingredientes
que las recetas consumen no tienen ni una compra registrada, así que el respaldo
del FIFO los valúa con el precio del catálogo y abona la cuenta 115 por unidades
que nunca entraron.

| Ingrediente | Unidades sin capa | Costo inventado |
|---|---|---|
| Habits Cacao | 180.00 | 172.90 |
| Habits Vainilla | 60.00 | 57.63 |
| Café | 72.00 | 23.39 |
| Yoghurt Griego | 120.00 | 11.26 |
| Cacao | 30.00 | 11.25 |
| Leche de coco | 300.00 | 10.73 |
| Hielo | 2800.00 | 10.02 |
| Stevia | 8.00 | 3.30 |
| Canela | 1.50 | 0.50 |
| Sal de mar | 1.50 | 0.16 |
| **Total** | | **301.16** |

**Consecuencia concreta:** al eliminar el gate «Facturado» (P5), la cuenta 115
Inventario quedaría alrededor de **−$301** y el Balance General seguiría
diciendo «✓ Cuadra», porque solo verifica que las sumas coincidan, no que los
signos tengan sentido.

Eso convierte el invariante **I3** de P3 —dejar de costear con el catálogo lo
que no tiene capa— en prerrequisito de P5, no en higiene opcional.

Con datos de prueba el desbalance no le cuesta nada a nadie, pero el defecto es
de código y se repetiría igual con dinero real: mientras el respaldo exista,
cualquier venta de un insumo sin compra capturada infla el costo y hunde el
inventario. Y con operación real va a pasar seguido, porque lo normal es vender
en la mañana y capturar la factura del proveedor en la tarde.

## Tests que fijan este baseline

| Test | Qué congela |
|---|---|
| `finanzas.PuntoEquilibrioTests.test_margen_con_add_ons_ignora_su_cargo_y_su_costo` | Que el margen de contribución excluye add-ons por los dos lados (sin cobertura hasta ahora) |
| `finanzas.PuntoEquilibrioTests.test_punto_equilibrio_baseline_con_ventas` | La cadena costos fijos → margen → unidades |
| `lealtad.MetricasTests.test_baseline_del_indicador_del_5_por_ciento` | El indicador que Andy mira, hoy calculado con el costo del catálogo |
| `contabilidad.RespaldoAlCatalogoTests.test_vender_sin_haber_comprado_deja_el_inventario_en_negativo` | Que hoy vender sin comprar deja la 115 en negativo y el balance igual se declara correcto |
