# main.py - Script para GitHub Actions que se conecta a Firestore

import os
import json
import base64
import time
import requests
from datetime import datetime, timezone

# Firebase
import firebase_admin
from firebase_admin import credentials, firestore

# Selenium
from selenium import webdriver
from selenium.webdriver.chrome.service import Service as ChromeService
from webdriver_manager.chrome import ChromeDriverManager
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException
from selenium_stealth import stealth

# --- CONFIGURACIÓN ---
# Leídos desde los Secretos de GitHub
FIREBASE_CREDENTIALS_B64 = os.environ.get('FIREBASE_CREDENTIALS_B64')
# Se estandariza para leer la misma variable que otros workflows, con un fallback.
REGISTRATION_API_KEY = os.environ.get('REGISTRATION_API_KEY')
HOST_URL = os.environ.get('HOST_URL', 'https://registroimeimultibanda.cl')


# --- CONFIGURACIÓN DE FIRESTORE (AJUSTADA) ---
COLECCION_FIRESTORE = "registros"
CAMPO_ESTADO = "status"
ESTADO_A_BUSCAR = "En Proceso"
CAMPO_IMEI = "imei1"
# -------------------------------------------------------------------------

ESTADO_INSCRITO = "Listo"
URL_PAGINA = "https://sucursalmiwom.wom.cl/listablanca/sello-multibanda/sello-multibandas.jsp"
TIEMPO_MAX_ESPERA = 30

def inicializar_firebase():
    """Inicializa la conexión con Firebase usando las credenciales de los secretos."""
    if not FIREBASE_CREDENTIALS_B64:
        raise ValueError("La variable de entorno FIREBASE_CREDENTIALS_B64 no está configurada. Revisa los secretos de tu repositorio de GitHub.")
    if not firebase_admin._apps:
        try:
            # Decodifica las credenciales desde Base64
            cred_json_str = base64.b64decode(FIREBASE_CREDENTIALS_B64).decode('utf-8')
            cred_dict = json.loads(cred_json_str)
            
            cred = credentials.Certificate(cred_dict)
            firebase_admin.initialize_app(cred)
            print("✅ Conexión exitosa con Firebase.")
        except Exception as e:
            print(f"❌ Error al inicializar Firebase: {e}")
            raise
    return firestore.client()

def verificar_imei_selenium(driver, imei):
    """Verifica un IMEI con la lógica que ya sabemos que funciona."""
    try:
        driver.get(URL_PAGINA)
        wait = WebDriverWait(driver, TIEMPO_MAX_ESPERA)
        
        input_field = wait.until(EC.visibility_of_element_located((By.ID, "imei")))
        input_field.clear()
        input_field.send_keys(imei)

        submit_button = wait.until(EC.element_to_be_clickable((By.ID, "search_imei")))
        driver.execute_script("arguments[0].click();", submit_button)
        print("Buscando resultado...")

        try:
            wait_short = WebDriverWait(driver, 5)
            wait_short.until(EC.visibility_of_element_located((By.ID, "respuesta_es_notfound_response")))
            return "Equipo no se encuentra inscrito."
        except TimeoutException:
            return "Equipo se encuentra inscrito."

    except TimeoutException:
        return "Error: La página no respondió a tiempo."
    except Exception as e:
        return f"Error en Selenium: {e}"

def procesar_orden_lista(order_number):
    """
    Llama a la API de la aplicación Next.js para procesar una orden que está lista.
    Esto incluye actualizar el estado, enviar correo y actualizar WooCommerce si aplica.
    """
    if not REGISTRATION_API_KEY:
        print("  - ⚠️ No se puede procesar la orden: REGISTRATION_API_KEY no está configurada.")
        return

    api_url = f"{HOST_URL}/api/update-wc-order"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {REGISTRATION_API_KEY}"
    }
    # Ahora enviamos el nuevo estado que queremos aplicar.
    payload = {
        "orderNumber": order_number,
        "newStatus": "Listo" 
    }

    try:
        response = requests.post(api_url, json=payload, headers=headers)
        if response.status_code == 200:
            print(f"  - ✅ Orden {order_number} procesada exitosamente a través de la API.")
        else:
            print(f"  - ❌ Error al procesar la orden {order_number}. Código: {response.status_code}, Respuesta: {response.text}")
    except Exception as e:
        print(f"  - ❌ Excepción al intentar procesar la orden {order_number}: {e}")

# --- LÓGICA PRINCIPAL ---
if __name__ == "__main__":
    db = inicializar_firebase()
    if not db:
        exit()

    print("🤖 Iniciando navegador en modo headless...")
    options = webdriver.ChromeOptions()
    options.add_argument("--headless")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    
    driver = webdriver.Chrome(service=ChromeService(ChromeDriverManager().install()), options=options)
    stealth(driver, languages=["es-ES", "es"], vendor="Google Inc.", platform="Win32")

    try:
        print(f"🤖 Buscando documentos en la colección '{COLECCION_FIRESTORE}' donde '{CAMPO_ESTADO}' sea '{ESTADO_A_BUSCAR}'...")
        
        docs_a_procesar_stream = db.collection(COLECCION_FIRESTORE).where(CAMPO_ESTADO, '==', ESTADO_A_BUSCAR).stream()
        
        docs_a_procesar = list(docs_a_procesar_stream)
        documentos_encontrados = len(docs_a_procesar)

        if documentos_encontrados == 0:
            print("✅ No se encontraron IMEIs pendientes para procesar.")
        else:
            print(f"Se encontraron {documentos_encontrados} documentos para procesar.")

        for doc in docs_a_procesar:
            doc_id = doc.id
            imei_actual = doc.to_dict().get(CAMPO_IMEI)
            
            if not imei_actual:
                print(f"⚠️ Documento {doc_id} no tiene campo '{CAMPO_IMEI}'. Saltando...")
                continue

            print(f"\n🔎 Procesando IMEI: {imei_actual} (Documento: {doc_id})")
            
            resultado_web = verificar_imei_selenium(driver, imei_actual)
            print(f"📄 Resultado obtenido: {resultado_web}")
            
            # Prepara los datos a actualizar, solo con el resultado de la verificación
            datos_para_actualizar_local = {
                'resultado_verificacion': resultado_web,
                'fecha_verificacion': datetime.now(timezone.utc)
            }
            
            # --- LÓGICA DE ACTUALIZACIÓN ---
            if "no se encuentra inscrito" not in resultado_web.lower() and "error" not in resultado_web.lower():
                # Si está inscrito, llamamos a la API para que maneje el cambio de estado,
                # el envío de correo y la actualización de WooCommerce.
                print(f"✅ Equipo inscrito. Llamando a la API para procesar la orden '{doc_id}'.")
                procesar_orden_lista(doc_id)
                
            # Siempre actualiza el documento en Firestore con el resultado de la verificación
            doc_ref = db.collection(COLECCION_FIRESTORE).document(doc_id)
            doc_ref.update(datos_para_actualizar_local)
            print(f"  - Resultado de la verificación guardado en el documento {doc_id}.")
        

    finally:
        driver.quit()
        print("\n🎉 Proceso completado.")

    
