#!/usr/bin/env python3
"""Pedidos de Mercado Libre YA ORGANIZADOS para el bot de ML (corre en la caja, sin IA).

Baja el estado CIFRADO del gist (solo lectura: nunca escribe el gist, nunca toca tokens),
lo descifra en memoria y deja en /workspace/ml_api/pedidos_listos/:
    <venta>.txt         texto listo para pegar (bloque + mensaje de precio) y datos para la foto
    <venta>.json        todos los datos del pedido
    <venta>_producto.jpg foto del producto (de la publicación)

Uso:
    python3 pedidos.py                 # pedidos de hoy (hora Colombia)
    python3 pedidos.py pendientes      # pagados que aún no están en pedidos_bodega.json (enviados)
    python3 pedidos.py ayer | todos
    python3 pedidos.py 2000016100923789          # por número de venta/orden
    python3 pedidos.py camacho                   # por comprador o apodo
    python3 pedidos.py camacho --numero 420      # pone el número de pedido en el texto
"""
import argparse, glob, io, json, os, re, sys, warnings
from datetime import datetime, timedelta, timezone
import requests
from cryptography.fernet import Fernet

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import formato

BOGOTA = timezone(timedelta(hours=-5))
SALIDA = os.path.join(HERE, "pedidos_listos")
BODEGA = "/workspace/ml_state/pedidos_bodega.json"
SHOPIFY = "/workspace/shopify_state/consecutivo.json"
VENTAS_PARSED = "/workspace/ml_state/ventas_parsed.json"
EXCELS = ["/home/box/Downloads/*Ventas_CO*.xlsx", "/workspace/ml_state/ventas_export/*Ventas_CO*.xlsx",
          "/workspace/ml_state/*Ventas_CO*.xlsx", "/workspace/*Ventas_CO*.xlsx"]

def bajar_estado():
    k = json.load(open(os.path.join(HERE, ".gist_key")))
    tok = os.environ.get("GH_TOKEN")
    h = {"Accept": "application/vnd.github+json", **({"Authorization": "Bearer " + tok} if tok else {})}
    r = requests.get(f"https://api.github.com/gists/{k['gist_id']}", headers=h, timeout=30); r.raise_for_status()
    f = r.json()["files"]["state.json.enc"]
    txt = f["content"] if not f.get("truncated") else requests.get(f["raw_url"], headers=h, timeout=60).text
    return json.loads(Fernet(k["state_key"].encode()).decrypt(txt.strip().encode()))   # solo en memoria

def _load(p, d):
    try:
        with open(p, encoding="utf-8") as f: return json.load(f)
    except Exception: return d

def bodega():
    """venta -> {numero, cliente, cc} de lo ya enviado; y el siguiente número sugerido."""
    b = _load(BODEGA, {}); env = {}; nums = [int(b.get("ultimo_numero_enviado") or 0)]
    for v, x in (b.get("enviados") or {}).items():
        if isinstance(x, dict): env[v] = x; nums.append(int(x.get("numero") or 0))
        elif isinstance(x, int): env[v] = {"numero": x}; nums.append(x)
    for x in b.get("enviados_manual") or []:
        if isinstance(x, dict) and x.get("venta"): env.setdefault(x["venta"], {"numero": x.get("n")}); nums.append(int(x.get("n") or 0))
    for x in (_load(SHOPIFY, {}).get("pedidos_enviados") or []): nums.append(int(x.get("bodega") or 0))
    excl = set(b.get("canceladas_excluidas") or []) | {x.get("venta") for x in b.get("no_enviar") or [] if isinstance(x, dict)}
    return env, max(nums) + 1, excl

def excel_ventas():
    """venta -> {'comprador','doc'} del export de ventas más nuevo que haya en la caja (si hay)."""
    out = {}
    files = sorted({f for g in EXCELS for f in glob.glob(g)}, key=os.path.getmtime)
    try: import openpyxl
    except ImportError: return out
    for fn in files:   # del más viejo al más nuevo: el nuevo pisa
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                ws = openpyxl.load_workbook(fn, read_only=True).active
                for r in ws.iter_rows(min_row=7, values_only=True):
                    if not r or not r[0]: continue
                    doc = (str(r[27] or "").strip() if len(r) > 27 else "")
                    m = re.match(r"([A-Z]{2,4})\s*:?\s*([\dA-Za-z.-]+)", doc)
                    out[str(r[0]).strip()] = {"comprador": (str(r[30]).strip() if len(r) > 30 and r[30] else ""),
                                              "doc": f"{m.group(1)} {m.group(2)}" if m else ""}
        except Exception: continue
    return out

def nombres_parsed():
    return {x["venta"]: (x.get("comprador") or "").strip() for x in _load(VENTAS_PARSED, []) if isinstance(x, dict)}

def bajar_foto(url, destino):
    if not url: return None
    try:
        r = requests.get(url, timeout=30); r.raise_for_status()
        try:
            from PIL import Image
            Image.open(io.BytesIO(r.content)).convert("RGB").save(destino, "JPEG", quality=92)
        except ImportError:
            open(destino, "wb").write(r.content)
        return destino
    except Exception: return None

def preparar(p, env, xls, parsed, numero=None, foto=True):
    v = p["venta"]; ya = env.get(v) or next((env[o] for o in p.get("ordenes", []) if o in env), None)
    nombre = (ya or {}).get("cliente") or (xls.get(v) or {}).get("comprador") or parsed.get(v) or p["comprador"]["nombre"]
    doc = ""
    if ya and ya.get("cc"): doc = "CC " + str(ya["cc"])
    elif (xls.get(v) or {}).get("doc"): doc = xls[v]["doc"]
    num = numero or (ya or {}).get("numero")
    q = dict(p); q["comprador"] = {**p["comprador"], "nombre": nombre}
    e0 = p.get("envio") or {}
    if e0.get("direccion"):   # se rearma aquí para que los ajustes de formato.py apliquen sin redesplegar
        l1, l2 = formato.lineas_direccion({**e0["direccion"], "neighborhood": {"name": e0.get("barrio")},
                                           "city": {"name": e0.get("ciudad")}, "state": {"name": e0.get("depto")}})
        q["envio"] = {**e0, "linea_direccion": l1, "linea_ciudad": l2}
    a = formato.armar(q, numero=num, cc=(doc.split(" ", 1)[1] if doc else None))
    bloque = a["bloque"]
    if doc and not doc.startswith("CC "): bloque = bloque.replace("CC " + doc.split(" ", 1)[1], doc, 1)
    c = p.get("cobro") or {}
    foto_cobro = (f"Precio {formato.pesos(c.get('precio', 0))} | Cargos -{formato.pesos(c.get('cargos', 0))}"
                  + (f" | Impuestos -{formato.pesos(c['impuestos'])}" if round(c.get("impuestos", 0)) > 0 else "")
                  + f" | Envíos -{formato.pesos(c.get('envio', 0))} (se quita en la foto)"
                  + f" | TOTAL de la foto (sin envío) {formato.pesos(c.get('total_sin_envio', 0))}")
    e = p.get("envio") or {}
    info = {"venta": v, "fecha": p.get("fecha"), "estado": p.get("estado"), "full": p.get("full"),
            "ya_enviado_como": (ya or {}).get("numero"), "bloque": bloque, "mensaje_precio": a["precio"],
            "foto_cobro": foto_cobro, "revisar": a["revisar"],
            "envio": {k: e.get(k) for k in ("estado", "subestado", "logistica", "opcion", "guia", "transportadora", "recibe")},
            "productos": [{"texto": i.get("texto"), "titulo": i["titulo"], "foto_url": i.get("foto_url")} for i in p["items"]],
            "apodo": p["comprador"].get("nickname")}
    os.makedirs(SALIDA, mode=0o700, exist_ok=True)
    fotos = []
    if foto:
        for n, i in enumerate(p["items"], 1):
            f = bajar_foto(i.get("foto_url"), os.path.join(SALIDA, f"{v}_producto{'' if n == 1 else n}.jpg"))
            if f: fotos.append(f)
    info["fotos_producto"] = fotos
    txt = (bloque + "\n----- mensaje de precio (aparte) -----\n" + a["precio"]
           + "\n----- para la foto del cobro -----\n" + foto_cobro + "\n"
           + ("\n----- revisar -----\n" + "\n".join("- " + r for r in a["revisar"]) + "\n" if a["revisar"] else "")
           + (f"\nYA ENVIADO como pedido {info['ya_enviado_como']}\n" if info["ya_enviado_como"] else ""))
    open(os.path.join(SALIDA, f"{v}.txt"), "w", encoding="utf-8").write(txt)
    json.dump(info, open(os.path.join(SALIDA, f"{v}.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    return info, txt

def main():
    ap = argparse.ArgumentParser(description="Pedidos ML listos para «Pedidos Mercado Libre» (solo lectura)")
    ap.add_argument("filtro", nargs="?", default="hoy", help="hoy | ayer | pendientes | todos | venta | comprador")
    ap.add_argument("--numero", type=int, help="número de pedido a poner (solo si sale UN pedido)")
    ap.add_argument("--sin-foto", action="store_true")
    ap.add_argument("--json", action="store_true", help="imprime JSON en vez de texto")
    a = ap.parse_args()
    st = bajar_estado()
    peds = [p for p in (st.get("pedidos") or {}).values() if p.get("estado") == "pagada"]
    env, siguiente, excl = bodega()
    peds = [p for p in peds if p["venta"] not in excl]
    hoy = datetime.now(BOGOTA).date()
    f = a.filtro.strip().lower()
    if f == "hoy": sel = [p for p in peds if p["fecha"][:10] == str(hoy)]
    elif f == "ayer": sel = [p for p in peds if p["fecha"][:10] == str(hoy - timedelta(days=1))]
    elif f == "todos": sel = peds
    elif f == "pendientes": sel = [p for p in peds if p["venta"] not in env and not set(p.get("ordenes", [])) & set(env)]
    else:
        n = formato.norm(f)
        sel = [p for p in peds if f in (p["venta"], *p.get("ordenes", []))
               or n in formato.norm(p["comprador"].get("nombre")) or n in formato.norm(p["comprador"].get("nickname"))]
    sel.sort(key=lambda p: p["fecha"])
    if a.numero and len(sel) != 1: sys.exit(f"--numero necesita UN solo pedido y salieron {len(sel)}")
    xls, parsed = excel_ventas(), nombres_parsed()
    res = [preparar(p, env, xls, parsed, a.numero, not a.sin_foto) for p in sel]
    if a.json:
        print(json.dumps({"siguiente_numero_sugerido": siguiente, "pedidos": [i for i, _ in res]}, ensure_ascii=False, indent=1)); return
    print(f"{len(res)} pedido(s) [{a.filtro}] | siguiente número sugerido: {siguiente} | carpeta: {SALIDA}\n")
    for i, t in res:
        print(f"===== venta {i['venta']} | {i['fecha']} | {'FULL' if i['full'] else 'despacho propio'} | fotos: {', '.join(i['fotos_producto']) or 'ninguna'}")
        print(t)

if __name__ == "__main__":
    main()
