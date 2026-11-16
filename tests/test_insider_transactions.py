"""Form 4 insider trades (owner approved 2026-11-16): parsing, point in time, coverage, unknown stays unknown."""
from __future__ import annotations

from datetime import date
import io
from pathlib import Path
import tempfile
import unittest
import zipfile

import fundamental_lab as fl
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
        self.assertAlmostEqual(f["insider_net_buy_180d"], (150001 - 50000) / 200001)

    def test_no_trades_inside_coverage_is_a_known_zero_and_no_ratio(self):
        self.assertEqual(it.factors([], date(2024, 6, 1), self.COVER),
                         {"insider_buyers_90d": 0, "insider_net_buy_180d": None})

    def test_outside_coverage_is_unknown(self):
        for day in (date(2023, 3, 1), date(2025, 2, 1)):  # under 180 days of history; after the last quarter
            self.assertEqual(it.factors(self.TRADES, day, self.COVER),
                             {"insider_buyers_90d": None, "insider_net_buy_180d": None})
        self.assertIsNone(it.factors(self.TRADES, date(2024, 6, 1), None)["insider_buyers_90d"])


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
