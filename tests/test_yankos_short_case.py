"""Characterization tests for the Yankos short-case scorer.

The expected scores and rationale lists below were captured from the scorer's
behavior *before* it was refactored to take a ShortCaseInputs dataclass and to
read its weights from SHORT_CASE_WEIGHTS. They pin the exact output so that
externalizing the weights cannot silently change which trades the scorer favors.
If a weight in SHORT_CASE_WEIGHTS is deliberately retuned later, the affected
expected value here must be updated in the same change.
"""
from __future__ import annotations

import unittest

from yankos_strategy_agent import ShortCaseInputs, build_short_case


def _inputs(**overrides) -> ShortCaseInputs:
    base = dict(
        ticker="AAA",
        instrument_type="common",
        risk_level="Medium",
        fresh_primary_data=True,
        timing="neutral",
        rsi=None,
        volume_ratio=None,
        macd_histogram=None,
        atr_14_pct=None,
        price_position_20d=None,
        price_position_60d=None,
        return_20d=None,
        return_60d=None,
        candle_patterns=[],
        candle_bias="neutral",
        candle_score=None,
        net_cash=None,
        fcf=None,
        ocf=None,
    )
    base.update(overrides)
    return ShortCaseInputs(**base)


class YankosShortCaseCharacterizationTests(unittest.TestCase):
    def test_strong_short_clamps_at_ten(self):
        score, rationale = build_short_case(_inputs(
            instrument_type="common", risk_level="Low", fresh_primary_data=True,
            timing="near_highs_reversal", rsi=80.0, volume_ratio=3.6, macd_histogram=-0.1,
            atr_14_pct=7.0, price_position_20d=97.0, price_position_60d=90.0,
            return_20d=30.0, return_60d=35.0, candle_patterns=["bearish_engulfing"],
            candle_bias="bearish", candle_score=-1.0,
        ))
        self.assertEqual(score, 10.0)
        self.assertEqual(len(rationale), 8)
        self.assertEqual(rationale[0], "Common stock structure is borrowable enough for directional short review.")
        self.assertEqual(rationale[3], "Technical timing points to a near-highs reversal risk.")

    def test_long_friendly_clamps_at_zero(self):
        score, rationale = build_short_case(_inputs(
            instrument_type="warrant", risk_level="High", fresh_primary_data=False,
            timing="early_accumulation", rsi=40.0, volume_ratio=1.0, macd_histogram=0.2,
            atr_14_pct=15.0, price_position_20d=30.0, price_position_60d=20.0,
            return_20d=2.0, return_60d=1.0, candle_patterns=["hammer"],
            candle_bias="bullish", candle_score=1.0, net_cash=100.0, fcf=50.0, ocf=25.0,
        ))
        self.assertEqual(score, 0.0)
        self.assertEqual(rationale[0], "Instrument type warrant is outside the short playbook.")

    def test_all_none_numeric_scores_categoricals_only(self):
        score, rationale = build_short_case(_inputs(
            instrument_type="common", risk_level="Medium", fresh_primary_data=True, timing="neutral",
        ))
        self.assertEqual(score, 1.5)
        self.assertEqual(len(rationale), 4)

    def test_mid_extended_case(self):
        score, rationale = build_short_case(_inputs(
            instrument_type="common", risk_level="Medium", fresh_primary_data=False,
            timing="already_extended", rsi=70.0, volume_ratio=1.2, macd_histogram=-0.02,
            atr_14_pct=9.0, price_position_20d=89.0, price_position_60d=77.0,
            return_20d=19.0, return_60d=31.0, candle_patterns=["doji"],
            candle_bias="neutral", candle_score=0.0, fcf=10.0,
        ))
        self.assertEqual(score, 5.4)
        self.assertEqual(len(rationale), 8)

    def test_doji_near_high_counts_but_rationale_is_capped(self):
        score, rationale = build_short_case(_inputs(
            instrument_type="common", risk_level="Low", fresh_primary_data=True, timing="neutral",
            rsi=59.0, volume_ratio=0.9, macd_histogram=0.09, atr_14_pct=8.5,
            price_position_20d=90.0, price_position_60d=76.0, return_20d=6.0, return_60d=10.0,
            candle_patterns=["doji"], candle_bias="bullish", candle_score=0.5,
        ))
        self.assertEqual(score, 3.0)
        self.assertEqual(len(rationale), 8)  # rationale is truncated to 8 entries

    def test_not_stretched_case_clamps_at_zero(self):
        score, rationale = build_short_case(_inputs(
            instrument_type="common", risk_level="Low", fresh_primary_data=True,
            timing="pre_run_watch", rsi=50.0, volume_ratio=2.0, macd_histogram=0.0,
            atr_14_pct=12.5, price_position_20d=54.0, return_20d=4.0,
        ))
        self.assertEqual(score, 0.0)
        self.assertEqual(rationale[3], "Technical timing pre_run_watch is friendlier to longs than fresh shorts.")


if __name__ == "__main__":
    unittest.main()
