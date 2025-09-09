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
        except (json.JSONDecodeError, TypeError, ValueError) as e:
            raise ValueError(f"Error decoding or parsing FIREBASE_CREDENTIALS_B64: {e}")
            
    return firestore.client()

def check_external_status(imei):
    """Verifica si el IMEI ya está inscrito a través de la API externa."""
    if not imei:
        return False
    url = "https://verificador-imei.onrender.com/verificar"
    try:
        response = requests.post(url, json={"imei": str(imei).strip()}, timeout=20)
        if response.status_code == 200:
            full_result = response.json().get('resultado', '')
            return "equipo se encuentra inscrito" in full_result.lower()
    except requests.exceptions.RequestException:
        pass # Ignora errores de conexión para no detener el flujo
    return False

def generate_discounted_link(order_number):
    """Llama a la API interna para generar un enlace de pago con descuento."""
    api_key = os.getenv('REGISTRATION_API_KEY')
    host_url = os.getenv('HOST_URL')
    if not api_key or not host_url:
        print(f"  - ⚠️ No se puede generar enlace con descuento para {order_number}: API Key o Host URL no configuradas.")
        return None

    api_url = f"{host_url}/api/create-discounted-payment-link"
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    payload = {"orderNumber": order_number}

    try:
        response = requests.post(api_url, json=payload, headers=headers)
        if response.status_code == 200:
            return response.json().get('paymentUrl')
        else:
            print(f"  - ❌ Error al generar enlace con descuento para {order_number}. Código: {response.status_code}, Respuesta: {response.text}")
            return None
    except Exception as e:
        print(f"  - ❌ Excepción al generar enlace con descuento para {order_number}: {e}")
        return None

def trigger_follow_up_email(to_email, user_name, order_number, device, imei, level, discount_link=None):
    """Triggers the follow-up email by calling the app's API endpoint."""
    api_key = os.getenv('REGISTRATION_API_KEY')
    host_url = os.getenv('HOST_URL')
    
    if not api_key or not host_url:
        print(" - API_KEY or HOST_URL not found. Cannot send email.")
        return False

    api_url = f"{host_url}/api/send-followup-email"
    
    payload = {
        "to": to_email,
        "data": {
            "name": user_name,
            "orderNumber": order_number,
            "device": device,
            "imei": imei,
            "paymentMethod": "MercadoPago", # Default a un método que no sea Manual
            "discount": level == 3,
            "discountLink": discount_link
        }
    }
    
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    
    try:
        response = requests.post(api_url, json=payload, headers=headers)
        response.raise_for_status()
        print(f"  - Correo de seguimiento (Nivel {level}) solicitado para {to_email} para la orden {order_number}.")
        return True
    except requests.exceptions.RequestException as e:
        print(f"  - Error al solicitar correo (Nivel {level}) a {to_email}: {e}")
        return False


def main():
    """Función principal del script."""
    print("🚀 Iniciando script de seguimiento de pagos pendientes...")
    
    try:
        db = initialize_firebase()
        main_company_id = os.getenv('MAIN_COMPANY_ID')
    except Exception as e:
        print(f"Error fatal de inicialización: {e}")
        return
        
    if not main_company_id:
        print("⚠️ MAIN_COMPANY_ID no configurado. Abortando.")
        return

    now_utc = datetime.now(timezone.utc)
    
    registros_ref = db.collection('registros')
    
    query = registros_ref.where('status', '==', 'Pendiente de Pago') \
                         .where('companyId', '==', main_company_id) \
                         .where('followUpLevel', '<', 3)
    
    docs_to_process = list(query.stream())

    if not docs_to_process:
        print("✅ No se encontraron órdenes pendientes que requieran seguimiento. Finalizando.")
        return

    print(f"Se encontraron {len(docs_to_process)} órdenes candidatas para seguimiento.")

    for doc in docs_to_process:
        data = doc.to_dict()
        doc_id = doc.id
        
        print(f"\n- Procesando orden: {doc_id} para {data.get('customerEmail')}")

        # --- Comprobaciones Inteligentes ---
        imei_a_verificar = data.get('imei1')
        email_cliente = data.get('customerEmail')
        
        # 1. ¿El cliente ya pagó otra orden con el mismo IMEI?
        paid_orders_query = registros_ref.where('imei1', '==', imei_a_verificar) \
                                           .where('customerEmail', '==', email_cliente) \
                                           .where('status', '!=', 'Pendiente de Pago') \
                                           .limit(1).stream()
        if any(paid_orders_query):
            print(f"  - ✅ El cliente ya pagó otra orden para este IMEI. Cancelando esta orden pendiente.")
            doc.reference.update({'status': 'Cancelado', 'followUpLevel': 4}) # Nivel 4 para indicar cancelado por sistema
            continue
            
        # 2. ¿El IMEI ya fue registrado por otro medio?
        if check_external_status(imei_a_verificar):
            print(f"  - ✅ El IMEI {imei_a_verificar} ya se encuentra registrado. Cancelando esta orden.")
            doc.reference.update({'status': 'Cancelado', 'followUpLevel': 4})
            continue

        # --- Lógica de Tiempo y Niveles ---
        created_at = data.get('createdAt').replace(tzinfo=timezone.utc)
        hours_since_creation = (now_utc - created_at).total_seconds() / 3600
        current_level = data.get('followUpLevel', 0)

        should_send = False
        next_level = 0

        if current_level == 0 and hours_since_creation >= 1:
            should_send, next_level = True, 1
        elif current_level == 1 and hours_since_creation >= 24:
            should_send, next_level = True, 2
        elif current_level == 2 and hours_since_creation >= 72: # 3 días
            should_send, next_level = True, 3
        
        if should_send:
            print(f"  - 📧 La orden califica para el correo de Nivel {next_level}.")
            
            discount_link = None
            if next_level == 3:
                discount_link = generate_discounted_link(doc_id)
            
            email_sent = trigger_follow_up_email(
                to_email=email_cliente,
                user_name=data.get('customerName'),
                order_number=doc_id,
                device=f"{data.get('brand', '')} {data.get('model', '')}",
                imei=imei_a_verificar,
                level=next_level,
                discount_link=discount_link
            )

            if email_sent:
                doc.reference.update({'followUpLevel': next_level})
                print(f"  - ✨ Nivel de seguimiento actualizado a {next_level} para la orden {doc_id}.")
        else:
            print(f"  - ⏳ Aún no es tiempo para el siguiente recordatorio (Nivel actual: {current_level}, Horas: {hours_since_creation:.2f}).")


    print("\n🎉 Proceso de seguimiento de pagos pendientes completado.")

if __name__ == "__main__":
    main()
