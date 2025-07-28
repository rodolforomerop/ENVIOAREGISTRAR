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

def format_timestamp(timestamp):
    """Formatea un Timestamp de Firestore a un string legible."""
    if timestamp and hasattr(timestamp, 'strftime'):
        return timestamp.strftime('%Y-%m-%d %H:%M:%S')
    return timestamp # Devuelve el valor original si no es un objeto de fecha

def main():
    """Función principal para generar el reporte."""
    db = None
    client_email = "No disponible (error inicial)"
    try:
        # --- Configuración ---
        ESTADO_A_FILTRAR = "Recibido"
        NUEVO_ESTADO_FIREBASE = "En Proceso"
        NOMBRE_HOJA_EXISTENTE = "Registros de IMEI Data Base"

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

        # 1. ABRIR LA HOJA DE CÁLCULO EXISTENTE
        # ======================================
        print(f"Abriendo hoja de cálculo existente: '{NOMBRE_HOJA_EXISTENTE}'...")
        try:
            sh = client.open(NOMBRE_HOJA_EXISTENTE)
            worksheet = sh.sheet1
            print(f"Hoja de cálculo '{NOMBRE_HOJA_EXISTENTE}' abierta exitosamente.")
            print(f"URL: {sh.url}")
        except gspread.exceptions.SpreadsheetNotFound:
            print("\n" + "="*80)
            print(f"ERROR CRÍTICO: No se encontró la hoja de cálculo '{NOMBRE_HOJA_EXISTENTE}'.")
            print("Por favor, asegúrate de que:")
            print(f"1. La hoja de cálculo con el nombre EXACTO '{NOMBRE_HOJA_EXISTENTE}' existe en Google Drive.")
            print(f"2. Has compartido esa hoja de cálculo con la cuenta de servicio '{client_email}' con permisos de 'Editor'.")
            print("="*80 + "\n")
            raise
        except Exception as e:
            print(f"Error al abrir la hoja de cálculo: {e}")
            raise

        # 2. PREPARAR Y AÑADIR DATOS
        # ============================
        firebase_fields_order = [
            "orderNumber",  # Col A
            "imei1",        # Col B
            "imei2",        # Col C
            "serialNumber", # Col D
            "brand",        # Col E
            "model",        # Col F
            "status",       # Col G
            "createdAt"     # Col H
        ]

        datos_para_sheets = []
        for doc in docs_a_procesar:
            reg = doc.to_dict()
            # Construye la fila con los datos de Firebase
            row_data = [format_timestamp(reg.get(field, '')) for field in firebase_fields_order]
            
            # Construye la fila completa con columnas vacías y valores estáticos
            # A, B, C, D, E, F, G, H (datos de firebase)
            # I, J, K, L (vacías)
            # M (Canal)
            # N, O (vacías)
            # P (Revisión IMEI)
            full_row = row_data + ['', '', '', '', 'RIM APP', '', '', 'OK']
            datos_para_sheets.append(full_row)
        
        # El script ahora asume que los encabezados ya existen.
        # No se añaden encabezados nuevos para no alterar la estructura.

        print(f"Añadiendo {len(datos_para_sheets)} nuevas filas a la hoja de cálculo...")
        worksheet.append_rows(datos_para_sheets, value_input_option='USER_ENTERED')
        
        print("Datos añadidos correctamente.")
        
        # 3. ACTUALIZAR ESTADO EN FIREBASE
        # =================================
        print(f"Actualizando {len(docs_a_procesar)} registros en Firebase al estado '{NUEVO_ESTADO_FIREBASE}'...")
        for doc in docs_a_procesar:
            try:
                doc.reference.update({'status': NUEVO_ESTADO_FIREBASE})
                print(f"  - Registro {doc.id} actualizado a '{NUEVO_ESTADO_FIREBASE}'.")
            except Exception as e:
                print(f"  - ERROR al actualizar el registro {doc.id}: {e}")

        print("\n¡Proceso de reporte completado exitosamente!")
        print(f"Se han añadido {len(docs_a_procesar)} registros a '{NOMBRE_HOJA_EXISTENTE}' y se han actualizado en Firebase.")

    except gspread.exceptions.GSpreadException as e:
        print("\n" + "="*80)
        print("ERROR CRÍTICO DE GOOGLE SHEETS:")
        print("="*80)
        print("Hubo un problema al interactuar con la API de Google Sheets.")
        print(f"Detalle del error: {e}")
        print("\nINSTRUCCIONES:")
        print("1. Verifica que la API de Google Sheets y la API de Google Drive estén habilitadas en tu proyecto de Google Cloud.")
        print(f"2. Asegúrate de que la cuenta de servicio '{client_email}' tenga permisos de 'Editor' sobre la hoja de cálculo '{NOMBRE_HOJA_EXISTENTE}'.")
        print("3. Revisa los logs de la ejecución en GitHub Actions para más detalles.")
        print("="*80 + "\n")
        raise
    except Exception as e:
        print(f"\nOcurrió un error grave durante la ejecución del script: {e}")
        raise

if __name__ == "__main__":
    main()
