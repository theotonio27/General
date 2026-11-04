#!/usr/bin/env python3
"""EUR closing prices of every company in lookthrough.json, for each snapshot date.

For each date in snapshots.json, a company's price is the last price traded by the
Xetra close (17:30 Frankfurt) that day, the moment your ETFs' closing prices are set.
US stocks are mid-session then, Asian markets are already closed (so their own
close), and holidays fall back to the previous session. It is converted to EUR at the
EUR/<currency> rate at the same moment. 30-minute bars from Yahoo; dates inside the
last month are recomputed on every run.

Writes company_prices.json: {rows: [{date, symbol, currency, close, eur}]}.
Usage: python3 fetch_company_prices.py
"""
from __future__ import annotations

import datetime as dt
import json
import sys
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from zoneinfo import ZoneInfo

HERE = Path(__file__).resolve().parent
LOOK, SNAPS, OUT = HERE / "lookthrough.json", HERE / "snapshots.json", HERE / "company_prices.json"
UA = {"User-Agent": "Mozilla/5.0"}
TOP_N = 600
SUBUNITS = {"GBp": ("GBP", 100), "GBX": ("GBP", 100), "ZAc": ("ZAR", 100), "ILA": ("ILS", 100)}


def series(symbol):
    """(currency, [(unix_ts_of_bar_end, close)]) from 30-minute bars over the last month, or None."""
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{urllib.parse.quote(symbol)}?range=1mo&interval=30m"
    for i in range(3):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=20) as r:
                res = json.load(r)["chart"]["result"][0]
            break
        except Exception:  # noqa: BLE001
            if i == 2:
                return None
            time.sleep(2 ** (i + 1))
    m = res["meta"]
    closes = (res.get("indicators", {}).get("quote") or [{}])[0].get("close") or []
    bars = [(t + 1800, c) for t, c in zip(res.get("timestamp") or [], closes) if c is not None]
    if not bars and m.get("regularMarketPrice") and m.get("regularMarketTime"):
        bars = [(m["regularMarketTime"], m["regularMarketPrice"])]
    return m.get("currency"), bars


def xetra_close(date):
    """17:30 Frankfurt time on `date`: when the ETF closing prices are set."""
    return int(dt.datetime.combine(dt.date.fromisoformat(date), dt.time(17, 30), ZoneInfo("Europe/Berlin")).timestamp())


LIVE_DATES = set()  # dates of a "live" snapshot: priced now instead of at the Xetra close


def on_or_before(bars, date):
    """Last price traded by the Xetra close on `date` (or by now, for live prices)."""
    cut, best = (int(time.time()) if date in LIVE_DATES else xetra_close(date)), None
    for t, c in bars:
        if t <= cut:
            best = c
    return best


def main():
    look = json.loads(LOOK.read_text())["rows"]
    snap_rows = json.loads(SNAPS.read_text())["rows"]
    dates = sorted({r["date"] for r in snap_rows})
    LIVE_DATES.update(r["date"] for r in snap_rows if r["kind"] == "live" and
                      not any(c["date"] == r["date"] and c["kind"] == "close" for c in snap_rows))
    # Only the companies that make up most of your exposure: the rest of the move stays in "Everything else".
    snaps = json.loads(SNAPS.read_text())["rows"]
    last = max(r["date"] for r in snaps)
    pos = {r["ticker"]: r["qty"] * r["price"] for r in snaps if r["date"] == last}
    exp = {}
    for r in look:
        if r.get("kind", "equity") == "equity" and r.get("symbol"):
            exp[r["symbol"]] = exp.get(r["symbol"], 0) + pos.get(r["fund"], 0) * r["weight"]
    syms = sorted(sorted(exp, key=exp.get, reverse=True)[:TOP_N])
    window = (dt.date.today() - dt.timedelta(days=28)).isoformat()
    todo = [d for d in dates if d >= window]

    def with_fallback(sym):
        """Bare numeric codes (e.g. 005935, 00939) lack an exchange suffix: try the usual ones."""
        g = series(sym)
        if g is None and sym.isdigit():
            for alt in (sym + ".KS", sym.lstrip("0").zfill(4) + ".HK", sym + ".T", sym + ".TW", sym + ".SS", sym + ".SZ"):
                g = series(alt)
                if g:
                    break
        return g

    with ThreadPoolExecutor(6) as ex:
        got = dict(zip(syms, ex.map(with_fallback, syms)))
    ccys = {SUBUNITS.get(g[0], (g[0], 1))[0] for g in got.values() if g and g[0]} - {"EUR"}
    with ThreadPoolExecutor(6) as ex:
        fx = dict(zip(ccys, ex.map(lambda c: series(f"EUR{c}=X"), ccys)))

    prev = json.loads(OUT.read_text())["rows"] if OUT.exists() else []
    rows = [r for r in prev if r["date"] not in todo]
    missing = []
    for s in syms:
        g = got.get(s)
        if not g or not g[0]:
            missing.append(s)
            continue
        ccy, div = SUBUNITS.get(g[0], (g[0], 1))
        for d in todo:
            c = on_or_before(g[1], d)
            rate = 1.0 if ccy == "EUR" else on_or_before((fx.get(ccy) or (None, []))[1], d)
            if c is None or not rate:
                continue
            rows.append({"date": d, "symbol": s, "currency": g[0], "close": round(c, 6), "eur": round(c / div / rate, 6)})
    rows.sort(key=lambda r: (r["date"], r["symbol"]))
    OUT.write_text(json.dumps({"note": "Company prices in EUR at each snapshot date\u2019s Xetra close (17:30 Frankfurt), converted at Yahoo FX at the same moment.",
                               "updated": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
                               "rows": rows}, indent=0, ensure_ascii=False) + "\n")
    print(json.dumps({"dates": todo, "symbols": len(syms), "priced": len(syms) - len(missing), "missing": missing,
                      "currencies": sorted(ccys)}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
