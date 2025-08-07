# sync_woocommerce.py - Script para GitHub Actions que dispara la sincronización de WooCommerce

import os
import requests

# --- CONFIGURACIÓN ---
# Leídos desde los Secretos de GitHub
REGISTRATION_API_KEY = os.environ.get('REGISTRATION_API_KEY')
HOST_URL = os.environ.get('HOST_URL', 'https://registroimeimultibanda.cl')


def trigger_sync():
    """
    Llama a la API de la aplicación Next.js para iniciar la sincronización de WooCommerce.
    """
    if not REGISTRATION_API_KEY:
        print("❌ Error: La variable de entorno REGISTRATION_API_KEY no está configurada.")
        return

    api_url = f"{HOST_URL}/api/sync-woocommerce"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {REGISTRATION_API_KEY}"
    }

    print(f"🚀 Disparando la sincronización de WooCommerce en {api_url}...")

    try:
        response = requests.post(api_url, headers=headers, timeout=120) # Timeout de 2 minutos
        
        # Lanza una excepción si la respuesta es un código de error (4xx o 5xx)
        response.raise_for_status()

        response_json = response.json()
        print("✅ Sincronización completada exitosamente.")
        print("--- Resumen ---")
        for result in response_json.get('results', []):
            store_id = result.get('storeId', 'Desconocida')
            if result.get('success'):
                imported = result.get('importedCount', 0)
                status = "Éxito"
                if imported > 0:
                    print(f"  - Tienda '{store_id}': {status} - Se importaron {imported} pedidos.")
                else:
                    print(f"  - Tienda '{store_id}': {status} - No se encontraron nuevos pedidos para importar.")
            else:
                status = "Fallido"
                error_msg = result.get('error', 'Error desconocido')
                print(f"  - Tienda '{store_id}': {status} - Error: {error_msg}")
        print("---------------")

    except requests.exceptions.HTTPError as http_err:
        print(f"❌ Error HTTP durante la sincronización: {http_err}")
        print(f"   Código de estado: {http_err.response.status_code}")
        try:
            # Intenta imprimir la respuesta del error si es JSON
            print(f"   Respuesta del servidor: {http_err.response.json()}")
        except ValueError:
            print(f"   Respuesta del servidor: {http_err.response.text}")
    except requests.exceptions.RequestException as e:
        print(f"❌ Excepción de red al intentar la sincronización: {e}")
    except Exception as e:
        print(f"❌ Ocurrió un error inesperado: {e}")


# --- LÓGICA PRINCIPAL ---
if __name__ == "__main__":
    trigger_sync()
