"""Datos gratis (API pública de ESPN, sin clave) + modelos por deporte + comparación con cuotas.

Fixtures / en vivo: site.api.espn.com/.../{deporte}/{liga}/scoreboard?dates=YYYYMMDD
Tablas:             site.api.espn.com/apis/v2/.../{deporte}/{liga}/standings
Forma reciente:     site.api.espn.com/.../{deporte}/{liga}/teams/{id}/schedule
Cuotas:             vienen dentro del scoreboard (DraftKings) cuando existen.

Modelo: fuerza de ataque/defensa de la temporada (65 %) mezclada con los últimos 5 partidos (35 %),
ajuste por días de descanso y, en vivo, recalculado con marcador, minuto y tarjetas rojas.
"""
import math
import re
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
# minutos por periodo (para el cálculo en vivo)
PERIODO_MIN = {"basketball/nba": 12, "basketball/wnba": 10, "football/nfl": 15, "hockey/nhl": 20}
N_PERIODOS = {"hockey/nhl": 3}

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


# ----------------------------------------------------------------- forma reciente
def _score(x):
    s = x.get("score")
    if isinstance(s, dict):
        s = s.get("value", s.get("displayValue"))
    try:
        return float(s)
    except (TypeError, ValueError):
        return None


def _forma(path, tid, soccer):
    """Últimos 5 partidos jugados del equipo: racha, goles/puntos y fecha del último."""
    j = _json(f"{SITE}/{path}/teams/{tid}/schedule", ttl=3600)
    if not j:
        return None
    jugados = []
    for e in j.get("events", []):
        comps = e.get("competitions") or []
        if not comps:
            continue
        c = comps[0]
        tp = ((e.get("status") or c.get("status") or {}).get("type") or {})
        if tp.get("state") != "post":
            continue
        cs = c.get("competitors", [])

        def cid(x):
            return str((x.get("team") or {}).get("id") or x.get("id"))

        mio = next((x for x in cs if cid(x) == str(tid)), None)
        rival = next((x for x in cs if x is not mio), None)
        if not mio or not rival:
            continue
        gf, gc = _score(mio), _score(rival)
        if gf is None or gc is None:
            continue
        try:
            dt = datetime.fromisoformat(c.get("date", e["date"]).replace("Z", "+00:00"))
        except Exception:
            continue
        jugados.append((dt, gf, gc))
    if not jugados:
        return None
    jugados.sort(key=lambda x: x[0])
    ult = jugados[-5:]
    racha, pts = [], 0
    for _, gf, gc in ult:
        if gf > gc:
            racha.append("G"); pts += 3 if soccer else 1
        elif gf == gc:
            racha.append("E"); pts += 1 if soccer else 0
        else:
            racha.append("P")
    n = len(ult)
    return {"n": n, "racha": racha, "pts": pts, "gf": sum(u[1] for u in ult) / n,
            "gc": sum(u[2] for u in ult) / n, "ult": ult[-1][0]}


def _ajustar(st, f):
    """Mezcla la temporada (65 %) con los últimos partidos (35 %)."""
    if not st or not f or f["n"] < 3:
        return st
    g = _gp(st)
    if not g:
        return st
    w = 0.35 * min(1.0, f["n"] / 5)
    a = dict(st)
    a["pointsFor"] = g * ((1 - w) * st.get("pointsFor", 0) / g + w * f["gf"])
    a["pointsAgainst"] = g * ((1 - w) * st.get("pointsAgainst", 0) / g + w * f["gc"])
    return a


def _desc(ev, fh, fa):
    """Diferencia de días de descanso (local - visita), limitada a ±3."""
    if not fh or not fa:
        return 0
    dh = min(7, max(0, (ev["dt"].date() - fh["ult"].astimezone(TZ).date()).days))
    da = min(7, max(0, (ev["dt"].date() - fa["ult"].astimezone(TZ).date()).days))
    return max(-3, min(3, dh - da))


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


def modelo_futbol(sh, sa, mu, desc=0):
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
    lh = mu * 1.12 * atk_h * def_a * (1 + 0.015 * desc)
    la = mu * 0.90 * atk_a * def_h * (1 - 0.015 * desc)
    r = _grid(lh, la)
    r.update(xg_l=lh, xg_v=la, gp=min(gh, ga_))
    return r


def modelo_puntos(sh, sa, lg, cfg, desc=0):
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
    pl = lg + (of_h - lg) + (ag_a - lg) + cfg["hfa"] / 2 + 0.3 * desc
    pv = lg + (of_a - lg) + (ag_h - lg) - cfg["hfa"] / 2 - 0.3 * desc
    margen = pl - pv
    return {"pl": pl, "pv": pv, "total": pl + pv, "margen": margen, "p1": _cdf(margen / cfg["sigma"]),
            "gp": min(gh, ga_)}


def modelo_conteo(sh, sa, mu, cfg, desc=0):
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
    lh = mu * atk_h * def_a * cfg["hfa"] * (1 + 0.01 * desc)
    la = mu * atk_a * def_h / cfg["hfa"] * (1 - 0.01 * desc)
    r = _grid(lh, la, 20)
    s = r["p1"] + r["p2"]
    p1 = r["p1"] + r["px"] * r["p1"] / s
    return {"p1": p1, "xg_l": lh, "xg_v": la, "total": lh + la, "gp": min(gh, ga_)}


def _modelo(cfg, ev, tabla, fh, fa):
    sh, sa = _ajustar(tabla.get(ev["lid"]), fh), _ajustar(tabla.get(ev["vid"]), fa)
    mu = _media_liga(tabla)
    d = _desc(ev, fh, fa)
    if cfg["kind"] == "soccer":
        return modelo_futbol(sh, sa, mu, d)
    if cfg["kind"] == "points":
        return modelo_puntos(sh, sa, mu, cfg, d)
    return modelo_conteo(sh, sa, mu, cfg, d)


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


# ----------------------------------------------------------------- fixtures y en vivo
def _ligas_filtro(cfg, filtro):
    ligas = [(p, n) for p, n in cfg["ligas"] if not filtro or filtro in n.lower()]
    por_equipo = bool(filtro) and not ligas  # el filtro no es una liga: buscar por nombre de equipo
    return (list(cfg["ligas"]) if por_equipo else ligas), por_equipo


def _parse_eventos(j, path, liga, cfg, desde, hasta):
    """Partidos que todavía no empezaron."""
    out = []
    for e in (j or {}).get("events", []):
        for comp in e.get("competitions", []):
            est = (comp.get("status") or e.get("status") or {}).get("type", {})
            if est.get("state") != "pre":
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
                            "cuotas": _cuotas(comp)})
                continue
            m = {c.get("homeAway"): c for c in cs}
            if "home" not in m or "away" not in m:
                continue
            out.append({"id": comp.get("id", e["id"]), "path": path, "liga": liga, "dt": dt,
                        "local": m["home"]["team"]["displayName"], "visita": m["away"]["team"]["displayName"],
                        "lid": str(m["home"]["team"]["id"]), "vid": str(m["away"]["team"]["id"]),
                        "cuotas": _cuotas(comp)})
    return out


def _parse_vivo(j, path, liga, cfg):
    out = []
    for e in (j or {}).get("events", []):
        for comp in e.get("competitions", []):
            st = comp.get("status") or e.get("status") or {}
            tp = st.get("type", {})
            if tp.get("state") != "in" or cfg["kind"] == "market":
                continue
            m = {c.get("homeAway"): c for c in comp.get("competitors", [])}
            if "home" not in m or "away" not in m:
                continue
            rojas = {}
            for d in comp.get("details", []) or []:
                if d.get("redCard"):
                    t = str((d.get("team") or {}).get("id"))
                    rojas[t] = rojas.get(t, 0) + 1
            out.append({"id": comp.get("id", e["id"]), "path": path, "liga": liga,
                        "dt": datetime.now(TZ), "local": m["home"]["team"]["displayName"],
                        "visita": m["away"]["team"]["displayName"], "lid": str(m["home"]["team"]["id"]),
                        "vid": str(m["away"]["team"]["id"]),
                        "gl": int(_score(m["home"]) or 0), "gv": int(_score(m["away"]) or 0),
                        "reloj": st.get("displayClock") or "", "periodo": st.get("period") or 0,
                        "estado": tp.get("name", ""), "rojas": rojas, "cuotas": None})
    return out


def _fetch(ligas, fechas, ttl):
    tareas = [(p, n, d) for p, n in ligas for d in fechas]

    def uno(t):
        return t, _json(f"{SITE}/{t[0]}/scoreboard", {"dates": t[2].strftime("%Y%m%d")}, ttl=ttl)

    with ThreadPoolExecutor(12) as ex:
        res = list(ex.map(uno, tareas))
    if res and all(j is None for _, j in res):
        raise SinConexion("No pude conectar con ESPN (¿sin internet o bloqueado?)")
    return [(t, j) for t, j in res if j is not None]


def proximos(deporte, dias=3, filtro=""):
    """Partidos por empezar hoy y en los próximos días. Si no hay nada, amplía hasta 10 días."""
    cfg = DEPORTES[deporte]
    hoy = datetime.now(TZ).date()
    ligas, por_equipo = _ligas_filtro(cfg, filtro)
    for ini, fin in ((0, dias), (dias, 10)):
        res = _fetch(ligas, [hoy + timedelta(d) for d in range(ini, fin)], 60)
        vistos, out = set(), []
        for (p, n, d), j in res:
            for ev in _parse_eventos(j, p, n, cfg, hoy + timedelta(ini), hoy + timedelta(fin - 1)):
                if por_equipo and filtro not in (ev["local"] + " " + ev["visita"]).lower():
                    continue
                if (p, ev["id"]) not in vistos:
                    vistos.add((p, ev["id"]))
                    out.append(ev)
        if out:
            return sorted(out, key=lambda e: e["dt"])
    return []


def vivos(deporte, filtro=""):
    cfg = DEPORTES[deporte]
    hoy = datetime.now(TZ).date()
    ligas, por_equipo = _ligas_filtro(cfg, filtro)
    res = _fetch(ligas, [hoy - timedelta(1), hoy, hoy + timedelta(1)], 25)
    vistos, out = set(), []
    for (p, n, d), j in res:
        for ev in _parse_vivo(j, p, n, cfg):
            if por_equipo and filtro not in (ev["local"] + " " + ev["visita"]).lower():
                continue
            if (p, ev["id"]) not in vistos:
                vistos.add((p, ev["id"]))
                out.append(ev)
    return out


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


def _mezcla(p, pm):
    """El mercado suele estar mejor calibrado que mi modelo (ve lesiones y alineaciones): 60 % mercado."""
    return 0.4 * p + 0.6 * pm


def _marcar(opts):
    """opts: (nombre, prob_modelo, cuota_mercado, prob_mercado). Devuelve líneas de valor/advertencia."""
    out, avisado = [], False
    for nom, p, c, pm in opts:
        if not c or pm is None:
            continue
        mix = _mezcla(p, pm)
        if abs(p - pm) >= 0.15:
            if avisado:
                continue  # una sola advertencia por partido
            avisado = True
            out.append(f"⚠️ Mi modelo ({p:.0%} para {nom}) discrepa mucho del mercado ({pm:.0%}): seguramente hay "
                       f"lesiones o alineaciones que no veo. Me fío más del mercado.")
        elif mix - pm >= 0.03 and mix * c - 1 >= 0.03:
            out.append(f"⭐ Posible valor: {nom} (combinada {mix:.0%} vs mercado {pm:.0%}, cuota {c:.2f}, "
                       f"valor esperado {mix * c - 1:+.0%})")
    return out


def _combinar(opciones, ps_mercado):
    """Reemplaza las primeras probabilidades de `opciones` por la mezcla modelo+mercado."""
    for i, pm in enumerate(ps_mercado):
        if pm is not None:
            etiqueta, p = opciones[i]
            opciones[i] = (etiqueta, _mezcla(p, pm))


def _prov(cu):
    return cu["prov"] or "referencia"


def _plural_v(n):
    return f"{n} victoria" + ("" if n == 1 else "s")


def _txt_forma(f, soccer):
    r = " ".join(f["racha"])
    if soccer:
        return f"{r} ({f['pts']} pts) · {f['gf']:.1f} GF / {f['gc']:.1f} GC"
    return f"{r} ({_plural_v(f['pts'])}) · {f['gf']:.1f} - {f['gc']:.1f} por juego"


def _senal(f, nombre, soccer):
    if not f or f["n"] < 4:
        return None
    rate = f["pts"] / (3 * f["n"]) if soccer else f["pts"] / f["n"]
    if rate <= 0.25:
        return f"🔻 {nombre} llega en mala racha"
    if rate >= 0.8:
        return f"🔥 {nombre} llega en gran momento"
    return None


def _lineas_forma(ev, fh, fa, soccer):
    out = []
    if fh and fa:
        out.append(f"📈 Forma (últimos {min(fh['n'], fa['n'])}, de viejo a nuevo):")
        out.append(f"   Local: {_txt_forma(fh, soccer)}")
        out.append(f"   Visita: {_txt_forma(fa, soccer)}")
        for s in (_senal(fh, ev["local"], soccer), _senal(fa, ev["visita"], soccer)):
            if s:
                out.append(s)
        dh = (ev["dt"].date() - fh["ult"].astimezone(TZ).date()).days
        da = (ev["dt"].date() - fa["ult"].astimezone(TZ).date()).days
        out.append(f"😴 Descanso: local {dh} días · visita {da} días"
                   + (" ⚠️ viene cansado" if min(dh, da) <= 2 and abs(dh - da) >= 2 else ""))
    return out


def analizar(ev, deporte, tablas, formas):
    """Partido por empezar → (texto, opciones[(etiqueta, prob)])."""
    cfg = DEPORTES[deporte]
    emoji = cfg["emoji"]
    cu = ev["cuotas"]
    cab = f"{emoji} {ev['local']} vs {ev['visita']}\n🏆 {ev['liga']} · {_dia(ev['dt'])} {ev['dt']:%H:%M}\n"
    lines, opciones = [], []

    if cfg["kind"] == "market":
        if cu and cu["home"] and cu["away"]:
            p = _sin_margen([cu["home"], cu["away"]])
            lines.append(f"Mercado ({_prov(cu)}): {ev['local']} {cu['home']:.2f} ({p[0]:.0%}) · "
                         f"{ev['visita']} {cu['away']:.2f} ({p[1]:.0%})")
            opciones = [(f"Gana {ev['local']}", p[0]), (f"Gana {ev['visita']}", p[1])]
            lines.append("Sin modelo propio para combate: solo se muestra lo que dice el mercado.")
        else:
            lines.append("Sin cuotas disponibles todavía.")
        return cab + "\n".join(lines) + "\n", opciones

    tabla = tablas.get(ev["path"], {})
    fh, fa = formas.get((ev["path"], ev["lid"])), formas.get((ev["path"], ev["vid"]))
    r = _modelo(cfg, ev, tabla, fh, fa)
    if r is None:
        lines.append("Sin datos suficientes de estos equipos para el modelo.")
    soccer = cfg["kind"] == "soccer"

    if r and soccer:
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
            lines.append(f"Mercado ({_prov(cu)}): {cu['home']:.2f} / {cu['draw']:.2f} / {cu['away']:.2f}")
            lines += _marcar([("Local", r["p1"], cu["home"], pm[0]), ("Empate", r["px"], cu["draw"], pm[1]),
                              ("Visita", r["p2"], cu["away"], pm[2])])
            _combinar(opciones, pm)
            lines.append(f"🎯 Estimación combinada (modelo + mercado): Local {opciones[0][1]:.0%} · "
                         f"Empate {opciones[1][1]:.0%} · Visita {opciones[2][1]:.0%}")
    elif r and cfg["kind"] == "points":
        lines.append(f"Puntos esperados: {ev['local']} {r['pl']:.1f} - {r['pv']:.1f} {ev['visita']} "
                     f"(total {r['total']:.1f}, diferencia {r['margen']:+.1f})")
        lines.append(f"Gana local {r['p1']:.0%} ({_c(r['p1'])}) · Gana visita {1 - r['p1']:.0%} ({_c(1 - r['p1'])})")
        opciones = [(f"Gana {ev['local']}", r["p1"]), (f"Gana {ev['visita']}", 1 - r["p1"])]
        if cu and cu["home"] and cu["away"]:
            pm = _sin_margen([cu["home"], cu["away"]])
            lines.append(f"Mercado ({_prov(cu)}): {cu['home']:.2f} / {cu['away']:.2f}"
                         + (f" · diferencia {cu['spread']:+}" if cu.get("spread") is not None else "")
                         + (f" · total {cu['linea']}" if cu.get("linea") else ""))
            lines += _marcar([("Local", r["p1"], cu["home"], pm[0]), ("Visita", 1 - r["p1"], cu["away"], pm[1])])
            _combinar(opciones, pm)
            lines.append(f"🎯 Estimación combinada (modelo + mercado): Local {opciones[0][1]:.0%} · "
                         f"Visita {opciones[1][1]:.0%}")
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
            lines.append(f"Mercado ({_prov(cu)}): {cu['home']:.2f} / {cu['away']:.2f}"
                         + (f" · total {cu['linea']}" if cu.get("linea") else ""))
            lines += _marcar([("Local", r["p1"], cu["home"], pm[0]), ("Visita", 1 - r["p1"], cu["away"], pm[1])])
            _combinar(opciones, pm)
            lines.append(f"🎯 Estimación combinada (modelo + mercado): Local {opciones[0][1]:.0%} · "
                         f"Visita {opciones[1][1]:.0%}")
    if r:
        lines += _lineas_forma(ev, fh, fa, soccer)
        if r["gp"] < 8:
            lines.append("⚠️ Pocos partidos jugados: la estimación es poco fiable.")
        if not cu:
            lines.append("Sin cuotas de referencia: compara tú con la cuota de tu casa (cuota justa arriba).")
    return cab + "\n".join(lines) + "\n", opciones


# ----------------------------------------------------------------- en vivo
def _minuto_futbol(ev):
    nombre = ev["estado"].upper()
    if "HALFTIME" in nombre:
        return 45
    nums = [int(n) for n in re.findall(r"\d+", ev["reloj"])]
    if not nums:
        return 45 if ev["periodo"] <= 1 else 60
    return sum(nums[:2]) if "+" in ev["reloj"] else nums[0]


def _tiempo_restante(ev):
    """Fracción del partido que falta para deportes por periodos (baloncesto, NFL, hockey)."""
    pm = PERIODO_MIN.get(ev["path"])
    if not pm:
        return None
    nper = N_PERIODOS.get(ev["path"], 4)
    reloj = ev["reloj"] or "0"
    try:
        if ":" in reloj:
            m, s = reloj.split(":")[:2]
            t = int(m) + float(s) / 60
        else:
            t = float(reloj) / 60  # ESPN da solo segundos cuando falta menos de 1 minuto
    except ValueError:
        t = 0.0
    total = nper * pm
    if ev["periodo"] > nper:  # tiempo extra
        return max(0.02, min(0.1, t / total))
    return max(0.005, ((nper - ev["periodo"]) * pm + t) / total)


def analizar_vivo(ev, deporte, tablas, formas):
    cfg = DEPORTES[deporte]
    cab = (f"🔴 EN VIVO · {cfg['emoji']} {ev['local']} {ev['gl']} - {ev['gv']} {ev['visita']}\n"
           f"🏆 {ev['liga']} · ")
    tabla = tablas.get(ev["path"], {})
    fh, fa = formas.get((ev["path"], ev["lid"])), formas.get((ev["path"], ev["vid"]))
    r = _modelo(cfg, ev, tabla, fh, fa)
    opciones, lines = [], []
    gl, gv = ev["gl"], ev["gv"]

    if cfg["kind"] == "soccer":
        minuto = _minuto_futbol(ev)
        cab += f"minuto {minuto}'\n"
        rl, rv = ev["rojas"].get(ev["lid"], 0), ev["rojas"].get(ev["vid"], 0)
        if rl or rv:
            lines.append(f"🟥 Expulsados: {ev['local']} {rl} · {ev['visita']} {rv}")
        if r is None:
            lines.append("Sin datos suficientes para recalcular en vivo (solo marcador).")
            return cab + "\n".join(lines) + "\n", opciones
        f = max(0.03, (90 - minuto) / 90) if minuto < 90 else 0.03
        lh = r["xg_l"] * f * (0.7 ** rl) * (1.25 ** rv)
        la = r["xg_v"] * f * (0.7 ** rv) * (1.25 ** rl)
        p1 = px = p2 = btts = 0.0
        tot = gl + gv
        lineas = [tot + 0.5, tot + 1.5]
        ov = {ln: 0.0 for ln in lineas}
        for i in range(12):
            for j in range(12):
                p = _pois(i, lh) * _pois(j, la)
                fl, fv = gl + i, gv + j
                if fl > fv: p1 += p
                elif fl == fv: px += p
                else: p2 += p
                if fl and fv: btts += p
                for ln in lineas:
                    if fl + fv > ln:
                        ov[ln] += p
        lines.append(f"Resultado final: Local {p1:.0%} ({_c(p1)}) · Empate {px:.0%} ({_c(px)}) · "
                     f"Visita {p2:.0%} ({_c(p2)})")
        lines.append(f"Más de {lineas[0]:g}: {ov[lineas[0]]:.0%} · Más de {lineas[1]:g}: {ov[lineas[1]]:.0%}"
                     f" · Ambos anotan: {btts:.0%}")
        opciones = [(f"Gana {ev['local']}", p1), ("Empate", px), (f"Gana {ev['visita']}", p2),
                    (f"Más de {lineas[0]:g} goles", ov[lineas[0]]), (f"Más de {lineas[1]:g} goles", ov[lineas[1]])]
        for s in (_senal(fh, ev["local"], True), _senal(fa, ev["visita"], True)):
            if s:
                lines.append(s)
        lines.append("ℹ️ Recalculado con marcador, minuto y tarjetas rojas. No ve ocasiones ni posesión.")
        return cab + "\n".join(lines) + "\n", opciones

    f = _tiempo_restante(ev)
    if cfg["kind"] == "points" and f is not None:
        cab += f"{ev['reloj']} (cuarto {ev['periodo']})\n"
        if r is None:
            lines.append("Sin datos suficientes para recalcular en vivo (solo marcador).")
            return cab + "\n".join(lines) + "\n", opciones
        media = (gl - gv) + r["margen"] * f
        sd = math.sqrt(cfg["sigma"] ** 2 * f + 16)  # +16: parones, faltas y remontadas finales
        p1 = _cdf(media / sd)
        lines.append(f"Gana local {p1:.0%} ({_c(p1)}) · Gana visita {1 - p1:.0%} ({_c(1 - p1)})")
        lines.append(f"Total proyectado: {gl + gv + r['total'] * f:.1f} · fracción de juego restante {f:.0%}")
        opciones = [(f"Gana {ev['local']}", p1), (f"Gana {ev['visita']}", 1 - p1)]
        lines.append("ℹ️ Recalculado con marcador y tiempo restante. No ve lesiones ni faltas.")
        return cab + "\n".join(lines) + "\n", opciones

    if cfg["kind"] == "count" and f is not None and r is not None:  # hockey
        cab += f"{ev['reloj']} (periodo {ev['periodo']})\n"
        lh, la = r["xg_l"] * f, r["xg_v"] * f
        p1 = px = p2 = 0.0
        for i in range(12):
            for j in range(12):
                p = _pois(i, lh) * _pois(j, la)
                if gl + i > gv + j: p1 += p
                elif gl + i == gv + j: px += p
                else: p2 += p
        s = p1 + p2
        p1 = p1 + px * p1 / s if s else 0.5
        lines.append(f"Gana local {p1:.0%} ({_c(p1)}) · Gana visita {1 - p1:.0%} ({_c(1 - p1)})")
        opciones = [(f"Gana {ev['local']}", p1), (f"Gana {ev['visita']}", 1 - p1)]
        return cab + "\n".join(lines) + "\n", opciones

    cab += f"{ev['reloj']} (periodo {ev['periodo']})\n"
    lines.append("Sin modelo en vivo para este deporte: solo se muestra el marcador.")
    return cab + "\n".join(lines) + "\n", opciones


# ----------------------------------------------------------------- reporte
def _cargar(evs, cfg):
    """Tablas y formas de todos los equipos involucrados, en paralelo."""
    paths = sorted({e["path"] for e in evs})
    equipos = sorted({(e["path"], t) for e in evs for t in (e["lid"], e["vid"]) if t})
    soccer = cfg["kind"] == "soccer"
    with ThreadPoolExecutor(12) as ex:
        tablas = dict(zip(paths, ex.map(_tabla, paths)))
        formas = dict(zip(equipos, ex.map(lambda pt: _forma(pt[0], pt[1], soccer), equipos)))
    return tablas, formas


def reporte(deporte, filtro="", maximo=24, solo_vivo=False):
    """Devuelve (lista de dicts {liga, texto, opciones, label}, nota). Primero los partidos en vivo."""
    cfg = DEPORTES[deporte]
    flt = filtro.lower().strip()
    if cfg["kind"] == "market":
        if solo_vivo:
            return [], ""
        vivos_, evs = [], proximos(deporte, filtro=flt)
    else:
        vivos_ = vivos(deporte, flt)
        evs = [] if solo_vivo else proximos(deporte, filtro=flt)
    if not vivos_ and not evs:
        msg = "No hay partidos en vivo ahora." if solo_vivo else (
            "No hay eventos próximos" + (f" para «{filtro}»" if filtro else "") + " en los próximos 10 días.")
        return [], msg
    nota = ""
    if len(evs) > maximo:
        ej = {"futbol": "colombia", "baloncesto": "nba"}.get(deporte, cfg["ligas"][0][1].lower())
        nota = (f"Mostrando los {maximo} primeros de {len(evs)}. "
                f"Filtra por liga o equipo, por ejemplo: /{deporte} {ej}")
        evs = evs[:maximo]
    todos = vivos_ + evs
    tablas, formas = ({}, {}) if cfg["kind"] == "market" else _cargar(todos, cfg)
    out = []
    for ev in vivos_:
        texto, ops = analizar_vivo(ev, deporte, tablas, formas)
        out.append({"liga": "🔴 EN VIVO", "texto": texto, "opciones": ops,
                    "label": f"{ev['local']} vs {ev['visita']} (en vivo)"})
    for ev in evs:
        texto, ops = analizar(ev, deporte, tablas, formas)
        out.append({"liga": ev["liga"], "texto": texto, "opciones": ops,
                    "label": f"{ev['local']} vs {ev['visita']}"})
    return out, nota
