#!/usr/bin/env python3
"""Programa SIN IA para una tienda de Mercado Libre (KPP).

Hace, en cada corrida (pensado para correr cada 5 min):
  1) Gracias post-venta: busca ventas pagadas recientes, revisa el chat del pack
     y si el vendedor no ha escrito nada, manda el texto de gracias (opcion OTHER).
  2) Aviso al dueño: tras cada gracias enviado, SOLO "Listo el gracias".
  3) Preguntas: responde con plantilla las comunes (precio, envio, stock) usando
     datos reales del item; las complejas despiertan al bot de Mercado Libre.
  Nunca manda nada al grupo de WhatsApp.

Por defecto es --dry-run: NO envia mensajes, NO responde, NO avisa.
Envio real solo con --live (y ML_LIVE=1 en el entorno, doble seguro).

Secretos por entorno (nunca se imprimen):
  ML_CLIENT_SECRET            secreto de la app
  ML_TOKENS_PATH              ruta del tokens.json (default ./tokens.json)
  NOTIFY_SMTP_USER/PASS/TO    correo para "Listo el gracias" (Gmail + app password)
  WAKE_WEBHOOK_URL            webhook de la rutina del bot (preguntas complejas)
  WAKE_EMAIL                  alternativa: correo que dispara la rutina
"""
import argparse, json, os, re, smtplib, sys, tempfile, time, unicodedata
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
import requests

API = "https://api.mercadolibre.com"
CLIENT_ID = os.environ.get("ML_CLIENT_ID", "")
SELLER_ID = ""  # se toma de tokens.json (user_id)
HERE = os.path.dirname(os.path.abspath(__file__))
TOKENS_PATH = os.environ.get("ML_TOKENS_PATH", os.path.join(HERE, "tokens.json"))
STATE_PATH = os.environ.get("ML_STATE_PATH", os.path.join(HERE, "state.json"))
BOGOTA = timezone(timedelta(hours=-5))

TEXTO_GRACIAS = ("Muchas gracias por haber comprado nuestros productos. Recuerda que no le "
    "estás comprando a revendedores, sino a uno de los mayores distribuidores a nivel "
    "nacional. Si tienes cualquier duda o reclamo, no dudes en escribirnos por el chat de "
    "Mercado Libre. Esperamos con ansias una nueva compra. ❤️🍃✨")
AVISO_KEVIN = "Listo el gracias"
VENTANA_HORAS = 72          # solo agradece ventas de los ultimos 3 dias
RESPUESTAS = os.path.join(HERE, "plantillas.json")

# En GitHub Actions (repo público) NUNCA se imprimen nombres, ids ni textos: solo conteos.
PUBLIC = os.environ.get("GITHUB_ACTIONS") == "true" or os.environ.get("ML_PUBLIC_LOG") == "1"
C = {}

def log(*a):
    print(datetime.now(BOGOTA).strftime("%H:%M:%S"), *a, flush=True)

def det(*a):
    """Detalle con datos privados: solo en local."""
    if not PUBLIC: log(*a)

def cuenta(k, n=1):
    C[k] = C.get(k, 0) + n

# ---------------------------------------------------------------- tokens
def _write_secure(path, data):
    d = os.path.dirname(os.path.abspath(path))
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".tok")
    with os.fdopen(fd, "w") as f:
        json.dump(data, f)
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)

def load_tokens():
    with open(TOKENS_PATH) as f:
        return json.load(f)

def refresh(tok):
    # El gist es la fuente de verdad: fuera de GitHub Actions NO se refresca (romperia el token rotatorio)
    if os.environ.get("GITHUB_ACTIONS") != "true" and os.environ.get("ML_ALLOW_LOCAL_REFRESH") != "1":
        sys.exit("Refresh local deshabilitado: los tokens viven en el gist (corre en GitHub Actions)")
    secret = os.environ.get("ML_CLIENT_SECRET")
    if not secret or not CLIENT_ID:
        sys.exit("Falta ML_CLIENT_ID / ML_CLIENT_SECRET")
    r = requests.post(API + "/oauth/token", timeout=30, headers={"accept": "application/json"}, data={
        "grant_type": "refresh_token", "client_id": CLIENT_ID,
        "client_secret": secret, "refresh_token": tok["refresh_token"]})
    if r.status_code != 200:
        sys.exit(f"Refresh fallo HTTP {r.status_code} (no se imprime detalle)")
    new = r.json(); new["obtained_at"] = int(time.time())
    _write_secure(TOKENS_PATH, new)   # el refresh token ROTA: guardar siempre el nuevo
    log("token renovado")
    return new

def get_token():
    tok = load_tokens()
    obt = tok.get("obtained_at", 0)
    if isinstance(obt, str):
        try: obt = datetime.fromisoformat(obt.replace("Z", "+00:00")).timestamp()
        except Exception: obt = 0
    if time.time() > obt + int(tok.get("expires_in", 21600)) - 900:
        tok = refresh(tok)
    return tok["access_token"]

class ML:
    def __init__(self):
        global SELLER_ID
        SELLER_ID = str(load_tokens()["user_id"])
        self.s = requests.Session()
        self.s.headers["Authorization"] = "Bearer " + get_token()
    def get(self, path, **params):
        r = self.s.get(API + path, params=params, timeout=30)
        if r.status_code >= 400:
            return {"_http": r.status_code, "_err": r.text[:200]}
        return r.json()
    def post(self, path, body, **params):
        r = self.s.post(API + path, params=params, json=body, timeout=30)
        return r.status_code, (r.json() if r.content and r.headers.get("content-type","").startswith("application/json") else r.text[:200])

# ---------------------------------------------------------------- estado
def load_state():
    try:
        with open(STATE_PATH) as f: return json.load(f)
    except FileNotFoundError:
        return {"gracias": [], "preguntas_respondidas": [], "preguntas_escaladas": []}

def save_state(st):
    for k in st:  # recorta para que no crezca sin limite
        if isinstance(st[k], list): st[k] = st[k][-2000:]
    _write_secure(STATE_PATH, st)

# ---------------------------------------------------------------- avisos (sin IA)
def avisar_kevin(texto, live):
    if not live:
        det(f"[dry-run] avisaria al dueño: {texto!r}"); return
    u, p, to = (os.environ.get(k) for k in ("NOTIFY_SMTP_USER", "NOTIFY_SMTP_PASS", "NOTIFY_TO"))
    if not (u and p and to):
        log("aviso NO enviado: falta NOTIFY_SMTP_*"); return
    m = EmailMessage(); m["From"], m["To"], m["Subject"] = u, to, texto
    m.set_content(texto)
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=30) as s:
        s.login(u, p); s.send_message(m)

def despertar_bot(q, motivo, live):
    payload = {"tipo": "pregunta_compleja", "question_id": q["id"], "item_id": q["item_id"],
               "texto": q["text"], "motivo": motivo}
    if not live:
        det(f"[dry-run] despertaria al bot ML: q={q['id']} motivo={motivo}"); return True
    url = os.environ.get("WAKE_WEBHOOK_URL")
    if url:
        r = requests.post(url, json=payload, timeout=30); return r.status_code < 300
    to = os.environ.get("WAKE_EMAIL")
    u, p = os.environ.get("NOTIFY_SMTP_USER"), os.environ.get("NOTIFY_SMTP_PASS")
    if to and u and p:
        m = EmailMessage(); m["From"], m["To"] = u, to
        m["Subject"] = f"[ML pregunta compleja] {q['id']}"
        m.set_content(json.dumps(payload, ensure_ascii=False, indent=1))
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=30) as s:
            s.login(u, p); s.send_message(m)
        return True
    log("no hay canal para despertar al bot"); return False

# ---------------------------------------------------------------- 1) gracias
def ya_agradecido(ml, pack_id):
    """True/False/None(no se puede escribir). Fuente de verdad: el chat del pack."""
    m = ml.get(f"/messages/packs/{pack_id}/sellers/{SELLER_ID}", tag="post_sale", mark_as_read="false")
    if "_http" in m: return None, f"mensajes HTTP {m['_http']}"
    for x in m.get("messages", []):
        if str(x["from"]["user_id"]) == SELLER_ID:
            return True, "el vendedor ya escribió en el chat"
    caps = ml.get(f"/messages/action_guide/packs/{pack_id}/caps_available", tag="post_sale")
    if isinstance(caps, list):
        other = next((c for c in caps if c.get("option_id") == "OTHER"), None)
        if not other or other.get("cap_available", 0) < 1:
            return None, "sin cupo para mensaje libre (OTHER)"
    elif "_http" in caps:
        return None, f"caps HTTP {caps['_http']}"
    return False, "chat vacío, cupo OTHER disponible"

def ventas_sin_gracias(ml):
    desde = (datetime.now(timezone.utc) - timedelta(hours=VENTANA_HORAS)).strftime("%Y-%m-%dT%H:%M:%S.000-00:00")
    packs = {}
    offset = 0
    while True:
        r = ml.get("/orders/search", seller=SELLER_ID, sort="date_desc", limit=50, offset=offset,
                   **{"order.date_created.from": desde})
        if "_http" in r: log("orders HTTP", r["_http"]); cuenta("errores_api"); break
        for o in r.get("results", []):
            pid = str(o.get("pack_id") or o["id"])
            p = packs.setdefault(pid, {"pack_id": pid, "orders": [], "estados": set(), "tags": set()})
            p["orders"].append(o); p["estados"].add(o["status"]); p["tags"].update(o.get("tags", []))
        offset += 50
        if offset >= r.get("paging", {}).get("total", 0): break
    return list(packs.values())

def resumen_pack(p):
    o = p["orders"][0]
    prods = "; ".join(f"{it['quantity']}x {it['item']['title']}" for x in p["orders"] for it in x["order_items"])
    fecha = datetime.fromisoformat(o["date_created"]).astimezone(BOGOTA).strftime("%d-%b %H:%M")
    return f"{fecha} | {o['buyer'].get('nickname')} | {prods}"

def correr_gracias(ml, st, live):
    enviados = 0
    for p in ventas_sin_gracias(ml):
        pid = p["pack_id"]
        if "paid" not in p["estados"] or p["estados"] & {"cancelled", "invalid"} or "fraud_risk_detected" in p["tags"]:
            det("OMITIR (no pagada/cancelada)", pid, resumen_pack(p)); cuenta("ventas_omitidas"); continue
        if pid in st["gracias"]:
            cuenta("ventas_ya_agradecidas"); continue
        ok, motivo = ya_agradecido(ml, pid)
        if ok is True:
            st["gracias"].append(pid); det("YA TIENE gracias", pid, resumen_pack(p)); cuenta("ventas_ya_agradecidas"); continue
        if ok is None:
            det("NO SE PUEDE escribir", pid, motivo, resumen_pack(p)); cuenta("ventas_sin_cupo_chat"); continue
        det("SIN GRACIAS ->", pid, resumen_pack(p)); cuenta("ventas_sin_gracias")
        if not live:
            det(f"[dry-run] mandaria gracias a pack {pid}"); continue
        code, resp = ml.post(f"/messages/action_guide/packs/{pid}/option",
                             {"option_id": "OTHER", "text": TEXTO_GRACIAS}, tag="post_sale")
        if code < 300:
            st["gracias"].append(pid); enviados += 1; cuenta("gracias_enviados"); save_state(st)
            avisar_kevin(AVISO_KEVIN, live)
        else:
            det("ERROR enviando gracias", pid, code); cuenta("errores_gracias")
    return enviados

# ---------------------------------------------------------------- 3) preguntas
def norm(t):
    t = unicodedata.normalize("NFKD", t.lower())
    return re.sub(r"\s+", " ", "".join(c for c in t if not unicodedata.combining(c)))

INTENCIONES = {
    "precio": r"\b(?:precio|cuanto (vale|cuesta|es|sale)|valor|costo|cuesta|vale)\b",
    "envio": r"\b(?:envio|envian|envias|despach|domicilio|llega|llegan|hacen envios|mandan|flete|gratis|cuanto (se )?demora|tiempo de entrega)",
    "stock": r"\b(?:disponib|hay (en )?(stock|existencia)|tiene[ns]? (en )?(stock|existencia)|stock|existencias|unidades|todavia (hay|tiene|venden)|aun (hay|tiene|venden)|lo tienen|lo tiene|quedan?\b)",
}
# Cualquiera de estas palabras convierte la pregunta en COMPLEJA (la ve el bot o el dueño)
COMPLEJA = r"\b(devol|reclam|garantia|reembolso|cambio|danad|roto|falt|no (me )?(ha )?llego|mi (compra|pedido|paquete)|ya (compre|pague)|factura|invima|registro|original|vencim|fecha de venc|embaraz|lactan|nin[oa]s?|bebe|diabet|hipertens|presion|enfermedad|medicamento|contraindic|efectos?|dosis|tomar|sirve para|engorda|adelgaz|ingredient|componentes|azucar|alerg|mayor|al por mayor|distribuid|descuento|rebaja|promocion|mas barato|negociable|combo|varios|cantidad|contraentrega|contra entrega|efectivo|whatsapp|telefono|celular|numero|correo|@|instagram|facebook|direccion|tienda fisica|recoger|personal|sabor|tamano|ml|gramos|porciones|cuantos)\b"
LARGO_MAX = 140

def clasificar(texto):
    n = norm(texto)
    if len(n) > LARGO_MAX: return "compleja", "pregunta larga"
    if re.search(r"\d{7,}", n): return "compleja", "trae números largos (teléfono/datos)"
    m = re.search(COMPLEJA, n)
    if m: return "compleja", f"palabra sensible «{m.group(0)}»"
    hits = [k for k, rx in INTENCIONES.items() if re.search(rx, n)]
    if len(hits) == 1: return hits[0], "común"
    if not hits: return "compleja", "no coincide con precio/envío/stock"
    return "compleja", "mezcla varias cosas: " + "+".join(hits)

def plantillas():
    with open(RESPUESTAS) as f: return json.load(f)

def datos_item(ml, item_id):
    d = ml.get(f"/items/{item_id}", attributes="id,title,price,available_quantity,shipping,status")
    return None if "_http" in d else d

def armar_respuesta(intent, item, tpl):
    if item is None: return None, "no pude leer el ítem por API"
    if item.get("status") != "active": return None, "publicación no activa"
    ship = item.get("shipping") or {}
    if intent == "precio":
        precio = f"${item['price']:,.0f}".replace(",", ".")
        return tpl["precio"].format(precio=precio), "ok"
    if intent == "envio":
        key = "envio_gratis" if ship.get("free_shipping") else "envio_normal"
        if ship.get("logistic_type") == "fulfillment": key += "_full"
        return tpl.get(key, tpl["envio_normal"]), "ok"
    if intent == "stock":
        if int(item.get("available_quantity", 0)) <= 0: return None, "sin stock: que decida el dueño"
        return tpl["stock"], "ok"
    return None, "sin plantilla"

def correr_preguntas(ml, st, live, pruebas=None, item_mock=None):
    tpl = plantillas()
    if pruebas is not None:
        qs = pruebas
    else:
        r = ml.get("/questions/search", seller_id=SELLER_ID, status="UNANSWERED", api_version="4", limit=50)
        qs = r.get("questions", []) if "_http" not in r else []
        if "_http" in r: log("questions HTTP", r["_http"]); cuenta("errores_api")
        cuenta("preguntas_sin_responder", len(qs))
    cache = {}
    for q in qs:
        if q["id"] in st["preguntas_respondidas"] or q["id"] in st["preguntas_escaladas"]: continue
        intent, motivo = clasificar(q["text"])
        cuenta("preguntas_revisadas")
        texto = None
        if intent != "compleja":
            if q["item_id"] not in cache: cache[q["item_id"]] = item_mock or datos_item(ml, q["item_id"])
            texto, motivo2 = armar_respuesta(intent, cache[q["item_id"]], tpl)
            if texto is None: intent, motivo = "compleja", motivo2
        det(f"PREGUNTA {q['id']!s:>12} «{q['text']}» -> {intent} ({motivo})" + (f"\n      RESPUESTA: {texto}" if texto else ""))
        cuenta("preguntas_comunes" if texto else "preguntas_complejas")
        if pruebas is not None: continue
        if texto:
            if live:
                code, _ = ml.post("/answers", {"question_id": q["id"], "text": texto})
                if code < 300: st["preguntas_respondidas"].append(q["id"]); cuenta("respuestas_enviadas")
                else: det("ERROR respondiendo", q["id"], code)
        else:
            if despertar_bot(q, motivo, live) and live:
                cuenta("bot_despertado")
                st["preguntas_escaladas"].append(q["id"])

PRUEBAS = ["¿Cuánto vale?", "Hola, ¿tienen disponible?", "Hacen envíos a Pasto?", "El envío es gratis?",
    "Precio por 3 unidades?", "Sirve para la artritis?", "Ya compré y no me ha llegado", "Tiene registro invima?",
    "Hay stock?", "Me pueden dar su WhatsApp 3001234567", "Cuanto cuesta y cuanto demora el envio a Cali?",
    "Es original?", "Buenas, todavía lo tienen?", "¿Lo puede tomar una mujer embarazada?",
    "Cuantas porciones trae?", "Hola"]

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--live", action="store_true", help="ENVÍA de verdad (requiere también ML_LIVE=1)")
    ap.add_argument("--solo", choices=["gracias", "preguntas", "pruebas"])
    ap.add_argument("--refresh", action="store_true", help="fuerza renovar el token (prueba)")
    a = ap.parse_args()
    live = a.live and os.environ.get("ML_LIVE") == "1"
    log("MODO", "LIVE (envía)" if live else "DRY-RUN (no envía nada)")
    if a.refresh: refresh(load_tokens())
    ml = ML(); st = load_state()
    if a.solo in (None, "gracias"): correr_gracias(ml, st, live)
    if a.solo in (None, "preguntas"): correr_preguntas(ml, st, live)
    if a.solo == "pruebas":
        item = "MCO4494424926"
        qs = [{"id": f"T{i}", "item_id": item, "text": t} for i, t in enumerate(PRUEBAS)]
        log("--- pruebas con datos REALES del ítem (si la API lo permite) ---")
        correr_preguntas(ml, st, False, qs)
        log("--- mismas pruebas con ítem SIMULADO (precio 65.000, stock 10, envío gratis) ---")
        correr_preguntas(ml, st, False, qs, item_mock={"status": "active", "price": 65000,
            "available_quantity": 10, "shipping": {"free_shipping": True, "logistic_type": "cross_docking"}})
    if live: save_state(st)
    log("RESUMEN", json.dumps(C, sort_keys=True))

if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as e:  # sin traceback en logs públicos (podría llevar ids en URLs)
        if not PUBLIC: raise
        print("ERROR", type(e).__name__, flush=True); sys.exit(1)
