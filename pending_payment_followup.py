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


def send_resend_email(api_key, to_email, user_name, order_number, device, imei, level, discount_link=None):
    """Envía el correo de seguimiento usando la API de Resend."""
    if not api_key:
        print(" - RESEND_API_KEY not found. Cannot send email.")
        return False

    url = "https://api.resend.com/emails"
    
    subjects = {
        1: f"Acción requerida para tu orden #{order_number}",
        2: f"Recordatorio: Tu equipo {device} aún necesita registro",
        3: f"Última oportunidad: ¡Registra tu IMEI con un 50% de descuento!",
    }
    
    html_contents = {
        1: f"""
            <p>Notamos que tu orden de registro <strong>#{order_number}</strong> para el equipo {device} (IMEI: {imei}) aún está pendiente de pago. ¡No te preocupes! Aún estás a tiempo de completarla.</p>
            <p>Completa el pago para que podamos iniciar el proceso y tener tu equipo listo para usar en todas las redes de Chile.</p>
        """,
        2: f"""
            <p>Solo un recordatorio amigable de que tu orden <strong>#{order_number}</strong> para registrar el equipo {device} (IMEI: {imei}) sigue pendiente. </p>
            <p>No dejes que tu equipo quede sin servicio. El proceso es 100% online y garantizado.</p>
        """,
        3: f"""
            <p>Vimos que aún no has completado el registro para tu equipo {device} (IMEI: {imei}). ¡No queremos que te quedes sin servicio!</p>
            <p>Como última oportunidad, te ofrecemos un <strong>descuento especial del 50%</strong> para que completes tu registro ahora. Esta oferta es válida solo a través del siguiente botón.</p>
        """
    }

    button_link = discount_link if level == 3 and discount_link else f"https://registroimeimultibanda.cl/dashboard?order_number={order_number}"

    html_body = f"""
    <!DOCTYPE html><html lang="es"><head><meta charset="UTF-8"><style>body{{font-family:sans-serif;}} .container{{max-width:580px; margin:auto; padding:20px; border:1px solid #ddd; border-radius:8px;}} .button{{background-color:#009959; color:white; padding:12px 24px; text-decoration:none; border-radius:5px; font-weight:bold;}}</style></head>
    <body><div class="container">
        <div style="text-align:center;"><img src="https://registroimeimultibanda.cl/Logo%20Registro%20IMEI%20Multibanda%20Chile.webp" width="180" alt="Logo"/></div>
        <h1 style="font-size:20px;">¡Hola, {user_name}!</h1>
        {html_contents[level]}
        <div style="text-align:center; margin:30px 0;"><a href="{button_link}" class="button">Completar Mi Pago Ahora</a></div>
        <hr/><p style="font-size:12px; color:#888; text-align:center;">Si ya realizaste el pago o tienes dudas, contacta a soporte.</p>
    </div></body></html>
    """

    payload = {
        "from": "Registro IMEI Multibanda <registro@registroimeimultibanda.cl>",
        "to": [to_email],
        "subject": subjects[level],
        "html": html_body
    }
    
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    
    try:
        response = requests.post(url, json=payload, headers=headers)
        response.raise_for_status()
        print(f"  - Correo de seguimiento (Nivel {level}) enviado a {to_email} para la orden {order_number}.")
        return True
    except requests.exceptions.RequestException as e:
        print(f"  - Error al enviar correo (Nivel {level}) a {to_email}: {e}")
        return False

def main():
    """Función principal del script."""
    print("🚀 Iniciando script de seguimiento de pagos pendientes...")
    
    try:
        db = initialize_firebase()
        resend_api_key = os.getenv('RESEND_API_KEY')
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
            
            email_sent = send_resend_email(
                api_key=resend_api_key,
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
