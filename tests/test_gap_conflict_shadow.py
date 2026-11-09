"""Favourable pre-market gap and one trader on both sides of a stock: measured only, unknown kept out, a lesson only
on a clear gap with enough sample."""
from __future__ import annotations

from datetime import date
import tempfile
import unittest
from pathlib import Path

import pandas as pd

import gap_conflict_shadow as gc


def frame(days: int = 20, high: float = 102.0, low: float = 98.0, close: float = 100.0) -> pd.DataFrame:
    index = pd.to_datetime([f"2026-10-{d:02d}" for d in range(1, days + 1)])
    return pd.DataFrame({"Open": close, "High": high, "Low": low, "Close": close}, index=index)


def metrics(rows):
    known = [r for r in rows if r.get("outcome_pct") is not None]
    days = len({r["session_date"] for r in known})
    mean = round(sum(r["outcome_pct"] for r in known) / len(known), 3) if known else None
    return {"known": len(known), "days": days, "mean_pct": mean, "mean_excess_pct": None, "beat_spy_pct": None,
            "status": "judged" if len(known) >= 2 and days >= 1 else "insufficient_sample"}


def record(ticker, direction, gap, outcome, kind="shadow_candidate", agent="yankos", session="2026-10-25"):
    return {"kind": kind, "agent": agent, "ticker": ticker, "direction": direction, "session_date": session,
            "preopen": {"gap_pct": gap}, "outcome_pct": outcome}


class AtrTests(unittest.TestCase):
    def test_atr_from_bars_before_the_session(self):
        self.assertEqual(gc.atr_pct(frame(), date(2026, 10, 25)), 4.0)       # 4 points on a 100 close
        self.assertIsNone(gc.atr_pct(frame(days=10), date(2026, 10, 25)))   # not enough sessions
        self.assertIsNone(gc.atr_pct(None, date(2026, 10, 25)))


class GapTests(unittest.TestCase):
    def test_the_gap_is_read_in_the_trade_direction(self):
        frames = lambda t: frame()  # noqa: E731 - ATR 4%
        self.assertTrue(gc.gap_reading(record("NVDA", "short", -5.85, 0), frames)["favourable_big"])
        self.assertFalse(gc.gap_reading(record("NVDA", "long", -5.85, 0), frames)["favourable_big"])
        self.assertFalse(gc.gap_reading(record("X", "long", 3.0, 0), frames)["favourable_big"])
        self.assertFalse(gc.gap_reading(record("X", "long", None, 0), frames)["known"])
        self.assertFalse(gc.gap_reading(record("X", "long", 5.0, 0), lambda t: None)["known"])


class EvaluateTests(unittest.TestCase):
    def test_both_measurements_and_their_lessons(self):
        records = [record("NVDA", "short", -6.0, -2.0), record("NVDA", "long", -6.0, None, kind="preopen_demoted"),
                   record("AMD", "long", 5.0, -1.0, session="2026-10-24"),
                   record("TSM", "long", 0.5, 1.0), record("MU", "long", -0.5, 2.0),
                   record("ASML", "long", None, 9.0),
                   record("KLAC", "long", 0.1, 1.5, agent="periklis"), record("KLAC", "short", 0.1, 0.5)]
        out = gc.evaluate(records, lambda t: frame(), metrics, 0.5)
        gap = out["favourable_gap"]
        self.assertEqual((gap["flagged"]["known"], gap["rest"]["known"], gap["unknown"]), (2, 4, 1))
        self.assertEqual(gap["verdict"], "flagged did worse")
        mix = out["both_directions"]
        self.assertEqual(mix["stocks"], 1)   # NVDA: yankos long (taken down) and short; KLAC is two traders
        self.assertEqual(mix["flagged"]["known"], 1)
        self.assertEqual(mix["verdict"], "insufficient_sample")
        lessons = gc.proposal(out)
        self.assertEqual([p["criterion_id"] for p in lessons], ["favourable_gap_spent"])
        self.assertFalse(out["active_decisions_changed"])

    def test_the_report_is_written(self):
        with tempfile.TemporaryDirectory() as folder:
            out = gc.run(Path(folder), [record("NVDA", "short", -6.0, -2.0)], lambda t: frame(), metrics, 0.5)
            text = (Path(folder) / "output" / "paper_learning" / "gap_conflict_shadow_latest.md").read_text(
                encoding="utf-8")
            self.assertIn("insufficient_sample", text)
            self.assertEqual(out["favourable_gap"]["flagged"]["known"], 1)


if __name__ == "__main__":
    unittest.main()
