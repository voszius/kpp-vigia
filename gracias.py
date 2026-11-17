#!/usr/bin/env python3
"""Ventas sin gracias VISIBLE para el comprador (las deja kpp-vigia en el estado cifrado).
Solo lectura: baja el gist, no lo escribe. Uso en la caja: python3 gracias.py
El bot manda el gracias a mano por el chat de Mercado Libre; el programa lo detecta en la próxima corrida."""
import pedidos
st = pedidos.bajar_estado()
p = st.get("gracias_para_bot") or {}
if not p: print("Sin ventas pendientes de gracias.")
for pid, x in p.items():
    print(f"pack/venta {pid} | {x.get('fecha')} | {x.get('venta')} | {x.get('motivo')}")
