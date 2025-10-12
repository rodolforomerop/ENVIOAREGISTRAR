# main.py - Script para GitHub Actions que se conecta a Firestore y consulta WOM
# Requisitos (requirements.txt):
# firebase-admin==6.*
# requests==2.*
# google-cloud-firestore==2.*
# (Opcional fallback) selenium==4.*, webdriver-manager==4.*, selenium-stealth==1.*

import os
import json
import base64
import time
import html
from datetime import datetime, timezone

import requests

# Firebase
import firebase_admin
from firebase_admin import credentials, firestore

# --- (Opcional) Selenium fallback ---
USE_SELENIUM_FALLBACK = True
try:
    from selenium import webdriver
    from selenium.webdriver.chrome.service import Service as ChromeService
    from webdriver_manager.chrome import ChromeDriverManager
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.webdriver.support import expected_conditions as EC
    from selenium.common.exceptions import TimeoutException
    from selenium_stealth import stealth
except Exception:
    USE_SELENIUM_FALLBACK = False

# ===================== CONFIG =====================
FIREBASE_CREDENTIALS_B64 = os.environ.get('FIREBASE_CREDENTIALS_B64')
REGISTRATION_API_KEY = os.environ.get('REGISTRATION_API_KEY')
HOST_URL = os.environ.get('HOST_URL', 'https://registroimeimultibanda.cl')

COLECCION_FIRESTORE = "registros"
CAMPO_ESTADO = "status"
ESTADO_A_BUSCAR = "En Proceso"
CAMPO_IMEI = "imei1"

URL_WOM_PAGE = "https://sucursalmiwom.wom.cl/listablanca/sello-multibanda/sello-multibandas.jsp#"
URL_WOM_API  = "https://sucursalmiwom.wom.cl/listablanca/api/whitelist/consultaImeiInfo/v2"

REQUEST_TIMEOUT = 20
MAX_REINTENTOS = 2
PAUSA_ENTRE_DOCS = 0.8  # segundos
# ==================================================


def inicializar_firebase():
    """Inicializa la conexión con Firebase usando las credenciales de los secretos."""
    if not FIREBASE_CREDENTIALS_B64:
        raise ValueError("FIREBASE_CREDENTIALS_B64 no está configurada.")
    if not firebase_admin._apps:
        cred_json_str = base64.b64decode(FIREBASE_CREDENTIALS_B64).decode('utf-8')
        cred_dict = json.loads(cred_json_str)
        cred = credentials.Certificate(cred_dict)
        firebase_admin.initialize_app(cred)
        print("✅ Conexión exitosa con Firebase.")
    return firestore.client()


def wom_api_consulta(imei: str) -> dict:
    """
    Consulta IMEI directamente al endpoint oficial de WOM.
    Devuelve dict con claves: ok(bool), estado(str|None), is5g(bool|None), mensaje(str|None), raw(str)
    Lanza excepción sólo en errores de red graves; maneja 4xx/5xx devolviendo ok=False.
    """
    payload = {"linea": "", "imei": imei, "token": ""}  # token vacío funciona hoy
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "X-Requested-With": "XMLHttpRequest",
        "Origin": "https://sucursalmiwom.wom.cl",
        "Referer": URL_WOM_PAGE,
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }
    try:
        r = requests.post(URL_WOM_API, json=payload, headers=headers, timeout=REQUEST_TIMEOUT)
    except requests.RequestException as e:
        return {"ok": False, "estado": None, "is5g": None, "mensaje": f"network:{type(e).__name__} {e}", "raw": ""}

    raw = r.text
    estado = None
    is5g = None
    msg = None

    try:
        data = r.json()
        arr = (data or {}).get("Resultado") or []
        if arr:
            it = arr[0]
            estado = it.get("ESTADO")
            is5g = it.get("is5g")
            msg = html.unescape((it.get("mensajeSubtel") or "")).replace("<br>", "\n")
    except ValueError:
        pass

    ok = (r.status_code == 200 and estado is not None)
    return {"ok": ok, "estado": estado, "is5g": is5g, "mensaje": msg, "raw": raw}


def verificar_imei_wom(imei: str) -> str:
    """
    Intenta vía HTTP directa; si falla o no devuelve estado, usa (opcional) Selenium fallback.
    Retorna un string amigable para almacenar en Firestore.
    """
    # 1) Intento HTTP directo (más robusto/ligero en CI)
    for intento in range(MAX_REINTENTOS + 1):
        res = wom_api_consulta(imei)
        if res["ok"] and res["estado"]:
            # Normaliza mensaje final
            if res["estado"] == "NOEN":
                return "Equipo no se encuentra inscrito."
            else:
                return f"Equipo inscrito (ESTADO={res['estado']}, 5G={res['is5g']})."
        time.sleep(0.4)

    # 2) Fallback Selenium (opcional)
    if not USE_SELENIUM_FALLBACK:
        return f"Error: WOM API sin estado válido. Raw={res['raw'][:200]}"

    try:
        print("⚠️ Intento fallback con Selenium (headless)…")
        options = webdriver.ChromeOptions()
        options.add_argument("--headless=new")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        driver = webdriver.Chrome(service=ChromeService(ChromeDriverManager().install()), options=options)
        try:
            stealth(driver, languages=["es-ES", "es"], vendor="Google Inc.", platform="Win32")
        except Exception:
            pass

        driver.get(URL_WOM_PAGE)
        wait = WebDriverWait(driver, 20)

        # Selectores más tolerantes
        input_candidates = [
            (By.CSS_SELECTOR, "input[name*='imei' i]"),
            (By.CSS_SELECTOR, "input[placeholder*='imei' i]"),
            (By.CSS_SELECTOR, "input[type='tel']"),
            (By.CSS_SELECTOR, "input[type='text']")
        ]
        input_el = None
        for how, sel in input_candidates:
            try:
                el = wait.until(EC.visibility_of_element_located((how, sel)))
                if el:
                    input_el = el
                    break
            except TimeoutException:
                continue
        if not input_el:
            return "Error en Selenium: no se encontró el campo IMEI."

        input_el.clear()
        input_el.send_keys(imei)

        btn_candidates = [
            (By.XPATH, "//button[contains(., 'Consultar IMEI') or contains(., 'Consultar') or contains(., 'Buscar')]"),
            (By.CSS_SELECTOR, "input[type='submit']")
        ]
        btn = None
        for how, sel in btn_candidates:
            try:
                el = driver.find_element(how, sel)
                if el.is_displayed():
                    btn = el
                    break
            except Exception:
                continue
        if btn is None:
            input_el.submit()
        else:
            driver.execute_script("arguments[0].click();", btn)

        # Espera texto de resultado en body
        time.sleep(1.2)
        body_txt = driver.find_element(By.TAG_NAME, "body").text.lower()
        if any(k in body_txt for k in ["no se encuentra", "no registrado", "no inscrito"]):
            return "Equipo no se encuentra inscrito."
        if any(k in body_txt for k in ["inscrito", "registrado", "homolog", "bloque", "activo"]):
            return "Equipo inscrito (detectado por DOM)."
        return "Sin resultado claro (fallback Selenium)."

    except Exception as e:
        return f"Error en Selenium: {type(e).__name__} {e}"
    finally:
        try:
            driver.quit()
        except Exception:
            pass


def procesar_orden_lista(order_number: str):
    """Llama a tu API Next.js para marcar la orden como 'Listo'."""
    if not REGISTRATION_API_KEY:
        print("  - ⚠️ REGISTRATION_API_KEY no configurada. No se llama a la API.")
        return

    api_url = f"{HOST_URL}/api/update-wc-order"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {REGISTRATION_API_KEY}"
    }
    payload = {"orderNumber": order_number, "newStatus": "Listo"}

    try:
        r = requests.post(api_url, json=payload, headers=headers, timeout=REQUEST_TIMEOUT)
        if r.status_code == 200:
            print(f"  - ✅ Orden {order_number} procesada exitosamente en la API.")
        else:
            print(f"  - ❌ Error al procesar {order_number}. HTTP {r.status_code} -> {r.text[:300]}")
    except requests.RequestException as e:
        print(f"  - ❌ Excepción al llamar API: {type(e).__name__} {e}")


if __name__ == "__main__":
    db = inicializar_firebase()
    if not db:
        raise SystemExit(1)

    print(f"🔎 Buscando documentos en '{COLECCION_FIRESTORE}' con {CAMPO_ESTADO} == '{ESTADO_A_BUSCAR}' …")
    # Puedes limitar lote si quieres: .limit(50)
    docs = list(db.collection(COLECCION_FIRESTORE).where(CAMPO_ESTADO, '==', ESTADO_A_BUSCAR).stream())
    if not docs:
        print("✅ No hay pendientes.")
        raise SystemExit(0)

    print(f"Encontrados: {len(docs)} documento(s).")
    for doc in docs:
        doc_id = doc.id
        data = doc.to_dict() or {}
        imei = str(data.get(CAMPO_IMEI) or "").strip()

        if not imei:
            print(f"⚠️ {doc_id}: sin campo '{CAMPO_IMEI}'. Saltando.")
            continue

        print(f"\n▶︎ Procesando {doc_id} | IMEI={imei}")
        resultado = verificar_imei_wom(imei)
        print(f"📄 Resultado: {resultado}")

        # Guardar resultado y fecha en Firestore
        doc_ref = db.collection(COLECCION_FIRESTORE).document(doc_id)
        try:
            doc_ref.update({
                'resultado_verificacion': resultado,
                'fecha_verificacion': datetime.now(timezone.utc)
            })
            print(f"  - Guardado en {doc_id}.")
        except Exception as e:
            print(f"  - ❌ Error guardando en {doc_id}: {e}")

        # Si el resultado no contiene "no se encuentra" ni "error" -> consideramos "inscrito"
        low = resultado.lower()
        if ("no se encuentra" not in low) and ("error" not in low):
            print(f"✅ Inscrito. Procesando orden {doc_id} en API…")
            procesar_orden_lista(doc_id)

        time.sleep(PAUSA_ENTRE_DOCS)

    print("\n🎉 Proceso completado.")
