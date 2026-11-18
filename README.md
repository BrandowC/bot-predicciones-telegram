# Bot de predicciones deportivas (Telegram)

Bot personal que muestra partidos de fútbol, baloncesto, béisbol, NFL, NHL y MMA con una estimación
estadística (Poisson / ratings de equipo) comparada con las cuotas del mercado, un registro de apuestas
y calculadoras (Mines y valor esperado). Datos gratis de la API pública de ESPN, sin clave.

> Son estimaciones, no garantías. No predice juegos de casino como Mines.

## Variables de entorno
- `TELEGRAM_TOKEN` (obligatoria): token de @BotFather. No lo subas al repositorio.
- `DATA_DIR` (opcional): carpeta persistente para `apuestas.db`.
- `PORT` (la pone el hosting): el bot responde `ok` en ese puerto.

## Correr en local
```
pip install -r requirements.txt
set TELEGRAM_TOKEN=...&& python bot.py
```
