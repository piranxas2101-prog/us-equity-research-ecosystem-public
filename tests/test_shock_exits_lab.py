"""The positions a market shock closed: what holding them would have done (RULES_REVIEW section 7, owner 2026-11-06
and 2026-11-17). Read-only; measured from the real exit price, against SPY over the same sessions."""
from __future__ import annotations

from datetime import date
import json
from pathlib import Path
import tempfile
import unittest

import pandas as pd

import shock_exits_lab as sl


def frame(start: date, closes: list[float]) -> pd.DataFrame:
    days = pd.bdate_range(start, periods=len(closes))
    return pd.DataFrame({"Open": closes, "High": closes, "Low": closes, "Close": closes}, index=days)


class ShockLabTests(unittest.TestCase):
    def setUp(self):
        self.frames = {
            "AAA": frame(date(2026, 11, 5), [10.0, 10.5, 10.8, 11.0, 11.2, 11.5, 11.0]),  # rises after the exit
            "BBB": frame(date(2026, 11, 5), [20.0, 19.0, 18.0, 18.0, 17.0, 16.0, 16.0]),  # keeps falling
            "SPY": frame(date(2026, 11, 5), [100.0, 101.0, 101.0, 101.0, 102.0, 102.0, 102.0]),
        }
        self.trades = [
            {"ticker": "AAA", "direction": "long", "exit_reason": "market_shock", "exit_price": 10.0,
             "exit_at": "2026-11-05T20:40:00+00:00", "session_date": "2026-11-02", "agent": "yankos"},
            {"ticker": "BBB", "direction": "long", "exit_reason": "market_shock", "exit_price": 20.0,
             "exit_at": "2026-11-05T20:40:00+00:00", "session_date": "2026-11-03", "agent": "periklis"},
            {"ticker": "CCC", "direction": "long", "exit_reason": "market_shock", "exit_price": 5.0,
             "exit_at": "2026-11-05T20:40:00+00:00", "session_date": "2026-11-03", "agent": "yankos"},
            {"ticker": "AAA", "direction": "long", "exit_reason": "scaled_stop", "exit_price": 10.0,
             "exit_at": "2026-11-05T20:40:00+00:00", "session_date": "2026-11-02", "agent": "yankos"},
        ]

    def test_only_shock_exits_are_measured(self):
        rows = sl.measure(self.trades, self.frames.get)
        self.assertEqual([r["ticker"] for r in rows], ["AAA", "BBB", "CCC"])

    def test_move_after_the_exit_and_against_spy(self):
        rows = {r["ticker"]: r for r in sl.measure(self.trades, self.frames.get)}
        five = rows["AAA"]["horizons"]["5"]
        self.assertEqual(five["move_pct"], 15.0)        # 10.0 -> 11.5 five sessions after the exit day
        self.assertEqual(five["spy_pct"], 2.0)          # 100 -> 102
        self.assertEqual(five["excess_pct"], 13.0)
        self.assertEqual(rows["BBB"]["horizons"]["5"]["move_pct"], -20.0)

    def test_a_missing_price_history_is_unknown(self):
        rows = {r["ticker"]: r for r in sl.measure(self.trades, self.frames.get)}
        self.assertIsNone(rows["CCC"]["horizons"]["5"]["move_pct"])

    def test_a_horizon_not_yet_stored_is_unknown(self):
        rows = {r["ticker"]: r for r in sl.measure(self.trades, self.frames.get)}
        self.assertIsNone(rows["AAA"]["horizons"]["10"]["move_pct"])

    def test_summary_per_shock_day(self):
        result = sl.summarize(sl.measure(self.trades, self.frames.get))
        day = result["by_shock_day"]["2026-11-05"]["5"]
        self.assertEqual((day["known"], day["mean_move_pct"], day["held_better"]), (2, -2.5, 1))
        self.assertEqual(result["verdict"], "insufficient_sample")

    def test_report_is_written_and_changes_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = sl.run(Path(tmp), trades=self.trades, frames=self.frames.get)
            data = json.loads((Path(tmp) / "output/paper_learning/shock_exits_latest.json").read_text("utf-8"))
            md = (Path(tmp) / "output/paper_learning/shock_exits_latest.md").read_text("utf-8")
        self.assertFalse(data["active_decisions_changed"])
        self.assertEqual(result["verdict"], data["verdict"])
        self.assertIn("5/11", md)


if __name__ == "__main__":
    unittest.main()


class JarvisCheckTests(unittest.TestCase):
    def test_the_lab_is_a_jarvis_check_now(self):
        import jarvis_checks
        root = Path(__file__).resolve().parents[1]
        self.assertEqual(jarvis_checks.PRODUCERS["shock_exits_lab"], ["shock_exits_lab.py"])
        check = {c["id"]: c for c in jarvis_checks.load_registry(root / "jarvis_checks.json")}["shock_longs_1105"]
        self.assertEqual((check["producer"], check["output"]), ("shock_exits_lab", "output/paper_learning/shock_exits_latest.json"))
        self.assertEqual(check["ready_values"], ["holding would have helped", "the exit helped"])
