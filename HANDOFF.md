# Handoff — 13 de agosto de 2026

Para retomar en otra conversación.

**El rediseño del costeo está EN PRODUCCIÓN.** PR #1 integrado a `main`,
migrado con el session pooler y desplegado en https://shake-pos.vercel.app.
`reglas-negocio-andy` cumplió su función y ya no se usa.

**Ese mismo día se publicó todo lo demás**: la alarma de margen (PR #2), los
seis pasos del PR #3 —P7, P9, P10, la gráfica de qué se vende más, el enlace
del libro a la nota, y la nota con hitos y PDF— y **P11 en dos tandas** (PR #4
y #5). **El plan de costeo está cerrado y de la lista que Andy pidió no queda
nada.** 294 tests.

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

## Alarma de margen (publicada, PR #2)

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

## Seis pasos más, publicados el 13 de agosto (PR #3)

| | Qué |
|---|---|
| **P7** | «Salieron / Cobrados / Regalados». `unidades_vendidas` sigue contando todo: lo regalado se produjo y consumió insumo. |
| **P9** | Entregar un premio de producto crea una cortesía que descuenta inventario y postea a la 506, bajo Mercadotecnia. |
| **P10** | Costo consolidado por producto, con lo que costaría a precios de la última compra, y panel de salud del costeo. |
| Gráfica | «Qué se vende más», repartiendo lo COBRADO. Sin librería ni JavaScript. |
| Libro → nota | La descripción de una venta con nota es un enlace. |
| Nota | Anuncia el premio ganado y el cambio de nivel, y se puede guardar en PDF. |

Se publicaron migrando `lealtad.0003` primero: agrega dos columnas que el
código nuevo lee.

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

## P11, hecho el 13 de agosto (PR #4 y #5)

Se fueron `Movimiento.facturado`, `Movimiento.fecha_factura` y
`Compra.costo_unitario`, y `monto_total`, `cantidad_receta` y `saldo_receta`
dejaron de admitir nulos. **Con esto el plan de costeo está cerrado.**

`costo_unitario` es ahora una propiedad derivada, solo para mostrar. Nunca para
reconstruir el total: 1.5 × 31.55 da 47.325 y lo que se pagó fueron 47.33.

**La lección, que vale para cualquier borrado futuro:** ningún orden de
despliegue funcionaba. `facturado` era NOT NULL sin default, así que migrar
primero tumbaba el código viejo y desplegar primero tumbaba el nuevo. Se partió
en dos tandas —quitarle el NOT NULL, desplegar, borrar— y **cada tanda en su
propia rama**, porque `manage.py migrate` aplica todo lo pendiente: juntas, la
línea de siempre las habría corrido de un golpe y la caja se cae igual.

## Qué sigue

**Del plan no queda nada, y de la lista de Andy tampoco.** Lo que sigue abierto
es deuda vieja:

- Los tres asuntos de la revisión de P3: editar una compra ya consumida no
  reajusta su capa, el recosteo masivo corre dentro del request, y el residuo
  de un centavo cuando muchas ventas cruzan las mismas capas.
- Los 44 hallazgos de la auditoría. **P9 agravó uno**: el posible deadlock
  entre `canjear()` y `expirar_puntos()`, porque ahora esa transacción también
  escribe inventario. Es el candidato más justificado.
- El menú móvil, que se lleva ~780px antes del contenido en 375×812.
- **Supabase tiene RLS apagado en las 47 tablas**: con la llave `anon`
  cualquiera las lee. Este sistema no usa la Data API —Django habla Postgres
  directo por el pooler— así que apagarla en Settings → API cierra la puerta
  sin tocar una sola tabla ni arriesgar el POS.

Y lo que no es código: **los tickets de las diez compras que faltan**. Son los
que destapan el Estado de Resultados, y ningún despliegue los sustituye.
