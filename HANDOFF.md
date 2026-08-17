# Handoff — 16 de agosto de 2026

Para retomar en otra conversación.

**El sistema está en uso real.** El 16 de agosto se publicaron siete cambios
(PR #8 a #14) y se corrigieron a mano las 24 compras de producción. **De la
lista que pidió Andy no queda nada.** 390 tests.

Contexto de fondo en [CLAUDE.md](CLAUDE.md); el plan de costeo, ya cerrado, en
[DISENO-COSTEO.md](DISENO-COSTEO.md) y [BASELINE-COSTEO.md](BASELINE-COSTEO.md).

## Lo más importante que pasó hoy

**Las 24 compras de producción estaban capturadas 1360 veces más baratas de lo
real.** En `cantidad` estaba el tamaño del paquete (1360) en vez del número de
paquetes (1). La capa de blueberries creía tener 1,849,600 g por $178.54, o sea
$0.0000956 el gramo contra los $0.13 reales.

Se corrigió en la base **antes de que ninguna venta se costeara con esos
precios** —producción tenía 0 ventas en ese momento, porque Andy había borrado
las de prueba—. La comprobación de que quedó bien: el costo real de las 24 ahora
coincide **exactamente** con el estimado del catálogo, dos números que se
calculan por caminos distintos. Antes no coincidía ninguno.

Nada en el sistema lo avisaba: `recostear --verificar` salía sano —sus tres
igualdades son de consistencia interna, y basura consistente las cumple— y la
alarma de margen solo mira las caídas, mientras que este error **sube** el
margen. Por eso el PR #13 existe.

## Lo que se publicó (16 de agosto)

| PR | Qué |
|---|---|
| #8 | **Borrar una venta con extras ya no truena.** Era el error 500 que Andy reportaba. |
| #9 | Test del día completo, de punta a punta y por HTTP. |
| #10 | Nombre en cada venta + lista de pedidos pendientes + marcar entregado. |
| #11 | Descuentos pactados por cliente, con vigencia. |
| #12 | Descuento % en la caja, que se autollena con el del cliente. |
| #13 | La captura de compras enseña en qué se convierte antes de guardar. |
| #14 | Merma: el conteo físico saca del inventario lo que se perdió. |

### El bug del borrado (PR #8), que vale por sí solo

Django manda **todos** los `pre_delete` primero, luego borra, y hasta el final
los `post_delete`; los hijos se borran antes que el padre. La guarda de
`_recostear_venta_de` preguntaba si la venta seguía existiendo — y sí existía.
Así que la señal del extra la volvía a costear e insertaba `ConsumoCapa`
colgando de una venta que desaparecía un instante después. En Postgres las
llaves foráneas son `DEFERRABLE`, así que la violación salía hasta el `COMMIT` y
la pantalla solo decía «error 500».

**Preguntar si algo existe no sirve como guarda dentro de un borrado en
cascada.** Hay que preguntarle al registro de lo que se está borrando
(`_pendiente`), que lo llenó el `pre_delete` del padre.

### Merma (PR #14): dónde vive y por qué

En `ConsumoCapa`, con la venta en blanco y un `ajuste` en su lugar. El
invariante que audita `recostear --verificar` es «lo consumido más el saldo da
lo que trajo la capa», y lo consumido lo cuenta esa tabla: **una tabla aparte lo
dejaría roto en cada capa que tocara una merma**, y ese auditor es lo único que
avisa cuando el costeo se descompone. Una `CheckConstraint` obliga a que cada
fila cuelgue de una venta o de un ajuste, nunca de las dos ni de ninguna.

Se cuesta con las mismas tres reglas que una venta. **Lo que sobra en un conteo
no se da de alta**: significa que falta capturar una compra, y darle entrada
obligaría a inventarle un precio.

### Descuento (PR #12): dónde vive y por qué

En `Venta.descuento_pct`, no en la nota. Todo lo que lee dinero —contabilidad,
margen, alarma, presupuesto, IVA, puntos— pasa por `Venta.ingreso`, así que
ponerlo ahí lo deja bien en los seis lugares a la vez.

**Un descuento del 100% no es una cortesía.** La cortesía va contra la 506 y
consume inventario sin ingreso; el descuento se queda en el ingreso. Por eso
`ingreso_lista` existe aparte: sin ese dato los dos se ven idénticos.

## Bugs que ya estaban y salieron hoy

- **`pago_con=NaN` tumbaba la caja con un 500.** `Decimal("NaN")` se construye
  sin quejarse y `NaN < 0` lanza `InvalidOperation` en vez de devolver `False`.
  Por `_to_decimal` pasan el efectivo recibido y la captura de compras.
  Arreglado en la raíz. `lealtad/api.py` ya tenía la guarda `is_finite()`; era
  cuestión de copiarla.
- **La nota con descuento no cuadraba por un centavo.** La plantilla redondeaba
  el descuento con `floatformat` y el total se redondea sobre el complemento:
  los dos suben. Se derivó el monto de la resta, que cuadra por construcción.
- **El comprobante hacía 20 consultas** donde bastaban 9, y **el panel ganó una
  consulta por ingrediente** con las mermas. Las dos las cazó `/code-review`.

## Cómo quedó producción

```
compras                     24
real coincide con catálogo  24   ← las 24, antes ninguna
capas mal calculadas         0
saldos que no cuadran        0
ventas / consumos            0
cuentas del catálogo        15   ← con la 507 Merma
```

Las 24 compras tienen su capa y su saldo correctos. Cuando Andy empiece a
vender, cada venta se costeará con precios reales.

## Decisiones que tomó Rubén y no hay que volver a preguntar

Las de agosto siguen vigentes (IVA solo en la nota; la alarma es de caídas; el
costo FIFO llega a la contabilidad; los datos transaccionales son de prueba; no
hay que avisarle a Andy antes de publicar). Se suman las de hoy:

- **Un solo campo de nombre en la caja.** El nombre con que se canta el pedido
  es el que se le pone al cliente en lealtad. Dos campos para lo mismo en la
  misma pantalla se contestan distinto.
- **El descuento se liga a un cliente del programa**, identificado por celular.
  Un descuento a nombre de «Juan» no se puede aplicar: hay tres Juanes.
- **Con dos descuentos vigentes gana el mayor.** Tiene que haber una regla, o el
  mismo cliente paga distinto según el orden de alta.
- **Sobrar en un conteo no da de alta inventario.**
- **La lista de pedidos muestra solo lo pendiente.** Una lista que crece todo el
  día deja de leerse a la tercera hora.

## Reglas de trabajo

Las de siempre —un paso por rama, nunca empujar a `main` sin consultarlo,
`/code-review` después de cada cambio— más dos que hoy se ganaron el sitio:

- **Los comandos para Rubén van completos y copiables**: con el `cd` al
  proyecto, `.venv/bin/python`, y todo valor que ya se conozca relleno. Solo la
  contraseña queda como marcador, y **sin corchetes angulares** — se le pegaron
  dentro del valor una vez y `dj_database_url` tronó con «Scheme '://' is
  unknown».
- **Verificar en el navegador, no solo con tests.** Los cuatro hallazgos que
  `/code-review` no vio —el `hidden` que el CSS pisaba, el «15.00%» donde iba
  «15%», el mensaje que no se pintaba, el factor con decimales— salieron ahí.

## Qué sigue

Nada de la lista de Andy. Lo abierto es deuda, por orden de peso:

1. **El costeo corre dentro del request.** ~480 consultas en la primera venta
   del día y ~1000 en la séptima, porque cada venta recuesta las anteriores. Con
   SQLite no se nota; contra el pooler y con volumen real, sí. **Es el riesgo
   operativo más serio que queda.**
2. **`?mes=13` da 500** en contabilidad, finanzas y presupuesto (`KeyError` en
   `contabilidad/views.py:63`). Tres líneas.
3. **RLS apagado en las 48 tablas de Supabase.** Este sistema no usa la Data
   API, así que apagarla en Settings → API cierra la puerta sin tocar una tabla.
4. **El cajero ve los paneles de lealtad** por URL directa —el menú los esconde,
   las URLs responden 200— con margen de miembros, LTV y utilidad neta.
5. **Una receta desactivada desaparece del carrito en silencio** y el cliente
   paga de menos. Fijado en el test E2E como hallazgo, no como diseño.
6. **No hay corte de caja**: `sincronizar_movimiento` postea toda venta a la 101
   sin mirar `metodo_pago`, así que lo cobrado con tarjeta figura como efectivo.
7. Los tres asuntos de la revisión de P3, los 44 hallazgos de la auditoría y el
   menú móvil que se lleva ~780px antes del contenido.

Y una idea que quedó diseñada pero no construida: **capturar compras desde la
foto del ticket**. El veredicto fue que la pieza valiosa no es el MCP sino la
previsualización que traduce el ticket a la capa resultante — y esa ya se
construyó en el PR #13. El transporte se puede añadir después si el flujo
demuestra que sirve.
