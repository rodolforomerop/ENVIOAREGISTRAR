# script.py - VERSIÓN PARA GITHUB ACTIONS (CON DEPURACIÓN MEJORADA)
import gspread
import time
import os
import json
from selenium import webdriver
from selenium.webdriver.chrome.service import Service as ChromeService
from webdriver_manager.chrome import ChromeDriverManager
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException
from selenium_stealth import stealth

# --- CONFIGURACIÓN (Leída desde los Secretos de GitHub) ---
NOMBRE_HOJA_CALCULO = os.environ.get('GSPREAD_SHEET_NAME')
CREDENCIALES_JSON = os.environ.get('GSPREAD_CREDENTIALS')

ESTADO_A_BUSCAR = "En Proceso"
ESTADO_FINALIZADO = "Listo"
COLUMNA_IMEI = "IMEI 1"
COLUMNA_ESTADO = "Estado"
URL_PAGINA = "https://sucursalmiwom.wom.cl/listablanca/sello-multibanda/sello-multibandas.jsp"
TIEMPO_MAX_ESPERA = 30

def conectar_a_google_sheets():
    """Conecta con Google Sheets usando las credenciales desde los secretos."""
    try:
        # 1. Intenta cargar las credenciales
        if not CREDENCIALES_JSON:
            print("❌ Error: El secreto 'GSPREAD_CREDENTIALS' no está definido.")
            return None
        creds_dict = json.loads(CREDENCIALES_JSON)
        print("✅ Credenciales JSON cargadas correctamente.")
        
        # 2. Intenta autenticar
        gc = gspread.service_account_from_dict(creds_dict)
        print("✅ Autenticación con la cuenta de servicio exitosa.")
        
        # 3. Intenta abrir la hoja de cálculo
        if not NOMBRE_HOJA_CALCULO:
            print("❌ Error: El secreto 'GSPREAD_SHEET_NAME' no está definido.")
            return None
        worksheet = gc.open(NOMBRE_HOJA_CALCULO).sheet1
        print("✅ Conexión y apertura de la hoja de cálculo exitosa.")
        return worksheet
        
    except json.JSONDecodeError:
        print("❌ Error Fatal: El contenido de 'GSPREAD_CREDENTIALS' no es un JSON válido. Asegúrate de copiar todo el contenido del archivo .json.")
        return None
    except gspread.exceptions.SpreadsheetNotFound:
        print(f"❌ Error Fatal: No se encontró la hoja de cálculo '{NOMBRE_HOJA_CALCULO}'. Verifica el nombre y asegúrate de haberla compartido con el 'client_email' de las credenciales.")
        return None
    except Exception as e:
        print(f"❌ Error inesperado al conectar con Google Sheets: {e}")
        return None

def verificar_imei_selenium(driver, imei):
    """Verifica un IMEI en la página web."""
    try:
        driver.get(URL_PAGINA)
        wait = WebDriverWait(driver, TIEMPO_MAX_ESPERA)
        
        input_field = wait.until(EC.visibility_of_element_located((By.ID, "imei")))
        input_field.clear()
        input_field.send_keys(imei)

        submit_button = wait.until(EC.element_to_be_clickable((By.ID, "search_imei")))
        # Usamos un clic con JavaScript para máxima compatibilidad en entornos automatizados
        driver.execute_script("arguments[0].click();", submit_button)
        print("Buscando resultado...")

        # Lógica para determinar el resultado
        try:
            # 1. Intenta encontrar el elemento de "NO ENCONTRADO" durante 5 segundos.
            wait_short = WebDriverWait(driver, 5)
            wait_short.until(EC.visibility_of_element_located((By.ID, "respuesta_es_notfound_response")))
            # Si lo encuentra, el equipo no está inscrito.
            return "Equipo no se encuentra inscrito."
        except TimeoutException:
            # 2. Si después de 5 seg no lo encontró, asumimos que es un caso de éxito.
            return "Equipo se encuentra inscrito."

    except TimeoutException:
        return "Error: La página no respondió a tiempo."
    except Exception as e:
        return f"Error en Selenium: {e}"

# --- LÓGICA PRINCIPAL ---
if __name__ == "__main__":
    hoja = conectar_a_google_sheets()
    if not hoja:
        # La función conectar_a_google_sheets ya imprimió el error específico.
        # Salimos del script para que el job de GitHub Actions falle y nos notifique.
        exit(1)

    print("🤖 Iniciando navegador en modo headless (invisible)...")
    options = webdriver.ChromeOptions()
    options.add_argument("--headless")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("window-size=1920,1080") # A veces ayuda a que las páginas se rendericen correctamente
    
    driver = webdriver.Chrome(service=ChromeService(ChromeDriverManager().install()), options=options)
    
    # Aplicar stealth para evitar ser detectado como bot
    stealth(driver,
            languages=["es-ES", "es"],
            vendor="Google Inc.",
            platform="Win32",
            webgl_vendor="Intel Inc.",
            renderer="Intel Iris OpenGL Engine",
            fix_hairline=True,
            )

    try:
        print("🤖 Iniciando proceso de verificación...")
        filas = hoja.get_all_records()
        col_estado_index = hoja.find(COLUMNA_ESTADO).col
        col_imei_index = hoja.find(COLUMNA_IMEI).col

        for indice, fila in enumerate(filas):
            # El número de fila real en la hoja es el índice + 2 (1 por el encabezado, 1 porque el índice es base 0)
            numero_fila_real = indice + 2
            
            if fila.get(COLUMNA_ESTADO) == ESTADO_A_BUSCAR and fila.get(COLUMNA_IMEI):
                imei_actual = str(fila.get(COLUMNA_IMEI))
                print(f"\n🔎 Procesando IMEI: {imei_actual} (Fila {numero_fila_real})")
                
                resultado_web = verificar_imei_selenium(driver, imei_actual)
                print(f"📄 Resultado obtenido: {resultado_web}")
                
                # Actualizar el estado basado en el resultado
                if "error" in resultado_web.lower():
                    # Si hay un error de Selenium, lo anotamos en la hoja
                    hoja.update_cell(numero_fila_real, col_estado_index, resultado_web)
                    print(f"⚠️ Error al procesar. Fila {numero_fila_real} actualizada con el mensaje de error.")
                elif "no se encuentra inscrito" in resultado_web.lower():
                    # Si no está inscrito, lo dejamos como "En Proceso" o el estado que definas
                    print(f"⚠️ Equipo no inscrito. La fila {numero_fila_real} no se modifica.")
                else:
                    # Si está inscrito, actualizamos a "Listo"
                    hoja.update_cell(numero_fila_real, col_estado_index, ESTADO_FINALIZADO)
                    print(f"✅ Equipo inscrito. Fila {numero_fila_real} actualizada a '{ESTADO_FINALIZADO}'.")
    finally:
        driver.quit()
        print("\n🎉 Proceso completado.")
