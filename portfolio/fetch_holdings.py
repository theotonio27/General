#!/usr/bin/env python3
"""Fetch the top holdings of each ETF from Yahoo Finance into lookthrough.json.

For each fund we try the symbols in lookthrough_sources.json in order: the fund's
own listing first, then a fund that tracks the same index (the index's top
holdings and weights are the same whichever fund tracks it, and this also gives
the index exposure for swap-based funds). The first symbol with holdings wins and
is recorded as the source.

Yahoo only publishes the top 10 holdings, so `coverage` (sum of weights) tells how
much of each fund is shown.

Needs curl and network access to fc.yahoo.com (login cookie) and query2.finance.yahoo.com.
Usage: python3 fetch_holdings.py [--only IS3Q,EUNL]
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys
import time
import urllib.parse
import subprocess
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
SOURCES = HERE / "lookthrough_sources.json"
OUT = HERE / "lookthrough.json"
UA = "Mozilla/5.0"


class Yahoo:
    """Yahoo rejects Python's TLS client with 429, so requests go through curl."""

    def __init__(self):
        self.jar = tempfile.NamedTemporaryFile(prefix="yjar", delete=False).name
        self._curl("https://fc.yahoo.com/", out=False)  # 404, but sets the A3 cookie
        for i in range(4):  # Yahoo rate-limits crumb requests; back off and retry
            self.crumb = self._curl("https://query2.finance.yahoo.com/v1/test/getcrumb").strip()
            if not self.crumb.startswith("Too Many"):
                break
            time.sleep(15 * (i + 1))
        if not self.crumb or "<" in self.crumb or " " in self.crumb:
            raise RuntimeError(f"no crumb: {self.crumb[:80]!r}")

    def _curl(self, url, out=True):
        cmd = ["curl", "-sS", "-m", "25", "-A", UA, "-b", self.jar, "-c", self.jar, url]
        r = subprocess.run(cmd + ([] if out else ["-o", "/dev/null"]), capture_output=True, text=True)
        if r.returncode:
            raise RuntimeError(r.stderr.strip() or f"curl exit {r.returncode}")
        return r.stdout

    def top_holdings(self, symbol):
        q = urllib.parse.urlencode({"modules": "topHoldings,quoteType", "crumb": self.crumb})
        url = f"https://query2.finance.yahoo.com/v10/finance/quoteSummary/{urllib.parse.quote(symbol)}?{q}"
        for i in range(3):
            txt = self._curl(url)
            if not txt.startswith("Too Many"):
                break
            time.sleep(2 ** (i + 1))
        try:
            j = json.loads(txt)
        except ValueError:
            return None
        res = ((j.get("quoteSummary") or {}).get("result") or [None])[0]
        if not res:
            return None
        th = res.get("topHoldings") or {}
        rows = []
        for h in th.get("holdings") or []:
            w = (h.get("holdingPercent") or {}).get("raw")
            name = (h.get("holdingName") or "")
            if w and not re.search(r"cash fund|money market|cash collateral", name, re.I):
                rows.append({"symbol": (h.get("symbol") or "").strip(), "name": (h.get("holdingName") or "").strip(), "weight": float(w)})
        return {"rows": rows, "longName": (res.get("quoteType") or {}).get("longName")} if rows else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", help="comma-separated fund tickers")
    args = ap.parse_args()
    cfg = json.loads(SOURCES.read_text())
    only = set(args.only.split(",")) if args.only else None
    try:
        y = Yahoo()
    except Exception as e:  # noqa: BLE001
        print(f"Cannot get a Yahoo session ({e}). Allow fc.yahoo.com in the environment's network settings.", file=sys.stderr)
        return 3

    prev = json.loads(OUT.read_text()) if OUT.exists() else {"rows": []}
    keep = [r for r in prev["rows"] if only and r["fund"] not in only]
    rows, report = list(keep), []
    today = dt.date.today().isoformat()
    for f in cfg["funds"]:
        if only and f["ticker"] not in only:
            continue
        got, used = None, None
        for sym in f["try"]:
            try:
                got = y.top_holdings(sym)
            except Exception as e:  # noqa: BLE001
                print(f"  {f['ticker']}: {sym} failed ({e})", file=sys.stderr)
            if got:
                used = sym
                break
        if not got:
            report.append({"fund": f["ticker"], "source": None, "n": 0, "coverage": 0})
            continue
        cov = sum(r["weight"] for r in got["rows"])
        for r in got["rows"]:
            rows.append({"fund": f["ticker"], "kind": f["kind"], "symbol": r["symbol"], "name": r["name"],
                         "weight": round(r["weight"], 6), "source": used,
                         "proxy": used not in f.get("own", []), "asOf": today})
        report.append({"fund": f["ticker"], "source": used, "n": len(got["rows"]), "coverage": round(cov, 4)})
        time.sleep(0.5)

    OUT.write_text(json.dumps({"note": "Top holdings per fund from Yahoo Finance. weight is the share of the fund (0-1). "
                                       "proxy=true means taken from another fund tracking the same index.",
                               "updated": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
                               "rows": rows}, indent=1, ensure_ascii=False) + "\n")
    print(json.dumps(report, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
