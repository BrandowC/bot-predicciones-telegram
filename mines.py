"""Calculadora de Mines (tablero de 25 casillas, ventaja de la casa 1 %).

No predice nada: las minas salen de un generador aleatorio. Esto solo muestra las
probabilidades reales y cuánto paga cada jugada, para que veas el riesgo.
"""
from math import comb

CASILLAS = 25
RTP = 0.99  # Stake Mines devuelve 99 % en promedio


def fila(minas, gemas):
    p = comb(CASILLAS - minas, gemas) / comb(CASILLAS, gemas)  # prob. de sacar `gemas` seguidas sin mina
    return p, RTP / p


def tabla(minas, monto=None):
    if not 1 <= minas <= 24:
        return "Las minas deben ser entre 1 y 24.\nEjemplo: /mines 3"
    maxg = CASILLAS - minas
    ks = list(range(1, min(maxg, 10) + 1))
    if maxg > 10:
        ks.append(maxg)
    enc = f"💣 Mines · {minas} mina{'s' if minas > 1 else ''} · {CASILLAS} casillas"
    if monto:
        enc += f" · apuesta {monto:g}"
    lin = [enc, "", "Gemas | Prob. de salir vivo | Multiplicador" + (" | Cobras" if monto else "")]
    for k in ks:
        p, m = fila(minas, k)
        s = f"{k:>5} | {p:>9.1%} | x{m:,.2f}"
        if monto:
            s += f" | {monto * m:,.2f}"
        lin.append(s)
    lin += ["",
            "La probabilidad es de sacar ESAS gemas seguidas sin tocar mina.",
            "La ventaja de la casa es 1 %: por cada 100 que juegues, en promedio pierdes 1, "
            "sin importar cuántas minas pongas ni cuándo te retires.",
            "No existe forma de predecir dónde están las minas."]
    return "\n".join(lin)


def objetivo(minas, mult):
    """Cuántas gemas hacen falta para llegar a cierto multiplicador y con qué probabilidad."""
    for k in range(1, CASILLAS - minas + 1):
        p, m = fila(minas, k)
        if m >= mult:
            return k, p, m
    return None
