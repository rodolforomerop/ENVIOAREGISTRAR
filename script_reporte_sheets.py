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
        NOMBRE_HOJA_DETALLE_PREFIX = "Registros"
        EMAIL_A_COMPARTIR = os.getenv('GOOGLE_SHEETS_SHARE_EMAIL')
        
        # Nombre de la hoja de cálculo "Maestra" que simula la respuesta del formulario
        NOMBRE_HOJA_MAESTRA = "Inscripción Embarque - Rodolfo Peña (respuestas)"

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

        print(f"Buscando registros con estado: '{ESTADO_A_FILTRAR}'...")
        registros_ref = db.collection('registros').where('status', '==', ESTADO_A_FILTRAR).stream()

        docs_a_procesar = list(registros_ref)

        if not docs_a_procesar:
            print(f"No se encontraron registros nuevos con el estado '{ESTADO_A_FILTRAR}'. No se generará el reporte.")
            return

        print(f"Se encontraron {len(docs_a_procesar)} registros. Procesando para Google Sheets...")

        # 1. ABRIR HOJA MAESTRA Y CREAR NUEVA PESTAÑA DE DETALLE
        # ========================================================
        try:
            print(f"Abriendo la hoja maestra '{NOMBRE_HOJA_MAESTRA}'...")
            sh_maestra = client.open(NOMBRE_HOJA_MAESTRA)
            worksheet_maestra = sh_maestra.sheet1 # La primera hoja/pestaña
            print("Hoja maestra abierta.")
        except gspread.exceptions.SpreadsheetNotFound:
            print(f"ERROR CRÍTICO: La hoja maestra '{NOMBRE_HOJA_MAESTRA}' no existe o la cuenta de servicio no tiene permisos para verla.")
            print("Asegúrate de que la hoja exista y de que hayas compartido el archivo con el email de la cuenta de servicio.")
            client_email = creds.service_account_email
            print(f"Comparte la hoja con: {client_email}")
            raise
        
        # Crear la nueva pestaña para los detalles
        timestamp_actual = datetime.now().strftime("%Y-%m-%d_%H-%M")
        nombre_hoja_detalle = f"{NOMBRE_HOJA_DETALLE_PREFIX} - {timestamp_actual}"
        print(f"Creando nueva pestaña de detalle: '{nombre_hoja_detalle}'...")
        worksheet_detalle = sh_maestra.add_worksheet(title=nombre_hoja_detalle, rows="100", cols="20")
        
        # Preparar datos para la nueva pestaña
        field_to_header_map = {
            "imei1": "IMEI 1", "imei2": "IMEI 2", "serialNumber": "Serie", 
            "brand": "Marca", "model": "Modelo"
        }
        firebase_fields_order = ["imei1", "imei2", "serialNumber", "brand", "model"]
        
        headers_row = [field_to_header_map[field] for field in firebase_fields_order]
        datos_para_sheets_detalle = [headers_row]
        for doc in docs_a_procesar:
            reg = doc.to_dict()
            row = [reg.get(field, '') for field in firebase_fields_order]
            datos_para_sheets_detalle.append(row)
        
        # Escribir y formatear la nueva pestaña de detalle
        worksheet_detalle.update('A1', datos_para_sheets_detalle)
        worksheet_detalle.format(f'A1:{chr(ord("A") + len(headers_row) - 1)}1', {'textFormat': {'bold': True}})
        print(f"Pestaña de detalle creada exitosamente en la hoja maestra.")


        # 2. ACTUALIZAR LA HOJA PRINCIPAL
        # ===============================

        # Añadir la nueva fila a la hoja principal (la primera pestaña)
        nueva_fila_maestra = [
            datetime.now().strftime("%d/%m/%Y %H:%M:%S"),
            len(docs_a_procesar),
            f"Ver pestaña '{nombre_hoja_detalle}'", # Referencia a la nueva pestaña
            "Recibido"  # Estado inicial
        ]
        print(f"Añadiendo nueva fila a la hoja principal: {nueva_fila_maestra}")
        worksheet_maestra.append_row(nueva_fila_maestra, table_range='A1')
        print("Hoja principal actualizada.")

        # 3. ACTUALIZAR ESTADO EN FIREBASE
        # ================================
        
        print(f"Actualizando estado de los {len(docs_a_procesar)} registros a '{NUEVO_ESTADO}' en Firebase...")
        batch = db.batch()
        for doc in docs_a_procesar:
            doc_ref = db.collection('registros').document(doc.id)
            batch.update(doc_ref, {'status': NUEVO_ESTADO})
        
        batch.commit()
        print(f"{len(docs_a_procesar)} registros actualizados en Firebase.")
        print("\n¡Proceso de automatización completado exitosamente!")

    except Exception as e:
        print(f"\nOcurrió un error grave durante la ejecución del script: {e}")
        raise

if __name__ == "__main__":
    main()

    
