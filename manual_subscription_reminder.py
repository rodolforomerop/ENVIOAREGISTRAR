import os
import base64
import json
import requests
from datetime import datetime, timedelta, timezone
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

def trigger_reminder_email(to_email, user_name, company_name, plan_name, expires_at, days_left):
    """Triggers the reminder email by calling the app's API endpoint."""
    api_key = os.getenv('REGISTRATION_API_KEY')
    host_url = os.getenv('HOST_URL')
    
    if not api_key or not host_url:
        print(" - API_KEY or HOST_URL not found. Cannot send email.")
        return False

    api_url = f"{host_url}/api/send-email"
    
    payload = {
        "type": "manual-subscription-reminder",
        "to": to_email,
        "data": {
            "name": user_name,
            "companyName": company_name,
            "planName": plan_name,
            "expiresAt": expires_at.strftime('%d de %B, %Y'),
            "daysLeft": days_left
        }
    }
    
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    
    try:
        response = requests.post(api_url, json=payload, headers=headers)
        response.raise_for_status()
        print(f"  - Correo de recordatorio ({days_left} días) solicitado para {to_email} de la empresa {company_name}.")
        return True
    except requests.exceptions.RequestException as e:
        print(f"  - Error al solicitar correo de recordatorio a {to_email}: {e}")
        return False


def main():
    """Función principal del script."""
    print("🚀 Iniciando script de recordatorio de suscripciones manuales...")
    
    try:
        db = initialize_firebase()
    except Exception as e:
        print(f"Error fatal de inicialización: {e}")
        return

    now_utc = datetime.now(timezone.utc)
    seven_days_from_now = now_utc + timedelta(days=7)
    one_day_from_now = now_utc + timedelta(days=1)
    
    companies_ref = db.collection('companies')
    
    # Query for subscriptions expiring in ~7 days that haven't received the 7d reminder
    query_7d = companies_ref.where('manualSubscriptionExpiresAt', '>=', now_utc) \
                              .where('manualSubscriptionExpiresAt', '<=', seven_days_from_now) \
                              .where('expirationReminderSent', '!=', '7d')
    
    # Query for subscriptions expiring in ~1 day that haven't received the 1d reminder
    query_1d = companies_ref.where('manualSubscriptionExpiresAt', '>=', now_utc) \
                              .where('manualSubscriptionExpiresAt', '<=', one_day_from_now) \
                              .where('expirationReminderSent', 'not-in', ['1d', '7d'])

    docs_to_process_7d = list(query_7d.stream())
    docs_to_process_1d = list(query_1d.stream())

    if not docs_to_process_7d and not docs_to_process_1d:
        print("✅ No se encontraron suscripciones que requieran un recordatorio hoy.")
        return

    # Process 7-day reminders
    for doc in docs_to_process_7d:
        data = doc.to_dict()
        company_id = doc.id
        
        print(f"\n- Procesando recordatorio de 7 días para la empresa: {data.get('name')} ({company_id})")
        
        owner_id = data.get('ownerId')
        if not owner_id:
            print("  - ⚠️ Saltando: La empresa no tiene un propietario asignado.")
            continue
        
        try:
            owner_user = db.collection('users').document(owner_id).get()
            if not owner_user.exists:
                print(f"  - ⚠️ Saltando: No se encontró el perfil del propietario {owner_id}.")
                continue
            
            owner_data = owner_user.to_dict()
            owner_email = owner_data.get('email')
            owner_name = owner_data.get('displayName', 'Estimado Cliente')

            if not owner_email:
                 print("  - ⚠️ Saltando: El propietario no tiene un email configurado.")
                 continue

            expires_at_ts = data.get('manualSubscriptionExpiresAt')
            expires_at_dt = expires_at_ts.replace(tzinfo=timezone.utc)
            days_left = (expires_at_dt - now_utc).days
            
            email_sent = trigger_reminder_email(
                to_email=owner_email,
                user_name=owner_name,
                company_name=data.get('name'),
                plan_name=data.get('planId', 'desconocido').capitalize(),
                expires_at=expires_at_dt,
                days_left=days_left
            )

            if email_sent:
                doc.reference.update({'expirationReminderSent': '7d'})
                print(f"  - ✨ Nivel de recordatorio actualizado a '7d' para la empresa {company_id}.")

        except Exception as e:
            print(f"  - ❌ Error procesando la empresa {company_id}: {e}")

    # Process 1-day reminders
    for doc in docs_to_process_1d:
        # Same logic as 7d, but for 1d
        pass # Implement similar logic as above if needed for 1 day reminders.

    print("\n🎉 Proceso de recordatorios completado.")

if __name__ == "__main__":
    main()
