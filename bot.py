import asyncio
import logging
import os
import threading

import httpx
from telegram import BotCommand, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (Application, CallbackQueryHandler, CommandHandler, ContextTypes,
                          ConversationHandler, MessageHandler, filters)
from telegram.request import HTTPXRequest

import apuestas
import deportes
import mines

logging.basicConfig(format="%(asctime)s %(levelname)s %(message)s", level=logging.WARNING)

AVISO = ("Son estimaciones estadísticas, no garantías. Las casas de apuestas tienen margen y datos que "
         "este modelo no ve. Apuesta solo lo que puedas perder.")

AYUDA = (
    "Deportes (primero los EN VIVO, luego hoy y próximos días, comparados con el mercado):\n"
    "/futbol  /baloncesto  /beisbol  /americano  /hockey  /mma  /boxeo\n"
    "Puedes filtrar: /futbol colombia · /futbol libertadores · /futbol millonarios\n"
    "/envivo – solo lo que se está jugando ahora (todos los deportes) · /envivo futbol\n\n"
    "Apuestas:\n"
    "/apostar – registrar una apuesta\n"
    "/pendientes – marcar ganada/perdida\n"
    "/balance – cuánto llevas ganado o perdido\n"
    "/historial – tus últimas apuestas\n\n"
    "Herramientas:\n"
    "/mines 3 – calculadora de Mines (minas) · /mines 3 10 5000 (minas, gemas, apuesta)\n"
    "/valor 2.10 55 – ¿vale la pena? (cuota y tu probabilidad en %)\n\n" + AVISO)

SEL, TEXTO, LIBRE, CUOTA, MONTO = range(5)


# ------------------------------------------------------------ deportes
async def listar(update: Update, ctx: ContextTypes.DEFAULT_TYPE, deporte: str, filtro=None,
                 solo_vivo=False, silencioso=False):
    """Envía los partidos de un deporte. Devuelve cuántos mostró."""
    if filtro is None:
        filtro = " ".join(ctx.args or [])
    espera = None if silencioso else await update.message.reply_text(
        "Buscando partidos y calculando…")
    try:
        items, nota = await asyncio.to_thread(deportes.reporte, deporte, filtro, 24, solo_vivo)
    except deportes.SinConexion as e:
        if espera:
            await espera.edit_text(str(e))
        return 0
    except Exception as e:
        logging.exception("error en %s", deporte)
        if espera:
            await espera.edit_text(f"Error obteniendo datos: {e}")
        return 0
    if not items:
        if espera:
            await espera.edit_text(nota)
        return 0
    if espera:
        await espera.delete()
    lista = ctx.chat_data.setdefault("lista", [])
    lista.clear()
    # agrupar por liga y enviar en bloques de 4 partidos con sus botones de "apostar"
    por_liga = {}
    for it in items:
        por_liga.setdefault(it["liga"], []).append(it)
    for liga, evs in por_liga.items():
        for i in range(0, len(evs), 4):
            bloque = evs[i:i + 4]
            botones = []
            for it in bloque:
                lista.append({"deporte": deporte, "evento": it["label"], "opciones": it["opciones"]})
                botones.append([InlineKeyboardButton(f"📝 Apostar: {it['label'][:40]}",
                                                     callback_data=f"ap:{len(lista) - 1}")])
            await update.message.reply_text("\n".join(it["texto"] for it in bloque),
                                            reply_markup=InlineKeyboardMarkup(botones))
    cierre = (nota + "\n" if nota else "") + AVISO
    await update.message.reply_text(cierre)
    return len(items)


async def envivo(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """/envivo → todos los deportes · /envivo futbol → uno · /envivo futbol colombia → filtrado."""
    args = [a.lower() for a in (ctx.args or [])]
    alias = {"fútbol": "futbol", "béisbol": "beisbol"}
    if args:
        args[0] = alias.get(args[0], args[0])
    if args and args[0] in deportes.DEPORTES:
        deps, filtro = [args[0]], " ".join(args[1:])
    else:
        deps, filtro = [d for d in deportes.DEPORTES if d != "mma"], " ".join(args)
    aviso = await update.message.reply_text("Buscando partidos en vivo…")
    total = 0
    for d in deps:
        total += await listar(update, ctx, d, filtro=filtro, solo_vivo=True, silencioso=True) or 0
    await aviso.delete()
    if not total:
        await update.message.reply_text("No hay partidos en vivo ahora mismo"
                                        + (f" para «{filtro}»" if filtro else "") + ".")


def _handler_deporte(deporte):
    async def h(update, ctx):
        await listar(update, ctx, deporte)
    return h


async def boxeo(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Boxeo: no encontré una fuente gratuita y confiable de peleas y cuotas, y prefiero no "
        "inventarte datos. Para peleas de UFC usa /mma. Si consigues una API de boxeo (por ejemplo "
        "The Odds API tiene plan gratis con clave), la conecto.")


# ------------------------------------------------------------ registro de apuestas
async def ap_desde_boton(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    i = int(q.data.split(":")[1])
    lista = ctx.chat_data.get("lista", [])
    if i >= len(lista):
        await q.message.reply_text("Esa lista ya expiró. Pide el deporte otra vez.")
        return ConversationHandler.END
    item = lista[i]
    ctx.user_data["bet"] = {"deporte": item["deporte"], "evento": item["evento"], "ops": item["opciones"]}
    filas = [[InlineKeyboardButton(f"{lab} ({p:.0%})", callback_data=f"op:{j}")]
             for j, (lab, p) in enumerate(item["opciones"])]
    filas.append([InlineKeyboardButton("✍️ Otra apuesta (escribirla)", callback_data="op:otra")])
    await q.message.reply_text(f"📝 {item['evento']}\n¿Qué vas a apostar? (entre paréntesis, la probabilidad "
                               f"que calcula el modelo)", reply_markup=InlineKeyboardMarkup(filas))
    return SEL


async def ap_eleccion(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    bet = ctx.user_data["bet"]
    dato = q.data.split(":")[1]
    if dato == "otra":
        await q.message.reply_text("Escribe tu apuesta (ej: Más de 220.5 puntos):")
        return TEXTO
    lab, p = bet["ops"][int(dato)]
    bet["seleccion"], bet["prob"] = lab, p
    await q.message.reply_text(f"{bet['evento']} → {lab}\n¿A qué cuota? (ej: 2.10)")
    return CUOTA


async def ap_texto(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    bet = ctx.user_data["bet"]
    bet["seleccion"], bet["prob"] = update.message.text.strip(), None
    await update.message.reply_text("¿A qué cuota? (ej: 2.10)")
    return CUOTA


async def ap_nueva(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data["bet"] = {"deporte": "otro", "evento": "", "ops": []}
    await update.message.reply_text("Escribe el partido y tu apuesta, por ejemplo:\n"
                                    "Millonarios vs Nacional – gana Millonarios\n\n(/cancelar para salir)")
    return LIBRE


async def ap_libre(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    bet = ctx.user_data["bet"]
    bet["evento"] = update.message.text.strip()
    bet["seleccion"], bet["prob"] = "", None
    await update.message.reply_text("¿A qué cuota? (ej: 2.10)")
    return CUOTA


def _num(txt):
    try:
        return float(txt.replace(",", ".").replace("$", "").strip())
    except ValueError:
        return None


async def ap_cuota(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    c = _num(update.message.text)
    if c is None or c <= 1.0:
        await update.message.reply_text("La cuota debe ser un número mayor que 1 (ej: 2.10). Intenta otra vez:")
        return CUOTA
    bet = ctx.user_data["bet"]
    bet["cuota"] = c
    extra = ""
    p = bet.get("prob")
    if p:
        ev = p * c - 1
        extra = (f"\nModelo: {p:.0%} de ganar · cuota justa {1 / p:.2f} · valor esperado {ev:+.0%}"
                 + (" ⚠️ el modelo ve esta cuota por debajo de lo justo." if ev < 0 else ""))
    await update.message.reply_text(f"Cuota {c:g}.{extra}\n¿Cuánto vas a apostar? (solo el número)")
    return MONTO


async def ap_monto(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    m = _num(update.message.text)
    if m is None or m <= 0:
        await update.message.reply_text("Escribe solo el monto, como número (ej: 20000):")
        return MONTO
    b = ctx.user_data.pop("bet")
    aid = apuestas.crear(update.effective_chat.id, b["deporte"], b["evento"], b.get("seleccion", ""),
                         b["cuota"], m, b.get("prob"))
    await update.message.reply_text(
        f"✅ Apuesta #{aid} registrada\n{b['evento']} {('→ ' + b['seleccion']) if b.get('seleccion') else ''}\n"
        f"Cuota {b['cuota']:g} · Monto {m:,.0f} · Ganancia posible {m * (b['cuota'] - 1):,.0f}\n\n"
        f"Cuando termine usa /pendientes para marcar si ganó o perdió.")
    return ConversationHandler.END


async def cancelar(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data.pop("bet", None)
    await update.message.reply_text("Cancelado.")
    return ConversationHandler.END


def _fmt(a):
    sel = f" → {a['seleccion']}" if a["seleccion"] else ""
    return f"#{a['id']} {a['evento']}{sel}\n   cuota {a['cuota']:g} · monto {a['monto']:,.0f} · {a['estado']}"


async def pendientes(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    filas = apuestas.pendientes(update.effective_chat.id)
    if not filas:
        await update.message.reply_text("No tienes apuestas pendientes.")
        return
    for a in filas:
        kb = InlineKeyboardMarkup([[
            InlineKeyboardButton("✅ Ganó", callback_data=f"res:{a['id']}:g"),
            InlineKeyboardButton("❌ Perdió", callback_data=f"res:{a['id']}:p"),
            InlineKeyboardButton("➖ Nula", callback_data=f"res:{a['id']}:n")]])
        await update.message.reply_text(_fmt(a), reply_markup=kb)


async def resolver_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    _, aid, r = q.data.split(":")
    a = apuestas.resolver(update.effective_chat.id, int(aid), r)
    await q.answer()
    if not a:
        await q.edit_message_text("Esa apuesta ya estaba cerrada.")
        return
    icono = {"ganada": "✅", "perdida": "❌", "nula": "➖"}[a["estado"]]
    await q.edit_message_text(f"{icono} {_fmt(a)}\n   resultado: {a['ganancia']:+,.0f}")


async def balance(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    b = apuestas.balance(update.effective_chat.id)
    if not b["total"]:
        await update.message.reply_text("Aún no has registrado apuestas. Usa /apostar.")
        return
    t = (f"📊 Balance\nApuestas cerradas: {b['cerradas']} (ganadas {b['ganadas']}"
         + (f", {b['ganadas'] / b['cerradas']:.0%}" if b["cerradas"] else "") + ")\n"
         f"Total apostado: {b['apostado']:,.0f}\nResultado: {b['ganancia']:+,.0f}  (ROI {b['roi']:+.1%})\n"
         f"Pendientes: {b['pendientes']} ({b['en_juego']:,.0f} en juego)")
    if b["por_deporte"]:
        t += "\n\nPor deporte:\n" + "\n".join(f"• {d}: {n} apuestas, {g:+,.0f}"
                                               for d, (n, g) in b["por_deporte"].items())
    await update.message.reply_text(t)


async def historial(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    filas = apuestas.ultimas(update.effective_chat.id)
    if not filas:
        await update.message.reply_text("Sin apuestas registradas.")
        return
    await update.message.reply_text("\n".join(_fmt(a) + (f" ({a['ganancia']:+,.0f})" if a["estado"] != "pendiente" else "")
                                              for a in filas))


# ------------------------------------------------------------ herramientas
async def mines_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    a = [_num(x) for x in (ctx.args or [])]
    if not a or a[0] is None:
        await update.message.reply_text(
            "Uso: /mines <minas> [gemas] [apuesta]\nEjemplos:\n/mines 3\n/mines 3 10 5000\n\n" + mines.tabla(3))
        return
    minas = int(a[0])
    monto = a[2] if len(a) > 2 and a[2] else None
    if len(a) > 1 and a[1]:
        k = int(a[1])
        if not (1 <= minas <= 24 and 1 <= k <= 25 - minas):
            await update.message.reply_text(f"Con {minas} minas puedes sacar entre 1 y {25 - minas} gemas.")
            return
        p, m = mines.fila(minas, k)
        t = (f"💣 {minas} minas, {k} gemas\nProbabilidad de salir vivo: {p:.2%}\nMultiplicador: x{m:,.2f}"
             f"\nProbabilidad de perder: {1 - p:.2%}")
        if monto:
            t += f"\nApuesta {monto:,.0f} → cobras {monto * m:,.2f} (si pierdes, -{monto:,.0f})"
        t += "\n\nVentaja de la casa: 1 %. No se puede predecir dónde están las minas."
        await update.message.reply_text(t)
        return
    await update.message.reply_text(mines.tabla(minas, monto))


async def valor(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    a = [_num(x) for x in (ctx.args or [])]
    if len(a) < 2 or None in a[:2] or a[0] <= 1 or not 0 < a[1] < 100:
        await update.message.reply_text("Uso: /valor <cuota> <tu probabilidad %>\nEjemplo: /valor 2.10 55")
        return
    c, p = a[0], a[1] / 100
    ev = p * c - 1
    kelly = max(0.0, (p * c - 1) / (c - 1))
    t = (f"Cuota {c:g} · tu probabilidad {p:.0%}\nCuota justa: {1 / p:.2f}\nValor esperado: {ev:+.1%} por cada unidad\n")
    if ev > 0:
        t += (f"✅ Hay valor si tu probabilidad es correcta. Kelly completo: {kelly:.1%} de tu banca "
              f"(lo prudente es 1/4 de eso: {kelly / 4:.1%}).")
    else:
        t += "❌ Con esa probabilidad, la cuota está por debajo de lo justo: a la larga pierdes."
    await update.message.reply_text(t)


async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Hola 👋\n\n" + AYUDA)


async def post_init(app: Application):
    await app.bot.set_my_commands([
        BotCommand("futbol", "Fútbol: partidos y predicciones"), BotCommand("baloncesto", "Baloncesto (NBA)"),
        BotCommand("beisbol", "Béisbol (MLB)"), BotCommand("americano", "Fútbol americano (NFL)"),
        BotCommand("envivo", "Partidos en vivo ahora"),
        BotCommand("hockey", "Hockey (NHL)"), BotCommand("mma", "MMA / UFC"), BotCommand("boxeo", "Boxeo"),
        BotCommand("apostar", "Registrar una apuesta"), BotCommand("pendientes", "Marcar ganada/perdida"),
        BotCommand("balance", "Mi balance"), BotCommand("historial", "Mis últimas apuestas"),
        BotCommand("mines", "Calculadora de Mines"), BotCommand("valor", "¿Tiene valor esta cuota?"),
        BotCommand("ayuda", "Ver comandos")])


def _health_server():
    """Muchos hostings gratis exigen que el servicio escuche en $PORT; así responde 'ok'."""
    port = os.environ.get("PORT")
    if not port:
        return
    from http.server import BaseHTTPRequestHandler, HTTPServer

    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"ok")

        def log_message(self, *a):
            pass

    threading.Thread(target=HTTPServer(("0.0.0.0", int(port)), H).serve_forever, daemon=True).start()


def main():
    _health_server()
    token = os.environ["TELEGRAM_TOKEN"]
    ipv4 = {"transport": httpx.AsyncHTTPTransport(local_address="0.0.0.0", retries=3)}
    req = HTTPXRequest(connect_timeout=30, read_timeout=30, write_timeout=30, pool_timeout=30, httpx_kwargs=ipv4)
    upd = HTTPXRequest(connect_timeout=30, read_timeout=60, httpx_kwargs=ipv4)
    app = (Application.builder().token(token).request(req).get_updates_request(upd)
           .post_init(post_init).build())

    conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(ap_desde_boton, pattern=r"^ap:\d+$"),
                      CommandHandler("apostar", ap_nueva)],
        states={
            SEL: [CallbackQueryHandler(ap_eleccion, pattern=r"^op:")],
            TEXTO: [MessageHandler(filters.TEXT & ~filters.COMMAND, ap_texto)],
            LIBRE: [MessageHandler(filters.TEXT & ~filters.COMMAND, ap_libre)],
            CUOTA: [MessageHandler(filters.TEXT & ~filters.COMMAND, ap_cuota)],
            MONTO: [MessageHandler(filters.TEXT & ~filters.COMMAND, ap_monto)],
        },
        fallbacks=[CommandHandler("cancelar", cancelar)],
        allow_reentry=True, per_message=False)
    app.add_handler(conv)

    for cmd in deportes.DEPORTES:
        app.add_handler(CommandHandler(cmd, _handler_deporte(cmd)))
    # alias con tilde (Telegram no los reconoce como comando, llegan como texto)
    app.add_handler(MessageHandler(filters.Regex(r"(?i)^/f[uú]tbol\b"), _handler_deporte("futbol")))
    app.add_handler(MessageHandler(filters.Regex(r"(?i)^/b[eé]isbol\b"), _handler_deporte("beisbol")))
    app.add_handler(MessageHandler(filters.Regex(r"(?i)^/americano\b"), _handler_deporte("americano")))
    app.add_handler(CommandHandler("envivo", envivo))
    app.add_handler(CommandHandler("boxeo", boxeo))
    app.add_handler(CommandHandler("pendientes", pendientes))
    app.add_handler(CallbackQueryHandler(resolver_cb, pattern=r"^res:"))
    app.add_handler(CommandHandler("balance", balance))
    app.add_handler(CommandHandler("historial", historial))
    app.add_handler(CommandHandler("mines", mines_cmd))
    app.add_handler(CommandHandler("valor", valor))
    app.add_handler(CommandHandler(["start", "ayuda", "help"], start))
    print("Bot encendido. Escríbele /start en Telegram. (Ctrl+C para apagar)", flush=True)
    app.run_polling()


if __name__ == "__main__":
    main()
