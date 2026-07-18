# Bot de turnos Prenot@Mi — Ciudadanía italiana (Consulado de Montevideo)

Script en Python + Playwright (headless) que:

1. Se loguea en [prenotami.esteri.it](https://prenotami.esteri.it) con tu cuenta.
2. Verifica disponibilidad del servicio de ciudadanía y reintenta automáticamente
   si no hay turnos.
3. Cuando aparece disponibilidad, completa el formulario con los datos de un CSV,
   selecciona **el primer día y horario disponible** del calendario y confirma.
4. Guarda screenshots de evidencia de cada paso en `evidencia/`.

## Cómo esquiva el "barajado" de campos

El sitio cambia la posición/orden de los inputs entre cargas para romper scripts
simples. Este bot **no usa posiciones ni índices**: localiza cada campo por el
texto de su etiqueta (`<label>`), y solo completa campos **visibles** (los
formularios anti-bot suelen incluir campos ocultos "trampa" que un humano nunca
llenaría). Por eso el CSV mapea *texto de etiqueta → valor*.

## Instalación

```bash
python -m venv .venv && source .venv/bin/activate   # opcional
pip install -r requirements.txt
playwright install chromium
```

## Configuración

1. Copiá `.env.example` a `.env` y completá:
   - `PRENOTAMI_EMAIL` / `PRENOTAMI_PASSWORD`: tu cuenta de Prenot@Mi.
   - `SERVICE_ID`: entrá logueado a **Reservar** (Prenota) sobre el servicio de
     ciudadanía; la URL queda `https://prenotami.esteri.it/Services/Booking/<ID>`.
     Ese número es el `SERVICE_ID`.
2. Copiá `datos.ejemplo.csv` a `datos.csv` y cargá tus datos. Cada fila es
   `campo,valor`, donde `campo` es el texto de la etiqueta tal como se ve en el
   formulario (no hace falta que sea exacto: la comparación ignora mayúsculas,
   tildes y coincidencias parciales).

```csv
campo,valor
Indirizzo,"Av. 18 de Julio 1234, apto 501, Montevideo"
Numero di persone,1
```

> Para saber qué campos pide el formulario de tu servicio, corré una vez el bot
> con `HEADLESS=false` cuando haya disponibilidad, o mirá el screenshot
> `03-formulario` en `evidencia/`.

## Uso

```bash
python prenotami_bot.py
```

El bot queda reintentando cada `CHECK_INTERVAL_SECONDS` (más un jitter
aleatorio) hasta encontrar turno. Al confirmar, Prenot@Mi te envía por email el
comprobante en PDF — revisalo siempre: **la fuente de verdad es ese email**, no
el log del bot.

### Tip: cuándo correrlo

Los cupos de ciudadanía en Montevideo suelen liberarse en tandas (habitualmente
a medianoche de Italia, 19:00–20:00 hora Uruguay según la época del año).
Conviene lanzar el bot unos minutos antes de esos horarios con un intervalo
moderado, en lugar de dejarlo martillando el sitio 24/7.

## Si algo falla

- **CAPTCHA o "Unavailable" en el login**: el sitio detectó tráfico inusual o
  está saturado. Ejecutá con `HEADLESS=false`, resolvé la verificación a mano y
  relanzá. El bot no intenta romper CAPTCHAs.
- **Cambió el HTML del sitio**: todos los selectores están centralizados en el
  diccionario `SEL` al inicio de `prenotami_bot.py`; ajustá ahí sin tocar la
  lógica.
- **El formulario tiene un campo que el bot no llenó**: aparece en el log como
  `! No encontré el control...` o en "campos del CSV que no aparecieron".
  Agregá/corregí la fila correspondiente en `datos.csv` usando el texto de la
  etiqueta que se ve en el screenshot del formulario.

## Advertencias

- Usalo solo con **tu propia cuenta y tus propios datos**, para tu trámite.
- No bajes el intervalo de chequeo a valores agresivos: además de ser mala
  ciudadanía digital, Prenot@Mi bloquea cuentas e IPs que hacen scraping
  intensivo, y perderías el acceso justo cuando aparezcan turnos.
- El sitio cambia seguido sus defensas y su HTML; es esperable tener que
  ajustar selectores cada tanto.
