# 🥤 Habits Shakes — Control de Inventario (Django)

Backend en Django (fase 1: modelos + admin) que replica tu Excel:
ingredientes, empaque, recetas editables, compras, ventas e inventario
con alertas de stock para preparar al menos 5 shakes de cada receta.

## Qué incluye

- **Ingredientes** — 31 insumos + 4 de empaque (vaso, tapa, popote, sticker),
  editables. El costo por unidad de receta se calcula solo.
- **Recetas** — Las 6 recetas Habits con sus ingredientes editables en línea
  (incluyen el empaque). Muestra costo, ganancia y margen automáticos.
- **Compras** — Registro de compras; alimenta el inventario.
- **Ventas** — Registro de ventas; calcula ingreso, costo y ganancia.
- **Inventario** — En la lista de Ingredientes ves stock disponible y un
  semáforo 🔴 FALTA / ✅ OK según el mínimo para 5 shakes de cada receta.

## Cómo correrlo

```bash
# 1. (Opcional) crear entorno virtual
python -m venv venv
source venv/bin/activate          # Windows: venv\Scripts\activate

# 2. Instalar dependencias
pip install -r requirements.txt

# 3. Preparar la base de datos (SQLite, ya configurada)
python manage.py migrate

# 4. Cargar datos de ejemplo (ingredientes, empaque, recetas, etc.)
python manage.py seed --reset            # inventario: ingredientes, recetas, etc.
python manage.py seed_finanzas --reset   # finanzas: costos fijos del negocio

# 5. Crear tu usuario administrador
python manage.py createsuperuser

# 6. Arrancar el servidor
python manage.py runserver
```

Abre http://127.0.0.1:8000/admin/ e inicia sesión.

## Notas

- Base de datos: **SQLite** (archivo `db.sqlite3`, sin configuración).
- El comando `seed` se puede correr de nuevo con `--reset` para reiniciar.
- Ingredientes y recetas son 100% editables desde el admin; al cambiar un
  costo o una cantidad, los totales y márgenes se recalculan al instante.

---

## Fase 2 — Finanzas (módulo independiente)

### Estructura del proyecto (módulos)

El proyecto está dividido en dos apps independientes, al estilo de los
módulos de un ERP:

- **`inventario`** — ingredientes, empaque, recetas, compras, ventas e
  inventario con alertas de stock.
- **`finanzas`** — costos fijos, inversión inicial, movimientos de efectivo
  y el panel financiero (punto de equilibrio, flujo de efectivo, recuperación).

La app `finanzas` lee las ventas y compras de `inventario` para sus cálculos,
pero mantiene sus propios modelos y su propia migración. Así puedes hacer
crecer cada módulo por separado.


Se agregaron tres secciones más al admin y un panel financiero:

- **Costos fijos (mensuales)** — renta, sueldos, mercadotecnia, etc.
  Ya vienen precargados los tuyos (renta $4,500 · sueldos $14,400 ·
  mercadotecnia $3,500 = $22,400/mes).
- **Inversión inicial** — captura aquí lo que gastaste para arrancar
  (equipo, mobiliario, depósito, permisos...). Es lo que se busca recuperar.
- **Movimientos de efectivo (extra)** — entradas/salidas que no son ventas
  ni compras (inyección de capital, retiro, reparación...).

### Panel financiero

Con el servidor corriendo, entra a:

    http://127.0.0.1:8000/admin/finanzas/

Verás:
- **Margen de contribución** promedio por shake.
- **Punto de equilibrio**: cuántos shakes vender al mes (y por día) para
  cubrir los costos fijos.
- **Flujo de efectivo mensual**: dinero real que entra y sale cada mes.
- **Punto de recuperación**: en qué mes recuperas tu inversión inicial
  (aparece en cuanto captures la inversión).

> Nota: el punto de equilibrio actual (~426 shakes/mes ≈ 14 al día) sale de
> tus costos fijos reales y el margen promedio de las recetas de ejemplo.
> Cambia al instante cuando ajustes precios, costos o costos fijos en el admin.

---

## Fase 3 — Contabilidad (módulo independiente, partida doble)

Tercera app del sistema, al estilo de un ERP. Lleva contabilidad formal de
partida doble y genera asientos automáticamente.

### Qué incluye

- **Catálogo de cuentas** — plan contable básico pre-cargado (Caja, IVA,
  Ventas, gastos por tipo, Capital). Editable.
- **Facturas de venta** — registras subtotal y si aplica IVA 16%; el total
  y el asiento contable se generan solos.
- **Facturas de gasto** — igual, clasificadas por tipo (insumos, renta,
  servicios, mercadotecnia, sueldos, otros).
- **Asientos contables (pólizas)** — cada uno con sus movimientos debe/haber,
  con un indicador ✅/❌ de si cuadra. Los automáticos salen de las facturas;
  también puedes capturar asientos manuales.
- **Reportes** — balanza de comprobación, estado de resultados y balance
  general, calculados en tiempo real desde los asientos.

### Asientos automáticos (cómo funcionan)

Venta de contado con IVA:
    DEBE  Caja y bancos     (total)
    HABER Ventas            (subtotal)
    HABER IVA por pagar     (iva)

Gasto de contado con IVA:
    DEBE  Cuenta de gasto   (subtotal)
    DEBE  IVA acreditable   (iva)
    HABER Caja y bancos     (total)

### Cómo verlo

Con el servidor corriendo:

    http://127.0.0.1:8000/admin/contabilidad/reportes/

Y para cargar el catálogo + facturas de ejemplo:

    python manage.py seed_contabilidad --reset

> Nota fiscal: bajo RESICO el SAT calcula tus impuestos con base en tu
> facturación (CFDI), no en esta contabilidad. Este módulo es una herramienta
> de gestión para entender tu negocio (patrimonio, utilidad real, IVA), no un
> sustituto de tus obligaciones ante el SAT ni de tu contador.

---

## Fase 4 — Presupuesto (módulo independiente)

Cuarta app del sistema. Permite planear y comparar contra lo real.

### Qué incluye

- **Presupuesto de ventas** — meta en $ por mes (ej. mayo 2025 = $60,000).
- **Presupuesto de gastos** — meta en $ por categoría y por mes
  (insumos, renta, servicios, mercadotecnia, sueldos, otro).
- **Panel comparativo** presupuesto vs. real, con diferencia y % de cumplimiento.

### De dónde sale "lo real"

- **Ventas reales**: de las ventas del módulo `inventario`.
- **Gastos reales**: de las facturas de gasto del módulo `contabilidad`
  (ya clasificadas por categoría).

No capturas nada dos veces: solo defines las metas y el panel compara solo.

### Cómo leerlo

- En **ventas**, verde = vendiste más que la meta (bueno).
- En **gastos**, verde = gastaste menos que la meta (bueno);
  diferencia positiva = te pasaste del presupuesto.

### Cómo verlo

    http://127.0.0.1:8000/admin/presupuesto/panel/

Cargar presupuesto de ejemplo:

    python manage.py seed_presupuesto --reset

---

## Fase 5 — Lealtad (módulo independiente)

Programa de lealtad digital: el cliente se identifica con su celular, acumula
puntos con cada compra, canjea premios y recibe mensajes automáticos por
WhatsApp. **Todo el programa es editable desde el panel**: niveles, premios,
regla de puntos, textos de los mensajes y cuándo se envían. No hay reglas de
negocio escritas en el código.

    http://127.0.0.1:8000/lealtad/

Cargar el programa con los valores base (niveles, premios, mensajes y
automatizaciones):

    python manage.py seed_lealtad --reset

### Cómo acumula puntos

**1 punto por cada $10 MXN** (editable). Una compra de $130 da 13 puntos.
Los puntos **vencen a los 12 meses** de ganarlos y se consumen por orden de
caducidad: primero el lote que expira antes.

Hay dos saldos y hacen cosas distintas:

- **Puntos disponibles** — se gastan al canjear un premio.
- **Puntos acumulados de por vida** — nunca bajan y son los que definen el
  nivel del cliente. Canjear no te baja de nivel.

### Niveles y premios (precargados, todos editables)

| Nivel | Puntos de por vida | Premio | Cuesta | Te cuesta | Vale |
|---|---|---|---|---|---|
| 🌱 Inicio | 0 | — | — | — | — |
| 💛 Fan | 100 | Latte gratis | 100 pts | ~$30 | $90 |
| 🧡 Power | 200 | Smoothie al 50% | 200 pts | ~$65 | $65 |
| 💗 Elite | 350 | Smoothie gratis | 350 pts | ~$80 | $130 |
| 💜 VIP | 500 | Premio VIP a elegir | 500 pts | ~$80 | $130 |

El costo y el valor de cada premio **se calculan solos** desde la receta ligada
en el módulo `inventario`. Si cambias un ingrediente, el margen del premio se
actualiza al instante.

### En la caja

El formulario de venta tiene un campo de **celular del cliente** (opcional).
Al teclearlo aparece quién es, cuántos puntos tiene y qué puede canjear. Al
guardar la venta:

- si el número no existe, se crea el cliente y se le manda la bienvenida;
- se acumulan los puntos y se disparan las automatizaciones;
- el comprobante muestra los puntos ganados y el QR de su tarjeta.

Las **cortesías no acumulan puntos** (no se cobraron). Si algo falla en el
programa de lealtad, la venta se registra igual: nunca se cae una venta por
culpa de los puntos.

### La tarjeta del cliente

- **Registro autoservicio**: `/unete/` — pon el QR en el mostrador y el cliente
  se da de alta solo con su celular, nombre y cumpleaños.
- **Tarjeta digital**: `/t/<token>/` — puntos, nivel, barra de progreso,
  catálogo de premios, historial y su código QR. Es pública (sin contraseña) y
  se instala en la pantalla de inicio del celular.
- **Google Wallet**: si configuras las credenciales aparece el botón "Guardar
  en Google Wallet" y el pase se actualiza solo cuando cambian sus puntos.

### Automatizaciones

Cada regla es un registro editable en `/lealtad/mensajes/` con su disparador,
su condición, su horario y su anti-spam. Vienen 9 precargadas y activas:

| Regla | Se dispara | Condición |
|---|---|---|
| Bienvenida | al registrarse | una sola vez |
| Aviso de compra y puntos | cada compra | — |
| Gracias por tu primera compra | primera compra | una sola vez |
| Ya puedes canjear un premio | al alcanzar un premio | no repetir 15 días |
| Cerca de tu premio | programada, 12:00 | ≥ 80% del premio |
| Recuperar inactivos | programada, 11:00 | 30 días sin comprar |
| Cumpleaños | programada, 10:00 | 7 días antes |
| Bienvenida a VIP | al alcanzar el nivel VIP | una sola vez |
| Puntos por caducar | programada, 11:00 | caducan en 15 días |

Puedes apagarlas, cambiarles el texto, la hora, los días de la semana o crear
las tuyas sin tocar código.

**El latido del programa** lo corre un solo comando cada 10 minutos: caduca los
puntos vencidos, expira los canjes que nadie recogió, evalúa las reglas
programadas y despacha la bandeja. Es idempotente, correrlo de más no duplica
nada. **Se necesita aunque no mandes ni un mensaje**, porque es lo que hace
caducar los puntos.

En esta Mac ya está instalado como agente de launchd:

    cp deploy/mx.shake.lealtad.plist ~/Library/LaunchAgents/
    launchctl load ~/Library/LaunchAgents/mx.shake.lealtad.plist

    launchctl list | grep lealtad        # ver que esté vivo
    tail -f deploy/lealtad_run.log       # ver qué ha hecho

En el servidor va por cron (`deploy/crontab.txt`):

    */10 * * * * cd /srv/habits && .venv/bin/python manage.py lealtad_run

Mientras tanto, el botón "Despachar la bandeja" del panel hace lo mismo a mano.

### Mensajería

De fábrica el proveedor es **simulado**: los mensajes se generan y se registran
en la bandeja pero no salen, así puedes probar el programa completo sin
contratar nada. Cada mensaje trae un enlace `wa.me` para mandarlo a mano.

Para encender WhatsApp de verdad, define estas variables de entorno y cambia el
proveedor en el admin → *Configuración del programa*:

    WHATSAPP_TOKEN=<token permanente de tu app de Meta>
    WHATSAPP_PHONE_NUMBER_ID=<ID del número en WhatsApp Business>
    WHATSAPP_VERIFY_TOKEN=<el que tú inventes para el webhook>
    WHATSAPP_APP_SECRET=<clave secreta de la app, en Meta → Configuración → Básica>

Pasos del alta en Meta:

1. Crea una app en developers.facebook.com y agrégale *WhatsApp*.
2. Verifica tu negocio y conecta un número dedicado (no uno personal).
3. Copia el *Phone Number ID* y genera un token permanente.
4. Da de alta el webhook apuntando a `https://tu-dominio/api/lealtad/webhooks/whatsapp`
   con tu `WHATSAPP_VERIFY_TOKEN`, y suscríbelo al campo `messages`. Así llegan
   los estados de entregado/leído y las bajas. **Sin `WHATSAPP_APP_SECRET` el
   webhook rechaza todo**: ese endpoint da de baja clientes, así que se verifica
   la firma HMAC de cada petición y no se deja abierto.
5. Sube las plantillas a aprobación (el texto de cada una está en
   `/lealtad/mensajes/`) y escribe su nombre aprobado en la plantilla.

Twilio queda disponible como respaldo de SMS con `TWILIO_ACCOUNT_SID`,
`TWILIO_AUTH_TOKEN` y `TWILIO_FROM`.

Los clientes que respondan **BAJA** o **STOP** dejan de recibir mensajes
automáticamente.

### Google Wallet

    GOOGLE_WALLET_ISSUER_ID=<ID de emisor de la consola de Google Wallet>
    GOOGLE_WALLET_SERVICE_ACCOUNT=/ruta/al/service-account.json

La cuenta de emisor es gratuita: se pide en pay.google.com/business/console y se
liga a una cuenta de servicio de Google Cloud con el permiso de Wallet. Sin esto
configurado la tarjeta web funciona igual, solo no aparece el botón.

### API para un ERP externo

Si algún día las ventas vienen de otro sistema, la API ya está lista. Genera un
token en el admin → *Configuración del programa* → *Token de la API* y úsalo así:

    curl -X POST http://127.0.0.1:8000/api/lealtad/purchases \
      -H "Authorization: Bearer <token>" \
      -H "Content-Type: application/json" \
      -d '{"phone_number":"9991234567","ticket_number":"A12345",
           "purchase_amount":260,"purchase_date":"2026-06-30",
           "items":[{"sku":"SMOOTHIE","qty":2,"price":130}]}'

| Endpoint | Qué hace |
|---|---|
| `POST /api/lealtad/customers` | Alta o actualización de un cliente |
| `POST /api/lealtad/purchases` | Registra la compra y acumula puntos |
| `GET /api/lealtad/customers/<id>/points` | Saldo, nivel y premios alcanzables |
| `POST /api/lealtad/rewards/redeem` | Canjea un premio |
| `POST /api/lealtad/campaigns/send` | Crea y encola una campaña |
| `GET/POST /api/lealtad/webhooks/whatsapp` | Verificación y estados de Meta |

`POST /purchases` es **idempotente por `ticket_number`**: si el ERP reintenta,
no se duplican los puntos.

### Paneles del módulo

| Ruta | Qué ves |
|---|---|
| `/lealtad/` | Clientes, puntos vivos, tasa de canje y % del margen que consume el programa |
| `/lealtad/clientes/` | Búsqueda, ficha, canje de premios y ajuste manual de puntos |
| `/lealtad/premios/` | Premios, niveles, regla de puntos y promociones — todo editable |
| `/lealtad/mensajes/` | Automatizaciones, textos, campañas y bandeja de salida |
| `/lealtad/marketing/` | Enviados, leídos, conversiones y ventas atribuidas |
| `/lealtad/metricas/` | Frecuencia, ticket promedio, retención 30/60/90, LTV y ROI |

### El número que importa

El objetivo es subir la frecuencia de visita **sin comprometer más del 5% del
margen bruto**. El panel lo calcula solo: toma el margen real de las ventas de
los miembros (desde las recetas de `inventario`) y lo compara con el costo de
los premios entregados. Si te pasas del 5%, el número se pone en rojo.

---

## Resumen de módulos del sistema

1. **inventario** — ingredientes, empaque, recetas, compras, ventas, stock.
2. **finanzas** — costos fijos, inversión, punto de equilibrio, flujo, recuperación.
3. **contabilidad** — partida doble, facturas, asientos automáticos, balanza,
   estado de resultados, balance general.
4. **presupuesto** — metas de ventas y gastos, comparación presupuesto vs. real.
5. **lealtad** — clientes, puntos, premios, tarjeta digital y automatizaciones.

### Paneles disponibles
- Finanzas:     /admin/finanzas/
- Contabilidad: /admin/contabilidad/reportes/
- Presupuesto:  /admin/presupuesto/panel/
- Lealtad:      /lealtad/

### Comandos de carga de datos de ejemplo
    python manage.py seed --reset
    python manage.py seed_finanzas --reset
    python manage.py seed_contabilidad --reset
    python manage.py seed_presupuesto --reset
    python manage.py seed_lealtad --reset

---

## Tests automatizados

El proyecto incluye 149 tests que cubren la lógica crítica, las vistas/paneles
y los comandos de carga de datos de los 5 módulos.

Para correrlos:

    python manage.py test

Para correr solo un módulo:

    python manage.py test contabilidad
    python manage.py test inventario
    python manage.py test finanzas
    python manage.py test presupuesto
    python manage.py test lealtad

### Qué cubren

- **inventario**: costo por receta, margen, stock disponible, consumo por
  ventas, alertas de faltante, autocompletado de precio en ventas.
- **contabilidad**: catálogo idempotente, IVA, asientos automáticos que
  cuadran, detección de asientos descuadrados, validación de movimientos,
  borrado en cascada, y que re-guardar facturas no deje asientos huérfanos.
- **finanzas**: total de costos fijos, inversión, margen de contribución,
  punto de equilibrio, detección de recuperación de inversión.
- **presupuesto**: comparativo de ventas y gastos contra lo real.
- **lealtad**: normalización de teléfonos, cálculo de puntos, caducidad FIFO a
  12 meses, canjes (saldo, límite por cliente, nivel mínimo), cada disparador de
  automatización con su anti-repetición, atribución de ventas, idempotencia de
  la API, y que una venta nunca se caiga por culpa del programa.

### Bugs corregidos (encontrados por los tests)

1. **Asientos duplicados al editar una factura** — al re-guardar una factura
   se creaba un asiento nuevo sin borrar el anterior, dejando huérfanos.
   Corregido usando limpieza por referencia.
2. **Asiento no se borraba al eliminar su factura** — el borrado dependía de
   un campo que podía estar desligado. Corregido con borrado por referencia.
3. **Pantallas de admin truenan en Django reciente** — varias columnas usaban
   `format_html` sin argumentos, lo que en versiones nuevas de Django lanza un
   error y rompe el listado de ingredientes, recetas y asientos. Corregido
   usando `mark_safe` para el HTML estático. Detectado por los tests de vista.

---

## Ventas personalizadas (sustituciones y extras)

Al registrar una venta puedes ajustar la receta para ese cliente:

### Sustituciones
Cambiar un ingrediente por otro (ej. leche de avena → leche de almendra),
manteniendo la misma cantidad. El **costo** de la venta se recalcula según la
diferencia de precio entre ambos ingredientes. El precio de venta NO cambia.
El inventario descuenta el ingrediente sustituto, no el original.

### Extras (catálogo editable)
Agregados que se cobran aparte (espresso, creatina...). Cada extra tiene:
- un **ingrediente** que consume del inventario y una cantidad,
- un **cargo** que se suma al precio de venta.

Vienen pre-cargados "Shot de espresso" (+$12) y "Creatina" (+$15). Puedes
agregar, editar o desactivar extras en el admin → **Extras (catálogo)**.

### Cómo se calcula cada venta
- Costo = costo receta base − ingrediente quitado + ingrediente sustituto + extras
- Precio = precio receta base + cargo de los extras

En el listado de ventas, la columna **Personalizada** marca 🔄 si tiene
sustituciones y ➕ si tiene extras.
