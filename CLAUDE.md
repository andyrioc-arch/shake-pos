# shake-pos — contexto del proyecto

Sistema POS para SHAKE: inventario, finanzas, contabilidad de partida doble,
presupuesto y programa de lealtad. Django 6, 5 apps, 174 tests. El README
explica qué hace cada módulo; este archivo cubre el despliegue y la operación.

## Estado

En producción desde el 6 de agosto de 2026: **https://shake-pos.vercel.app**

Todo el trabajo de despliegue vive en la rama **`deploy/vercel`**, que ya está
en GitHub. `main` se mergeó a esta rama el 8 de agosto de 2026, así que aquí
vive también el trabajo de lealtad (código único de cliente, devoluciones de
canje); lo contrario no es cierto: `main` no tiene nada del despliegue.

| Pieza | Dónde |
|---|---|
| App | Vercel, proyecto `shake-pos` (cuenta personal de Rubén, plan Hobby) |
| Base | Supabase, proyecto `shake-pos`, ref `xrimahsxtkfdhenigito`, Postgres 17, us-east-1 |
| Repo | `github.com/andyrioc-arch/shake-pos` |

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

## Publicar

```
vercel deploy --prod --yes
```

Antes: `python manage.py test` (deben pasar 174). Las migraciones no corren en
el despliegue; se aplican aparte con la cadena del session pooler (5432):

```
DATABASE_URL='<session pooler :5432>' python manage.py migrate
```

**Pendiente de aplicar: `lealtad.0002`.** Agrega la columna única `codigo` a
`Cliente` y le asigna uno a cada cliente existente. Hasta que corra, cualquier
vista que toque `Cliente` truena en producción con «column does not exist».

No hay despliegue automático: Vercel no pudo conectar el repo por ser de otra
cuenta. Cada publicación es manual.

## Pendiente

- Rama sin empujar, sin PR.
- Sin despliegue automático (punto único de falla).
- Datos de ejemplo en los 5 módulos; faltan los reales.
- El premio "Latte gratis" no tiene receta ligada, así que no calcula margen.
- **44 hallazgos de una auditoría** sin atender: solo se arregló lo que
  bloqueaba el despliegue. Los relevantes son bugs que aparecen con volumen —un
  `select_for_update` que falla en Postgres por un join nullable, posible
  deadlock por orden de bloqueo invertido entre `canjear()` y
  `expirar_puntos()`, desbordamiento de `CharField` al truncar descripciones, y
  consultas N+1 en los paneles que eran gratis en SQLite y ahora pagan latencia
  de red. Son cambios en la lógica de negocio: consultarlos con Andy.
