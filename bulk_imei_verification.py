import os
import base64
import json
import requests
import time
from datetime import datetime, timezone
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

def main():
    """Función principal del script de verificación masiva desde Firestore."""
    print("🚀 Iniciando Verificación Masiva de IMEI desde Firestore...")
    
    try:
        db = initialize_firebase()
        
        batch_id = os.getenv('BATCH_ID')
        if not batch_id:
            raise ValueError("BATCH_ID es una variable de entorno requerida.")

        print(f"📄 Procesando Lote de Verificación: {batch_id}")

        batch_ref = db.collection('imei_batches').document(batch_id)
        imeis_ref = batch_ref.collection('imeis')

        # Buscar solo los IMEIs pendientes de este lote
        docs_to_process = imeis_ref.where('status', '==', 'pending_verification').stream()
        
        processed_count = 0
        for doc in docs_to_process:
            imei_data = doc.to_dict()
            imei1 = imei_data.get('imei1')
            imei2 = imei_data.get('imei2')
            doc_id = doc.id
            
            print(f"\n  - Verificando documento: {doc_id} (IMEI1: {imei1})")
            
            update_data = {
                'verifiedAt': datetime.now(timezone.utc),
            }
            
            # Procesar IMEI 1
            if imei1:
                resultado1 = check_imei_status(imei1)
                update_data['result1'] = resultado1
                print(f"    -> Resultado IMEI 1: {resultado1}")
                time.sleep(1) # Pausa entre verificaciones

            # Procesar IMEI 2 si existe
            if imei2:
                resultado2 = check_imei_status(imei2)
                update_data['result2'] = resultado2
                print(f"    -> Resultado IMEI 2: {resultado2}")
                time.sleep(1)
            
            update_data['status'] = 'verified'
            imeis_ref.document(doc_id).update(update_data)
            print(f"    -> Documento {doc_id} actualizado a 'verified'.")
            processed_count += 1
            
        if processed_count == 0:
            print("⚠️ No se encontraron IMEIs pendientes en este lote.")
        else:
            print(f"\n✅ Verificados {processed_count} registros.")

        # Marcar el lote como completado
        batch_ref.update({'status': 'completed', 'completedAt': datetime.now(timezone.utc)})
        print(f"\n🎉 Lote {batch_id} marcado como completado.")

    except Exception as e:
        print(f"❌ Error fatal durante la ejecución: {e}")
        # Opcional: Marcar el lote como fallido si ocurre un error grave
        if 'batch_ref' in locals() and batch_ref:
            batch_ref.update({'status': 'failed', 'error': str(e)})
        raise

if __name__ == "__main__":
    main()
