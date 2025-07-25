import os
import base64
import json
import gspread
import firebase_admin
from google.oauth2.service_account import Credentials
from firebase_admin import credentials, firestore
from datetime import datetime

def initialize_firebase():
    """Inicializa la app de Firebase Admin si no está ya inicializada."""
    if not firebase_admin._apps:
        b64_creds = os.getenv('FIREBASE_SERVICE_ACCOUNT')
        if not b64_creds:
            raise ValueError("La variable de entorno FIREBASE_SERVICE_ACCOUNT no está configurada.")
        
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
        NOMBRE_NUEVA_HOJA = "Registros para Procesar"
        EMAIL_A_COMPARTIR = os.getenv('GOOGLE_SHEETS_SHARE_EMAIL')

        if not EMAIL_A_COMPARTIR:
            raise ValueError("La variable de entorno GOOGLE_SHEETS_SHARE_EMAIL no está configurada. No se puede compartir el archivo.")

        print("Inicializando servicios...")
        db = initialize_firebase()
        
        # Autenticación con Google Sheets
        sheets_creds_dict = get_google_sheets_credentials()
        scopes = [
            "https://www.googleapis.com/auth/spreadsheets",
            "https://www.googleapis.com/auth/drive"
        ]
        creds = Credentials.from_service_account_info(sheets_creds_dict, scopes=scopes)
        client = gspread.authorize(creds)
        client_email = creds.service_account_email


        print(f"Buscando registros con estado: '{ESTADO_A_FILTRAR}'...")
        registros_ref = db.collection('registros').where('status', '==', ESTADO_A_FILTRAR).stream()

        docs_a_procesar = list(registros_ref)

        if not docs_a_procesar:
            print(f"No se encontraron registros nuevos con el estado '{ESTADO_A_FILTRAR}'. No se generará el reporte.")
            return

        print(f"Se encontraron {len(docs_a_procesar)} registros. Procesando para Google Sheets...")

        # 1. CREAR UNA NUEVA HOJA DE CÁLCULO
        # ==================================
        timestamp_actual = datetime.now().strftime("%Y-%m-%d %H:%M")
        nombre_completo_hoja = f"{NOMBRE_NUEVA_HOJA} - {timestamp_actual}"
        
        print(f"Creando nueva hoja de cálculo con el nombre: '{nombre_completo_hoja}'...")
        try:
            sh = client.create(nombre_completo_hoja)
            print(f"Hoja de cálculo '{nombre_completo_hoja}' creada exitosamente.")
            print(f"URL: {sh.url}")
        except Exception as e:
            print(f"Error al crear la hoja de cálculo: {e}")
            raise

        # 2. PREPARAR Y ESCRIBIR DATOS
        # ============================
        worksheet = sh.sheet1
        
        field_to_header_map = {
            "imei1": "IMEI 1", "imei2": "IMEI 2", "serialNumber": "Serie", 
            "brand": "Marca", "model": "Modelo"
        }
        firebase_fields_order = ["imei1", "imei2", "serialNumber", "brand", "model"]
        
        headers_row = [field_to_header_map[field] for field in firebase_fields_order]
        datos_para_sheets = [headers_row]
        
        for doc in docs_a_procesar:
            reg = doc.to_dict()
            row = [reg.get(field, '') for field in firebase_fields_order]
            datos_para_sheets.append(row)
        
        print("Escribiendo datos en la hoja de cálculo...")
        worksheet.update('A1', datos_para_sheets)
        worksheet.format(f'A1:{chr(ord("A") + len(headers_row) - 1)}1', {'textFormat': {'bold': True}})
        print("Datos escritos y formateados correctamente.")
        
        # 3. COMPARTIR LA HOJA DE CÁLCULO
        # ===============================
        print(f"Compartiendo la hoja con '{EMAIL_A_COMPARTIR}' (Editor)...")
        try:
            sh.share(EMAIL_A_COMPARTIR, perm_type='user', role='writer', notify=True)
            print("Hoja compartida exitosamente.")
        except Exception as e:
            print(f"ADVERTENCIA: No se pudo compartir la hoja con {EMAIL_A_COMPARTIR}. Error: {e}")
            print("Asegúrate de que el email es válido y que tienes permisos de 'Manager' sobre el archivo.")
            print(f"El propietario actual del archivo es la cuenta de servicio: {client_email}")


        # 4. ACTUALIZAR ESTADO EN FIREBASE
        # ================================
        
        print(f"Actualizando estado de los {len(docs_a_procesar)} registros a '{NUEVO_ESTADO}' en Firebase...")
        batch = db.batch()
        for doc in docs_a_procesar:
            doc_ref = db.collection('registros').document(doc.id)
            batch.update(doc_ref, {'status': NUEVO_ESTADO})
        
        batch.commit()
        print(f"{len(docs_a_procesar)} registros actualizados en Firebase.")
        print("\n¡Proceso de automatización completado exitosamente!")

    except gspread.exceptions.GSpreadException as e:
        print("\n" + "="*80)
        print("ERROR CRÍTICO DE GOOGLE SHEETS:")
        print("="*80)
        print("Hubo un problema al interactuar con la API de Google Sheets.")
        print(f"Detalle del error: {e}")
        print("\nINSTRUCCIONES:")
        print("1. Verifica que la API de Google Sheets y la API de Google Drive estén habilitadas en tu proyecto de Google Cloud.")
        print(f"2. Asegúrate de que la cuenta de servicio '{client_email}' tenga permisos suficientes (rol de 'Editor' o superior en el proyecto de GCP).")
        print("3. Revisa los logs de la ejecución en GitHub Actions para más detalles.")
        print("="*80 + "\n")
        raise
    except Exception as e:
        print(f"\nOcurrió un error grave durante la ejecución del script: {e}")
        raise

if __name__ == "__main__":
    main()
