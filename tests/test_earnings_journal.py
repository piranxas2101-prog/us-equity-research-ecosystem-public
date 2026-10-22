"""Results journal: the reaction window and drift against SPY, results known on the filing day only, findings
found by themselves and proposed to Giannis only past the luck checks."""
from __future__ import annotations

from datetime import date
import json
import sqlite3
import unittest

import pandas as pd

import earnings_journal as ej


def prices(values):
    index = pd.bdate_range("2026-01-05", periods=len(values))
    return pd.DataFrame({"Close": values}, index=index)


class ReactionTests(unittest.TestCase):
    def test_window_is_the_close_before_to_the_session_after_and_drift_follows(self):
        stock = prices([100.0] * 5 + [90.0, 99.0] + [99.0] * 30)   # filed on day 5 (a fall, then half back)
        spy = prices([100.0] * 37)
        filed = stock.index[5].date()
        got = ej.reaction(stock, spy, filed)
        self.assertEqual(got["reaction_pct"], -1.0)                 # 100 -> 99 over the two sessions
        self.assertEqual((got["drift5_pct"], got["drift20_pct"]), (0.0, 0.0))

    def test_a_window_not_stored_yet_stays_unknown(self):
        stock = prices([100.0] * 8)
        got = ej.reaction(stock, prices([100.0] * 8), stock.index[5].date())
        self.assertIsNotNone(got["reaction_pct"])
        self.assertIsNone(got["drift5_pct"])
        self.assertEqual(ej.reaction(None, prices([1.0]), date(2026, 1, 5))["reaction_pct"], None)


class FilingsTests(unittest.TestCase):
    def test_periodic_reports_only_since_the_start(self):
        raw = {"facts": {"us-gaap": {"Revenues": {"units": {"USD": [
            {"form": "10-Q", "accn": "a1", "filed": "2026-04-17", "val": 1.0},
            {"form": "8-K", "accn": "a2", "filed": "2026-04-16", "val": 1.0},
            {"form": "10-K", "accn": "a3", "filed": "2019-02-01", "val": 1.0}]}}}}}
        self.assertEqual(ej.filings(raw, date(2021, 1, 1)), {"a1": {"form": "10-Q", "filed": "2026-04-17"}})


class FindingTests(unittest.TestCase):
    def test_the_journal_finds_a_stable_difference_by_itself_and_proposes_it_to_giannis(self):
        conn = sqlite3.connect(":memory:")
        ej.schema(conn)
        k = 0
        for year in (2022, 2023, 2024, 2025):
            for i in range(60):
                for trend, sign in (("accelerating", 1.0), ("decelerating", -1.0)):
                    k += 1
                    tags = {t: None for t in ej.TAGS} | {"revenue_trend": trend}
                    drift = sign if i % 5 else -sign  # 80% in the tag's direction
                    conn.execute("INSERT INTO earnings_event VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                                 (f"a{k}", "T", "1", "10-Q", f"{year}-05-01", None, "quarterly", "{}",
                                  json.dumps(tags), None, None, drift, None, "t", 0))
        result = ej.findings(conn)
        found = {(f["outcome"], f["value"]) for f in result["findings"]}
        self.assertIn(("drift5", "accelerating"), found)
        items = ej.proposals(result)
        by_agent = {(i["target_agent"], i["criterion_id"]) for i in items}
        key = "drift5:revenue_trend=accelerating:better"
        self.assertIn(("giannis", f"results:{key}"), by_agent)
        self.assertIn(("yankos", f"results_entry:{key}"), by_agent)          # the traders learn the entries
        self.assertIn(("periklis", f"results_entry:{key}"), by_agent)
        self.assertIn(("position_monitor", f"results_hold:{key}"), by_agent)  # the monitors learn the holding

    def test_no_finding_without_enough_events(self):
        conn = sqlite3.connect(":memory:")
        ej.schema(conn)
        self.assertEqual(ej.findings(conn)["findings"], [])


class TagTests(unittest.TestCase):
    def test_buckets_keep_unknown(self):
        tags = ej.event_tags({"tags": {"revenue_trend": "steady", "cash_runway_months": None},
                              "periods": [{"revenue_yoy_pct": 30.0}]})
        self.assertEqual((tags["growth_bucket"], tags["runway_bucket"]), (">=25%", None))


if __name__ == "__main__":
    unittest.main()


class ShareClassTests(unittest.TestCase):
    def test_two_tickers_of_one_company_record_its_report_once(self):
        import gzip
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            facts = root / "facts"
            facts.mkdir()
            raw = {"facts": {"us-gaap": {"Revenues": {"units": {"USD": [
                {"start": "2026-01-01", "end": "2026-03-31", "val": 10.0, "form": "10-Q", "accn": "x1",
                 "filed": "2026-04-20"}]}}}}}
            (facts / "CIK0000000001.json.gz").write_bytes(gzip.compress(json.dumps(raw).encode()))
            tickers = root / "tickers.json"
            tickers.write_text(json.dumps({"fields": ["cik", "name", "ticker", "exchange"],
                                           "data": [[1, "Co", "AAA", "Nasdaq"], [1, "Co", "AAB", "Nasdaq"]]}),
                               encoding="utf-8")
            prices = root / "prices"
            prices.mkdir()
            index = pd.bdate_range("2026-04-01", periods=40)
            for symbol in ("SPY", "AAA", "AAB"):
                pd.DataFrame({"Open": 100.0, "High": 101.0, "Low": 99.0, "Close": 100.0, "Volume": 1e6},
                             index=pd.Index(index, name="Date")).to_csv(prices / f"{symbol}.csv.gz")
            conn = sqlite3.connect(":memory:")
            counts = ej.update(conn, ["AAA", "AAB"], as_of=date(2026, 10, 22), companyfacts=facts,
                               sec_tickers=tickers, price_dir=prices, runs_dir=None)
            self.assertEqual(counts["added"], 1)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM earnings_event").fetchone()[0], 1)
