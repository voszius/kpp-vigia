# kpp-vigia

Programa sin IA para una tienda de Mercado Libre. Corre en GitHub Actions cada 5 minutos.

- **Gracias post-venta:** detecta ventas pagadas cuyo chat no tiene mensajes del vendedor y manda el agradecimiento (un solo mensaje libre por venta).
- **Preguntas comunes** (precio, envío, disponibilidad): se responden con `plantillas.json` usando datos del ítem. Las complejas se escalan.
- **Seguridad:** tokens y estado viven cifrados en un gist secreto (`deploy/sync_gist.py`); los secretos están en GitHub Secrets. Los logs solo muestran conteos.
- **Modo:** por defecto prueba (no envía nada). El envío real requiere `--live` y `ML_LIVE=1`.
