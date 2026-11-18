"""Datos gratis (API pública de ESPN, sin clave) + modelos por deporte + comparación con cuotas.

Fixtures:   site.api.espn.com/.../{deporte}/{liga}/scoreboard?dates=YYYYMMDD
Tablas:     site.api.espn.com/apis/v2/.../{deporte}/{liga}/standings  (goles/puntos a favor y en contra)
Cuotas:     vienen dentro del scoreboard (DraftKings) cuando existen.
"""
import math
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import requests

TZ = ZoneInfo("America/Bogota")
SITE = "https://site.api.espn.com/apis/site/v2/sports"
STAND = "https://site.api.espn.com/apis/v2/sports"
_http = requests.Session()
_http.headers["User-Agent"] = "Mozilla/5.0"

FUTBOL = [
    ("col.1", "Liga BetPlay (Colombia)"), ("conmebol.libertadores", "Copa Libertadores"),
    ("conmebol.sudamericana", "Copa Sudamericana"), ("col.copa", "Copa Colombia"),
    ("eng.1", "Premier League"), ("esp.1", "La Liga"), ("ger.1", "Bundesliga"),
    ("ita.1", "Serie A"), ("fra.1", "Ligue 1"), ("uefa.champions", "Champions League"),
    ("uefa.europa", "Europa League"), ("usa.1", "MLS"), ("mex.1", "Liga MX"),
    ("arg.1", "Liga Argentina"), ("bra.1", "Brasileirão"), ("ned.1", "Eredivisie"),
    ("por.1", "Primeira Liga"),
]

DEPORTES = {
    "futbol": {"emoji": "⚽", "kind": "soccer", "ligas": [("soccer/" + c, n) for c, n in FUTBOL]},
    "baloncesto": {"emoji": "🏀", "kind": "points", "sigma": 12.0, "hfa": 2.5, "k": 6,
                   "ligas": [("basketball/nba", "NBA"), ("basketball/wnba", "WNBA")]},
    "americano": {"emoji": "🏈", "kind": "points", "sigma": 13.5, "hfa": 1.8, "k": 4,
                  "ligas": [("football/nfl", "NFL")]},
    "hockey": {"emoji": "🏒", "kind": "count", "hfa": 1.04, "ligas": [("hockey/nhl", "NHL")]},
    "beisbol": {"emoji": "⚾", "kind": "count", "hfa": 1.04, "ligas": [("baseball/mlb", "MLB")]},
    "mma": {"emoji": "🥊", "kind": "market", "ligas": [("mma/ufc", "UFC")]},
}

_cache = {}


class SinConexion(RuntimeError):
    pass


def _json(url, params=None, ttl=300):
    key = (url, tuple(sorted((params or {}).items())))
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < ttl:
        return hit[1]
    try:
        r = _http.get(url, params=params, timeout=20)
        if r.status_code >= 500:
            return None
        data = r.json() if r.ok else {}
    except Exception:
        return None
    _cache[key] = (time.time(), data)
    return data


# ----------------------------------------------------------------- tablas
def _tabla(path):
    j = _json(f"{STAND}/{path}/standings", ttl=6 * 3600) or {}
    entradas = []

    def recorrer(n):
        for c in n.get("children") or []:
            recorrer(c)
        entradas.extend((n.get("standings") or {}).get("entries", []))

    recorrer(j)
    out = {}
    for e in entradas:
        st = {s["name"]: s["value"] for s in e.get("stats", []) if s.get("value") is not None}
        out[str(e["team"]["id"])] = st
    return out


def _gp(st):
    return st.get("gamesPlayed") or (st.get("wins", 0) + st.get("losses", 0) + st.get("ties", 0)
                                     + st.get("otLosses", 0))


def _media_liga(tabla):
    gp = sum(_gp(s) for s in tabla.values())
    gf = sum(s.get("pointsFor", 0) for s in tabla.values())
    return gf / gp if gp else None


# ----------------------------------------------------------------- modelos
def _pois(k, lam):
    return math.exp(-lam) * lam ** k / math.factorial(k)


def _cdf(x):
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def _grid(lh, la, n=14):
    p1 = px = p2 = btts = o15 = o25 = o35 = 0.0
    mejor, pm = (0, 0), 0.0
    for i in range(n):
        for j in range(n):
            p = _pois(i, lh) * _pois(j, la)
            if i > j: p1 += p
            elif i == j: px += p
            else: p2 += p
            if i and j: btts += p
            if i + j > 1.5: o15 += p
            if i + j > 2.5: o25 += p
            if i + j > 3.5: o35 += p
            if p > pm: pm, mejor = p, (i, j)
    return {"p1": p1, "px": px, "p2": p2, "btts": btts, "o15": o15, "o25": o25, "o35": o35, "marcador": mejor}


def modelo_futbol(sh, sa, mu):
    if not sh or not sa or not mu:
        return None
    gh, ga_ = _gp(sh), _gp(sa)
    if gh < 3 or ga_ < 3:
        return None
    k = 3

    def f(st, g):
        return ((st.get("pointsFor", 0) + k * mu) / (g + k) / mu,
                (st.get("pointsAgainst", 0) + k * mu) / (g + k) / mu)

    atk_h, def_h = f(sh, gh)
    atk_a, def_a = f(sa, ga_)
    lh = mu * 1.12 * atk_h * def_a
    la = mu * 0.90 * atk_a * def_h
    r = _grid(lh, la)
    r.update(xg_l=lh, xg_v=la, gp=min(gh, ga_))
    return r


def modelo_puntos(sh, sa, lg, cfg):
    if not sh or not sa or not lg:
        return None
    gh, ga_ = _gp(sh), _gp(sa)
    if gh < 2 or ga_ < 2:
        return None
    k = cfg["k"]

    def f(st, g):
        return ((st.get("pointsFor", 0) + k * lg) / (g + k), (st.get("pointsAgainst", 0) + k * lg) / (g + k))

    of_h, ag_h = f(sh, gh)
    of_a, ag_a = f(sa, ga_)
    pl = lg + (of_h - lg) + (ag_a - lg) + cfg["hfa"] / 2
    pv = lg + (of_a - lg) + (ag_h - lg) - cfg["hfa"] / 2
    margen = pl - pv
    return {"pl": pl, "pv": pv, "total": pl + pv, "margen": margen, "p1": _cdf(margen / cfg["sigma"]),
            "gp": min(gh, ga_)}


def modelo_conteo(sh, sa, mu, cfg):
    """Hockey / béisbol: Poisson sin empates (el empate se reparte proporcional)."""
    if not sh or not sa or not mu:
        return None
    gh, ga_ = _gp(sh), _gp(sa)
    if gh < 3 or ga_ < 3:
        return None
    k = 5

    def f(st, g):
        return ((st.get("pointsFor", 0) + k * mu) / (g + k) / mu,
                (st.get("pointsAgainst", 0) + k * mu) / (g + k) / mu)

    atk_h, def_h = f(sh, gh)
    atk_a, def_a = f(sa, ga_)
    lh = mu * atk_h * def_a * cfg["hfa"]
    la = mu * atk_a * def_h / cfg["hfa"]
    r = _grid(lh, la, 20)
    s = r["p1"] + r["p2"]
    p1 = r["p1"] + r["px"] * r["p1"] / s
    return {"p1": p1, "xg_l": lh, "xg_v": la, "total": lh + la, "gp": min(gh, ga_)}


# ----------------------------------------------------------------- cuotas
def _am(s):
    if s is None:
        return None
    s = str(s).strip().upper()
    if s in ("EVEN", "EV", "PK"):
        return 2.0
    try:
        n = float(s)
    except ValueError:
        return None
    if n > 0:
        return 1 + n / 100
    if n < 0:
        return 1 + 100 / abs(n)
    return None


def _cuotas(comp):
    lst = comp.get("odds") or []
    o = lst[0] if lst and lst[0] else None
    if not o:
        return None

    def cl(d):
        return _am(((d or {}).get("close") or {}).get("odds"))

    ml = o.get("moneyline") or {}
    home, away, draw = cl(ml.get("home")), cl(ml.get("away")), cl(ml.get("draw"))
    if home is None:
        home = _am((o.get("homeTeamOdds") or {}).get("moneyLine"))
    if away is None:
        away = _am((o.get("awayTeamOdds") or {}).get("moneyLine"))
    if draw is None:
        draw = _am((o.get("drawOdds") or {}).get("moneyLine"))
    tot = o.get("total") or {}
    over, under = cl(tot.get("over")), cl(tot.get("under"))
    if not (home and away) and not o.get("overUnder"):
        return None
    return {"home": home, "away": away, "draw": draw, "linea": o.get("overUnder"), "over": over,
            "under": under, "spread": o.get("spread"), "prov": (o.get("provider") or {}).get("displayName", "")}


def _sin_margen(cuotas):
    """Probabilidades del mercado quitando el margen de la casa."""
    imp = [1 / c if c else None for c in cuotas]
    s = sum(i for i in imp if i)
    return [i / s if i else None for i in imp]


# ----------------------------------------------------------------- fixtures
def _parse_eventos(j, path, liga, cfg, desde, hasta):
    out = []
    for e in (j or {}).get("events", []):
        for comp in e.get("competitions", []):
            est = (comp.get("status") or e.get("status") or {}).get("type", {})
            if est.get("state") == "post":
                continue
            try:
                dt = datetime.fromisoformat(comp.get("date", e["date"]).replace("Z", "+00:00")).astimezone(TZ)
            except Exception:
                continue
            if not (desde <= dt.date() <= hasta):
                continue
            cs = comp.get("competitors", [])
            if cfg["kind"] == "market":
                if len(cs) != 2:
                    continue
                nombres = [(c.get("athlete") or c.get("team") or {}).get("displayName", "?") for c in cs]
                out.append({"id": comp.get("id", e["id"]), "path": path, "liga": liga, "dt": dt,
                            "local": nombres[0], "visita": nombres[1], "lid": None, "vid": None,
                            "cuotas": _cuotas(comp), "vivo": est.get("state") == "in"})
                continue
            m = {c.get("homeAway"): c for c in cs}
            if "home" not in m or "away" not in m:
                continue
            out.append({"id": comp.get("id", e["id"]), "path": path, "liga": liga, "dt": dt,
                        "local": m["home"]["team"]["displayName"], "visita": m["away"]["team"]["displayName"],
                        "lid": str(m["home"]["team"]["id"]), "vid": str(m["away"]["team"]["id"]),
                        "cuotas": _cuotas(comp), "vivo": est.get("state") == "in"})
    return out


def proximos(deporte, dias=3, filtro=""):
    """Eventos de hoy y próximos días. Si no hay nada, amplía la ventana hasta 10 días."""
    cfg = DEPORTES[deporte]
    hoy = datetime.now(TZ).date()
    ligas = [(p, n) for p, n in cfg["ligas"] if not filtro or filtro in n.lower()]
    por_equipo = bool(filtro) and not ligas  # el filtro no es una liga: buscar por nombre de equipo
    if por_equipo:
        ligas = list(cfg["ligas"])
    fallos = total = 0
    for ini, fin in ((0, dias), (dias, 10)):
        tareas = [(p, n, hoy + timedelta(d)) for p, n in ligas for d in range(ini, fin)]

        def uno(t):
            return t, _json(f"{SITE}/{t[0]}/scoreboard", {"dates": t[2].strftime("%Y%m%d")}, ttl=600)

        with ThreadPoolExecutor(12) as ex:
            res = list(ex.map(uno, tareas))
        vistos, out = set(), []
        for (p, n, d), j in res:
            total += 1
            if j is None:
                fallos += 1
                continue
            for ev in _parse_eventos(j, p, n, cfg, hoy + timedelta(ini), hoy + timedelta(fin - 1)):
                if por_equipo and filtro not in (ev["local"] + " " + ev["visita"]).lower():
                    continue
                if (p, ev["id"]) not in vistos:
                    vistos.add((p, ev["id"]))
                    out.append(ev)
        if out:
            break
    if total and fallos == total:
        raise SinConexion("No pude conectar con ESPN (¿sin internet o bloqueado?)")
    return sorted(out, key=lambda e: e["dt"])


# ----------------------------------------------------------------- texto
def _dia(dt):
    hoy = datetime.now(TZ).date()
    if dt.date() == hoy:
        return "hoy"
    if dt.date() == hoy + timedelta(days=1):
        return "mañana"
    return dt.strftime("%d/%m")


def _c(p):
    return f"{1 / p:.2f}" if p and p > 0.001 else "-"


def _marcar(opts):
    """opts: (nombre, prob_modelo, cuota_mercado, prob_mercado). Devuelve líneas de valor."""
    out = []
    for nom, p, c, pm in opts:
        if c and pm is not None and p - pm >= 0.05 and p * c - 1 >= 0.05:
            out.append(f"⭐ Posible valor: {nom} (modelo {p:.0%} vs mercado {pm:.0%}, cuota {c:.2f}, "
                       f"valor esperado {p * c - 1:+.0%})")
    return out


def analizar(ev, deporte, tablas):
    """Devuelve (texto, opciones) donde opciones = [(etiqueta, prob)] para el registro de apuestas."""
    cfg = DEPORTES[deporte]
    emoji = cfg["emoji"]
    cu = ev["cuotas"]
    cab = (f"{emoji} {ev['local']} vs {ev['visita']}" + ("  🔴 EN JUEGO" if ev["vivo"] else "") +
           f"\n🏆 {ev['liga']} · {_dia(ev['dt'])} {ev['dt']:%H:%M}\n")
    tabla = tablas.get(ev["path"], {})
    sh, sa = tabla.get(ev["lid"]), tabla.get(ev["vid"])
    lines, opciones = [], []

    if cfg["kind"] == "market":
        if cu and cu["home"] and cu["away"]:
            p = _sin_margen([cu["home"], cu["away"]])
            lines.append(f"Mercado ({cu['prov']}): {ev['local']} {cu['home']:.2f} ({p[0]:.0%}) · "
                         f"{ev['visita']} {cu['away']:.2f} ({p[1]:.0%})")
            opciones = [(f"Gana {ev['local']}", p[0]), (f"Gana {ev['visita']}", p[1])]
            lines.append("Sin modelo propio para combate: solo se muestra lo que dice el mercado.")
        else:
            lines.append("Sin cuotas disponibles todavía.")
        return cab + "\n".join(lines) + "\n", opciones

    mu = _media_liga(tabla)
    r = None
    if cfg["kind"] == "soccer":
        r = modelo_futbol(sh, sa, mu)
    elif cfg["kind"] == "points":
        r = modelo_puntos(sh, sa, mu, cfg)
    else:
        r = modelo_conteo(sh, sa, mu, cfg)
    if r is None:
        lines.append("Sin datos suficientes de estos equipos para el modelo.")

    if r and cfg["kind"] == "soccer":
        lines.append(f"Goles esperados: {r['xg_l']:.2f} - {r['xg_v']:.2f} · marcador probable "
                     f"{r['marcador'][0]}-{r['marcador'][1]}")
        lines.append(f"Local {r['p1']:.0%} ({_c(r['p1'])}) · Empate {r['px']:.0%} ({_c(r['px'])}) · "
                     f"Visita {r['p2']:.0%} ({_c(r['p2'])})")
        lines.append(f"+1.5: {r['o15']:.0%} · +2.5: {r['o25']:.0%} · +3.5: {r['o35']:.0%} · "
                     f"Ambos anotan: {r['btts']:.0%}")
        opciones = [(f"Gana {ev['local']}", r["p1"]), ("Empate", r["px"]), (f"Gana {ev['visita']}", r["p2"]),
                    ("Más de 2.5 goles", r["o25"]), ("Menos de 2.5 goles", 1 - r["o25"]),
                    ("Ambos anotan", r["btts"])]
        if cu and cu["home"] and cu["away"] and cu["draw"]:
            pm = _sin_margen([cu["home"], cu["draw"], cu["away"]])
            lines.append(f"Mercado ({cu['prov']}): {cu['home']:.2f} / {cu['draw']:.2f} / {cu['away']:.2f}")
            lines += _marcar([("Local", r["p1"], cu["home"], pm[0]), ("Empate", r["px"], cu["draw"], pm[1]),
                              ("Visita", r["p2"], cu["away"], pm[2])])
    elif r and cfg["kind"] == "points":
        lines.append(f"Puntos esperados: {ev['local']} {r['pl']:.1f} - {r['pv']:.1f} {ev['visita']} "
                     f"(total {r['total']:.1f}, diferencia {r['margen']:+.1f})")
        lines.append(f"Gana local {r['p1']:.0%} ({_c(r['p1'])}) · Gana visita {1 - r['p1']:.0%} ({_c(1 - r['p1'])})")
        opciones = [(f"Gana {ev['local']}", r["p1"]), (f"Gana {ev['visita']}", 1 - r["p1"])]
        if cu and cu["home"] and cu["away"]:
            pm = _sin_margen([cu["home"], cu["away"]])
            lines.append(f"Mercado ({cu['prov']}): {cu['home']:.2f} / {cu['away']:.2f}"
                         + (f" · diferencia {cu['spread']:+}" if cu.get("spread") is not None else "")
                         + (f" · total {cu['linea']}" if cu.get("linea") else ""))
            lines += _marcar([("Local", r["p1"], cu["home"], pm[0]), ("Visita", 1 - r["p1"], cu["away"], pm[1])])
        if cu and cu.get("linea"):
            d = r["total"] - float(cu["linea"])
            if abs(d) >= 4:
                lines.append(f"📊 Mi total ({r['total']:.1f}) vs línea {cu['linea']}: tira a "
                             f"{'MÁS' if d > 0 else 'MENOS'} (muy aproximado)")
    elif r:
        lines.append(f"Esperado: {r['xg_l']:.1f} - {r['xg_v']:.1f} (total {r['total']:.1f})")
        lines.append(f"Gana local {r['p1']:.0%} ({_c(r['p1'])}) · Gana visita {1 - r['p1']:.0%} ({_c(1 - r['p1'])})")
        opciones = [(f"Gana {ev['local']}", r["p1"]), (f"Gana {ev['visita']}", 1 - r["p1"])]
        if cu and cu["home"] and cu["away"]:
            pm = _sin_margen([cu["home"], cu["away"]])
            lines.append(f"Mercado ({cu['prov']}): {cu['home']:.2f} / {cu['away']:.2f}"
                         + (f" · total {cu['linea']}" if cu.get("linea") else ""))
            lines += _marcar([("Local", r["p1"], cu["home"], pm[0]), ("Visita", 1 - r["p1"], cu["away"], pm[1])])
    if r and r["gp"] < 8:
        lines.append("⚠️ Pocos partidos jugados: la estimación es poco fiable.")
    if not cu and r:
        lines.append("Sin cuotas de referencia: compara tú con la cuota de tu casa (cuota justa arriba).")
    return cab + "\n".join(lines) + "\n", opciones


def reporte(deporte, filtro="", maximo=24):
    """Devuelve (lista de dicts {liga, texto, opciones, label}, nota)."""
    evs = proximos(deporte, filtro=filtro.lower().strip())
    if not evs:
        return [], "No hay eventos próximos" + (f" para «{filtro}»" if filtro else "") + " en los próximos 10 días."
    nota = ""
    if len(evs) > maximo:
        ej = {"futbol": "colombia", "baloncesto": "nba"}.get(deporte, DEPORTES[deporte]["ligas"][0][1].lower())
        nota = (f"Mostrando los {maximo} primeros de {len(evs)}. "
                f"Filtra por liga o equipo, por ejemplo: /{deporte} {ej}")
        evs = evs[:maximo]
    paths = sorted({e["path"] for e in evs})
    cfg = DEPORTES[deporte]
    with ThreadPoolExecutor(8) as ex:
        tablas = dict(zip(paths, ex.map(_tabla, paths))) if cfg["kind"] != "market" else {}
    out = []
    for ev in evs:
        texto, ops = analizar(ev, deporte, tablas)
        out.append({"liga": ev["liga"], "texto": texto, "opciones": ops,
                    "label": f"{ev['local']} vs {ev['visita']}"})
    return out, nota
