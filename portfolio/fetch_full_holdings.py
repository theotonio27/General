#!/usr/bin/env python3
"""Fetch every holding of every ETF from the fund providers into lookthrough.json.

Sources (see fund_sources.json):
  ishares  https://www.ishares.com/uk/individual/en/products/<id>/x/latest-holdings.csv
  amundi   POST https://www.amundietf.com/mapi/ProductAPI/getProductsData (composition by ISIN)
  dws      https://etf.dws.com/etfdata/export/GBR/ENG/excel/product/constituent/<ISIN>/ (xlsx)
  yahoo    top-10 fallback (fetch_holdings.Yahoo) where the provider's site is blocked

Output rows (one per fund and company, or fund and bond issuer):
  {fund, kind: equity|bond, key, symbol (Yahoo), name, weight (0-1), country, sector, source, proxy}
Equities are keyed by Yahoo symbol, so a company merges across funds and providers.
Bond lines are grouped per fund by issuer (governments by country).
Cash, money market, futures and FX lines are left out.

Usage: python3 fetch_full_holdings.py [--only EUNL,PRAR]
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import io
import json
import re
import subprocess
import sys
import zipfile
import xml.etree.ElementTree as ET
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
SOURCES, OUT = HERE / "fund_sources.json", HERE / "lookthrough.json"
UA = "Mozilla/5.0"

# Exchange name (iShares / DWS) -> Yahoo suffix. Matched by substring, first hit wins.
EXCHANGES = [
    ("euronext amsterdam", ".AS"), ("euronext paris", ".PA"), ("euronext brussels", ".BR"), ("euronext lisbon", ".LS"),
    ("euronext dublin", ".IR"), ("oslo", ".OL"), ("stockholm", ".ST"), ("copenhagen", ".CO"), ("helsinki", ".HE"),
    ("iceland", ".IC"), ("nasdaq", ""), ("new york", ""), ("nyse mkt", ""), ("nyse arca", ""), ("cboe", ""), ("nyse", ""),
    ("tokyo", ".T"), ("korea exchange (kosdaq", ".KQ"), ("korea", ".KS"), ("taiwan", ".TW"), ("gretai", ".TWO"),
    ("hong kong", ".HK"), ("shanghai", ".SS"), ("shenzhen", ".SZ"), ("national stock exchange of india", ".NS"),
    ("bse ltd", ".BO"), ("london", ".L"), ("xetra", ".DE"), ("deutsche b", ".DE"), ("euronext amsterdam", ".AS"),
    ("euronext paris", ".PA"), ("euronext brussels", ".BR"), ("euronext lisbon", ".LS"), ("euronext dublin", ".IR"),
    ("irish", ".IR"), ("six swiss", ".SW"), ("swiss", ".SW"), ("borsa italiana", ".MI"), ("italian", ".MI"),
    ("madrid", ".MC"), ("bolsa de madrid", ".MC"), ("toronto", ".TO"), ("asx", ".AX"), ("australian", ".AX"),
    ("stockholm", ".ST"), ("copenhagen", ".CO"), ("helsinki", ".HE"), ("oslo", ".OL"), ("wiener", ".VI"), ("vienna", ".VI"),
    ("johannesburg", ".JO"), ("b3", ".SA"), ("bovespa", ".SA"), ("mexic", ".MX"), ("singapore", ".SI"),
    ("saudi", ".SR"), ("indonesia", ".JK"), ("malaysia", ".KL"), ("thailand", ".BK"), ("philippine", ".PS"),
    ("santiago", ".SN"), ("istanbul", ".IS"), ("warsaw", ".WA"), ("tel aviv", ".TA"), ("qatar", ".QA"),
    ("abu dhabi", ".AE"), ("dubai", ".AE"), ("kuwait", ".KW"), ("new zealand", ".NZ"), ("athens", ".AT"),
    ("budapest", ".BD"), ("prague", ".PR"), ("colombia", ".CL"), ("lima", ".LM"),
]
# Bloomberg exchange code -> Yahoo suffix (Amundi's "AAPL UW").
BBG = {"UW": "", "UN": "", "UQ": "", "UR": "", "UA": "", "UF": "", "UP": "", "US": "", "UV": "",
       "LN": ".L", "FP": ".PA", "GY": ".DE", "GR": ".DE", "NA": ".AS", "SW": ".SW", "SE": ".SW", "VX": ".SW",
       "IM": ".MI", "SM": ".MC", "SQ": ".MC", "BB": ".BR", "ID": ".IR", "AV": ".VI", "PL": ".LS", "SS": ".ST",
       "DC": ".CO", "FH": ".HE", "NO": ".OL", "JT": ".T", "JP": ".T", "KS": ".KS", "KP": ".KS", "KQ": ".KQ",
       "TT": ".TW", "HK": ".HK", "AU": ".AX", "AT": ".AX", "CN": ".TO", "CT": ".TO", "IN": ".NS", "IS": ".NS",
       "IB": ".NS", "SJ": ".JO", "BZ": ".SA", "MM": ".MX", "SP": ".SI", "AB": ".SR", "IJ": ".JK", "MK": ".KL",
       "TB": ".BK", "PM": ".PS", "CI": ".SN", "TI": ".IS", "PW": ".WA", "IT": ".TA", "QD": ".QA", "NZ": ".NZ"}
ALIASES = {"GOOG": "GOOGL", "FOX": "FOXA", "NWS": "NWSA", "005935.KS": "005930.KS", "NDA.HE": "NDA-FI.HE",
           "ATCO-A": "ATCO-A.ST", "ATCO-B": "ATCO-B.ST"}
GOV_SECTORS = ("treasur", "government", "sovereign", "governm")
SKIP_CLASSES = ("money market", "cash", "futures", "fx", "derivative", "swap", "forward")


def curl(url, data=None):
    cmd = ["curl", "-sS", "--compressed", "-m", "120", "-A", UA, "-L", url]
    if data is not None:
        cmd += ["-H", "Content-Type: application/json", "-X", "POST", "-d", json.dumps(data)]
    r = subprocess.run(cmd, capture_output=True)
    if r.returncode:
        raise RuntimeError(r.stderr.decode(errors="replace").strip())
    return r.stdout


def norm_name(name):
    s = name.lower()
    s = re.sub(r"\b(class [a-z]|ordinary shares|units?|adr|ads|non[- ]voting|registered shares|shs|reg|pref)\b.*$", "", s)
    s = re.sub(r"[^a-z0-9 ]", " ", s)
    s = re.sub(r"\b(inc|corp|corporation|co|ltd|limited|plc|sa|ag|nv|se|holdings?|group|the|de|cv|sab|asa|ab|oyj|spa|as|bhd|tbk|pcl|kk)\b", " ", s)
    return re.sub(r"\s+", " ", s).strip()


NORDIC = (".ST", ".CO", ".HE", ".OL")


def canon(t, suf):
    """Yahoo's spelling of a ticker on an exchange."""
    t = t.strip().replace("*", "")
    if suf == "":
        t = {"BRKB": "BRK-B", "BFB": "BF-B", "BFA": "BF-A"}.get(t, t.replace(".", "-").replace("/", "-").replace(" ", "-"))
    elif suf == ".HK":
        t = t.lstrip("0").zfill(4)
    elif suf == ".L":
        t = t.rstrip(".").rstrip("/").replace(".", "-").replace("/", "-").replace(" ", "-")
    elif suf == ".TO":
        t = t.replace(".", "-").replace(" ", "-")
    elif suf in NORDIC:
        t = t.replace(" ", "-")
        if "-" not in t and len(t) >= 4 and t[-1] in "AB" and t not in ("SAAB", "KNEBV", "GMAB", "PNDORA", "NOKIA", "ORSTED"):
            t = t[:-1] + "-" + t[-1]
    elif suf in (".SS", ".SZ") and t.isdigit():
        suf = ".SS" if t.startswith("6") else ".SZ"
    return ALIASES.get(t + suf, t + suf)


def yahoo_from_exchange(ticker, exchange):
    if not ticker or ticker == "-":
        return None
    ex = (exchange or "").lower()
    suf = next((s for k, s in EXCHANGES if k in ex), None)
    if suf is None:
        return None
    return canon(ticker, suf)


def yahoo_from_bbg(bbg):
    if not bbg or " " not in bbg:
        return None
    t, code = bbg.rsplit(" ", 1)
    suf = BBG.get(code)
    return None if suf is None else canon(t, suf)


def bond_issuer(name, sector, country, ticker=None):
    """(key, display name) for a bond line."""
    if any(g in (sector or "").lower() for g in GOV_SECTORS):
        return "gov:" + (country or "?"), f"{country} (government)"
    base = re.sub(r"\s+\d+(\.\d+)?\s*%.*$", "", name or "").strip()
    base = re.sub(r"\s+\d{1,2}[A-Za-z]{3}\d{2,4}.*$", "", base).strip() or (name or "?")
    if ticker and ticker not in ("-", ""):
        return "bond:" + ticker, base
    return "bond:" + norm_name(base), base


GERMAN = {"Emittententicker": "Ticker", "Name": "Name", "Sektor": "Sector", "Anlageklasse": "Asset Class",
          "Gewichtung (%)": "Weight (%)", "Standort": "Location", "Börse": "Exchange"}
GERMAN_CLASSES = {"aktien": "equity", "anleihen": "fixed income", "geldmarkt": "money market", "bar": "cash"}


def ishares(f):
    """Full holdings CSV. The UK site (English) has most funds; German-domiciled ones (e.g. EXI2) only the DE site."""
    site = f.get("site", "uk")
    url = (f"https://www.ishares.com/de/privatanleger/de/produkte/{f['id']}/x/latest-holdings.csv" if site == "de"
           else f"https://www.ishares.com/uk/individual/en/products/{f['id']}/x/latest-holdings.csv")
    text = curl(url).decode("utf-8-sig", "replace")
    lines = text.splitlines()
    start = next((i for i, l in enumerate(lines) if l.startswith(("Ticker,", "Emittententicker,"))), None)
    if start is None:
        raise RuntimeError(text[:120].strip() or "empty response")
    german = lines[start].startswith("Emittententicker")
    rows = []

    def num(v):
        v = (v or "").strip()
        v = v.replace(".", "").replace(",", ".") if german else v.replace(",", "")
        try:
            return float(v)
        except ValueError:
            return 0.0
    for r in csv.DictReader(io.StringIO("\n".join(lines[start:]))):
        if german:
            r = {GERMAN.get(k, k): v for k, v in r.items()}
            r["Asset Class"] = GERMAN_CLASSES.get((r.get("Asset Class") or "").lower(), r.get("Asset Class"))
        if not r.get("Name") or r.get("Weight (%)") in (None, ""):
            continue
        cls = (r.get("Asset Class") or "").lower()
        if any(s in cls for s in SKIP_CLASSES) or "cash" in (r.get("Sector") or "").lower():
            continue
        w = num(r["Weight (%)"]) / 100
        if w <= 0:
            continue
        rows.append({"ticker": r.get("Ticker"), "name": r["Name"], "weight": w, "country": r.get("Location"),
                     "sector": r.get("Sector"), "symbol": yahoo_from_exchange(r.get("Ticker"), r.get("Exchange")),
                     "bond": "fixed income" in cls or "bond" in cls})
    return rows


def amundi(f):
    body = {"context": {"countryCode": "DEU", "languageCode": "en", "userProfileName": "INSTIT"}, "productIds": [f["id"]],
            "productType": "PRODUCT", "composition": {"compositionFields": ["type", "bbg", "isin", "name", "weight", "sector", "countryOfRisk", "currency"]}}
    comp = ((json.loads(curl("https://www.amundietf.com/mapi/ProductAPI/getProductsData", body))["products"][0].get("composition") or {})
            .get("compositionData") or [])
    rows = []
    for c in comp:
        ch = c["compositionCharacteristics"]
        typ = (ch.get("type") or "").upper()
        if not ch.get("name") or typ in ("CASH", "FUTURE", "FUTURES", "FX_FORWARD", "FORWARD", "SWAP", "MONEY_MARKET") or not c.get("weight"):
            continue
        rows.append({"ticker": (ch.get("bbg") or "").split(" ")[0], "name": ch["name"], "weight": float(c["weight"]),
                     "country": ch.get("countryOfRisk"), "sector": ch.get("sector") if typ != "GOVERNMENT" else "Government",
                     "symbol": yahoo_from_bbg(ch.get("bbg")) if typ.startswith("EQUITY") else None,
                     "isin": ch.get("isin"), "bond": not typ.startswith("EQUITY")})
    return rows


def xlsx_rows(data):
    M = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
    z = zipfile.ZipFile(io.BytesIO(data))
    names = z.namelist()
    ss_name = next((n for n in names if n.endswith("sharedStrings.xml")), None)
    ss = [''.join(t.text or '' for t in si.iter(M + "t")) for si in ET.fromstring(z.read(ss_name)).findall(M + "si")] if ss_name else []
    sheet = next(n for n in names if "/worksheets/" in n and n.endswith(".xml"))
    out = []
    for r in ET.fromstring(z.read(sheet)).iter(M + "row"):
        vals = []
        for c in r.findall(M + "c"):
            v = c.find(M + "v")
            if v is None:
                i = c.find(M + "is")
                vals.append(''.join(x.text or '' for x in i.iter(M + "t")) if i is not None else '')
            else:
                vals.append(ss[int(v.text)] if c.get("t") == "s" else v.text)
        out.append(vals)
    return out


def dws(f):
    table = xlsx_rows(curl(f"https://etf.dws.com/etfdata/export/GBR/ENG/excel/product/constituent/{f['id']}/"))
    head = next(i for i, r in enumerate(table) if r[:2] == ["Name", "ISIN"])
    rows = []
    for r in table[head + 1:]:
        if len(r) < 11 or not r[1]:
            continue
        _, name, isin, country, _ccy, exchange, typ, _rating, _listing, sector, weight = r[:11]
        if typ.lower() not in ("equities", "equity") or not weight:
            continue
        rows.append({"ticker": None, "name": name, "weight": float(weight), "country": country, "sector": sector,
                     "symbol": None, "isin": isin, "exchange": exchange, "bond": False})
    return rows


def yahoo(f):
    from fetch_holdings import Yahoo  # top-10 fallback
    y = Yahoo()
    for sym in f["try"]:
        got = y.top_holdings(sym)
        if got:
            return [{"ticker": r["symbol"], "name": r["name"], "weight": r["weight"], "country": None, "sector": None,
                     "symbol": ALIASES.get(r["symbol"], r["symbol"]) or None, "bond": False} for r in got["rows"]]
    return []


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only")
    args = ap.parse_args()
    cfg = json.loads(SOURCES.read_text())["funds"]
    only = set(args.only.split(",")) if args.only else None
    prev = json.loads(OUT.read_text())["rows"] if OUT.exists() else []

    raw, report, cache = {}, [], {}
    for f in cfg:
        if only and f["ticker"] not in only:
            continue
        if f["provider"] == "none":
            raw[f["ticker"]] = []
            report.append({"fund": f["ticker"], "n": 0, "sum": 0, "source": f["label"]})
            continue
        ck = (f["provider"], f.get("id") or tuple(f.get("try", [])))
        try:
            if ck not in cache:
                cache[ck] = {"ishares": ishares, "amundi": amundi, "dws": dws, "yahoo": yahoo}[f["provider"]](f)
            raw[f["ticker"]] = cache[ck]
        except Exception as e:  # noqa: BLE001
            print(f"  {f['ticker']}: {f['provider']} failed: {e}", file=sys.stderr)
            continue
        rows = raw[f["ticker"]]
        report.append({"fund": f["ticker"], "n": len(rows), "sum": round(sum(r["weight"] for r in rows), 4), "source": f["label"]})

    # Give DWS rows (ISIN only) a Yahoo symbol: by ISIN from Amundi rows, else by name and country.
    by_isin, by_name = {}, {}
    for rows in raw.values():
        for r in rows:
            if r.get("symbol") and not r["bond"]:
                if r.get("isin"):
                    by_isin.setdefault(r["isin"], r["symbol"])
                by_name.setdefault((norm_name(r["name"]), (r.get("country") or "").lower()), r["symbol"])
    # Name alone, when only one symbol carries that name (country names differ between providers).
    seen = defaultdict(set)
    for (n, _c), sym in by_name.items():
        seen[n].add(sym)
    by_name_only = {n: next(iter(v)) for n, v in seen.items() if len(v) == 1}
    unmatched = 0
    for rows in raw.values():
        for r in rows:
            if not r["bond"] and not r.get("symbol"):
                n = norm_name(r["name"])
                r["symbol"] = by_isin.get(r.get("isin")) or by_name.get((n, (r.get("country") or "").lower())) or by_name_only.get(n)
                if not r["symbol"] and r.get("exchange") and r.get("isin"):
                    unmatched += 1

    # Aggregate per fund and key.
    meta = {f["ticker"]: f for f in cfg}
    out, names = [], defaultdict(list)
    for fund, rows in raw.items():
        f = meta[fund]
        agg = {}
        for r in rows:
            if r["bond"] or f["kind"] == "bond":
                key, disp = bond_issuer(r["name"], r.get("sector"), r.get("country"), r.get("ticker") if f["provider"] == "ishares" else None)
                kind = "bond"
            else:
                key = r.get("symbol") or ("name:" + norm_name(r["name"]))
                disp, kind = r["name"], "equity"
            a = agg.setdefault(key, {"fund": fund, "kind": kind, "key": key, "symbol": r.get("symbol") if kind == "equity" else None,
                                     "name": disp, "weight": 0.0, "country": r.get("country"), "sector": r.get("sector"),
                                     "source": f["label"], "proxy": bool(f.get("proxy"))})
            a["weight"] += r["weight"]
            names[key].append(disp)
        out.extend(agg.values())
    # One display name per key: prefer mixed case (e.g. "Apple Inc") over provider capitals.
    for r in out:
        cands = names[r["key"]]
        mixed = [n for n in cands if n != n.upper()]
        r["name"] = min(mixed, key=len) if mixed else min(cands, key=len).title()
        if r["kind"] == "equity":
            r["name"] = re.sub(r"\s+(Class|Cl)\s+[A-Z]\b.*$", "", r["name"], flags=re.I).strip()
        r["weight"] = round(r["weight"], 7)
    if only:
        out = [r for r in prev if r["fund"] not in only] + out

    OUT.write_text(json.dumps({"note": "Every holding of each ETF from the fund providers (bond lines grouped by issuer). weight is the share of the fund (0-1).",
                               "updated": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
                               "funds": {f["ticker"]: {"source": f["label"], "proxy": bool(f.get("proxy")), "why": f.get("why"), "kind": f["kind"]} for f in cfg},
                               "rows": out}, separators=(",", ":"), ensure_ascii=False) + "\n")
    print(json.dumps({"report": report, "rows": len(out), "bytes": OUT.stat().st_size, "dws_unmatched": unmatched}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
