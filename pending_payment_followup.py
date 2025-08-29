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

def send_resend_email(api_key, to_email, user_name, order_number, device, imei, payment_method):
    """Sends the pending payment reminder email using the Resend API."""
    if not api_key:
        print(" - RESEND_API_KEY not found. Cannot send email.")
        return False

    url = "https://api.resend.com/emails"
    
    # Define the HTML content for the email
    html_content = f"""
    <!DOCTYPE html>
    <html lang="es">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <style>
            body {{ margin: 0; background-color: #f4f4f7; font-family: sans-serif; }}
            .container {{ background-color: #ffffff; border: 1px solid #e0e0e0; border-radius: 8px; margin: 32px auto; padding: 32px; max-width: 520px; }}
            .logo {{ text-align: center; }}
            .text {{ font-size: 16px; color: #333333; line-height: 1.6; }}
            .button-section {{ text-align: center; margin: 24px 0; }}
            .button {{ background-color: #009959; color: #ffffff; font-weight: 600; border-radius: 6px; padding: 12px 24px; text-decoration: none; }}
            .footer-text {{ font-size: 12px; color: #888888; text-align: center; }}
            .order-summary {{ background-color: #f9f9f9; border: 1px solid #eeeeee; border-radius: 6px; padding: 16px; margin: 24px 0; }}
            .order-summary-title {{ font-size: 16px; font-weight: bold; margin-bottom: 12px; }}
        </style>
    </head>
    <body>
        <div class="container">
            <div class="logo">
                <img src="https://registroimeimultibanda.cl/Logo%20Registro%20IMEI%20Multibanda%20Chile.webp" width="180" alt="Registro IMEI Multibanda Chile" />
            </div>
            <h1 style="font-size: 20px; font-weight: bold; margin-top: 32px;">¡Hola, {user_name}!</h1>
            <p class="text">
                Notamos que tu orden de registro <strong>#{order_number}</strong> aún está pendiente de pago. ¡No te preocupes! Aún estás a tiempo de completarla.
            </p>
            <div class="order-summary">
                <p class="order-summary-title">Resumen de tu Orden:</p>
                <p class="text" style="font-size: 14px; margin: 4px 0;"><strong>Dispositivo:</strong> {device}</p>
                <p class="text" style="font-size: 14px; margin: 4px 0;"><strong>IMEI:</strong> {imei}</p>
            </div>
            <p class="text">
                Completa el pago para que podamos iniciar el proceso y tener tu equipo listo para usar en todas las redes de Chile.
            </p>
            <div class="button-section">
                <a href="https://registroimeimultibanda.cl/dashboard?order_number={order_number}" class="button">
                    Completar Mi Pago Ahora
                </a>
            </div>
            <hr style="border: none; border-top: 1px solid #e0e0e0; margin: 24px 0;" />
            <p class="footer-text">
                Si ya realizaste el pago, por favor ignora este mensaje. Si tienes alguna duda, contacta a nuestro equipo de soporte.
            </p>
            <p class="footer-text">
                © {datetime.now().year} Registro IMEI Multibanda. Todos los derechos reservados.
            </p>
        </div>
    </body>
    </html>
    """

    payload = {
        "from": "Registro IMEI Multibanda <registro@registroimeimultibanda.cl>",
        "to": [to_email],
        "subject": f"Acción requerida para tu orden #{order_number}",
        "html": html_content
    }
    
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }
    
    try:
        response = requests.post(url, json=payload, headers=headers)
        response.raise_for_status()
        print(f"  - Reminder email sent successfully to {to_email} for order {order_number}.")
        return True
    except requests.exceptions.RequestException as e:
        print(f"  - Error sending email to {to_email} for order {order_number}: {e}")
        if e.response is not None:
            print(f"  - API Response: {e.response.text}")
        return False

def main():
    """Main function of the script."""
    print("🚀 Starting pending payment reminder script...")
    
    try:
        db = initialize_firebase()
        resend_api_key = os.getenv('RESEND_API_KEY')
        main_company_id = os.getenv('MAIN_COMPANY_ID')
    except Exception as e:
        print(f"Failed to initialize. Aborting. Error: {e}")
        return
        
    if not main_company_id:
        print("⚠️ MAIN_COMPANY_ID environment variable not set. Cannot filter for direct sales. Aborting.")
        return

    now = datetime.now(timezone.utc)
    one_hour_ago = now - timedelta(hours=1)
    twenty_four_hours_ago = now - timedelta(hours=24)

    registros_ref = db.collection('registros')
    
    # Query for orders that are pending payment, older than 1 hour, newer than 24 hours,
    # belong to the main company, and haven't had a follow-up sent.
    query = registros_ref.where('status', '==', 'Pendiente de Pago') \
                         .where('companyId', '==', main_company_id) \
                         .where('followUpSent', '==', False) \
                         .where('createdAt', '<=', one_hour_ago) \
                         .where('createdAt', '>=', twenty_four_hours_ago)
    
    docs_to_process = list(query.stream())

    if not docs_to_process:
        print("✅ No pending payment orders found matching the criteria. Finishing.")
        return

    print(f"Found {len(docs_to_process)} orders to process.")

    for doc in docs_to_process:
        data = doc.to_dict()
        doc_id = doc.id
        
        print(f"\n- Processing order: {doc_id} for {data.get('customerEmail')}")

        email_sent = send_resend_email(
            api_key=resend_api_key,
            to_email=data.get('customerEmail'),
            user_name=data.get('customerName'),
            order_number=doc_id,
            device=f"{data.get('brand', '')} {data.get('model', '')}",
            imei=data.get('imei1'),
            payment_method=data.get('paymentMethod')
        )

        if email_sent:
            doc.reference.update({'followUpSent': True})
            print(f"  - Marked order {doc_id} as followUpSent: True.")

    print("\n🎉 Pending payment reminder process completed.")

if __name__ == "__main__":
    main()
