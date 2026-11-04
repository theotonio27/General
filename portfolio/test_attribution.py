"""Checks the attribution maths. Run: python3 -m unittest -v test_attribution"""
import json
import unittest
from pathlib import Path

from attribution import attribute, snaps_in_order

HERE = Path(__file__).resolve().parent

# Daily % column from the 2026-11-02 Trading 212 screenshots.
DAILY = {"IS3Q": -0.04, "EXI2": -0.18, "IS3K": 0.29, "XDWT": -1.02, "AUM5": -0.11, "LYP6": 0.78, "LYMS": -0.36,
         "IS3C": 0.16, "PRAR": 0.36, "EMBH": 0.0, "DBXJ": 0.19, "79U0": 0.26, "QDVY": -1.00, "IBCD": 0.37,
         "LEMA": 0.99, "PRAJ": 0.19, "EUNL": 0.0, "EUNA": 0.60, "LYTR": -1.73, "WELK": 1.20, "XDWI": 1.04,
         "EBUY": 0.07, "EFRW": 0.93, "AMEL": 1.33, "TRDE": 0.05, "LKOR": 1.47, "VGWD": 0.19, "SEC0": -1.08,
         "RARE1": 2.04, "VUAA": -0.12}


class Attribution(unittest.TestCase):
    def setUp(self):
        hold = json.loads((HERE / "holdings.json").read_text())["holdings"]
        now = [{"snap": "2026-11-02-screenshot", "date": "2026-11-02", "kind": "screenshot", "ticker": h["ticker"],
                "name": h["name"], "qty": h["qty"], "price": h["price"], "stale": False} for h in hold]
        # Rebuild the previous close from each asset's daily % so we know the right answer.
        prev = [dict(r, snap="prev", date="2026-10-30", kind="close",
                     price=r["price"] / (1 + DAILY[r["ticker"]] / 100)) for r in now]
        self.rows = prev + now
        self.res = attribute(self.rows, "prev", now[0]["snap"])

    def test_order(self):
        self.assertEqual(snaps_in_order(self.rows), ["prev", "2026-11-02-screenshot"])

    def test_asset_euros_match_daily_pct(self):
        for a in self.res["assets"]:
            value = a["qty"] * a["p_now"]
            d = DAILY[a["ticker"]] / 100
            self.assertAlmostEqual(a["d_eur"], value * d / (1 + d), places=6)
            self.assertAlmostEqual(a["ret"], d, places=9)

    def test_pp_sum_to_total(self):
        self.assertAlmostEqual(sum(a["pp"] for a in self.res["assets"]), self.res["d_pct"] * 100, places=9)
        self.assertAlmostEqual(self.res["v_now"] - self.res["v_base"], self.res["d_eur"], places=6)
        self.assertAlmostEqual(self.res["flows"], 0, places=6)

    def test_flows_not_counted_as_performance(self):
        rows = [dict(r) for r in self.rows]
        for r in rows:
            if r["snap"] != "prev" and r["ticker"] == "VUAA":
                r["qty"] += 10  # bought 10 more
        res = attribute(rows, "prev", "2026-11-02-screenshot")
        self.assertAlmostEqual(res["d_eur"], self.res["d_eur"], places=6)
        self.assertAlmostEqual(res["flows"], 10 * 131.39, places=4)


class LiveOrdering(unittest.TestCase):
    def test_live_sorts_after_close_of_same_and_earlier_days(self):
        rows = [{"snap": "live", "date": "2026-11-04", "kind": "live"},
                {"snap": "2026-11-04", "date": "2026-11-04", "kind": "close"},
                {"snap": "2026-11-03", "date": "2026-11-03", "kind": "close"}]
        self.assertEqual(snaps_in_order(rows), ["2026-11-03", "2026-11-04", "live"])


if __name__ == "__main__":
    unittest.main()
