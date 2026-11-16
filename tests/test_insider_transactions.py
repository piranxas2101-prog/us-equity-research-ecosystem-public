"""Form 4 insider trades (owner approved 2026-11-16): parsing, point in time, coverage, unknown stays unknown."""
from __future__ import annotations

from datetime import date
import io
import json
from pathlib import Path
import tempfile
import unittest
import zipfile

import fundamental_lab as fl
import outcome_evaluator as oe
import insider_transactions as it


def data_set(trades, forms=None) -> bytes:
    """A quarter's data set in the SEC's layout: SUBMISSION, REPORTINGOWNER and NONDERIV_TRANS tab files."""
    sub = ["ACCESSION_NUMBER\tFILING_DATE\tPERIOD_OF_REPORT\tDOCUMENT_TYPE\tISSUERCIK\tISSUERNAME\tISSUERTRADINGSYMBOL"]
    own = ["ACCESSION_NUMBER\tRPTOWNERCIK\tRPTOWNERNAME\tRPTOWNER_RELATIONSHIP"]
    trans = ["ACCESSION_NUMBER\tNONDERIV_TRANS_SK\tTRANS_DATE\tTRANS_CODE\tTRANS_SHARES\tTRANS_PRICEPERSHARE\t"
             "TRANS_ACQUIRED_DISP_CD"]
    for k, (cik, filed, owner, code, shares, price) in enumerate(trades):
        acc = f"0000-{k}"
        form = (forms or {}).get(k, "4")
        sub.append(f"{acc}\t{filed}\t{filed}\t{form}\t{cik}\tCo\tCO")
        own.append(f"{acc}\t{owner}\tName\tDirector")
        trans.append(f"{acc}\t{k}\t{filed}\t{code}\t{shares}\t{price}\t{'A' if code == 'P' else 'D'}")
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, lines in (("SUBMISSION.tsv", sub), ("REPORTINGOWNER.tsv", own), ("NONDERIV_TRANS.tsv", trans)):
            archive.writestr(name, "\n".join(lines) + "\n")
    return buffer.getvalue()


class ParseTests(unittest.TestCase):
    def test_only_open_market_trades_of_form_4_with_size_and_price(self):
        rows = it.parse_quarter(data_set([
            (320193, "15-JAN-2024", "o1", "P", "1000", "10.5"),
            (320193, "16-JAN-2024", "o2", "S", "200", "11"),
            (320193, "17-JAN-2024", "o3", "A", "5000", "0"),     # a grant: not a decision to trade
            (320193, "18-JAN-2024", "o4", "P", "", "10"),        # no size: cannot be weighed, not zero dollars
            (320193, "19-JAN-2024", "o5", "P", "100", "10"),     # a Form 3 below: not a Form 4
        ], forms={4: "3"}))
        self.assertEqual([(r["owner"], r["code"], r["usd"], r["filed"]) for r in rows],
                         [("o1", "P", 10500.0, "2024-01-15"), ("o2", "S", 2200.0, "2024-01-16")])

    def test_quarters_are_the_finished_ones(self):
        self.assertEqual(it.quarters("2025q3", date(2026, 1, 5)), ["2025q3", "2025q4"])
        self.assertEqual(it.quarter_bounds("2024q4"), (date(2024, 10, 1), date(2024, 12, 31)))


class FactorTests(unittest.TestCase):
    COVER = ["2023-01-01", "2024-12-31"]
    TRADES = [["2024-03-01", "a", "P", 100000.0], ["2024-05-01", "b", "P", 50000.0], ["2024-05-02", "b", "P", 1.0],
              ["2024-05-20", "c", "S", 50000.0], ["2024-07-01", "d", "P", 999.0]]

    def test_buyers_and_net_ratio_point_in_time(self):
        f = it.factors(self.TRADES, date(2024, 6, 1), self.COVER)
        # b bought twice in the 90 days (one insider); a's buy is older than 90 days; d filed later and is not seen
        self.assertEqual(f["insider_buyers_90d"], 1)
        self.assertEqual(it.factors(self.TRADES, date(2024, 5, 25), self.COVER)["insider_buyers_90d"], 2)
        # filings counted once per insider and day: buys a, b (twice, two days) = 3, sells c = 1; dollars do not weigh
        self.assertAlmostEqual(f["insider_net_buy_180d"], (3 - 1) / 4)

    def test_one_absurd_price_does_not_decide_the_ratio(self):
        trades = [["2024-05-01", "a", "P", 7e15], ["2024-05-02", "b", "S", 1e5], ["2024-05-03", "c", "S", 1e5],
                  ["2024-05-03", "c", "S", 2e5]]  # c's two lines of one filing are one sale
        self.assertAlmostEqual(it.factors(trades, date(2024, 6, 1), self.COVER)["insider_net_buy_180d"], -1 / 3)

    def test_no_trades_inside_coverage_is_a_known_zero_and_no_ratio(self):
        f = it.factors([], date(2024, 6, 1), self.COVER)
        self.assertEqual((f["insider_buyers_90d"], f["insider_net_buy_180d"]), (0, None))

    def test_outside_coverage_is_unknown(self):
        for day in (date(2023, 3, 1), date(2025, 2, 1)):  # under 180 days of history; after the last quarter
            self.assertTrue(all(v is None for v in it.factors(self.TRADES, day, self.COVER).values()))
        self.assertIsNone(it.factors(self.TRADES, date(2024, 6, 1), None)["insider_buyers_90d"])


class OpportunisticTests(unittest.TestCase):
    COVER = ["2018-01-01", "2024-12-31"]
    # r trades every March (routine); o trades in three years but never the same month (opportunistic);
    # n has only two years of history (not classified)
    HISTORY = [["2021-03-10", "r", "S", 1.0], ["2022-03-11", "r", "S", 1.0], ["2023-03-09", "r", "S", 1.0],
               ["2021-02-01", "o", "S", 1.0], ["2022-06-01", "o", "P", 1.0], ["2023-11-01", "o", "S", 1.0],
               ["2022-05-01", "n", "P", 1.0], ["2023-05-01", "n", "P", 1.0]]

    def test_routine_opportunistic_and_unclassified(self):
        self.assertEqual(it.classify(self.HISTORY, 2024), {"r": "routine", "o": "opportunistic"})

    def test_only_opportunistic_insiders_count(self):
        trades = self.HISTORY + [["2024-03-12", "r", "P", 1.0], ["2024-04-01", "o", "P", 1.0],
                                 ["2024-04-02", "n", "P", 1.0], ["2024-04-03", "o", "S", 1.0]]
        f = it.factors(trades, date(2024, 5, 1), self.COVER)
        self.assertEqual((f["insider_opp_buyers_90d"], f["insider_opp_sellers_90d"]), (1, 1))
        self.assertEqual(f["insider_buyers_90d"], 3)  # the plain count sees all three buyers

    def test_without_three_full_years_the_opportunistic_factors_are_unknown(self):
        f = it.factors(self.HISTORY, date(2020, 6, 1), self.COVER)  # 2017 is not covered
        self.assertIsNone(f["insider_opp_buyers_90d"])
        self.assertEqual(f["insider_buyers_90d"], 0)

    def test_the_lab_compares_has_one_with_has_none(self):
        rows = []
        for month in range(1, 13):
            for k in range(40):
                event = k % 4 == 0
                rows.append({"ticker": f"T{k}", "date": f"2024-{month:02d}-01", "score": 70,
                             "market_cap": 1e9, "factor_partial": [],
                             "factors": {"insider_opp_buyers_90d": 1 if event else 0},
                             "forward": {h: (0.05 if event else 0.0) + 0.001 * k for h in fl.HORIZONS}})
        fl.add_excess(rows)
        fl.add_factor_ranks(rows)
        report = fl.factor_report(rows, "insider_opp_buyers_90d", 60)
        self.assertGreater(report["best_vs_worst"]["spread_pct"], 4)
        self.assertGreater(report["large_caps"]["spread_pct"], 4)


FORM4 = """<SEC-DOCUMENT>
<XML>
<?xml version="1.0"?>
<ownershipDocument>
  <documentType>4</documentType>
  <issuer><issuerCik>0000320193</issuerCik></issuer>
  <reportingOwner><reportingOwnerId><rptOwnerCik>0001214156</rptOwnerCik></reportingOwnerId></reportingOwner>
  <nonDerivativeTable>
    <nonDerivativeTransaction><transactionCoding><transactionCode>P</transactionCode></transactionCoding>
      <transactionAmounts><transactionShares><value>100</value></transactionShares>
      <transactionPricePerShare><value>20.5</value></transactionPricePerShare></transactionAmounts>
    </nonDerivativeTransaction>
    <nonDerivativeTransaction><transactionCoding><transactionCode>M</transactionCode></transactionCoding>
      <transactionAmounts><transactionShares><value>500</value></transactionShares>
      <transactionPricePerShare><value>1</value></transactionPricePerShare></transactionAmounts>
    </nonDerivativeTransaction>
  </nonDerivativeTable>
</ownershipDocument>
</XML>
</SEC-DOCUMENT>"""
INDEX = """Form Type   Company Name      CIK         Date Filed  File Name
-----------------------------------------------------------------
4           APPLE INC         320193      20261001    edgar/data/320193/0001-26-000001.txt
4           COOK TIMOTHY      1214156     20261001    edgar/data/320193/0001-26-000001.txt
4           PRIVATE FUND LP   999999      20261001    edgar/data/999999/0002-26-000002.txt
10-K        APPLE INC         320193      20261001    edgar/data/320193/0003-26-000003.txt
"""


class DailyTests(unittest.TestCase):
    def test_a_form_4_gives_its_open_market_trades(self):
        trades = it.parse_form4(FORM4, date(2026, 10, 1))
        self.assertEqual(trades, [{"cik": 320193, "filed": "2026-10-01", "owner": "1214156", "code": "P",
                                   "usd": 2050.0}])
        self.assertEqual(sorted(it.form4_filings(INDEX)), ["edgar/data/320193/0001-26-000001.txt",
                                                          "edgar/data/999999/0002-26-000002.txt"])

    def fetcher(self, missing=()):
        calls = []

        def fetch(url):
            calls.append(url)
            if any(m in url for m in missing):
                return 404, None
            if "form.20261001" in url:
                return 200, INDEX.encode()
            if url.endswith("0001-26-000001.txt"):
                return 200, FORM4.encode()
            return 404, None
        return fetch, calls

    def test_days_are_read_in_order_weekends_recorded_and_unlisted_filers_skipped(self):
        fetch, calls = self.fetcher()
        with tempfile.TemporaryDirectory() as folder:
            result = it.sync_daily(fetch, start=date(2026, 10, 1), today=date(2026, 10, 6), listed={320193},
                                   directory=Path(folder), sleeper=lambda s: None)
            # Thursday read; Friday 10-02 has no index but is a weekday only 2 business days back -> settled holiday
            self.assertEqual(result["read"], ["2026-10-01"])
            self.assertIn("2026-10-03", result["no_index"])
            self.assertFalse(any("0002-26" in c for c in calls))  # a filer that is not a listed company
            day = json.loads((Path(folder) / "2026-10-01.json").read_text(encoding="utf-8"))
            self.assertEqual(day["trades"], [["2026-10-01", "1214156", "P", 2050.0, 320193]])

    def test_a_failed_filing_leaves_the_day_unwritten_and_stops(self):
        fetch, _ = self.fetcher(missing=("0001-26-000001",))
        with tempfile.TemporaryDirectory() as folder:
            result = it.sync_daily(fetch, start=date(2026, 10, 1), today=date(2026, 10, 6), listed={320193},
                                   directory=Path(folder), sleeper=lambda s: None)
            self.assertEqual(result["read"], [])
            self.assertIn("2026-10-01", result["stopped"])
            self.assertFalse((Path(folder) / "2026-10-01.json").exists())

    def test_a_temporary_refusal_is_retried(self):
        fetch, _ = self.fetcher()
        answers = iter([(503, None), (503, None)])

        def flaky(url):
            if url.endswith("0001-26-000001.txt"):
                nxt = next(answers, None)
                if nxt:
                    return nxt
            return fetch(url)
        waits = []
        with tempfile.TemporaryDirectory() as folder:
            result = it.sync_daily(flaky, start=date(2026, 10, 1), today=date(2026, 10, 2), listed={320193},
                                   directory=Path(folder), sleeper=waits.append)
            self.assertEqual(result["read"], ["2026-10-01"])
            self.assertIn(2.0, waits)
            self.assertIn(10.0, waits)

    def test_a_refusal_that_does_not_clear_stops_the_day(self):
        fetch, _ = self.fetcher()

        def refusing(url):
            return (503, None) if url.endswith("0001-26-000001.txt") else fetch(url)
        with tempfile.TemporaryDirectory() as folder:
            result = it.sync_daily(refusing, start=date(2026, 10, 1), today=date(2026, 10, 2), listed={320193},
                                   directory=Path(folder), sleeper=lambda s: None)
            self.assertIn("http 503", result["stopped"])
            self.assertFalse((Path(folder) / "2026-10-01.json").exists())

    def test_a_recent_weekday_without_an_index_is_not_yet_published(self):
        fetch, _ = self.fetcher()
        with tempfile.TemporaryDirectory() as folder:
            result = it.sync_daily(fetch, start=date(2026, 10, 2), today=date(2026, 10, 3),
                                   directory=Path(folder), sleeper=lambda s: None)
            self.assertIn("http 404", result["stopped"])

    def test_daily_files_extend_coverage_until_the_first_gap_and_feed_the_live_tag(self):
        with tempfile.TemporaryDirectory() as folder:
            directory = Path(folder)
            quarter = lambda url: data_set([(320193, "15-SEP-2026", "o1", "P", "10", "10")] if "2026q3" in url  # noqa: E731
                                           else [])
            it.sync(quarter, ["2026q1", "2026q2", "2026q3"], directory, sleeper=lambda s: None)  # 180 days of history
            it.build(directory, directory / "index.json.gz")
            (directory / "daily").mkdir()
            for day, trades in (("2026-10-01", [["2026-10-01", "o2", "P", 5.0, 320193], ["2026-10-01", "1780525", "S", 1.0, 1]]),
                                ("2026-10-02", []),
                                ("2026-10-04", [["2026-10-04", "o3", "P", 5.0, 320193]])):  # 10-03 missing
                (directory / "daily" / f"{day}.json").write_text(json.dumps({"trades": trades}), encoding="utf-8")
            source = it.InsiderSource(directory / "index.json.gz")
            self.assertEqual((source.coverage[1], source.daily_days), ("2026-10-02", 2))
            self.assertEqual(source.trades(1)[0][1], "0001780525")  # the quarterly sets' form of the same CIK
            self.assertEqual([t[1] for t in source.trades(320193)], ["o1", "o2"])  # o3 after the gap is not used
            rows = [{"ticker": "AAPL"}, {"ticker": "NOPE"}]
            it.annotate(rows, source, {"AAPL": 320193}, today=date(2026, 10, 5))
            self.assertEqual(rows[0]["insider_factors"]["as_of"], "2026-10-02")
            self.assertEqual(rows[0]["insider_factors"]["insider_buyers_90d"], 2)
            self.assertIsNone(rows[0]["insider_factors"]["insider_opp_buyers_90d"])  # no three years of history
            self.assertIsNone(rows[1]["insider_factors"]["insider_buyers_90d"])
            stale = [{"ticker": "AAPL"}]
            it.annotate(stale, source, {"AAPL": 320193}, today=date(2026, 10, 20))
            self.assertIsNone(stale[0]["insider_factors"]["as_of"])
            self.assertIsNone(stale[0]["insider_factors"]["insider_buyers_90d"])

    def test_the_outcome_label(self):
        label = lambda v: oe.factor_labels({"insider_factors": {"insider_opp_buyers_90d": v}})  # noqa: E731
        self.assertEqual(label(2)["giannis_insider_opp_buy"], "opportunistic buyer")
        self.assertEqual(label(0)["giannis_insider_opp_buy"], "none")
        self.assertIsNone(label(None)["giannis_insider_opp_buy"])


class StoreTests(unittest.TestCase):
    def test_sync_keeps_good_zips_lists_failures_and_build_stops_at_a_gap(self):
        good = data_set([(1, "15-JAN-2024", "o1", "P", "10", "10")])
        later = data_set([(1, "15-JUL-2024", "o2", "P", "10", "10")])

        def fetch(url):
            if "2024q2" in url:
                raise OSError("not there")
            return later if "2024q3" in url else good

        with tempfile.TemporaryDirectory() as folder:
            directory = Path(folder)
            result = it.sync(fetch, ["2024q1", "2024q2", "2024q3"], directory, sleeper=lambda s: None)
            self.assertEqual((result["added"], result["failed"]), (["2024q1", "2024q3"], {"2024q2": "OSError"}))
            summary = it.build(directory, directory / "index.json.gz")
            self.assertEqual((summary["quarters"], summary["skipped_after_gap"]), (1, 1))  # 2024q3 after the hole
            source = it.InsiderSource(directory / "index.json.gz")
            self.assertEqual(source.coverage, ["2024-01-01", "2024-03-31"])
            self.assertEqual(source.trades(1), [["2024-01-15", "o1", "P", 100.0]])
            self.assertEqual(it.InsiderSource(directory / "missing.json.gz").factors(1, date(2024, 3, 1))
                             ["insider_buyers_90d"], None)

    def test_the_lab_reads_insider_factors_and_measures_them_in_the_literatures_direction(self):
        self.assertEqual((fl.FACTOR_SIGNS["insider_buyers_90d"], fl.FACTOR_SIGNS["insider_net_buy_180d"]), (1, 1))


if __name__ == "__main__":
    unittest.main()
