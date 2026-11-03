"""Results calendar (F5, owner's decisions 2026-11-03): the expected next report, three legacy shorts out before
results (and not opened inside that window), and the expected results date as the catalyst of the shorts that
gain through results."""
from __future__ import annotations

from datetime import date, timedelta
import json
from pathlib import Path
import tempfile
import unittest

import alpaca_multi_day_monitor as mdm
import alpaca_short_gate_data as sg
import results_calendar as rc
from tests.test_multi_day_positions import entry, ny

TODAY = date(2026, 11, 3)  # a Tuesday


def calendar(expected: str, as_of: str = "2026-11-03", ticker: str = "ABC", lead: int = 5) -> dict:
    start = (date.fromisoformat(expected) - timedelta(days=lead)).isoformat()
    return {"as_of": as_of, "symbols": {ticker: {"expected_date": expected, "window_start": start,
                                                 "method": "last_year_plus_52_weeks"}}}


class CalendarTests(unittest.TestCase):
    def test_next_report_from_last_years_same_report_window_by_kind_and_share_classes(self):
        q = [date(2025, 2, 5), date(2025, 5, 7), date(2025, 8, 6), date(2026, 2, 4), date(2026, 5, 6), date(2026, 8, 5)]
        reports = {"1": [(d, False) for d in q] + [(date(2025, 11, 21), True)],      # a 10-K filed late in November
                   "2": [(date(2026, 8, 12), False)],
                   "_tickers": {"1": ["GOOG"], "2": ["NEW"]}}
        built = rc.build(reports, date(2026, 8, 20), classes={"1": ["GOOG", "GOOGL"]})["symbols"]
        self.assertEqual((built["GOOGL"]["expected_date"], built["GOOGL"]["window_start"], built["GOOGL"]["annual"]),
                         ("2026-11-20", "2026-10-23", True))                         # results may come 4 weeks early
        self.assertEqual(built["GOOG"], built["GOOGL"])
        self.assertEqual((built["NEW"]["expected_date"], built["NEW"]["window_start"], built["NEW"]["method"]),
                         ("2026-11-11", "2026-11-06", "last_report_plus_13_weeks"))
        after_q3 = rc.build({"1": [(d, False) for d in q] + [(date(2025, 11, 5), False)], "_tickers": {"1": ["A"]}},
                            date(2026, 8, 6))["symbols"]["A"]
        self.assertEqual(after_q3["expected_date"], "2026-11-04")                    # not the 2026-08-05 report's twin
        cart = [date(2025, 5, 7), date(2025, 8, 8), date(2025, 11, 10), date(2026, 5, 7), date(2026, 8, 7)]
        released = rc.build({"1": [(d, False) for d in cart] + [(date(2026, 2, 26), True)],
                             "_tickers": {"1": ["CART"]}, "_announced": {"1": date(2026, 10, 29)}},
                            date(2026, 11, 3))["symbols"]["CART"]                    # Q3 out on 10/29, 10-Q to come
        self.assertEqual(released["expected_date"], "2027-02-25")
        self.assertEqual(rc.build({}, TODAY)["symbols"], {})

    def test_stale_calendar_unknown_symbol_and_long_overdue_report_decide_nothing(self):
        self.assertEqual(rc.expected_date(calendar("2026-11-05"), "abc", TODAY), date(2026, 11, 5))
        self.assertIsNone(rc.expected_date(calendar("2026-11-05", as_of="2026-10-27"), "ABC", TODAY))
        self.assertIsNone(rc.expected_date(calendar("2026-11-05"), "XYZ", TODAY))
        self.assertIsNone(rc.expected_date(calendar("2026-10-15"), "ABC", TODAY))   # 19 days late: not trusted
        self.assertIsNone(rc.expected_date(None, "ABC", TODAY))

    def test_step_aside_only_for_the_three_legacy_shorts_within_three_sessions(self):
        near = calendar("2026-11-11")                                                  # window opens in 3 sessions
        self.assertEqual(rc.step_aside(near, "ABC", "controlled_breakout_swing", "short", TODAY), date(2026, 11, 11))
        self.assertEqual(rc.step_aside(calendar("2026-10-30"), "ABC", "early_accumulation_swing", "short", TODAY),
                         date(2026, 10, 30))                                           # overdue: still pending
        self.assertIsNone(rc.step_aside(calendar("2026-11-12"), "ABC", "controlled_breakout_swing", "short", TODAY))
        annual = calendar("2026-11-27", lead=28)                                       # 10-K based: opens 10-30
        self.assertEqual(rc.step_aside(annual, "ABC", "pre_run_breakout_confirmation", "short", TODAY),
                         date(2026, 11, 27))
        self.assertIsNone(rc.step_aside(near, "ABC", "support_bounce_swing", "long", TODAY))     # gains through
        self.assertIsNone(rc.step_aside(near, "ABC", "momentum_12_1", "short", TODAY))


class ReleaseTests(unittest.TestCase):
    def test_ptc_10k_results_dated_by_last_years_8k_202_with_the_short_window(self):
        reports = {"1": [(date(2025, 8, 1), False), (date(2025, 11, 21), True), (date(2026, 2, 5), False),
                         (date(2026, 5, 7), False), (date(2026, 7, 31), False)], "_tickers": {"1": ["PTC"]}}
        releases = {"1": [date(2025, 7, 30), date(2025, 11, 5), date(2026, 2, 4), date(2026, 5, 6), date(2026, 7, 29)]}
        item = rc.build(reports, TODAY, releases=releases)["symbols"]["PTC"]
        self.assertEqual((item["expected_date"], item["window_start"], item["from_release"]),
                         ("2026-11-04", "2026-10-30", True))
        without = rc.build(reports, TODAY)["symbols"]["PTC"]                        # filing dates only
        self.assertEqual((without["expected_date"], without["window_start"]), ("2026-11-20", "2026-10-23"))

    def test_submissions_give_the_8k_with_item_202_and_one_older_page_when_needed(self):
        import sec_results_releases as srr

        recent = {"filings": {"recent": {"accessionNumber": ["a1", "a2", "a3"], "form": ["8-K", "8-K", "10-Q"],
                                         "filingDate": ["2026-10-29", "2026-09-01", "2026-10-30"],
                                         "items": ["2.02,9.01", "5.02", ""]},
                              "files": [{"name": "CIK0000000001-submissions-001.json"}]}}
        older = {"accessionNumber": ["a0"], "form": ["8-K/A"], "filingDate": ["2025-10-30"], "items": ["2.02"]}
        asked = []

        def fetch(url):
            asked.append(url)
            return recent if url.endswith("CIK0000000001.json") else older

        found = srr.ask(1, fetch, TODAY)
        self.assertEqual([r["accession"] for r in found], ["a1", "a0"])            # recent reaches only 2026-09
        self.assertEqual(len(asked), 2)
        import sqlite3
        conn = sqlite3.connect(":memory:")
        report = srr.run(conn, [1, 2], fetch=lambda url: fetch(url) if "0000000001" in url else (_ for _ in ()).throw(
            __import__("urllib.error").error.HTTPError(url, 404, "nf", {}, None)), today=TODAY, budget=10)
        self.assertEqual((report["counts"]["asked"], report["counts"]["not_found"], report["counts"]["releases_stored"]),
                         (1, 1, 2))
        self.assertEqual(srr.due(conn, [1, 2, 3], TODAY), [3])                     # asked ones wait 30 days


class MonitorTests(unittest.TestCase):
    def test_a_legacy_short_closes_before_results_and_a_support_bounce_long_sits_through(self):
        short = entry(sessions=60, filled_at="2026-10-20T14:00:00Z", playbook="controlled_breakout_swing",
                      direction="short")
        long = entry(sessions=60, filled_at="2026-10-20T14:00:00Z")
        with tempfile.TemporaryDirectory() as folder:
            (Path(folder) / "yankos_strategy_latest.json").write_text(json.dumps({"results": []}), encoding="utf-8")
            for e, expected in ((short, "results_ahead"), (long, None)):
                reason, _ = mdm.decide(e, {"unrealized_plpc": "0.0"}, now=ny("2026-11-03 10:00"), shock=None,
                                       strategy_dir=Path(folder), calendar=calendar("2026-11-05"))
                self.assertEqual(reason, expected)
            reason, _ = mdm.decide(short, {"unrealized_plpc": "0.0"}, now=ny("2026-11-03 10:00"), shock=None,
                                   strategy_dir=Path(folder))                       # no calendar: unknown, held
            self.assertIsNone(reason)


class ShortGateTests(unittest.TestCase):
    def data(self, expected: str) -> dict:
        asset = {"symbol": "ABC", "shortable": True, "easy_to_borrow": True}
        return sg.build([asset], [{"ticker": "ABC", "short_percent_of_float": 0.05, "shares_outstanding": 5e7}],
                        "2026-11-03", info=lambda _s: {}, calendar=calendar(expected))

    def test_results_date_dates_the_catalyst_only_of_shorts_that_gain_through_results(self):
        data = self.data("2026-11-20")
        self.assertEqual(data["symbols"]["ABC"]["expected_results_date"], "2026-11-20")
        resolved, left = sg.resolve(["catalyst_timing"], data, "ABC", "2026-11-03", playbook="mean_reversion_overextension")
        self.assertEqual(left, [])
        self.assertIn("2026-11-20", resolved["catalyst_timing"])
        for playbook in ("failed_breakout_reversal", "catalyst_momentum", None):
            self.assertEqual(sg.resolve(["catalyst_timing"], data, "ABC", "2026-11-03", playbook=playbook)[1],
                             ["catalyst_timing"])
        far = self.data("2027-03-01")                                                  # beyond a quarter
        self.assertEqual(sg.resolve(["catalyst_timing"], far, "ABC", "2026-11-03", playbook="gap_fade")[1],
                         ["catalyst_timing"])

    def test_koulis_does_not_open_a_legacy_short_it_would_close_before_results(self):
        data = self.data("2026-11-05")
        self.assertEqual(data["symbols"]["ABC"]["results_window_start"], "2026-10-31")
        self.assertEqual(rc.entry_results_ahead(data, "ABC", "pre_run_breakout_confirmation", "short", "2026-11-03"),
                         "2026-11-05")
        self.assertIsNone(rc.entry_results_ahead(data, "ABC", "gap_fade", "short", "2026-11-03"))
        self.assertIsNone(rc.entry_results_ahead(data, "ABC", "pre_run_breakout_confirmation", "short", "2026-11-04"))
        self.assertIsNone(rc.entry_results_ahead(self.data("2026-11-20"), "ABC", "pre_run_breakout_confirmation",
                                                 "short", "2026-11-03"))


if __name__ == "__main__":
    unittest.main()
