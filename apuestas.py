"""Registro de apuestas en SQLite (un archivo apuestas.db junto al bot)."""
import os
import sqlite3
from datetime import datetime

# En un servidor, apunta DATA_DIR a un disco persistente para no perder el historial.
DB = os.path.join(os.environ.get("DATA_DIR") or os.path.dirname(os.path.abspath(__file__)), "apuestas.db")


def _con():
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    c.execute("""CREATE TABLE IF NOT EXISTS apuestas (
        id INTEGER PRIMARY KEY AUTOINCREMENT, chat_id INTEGER, fecha TEXT, deporte TEXT,
        evento TEXT, seleccion TEXT, cuota REAL, monto REAL, prob REAL,
        estado TEXT DEFAULT 'pendiente', ganancia REAL DEFAULT 0)""")
    return c


def crear(chat_id, deporte, evento, seleccion, cuota, monto, prob=None):
    with _con() as c:
        cur = c.execute("INSERT INTO apuestas (chat_id,fecha,deporte,evento,seleccion,cuota,monto,prob) "
                        "VALUES (?,?,?,?,?,?,?,?)",
                        (chat_id, datetime.now().strftime("%Y-%m-%d %H:%M"), deporte, evento, seleccion,
                         cuota, monto, prob))
        return cur.lastrowid


def pendientes(chat_id):
    with _con() as c:
        return c.execute("SELECT * FROM apuestas WHERE chat_id=? AND estado='pendiente' ORDER BY id",
                         (chat_id,)).fetchall()


def ultimas(chat_id, n=10):
    with _con() as c:
        return c.execute("SELECT * FROM apuestas WHERE chat_id=? ORDER BY id DESC LIMIT ?",
                         (chat_id, n)).fetchall()


def resolver(chat_id, apuesta_id, resultado):
    """resultado: 'g' ganó, 'p' perdió, 'n' nula. Devuelve la fila actualizada o None."""
    with _con() as c:
        a = c.execute("SELECT * FROM apuestas WHERE id=? AND chat_id=? AND estado='pendiente'",
                      (apuesta_id, chat_id)).fetchone()
        if not a:
            return None
        estado, gan = {"g": ("ganada", a["monto"] * (a["cuota"] - 1)),
                       "p": ("perdida", -a["monto"]), "n": ("nula", 0.0)}[resultado]
        c.execute("UPDATE apuestas SET estado=?, ganancia=? WHERE id=?", (estado, gan, apuesta_id))
        return c.execute("SELECT * FROM apuestas WHERE id=?", (apuesta_id,)).fetchone()


def balance(chat_id):
    with _con() as c:
        filas = c.execute("SELECT * FROM apuestas WHERE chat_id=?", (chat_id,)).fetchall()
    cerr = [f for f in filas if f["estado"] in ("ganada", "perdida")]
    pend = [f for f in filas if f["estado"] == "pendiente"]
    apostado = sum(f["monto"] for f in cerr)
    ganancia = sum(f["ganancia"] for f in cerr)
    por_dep = {}
    for f in cerr:
        d = por_dep.setdefault(f["deporte"] or "otro", [0, 0.0])
        d[0] += 1
        d[1] += f["ganancia"]
    return {"total": len(filas), "cerradas": len(cerr), "ganadas": sum(f["estado"] == "ganada" for f in cerr),
            "pendientes": len(pend), "en_juego": sum(f["monto"] for f in pend), "apostado": apostado,
            "ganancia": ganancia, "roi": (ganancia / apostado) if apostado else 0.0, "por_deporte": por_dep}
