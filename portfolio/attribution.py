"""Portfolio change attribution between two snapshots.

Rows are the long-format snapshot rows stored in snapshots.json:
    {snap, date, kind, ticker, name, qty, price, stale}

Price effect uses the quantities held at the baseline (what you owned over the
period). Quantity changes since then are reported separately as flows, so a
purchase never shows up as performance.

    d_eur_i = q_i,base * (p_i,now - p_i,base)
    v_base  = sum(q_i,base * p_i,base)
    d_pct   = sum(d_eur_i) / v_base
    pp_i    = d_eur_i / v_base * 100      (sums exactly to d_pct * 100)
    flows   = v_now - v_base - sum(d_eur_i)
"""
from __future__ import annotations


def snaps_in_order(rows):
    """Snapshot ids, oldest first (a same-day screenshot sorts before that day's close)."""
    seen = {}
    for r in rows:
        seen.setdefault(r["snap"], (r["date"], 0 if r["kind"] == "screenshot" else 1))
    return [s for s, _ in sorted(seen.items(), key=lambda kv: kv[1])]


def attribute(rows, base, now):
    b = {r["ticker"]: r for r in rows if r["snap"] == base}
    n = {r["ticker"]: r for r in rows if r["snap"] == now}
    v_base = sum(r["qty"] * r["price"] for r in b.values())
    v_now = sum(r["qty"] * r["price"] for r in n.values())
    assets = []
    for t in sorted(set(b) | set(n)):
        rb, rn = b.get(t), n.get(t)
        q = rb["qty"] if rb else 0.0
        pb = rb["price"] if rb else None
        pn = rn["price"] if rn else None
        d_eur = q * (pn - pb) if (pb is not None and pn is not None) else 0.0
        assets.append({
            "ticker": t,
            "name": (rn or rb)["name"],
            "qty": q,
            "p_base": pb,
            "p_now": pn,
            "ret": (pn / pb - 1) if (pb and pn is not None) else None,
            "d_eur": d_eur,
            "pp": d_eur / v_base * 100 if v_base else 0.0,
            "weight": (rn["qty"] * rn["price"] / v_now) if (rn and v_now) else 0.0,
            "stale": bool(rn and rn.get("stale")),
        })
    d_eur = sum(a["d_eur"] for a in assets)
    return {
        "base": base,
        "now": now,
        "v_base": v_base,
        "v_now": v_now,
        "d_eur": d_eur,
        "d_pct": d_eur / v_base if v_base else 0.0,
        "flows": v_now - v_base - d_eur,
        "assets": sorted(assets, key=lambda a: a["d_eur"]),
    }


def summary_line(res, top=3):
    """One-line text for the morning push notification."""
    a = res["assets"]
    up = [x for x in reversed(a) if x["d_eur"] > 0][:top]
    down = [x for x in a if x["d_eur"] < 0][:top]
    fmt = lambda x: f"{x['ticker']} {x['d_eur']:+,.0f}€ ({x['pp']:+.2f}pp)"
    parts = [f"Portfolio {res['d_eur']:+,.2f}€ ({res['d_pct'] * 100:+.2f}%) since {res['base']}, now €{res['v_now']:,.0f}."]
    if up:
        parts.append("Up: " + ", ".join(map(fmt, up)) + ".")
    if down:
        parts.append("Down: " + ", ".join(map(fmt, down)) + ".")
    if abs(res["flows"]) >= 1:
        parts.append(f"Flows {res['flows']:+,.0f}€ (buys/sells, not performance).")
    return " ".join(parts)
