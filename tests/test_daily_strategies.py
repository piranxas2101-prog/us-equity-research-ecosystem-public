"""Daily-bar strategies, the trader bridge and the price store: rules fire where they should, never on missing data."""
from __future__ import annotations

from datetime import date
from pathlib import Path
import tempfile
import unittest

import numpy as np
import pandas as pd

import daily_strategies as ds
import price_history_store as phs
import strategy_signals


def bars(closes, *, volume=1_000_000, opens=None, highs=None, lows=None, start="2024-01-02"):
    index = pd.bdate_range(start, periods=len(closes))
    closes = np.asarray(closes, dtype=float)
    opens = closes if opens is None else np.asarray(opens, dtype=float)
    highs = np.maximum(opens, closes) * 1.005 if highs is None else np.asarray(highs, dtype=float)
    lows = np.minimum(opens, closes) * 0.995 if lows is None else np.asarray(lows, dtype=float)
    vol = np.full(len(closes), float(volume)) if np.isscalar(volume) else np.asarray(volume, dtype=float)
    return pd.DataFrame({"Open": opens, "High": highs, "Low": lows, "Close": closes, "Volume": vol}, index=index)


class StrategyRuleTests(unittest.TestCase):
    def test_momentum_fires_long_in_a_steady_uptrend_and_not_without_history(self):
        trend = bars(100 * np.cumprod(np.full(300, 1.0015)))
        self.assertGreaterEqual(ds.score_latest("momentum_12_1", trend)["long_score"], 7.25)
        self.assertIsNone(ds.score_latest("momentum_12_1", trend.iloc[:100]))  # too short: no judgement

    def test_rsi2_buys_a_sharp_dip_inside_an_uptrend(self):
        closes = list(100 * np.cumprod(np.full(250, 1.002))) + [None] * 0
        closes[-3:] = [closes[-4] * 0.97, closes[-4] * 0.94, closes[-4] * 0.92]
        self.assertGreaterEqual(ds.score_latest("rsi2_mean_reversion", bars(closes))["long_score"], 7.25)

    def test_failed_breakdown_needs_a_close_back_above_the_old_low(self):
        closes = np.full(80, 50.0)
        frame = bars(closes)
        frame.iloc[-1, frame.columns.get_loc("Low")] = 45.0   # undercut the 20-day low intraday
        frame.iloc[-1, frame.columns.get_loc("High")] = 50.6
        frame.iloc[-1, frame.columns.get_loc("Close")] = 50.4  # closed back near the high
        self.assertGreaterEqual(ds.score_latest("failed_breakdown_reclaim", frame)["long_score"], 7.25)
        frame.iloc[-1, frame.columns.get_loc("Close")] = 45.5  # stayed broken: no signal
        self.assertEqual(ds.score_latest("failed_breakdown_reclaim", frame)["long_score"], 0.0)

    def test_gap_continuation_and_gap_fade_read_the_same_gap_differently(self):
        closes = list(np.full(80, 20.0))
        opens = list(closes)
        volume = list(np.full(80, 1e6))
        opens[-1], closes[-1], volume[-1] = 22.0, 22.8, 3e6   # +10% gap that held on 3x volume
        held = bars(closes, opens=opens, volume=volume)
        self.assertGreaterEqual(ds.score_latest("gap_continuation", held)["long_score"], 7.25)
        self.assertEqual(ds.score_latest("gap_fade", held)["short_score"], 0.0)
        closes[-1] = 20.4                                       # the same gap faded into the close
        faded = bars(closes, opens=opens, volume=volume, highs=[c * 1.005 for c in opens])
        self.assertGreaterEqual(ds.score_latest("gap_fade", faded)["short_score"], 7.35)

    def test_long_only_strategies_never_score_a_short(self):
        frame = bars(100 * np.cumprod(np.full(300, 0.998)))
        for name in ("high_52w_breakout", "failed_breakdown_reclaim"):
            self.assertEqual(ds.score_history(name, frame)["short"].max(), 0.0)

    def test_every_strategy_belongs_to_a_trader_and_intraday_ones_are_kept_apart(self):
        for strategy in ds.STRATEGIES.values():
            self.assertIn(strategy.trader, ("yankos", "periklis"))
        self.assertFalse(set(ds.INTRADAY_ONLY) & set(ds.STRATEGIES))


class TraderBridgeTests(unittest.TestCase):
    def test_a_current_history_is_scored_and_a_missing_one_is_stated(self):
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder)
            history = bars(100 * np.cumprod(np.full(300, 1.0015)), start="2025-08-01")
            history.index.name = "Date"
            history.to_csv(folder / "ABC.csv.gz", compression="gzip")
            today = history.index.max().date()
            playbook = strategy_signals.playbooks_for("periklis")[0]
            scored = strategy_signals.score({**playbook, "name": "momentum_12_1"}, {"ticker": "ABC"},
                                            directory=folder, today=today)
            self.assertGreaterEqual(scored.long_score, 7.25)
            self.assertEqual(scored.meta["strategy_session"], str(today))
            missing = strategy_signals.score({"name": "momentum_12_1"}, {"ticker": "XYZ"}, directory=folder, today=today)
            self.assertEqual((missing.long_score, missing.short_score), (0.0, 0.0))
            self.assertIn("cannot be judged", missing.long_rationale[0])
            stale = strategy_signals.score({"name": "momentum_12_1"}, {"ticker": "ABC"}, directory=folder,
                                           today=date(today.year + 1, 1, 15))
            self.assertEqual(stale.long_score, 0.0)

    def test_playbook_entries_split_the_strategies_between_the_traders(self):
        names = {p["name"] for p in strategy_signals.playbooks_for("yankos")} | {
            p["name"] for p in strategy_signals.playbooks_for("periklis")}
        self.assertEqual(names, set(ds.STRATEGIES))


class PriceStoreTests(unittest.TestCase):
    def test_new_days_are_merged_and_a_split_triggers_a_full_refetch(self):
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder)
            first = bars([10.0, 10.5, 11.0], start="2026-10-01")
            calls = []

            def download(symbols, start):
                calls.append(start)
                return {"ABC": first if len(calls) == 1 else bars([5.0, 5.25, 5.5, 5.6], start="2026-10-01")}

            phs.update(["ABC"], folder, today=date(2026, 10, 8), download=download, pause=0, progress=lambda _m: None)
            self.assertEqual(len(phs.load("ABC", folder)), 3)
            status = phs.update(["ABC"], folder, today=date(2026, 10, 12), download=download, pause=0,
                                progress=lambda _m: None)
            stored = phs.load("ABC", folder)
            self.assertEqual(status["counts"].get("refetched_after_split"), 1)  # halved prices: a 2-for-1 split
            self.assertAlmostEqual(float(stored["Close"].iloc[0]), 5.0)


if __name__ == "__main__":
    unittest.main()
