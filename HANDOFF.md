# Handoff — 13 de agosto de 2026

Para retomar en otra conversación.

**El rediseño del costeo está EN PRODUCCIÓN.** PR #1 integrado a `main`,
migrado con el session pooler y desplegado en https://shake-pos.vercel.app.
`reglas-negocio-andy` cumplió su función y ya no se usa.

**Lo siguiente ya está hecho y sin publicar: la alarma de margen bajo**, en la
rama `alarma-margen-bajo`. Es la primera pieza de la lista que Andy pidió y sí
ve. 253 tests. Ver [Alarma de margen](#alarma-de-margen-hecha-sin-publicar).

Contexto de fondo en [CLAUDE.md](CLAUDE.md), plan completo en
[DISENO-COSTEO.md](DISENO-COSTEO.md), foto previa en
[BASELINE-COSTEO.md](BASELINE-COSTEO.md).

## Cómo quedó producción

Medido después de migrar, desplegar y correr `sincronizar_contabilidad` +
`recostear --todo`:

```
balanza cuadra     True
balance cuadra     True
saldo inventario   487.81      ← positivo
ventas             8
sin costear        0
incompletas        8
consumo sin capa   3573
```

Ese saldo positivo es el punto de todo el bloque. Antes, cuando el FIFO no
encontraba compras, completaba el faltante con precios de catálogo y **abonaba
inventario que nunca entró**: la cuenta se hundía a negativo y el balance se
declaraba correcto igual, porque solo verifica que las sumas coincidan y no que
los signos tengan sentido.

**Las 8 ventas están incompletas, así que ninguna reconoce ingreso y la 401
sigue en cero.** No es una falla: es el invariante funcionando. Lo que antes
escondía un botón que nadie presionaba, ahora está a la vista y con nombre.

## Lo que se publicó

**240 tests.** Ocho pasos del plan, más dos reglas visibles.

| | Qué |
|---|---|
| C1 | La caja pide el nombre del cliente. Aparece solo cuando hace falta: cliente nuevo, o uno que existe pero se dio de alta sin nombre. |
| C2 | Quitado «Muéstralo al pagar» de la tarjeta de lealtad. |
| P-IVA | El IVA sale de la contabilidad. Se borran las cuentas 105 y 201; los importes van completos. El único lugar donde se desglosa es la nota al cliente. |
| P0 | Baseline y censo de producción, congelados en tests. |
| P1 | La compra guarda el monto pagado (`monto_total`). El costo unitario queda derivado. |
| P2 | La caja guarda el monto exacto y deja de pisar el costo del catálogo. |
| P3 | El costo de ventas pasa a ser un dato guardado, con capas de inventario por FIFO. |
| P4 | Los paneles leen el costo guardado. Las cortesías salen del margen del producto. |
| P5 | Fuera el botón «Facturado»: el reconocimiento es automático. |
| P6 | La 506 Cortesías se lee dentro de la 504 Mercadotecnia, sin mover el posteo. |

### Lo que hay que saber de P4

- `Venta.costo_de_ventas` es la propiedad puente que leen los paneles: el
  `costo_fifo` si la venta está costeada **por completo**, y si no el estimado
  del catálogo. **Un costo incompleto también cae al estimado**, y esto no
  estaba en el plan: una venta sin ninguna capa guarda `costo_fifo = 0.00`, y
  publicar ese cero como costo hace ver la venta el doble de rentable justo
  cuando menos se sabe. La contabilidad sigue leyendo `costo_fifo` en crudo.
- El margen de contribución resta ingreso menos costo **de la línea completa**,
  así que los add-ons entran con su cargo y con su costo. El baseline de P0 se
  movió de $85.00 a $87.00; es una corrección, documentada en el test.
- Las cortesías salen del margen y del costo variable, pero **no desaparecen**:
  tienen su propia columna en el flujo y se siguen restando de la ganancia
  operativa. El plan solo decía «excluirlas»; hacerlo a secas dejaba el insumo
  regalado sin restarse en ningún lado y adelantaba el retorno de la inversión.

### Lo que hay que saber de P5

- Compras y gastos se reconocen al capturarse. Una venta se reconoce solo si su
  costo está completo. El libro cambió la columna «Facturado» por «En
  resultados»: **✓ Reconocido**, **⏳ Falta costo** o **⚠ Sin reconocer**.
- **La migración no toca un solo dato, contra lo que decía el plan.** El diseño
  volteaba `facturado=True` en todo el histórico y agregaba un campo testigo
  para poder revertirlo. Sobra: el código nuevo no lee esa columna en ningún
  camino. Congelada como la dejó el código viejo, revertir el despliegue
  devuelve el reporte publicado sin migración inversa. Voltearla habría hecho
  lo contrario: al revertir, el código viejo vería todo facturado y reconocería
  el histórico entero con el FIFO que inventa costo de catálogo.
- Se borraron `posting.marcar_facturado()`, `posting.resincronizar_cogs()`,
  `posting.fifo_cogs()`, la vista `movimiento_facturar`, su ruta y el
  formulario. `fifo_cogs` era una segunda implementación completa del FIFO
  viviendo en `contabilidad`; su docstring decía que la usaba
  `recostear --verificar`, y ese comando declara explícitamente lo contrario.

### Lo que hay que saber de P6

- La jerarquía vive en `Cuenta.padre`, no en renombrar la 506 a «504.01»:
  `crear_catalogo()` usa `update_or_create(codigo=...)` y habría dejado dos
  cuentas para lo mismo.
- `crear_catalogo()` repone la relación en cada corrida, porque
  `_cuenta_segura()` puede recrear una cuenta borrada y renacería suelta,
  desapareciendo de su grupo sin que nadie se entere.
- La 504 es cuenta de movimiento **y** padre a la vez: el reporte muestra
  «Mercadotecnia (directo) + ↳ Cortesías = Total mercadotecnia».

## Decisiones que tomó Rubén y no hay que volver a preguntar

- **El IVA no se contabiliza.** Se separa únicamente en la nota al cliente.
- **El cambio se aplica sobre agosto en curso**, no se espera al corte de mes.
- **Cortesías**: la 506 se agrupa bajo 504 Mercadotecnia; no se cambia el posteo.
- **La alarma de margen es cuando el margen BAJA**, no cuando sube.
- **El costo FIFO llega hasta la contabilidad**, no solo a reportes.
- **Dos cajeros simultáneos no va a ocurrir**: el trabajo de concurrencia no
  aplica y P8 se cae del plan.
- **Los datos transaccionales de producción son de prueba.** No hay número
  histórico que preservar.
- **A Andy no hay que avisarle antes de publicar**: está al tanto del trabajo.
- La receta del Latte gratis la hace Andy.

## Reglas de trabajo

- **Un paso por rama.** El bloque P0–P6 fue grande porque el diseño no lo
  dejaba partir —quitar el gate sin el costo persistido deja el margen mal, y
  persistir capas antes de arreglar el redondeo obliga a recostear dos veces—.
  P7, P9, P10 y P11 son independientes y van sueltos.
- **Commitear seguido.** Este bloque llegó a 1,457 líneas en 40 archivos con
  cero commits. Un día de trabajo verificado sin punto de guardado.
- Nunca empujar a `main` sin consultarlo. El repo es de Andy.
- Después de cada cambio de código: `/code-review`, `/simplify`, `/qa`, y
  `/impeccable audit` solo si hay interfaz.
- Tras cualquier despliegue que toque costeo: `sincronizar_contabilidad`,
  luego `recostear --todo`, luego `--verificar`. **En ese orden**: el primero es
  el que le da a cada compra su asiento de entrada a Inventario, y sin él las
  ventas abonan un activo que nadie cargó.

### La lección de orden que dejó este despliegue

Las dos reglas de orden se contradijeron: `contabilidad/0009` agrega una columna
que el código nuevo necesita (migrar primero) pero no se puede aplicar sin pasar
por `contabilidad/0007`, que borra cuentas que el código viejo usa (desplegar
primero). **Las migraciones de una app son una historia lineal, así que a veces
no hay orden que cumpla las dos.** Cuando pase: medir el riesgo real de la que
se incumple, en el código, no en la regla. Aquí se migró todo primero porque el
posteo de IVA del código viejo vive dentro de `if mov.facturado:` y ese botón no
se presionó nunca.

## Alarma de margen (hecha, sin publicar)

Rama `alarma-margen-bajo`, seis commits, 253 tests. Avisa cuando el margen de un
producto **baja** más que el umbral respecto al mes anterior. Solo la caída: que
un producto mejore no es una alarma, y mezclar las dos direcciones convierte el
aviso en ruido que se aprende a ignorar.

Decisiones que tomó Rubén y ya no hay que preguntar:

- **El disparador es una caída del 10% contra el mes anterior**, no un umbral de
  margen mínimo ni una comparación contra el esperado.
- **Un solo umbral global**, configurable desde el admin, no uno por receta.

Tres cosas que la alarma se niega a hacer, y por qué:

- **Inventar una caída.** Un producto que no se vendió en alguno de los dos
  meses no aparece, igual que el costeo no inventa un costo.
- **Contar cortesías.** No cobran; incluirlas hunde el margen sin que el precio
  ni el costo hayan cambiado. El filtro bajó a `Venta.objects.comerciales()`,
  junto a `con_costeo()`, y `finanzas.calculos` ya lo usa.
- **Callar que se apoya en el catálogo.** Si a alguna venta le falta su compra,
  el aviso se marca «· estimado», con la explicación visible: ese costo es el de
  hoy, así que corregir un ingrediente mueve el porcentaje solo.

**Limitación conocida, decidida a propósito.** Compara un mes en curso —que a
principios de mes puede llevar una sola venta— contra un mes completo. Una venta
atípica enciende el aviso igual que una subida real de insumos; la columna de
unidades (`40 → 1`) es la pista. Con 8 ventas capturadas, cualquier piso de
unidades dejaría la alarma muda. Se revisa cuando haya un mes con volumen real.

**Para publicarla:** `inventario.0010` **crea** la tabla que el código nuevo
necesita, así que **migrar primero y desplegar después**. No toca costeo, así
que no hace falta `sincronizar_contabilidad` ni `recostear`.

Lo que se aprendió peleando con la interfaz, y está en CLAUDE.md:

- `base.html` vuelve toda tabla `display:block` con su propio `overflow-x` en
  móvil. Eso mete un contenedor de desplazamiento entre la celda y el scroller
  y ahí `position:sticky` deja de aplicar, igual que `border-collapse:collapse`.
  Una columna anclada necesita devolverle a la tabla su `display:table`.
- Los tonos vivos de la marca no alcanzan para texto: 3.08:1 el rosa y 2.95:1
  el azul, contra el 4.5:1 de WCAG AA. Donde el color carga un dato se usan
  `#c81a63` y `#1478cc`.

## Seis pasos más, hechos el 13 de agosto (rama `pdf-de-la-nota`)

294 tests. Con esto se cierra el plan de costeo salvo P11, y la lista que Andy
pidió salvo lo que no se ha propuesto.

| | Qué |
|---|---|
| **P7** | «Salieron / Cobrados / Regalados». `unidades_vendidas` sigue contando todo: lo regalado se produjo y consumió insumo. |
| **P9** | Entregar un premio de producto crea una cortesía que descuenta inventario y postea a la 506, bajo Mercadotecnia. |
| **P10** | Costo consolidado por producto, con lo que costaría a precios de la última compra, y panel de salud del costeo. |
| Gráfica | «Qué se vende más», repartiendo lo COBRADO. Sin librería ni JavaScript. |
| Libro → nota | La descripción de una venta con nota es un enlace. |
| Nota | Anuncia el premio ganado y el cambio de nivel, y se puede guardar en PDF. |

**Para publicarla:** `lealtad.0003` **agrega** dos columnas que el código nuevo
lee, así que **migrar primero y desplegar después**. No cambia el costeo de
ninguna venta existente, así que no hace falta `recostear`.

Cinco cosas que costaron encontrar y no hay que volver a descubrir:

- **`{# #}` de Django es de UNA línea.** Con dos, se imprime tal cual — y se
  imprimió en la nota, que es la única página que ve el cliente. Para varias
  líneas, `{% comment %}`. Hay un test que recorre las plantillas.
- **Un costo copiado se queda viejo.** `Canje` guardaba el FIFO del premio en
  su propia columna; el costeo lo rehace cada vez que se captura una compra
  atrasada, y la copia no se enteraba. Se deriva de la cortesía.
- **Un hito deducido de un acumulado se equivoca.** El nivel que desbloqueó una
  compra se guarda al registrarla, donde ya se sabe: `puntos_historicos`
  también sube con `ajustar_puntos`, que no crea compra.
- **`costeo.diagnostico()` ya medía la salud del costeo.** El panel la
  reimplementaba y se tragaba dos síntomas.
- **Una propiedad en un bucle de plantilla se evalúa cada vez.** El costo de la
  última compra llevó el catálogo de 61 a 251 consultas; resolverlo en la vista
  lo dejó en 7.

## Qué sigue

**P11** es lo único que queda del plan, y sigue diferido a propósito hasta un
ciclo de operación real: borra `Movimiento.facturado`, `Movimiento.fecha_factura`
y `Compra.costo_unitario`, y mientras existan, todo esto se revierte con un
despliegue.

De lo que Andy pidió no queda nada sin hacer. Lo que sigue abierto es el
inventario de deuda: los tres asuntos de la revisión de P3, los 44 hallazgos de
auditoría —el deadlock entre `canjear()` y `expirar_puntos()` lo agrava P9, que
mete escrituras de inventario a esa transacción—, el menú móvil, y que Supabase
tiene RLS apagado en las 47 tablas.

## Lo que no depende de código

**Faltan capturar las compras reales de diez insumos.** La lista está en
BASELINE-COSTEO.md. Ningún cambio de código arregla esto: son capturas que hace
Andy con los tickets físicos.

Desde P5 **esto bloquea el Estado de Resultados**, no solo el costo: una venta
sin costo completo no reconoce ingreso. Confirmado en producción — las 8 ventas
están incompletas. Es a propósito, pero significa que el reporte lo destapan los
tickets, no un despliegue.

Probado en vivo antes de publicar: capturas la compra que faltaba y la venta
pasa sola de «Falta costo» a «Reconocido», con su costo en el reporte.

## Decisión abierta

**Los datos de prueba se borraron en LOCAL, no en producción.** Producción
conserva sus 8 ventas, 10 compras y 2 clientes de prueba, y son justamente los
que aparecen como incompletos. Si la idea es arrancar limpio y capturar solo
datos reales, conviene borrarlos también allá; si se borran, el reporte queda
vacío en vez de mostrar ocho ventas diferidas. No se hizo porque requiere la
cadena de conexión, que la maneja Rubén.

Al borrar, cuidado con una trampa: `Movimiento.venta` es CASCADE pero
`Movimiento.asiento_flujo` y `asiento_reconocimiento` son **SET_NULL**. Borrar
una venta se lleva su movimiento y deja **vivo su asiento contable**, así que
los reportes seguirían mostrando ingresos e inventario de transacciones que ya
no existen. Hay que barrer los asientos automáticos que no referencie ningún
movimiento vivo.

## Anotado y no atendido

De la revisión adversarial de P3, tres asuntos que no corrompen datos:

- Editar una compra ya consumida no reajusta su capa ni recuesta.
- El recosteo masivo corre dentro del request; con muchas ventas y una factura
  retroactiva puede tardar.
- Residuo de un centavo cuando muchas ventas cruzan las mismas capas.

De interfaz en el mostrador quedaban dos. **Las tablas ya se resolvieron**: el
libro de movimientos y el flujo mensual viven en un contenedor
`overflow-x:auto` con `min-width:max-content`. El detalle que costó encontrar es
que envolverlas no basta —con `width:100%` la tabla se encoge al contenedor y su
contenido se desborda de su propia caja, así que ni desplazándose hasta el final
se alcanza la última columna—; hace falta el `max-content`, que además no hay
que recordar subir cada vez que se agregue una columna.

Sigue abierto **el menú, que ocupa casi toda la pantalla del celular**: medido en
375×812 se lleva ~780px antes de que empiece el contenido. Está en la plantilla
base y lo comparten todas las pantallas, así que arreglarlo es una decisión de
diseño, no un ajuste suelto.

**44 hallazgos de una auditoría previa** siguen sin atender. Se cerró el
desbordamiento de `CharField` en lealtad; siguen abiertos el `select_for_update`
con join nullable, el posible deadlock entre `canjear()` y `expirar_puntos()`, y
las consultas N+1 de los paneles.
