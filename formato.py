"""Formato del mensaje de pedido para el grupo «Pedidos Mercado Libre» (sin IA).
Lo usan ml_bot.py (GitHub Actions) y pedidos.py (caja). Replica el formato del bot de ML:

    Pedido mercado libre N

    <unidades> <producto>          (un bloque por producto, separados por línea en blanco)

    <nombre de la venta>
    CC <número>
    <dirección, extra, Referencia: ..., barrio>
    <Ciudad, Depto>

Full: sin dirección y con «No se despacha lo envía mercado libre».
Mensaje aparte: «Precio unitario: 18.000» (una línea por producto).
"""
import json, os, re, unicodedata

HERE = os.path.dirname(os.path.abspath(__file__))
TEXTO_FULL = "No se despacha lo envía mercado libre"

def productos():
    try:
        with open(os.path.join(HERE, "productos.json"), encoding="utf-8") as f: return json.load(f)
    except FileNotFoundError:
        return {}

def _sp(t):
    return re.sub(r"\s+", " ", (t or "")).strip()

def norm(t):
    t = unicodedata.normalize("NFKD", (t or "").lower())
    return re.sub(r"\s+", " ", "".join(c for c in t if not unicodedata.combining(c))).strip()

def pesos(n):
    """18000 -> '18.000'; 15998.333 -> '15.998' (redondeado, como el bot)."""
    return f"{int(round(float(n))):,}".replace(",", ".")

def nombre_comprador(buyer):
    n = _sp(f"{buyer.get('first_name') or ''} {buyer.get('last_name') or ''}")
    return n or buyer.get("nickname") or ""

def linea_producto(it, prods=None):
    """it: {'item_id','titulo','cantidad','sabor'} -> (texto, unidades, revisar)"""
    prods = prods if prods is not None else productos()
    m = prods.get(it["item_id"])
    if m:
        u = int(m.get("por_unidad", 1)) * int(it["cantidad"])
        sab = (it.get("sabor") or "").strip().lower()
        if sab and sab in (m.get("por_sabor") or {}):
            nom = m["por_sabor"][sab]                      # p. ej. Ucol Jarabe cereza se manda como Vcol
        else:
            nom = m["nombre"] + (f" {sab}" if m.get("con_sabor") and sab else "")
        return f"{u} {nom}", u, bool(m.get("revisar"))
    x = re.search(r"\bX\s?(\d+)\b", it.get("titulo") or "", re.I)
    u = (int(x.group(1)) if x else 1) * int(it["cantidad"])
    return f"{u} {it.get('titulo')}", u, True

def _cap_dir(t):
    t = _sp(t)
    if t.isupper(): t = t.title()
    t = re.sub(r"(\d)([a-z])\b", lambda m: m.group(1) + m.group(2).upper(), t)      # 31a -> 31A
    t = re.sub(r"\bsur\b", "Sur", t); t = re.sub(r"\bnorte\b", "Norte", t); t = re.sub(r"\beste\b", "Este", t)
    return t

NUM_OK = re.compile(r"^\d+\s?[A-Za-z]{0,2}(\s?bis)?(\s?[A-Za-z])?(\s?(sur|norte|este))?\s?-\s?\d+\s?[A-Za-z]{0,2}(\s?(sur|norte|este))?(\s.*)?$", re.I)

def lineas_direccion(a):
    """receiver_address de /shipments -> (linea_direccion, linea_ciudad)"""
    if not a: return "", ""
    calle = _cap_dir(a.get("street_name"))
    num = _cap_dir(a.get("street_number")).lstrip("#").strip()
    if calle and NUM_OK.match(num):
        d = f"{calle} #{num.replace(' - ', '-')}"
    else:   # número raro (Mz, SN, incompleto): se deja la dirección tal como la escribió el comprador
        d = _cap_dir(a.get("address_line") or f"{calle} {num}")
        d = re.sub(r"\s*#?\s*s/?n(\s*-\s*s/?n)?\s*$", "", d, flags=re.I).rstrip(" #-")
    estado = _sp((a.get("state") or {}).get("name"))
    ciudad = _sp((a.get("city") or {}).get("name"))
    barrio = _sp((a.get("neighborhood") or {}).get("name"))
    com = _sp(a.get("comment"))
    for pre in (estado, "Cundinamarca", "Bogotá D.C.", "Bogota D.C."):
        if pre and norm(com).startswith(norm(pre) + " ") : com = com[len(pre):].strip()
    if com in (".", "-", "N/A", "n/a", "0"): com = ""
    extra, ref = com, ""
    m = re.search(r"referencia\s*:?", com, re.I)
    if m: extra, ref = com[:m.start()].strip(" ,.-"), com[m.end():].strip(" ,.-")
    partes = [d]
    if extra: partes.append(extra[:1].upper() + extra[1:])
    if ref: partes.append("Referencia: " + ref)
    if barrio and norm(barrio) not in (norm(ciudad), norm(estado)):
        partes.append(barrio.title() if barrio.isupper() or barrio.islower() else barrio)
    linea_ciudad = ", ".join(x for x in [ciudad.title() if ciudad.isupper() else ciudad, estado] if x)
    if norm(ciudad) == norm(estado): linea_ciudad = estado
    return ", ".join(p for p in partes if p), linea_ciudad

def armar(p, numero=None, cc=None, prods=None):
    """p: pedido del estado (st['pedidos'][venta]). Devuelve dict con 'bloque', 'precio', 'revisar'."""
    prods = prods if prods is not None else productos()
    lineas, precios, revisar = [], [], []
    for it in p["items"]:
        txt, u, rev = linea_producto(it, prods)
        lineas.append(txt)
        precios.append("Precio unitario: " + pesos(float(it["precio"]) * int(it["cantidad"]) / max(u, 1)))
        if rev: revisar.append(f"nombre del producto «{it.get('titulo')}» ({it['item_id']}): revisar")
    n = str(numero) if numero else "N"
    out = [f"Pedido mercado libre {n}", ""]
    for l in lineas: out += [l, ""]
    out.append(p["comprador"]["nombre"])
    out.append(f"CC {cc}" if cc else "CC (pendiente)")
    if p.get("full"):
        out += ["", TEXTO_FULL]
    else:
        e = p.get("envio") or {}
        if e.get("linea_direccion"): out.append(e["linea_direccion"])
        if e.get("linea_ciudad"): out.append(e["linea_ciudad"])
    num = str(((p.get("envio") or {}).get("direccion") or {}).get("street_number") or "").lstrip("#").strip()
    if not p.get("full") and p.get("envio", {}).get("direccion") and not NUM_OK.match(_cap_dir(num)):
        revisar.append("la dirección viene escrita rara por el comprador: revisar antes de mandar")
    if not numero: revisar.append("falta el número del pedido (consecutivo compartido con Shopify)")
    if not cc: revisar.append("falta la CC (la API no la da sin permiso de facturación)")
    return {"bloque": "\n".join(out) + "\n", "precio": "\n".join(precios) + "\n", "revisar": revisar}
