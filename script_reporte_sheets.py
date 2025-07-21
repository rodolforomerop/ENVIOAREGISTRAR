import os
import base64
import json
import gspread
import firebase_admin
from google.oauth2.service_account import Credentials
from firebase_admin import credentials, firestore

def initialize_firebase():
    """Inicializa la app de Firebase Admin si no está ya inicializada."""
    if not firebase_admin._apps:
        b64_creds = os.getenv('FIREBASE_SERVICE_ACCOUNT')
        if not b64_creds:
            raise ValueError("La variable de entorno FIREBASE_SERVICE_ACCOUNT no está configurada. Por favor, configúrala en los secretos del repositorio de GitHub.")
        
        try:
            decoded_creds_str = base64.b64decode(b64_creds).decode('utf-8')
            firebase_creds_dict = json.loads(decoded_creds_str)
            cred = credentials.Certificate(firebase_creds_dict)
            firebase_admin.initialize_app(cred)
        except (json.JSONDecodeError, TypeError, ValueError) as e:
            raise ValueError(f"Error al decodificar o parsear FIREBASE_SERVICE_ACCOUNT: {e}")
            
    return firestore.client()

def get_google_sheets_credentials():
    """Decodifica las credenciales de Google Sheets desde la variable de entorno."""
    b64_creds = os.getenv('GOOGLE_SHEETS_CREDENTIALS')
    if not b64_creds:
        raise ValueError("La variable de entorno GOOGLE_SHEETS_CREDENTIALS no está configurada.")
    decoded_creds = base64.b64decode(b64_creds)
    return json.loads(decoded_creds)

def main():
    """Función principal para generar el reporte."""
    db = None
    try:
        # --- Configuración ---
        ESTADO_A_FILTRAR = "Recibido" 
        NUEVO_ESTADO = "En Proceso"
        NOMBRE_GOOGLE_SHEET = "Registros para Procesar"
            
        print("Inicializando servicios...")
        db = initialize_firebase()
        
        # Autenticación con Google Sheets
        sheets_creds_dict = get_google_sheets_credentials()
        # ** FIX: Added the broader 'drive' scope to allow finding shared files. **
        scopes = [
            "https://www.googleapis.com/auth/spreadsheets",
            "https://www.googleapis.com/auth/drive"
        ]
        creds = Credentials.from_service_account_info(sheets_creds_dict, scopes=scopes)
        client = gspread.authorize(creds)

        print(f"Buscando registros con estado: '{ESTADO_A_FILTRAR}'...")
        registros_ref = db.collection('registros').where('status', '==', ESTADO_A_FILTRAR).stream()

        docs_a_procesar = list(registros_ref)

        if not docs_a_procesar:
            print(f"No se encontraron registros nuevos con el estado '{ESTADO_A_FILTRAR}'. No se generará el reporte.")
            return

        print(f"Se encontraron {len(docs_a_procesar)} registros. Procesando para Google Sheets...")

        headers = ["imei1", "imei2", "serialNumber", "brand", "model"]
        
        datos_para_sheets = [headers]
        for doc in docs_a_procesar:
            reg = doc.to_dict()
            row = [reg.get(header, '') for header in headers]
            datos_para_sheets.append(row)
            
        try:
            # ABRIR la hoja de cálculo que ya existe y fue compartida.
            print(f"Intentando abrir la hoja de cálculo '{NOMBRE_GOOGLE_SHEET}'...")
            spreadsheet = client.open(NOMBRE_GOOGLE_SHEET)
            print(f"Hoja de cálculo '{NOMBRE_GOOGLE_SHEET}' abierta exitosamente.")

        except gspread.exceptions.SpreadsheetNotFound:
            print(f"\n--- ERROR CRÍTICO: HOJA DE CÁLCULO NO ENCONTRADA ---")
            print(f"El script no pudo encontrar la hoja de cálculo llamada '{NOMBRE_GOOGLE_SHEET}'.")
            print("SOLUCIÓN: Asegúrate de haber creado la hoja en tu Google Drive y de haberla compartido con la cuenta de servicio con permisos de 'Editor'.")
            print(f"El correo de la cuenta de servicio es: {sheets_creds_dict.get('client_email')}")
            print("---------------------------------------------------\n")
            raise

        worksheet = spreadsheet.sheet1
        worksheet.clear()
        worksheet.update('A1', datos_para_sheets)
        worksheet.format(f'A1:{chr(ord("A") + len(headers) - 1)}1', {'textFormat': {'bold': True}})

        print(f"\n¡Reporte generado exitosamente con {len(docs_a_procesar)} registros!")
        print(f"Puedes verlo en: {spreadsheet.url}")

        print(f"Actualizando estado de los registros a '{NUEVO_ESTADO}'...")
        batch = db.batch()
        for doc in docs_a_procesar:
            doc_ref = db.collection('registros').document(doc.id)
            batch.update(doc_ref, {'status': NUEVO_ESTADO})
        
        batch.commit()
        print(f"{len(docs_a_procesar)} registros actualizados en Firebase.")

    except Exception as e:
        print(f"\nOcurrió un error grave durante la ejecución del script: {e}")
        # Considera agregar notificaciones aquí si el script falla (ej. email, Slack)

if __name__ == "__main__":
    main()
