#!/usr/bin/env python3
"""
Bot de agendamiento para Prenot@Mi (prenotami.esteri.it)
Consulado de Italia en Montevideo - Turnos de ciudadanía.

Flujo:
  1. Login con las credenciales del .env
  2. Abre la página de reserva del servicio (SERVICE_ID)
  3. Si no hay disponibilidad, reintenta cada CHECK_INTERVAL_SECONDS
  4. Cuando hay disponibilidad: completa el formulario con los datos del CSV,
     elige el PRIMER día disponible del calendario y el PRIMER horario libre,
     acepta la política de privacidad y confirma.
  5. Guarda screenshots de evidencia en cada paso (EVIDENCIA_DIR).

Los campos del formulario se localizan por el TEXTO DE SU ETIQUETA (label),
no por posición ni por orden en el DOM: así el script sigue funcionando aunque
el sitio cambie de lugar los inputs entre cargas. Solo se completan campos
visibles, para no caer en campos-trampa (honeypots) ocultos.
"""

import csv
import os
import random
import re
import sys
import time
import unicodedata
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
from playwright.sync_api import (
    Page,
    TimeoutError as PlaywrightTimeout,
    sync_playwright,
)

BASE_URL = "https://prenotami.esteri.it"
LOGIN_URL = f"{BASE_URL}/Home"
SERVICES_URL = f"{BASE_URL}/Services"

# Selectores centralizados: si el consulado cambia el HTML, ajustar SOLO acá.
SEL = {
    "login_email": "#login-email",
    "login_password": "#login-password",
    "login_submit": "form#login-form button[type=submit], form button[type=submit]",
    # Posibles representaciones de un día disponible en el calendario
    "dias_disponibles": [
        "td.availableDay a",
        "td.availableDay",
        "td.day.available",
        ".vc-day.is-available",
        "td[data-available='true']",
        "table td a.available",
    ],
    # Posibles botones de "mes siguiente"
    "mes_siguiente": [
        "a[data-handler='next']",
        ".ui-datepicker-next",
        "button[aria-label*='next' i]",
        "button[aria-label*='siguiente' i]",
        "button[aria-label*='successivo' i]",
        ".calendar-next",
    ],
    # Horarios: radios o botones dentro del panel de slots
    "slots_horario": [
        "input[type=radio][name*='slot' i]:not([disabled])",
        "input[type=radio][name*='orario' i]:not([disabled])",
        ".time-slot:not(.disabled)",
        "input[type=radio]:not([disabled])",
    ],
    "privacy_check": "#PrivacyCheck, input[name='PrivacyCheck'], input[type=checkbox][id*='rivacy']",
    "confirmar": [
        "#btnPrenotaNoOtp",
        "button#btnPrenota",
        "button[type=submit].button.primary",
        "form button[type=submit]",
    ],
}

# Textos (en it/en/es) con los que Prenot@Mi avisa que no hay turnos.
# El texto real verificado en la sede de Montevideo (hidden #WlNotAvailable):
# "Sorry, all appointments for this service are currently booked. Please check
#  again tomorrow for cancellations or new appointments."
SIN_DISPONIBILIDAD = [
    "all appointments for this service are currently booked",
    "sorry, all appointments",
    "non ci sono disponibilit",       # "Al momento non ci sono disponibilità"
    "no hay disponibilidad",
    "posti esauriti",
]


def log(msg: str) -> None:
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}", flush=True)


def normalizar(texto: str) -> str:
    """minúsculas, sin tildes, espacios colapsados: para comparar etiquetas."""
    texto = unicodedata.normalize("NFKD", texto)
    texto = "".join(c for c in texto if not unicodedata.combining(c))
    texto = re.sub(r"[\s*:]+", " ", texto.lower()).strip()
    return texto


def cargar_datos_csv(ruta: str) -> dict[str, str]:
    """CSV con columnas campo,valor -> dict {etiqueta_normalizada: valor}."""
    datos: dict[str, str] = {}
    with open(ruta, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        columnas = {normalizar(c): c for c in (reader.fieldnames or [])}
        if "campo" not in columnas or "valor" not in columnas:
            raise SystemExit(
                f"El CSV '{ruta}' debe tener las columnas 'campo' y 'valor'. "
                f"Encontradas: {reader.fieldnames}"
            )
        for fila in reader:
            campo = (fila[columnas["campo"]] or "").strip()
            valor = (fila[columnas["valor"]] or "").strip()
            if campo:
                datos[normalizar(campo)] = valor
    if not datos:
        raise SystemExit(f"El CSV '{ruta}' no tiene filas con datos.")
    return datos


class Config:
    def __init__(self) -> None:
        load_dotenv()
        self.email = os.getenv("PRENOTAMI_EMAIL", "")
        self.password = os.getenv("PRENOTAMI_PASSWORD", "")
        self.service_id = os.getenv("SERVICE_ID", "").strip()
        self.headless = os.getenv("HEADLESS", "true").lower() != "false"
        self.intervalo = int(os.getenv("CHECK_INTERVAL_SECONDS", "120"))
        self.max_intentos = int(os.getenv("MAX_ATTEMPTS", "0"))
        self.csv_datos = os.getenv("CSV_DATOS", "datos.csv")
        self.evidencia_dir = Path(os.getenv("EVIDENCIA_DIR", "evidencia"))
        self.max_meses = int(os.getenv("MAX_MESES_CALENDARIO", "8"))

        faltan = [
            nombre
            for nombre, valor in [
                ("PRENOTAMI_EMAIL", self.email),
                ("PRENOTAMI_PASSWORD", self.password),
                ("SERVICE_ID", self.service_id),
            ]
            if not valor
        ]
        if faltan:
            raise SystemExit(
                f"Faltan variables en el .env: {', '.join(faltan)}. "
                "Copiá .env.example a .env y completalo."
            )
        self.evidencia_dir.mkdir(parents=True, exist_ok=True)


def screenshot(page: Page, cfg: Config, nombre: str) -> None:
    ruta = cfg.evidencia_dir / f"{datetime.now().strftime('%Y%m%d-%H%M%S')}-{nombre}.png"
    try:
        page.screenshot(path=str(ruta), full_page=True)
        log(f"Screenshot: {ruta}")
    except Exception as e:  # la evidencia nunca debe tirar abajo el flujo
        log(f"No se pudo sacar screenshot ({nombre}): {e}")


def login(page: Page, cfg: Config) -> None:
    log("Abriendo página de login...")
    page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=60_000)
    page.wait_for_selector(SEL["login_email"], timeout=30_000)
    # Tipear con pequeñas demoras, como un humano
    page.locator(SEL["login_email"]).click()
    page.keyboard.type(cfg.email, delay=random.randint(40, 90))
    page.locator(SEL["login_password"]).click()
    page.keyboard.type(cfg.password, delay=random.randint(40, 90))
    screenshot(page, cfg, "01-login")
    page.locator(SEL["login_submit"]).first.click()
    try:
        page.wait_for_url(re.compile(r"/(UserArea|Services)"), timeout=45_000)
    except PlaywrightTimeout:
        cuerpo = normalizar(page.inner_text("body"))
        screenshot(page, cfg, "01-login-error")
        if "captcha" in cuerpo or "unavailable" in cuerpo:
            raise SystemExit(
                "El sitio pidió verificación adicional (CAPTCHA) o está saturado. "
                "Ejecutá con HEADLESS=false, completá la verificación a mano y "
                "volvé a intentar."
            )
        raise SystemExit("Login falló: revisá email/password en el .env.")
    log("Login OK.")
    screenshot(page, cfg, "02-logueado")


def cerrar_modales(page: Page) -> None:
    """Cierra modales jquery-confirm (el 'OK' del aviso de sin turnos, etc.)."""
    try:
        botones = page.locator(".jconfirm-buttons button")
        if botones.count() and botones.first.is_visible():
            botones.first.click()
            page.wait_for_timeout(500)
    except Exception:
        pass


def hay_disponibilidad(page: Page, cfg: Config) -> bool:
    """Abre la página de reserva y devuelve True si cargó el formulario.

    Comportamiento verificado del sitio: si no hay turnos, el servidor
    redirige a /Services, deja el mensaje en el hidden #WlNotAvailable y lo
    muestra en un modal jquery-confirm.
    """
    url = f"{SERVICES_URL}/Booking/{cfg.service_id}"
    page.goto(url, wait_until="domcontentloaded", timeout=60_000)
    page.wait_for_timeout(2_000)  # dar tiempo al posible modal de "sin turnos"

    # Rebote a /Services => sin turnos
    if re.search(r"/Services/?$", page.url):
        aviso = page.locator("#WlNotAvailable")
        if aviso.count():
            try:
                msg = aviso.first.input_value()
                if msg:
                    log(f"Mensaje del sitio: {msg[:100]}")
            except Exception:
                pass
        cerrar_modales(page)
        return False

    cuerpo = normalizar(page.inner_text("body"))
    if any(patron in cuerpo for patron in SIN_DISPONIBILIDAD):
        cerrar_modales(page)
        return False
    return True


def llenar_formulario(page: Page, cfg: Config, datos: dict[str, str]) -> None:
    """
    Completa cada campo buscándolo por el texto de su <label>.
    Robusto ante cambios de posición/orden de los inputs.
    """
    labels = page.locator("label")
    usados: set[str] = set()

    for i in range(labels.count()):
        label = labels.nth(i)
        try:
            if not label.is_visible():
                continue
            texto = normalizar(label.inner_text())
        except Exception:
            continue
        if not texto:
            continue

        # Buscar en el CSV una clave igual o contenida en la etiqueta
        valor = None
        clave_usada = None
        for clave, v in datos.items():
            if clave == texto or clave in texto or texto in clave:
                valor, clave_usada = v, clave
                break
        if valor is None:
            continue

        # Localizar el control asociado: por atributo for, o el siguiente
        # input/select/textarea dentro del mismo contenedor.
        control = None
        for_attr = label.get_attribute("for")
        if for_attr:
            id_escapado = re.sub(r"([^a-zA-Z0-9_-])", r"\\\1", for_attr)
            candidato = page.locator(f"#{id_escapado}")
            if candidato.count() and candidato.first.is_visible():
                control = candidato.first
        if control is None:
            candidato = label.locator(
                "xpath=following::*[self::input or self::select or self::textarea][1]"
            )
            if candidato.count() and candidato.first.is_visible():
                control = candidato.first
        if control is None:
            log(f"  ! No encontré el control para la etiqueta '{texto}'")
            continue

        tag = (control.evaluate("el => el.tagName") or "").lower()
        tipo = (control.get_attribute("type") or "").lower()

        try:
            if tag == "select":
                # Intentar por texto visible de la opción; si no, por value
                try:
                    control.select_option(label=valor)
                except Exception:
                    control.select_option(value=valor)
            elif tipo == "checkbox":
                if valor.lower() in ("si", "sí", "yes", "true", "1", "x"):
                    control.check()
            elif tipo == "radio":
                # Buscar el radio del grupo cuya etiqueta coincida con el valor
                name = control.get_attribute("name") or ""
                grupo = page.locator(f"input[type=radio][name='{name}']")
                marcado = False
                for j in range(grupo.count()):
                    r = grupo.nth(j)
                    rid = r.get_attribute("id")
                    if rid:
                        et = page.locator(f"label[for='{rid}']")
                        if et.count() and normalizar(valor) in normalizar(et.first.inner_text()):
                            r.check()
                            marcado = True
                            break
                if not marcado:
                    control.check()
            else:
                control.click()
                control.fill("")
                page.keyboard.type(valor, delay=random.randint(25, 60))
            usados.add(clave_usada)
            log(f"  ✓ '{texto}' <- '{valor}'")
        except Exception as e:
            log(f"  ! Error completando '{texto}': {e}")

    sin_usar = set(datos) - usados
    if sin_usar:
        log(f"  Aviso: campos del CSV que no aparecieron en el formulario: {sorted(sin_usar)}")


def elegir_primer_dia(page: Page, cfg: Config) -> bool:
    """Busca el primer día disponible, avanzando de mes hasta MAX_MESES."""
    for mes in range(cfg.max_meses):
        for sel in SEL["dias_disponibles"]:
            dias = page.locator(sel)
            try:
                n = dias.count()
            except Exception:
                n = 0
            if n:
                dias.first.click()
                log(f"Día disponible seleccionado (selector '{sel}', mes +{mes}).")
                page.wait_for_timeout(1_500)
                return True
        # Sin días este mes: intentar pasar al siguiente
        avanzo = False
        for sel in SEL["mes_siguiente"]:
            btn = page.locator(sel)
            if btn.count() and btn.first.is_visible():
                btn.first.click()
                page.wait_for_timeout(1_200)
                avanzo = True
                break
        if not avanzo:
            break
    return False


def elegir_primer_horario(page: Page) -> bool:
    for sel in SEL["slots_horario"]:
        slots = page.locator(sel)
        try:
            n = slots.count()
        except Exception:
            n = 0
        for j in range(n):
            slot = slots.nth(j)
            try:
                if slot.is_visible() or slot.get_attribute("type") == "radio":
                    slot.check() if slot.get_attribute("type") == "radio" else slot.click()
                    log(f"Horario seleccionado (selector '{sel}').")
                    return True
            except Exception:
                continue
    # Muchos servicios de ciudadanía no tienen elección de horario: no es error
    log("No se encontró selector de horario (puede que el servicio no lo requiera).")
    return False


def confirmar(page: Page, cfg: Config) -> None:
    privacy = page.locator(SEL["privacy_check"])
    if privacy.count():
        try:
            privacy.first.check()
            log("Checkbox de privacidad marcado.")
        except Exception as e:
            log(f"No pude marcar privacidad: {e}")

    screenshot(page, cfg, "05-antes-de-confirmar")
    for sel in SEL["confirmar"]:
        btn = page.locator(sel)
        if btn.count() and btn.first.is_visible():
            btn.first.click()
            log("Click en confirmar enviado.")
            break
    else:
        raise RuntimeError("No encontré el botón de confirmación.")

    # A veces aparece un modal final de confirmación ("Sei sicuro?" / OK)
    page.wait_for_timeout(2_000)
    for sel in ["button:has-text('OK')", "button:has-text('Ok')",
                "button:has-text('Conferma')", "button:has-text('Confirm')"]:
        btn = page.locator(sel)
        if btn.count() and btn.first.is_visible():
            btn.first.click()
            break
    page.wait_for_load_state("networkidle", timeout=60_000)
    screenshot(page, cfg, "06-resultado")


def main() -> None:
    cfg = Config()
    datos = cargar_datos_csv(cfg.csv_datos)
    log(f"Datos cargados del CSV ({len(datos)} campos).")

    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            headless=cfg.headless,
            args=["--disable-blink-features=AutomationControlled"],
        )
        context = browser.new_context(
            locale="es-UY",
            timezone_id="America/Montevideo",
            viewport={"width": 1366, "height": 768},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
            ),
        )
        page = context.new_page()
        page.set_default_timeout(30_000)

        try:
            login(page, cfg)

            intento = 0
            while True:
                intento += 1
                log(f"Intento {intento}: verificando disponibilidad...")
                try:
                    if hay_disponibilidad(page, cfg):
                        log("¡Hay disponibilidad! Formulario cargado.")
                        screenshot(page, cfg, "03-formulario")
                        break
                    log("Sin turnos por ahora.")
                except PlaywrightTimeout:
                    log("Timeout cargando la página; reintento.")

                if cfg.max_intentos and intento >= cfg.max_intentos:
                    log("Se alcanzó MAX_ATTEMPTS. Fin.")
                    return
                espera = cfg.intervalo + random.randint(5, 30)
                log(f"Esperando {espera}s antes del próximo intento...")
                time.sleep(espera)
                # Refrescar sesión si expiró
                if "/Home" in page.url or "login" in page.url.lower():
                    log("Sesión expirada: relogueando...")
                    login(page, cfg)

            llenar_formulario(page, cfg, datos)
            screenshot(page, cfg, "04-formulario-completo")

            if not elegir_primer_dia(page, cfg):
                log("No encontré días disponibles en el calendario "
                    "(pueden haberse agotado mientras completábamos el formulario).")
                screenshot(page, cfg, "04b-sin-dias")
                return
            elegir_primer_horario(page)

            confirmar(page, cfg)
            log("Proceso terminado. Revisá los screenshots en la carpeta de "
                "evidencia y tu email: Prenot@Mi envía la confirmación con el "
                "comprobante en PDF.")
        finally:
            screenshot(page, cfg, "99-final")
            context.close()
            browser.close()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        log("Interrumpido por el usuario.")
        sys.exit(130)
