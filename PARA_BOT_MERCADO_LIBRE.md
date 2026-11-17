# Pedidos de Mercado Libre ya listos (para el bot de Mercado Libre)

Desde el 17-nov-2026 el programa **kpp-vigia** (GitHub, cada 5 min) ya lee por API cada venta pagada y deja
sus datos organizados en el estado cifrado. **Ya no hace falta entrar a Mercado Libre a leer los datos del pedido.**
Tu trabajo: cuando Kevin diga «manda el pedido», tomar el texto listo, hacer la foto del cobro y mandarlo
(con Wapi) al grupo «Pedidos mercado libre», como siempre.

## Comando

```bash
cd /workspace/ml_api && python3 pedidos.py pendientes            # pagados que aún no están en pedidos_bodega.json
python3 pedidos.py                                               # los de hoy (hora Colombia)
python3 pedidos.py ayer | todos
python3 pedidos.py 2000016100923789                              # por número de venta u orden
python3 pedidos.py camacho                                       # por comprador o apodo
python3 pedidos.py camacho --numero 420                          # con el número de pedido ya puesto
python3 pedidos.py pendientes --json                             # salida JSON
```

Imprime el «siguiente número sugerido» (el mayor entre pedidos_bodega.json y Shopify + 1). El número lo
sigues asignando tú al mandar, igual que antes (consecutivo compartido con Shopify).

## Qué deja en `/workspace/ml_api/pedidos_listos/`

- `<venta>.txt`: el bloque listo para pegar, el mensaje aparte de «Precio unitario», los valores para la
  foto del cobro (precio, cargos, impuestos, envío que se quita y TOTAL sin envío) y una lista «revisar».
- `<venta>.json`: todos los datos (envío, guía, Full, foto, etc.).
- `<venta>_producto.jpg`: foto del producto de la publicación (por si sirve; la foto que va al grupo sigue
  siendo la del recuadro «Cobro aprobado», como pidió Kevin).

## Lo que todavía tienes que completar

- **CC del comprador:** la API no la da sin el permiso de facturación (no se pidió). Si la venta ya está en
  `pedidos_bodega.json` o hay un export de ventas (.xlsx) en la caja, el script la toma de ahí; si no, sale
  `CC (pendiente)` y la tomas de la pantalla del cobro que ya abres para la foto.
- **Número del pedido:** sale `N` hasta que uses `--numero`.
- Si en «revisar» dice que la dirección vino rara o que el nombre del producto hay que revisarlo, ajústalo
  a mano. Los nombres cortos de los productos están en `productos.json` (puedes corregirlos ahí).
- Después de mandar, anota en `pedidos_bodega.json` (enviados) como siempre: así deja de salir en `pendientes`.

## Reglas

- El script es solo lectura: baja el gist, no lo escribe y no toca tokens. No lo cambies para refrescar tokens.
- No pegues estos datos en ningún log ni en GitHub (el repo es público).
- Full: el bloque ya sale sin dirección y con «No se despacha lo envía mercado libre».
