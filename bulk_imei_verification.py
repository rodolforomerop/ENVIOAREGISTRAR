import os
import base64
import json
import time
import html
from datetime import datetime, timezone

import requests
import firebase_admin
from firebase_admin import credentials, firestore
from google.cloud.firestore_v1 import FieldFilter

# ================== Config ==================
WOM_PAGE = "https://sucursalmiwom.wom.cl/listablanca/sello-multibanda/sello-multibandas.jsp#"
WOM_API  = "https://sucursalmiwom.wom.cl/listablanca/api/whitelist/consultaImeiInfo/v2"

REQ_TIMEOUT = 20
RETRIES_PER_IMEI = 2
SLEEP_BETWEEN_IMEIS = 1.0   # segundos entre IMEIs
SLEEP_BETWEEN_RETRIES = 0.6 # segundos entre reintentos

# Firestore
COL_BATCHES = "imei_batches"
SUBCOL_IMEIS = "imeis"
# ============================================


def initialize_firebase():
    """Inicializa Firebase Admin con credenciales base64 del env."""
    if not firebase_admin._apps:
        b64_creds = os.getenv('FIREBASE_CREDENTIALS_B64')
        if not b64_creds:
            raise ValueError("FIREBASE_CREDENTIALS_B64 no está configurada.")
        try:
            decoded = base64.b64decode(b64_creds).decode('utf-8')
            cred_dict = json.loads(decoded)
            cred = credentials.Certificate(cred_dict)
            firebase_admin.initialize_app(cred)
            print("✅ Firebase inicializado.")
        except Exception as e:
            raise ValueError(f"Error al decodificar/parsear FIREBASE_CREDENTIALS_B64: {e}")
    return firestore.client()


def wom_query(imei: str) -> dict:
    """
    Consulta directa al endpoint oficial de WOM usando sesión y backoff.
    Retorna:
      { ok:bool, estado:str|None, is5g:bool|None, mensaje:str|None,
        raw:str, http:int|None, error:str|None }
    ok=True sólo si hay HTTP 200 y un ESTADO presente.
    """
    if not imei or not str(imei).strip():
        return {"ok": False, "estado": None, "is5g": None, "mensaje": "IMEI vacío", "raw": "", "http": None, "error": "imei_vacio"}

    imei_str = str(imei).strip()
    payload = {"linea": "", "imei": imei_str, "token": ""}  # hoy token vacío funciona

    base_headers = {
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "Content-Type": "application/json",
        "X-Requested-With": "XMLHttpRequest",
        "Origin": "https://sucursalmiwom.wom.cl",
        "Referer": WOM_PAGE,
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Cache-Control": "no-cache",
    }

    s = requests.Session()
    estado = None
    is5g = None
    msg = None
    raw = ""
    http = None
    last_err = None

    for attempt in range(RETRIES_PER_IMEI + 1):
        try:
            # 1) GET previo para cookies/sesión
            s.get(WOM_PAGE, headers={"User-Agent": base_headers["User-Agent"]}, timeout=REQ_TIMEOUT)

            # 2) POST real
            r = s.post(WOM_API, json=payload, headers=base_headers, timeout=REQ_TIMEOUT)
            http = r.status_code
            raw = r.text

            data = None
            try:
                data = r.json()
            except ValueError:
                data = None

            if http == 200 and isinstance(data, dict):
                arr = (data or {}).get("Resultado") or []
                if arr:
                    it = arr[0]
                    estado = (it.get("ESTADO") or "").strip() or None
                    is5g = it.get("is5g")
                    msg = html.unescape((it.get("mensajeSubtel") or "")).replace("<br>", "\n")
                    # Sólo ok=True si hay ESTADO
                    return {"ok": bool(estado), "estado": estado, "is5g": is5g, "mensaje": msg, "raw": raw, "http": http, "error": None}

            last_err = f"http={http}, sin ESTADO"
        except requests.RequestException as e:
            last_err = f"red:{type(e).__name__} {e}"

        # backoff + jitter leve
        time.sleep(SLEEP_BETWEEN_RETRIES + 0.2 * attempt)

    return {"ok": False, "estado": None, "is5g": None, "mensaje": None, "raw": raw, "http": http, "error": last_err}


def check_imei_status(imei: str) -> str:
    """
    Devuelve SOLO:
      - 'Equipo Inscrito'       (si ESTADO != 'NOEN')
      - 'Equipo NO inscrito'    (si ESTADO == 'NOEN')
      - 'Sin resultado'         (si no se obtuvo ESTADO real tras reintentos)
    """
    for _ in range(RETRIES_PER_IMEI + 1):
        res = wom_query(imei)
        estado = (res.get("estado") or "").strip().upper()
        if res.get("ok") and estado:
            return "Equipo NO inscrito" if estado == "NOEN" else "Equipo Inscrito"
        time.sleep(SLEEP_BETWEEN_RETRIES)

    return "Sin resultado"


def send_completion_notification(batch_id: str, company_id: str, item_count: int):
    """
    Notifica a tu API Next.js que terminó el lote.
    """
    api_key = os.getenv('REGISTRATION_API_KEY')
    host_url = os.getenv('HOST_URL', 'https://registroimeimultibanda.cl')

    if not api_key or not host_url:
        print("⚠️ Notificación omitida: faltan REGISTRATION_API_KEY o HOST_URL.")
        return

    db = firestore.client()
    company_doc = db.collection('companies').document(company_id).get()
    if not company_doc.exists:
        print(f"⚠️ Empresa {company_id} no encontrada para notificar.")
        return

    owner_id = (company_doc.to_dict() or {}).get('ownerId')
    if not owner_id:
        print(f"⚠️ Empresa {company_id} sin ownerId.")
        return

    api_url = f"{host_url}/api/trigger-notification"
    headers = {"Authorization": f"Bearer {api_key}"}
    payload = {
        "userId": owner_id,
        "payload": {
            "title": "✅ Lote de Verificación Completado",
            "body": f"El lote {batch_id} con {item_count} IMEI(s) ha sido verificado. ¡Revisa los resultados!",
            "data": {"url": f"/dashboard?batch_id={batch_id}"}
        }
    }

    try:
        r = requests.post(api_url, json=payload, headers=headers, timeout=REQ_TIMEOUT)
        if r.status_code == 200:
            print(f"  - ✅ Notificación enviada al propietario {owner_id}.")
        else:
            print(f"  - ❌ Error al notificar. HTTP {r.status_code} -> {r.text[:300]}")
    except requests.RequestException as e:
        print(f"  - ❌ Excepción notificando: {type(e).__name__} {e}")


def main():
    """
    Verificación masiva por lote (subcolección imeis en 'imei_batches/{batch_id}/imeis')
    Requiere env: BATCH_ID
    """
    print("🚀 Iniciando Verificación Masiva de IMEI…")
    batch_id = os.getenv('BATCH_ID')
    if not batch_id:
        raise ValueError("BATCH_ID es requerido (variable de entorno).")

    db = initialize_firebase()

    batch_ref = db.collection(COL_BATCHES).document(batch_id)
    batch_doc = batch_ref.get()
    if not batch_doc.exists:
        raise ValueError(f"No se encontró el lote con ID: {batch_id}")

    batch_data = batch_doc.to_dict() or {}
    company_id = batch_data.get('companyId')

    # Leer items pending_verification sin warnings
    imeis_ref = batch_ref.collection(SUBCOL_IMEIS)
    q = imeis_ref.where(filter=FieldFilter('status', '==', 'pending_verification'))
    docs_to_process = list(q.stream())
    total_items = batch_data.get('itemCount', len(docs_to_process))

    if not docs_to_process:
        print("⚠️ No hay IMEIs pendientes en este lote.")
        if total_items == 0:
            batch_ref.update({'status': 'completed', 'completedAt': datetime.now(timezone.utc)})
        return

    print(f"📄 Lote: {batch_id} | Pendientes: {len(docs_to_process)}")
    processed_count = 0

    for doc in docs_to_process:
        d = doc.to_dict() or {}
        doc_id = doc.id
        imei1 = d.get('imei1')
        imei2 = d.get('imei2')

        print(f"\n  - Verificando documento: {doc_id} | IMEI1={imei1} IMEI2={imei2}")

        update_data = {'verifiedAt': datetime.now(timezone.utc)}

        # IMEI 1
        update_data['result1'] = check_imei_status(imei1) if imei1 else "Sin resultado"
        print(f"    -> Resultado IMEI 1: {update_data['result1']}")
        time.sleep(SLEEP_BETWEEN_IMEIS)

        # IMEI 2 (opcional)
        if imei2:
            update_data['result2'] = check_imei_status(imei2)
            print(f"    -> Resultado IMEI 2: {update_data['result2']}")
            time.sleep(SLEEP_BETWEEN_IMEIS)

        # Consolidado final
        r1 = (update_data.get('result1') or "").lower()
        r2 = (update_data.get('result2') or "").lower()
        if "equipo inscrito" in r1 or "equipo inscrito" in r2:
            update_data['result_final'] = "Equipo Inscrito"
        elif ("equipo no inscrito" in r1) and (("equipo no inscrito" in r2) or not r2):
            update_data['result_final'] = "Equipo NO inscrito"
        else:
            update_data['result_final'] = "Sin resultado"

        update_data['status'] = 'verified'
        try:
            imeis_ref.document(doc_id).update(update_data)
            print(f"    -> Documento {doc_id} actualizado a 'verified'. (Final: {update_data['result_final']})")
        except Exception as e:
            print(f"    -> ❌ Error actualizando {doc_id}: {e}")

        processed_count += 1

    print(f"\n✅ Verificados {processed_count} registro(s).")
    try:
        batch_ref.update({'status': 'completed', 'completedAt': datetime.now(timezone.utc)})
        print(f"🎉 Lote {batch_id} marcado como 'completed'.")
    except Exception as e:
        print(f"❌ Error marcando lote como completed: {e}")

    if company_id:
        send_completion_notification(batch_id, company_id, total_items)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"❌ Error fatal: {e}")
        # marca el lote como fallido si es posible
        try:
            db = firestore.client()
            batch_id = os.getenv('BATCH_ID')
            if batch_id:
                db.collection(COL_BATCHES).document(batch_id).update({
                    'status': 'failed',
                    'error': str(e),
                    'failedAt': datetime.now(timezone.utc)
                })
        except Exception:
            pass
        raise
