import os
import base64
import json
import gspread
from google.oauth2.service_account import Credentials
import firebase_admin
from firebase_admin import credentials, firestore

def get_firebase_credentials():
    """Decodifica las credenciales de Firebase desde la variable de entorno."""
    b64_creds = os.getenv('FIREBASE_SERVICE_ACCOUNT')
    if not b64_creds:
        raise ValueError("La variable de entorno FIREBASE_SERVICE_ACCOUNT no está configurada.")
    decoded_creds = base64.b64decode(b64_creds)
    return json.loads(decoded_creds)

def get_google_sheets_credentials():
    """Decodifica las credenciales de Google Sheets desde la variable de entorno."""
    b64_creds = os.getenv('GOOGLE_SHEETS_CREDENTIALS')
    if not b64_creds:
        raise ValueError("La variable de entorno GOOGLE_SHEETS_CREDENTIALS no está configurada.")
    decoded_creds = base64.b64decode(b64_creds)
    return json.loads(decoded_creds)

def initialize_firebase():
    """Inicializa la app de Firebase Admin si no está ya inicializada."""
    if not firebase_admin._apps:
        firebase_creds_dict = get_firebase_credentials()
        cred = credentials.Certificate(firebase_creds_dict)
        firebase_admin.initialize_app(cred)
    return firestore.client()

def main():
    """Función principal para generar el reporte."""
    try:
        # --- Configuración ---
        # Cambia este estado al que quieras filtrar
        ESTADO_A_FILTRAR = "Recibido" 
        # Nombre del archivo de Google Sheets que se creará o actualizará
        NOMBRE_GOOGLE_SHEET = "Reporte de Registros IMEI"
        # Email del usuario o cuenta de servicio al que se le compartirá la hoja
        EMAIL_PARA_COMPARTIR = os.getenv('GOOGLE_SHEETS_SHARE_EMAIL')
        if not EMAIL_PARA_COMPARTIR:
            print("Advertencia: La variable GOOGLE_SHEETS_SHARE_EMAIL no está configurada. La hoja no se compartirá.")
            
        print("Inicializando servicios...")
        db = initialize_firebase()
        
        # Autenticación con Google Sheets
        sheets_creds_dict = get_google_sheets_credentials()
        scopes = ["https://www.googleapis.com/auth/spreadsheets"]
        creds = Credentials.from_service_account_info(sheets_creds_dict, scopes=scopes)
        client = gspread.authorize(creds)

        print(f"Buscando registros con estado: '{ESTADO_A_FILTRAR}'...")
        registros_ref = db.collection('registros').where('status', '==', ESTADO_A_FILTRAR).stream()

        registros = [doc.to_dict() for doc in registros_ref]

        if not registros:
            print(f"No se encontraron registros con el estado '{ESTADO_A_FILTRAR}'. No se generará el reporte.")
            return

        print(f"Se encontraron {len(registros)} registros. Procesando para Google Sheets...")

        # Preparar datos para la hoja de cálculo
        headers = [
            "orderNumber", "status", "createdAt", "customerName", "customerEmail",
            "whatsapp", "deviceType", "brand", "model", "imei1", "imei2", "serialNumber",
            "processingDate", "resultado_verificacion", "fecha_verificacion"
        ]
        
        datos_para_sheets = [headers]
        for reg in registros:
            # Convertir Timestamps y otros tipos a string para la hoja
            row = []
            for header in headers:
                value = reg.get(header, '')
                if hasattr(value, 'isoformat'): # Para Timestamps de Firebase
                    value = value.isoformat()
                row.append(str(value))
            datos_para_sheets.append(row)
            
        # Crear o abrir la hoja de cálculo
        try:
            spreadsheet = client.open(NOMBRE_GOOGLE_SHEET)
            print(f"Hoja de cálculo '{NOMBRE_GOOGLE_SHEET}' encontrada.")
        except gspread.exceptions.SpreadsheetNotFound:
            spreadsheet = client.create(NOMBRE_GOOGLE_SHEET)
            print(f"Hoja de cálculo '{NOMBRE_GOOGLE_SHEET}' creada.")

        # Compartir la hoja si se proporcionó un email
        if EMAIL_PARA_COMPARTIR:
            try:
                spreadsheet.share(EMAIL_PARA_COMPARTIR, perm_type='user', role='writer')
                print(f"Hoja compartida con {EMAIL_PARA_COMPARTIR}.")
            except Exception as e:
                print(f"No se pudo compartir la hoja. Error: {e}")


        worksheet = spreadsheet.sheet1
        worksheet.clear()
        worksheet.update('A1', datos_para_sheets)
        worksheet.format('A1:O1', {'textFormat': {'bold': True}})

        print("\n¡Reporte generado exitosamente!")
        print(f"Puedes verlo en: {spreadsheet.url}")

    except Exception as e:
        print(f"\nOcurrió un error grave durante la ejecución del script: {e}")
        # Considera agregar notificaciones aquí si el script falla (ej. email, Slack)

if __name__ == "__main__":
    main()

