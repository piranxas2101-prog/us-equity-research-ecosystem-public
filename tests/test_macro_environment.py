"""Macro environment: market state from SPY, breadth only with enough priced companies, backdrop with unknowns kept."""
from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

import numpy as np
import pandas as pd

import macro_environment as me


def series(closes, start="2024-01-02") -> pd.DataFrame:
    index = pd.bdate_range(start, periods=len(closes), name="Date")
    return pd.DataFrame({"Open": closes, "High": closes, "Low": closes, "Close": closes, "Volume": 1e6}, index=index)


EXPANSION = {"regime": "expansion", "confidence": "Medium", "signals": {"yield_curve": "positive"},
             "sector_impacts": {"technology": {"stance": "tailwind"}, "utilities": {"stance": "neutral"}}}


class MarketTests(unittest.TestCase):
    def test_a_steady_rise_is_an_uptrend_with_calm_volatility(self):
        state = me.market_state(series(100 * np.cumprod(np.full(300, 1.001))))
        self.assertEqual((state["trend"], state["volatility"], state["above_sma200"]), ("up", "calm", True))
        self.assertGreater(state["return_63d_pct"], 0)

    def test_too_little_history_is_unknown(self):
        self.assertEqual(me.market_state(series([100.0] * 100))["status"], "unknown")
        self.assertEqual(me.market_state(None)["status"], "unknown")

    def test_breadth_counts_only_symbols_priced_on_the_session(self):
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder)
            series(list(np.linspace(50, 100, 250))).to_csv(folder / "UP.csv.gz", compression="gzip")
            series(list(np.linspace(100, 50, 250))).to_csv(folder / "DOWN.csv.gz", compression="gzip")
            series(list(np.linspace(50, 100, 249))).to_csv(folder / "OLD.csv.gz", compression="gzip")
            session = str(pd.bdate_range("2024-01-02", periods=250)[-1].date())
            original = me.MIN_BREADTH_SYMBOLS
            me.MIN_BREADTH_SYMBOLS = 2
            try:
                width = me.breadth(folder, session, ["UP", "DOWN", "OLD", "NONE"])
            finally:
                me.MIN_BREADTH_SYMBOLS = original
            self.assertEqual((width["counted"], width["above_sma200_pct"]), (2, 50.0))
            self.assertEqual(me.breadth(folder, session, ["UP"])["status"], "unknown")  # below the minimum


class BackdropTests(unittest.TestCase):
    def test_expansion_uptrend_and_broad_participation_are_supportive(self):
        market = {"status": "ok", "trend": "up", "volatility": "calm", "realized_vol_20d_pct": 10.0}
        back = me.backdrop(EXPANSION, market, {"status": "ok", "participation": "broad", "above_sma200_pct": 70.0})
        self.assertEqual((back["label"], back["complete"]), ("supportive", True))

    def test_downtrend_with_stress_is_hostile_and_missing_parts_are_flagged(self):
        market = {"status": "ok", "trend": "down", "volatility": "stressed", "realized_vol_20d_pct": 40.0}
        back = me.backdrop({"regime": "transition"}, market, {"status": "unknown"})
        self.assertEqual((back["label"], back["complete"]), ("hostile", False))
        self.assertEqual(me.backdrop({}, {"status": "unknown"}, {"status": "unknown"})["label"], "unknown")

    def test_the_report_explains_and_states_unknowns(self):
        env = {"economy": EXPANSION, "market": {"status": "unknown", "reason": "no SPY history"},
               "breadth": {"status": "unknown", "reason": "only 10 companies"},
               "backdrop": {"label": "mixed", "complete": False}}
        text = "\n".join(me.report_lines(env))
        self.assertIn("επέκταση", text)
        self.assertIn("Αγορά: άγνωστη (no SPY history)", text)
        self.assertIn("Ούριος άνεμος: technology", text)
        self.assertEqual(me.report_lines({}), [])


if __name__ == "__main__":
    unittest.main()
