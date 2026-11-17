# kpp-vigia

Programa sin IA para una tienda de Mercado Libre. Corre en GitHub Actions cada 5 minutos.

- **Gracias post-venta:** detecta ventas pagadas cuyo chat no tiene un mensaje del vendedor **visible para el comprador**. Mercado Libre rechaza el gracias mandado por API (moderación `AUTOMATIC_MESSAGE`, el comprador no lo ve), así que el envío por API quedó apagado (`ML_GRACIAS_API=1` lo reactiva) y esas ventas quedan en `gracias_para_bot` del estado cifrado para mandarlo por el chat de ML (`python3 gracias.py` en la caja).
- **Preguntas comunes** (precio, envío, disponibilidad): se responden con `plantillas.json` usando datos del ítem. Las complejas se escalan.
- **Pedidos listos:** por cada venta pagada guarda en el estado cifrado los datos ya organizados (producto con nombre corto de `productos.json`, comprador, dirección, envío/Full, cobro y foto del producto). Solo lectura en Mercado Libre. `pedidos.py` (en la caja) los baja y arma el texto con `formato.py`.
- **Seguridad:** tokens y estado viven cifrados en un gist secreto (`deploy/sync_gist.py`); los secretos están en GitHub Secrets. Los logs solo muestran conteos.
- **Modo:** real (`--live` + `ML_LIVE=1`). Para probar sin enviar: workflow_dispatch con `dry_run`.
- **Verificación:** un mensaje solo cuenta si `status=available` y su moderación no es `rejected`. Tras cada gracias relee el chat; 1 reintento; si falla queda `fallido` en el estado y la corrida falla. Nunca se envía dos veces al mismo pack. Sin correos.
