# script.py - Verificación IMEI desde Google Sheets (sin Selenium, usando WOM API)
import os
import time
import json
import html
import requests
import gspread
from datetime import datetime, timezone

# === Configuración desde Secrets/Env ===
GSPREAD_SHEET_NAME = os.environ.get('GSPREAD_SHEET_NAME')  # nombre de la hoja
GSPREAD_CREDENTIALS = os.environ.get('GSPREAD_CREDENTIALS')  # JSON de service account (texto)
ESTADO_A_BUSCAR = "En Proceso"
ESTADO_FINALIZADO = "Listo"
COLUMNA_IMEI = "IMEI 1"
COLUMNA_ESTADO = "Estado"

# WOM
WOM_PAGE = "https://sucursalmiwom.wom.cl/listablanca/sello-multibanda/sello-multibandas.jsp#"
WOM_API  = "https://sucursalmiwom.wom.cl/listablanca/api/whitelist/consultaImeiInfo/v2"
REQ_TIMEOUT = 20
RETRIES_PER_IMEI = 2
SLEEP_BETWEEN_RETRIES = 0.6
SLEEP_BETWEEN_ROWS = 0.5  # para no castigar la API de Sheets ni WOM

# =============== WOM helpers ==================
def wom_query(imei: str) -> dict:
    """
    Consulta directa al endpoint oficial de WOM usando requests.Session() y backoff.
    Retorna: { ok, estado, is5g, mensaje, raw, http, error }
    ok=True solo si hay HTTP 200 y un ESTADO presente.
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
    raw = ""
    http = None
    last_err = None

    for attempt in range(RETRIES_PER_IMEI + 1):
        try:
            # GET previo para cookies/sesión
            s.get(WOM_PAGE, headers={"User-Agent": base_headers["User-Agent"]}, timeout=REQ_TIMEOUT)
            # POST real
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
                    estado = (it.get("ESTADO") or "").strip()
                    is5g = it.get("is5g")
                    msg = html.unescape((it.get("mensajeSubtel") or "")).replace("<br>", "\n")
                    return {"ok": bool(estado), "estado": estado, "is5g": is5g, "mensaje": msg, "raw": raw, "http": http, "error": None}

            last_err = f"http={http}, sin ESTADO"
        except requests.RequestException as e:
            last_err = f"red:{type(e).__name__} {e}"

        time.sleep(SLEEP_BETWEEN_RETRIES + 0.2 * attempt)

    return {"ok": False, "estado": None, "is5g": None, "mensaje": None, "raw": raw, "http": http, "error": last_err}


def clasificar_estado_simple(estado: str | None) -> str:
    """
    Mapea el ESTADO de WOM a solo dos etiquetas:
      - 'Equipo NO inscrito' si ESTADO == 'NOEN'
      - 'Equipo Inscrito'    en cualquier otro caso con ESTADO presente
      - 'Sin resultado'      si no hay estado
    """
    if not estado:
        return "Sin resultado"
    return "Equipo NO inscrito" if str(estado).strip().upper() == "NOEN" else "Equipo Inscrito"


# =============== Google Sheets helpers ==================
def conectar_a_google_sheets():
    """Conecta con Google Sheets usando credenciales JSON (texto en env)."""
    if not GSPREAD_CREDENTIALS:
        raise RuntimeError("El secreto 'GSPREAD_CREDENTIALS' no está definido.")
    if not GSPREAD_SHEET_NAME:
        raise RuntimeError("El secreto 'GSPREAD_SHEET_NAME' no está definido.")

    creds_dict = json.loads(GSPREAD_CREDENTIALS)
    gc = gspread.service_account_from_dict(creds_dict)
    ws = gc.open(GSPREAD_SHEET_NAME).sheet1
    return ws


def obtener_indices_columnas(ws):
    """Lee la fila de encabezados y devuelve dict nombre->indice (1-based)."""
    headers = ws.row_values(1)
    mapa = {h.strip(): i+1 for i, h in enumerate(headers)}
    if COLUMNA_IMEI not in mapa or COLUMNA_ESTADO not in mapa:
        raise RuntimeError(f"No se hallaron columnas '{COLUMNA_IMEI}' y/o '{COLUMNA_ESTADO}' en la fila 1.")
    return mapa


# =================== Main =====================
if __name__ == "__main__":
    print("🚀 Verificación de IMEI desde Google Sheets (sin Selenium)")
    try:
        ws = conectar_a_google_sheets()
        print("✅ Conectado a Google Sheets.")

        col_map = obtener_indices_columnas(ws)
        col_estado_idx = col_map[COLUMNA_ESTADO]
        col_imei_idx = col_map[COLUMNA_IMEI]

        # Trae todas las filas como dicts (usa encabezados)
        filas = ws.get_all_records()
        print(f"📄 Filas leídas: {len(filas)}")

        for i, fila in enumerate(filas, start=2):  # datos empiezan en fila 2
            estado_actual = str(fila.get(COLUMNA_ESTADO) or "").strip()
            imei = str(fila.get(COLUMNA_IMEI) or "").strip()

            if estado_actual != ESTADO_A_BUSCAR or not imei:
                continue

            print(f"\n🔎 Fila {i} | IMEI: {imei}")
            # Consulta WOM
            res = wom_query(imei)
            if res.get("ok"):
                etiqueta = clasificar_estado_simple(res.get("estado"))
            else:
                etiqueta = "Sin resultado"

            print(f"   → Resultado: {etiqueta}")

            # Reglas de actualización (como en tu script original)
            if etiqueta == "Equipo Inscrito":
                ws.update_cell(i, col_estado_idx, ESTADO_FINALIZADO)
                print(f"   ✅ Estado ACTUALIZADO a '{ESTADO_FINALIZADO}' (fila {i})")
            elif etiqueta == "Equipo NO inscrito":
                # no actualizamos, lo dejamos en "En Proceso"
                print(f"   ⚠️ Equipo NO inscrito. Fila {i} se mantiene en '{ESTADO_A_BUSCAR}'.")
            else:
                # Etiqueta 'Sin resultado': deja trazas útiles en la celda Estado
                ws.update_cell(i, col_estado_idx, "Sin resultado")
                print(f"   ⚠️ Sin resultado. Fila {i} marcada como 'Sin resultado'.")

            time.sleep(SLEEP_BETWEEN_ROWS)

        print("\n🎉 Proceso completado.")
    except Exception as e:
        print(f"❌ Error: {e}")
        raise
