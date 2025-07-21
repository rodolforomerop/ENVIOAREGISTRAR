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
        EMAIL_PARA_COMPARTIR = os.getenv('GOOGLE_SHEETS_SHARE_EMAIL')
        if not EMAIL_PARA_COMPARTIR:
            print("Advertencia: La variable GOOGLE_SHEETS_SHARE_EMAIL no está configurada. La hoja no se compartirá.")
            
        print("Inicializando servicios...")
        db = initialize_firebase()
        
        # Autenticación con Google Sheets
        sheets_creds_dict = get_google_sheets_credentials()
        scopes = [
            "https://www.googleapis.com/auth/spreadsheets",
            "https://www.googleapis.com/auth/drive.file"
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
            spreadsheet = client.open(NOMBRE_GOOGLE_SHEET)
            print(f"Hoja de cálculo '{NOMBRE_GOOGLE_SHEET}' encontrada.")
        except gspread.exceptions.SpreadsheetNotFound:
            print(f"Hoja de cálculo '{NOMBRE_GOOGLE_SHEET}' no encontrada. Intentando crear una nueva...")
            try:
                spreadsheet = client.create(NOMBRE_GOOGLE_SHEET)
                print(f"Hoja de cálculo '{NOMBRE_GOOGLE_SHEET}' creada.")
                if EMAIL_PARA_COMPARTIR:
                    try:
                        spreadsheet.share(EMAIL_PARA_COMPARTIR, perm_type='user', role='writer')
                        print(f"Hoja compartida con {EMAIL_PARA_COMPARTIR}.")
                    except Exception as e:
                        print(f"ADVERTENCIA: No se pudo compartir la hoja. Error: {e}. El propietario de la hoja será la cuenta de servicio.")
            except gspread.exceptions.APIError as e:
                if "storageQuotaExceeded" in str(e):
                    print("\n--- ERROR CRÍTICO: CUOTA DE GOOGLE DRIVE EXCEDIDA ---")
                    print("La cuenta de servicio no tiene espacio para crear nuevos archivos en Google Drive.")
                    print("SOLUCIÓN: Debes iniciar sesión en Google Drive con la cuenta de servicio y eliminar los archivos 'huérfanos' que ha creado.")
                    print("Consulta la sección 'Solución de Problemas' en el archivo README.md para obtener instrucciones detalladas.")
                    print("---------------------------------------------------\n")
                raise e # Relanza el error para que el workflow falle y te notifique

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
