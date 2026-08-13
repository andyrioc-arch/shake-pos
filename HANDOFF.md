# Handoff — 13 de agosto de 2026

Para retomar en otra conversación. Estado de la rama **`reglas-negocio-andy`**,
que no está empujada ni desplegada. Producción sigue exactamente como estaba.

Contexto de fondo en [CLAUDE.md](CLAUDE.md), plan completo en
[DISENO-COSTEO.md](DISENO-COSTEO.md), foto previa en
[BASELINE-COSTEO.md](BASELINE-COSTEO.md).

## Lo que quedó hecho

**229 tests pasan.** Siete pasos del plan de costeo, más dos reglas visibles.

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
- El margen de contribución ahora resta ingreso menos costo **de la línea
  completa**, así que los add-ons entran con su cargo y con su costo. El
  baseline de P0 se movió de $85.00 a $87.00; es una corrección, está
  documentada en el test.
- Las cortesías salen del margen y del costo variable, pero **no desaparecen**:
  tienen su propia columna en el flujo y se siguen restando de la ganancia
  operativa. El plan solo decía «excluirlas»; hacerlo a secas dejaba el insumo
  regalado sin restarse en ningún lado y adelantaba el retorno de la inversión.

### Lo que hay que saber de P5

- Compras y gastos se reconocen al capturarse. Una venta se reconoce solo si su
  costo está completo. El libro de movimientos cambió la columna «Facturado»
  por «En resultados», que dice **✓ Reconocido** o **⏳ Falta costo**.
- **La migración no toca un solo dato, contra lo que decía el plan.** El diseño
  volteaba `facturado=True` en todo el histórico y agregaba un campo testigo
  para poder revertirlo. Sobra: el código nuevo no lee esa columna en ningún
  camino. Congelada como la dejó el código viejo, revertir el despliegue
  devuelve el reporte publicado sin migración inversa. Volteándola habría hecho
  lo contrario: al revertir, el código viejo vería todo facturado y reconocería
  el histórico entero con el FIFO que inventa costo de catálogo.
- Se borraron `posting.marcar_facturado()`, `posting.resincronizar_cogs()`, la
  vista `movimiento_facturar`, su ruta y el formulario de la plantilla.
  `resincronizar_cogs` ya no hacía falta: `inventario.costeo` resincroniza por
  venta.

### El aviso importante de P5

**P5 no destapa los reportes por sí solo.** Verificado en local: las 6 ventas
del seed siguen sin reconocer porque 17 insumos no tienen ni una compra. En
producción pasa lo mismo con los diez insumos de BASELINE-COSTEO.md. La cuenta
401 seguirá en cero hasta que Andy capture esas compras — **la captura dejó de
ser una molestia y es ahora el bloqueo del Estado de Resultados.**

## Decisiones que tomó Rubén y no hay que volver a preguntar

- **El IVA no se contabiliza.** Se separa únicamente en la nota al cliente.
- **El cambio se aplica sobre agosto en curso**, no se espera al corte de mes.
- **Cortesías**: la cuenta 506 se reclasifica como subcuenta de 504
  Mercadotecnia; no se cambia el posteo.
- **La alarma de margen es cuando el margen BAJA**, no cuando sube.
- **El costo FIFO llega hasta la contabilidad**, no solo a reportes.
- **Dos cajeros simultáneos no va a ocurrir**: el trabajo de concurrencia no
  aplica y P8 se cae del plan.
- **Los datos transaccionales de producción son de prueba.** No hay número
  histórico que preservar.
- La receta del Latte gratis la hace Andy.

## Reglas de trabajo

- Nunca empujar a `main` sin consultarlo. El repo es de Andy.
- Después de cada cambio de código: `/code-review`, `/simplify`, `/qa`, y
  `/impeccable audit` solo si hay interfaz.
- **El orden de publicación depende de la migración**, y las pendientes van en
  sentidos distintos:
  - `contabilidad/0007` **quita** cuentas que el código viejo usa →
    **desplegar primero, migrar después**.
  - `inventario/0008` y `0009` **agregan** columnas que el código nuevo
    necesita → **migrar primero, desplegar después**.
  - `contabilidad/0008` no toca datos → el orden da igual; correrla con el
    despliegue para no dejar modelo y base desfasados.
  - `contabilidad/0009` **agrega** la columna `Cuenta.padre` que el reporte
    nuevo necesita → **migrar primero, desplegar después**. Nace nullable.
- Tras cualquier despliegue que toque costeo: `manage.py recostear --todo` y
  luego `--verificar`.

## Qué sigue

Recomendación: **saltar a lo que Andy ve**, que es casi todo lo que ella pidió y
sigue sin empezar. Lo que queda del plan de costeo es chico salvo P9.

**Del plan de costeo (3 pasos, 1 con peso real):**

- **P7** — la parte del margen ya quedó con P4 y tiene tests. Falta solo separar
  «vendidas» de «regaladas» en el catálogo, sin cambiar `unidades_vendidas`.
- **P9** — que canjear un premio descuente inventario y vaya a gasto de
  mercadotecnia. Hoy no existe nada de eso: `lealtad` no importa `contabilidad`
  en ningún lado. Regla explícita de Andy.
- **P10** — costo consolidado por producto (no por ingrediente) y panel de salud
  del costeo.
- **P11** — diferido a propósito hasta un ciclo de operación real. Es borrar
  columnas viejas; mientras existan, todo se revierte con un despliegue.

**Lo que Andy ve y no existe:** desglose de cuentas, enlace del libro de
movimientos a la nota, alarma de margen bajo, gráfica de % de ventas por
producto, PDF de la nota al escanearla, y que la nota anuncie el premio
alcanzado y el cambio de nivel.

## Lo que no depende de código

**Faltan capturar las compras reales de diez insumos.** Con datos reales, el 72%
del costo de ventas no tendría respaldo. La lista está en BASELINE-COSTEO.md.
Ningún cambio de código arregla esto: son capturas que hace Andy con los tickets.

Desde P5 **esto bloquea el Estado de Resultados**, no solo el costo: una venta
sin costo completo no reconoce ingreso. Es a propósito —reconocer el ingreso sin
su costo infla la utilidad bruta en silencio— pero significa que el reporte no
se destapa con un despliegue, sino cuando entren esas compras.

## Anotado y no atendido

De la revisión adversarial de P3, tres asuntos que no corrompen datos:

- Editar una compra ya consumida no reajusta su capa ni recuesta.
- El recosteo masivo corre dentro del request; con muchas ventas y una factura
  retroactiva puede tardar.
- Residuo de un centavo cuando muchas ventas cruzan las mismas capas.

De interfaz en el mostrador quedaban dos. **Las tablas ya se resolvieron**: el
libro de movimientos y el flujo mensual viven ahora en un contenedor
`overflow-x:auto` con `min-width:max-content`. El detalle que costó encontrar es
que envolverlas no basta —con `width:100%` la tabla se encoge al contenedor y su
contenido se desborda de su propia caja, así que ni desplazándose hasta el final
se alcanza la última columna—; hace falta el `max-content`, que además no hay
que recordar subir cada vez que se agregue una columna.

Sigue abierto **el menú, que ocupa casi toda la pantalla del celular**: medido en
375×812 se lleva ~780px antes de que empiece el contenido. Está en la plantilla
base y lo comparten todas las pantallas, así que arreglarlo es una decisión de
diseño, no un ajuste de este bloque.
