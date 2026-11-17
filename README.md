# kpp-vigia

Programa sin IA para una tienda de Mercado Libre. Corre en GitHub Actions cada 5 minutos.

- **Gracias post-venta:** detecta ventas pagadas cuyo chat no tiene mensajes del vendedor y manda el agradecimiento (un solo mensaje libre por venta).
- **Preguntas comunes** (precio, envío, disponibilidad): se responden con `plantillas.json` usando datos del ítem. Las complejas se escalan.
- **Seguridad:** tokens y estado viven cifrados en un gist secreto (`deploy/sync_gist.py`); los secretos están en GitHub Secrets. Los logs solo muestran conteos.
- **Modo:** real (`--live` + `ML_LIVE=1`). Para probar sin enviar: workflow_dispatch con `dry_run`.
- **Verificación:** tras cada gracias relee el chat; 1 reintento; si falla queda `fallido` en el estado y la corrida falla. Nunca se envía dos veces al mismo pack. Sin correos.
