"""Results calendar (F5, owner's decisions 2026-11-03): the expected next report, three legacy shorts out before
results (and not opened inside that window), and the expected results date as the catalyst of the shorts that
gain through results."""
from __future__ import annotations

from datetime import date
import json
from pathlib import Path
import tempfile
import unittest

import alpaca_multi_day_monitor as mdm
import alpaca_short_gate_data as sg
import results_calendar as rc
from tests.test_multi_day_positions import entry, ny

TODAY = date(2026, 11, 3)  # a Tuesday


def calendar(expected: str, as_of: str = "2026-11-03", ticker: str = "ABC") -> dict:
    return {"as_of": as_of, "symbols": {ticker: {"expected_date": expected, "method": "last_year_plus_52_weeks"}}}


class CalendarTests(unittest.TestCase):
    def test_next_report_from_last_years_same_report_not_the_last_reports_twin(self):
        reports = {"ABC": [date(2025, 2, 5), date(2025, 5, 7), date(2025, 8, 6), date(2025, 11, 5),
                           date(2026, 2, 4), date(2026, 5, 6), date(2026, 8, 5), date(2026, 11, 4)],
                   "NEW": [date(2026, 8, 12)]}
        built = rc.build(reports, date(2026, 11, 4))
        self.assertEqual(built["symbols"]["ABC"]["expected_date"], "2027-02-03")   # 2026-02-04 + 52 weeks
        self.assertEqual(built["symbols"]["NEW"], {"expected_date": "2026-11-11", "method": "last_report_plus_13_weeks",
                                                    "last_report": "2026-08-12"})
        self.assertEqual(rc.build({}, TODAY)["symbols"], {})

    def test_stale_calendar_unknown_symbol_and_long_overdue_report_decide_nothing(self):
        self.assertEqual(rc.expected_date(calendar("2026-11-05"), "abc", TODAY), date(2026, 11, 5))
        self.assertIsNone(rc.expected_date(calendar("2026-11-05", as_of="2026-10-27"), "ABC", TODAY))
        self.assertIsNone(rc.expected_date(calendar("2026-11-05"), "XYZ", TODAY))
        self.assertIsNone(rc.expected_date(calendar("2026-10-15"), "ABC", TODAY))   # 19 days late: not trusted
        self.assertIsNone(rc.expected_date(None, "ABC", TODAY))

    def test_step_aside_only_for_the_three_legacy_shorts_within_three_sessions(self):
        near = calendar("2026-11-06")                                                  # 3 sessions ahead
        self.assertEqual(rc.step_aside(near, "ABC", "controlled_breakout_swing", "short", TODAY), date(2026, 11, 6))
        self.assertEqual(rc.step_aside(calendar("2026-10-30"), "ABC", "early_accumulation_swing", "short", TODAY),
                         date(2026, 10, 30))                                           # overdue: still pending
        self.assertIsNone(rc.step_aside(calendar("2026-11-09"), "ABC", "controlled_breakout_swing", "short", TODAY))
        self.assertIsNone(rc.step_aside(near, "ABC", "support_bounce_swing", "long", TODAY))     # gains through
        self.assertIsNone(rc.step_aside(near, "ABC", "momentum_12_1", "short", TODAY))


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
        self.assertEqual(rc.entry_results_ahead(data, "ABC", "pre_run_breakout_confirmation", "short", "2026-11-03"),
                         "2026-11-05")
        self.assertIsNone(rc.entry_results_ahead(data, "ABC", "gap_fade", "short", "2026-11-03"))
        self.assertIsNone(rc.entry_results_ahead(data, "ABC", "pre_run_breakout_confirmation", "short", "2026-11-04"))
        self.assertIsNone(rc.entry_results_ahead(self.data("2026-11-20"), "ABC", "pre_run_breakout_confirmation",
                                                 "short", "2026-11-03"))


if __name__ == "__main__":
    unittest.main()
