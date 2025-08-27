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
    """Verifica un solo IMEI usando la API externa y acorta el resultado."""
    if not imei or not str(imei).strip():
        return "Vacío"
        
    url = "https://verificador-imei.onrender.com/verificar"
    try:
        response = requests.post(url, json={"imei": str(imei).strip()}, timeout=20)
        response.raise_for_status()
        
        full_result = response.json().get('resultado', 'Error: Respuesta inesperada')
        if "no está inscrito" in full_result.lower():
            return "Equipo NO inscrito"
        elif "equipo se encuentra inscrito" in full_result.lower():
            return "Equipo inscrito correctamente"
        else:
            return full_result

    except requests.exceptions.RequestException as e:
        print(f"  - Error de red para IMEI {imei}: {e}")
        return "Error de Conexión"
    except Exception as e:
        print(f"  - Error inesperado para IMEI {imei}: {e}")
        return "Error en Script"

def send_completion_notification(batch_id, company_id, item_count):
    """Llama a la API de la app Next.js para enviar una notificación push."""
    api_key = os.getenv('REGISTRATION_API_KEY')
    host_url = os.getenv('HOST_URL', 'https://registroimeimultibanda.cl')
    
    if not api_key or not host_url:
        print("⚠️ No se pueden enviar notificaciones: REGISTRATION_API_KEY o HOST_URL no están configuradas.")
        return
    
    db = firestore.client()
    company_ref = db.collection('companies').document(company_id)
    company_doc = company_ref.get()
    if not company_doc.exists:
        print(f"⚠️ No se encontró la empresa con ID {company_id} para notificar.")
        return
        
    owner_id = company_doc.to_dict().get('ownerId')
    if not owner_id:
        print(f"⚠️ La empresa {company_id} no tiene un propietario asignado.")
        return

    api_url = f"{host_url}/api/trigger-notification"
    headers = {"Authorization": f"Bearer {api_key}"}
    payload = {
        "userId": owner_id,
        "payload": {
            "title": "✅ Lote de Verificación Completado",
            "body": f"El lote {batch_id} con {item_count} IMEI(s) ha sido verificado. ¡Revisa los resultados!",
            "data": {
                "url": f"/dashboard?batch_id={batch_id}"
            }
        }
    }
    
    try:
        response = requests.post(api_url, json=payload, headers=headers)
        if response.status_code == 200:
            print(f"  - ✅ Notificación enviada exitosamente al propietario {owner_id}.")
        else:
            print(f"  - ❌ Error al enviar notificación. Código: {response.status_code}, Respuesta: {response.text}")
    except Exception as e:
        print(f"  - ❌ Excepción al intentar enviar notificación: {e}")


def main():
    """Función principal del script de verificación masiva desde Firestore."""
    print("🚀 Iniciando Verificación Masiva de IMEI desde Firestore...")
    
    batch_id = os.getenv('BATCH_ID')
    db = None
    batch_ref = None

    try:
        db = initialize_firebase()
        
        if not batch_id:
            raise ValueError("BATCH_ID es una variable de entorno requerida.")

        print(f"📄 Procesando Lote de Verificación: {batch_id}")

        batch_ref = db.collection('imei_batches').document(batch_id)
        batch_doc = batch_ref.get()
        if not batch_doc.exists:
             raise ValueError(f"No se encontró el lote con el ID: {batch_id}")
        
        batch_data = batch_doc.to_dict()
        company_id = batch_data.get('companyId')
        
        imeis_ref = batch_ref.collection('imeis')
        docs_to_process_stream = imeis_ref.where('status', '==', 'pending_verification').stream()
        docs_to_process = list(docs_to_process_stream)
        
        total_items = batch_data.get('itemCount', len(docs_to_process))
        processed_count = 0
        
        for doc in docs_to_process:
            imei_data = doc.to_dict()
            imei1 = imei_data.get('imei1')
            imei2 = imei_data.get('imei2')
            doc_id = doc.id
            
            print(f"\n  - Verificando documento: {doc_id} (IMEI1: {imei1})")
            
            update_data = {'verifiedAt': datetime.now(timezone.utc)}
            
            if imei1:
                update_data['result1'] = check_imei_status(imei1)
                print(f"    -> Resultado IMEI 1: {update_data['result1']}")
                time.sleep(1)

            if imei2:
                update_data['result2'] = check_imei_status(imei2)
                print(f"    -> Resultado IMEI 2: {update_data['result2']}")
                time.sleep(1)
            
            update_data['status'] = 'verified'
            imeis_ref.document(doc_id).update(update_data)
            print(f"    -> Documento {doc_id} actualizado a 'verified'.")
            processed_count += 1
            
        if processed_count == 0:
            print("⚠️ No se encontraron IMEIs pendientes en este lote.")
        else:
            print(f"\n✅ Verificados {processed_count} registros.")

        batch_ref.update({'status': 'completed', 'completedAt': datetime.now(timezone.utc)})
        print(f"\n🎉 Lote {batch_id} marcado como completado.")
        
        if company_id:
            send_completion_notification(batch_id, company_id, total_items)

    except Exception as e:
        print(f"❌ Error fatal durante la ejecución: {e}")
        if batch_ref:
            try:
                batch_ref.update({'status': 'failed', 'error': str(e)})
            except Exception as update_err:
                print(f"Error adicional al intentar marcar el lote como fallido: {update_err}")
        raise

if __name__ == "__main__":
    main()
