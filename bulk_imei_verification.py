import os
import base64
import json
import gspread
import requests
import time
from google.oauth2.service_account import Credentials
import firebase_admin
from firebase_admin import credentials, firestore

def initialize_firebase():
    """Inicializa la app de Firebase Admin si no está ya inicializada."""
    if not firebase_admin._apps:
        b64_creds = os.getenv('FIREBASE_CREDENTIALS_B64')
        if not b64_creds:
            raise ValueError("La variable de entorno FIREBASE_CREDENTIALS_B64 no está configurada.")
        
        try:
            decoded_creds_str = base64.b64decode(b64_creds).decode('utf-8')
            firebase_creds_dict = json.loads(decoded_creds_str)
            cred = credentials.Certificate(firebase_creds_dict)
            firebase_admin.initialize_app(cred)
        except Exception as e:
            raise ValueError(f"Error al decodificar o parsear FIREBASE_CREDENTIALS_B64: {e}")
    return firestore.client()

def get_google_sheets_client():
    """Obtiene un cliente autenticado para Google Sheets."""
    b64_creds = os.getenv('GOOGLE_SHEETS_CREDENTIALS')
    if not b64_creds:
        raise ValueError("La variable de entorno GOOGLE_SHEETS_CREDENTIALS no está configurada.")
    
    try:
        decoded_creds = base64.b64decode(b64_creds).decode('utf-8')
        creds_dict = json.loads(decoded_creds)
        scopes = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
        creds = Credentials.from_service_account_info(creds_dict, scopes=scopes)
        return gspread.authorize(creds)
    except Exception as e:
        raise ValueError(f"Error al procesar las credenciales de Google Sheets: {e}")

def check_imei_status(imei):
    """Verifica un solo IMEI usando la API externa."""
    if not imei or not str(imei).strip():
        return "Vacío"
        
    url = "https://verificador-imei.onrender.com/verificar"
    try:
        response = requests.post(url, json={"imei": str(imei).strip()}, timeout=20)
        response.raise_for_status()
        return response.json().get('resultado', 'Error: Respuesta inesperada')
    except requests.exceptions.RequestException as e:
        print(f"  - Error de red para IMEI {imei}: {e}")
        return "Error de Conexión"
    except Exception as e:
        print(f"  - Error inesperado para IMEI {imei}: {e}")
        return "Error en Script"

def get_or_create_header(worksheet, header_name):
    """Busca un encabezado, si no existe lo crea y devuelve su índice de columna (1-based)."""
    headers = worksheet.row_values(1)
    try:
        return headers.index(header_name) + 1
    except ValueError:
        new_col_index = len(headers) + 1
        worksheet.update_cell(1, new_col_index, header_name)
        print(f"  - Columna '{header_name}' no encontrada, creada en la columna {new_col_index}.")
        return new_col_index

def main():
    """Función principal del script de verificación masiva."""
    print("🚀 Iniciando Verificación Masiva de IMEI...")
    
    try:
        db = initialize_firebase()
        gc = get_google_sheets_client()
        
        company_id = os.getenv('COMPANY_ID')
        spreadsheet_url = os.getenv('SPREADSHEET_URL')
        
        if not company_id or not spreadsheet_url:
            raise ValueError("COMPANY_ID y SPREADSHEET_URL son variables de entorno requeridas.")

        print(f"🏢 Empresa: {company_id}")
        print(f"🔗 URL de Hoja de Cálculo: {spreadsheet_url}")

        sh = gc.open_by_url(spreadsheet_url)
        worksheet = sh.sheet1
        print(f"✅ Hoja de cálculo '{sh.title}' abierta correctamente.")
        
        records = worksheet.get_all_records()
        
        if not records:
            print("⚠️ La hoja de cálculo está vacía o no tiene cabeceras. No hay nada que procesar.")
            return

        headers = worksheet.row_values(1)
        imei1_col_name = "IMEI 1"
        imei2_col_name = "IMEI 2"
        
        imei1_col_index = headers.index(imei1_col_name) + 1 if imei1_col_name in headers else None
        imei2_col_index = headers.index(imei2_col_name) + 1 if imei2_col_name in headers else None
        
        if not imei1_col_index and not imei2_col_index:
            print(f"❌ Error: La hoja de cálculo debe contener al menos una de las columnas: '{imei1_col_name}' o '{imei2_col_name}'.")
            return

        result1_col_index = get_or_create_header(worksheet, "Resultado IMEI 1") if imei1_col_index else None
        result2_col_index = get_or_create_header(worksheet, "Resultado IMEI 2") if imei2_col_index else None
            
        print(f"🔎 Encontradas {len(records)} filas para procesar.")
        
        updates_to_batch = []
        for i, row in enumerate(records):
            row_num = i + 2  # +1 por la cabecera, +1 por el índice base 0
            
            # Procesar IMEI 1
            if imei1_col_index:
                imei1 = row.get(imei1_col_name)
                print(f"  - Fila {row_num}: Verificando {imei1_col_name} '{imei1}'...")
                resultado1 = check_imei_status(imei1)
                print(f"    -> Resultado: {resultado1}")
                updates_to_batch.append({
                    'range': f'R{row_num}C{result1_col_index}',
                    'values': [[resultado1]],
                })
                time.sleep(1) # Pausa entre verificaciones
            
            # Procesar IMEI 2
            if imei2_col_index:
                imei2 = row.get(imei2_col_name)
                print(f"  - Fila {row_num}: Verificando {imei2_col_name} '{imei2}'...")
                resultado2 = check_imei_status(imei2)
                print(f"    -> Resultado: {resultado2}")
                updates_to_batch.append({
                    'range': f'R{row_num}C{result2_col_index}',
                    'values': [[resultado2]],
                })
                time.sleep(1) # Pausa entre verificaciones

        if updates_to_batch:
            print(f"\n✍️ Escribiendo {len(updates_to_batch)} resultados en la hoja de cálculo...")
            worksheet.batch_update(updates_to_batch, value_input_option='USER_ENTERED')
            print("✅ ¡Resultados escritos exitosamente!")

        print("\n🎉 Proceso de verificación masiva completado.")

    except Exception as e:
        print(f"❌ Error fatal durante la ejecución: {e}")
        raise

if __name__ == "__main__":
    main()
