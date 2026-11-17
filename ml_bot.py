#!/usr/bin/env python3
"""Programa SIN IA para una tienda de Mercado Libre (KPP).

Hace, en cada corrida (pensado para correr cada 5 min):
  1) Gracias post-venta: busca ventas pagadas recientes y revisa si el chat del pack tiene un
     mensaje del vendedor VISIBLE (no rechazado en moderación). ML rechaza el gracias por API
     (AUTOMATIC_MESSAGE), así que por defecto NO lo manda: lo deja en gracias_para_bot del estado.
  2) Verifica que el gracias quedó en el chat (relee el pack; 1 reintento) y deja
     "Listo el gracias" SOLO en el estado cifrado (sin correos).
  3) Preguntas: responde con plantilla las comunes (precio, envio, stock) usando
     datos reales del item; las complejas (o si no se puede leer el ítem) quedan
     marcadas en el estado para el bot de Mercado Libre. No se manda ningún correo.
  4) Pedidos listos: guarda en el estado cifrado los datos de cada venta pagada ya organizados
     (producto, comprador, dirección, envío, cobro, foto del producto) para el bot de ML.
  Nunca manda nada al grupo de WhatsApp.

Por defecto es --dry-run: NO envia mensajes, NO responde.
Envio real solo con --live (y ML_LIVE=1 en el entorno, doble seguro).

Secretos por entorno (nunca se imprimen):
  ML_CLIENT_SECRET            secreto de la app
  ML_TOKENS_PATH              ruta del tokens.json (default ./tokens.json)
  WAKE_WEBHOOK_URL            (opcional) webhook de la rutina del bot (preguntas complejas)
  ML_ALERT_FILE               archivo donde se escribe la alerta (gracias fallido) para el workflow
"""
import argparse, json, os, re, sys, tempfile, time, unicodedata
from datetime import datetime, timedelta, timezone
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
VENTANA_HORAS = 72
# Mandar el gracias por API queda APAGADO: ML lo rechaza como AUTOMATIC_MESSAGE (17-nov-2026, 3 de 3).
GRACIAS_POR_API = os.environ.get("ML_GRACIAS_API") == "1"          # solo agradece ventas de los ultimos 3 dias
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
        with open(STATE_PATH) as f: st = json.load(f)
    except FileNotFoundError:
        st = {}
    for k in ("gracias", "preguntas_respondidas", "preguntas_escaladas", "avisos"): st.setdefault(k, [])
    for k in ("gracias_detalle", "preguntas_para_bot", "gracias_para_bot"): st.setdefault(k, {})
    return st

def save_state(st):
    for k in st:  # recorta para que no crezca sin limite
        if isinstance(st[k], list): st[k] = st[k][-2000:]
        elif isinstance(st[k], dict) and len(st[k]) > 2000: st[k] = dict(list(st[k].items())[-2000:])
    _write_secure(STATE_PATH, st)

# ---------------------------------------------------------------- avisos (sin IA)
def ahora():
    return datetime.now(BOGOTA).strftime("%Y-%m-%d %H:%M")

def avisar_kevin(st, texto, live):
    """Sin correos (Kevin no los quiere): el aviso queda SOLO en el estado cifrado del gist."""
    if not live:
        det(f"[dry-run] dejaria aviso en el estado: {texto!r}"); return
    st["avisos"].append({"fecha": ahora(), "texto": texto})

ALERTAS = []
def alerta(motivo):
    """Algo requiere al bot: el workflow termina en aviso (sin datos personales)."""
    ALERTAS.append(motivo)

def despertar_bot(st, q, motivo, live):
    """Marca la pregunta en el estado para el bot de ML (y webhook opcional). Sin correos."""
    if not live:
        det(f"[dry-run] dejaria para el bot ML: q={q['id']} motivo={motivo}"); return True
    alerta("pregunta_para_bot")
    st["preguntas_para_bot"][str(q["id"])] = {"fecha": ahora(), "item_id": q.get("item_id"),
        "texto": q.get("text"), "motivo": motivo}
    url = os.environ.get("WAKE_WEBHOOK_URL")
    if url:
        try: requests.post(url, json={"tipo": "pregunta_compleja", "question_id": q["id"], "motivo": motivo}, timeout=30)
        except Exception as e: log("webhook bot fallo", type(e).__name__)
    return True

# ---------------------------------------------------------------- 1) gracias
def _txt(x):
    t = x.get("text")
    if isinstance(t, dict): t = t.get("plain") or ""
    return t or ""

def _cmp(t):
    return re.sub(r"[^a-z0-9]", "", norm(t))

def visible(x):
    """El comprador SOLO ve el mensaje si ML no lo rechazó en moderación.
    Ojo: la API devuelve también los mensajes del vendedor rechazados (status 'moderated',
    message_moderation.status 'rejected', p. ej. reason AUTOMATIC_MESSAGE): esos NO le llegan."""
    mod = (x.get("message_moderation") or x.get("moderation") or {})
    return x.get("status", "available") == "available" and str(mod.get("status") or "").lower() != "rejected"

def mensajes_vendedor(ml, pack_id):
    m = ml.get(f"/messages/packs/{pack_id}/sellers/{SELLER_ID}", tag="post_sale", mark_as_read="false")
    if "_http" in m: return None
    return [x for x in m.get("messages", []) if str((x.get("from") or {}).get("user_id")) == SELLER_ID]

def gracias_en_chat(ml, pack_id):
    """'ok' si el gracias está en el chat Y es visible para el comprador; 'rechazado' si está
    pero ML lo rechazó (no le llega); 'otro' si hay otro mensaje visible del vendedor;
    'no' si no hay nada visible del vendedor; None si no se pudo leer."""
    mv = mensajes_vendedor(ml, pack_id)
    if mv is None: return None
    obj = _cmp(TEXTO_GRACIAS)[:120]
    es = [x for x in mv if obj in _cmp(_txt(x))]
    if any(visible(x) for x in es): return "ok"
    if any(visible(x) for x in mv): return "otro"
    if es: return "rechazado"
    return "no"

def motivo_rechazo(ml, pack_id):
    for x in mensajes_vendedor(ml, pack_id) or []:
        if not visible(x):
            return str((x.get("message_moderation") or {}).get("reason") or x.get("status"))
    return ""

def gracias_para_bot(st, pid, motivo, p, live):
    """El gracias NO le llegó al comprador: queda en el estado cifrado para que lo mande el bot
    por el chat de Mercado Libre (navegador). Sin datos personales en logs públicos."""
    cuenta("gracias_para_bot"); alerta("gracias_pendiente")
    det("GRACIAS PARA EL BOT", pid, motivo, resumen_pack(p))
    if live and pid not in st["gracias_para_bot"]:
        st["gracias_para_bot"][pid] = {"fecha": ahora(), "motivo": motivo, "venta": resumen_pack(p)}

def verificar(ml, pack_id, esperas=(4, 10, 20)):
    r = None
    for w in esperas:
        time.sleep(w); r = gracias_en_chat(ml, pack_id)
        if r == "ok": return r
    return r

def ya_agradecido(ml, pack_id):
    """True/False/None(no se puede escribir). Fuente de verdad: el chat del pack."""
    m = ml.get(f"/messages/packs/{pack_id}/sellers/{SELLER_ID}", tag="post_sale", mark_as_read="false")
    if "_http" in m: return None, f"mensajes HTTP {m['_http']}"
    mv = [x for x in m.get("messages", []) if str(x["from"]["user_id"]) == SELLER_ID]
    if any(visible(x) for x in mv):
        return True, "el vendedor ya escribió en el chat (visible)"
    if mv:   # hay mensajes del vendedor pero ML los rechazó: el comprador no ve nada
        return "rechazado", "mensaje del vendedor rechazado por ML (" + motivo_rechazo(ml, pack_id) + ")"
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

def correr_gracias(ml, st, live, packs=None):
    enviados = 0
    for p in (packs if packs is not None else ventas_sin_gracias(ml)):
        pid = p["pack_id"]
        if "paid" not in p["estados"] or p["estados"] & {"cancelled", "invalid"} or "fraud_risk_detected" in p["tags"]:
            det("OMITIR (no pagada/cancelada)", pid, resumen_pack(p)); cuenta("ventas_omitidas"); continue
        dd = st["gracias_detalle"].get(pid)
        pendiente = pid in st["gracias_para_bot"]
        if pid in st["gracias"] and not pendiente and (dd is None or dd.get("estado") == "visible"):
            cuenta("ventas_ya_agradecidas"); continue
        if pid in st["gracias"] or pendiente:
            # enviado por API o pendiente para el bot: se revisa que de verdad sea VISIBLE
            r = gracias_en_chat(ml, pid)
            if r in ("ok", "otro"):
                if live:
                    if pid not in st["gracias"]: st["gracias"].append(pid)
                    st["gracias_detalle"].setdefault(pid, {})["estado"] = "visible"
                    st["gracias_para_bot"].pop(pid, None)
                det("YA TIENE gracias (visible)", pid, resumen_pack(p)); cuenta("ventas_ya_agradecidas"); continue
            if r is None:
                cuenta("errores_api"); continue
            if live and dd is not None and dd.get("estado") != "rechazado_por_ml":
                dd["estado"] = "rechazado_por_ml"; dd["motivo"] = motivo_rechazo(ml, pid) or "no visible"
            gracias_para_bot(st, pid, "el gracias no es visible para el comprador (" +
                             (motivo_rechazo(ml, pid) or "no hay mensaje") + ")", p, live); continue
        ok, motivo = ya_agradecido(ml, pid)
        if ok is True:
            st["gracias"].append(pid); det("YA TIENE gracias", pid, resumen_pack(p)); cuenta("ventas_ya_agradecidas"); continue
        if ok == "rechazado":
            gracias_para_bot(st, pid, motivo, p, live); continue
        if ok is None:
            det("NO SE PUEDE escribir", pid, motivo, resumen_pack(p)); cuenta("ventas_sin_cupo_chat"); continue
        det("SIN GRACIAS ->", pid, resumen_pack(p)); cuenta("ventas_sin_gracias")
        if not GRACIAS_POR_API:
            # Mercado Libre RECHAZA el gracias mandado por API (moderación AUTOMATIC_MESSAGE:
            # "mensaje automático de integrador"); el comprador no lo ve. Queda para el bot.
            gracias_para_bot(st, pid, "chat vacío: mandar gracias por el chat de ML", p, live); continue
        if not live:
            det(f"[dry-run] mandaria gracias a pack {pid}"); continue
        # Desde aquí el pack queda bloqueado: NUNCA se vuelve a enviar en otra corrida.
        st["gracias"].append(pid)
        st["gracias_detalle"][pid] = {"estado": "enviando", "fecha": ahora()}; save_state(st)
        res, intentos = None, 0
        for intento in (1, 2):           # 1 envío + 1 reintento como máximo
            intentos = intento
            code, _ = ml.post(f"/messages/action_guide/packs/{pid}/option",
                              {"option_id": "OTHER", "text": TEXTO_GRACIAS}, tag="post_sale")
            log(f"envío gracias intento {intento}: HTTP {code}")
            res = verificar(ml, pid)
            if res == "ok": break
            if res in ("otro", None, "rechazado"): break   # hay algo del vendedor o no se pudo leer: no arriesgar duplicado
        d = st["gracias_detalle"][pid]; d["intentos"] = intentos
        if res == "rechazado":
            d["estado"] = "rechazado_por_ml"; d["motivo"] = motivo_rechazo(ml, pid)
            gracias_para_bot(st, pid, "ML rechazó el gracias por API (" + d["motivo"] + ")", p, live)
        elif res == "ok":
            d["estado"] = "visible"; enviados += 1; cuenta("gracias_enviados"); cuenta("gracias_verificados")
            avisar_kevin(st, AVISO_KEVIN, live)
        else:
            d["estado"] = "fallido"; d["motivo"] = {"otro": "hay mensaje del vendedor pero no coincide",
                None: "no se pudo releer el chat", "no": "no aparece en el chat"}[res]
            cuenta("gracias_fallidos"); alerta("gracias_fallido")
        save_state(st)
    return enviados


# ---------------------------------------------------------------- 2b) pedidos listos (solo lectura)
# Por cada venta pagada guarda en el estado CIFRADO los datos ya organizados para el mensaje
# a «Pedidos Mercado Libre». Solo hace GET: no imprime datos privados ni cambia nada en ML.
# No pide la etiqueta: descargarla la marca como impresa en Mercado Libre.
import formato
PEDIDOS_MAX = 400
REFRESCO_PEDIDO_S = 2 * 3600       # re-lee envío/cobro cada 2 h hasta que salga despachado
ESTADOS_ENVIO_FINALES = {"shipped", "delivered", "not_delivered", "cancelled"}

def _pago_aprobado(ml, o):
    tot = {"precio": 0.0, "cargos": 0.0, "neto": 0.0, "ok": True}
    for p in o.get("payments", []):
        if p.get("status") != "approved": continue
        c = ml.get(f"/collections/{p['id']}")
        if "_http" in c: tot["ok"] = False; continue
        tot["precio"] += float(c.get("transaction_amount") or 0)
        tot["cargos"] += float(c.get("marketplace_fee") or 0) + float(c.get("mercadopago_fee") or 0) \
            + float(c.get("finance_fee") or 0)
        tot["neto"] += float(c.get("net_received_amount") or 0)
    return tot

def datos_pedido(ml, p, prods, cache_fotos):
    o0 = p["orders"][0]
    full_o = ml.get(f"/orders/{o0['id']}")
    buyer = full_o.get("buyer", o0.get("buyer", {})) if "_http" not in full_o else o0.get("buyer", {})
    items = []
    for o in p["orders"]:
        for it in o["order_items"]:
            iid = it["item"]["id"]
            if iid not in cache_fotos:
                d = ml.get(f"/items/{iid}", attributes="id,pictures,secure_thumbnail")
                pics = [] if "_http" in d else [x.get("secure_url") or x.get("url") for x in d.get("pictures", [])]
                cache_fotos[iid] = pics[0] if pics else (None if "_http" in d else d.get("secure_thumbnail"))
            sabor = next((a.get("value_name") for a in it["item"].get("variation_attributes") or []
                          if a.get("id") == "FLAVOR"), None)
            x = {"item_id": iid, "titulo": it["item"]["title"], "cantidad": int(it["quantity"]),
                 "precio": float(it["unit_price"]), "sabor": sabor, "foto_url": cache_fotos[iid],
                 "orden": str(o["id"])}
            x["texto"], x["unidades"], _ = formato.linea_producto(x, prods)
            items.append(x)
    envio, envio_costo = {}, 0.0
    sid = (o0.get("shipping") or {}).get("id")
    if sid:
        s = ml.get(f"/shipments/{sid}")
        if "_http" not in s:
            a = s.get("receiver_address") or {}
            l1, l2 = formato.lineas_direccion(a)
            envio = {"shipment_id": str(sid), "estado": s.get("status"), "subestado": s.get("substatus"),
                     "logistica": s.get("logistic_type"), "modo": s.get("mode"),
                     "opcion": (s.get("shipping_option") or {}).get("name"),
                     "guia": s.get("tracking_number"), "transportadora": s.get("tracking_method"),
                     "recibe": a.get("receiver_name"), "telefono": a.get("receiver_phone"),
                     "direccion": {k: a.get(k) for k in ("address_line", "street_name", "street_number",
                                   "comment", "zip_code")},
                     "barrio": (a.get("neighborhood") or {}).get("name"),
                     "ciudad": (a.get("city") or {}).get("name"), "depto": (a.get("state") or {}).get("name"),
                     "linea_direccion": l1, "linea_ciudad": l2}
        c = ml.get(f"/shipments/{sid}/costs")
        if "_http" not in c:
            envio_costo = sum(float(x.get("cost") or 0) for x in c.get("senders", [])
                              if str(x.get("user_id")) == SELLER_ID)
    cobro = {"precio": 0.0, "cargos": 0.0, "neto": 0.0, "ok": True}
    for o in p["orders"]:
        t = _pago_aprobado(ml, o)
        for k in ("precio", "cargos", "neto"): cobro[k] += t[k]
        cobro["ok"] = cobro["ok"] and t["ok"]
    cobro["envio"] = envio_costo
    cobro["total_sin_envio"] = round(cobro["neto"] + envio_costo, 2)       # lo que va en la foto
    cobro["impuestos"] = round(cobro["precio"] - cobro["cargos"] - cobro["total_sin_envio"], 2)
    fecha = datetime.fromisoformat(o0["date_created"]).astimezone(BOGOTA).strftime("%Y-%m-%d %H:%M")
    ped = {"venta": p["pack_id"], "ordenes": [str(o["id"]) for o in p["orders"]], "fecha": fecha,
           "comprador": {"nombre": formato.nombre_comprador(buyer), "nickname": buyer.get("nickname"),
                         "id": buyer.get("id")},
           "items": items, "full": envio.get("logistica") == "fulfillment", "envio": envio, "cobro": cobro,
           "estado": "pagada", "actualizado": int(time.time())}
    a = formato.armar(ped, prods=prods)
    ped["texto"] = {"bloque_sin_numero_ni_cc": a["bloque"], "precio_unitario": a["precio"]}
    return ped

def correr_pedidos(ml, st, packs):
    """Devuelve True si cambió algo en st['pedidos']."""
    st.setdefault("pedidos", {})
    prods, cache, cambio = formato.productos(), {}, False
    for p in packs:
        pid = p["pack_id"]; prev = st["pedidos"].get(pid)
        if p["estados"] & {"cancelled", "invalid"} and "paid" not in p["estados"] or "fraud_risk_detected" in p["tags"]:
            if prev and prev.get("estado") != "cancelada":
                prev["estado"] = "cancelada"; cambio = True; cuenta("pedidos_cancelados")
            continue
        if "paid" not in p["estados"]: continue
        if prev and prev.get("estado") == "pagada":
            env = (prev.get("envio") or {}).get("estado")
            if env in ESTADOS_ENVIO_FINALES or time.time() - prev.get("actualizado", 0) < REFRESCO_PEDIDO_S:
                cuenta("pedidos_ya_listos"); continue
        try:
            ped = datos_pedido(ml, p, prods, cache)
        except Exception as e:
            log("pedido: error", type(e).__name__); cuenta("pedidos_error"); continue
        if prev: ped["primera_vez"] = prev.get("primera_vez", prev.get("fecha"))
        else: ped["primera_vez"] = ahora()
        st["pedidos"][pid] = ped; cambio = True
        cuenta("pedidos_actualizados" if prev else "pedidos_nuevos")
        det("PEDIDO", pid, ped["texto"]["bloque_sin_numero_ni_cc"].replace("\n", " | "))
    if len(st["pedidos"]) > PEDIDOS_MAX:
        st["pedidos"] = dict(sorted(st["pedidos"].items(), key=lambda kv: kv[1].get("fecha", ""))[-PEDIDOS_MAX:])
    return cambio

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
        if not isinstance(item.get("price"), (int, float)) or item["price"] <= 0: return None, "precio no confiable"
        precio = f"${item['price']:,.0f}".replace(",", ".")
        return tpl["precio"].format(precio=precio), "ok"
    if intent == "envio":
        key = "envio_gratis" if ship.get("free_shipping") else "envio_normal"
        if ship.get("logistic_type") == "fulfillment": key += "_full"
        return tpl.get(key, tpl["envio_normal"]), "ok"
    if intent == "stock":
        if int(item.get("available_quantity") or 0) <= 0: return None, "sin stock: que decida el dueño"
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
                else:
                    det("ERROR respondiendo", q["id"], code); cuenta("errores_respuesta")
                    if live:
                        despertar_bot(st, q, f"error API respondiendo HTTP {code}", live)
                        st["preguntas_escaladas"].append(q["id"]); cuenta("preguntas_para_bot")
        else:
            if despertar_bot(st, q, motivo, live) and live:
                cuenta("preguntas_para_bot")
                st["preguntas_escaladas"].append(q["id"])

PRUEBAS = ["¿Cuánto vale?", "Hola, ¿tienen disponible?", "Hacen envíos a Pasto?", "El envío es gratis?",
    "Precio por 3 unidades?", "Sirve para la artritis?", "Ya compré y no me ha llegado", "Tiene registro invima?",
    "Hay stock?", "Me pueden dar su WhatsApp 3001234567", "Cuanto cuesta y cuanto demora el envio a Cali?",
    "Es original?", "Buenas, todavía lo tienen?", "¿Lo puede tomar una mujer embarazada?",
    "Cuantas porciones trae?", "Hola"]

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--live", action="store_true", help="ENVÍA de verdad (requiere también ML_LIVE=1)")
    ap.add_argument("--solo", choices=["gracias", "preguntas", "pruebas", "pedidos"])
    ap.add_argument("--refresh", action="store_true", help="fuerza renovar el token (prueba)")
    a = ap.parse_args()
    live = a.live and os.environ.get("ML_LIVE") == "1"
    log("MODO", "LIVE (envía)" if live else "DRY-RUN (no envía nada)")
    if a.refresh: refresh(load_tokens())
    ml = ML(); st = load_state()
    packs = ventas_sin_gracias(ml) if a.solo in (None, "gracias", "pedidos") else []
    cambio_pedidos = False
    if a.solo in (None, "pedidos"): cambio_pedidos = correr_pedidos(ml, st, packs)
    if a.solo in (None, "gracias"): correr_gracias(ml, st, live, packs)
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
    elif cambio_pedidos:   # en dry-run solo se guardan los pedidos (lectura pura): el resto no cambia
        st0 = load_state(); st0["pedidos"] = st["pedidos"]; save_state(st0)
    log("RESUMEN", json.dumps(C, sort_keys=True))
    if ALERTAS:
        af = os.environ.get("ML_ALERT_FILE")
        if af:
            with open(af, "w") as f: f.write(",".join(sorted(set(ALERTAS))))
        log("ALERTA para el bot:", ",".join(sorted(set(ALERTAS))))

if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as e:  # sin traceback en logs públicos (podría llevar ids en URLs)
        if not PUBLIC: raise
        print("ERROR", type(e).__name__, flush=True); sys.exit(1)
