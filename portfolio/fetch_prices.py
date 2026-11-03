#!/usr/bin/env python3
"""Fetch the last daily close for every holding and append a snapshot.

Usage:
    python3 fetch_prices.py            # fetch, append to snapshots.json, print summary
    python3 fetch_prices.py --dry-run  # fetch and print, write nothing
    python3 fetch_prices.py --resolve  # check/repair Yahoo symbols in holdings.json only

Needs network access to query1/query2.finance.yahoo.com. Standard library only.
Exit codes: 2 more than MAX_MISSING tickers unpriced, 3 Yahoo unreachable,
4 nothing new (the latest close is already stored; snapshots.json untouched).
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
import time
import urllib.parse
import urllib.request
from collections import Counter
from pathlib import Path

from attribution import attribute, snaps_in_order, summary_line

HERE = Path(__file__).resolve().parent
HOLDINGS = HERE / "holdings.json"
SNAPSHOTS = HERE / "snapshots.json"
MAX_MISSING = 3
SUFFIXES = [".DE", ".F", ".MU", ".SG", ".DU", ".PA", ".AS", ".MI", ".BR", ".VI"]
UA = {"User-Agent": "Mozilla/5.0 (portfolio-morning-tracker)"}


def get_json(url, tries=3):
    for i in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=20) as r:
                return json.load(r)
        except Exception:  # noqa: BLE001
            if i == tries - 1:
                raise
            time.sleep(2 ** (i + 1))


def last_close(symbol, on=None):
    """(date, close, currency) of the last completed daily bar (or the bar dated `on`), or None."""
    q = urllib.parse.quote(symbol)
    try:
        j = get_json(f"https://query1.finance.yahoo.com/v8/finance/chart/{q}?range=10d&interval=1d")
    except Exception:  # noqa: BLE001
        return None
    res = (j.get("chart") or {}).get("result") or []
    if not res:
        return None
    r = res[0]
    meta = r.get("meta", {})
    ts = r.get("timestamp") or []
    closes = ((r.get("indicators") or {}).get("quote") or [{}])[0].get("close") or []
    tz = dt.timezone(dt.timedelta(seconds=meta.get("gmtoffset", 0)))
    now = dt.datetime.now(tz)
    session_end = ((meta.get("currentTradingPeriod") or {}).get("regular") or {}).get("end", 0)
    closed_today = now.timestamp() > session_end > 0
    bars = [(dt.datetime.fromtimestamp(t, tz).date(), c) for t, c in zip(ts, closes) if c is not None]
    if on:
        bars = [b for b in bars if b[0].isoformat() == on]
        return (on, float(bars[-1][1]), meta.get("currency")) if bars else None
    # Only completed sessions: skip today's bar while the market is still open.
    done = [b for b in bars if b[0] < now.date() or closed_today]
    if not done:
        return None
    d, c = done[-1]
    return d.isoformat(), float(c), meta.get("currency")


def candidates(h):
    seen = [h["yahoo"]] + [h["ticker"] + s for s in SUFFIXES]
    try:
        j = get_json("https://query2.finance.yahoo.com/v1/finance/search?" + urllib.parse.urlencode({"q": h["ticker"], "quotesCount": 10, "newsCount": 0}))
        seen += [q["symbol"] for q in j.get("quotes", []) if q.get("symbol")]
        j = get_json("https://query2.finance.yahoo.com/v1/finance/search?" + urllib.parse.urlencode({"q": h["name"], "quotesCount": 10, "newsCount": 0}))
        seen += [q["symbol"] for q in j.get("quotes", []) if q.get("symbol")]
    except Exception:  # noqa: BLE001
        pass
    out = []
    for s in seen:
        if s not in out:
            out.append(s)
    return out


def resolve(h, ref_price, tol=0.05):
    """First EUR-quoted symbol whose close is within tol of ref_price."""
    for sym in candidates(h):
        got = last_close(sym)
        if got and got[2] == "EUR" and abs(got[1] / ref_price - 1) <= tol:
            return sym, got
    return None, None


def load(path, default):
    return json.loads(path.read_text()) if path.exists() else default


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--resolve", action="store_true")
    ap.add_argument("--date", help="backfill the close of this YYYY-MM-DD instead of the latest one")
    args = ap.parse_args()

    try:  # fail fast when the network policy blocks Yahoo, instead of retrying every symbol
        get_json("https://query1.finance.yahoo.com/v8/finance/chart/EUNL.DE?range=1d&interval=1d", tries=1)
    except Exception as e:  # noqa: BLE001
        print(f"Cannot reach query1.finance.yahoo.com ({e}). Allow it in the environment's network settings.", file=sys.stderr)
        return 3

    hold = load(HOLDINGS, None)
    snaps = load(SNAPSHOTS, {"rows": []})
    rows = snaps["rows"]
    order = snaps_in_order(rows)
    last = {r["ticker"]: r for r in rows if order and r["snap"] == order[-1]}

    prices, missing, changed = {}, [], False
    for h in hold["holdings"]:
        ref = (last.get(h["ticker"]) or h)["price"]
        got = last_close(h["yahoo"], args.date)
        ok = got and got[2] == "EUR" and abs(got[1] / ref - 1) <= 0.25
        if not args.date and (not ok or args.resolve and not h.get("verified")):
            sym, got2 = resolve(h, ref, tol=0.05 if args.resolve else 0.25)
            if sym:
                if sym != h["yahoo"]:
                    print(f"  {h['ticker']}: {h['yahoo']} -> {sym}", file=sys.stderr)
                h["yahoo"], h["verified"], got, ok, changed = sym, True, got2, True, True
        if ok:
            prices[h["ticker"]] = got
        else:
            missing.append(h["ticker"])
            print(f"  {h['ticker']}: no EUR price for {h['yahoo']}", file=sys.stderr)

    if changed and not args.dry_run:
        HOLDINGS.write_text(json.dumps(hold, indent=2, ensure_ascii=False) + "\n")
    if args.resolve:
        print(json.dumps({"resolved": len(prices), "missing": missing}, indent=2))
        return 0
    if not prices:
        print("No prices fetched; is query1.finance.yahoo.com reachable?", file=sys.stderr)
        return 2

    date = args.date or Counter(p[0] for p in prices.values()).most_common(1)[0][0]
    # Every row must be that day's close: refetch laggards for the exact date, else carry forward as stale.
    for h in hold["holdings"]:
        p = prices.get(h["ticker"])
        if p and p[0] != date:
            p = last_close(h["yahoo"], date)
            if p:
                prices[h["ticker"]] = p
            else:
                prices.pop(h["ticker"])
                missing.append(h["ticker"])
                print(f"  {h['ticker']}: no {date} close on {h['yahoo']}, carried forward", file=sys.stderr)
    prior = [s for s in snaps_in_order(rows) if s < date]
    last = {r["ticker"]: r for r in rows if prior and r["snap"] == prior[-1]} or last
    new = []
    for h in hold["holdings"]:
        p = prices.get(h["ticker"])
        stale = p is None
        price = p[1] if p else (last.get(h["ticker"]) or h)["price"]
        new.append({"snap": date, "date": date, "kind": "close", "ticker": h["ticker"], "name": h["name"],
                    "qty": h["qty"], "price": round(price, 4), "stale": stale})

    rows = [r for r in rows if r["snap"] != date] + new
    order = snaps_in_order(rows)
    out = {"date": date, "missing": missing}
    if len(order) >= 2:
        res = attribute(rows, order[-2], order[-1])
        out.update(base=res["base"], v_now=round(res["v_now"], 2), d_eur=round(res["d_eur"], 2),
                   d_pct=round(res["d_pct"] * 100, 3), summary=summary_line(res))
    key = lambda r: (r["snap"], r["ticker"])
    out["new"] = sorted(rows, key=key) != sorted(snaps["rows"], key=key)
    rows = sorted(rows, key=lambda r: (r["date"], r["ticker"]))
    if not out["new"]:
        print(json.dumps(out, indent=2, ensure_ascii=False))
        print(f"No new close since {date}.", file=sys.stderr)
        return 4
    if not args.dry_run:
        snaps["rows"] = rows
        snaps["updated"] = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
        SNAPSHOTS.write_text(json.dumps(snaps, indent=1, ensure_ascii=False) + "\n")
    print(json.dumps(out, indent=2, ensure_ascii=False))
    return 2 if len(missing) > MAX_MISSING else 0


if __name__ == "__main__":
    sys.exit(main())
