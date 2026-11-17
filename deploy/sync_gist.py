#!/usr/bin/env python3
"""Baja/sube tokens.json + state.json CIFRADOS (Fernet) a un gist secreto.
Uso: sync_gist.py pull | push  — env: GIST_ID, GIST_TOKEN, STATE_KEY. No imprime contenido."""
import os, sys, time, requests
from cryptography.fernet import Fernet
G = f"https://api.github.com/gists/{os.environ['GIST_ID']}"
H = {"Authorization": f"Bearer {os.environ['GIST_TOKEN']}", "Accept": "application/vnd.github+json"}
F = Fernet(os.environ["STATE_KEY"].encode())
FILES = ["tokens.json", "state.json"]

def intento(fn):
    for i in range(5):
        try: return fn()
        except Exception as e:
            print("reintento", i + 1, type(e).__name__, flush=True); time.sleep(5 * (i + 1))
    sys.exit("sync_gist fallo")

def pull():
    r = requests.get(G, headers=H, timeout=30); r.raise_for_status()
    g = r.json()["files"]
    for n in FILES:
        if n + ".enc" in g:
            c = g[n + ".enc"]
            txt = c["content"] if not c.get("truncated") else requests.get(c["raw_url"], headers=H, timeout=30).text
            fd = os.open(n, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            os.write(fd, F.decrypt(txt.strip().encode())); os.close(fd)
    if not os.path.exists("tokens.json"): sys.exit("gist sin tokens")

def push():
    files = {n + ".enc": {"content": F.encrypt(open(n, "rb").read()).decode()} for n in FILES if os.path.exists(n)}
    if "tokens.json.enc" not in files: sys.exit("no hay tokens.json local; no se sube nada")
    r = requests.patch(G, headers=H, json={"files": files}, timeout=30); r.raise_for_status()

intento(pull if sys.argv[1] == "pull" else push)
print("ok", sys.argv[1], flush=True)
