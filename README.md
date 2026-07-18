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
   - `SERVICE_ID`: ya viene con `5679`, el servicio de **Cittadinanza per
     discendenza maggiorenni (L. 74/2025)** de la sede de Montevideo
     (verificado contra la página real en 07/2026). Otros servicios de la sede:

     | ID   | Servicio |
     |------|----------|
     | 5679 | Ciudadanía por descendencia — mayores (L. 74/2025) |
     | 5896 | Ciudadanía hijos menores (turnos a partir de 06/2026) |
     | 5126 | Pasaporte |
     | 5199 | Cédula de identidad electrónica (CIE) |
     | 5322 | Estado civil (matrimonio, divorcio, defunción) |
     | 4947 | Legalización de traducciones |
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

## El flujo real de reserva (y cómo lo cubre el bot)

Flujo verificado con capturas reales del servicio de Ciudadanía:

1. **Página de formulario**: "Tipo de reserva" (desplegable, p.ej. *Reserva
   unica*), "Notas para la Sede" (texto libre), checkbox de privacidad y botón
   **AVANZAR** → el bot completa todo desde `datos.csv`, marca privacidad y
   avanza. Si el formulario pide subir documentos, Prenot@Mi solo acepta
   **PDF**: una fila del CSV con una ruta de archivo hace que el bot lo suba.
2. **Página de calendario**: "Selezionare una data e una fascia oraria
   disponibile" — días **verdes** = disponibles, rojos = no, azul =
   seleccionado; al elegir día aparecen las fascias horarias al costado
   (p.ej. `08:00 - 09:00 (2)`) y el botón **PRENOTA** → el bot elige el primer
   día verde (detectándolo por clase CSS o directamente por el color de fondo),
   la primera fascia horaria y confirma.
3. **Código OTP**: al confirmar puede llegar un código numérico por email que
   hay que ingresar para finalizar → ver sección siguiente.
4. Email final de confirmación con los datos del turno.

### OTP automático

Si configurás `IMAP_USER`/`IMAP_PASSWORD` en el `.env` (para Gmail: una
[contraseña de aplicación](https://myaccount.google.com/apppasswords), nunca tu
password real), el bot espera el email de `esteri.it`, extrae el código y lo
ingresa solo. Si no, te lo pide por consola: tenés que estar mirando la
terminal en ese momento, porque el turno no se confirma sin el código.

## Uso

```bash
python prenotami_bot.py
```

El bot queda reintentando cada `CHECK_INTERVAL_SECONDS` (más un jitter
aleatorio) hasta encontrar turno. Al confirmar, Prenot@Mi te envía por email el
comprobante en PDF — revisalo siempre: **la fuente de verdad es ese email**, no
el log del bot.

> **Después de conseguir el turno**: entre 10 y 3 días antes de la fecha tenés
> que entrar a **Mis reservas** y confirmar tu asistencia, o el turno se
> pierde. Eso el bot no lo hace: anotalo en tu calendario.

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
