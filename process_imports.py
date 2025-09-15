import os
import base64
import json
import requests
import time
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

def generate_serial_number(brand, model):
    """Calls the app's API to generate a serial number."""
    api_key = os.getenv('REGISTRATION_API_KEY')
    host_url = os.getenv('HOST_URL', 'https://registroimeimultibanda.cl')
    
    if not api_key or not host_url:
        print("  - ⚠️ Cannot generate serial number: REGISTRATION_API_KEY or HOST_URL not configured.")
        return None
    
    api_url = f"{host_url}/api/generate-serial-number"
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    payload = {"brand": brand, "model": model}
    
    try:
        response = requests.post(api_url, json=payload, headers=headers, timeout=20)
        response.raise_for_status()
        serial = response.json().get('serialNumber')
        if serial:
            return serial
        else:
            print(f"  - ❌ API did not return a serial number for {brand} {model}.")
            return None
    except requests.exceptions.RequestException as e:
        print(f"  - ❌ Network error generating serial number: {e}")
        return None
    except Exception as e:
        print(f"  - ❌ Exception generating serial number: {e}")
        return None

def main():
    """Main function for the import processing script."""
    print("🚀 Starting Import Batch Processing from Firestore...")
    
    batch_id = os.getenv('BATCH_ID')
    db = None
    batch_ref = None

    try:
        db = initialize_firebase()
        
        if not batch_id:
            raise ValueError("BATCH_ID is a required environment variable.")

        print(f"📄 Processing Import Batch: {batch_id}")

        batch_ref = db.collection('pending_imports').document(batch_id)
        batch_doc = batch_ref.get()
        if not batch_doc.exists:
             raise ValueError(f"Batch with ID not found: {batch_id}")
        
        batch_data = batch_doc.to_dict()
        items_ref = batch_ref.collection('items')
        docs_to_process = list(items_ref.stream())
        
        if not docs_to_process:
            print("⚠️ No items found in this batch to process.")
            batch_ref.update({'status': 'completed', 'error': 'No items found.'})
            return
            
        print(f"Found {len(docs_to_process)} items to process.")
        
        processed_count = 0
        final_batch = db.batch()

        for item_doc in docs_to_process:
            item_data = item_doc.to_dict()
            
            # Generate new order number
            timestamp = int(time.time() * 1000)
            order_number = f"CR-{timestamp}-{processed_count}"

            new_registration_data = {
                "orderNumber": order_number,
                "userId": batch_data.get('userId'),
                "companyId": batch_data.get('companyId'),
                "customerName": batch_data.get('customerName'),
                "customerEmail": batch_data.get('customerEmail'),
                "paymentMethod": 'Credits' if batch_data.get('processingMethod') == 'internal' else 'Manual',
                "status": 'Recibido' if batch_data.get('processingMethod') == 'internal' else 'Pendiente de Envío',
                "createdAt": datetime.now(timezone.utc),
                "paymentDate": datetime.now(timezone.utc) if batch_data.get('processingMethod') == 'internal' else None,
                "batchId": batch_id,
                **item_data  # Add data from the imported row
            }
            
            # Generate serial number if missing for smartphone
            if new_registration_data.get('deviceType') == 'smartphone' and not new_registration_data.get('serialNumber'):
                print(f"  - Generating serial for {new_registration_data.get('brand')} {new_registration_data.get('model')}...")
                serial = generate_serial_number(new_registration_data.get('brand'), new_registration_data.get('model'))
                if serial:
                    new_registration_data['serialNumber'] = serial
                    print(f"    -> Generated: {serial}")
                else:
                    print(f"    -> Failed to generate serial. Continuing without it.")
                time.sleep(1) # API rate limit

            # Clean up None values
            final_data_to_save = {k: v for k, v in new_registration_data.items() if v is not None}

            # Add to batch for final insertion
            reg_ref = db.collection('registros').document(order_number)
            final_batch.set(reg_ref, final_data_to_save)
            processed_count += 1
            print(f"  - Prepared registration {order_number} for saving.")
        
        # Commit all new registrations at once
        final_batch.commit()
        print(f"\n✅ Successfully committed {processed_count} new registrations to 'registros' collection.")

        # Update company stats if internal processing
        if batch_data.get('processingMethod') == 'internal':
            company_ref = db.collection('companies').document(batch_data.get('companyId'))
            company_ref.update({'credits': firestore.FieldValue.increment(-processed_count)})
            print(f"  - Deducted {processed_count} credits from company {batch_data.get('companyId')}.")

        # Mark the batch as completed
        batch_ref.update({'status': 'completed', 'completedAt': datetime.now(timezone.utc)})
        print(f"\n🎉 Batch {batch_id} marked as completed.")
        
        # Send a single summary email
        api_key = os.getenv('REGISTRATION_API_KEY')
        host_url = os.getenv('HOST_URL')
        if api_key and host_url:
            requests.post(f"{host_url}/api/send-email", 
                headers={"Authorization": f"Bearer {api_key}"},
                json={
                    "type": "registration-batch-completed",
                    "to": batch_data.get('customerEmail'),
                    "data": {
                        "name": batch_data.get('customerName'),
                        "batchId": batch_id,
                        "count": processed_count,
                    }
                }
            )
            print("  - Requested summary email.")

    except Exception as e:
        print(f"❌ Fatal error during script execution: {e}")
        if batch_ref:
            try:
                batch_ref.update({'status': 'failed', 'error': str(e)})
            except Exception as update_err:
                print(f"Additional error while trying to mark batch as failed: {update_err}")
        raise

if __name__ == "__main__":
    main()

    
