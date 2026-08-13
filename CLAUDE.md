# shake-pos — contexto del proyecto

Sistema POS para SHAKE: inventario, finanzas, contabilidad de partida doble,
presupuesto y programa de lealtad. Django 6, 5 apps. El README explica qué hace
cada módulo; este archivo cubre el despliegue y la operación.

## Estado

En producción desde el 6 de agosto de 2026: **https://shake-pos.vercel.app**

`main` es la rama oficial y de publicación desde el 12 de agosto de 2026, cuando
se puso al día con `deploy/vercel` mediante un avance directo. `deploy/vercel`
quedó archivada y no se vuelve a tocar.

**El 13 de agosto de 2026 se publicó el rediseño del costeo** (PR #1: P-IVA y
P0–P6). Producción corre `main` y tiene aplicadas todas las migraciones hasta
`contabilidad.0009` e `inventario.0009`.

**La alarma de margen se publicó el 13 de agosto** (PR #2, `inventario.0010`).

**Seis pasos más esperan en la rama `pdf-de-la-nota`**: P7, P9, P10, la gráfica
de qué se vende más, el enlace del libro a la nota, y la nota con hitos y PDF.
Agregan `lealtad.0003`, que **crea** dos columnas que el código nuevo lee: por
la regla de abajo, **migrar primero y desplegar después**.

| Pieza | Dónde |
|---|---|
| App | Vercel, proyecto `shake-pos` (cuenta personal de Rubén, plan Hobby) |
| Base | Supabase, proyecto `shake-pos`, ref `xrimahsxtkfdhenigito`, Postgres 17, us-east-1, **plan Pro con respaldos diarios** |
| Repo | `github.com/andyrioc-arch/shake-pos` |

**Los datos transaccionales de producción son de PRUEBA** (10 compras, 8 ventas,
2 clientes, 1 canje). Lo único real es el catálogo —19 recetas, 30
ingredientes— y los 3 costos fijos. Ningún número histórico es un contrato que
haya que preservar.

## Estado del costeo tras publicar

Verificado contra producción el 13 de agosto de 2026, después de migrar,
desplegar y correr `sincronizar_contabilidad` + `recostear --todo`:

```
balanza cuadra     True
balance cuadra     True
saldo inventario   487.81      ← positivo; antes esta cuenta se hundía
ventas             8
sin costear        0
incompletas        8           ← las 8, por los insumos sin capturar
consumo sin capa   3573
```

**Las 8 ventas están incompletas, así que ninguna reconoce ingreso: la cuenta
401 sigue en cero.** Es el comportamiento correcto, no una falla —el ingreso y
su costo entran juntos o no entran—, y se destapa capturando las compras que
faltan, no tocando código.

El plan completo está en [DISENO-COSTEO.md](DISENO-COSTEO.md) y la foto previa
en [BASELINE-COSTEO.md](BASELINE-COSTEO.md).

**De aquí en adelante, un paso por rama.** El bloque P0–P6 fue grande porque el
diseño no lo dejaba partir; P7, P9, P10 y P11 son independientes y van sueltos.

## Reglas de este proyecto

- **Nunca hacer push a `main`.** El repo es de Andy; los cambios van en rama y
  se consultan antes de empujar.
- **No manejar credenciales.** Contraseñas y cadenas de conexión las configura
  el usuario en los dashboards o en su terminal. No pedirlas por chat, no
  escribirlas en archivos, no leerlas de vuelta.
- **No romper el flujo local.** `settings.py` sigue usando SQLite cuando no hay
  `DATABASE_URL`; el `runserver` de siempre debe funcionar sin configurar nada.

## Lo que se cambió para desplegar

Cinco archivos, todos compatibles hacia atrás:

- `settings.py` — Postgres vía `DATABASE_URL`, con lo que exige el pooler en
  modo transaction. Sin esa variable, SQLite. En producción **no** hay respaldo
  a SQLite: si falta, la app se niega a arrancar en vez de perder datos callada.
  `DEBUG` se apaga solo cuando existe la variable `VERCEL`.
- `requirements.txt` — `psycopg[binary]`, `dj-database-url`, tope `Django<7`.
- `vercel.json` — `collectstatic` en el build y el cron.
- `.python-version` — 3.12.
- `lealtad/api.py` + `urls.py` — endpoint `GET /api/lealtad/cron/run` protegido
  con `CRON_SECRET`, porque Vercel no tiene cron de sistema. `lealtad_run`
  acepta `--limite`.

## Trampas que ya costaron tiempo

**Dos cadenas de conexión, no intercambiables.**
La app usa el *transaction pooler* (**6543**); las migraciones, el *session
pooler* (**5432**). La conexión directa `db.<ref>.supabase.co` resuelve **solo a
IPv6** y las funciones de Vercel son IPv4 — da `Cannot assign requested
address`. DDL por el pooler transaction tampoco funciona bien.

**El modo transaction necesita dos ajustes.**
`DISABLE_SERVER_SIDE_CURSORS` y `prepare_threshold = None`. Sin el segundo,
psycopg3 promueve consultas a prepared statements y el pooler las rota entre
backends: errores intermitentes que solo salen bajo carga.

**Vercel autodetecta Django.** No crear `api/index.py` ni handler WSGI: basta
`manage.py` + `WSGI_APPLICATION`. `builds` y `routes` están deprecados.

**`DJANGO_SECRET_KEY` hace falta en el entorno de Build**, no solo en runtime:
el build corre `collectstatic`, que importa `settings.py`.

**No editar variables de entorno desde el dashboard de Vercel.** Editar en
sitio no guardó el valor nuevo dos veces seguidas. Borrar y crear de cero.

**No pasar valores por tubería de PowerShell** (`"x" | comando`): agrega BOM y
el valor guardado deja de coincidir. Usar Bash con `printf`, o el dashboard.

**Cron diario por límite del plan Hobby.** `0 15 * * *` = 09:00 CDMX. Con Pro se
puede devolver a `*/10 * * * *`, que es lo que el módulo de lealtad espera.

**En móvil, `base.html` vuelve toda tabla `display:block` con su propio
`overflow-x`.** Es la regla que las tres copias de `.scroll-x` del proyecto
vienen corrigiendo por separado, y tiene un efecto que no se ve venir: mete un
contenedor de desplazamiento entre la celda y el scroller, y ahí
`position:sticky` deja de aplicar. Una columna anclada dentro de `.scroll-x`
necesita devolverle a la tabla su `display:table` y `overflow:visible`. Lo
mismo hace `border-collapse:collapse`, que desactiva `sticky` en las celdas.

**Los tonos de marca no alcanzan para texto.** El rosa `#fa5598` da 3.08:1
sobre blanco y el azul `#1e9aff` 2.95:1, contra el 4.5:1 que exige WCAG AA.
Donde el color carga un dato o un estado se usan `#c81a63` y `#1478cc`, el
mismo tono más oscuro. Los tonos vivos se quedan en el cromo de marca.

**`{# #}` de Django es un comentario de UNA línea.** Si abarca dos, se imprime
tal cual en la página; pasó en la nota, que es lo único que ve el cliente. Para
varias líneas, `{% comment %}`. Hay un test que recorre todas las plantillas.

**Una propiedad dentro de un bucle de plantilla se evalúa en cada vuelta**, y
si consulta la base, se nota: el costo de la última compra llevó el catálogo de
61 a 251 consultas. Lo que recorra el catálogo se resuelve en la vista y se
pasa ya calculado.

## Publicar

```
vercel deploy --prod --yes
```

Antes: `python manage.py test` (deben pasar todos). Las migraciones no corren en
el despliegue; se aplican aparte con la cadena del session pooler (5432):

```
DATABASE_URL='<session pooler :5432>' python manage.py migrate
```

**El orden depende de la migración, no hay una regla única.**

- *Agrega* algo que el código nuevo necesita (una columna, una cuenta):
  **migrar primero, desplegar después.** Toda columna nueva debe nacer
  aceptando nulos o con `db_default`, porque Django suelta el DEFAULT justo
  después del `ADD COLUMN` y en esa ventana el código viejo escribe sin ella.
- *Quita* algo que el código viejo todavía usa: **desplegar primero, migrar
  después.** Al revés, el código viejo sigue escribiendo en lo que se acaba de
  borrar y `_cuenta_segura()` lo recrea sin avisar, dejando una cuenta huérfana
  fuera del catálogo. Es el caso de `contabilidad.0007`, que borra las cuentas
  de IVA.
- *No toca datos*: el orden da igual. Es el caso de `contabilidad.0008`, que
  solo congela la columna `facturado` como reliquia. Aun así conviene correrla
  con el despliegue para que el modelo y la base no queden desfasados.

**Cuando las dos reglas se contradicen, gana la que se pueda cumplir.** Las
migraciones de una app son una historia lineal: no se puede aplicar la 0009 sin
pasar por la 0007. Si una agrega algo que el código nuevo necesita y otra
anterior quita algo que el viejo usa, no hay orden que satisfaga a las dos —hay
que medir el riesgo real de la que se incumple, en el código, no en la regla.
Fue el caso del bloque de costeo: se migró todo primero porque el posteo de IVA
del código viejo vive dentro de `if mov.facturado:`, y ese botón no se presionó
nunca.

**Después de un despliegue que cambie cómo se reconoce, en este orden:**

1. `sincronizar_contabilidad` — crea los movimientos y asientos que faltan. Es
   lo que le da a cada COMPRA su asiento de entrada a Inventario. Saltárselo
   deja el activo sin cargos mientras las ventas lo abonan: inventario negativo.
2. `recostear --todo` — abre las capas huérfanas y cuesta todas las ventas.
3. `recostear --verificar` — audita. Debe salir sano y con saldo de 115 ≥ 0.

No hay despliegue automático: Vercel no pudo conectar el repo por ser de otra
cuenta. Cada publicación es manual.

## Cómo funciona el costeo (lo más delicado del sistema)

Desde el 12 de agosto de 2026, el costo de una venta es un **dato guardado**, no
un cálculo que se rehace al vuelo.

- Cada compra es una **capa**: `Compra.cantidad_receta` es cuánto trajo,
  congelado al comprarla, y `Compra.saldo_receta` cuánto le queda. El precio de
  la capa no se guarda, se deriva (`costo_unitario_capa` = total ÷ cantidad
  congelada), así que corregir la presentación de un ingrediente mañana no
  cambia lo que costó esta compra.
- Cada venta guarda su costo real (`Venta.costo_fifo`). `NULL` significa «sin
  costear»; nunca cero por defecto.
- `ConsumoCapa` registra qué capa surtió a qué venta. Es lo que permite
  **deshacer** el costeo, y sin eso no se puede recostear ni borrar nada bien.
- Todo vive en `inventario/costeo.py`; `inventario/signals.py` lo mantiene al
  día pase lo que pase con ventas y compras.

Tres reglas que sostienen el módulo. Romper cualquiera reintroduce un bug que ya
costó encontrar:

1. **No se inventa costo.** Si faltan capas, esa parte no se cuesta y la venta
   queda `costo_incompleto=True`. Costearla con el precio del catálogo abona
   inventario que nunca entró y deja el activo en negativo, con el balance
   diciendo «cuadra» igual.
2. **Ingreso y costo entran juntos o no entran.** Una venta sin costo completo
   no genera asiento de reconocimiento. Reconocer el ingreso con costo parcial
   infla la utilidad bruta en silencio.
3. **Se redondea UNA sola vez**, al escribir `costo_fifo`. Redondear cada capa
   deja un centavo atorado en Inventario por venta, y se acumula.

Al borrar o editar una venta se devuelve el inventario **y se recuestan las
ventas posteriores**: con FIFO, liberar una capa vieja cambia el costo de todo
lo que vino después.

Desde P5 **no hay botón «Facturado»**: compras y gastos se reconocen al
capturarse, y una venta se reconoce cuando su costo está completo. Los paneles
comerciales leen `Venta.costo_de_ventas`, que cae al estimado del catálogo si el
costo falta **o está incompleto**; la contabilidad no la usa nunca, solo
`costo_fifo`. `Movimiento.facturado` quedó como columna muerta: nadie la lee,
y se queda congelada hasta que P11 la borre para que revertir el despliegue
devuelva el reporte que estaba publicado.

```
python manage.py recostear --todo          # rehace el costeo completo
python manage.py recostear --verificar     # no cambia nada, solo audita
python manage.py recostear --solo-pendientes
```

`--verificar` comprueba tres igualdades: que cada venta valga lo que suman sus
capas, que ninguna capa deba más de lo que trajo, y que ninguna venta reconocida
esté sin costo completo. **Correrlo después de cada despliegue que toque
costeo.**

## Alarma de margen (en rama, sin publicar)

Avisa cuando el margen de un producto **baja**. Solo la caída: mezclar las dos
direcciones convierte el aviso en ruido que se aprende a ignorar.

- Compara el mes en curso contra el mes calendario anterior; el umbral es una
  caída relativa, global y configurable en el admin (10% por omisión, en
  `ConfiguracionAlarmas`).
- El margen del mes sale de `Venta.ganancia` sumada por producto, con
  `Venta.objects.comerciales()` —sin cortesías, que no cobran y hundirían el
  número sin que el precio ni el costo hayan cambiado—.
- Un producto que no se vendió en alguno de los dos meses **no aparece**: sin
  base de comparación no se inventa una caída, igual que el costeo no inventa
  un costo.
- Si alguna venta de cualquiera de los dos meses no tiene su costo completo, el
  aviso se marca «· estimado»: ese margen se apoya en el catálogo de HOY, así
  que corregir un ingrediente lo mueve sin que se haya registrado una venta.

Vive en `inventario/alarmas.py` y se muestra en el panel de inventario, detrás
de la misma puerta que las columnas de costo (solo superusuario).

**Limitación conocida, decidida a propósito:** compara un mes en curso —que
puede llevar una sola venta— contra un mes completo, así que a principios de
mes una venta atípica enciende el aviso igual que una subida real de insumos.
La columna de unidades (`40 → 1`) es la pista. Con el volumen de hoy cualquier
piso de unidades dejaría la alarma muda; se revisa cuando haya un mes con
ventas de verdad.

## Pendiente

- **Faltan las compras reales de diez insumos, y ya bloquean el reporte.**
  Medido en producción el 13 de agosto: las 8 ventas están incompletas, así que
  ninguna reconoce ingreso y la 401 sigue en cero. Es a propósito —el ingreso y
  su costo entran juntos o no entran— pero significa que el Estado de
  Resultados lo destapan los tickets, no un despliegue. La lista está en
  BASELINE-COSTEO.md y lo captura Andy.
- Sin despliegue automático (punto único de falla): Vercel no pudo conectar el
  repo por ser de otra cuenta.
- El premio "Latte gratis" no tiene receta ligada. Lo hace Andy.
- Quedan **P7, P9, P10 y P11** del plan de costeo, más lo que Andy ve y aún no
  existe: enlace del libro a la nota, alarma de margen, gráfica de productos,
  PDF de la nota, aviso de premio y cambio de nivel en la nota, y costo
  consolidado por producto. P8 se cayó del plan; el desglose de cuentas lo
  cerró P6.
- Tres asuntos menores anotados en la revisión de P3 y no atendidos: editar una
  compra ya consumida no reajusta su capa, el recosteo masivo corre dentro del
  request, y hay un residuo de un centavo cuando muchas ventas cruzan las mismas
  capas.
- **44 hallazgos de una auditoría** previa sin atender. De ellos, el
  desbordamiento de `CharField` ya se cerró para `Cliente.nombre` y
  `Cliente.notas`; siguen abiertos el `select_for_update` con join nullable, el
  posible deadlock entre `canjear()` y `expirar_puntos()`, y las consultas N+1
  de los paneles.
