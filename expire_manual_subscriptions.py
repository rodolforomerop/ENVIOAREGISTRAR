import os
import base64
import json
import requests
from datetime import datetime, timezone
import firebase_admin
from firebase_admin import credentials, firestore

def initialize_firebase():
    """Initializes the Firebase Admin app if not already initialized."""
    if not firebase_admin._apps:
        b64_creds = os.getenv('FIREBASE_CREDENTIALS_B64')
        if not b64_creds:
            raise ValueError("Environment variable FIREBASE_CREDENTIALS_B64 is not set.")
        
        try:
            decoded_creds_str = base64.b64decode(b64_creds).decode('utf-8')
            firebase_creds_dict = json.loads(decoded_creds_str)
            cred = credentials.Certificate(firebase_creds_dict)
            firebase_admin.initialize_app(cred)
        except Exception as e:
            raise ValueError(f"Error decoding or parsing FIREBASE_CREDENTIALS_B64: {e}")
            
    return firestore.client()

def trigger_expiration_email(to_email, user_name, company_name, plan_name):
    """Triggers the expiration email by calling the app's API endpoint."""
    api_key = os.getenv('REGISTRATION_API_KEY')
    host_url = os.getenv('HOST_URL')
    
    if not api_key or not host_url:
        print(" - API_KEY or HOST_URL not found. Cannot send email.")
        return False

    api_url = f"{host_url}/api/send-email"
    
    payload = {
        "type": "manual-subscription-expired",
        "to": to_email,
        "data": {
            "name": user_name,
            "companyName": company_name,
            "planName": plan_name,
        }
    }
    
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    
    try:
        response = requests.post(api_url, json=payload, headers=headers)
        response.raise_for_status()
        print(f"  - Correo de expiración solicitado para {to_email} de la empresa {company_name}.")
        return True
    except requests.exceptions.RequestException as e:
        print(f"  - Error al solicitar correo de expiración a {to_email}: {e}")
        return False

def main():
    """Main function to expire manual subscriptions."""
    print("🚀 Iniciando script para expirar suscripciones manuales...")
    
    try:
        db = initialize_firebase()
    except Exception as e:
        print(f"Error fatal de inicialización: {e}")
        return

    now_utc = datetime.now(timezone.utc)
    
    companies_ref = db.collection('companies')
    
    # Query for companies with a manual expiration date in the past
    query = companies_ref.where('manualSubscriptionExpiresAt', '<=', now_utc)
    
    docs_to_process = list(query.stream())

    if not docs_to_process:
        print("✅ No se encontraron suscripciones manuales expiradas para procesar.")
        return

    print(f"Se encontraron {len(docs_to_process)} empresas con suscripciones manuales expiradas.")

    for doc in docs_to_process:
        data = doc.to_dict()
        company_id = doc.id
        
        print(f"\n- Procesando expiración para la empresa: {data.get('name')} ({company_id})")

        owner_id = data.get('ownerId')
        if not owner_id:
            print("  - ⚠️ Saltando: La empresa no tiene un propietario asignado.")
            continue
        
        try:
            # Revert plan to 'individual'
            update_data = {
                'planId': 'individual',
                'manualSubscriptionExpiresAt': firestore.DELETE_FIELD,
                'expirationReminderSent': firestore.DELETE_FIELD
            }
            doc.reference.update(update_data)
            print(f"  - ✅ Plan de la empresa {company_id} revertido a 'individual'.")
            
            # Send notification email
            owner_user_doc = db.collection('users').document(owner_id).get()
            if owner_user_doc.exists:
                owner_data = owner_user_doc.to_dict()
                owner_email = owner_data.get('email')
                owner_name = owner_data.get('displayName', 'Estimado Cliente')

                if owner_email:
                    trigger_expiration_email(
                        to_email=owner_email,
                        user_name=owner_name,
                        company_name=data.get('name'),
                        plan_name=data.get('planId', 'desconocido').capitalize()
                    )
            else:
                 print(f"  - ⚠️ No se encontró el perfil del propietario {owner_id} para enviar notificación.")

        except Exception as e:
            print(f"  - ❌ Error procesando la expiración de la empresa {company_id}: {e}")

    print("\n🎉 Proceso de expiración de suscripciones completado.")

if __name__ == "__main__":
    main()
