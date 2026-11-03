"""Results lab (F5, owner 2026-11-03): the next-report estimate and the results window."""
from __future__ import annotations

from datetime import date, timedelta
import unittest

import results_lab as rl


class ResultsLabTests(unittest.TestCase):
    def test_next_report_is_last_years_same_report_plus_52_weeks_else_13_weeks(self):
        reports = [(date(2025, 1, 29), None), (date(2025, 4, 30), None), (date(2025, 7, 30), None),
                   (date(2025, 10, 29), None)]
        self.assertEqual(rl.estimate_next(reports, date(2026, 1, 2)),
                         (date(2025, 1, 29) + timedelta(weeks=52), "last_year_plus_52_weeks"))
        self.assertEqual(rl.estimate_next(reports[-1:], date(2025, 11, 1)),
                         (date(2025, 10, 29) + timedelta(weeks=13), "last_report_plus_13_weeks"))
        self.assertEqual(rl.estimate_next([], date(2025, 11, 1)), (None, "unknown"))

    def test_window_runs_from_two_sessions_before_the_filing_day_to_the_session_after(self):
        days = [date(2026, 10, 26) + timedelta(days=k) for k in range(6)]
        closes = [100.0, 100.0, 110.0, 90.0, 95.0, 96.0]
        start, end, ret = rl.window_return(days, closes, date(2026, 10, 29))   # filing day = index 3
        self.assertEqual((start, end), (1, 4))
        self.assertAlmostEqual(ret, -0.05)
        self.assertIsNone(rl.window_return(days, closes, date(2026, 10, 26)))  # no sessions before it

    def test_stats_report_the_tail_and_unknown_is_empty(self):
        stats = rl._stats([("2024-01", -12.0), ("2024-02", 2.0), ("2024-03", 4.0)])
        self.assertEqual((stats["n"], stats["share_below_minus10"]), (3, 0.333))
        self.assertEqual(rl._stats([]), {"n": 0})


if __name__ == "__main__":
    unittest.main()
