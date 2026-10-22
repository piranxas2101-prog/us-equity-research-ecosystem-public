"""Tests for shadow variants: overrides, variant validation and comparison."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import periklis_strategy_agent as periklis
import shadow_replay
import yankos_strategy_agent as yankos


class ScoringOverrideTests(unittest.TestCase):
    def test_overrides_apply_and_always_restore(self) -> None:
        original = dict(yankos.VERDICT_THRESHOLDS)
        with yankos.scoring_overrides(verdict_thresholds={"long_candidate_min": 9.0}):
            self.assertEqual(yankos.VERDICT_THRESHOLDS["long_candidate_min"], 9.0)
            self.assertEqual(yankos.VERDICT_THRESHOLDS["short_watch_min"], original["short_watch_min"])
        self.assertEqual(yankos.VERDICT_THRESHOLDS, original)

    def test_restored_even_when_replay_fails(self) -> None:
        original = dict(yankos.SHORT_CASE_WEIGHTS)
        with self.assertRaises(RuntimeError):
            with yankos.scoring_overrides(short_case_weights={"risk_low": 5.0}):
                raise RuntimeError("replay crashed")
        self.assertEqual(yankos.SHORT_CASE_WEIGHTS, original)

    def test_unknown_keys_are_rejected(self) -> None:
        with self.assertRaises(ValueError):
            with yankos.scoring_overrides(verdict_thresholds={"long_candidate_minimum": 8.0}):
                pass

    def test_short_case_reads_overridden_weights(self) -> None:
        inputs = yankos.ShortCaseInputs(
            ticker="AAA", instrument_type="common", risk_level="Medium", fresh_primary_data=True, timing="neutral",
            rsi=None, volume_ratio=None, macd_histogram=None, atr_14_pct=None, price_position_20d=None,
            price_position_60d=None, return_20d=None, return_60d=None, candle_patterns=[], candle_bias="neutral",
            candle_score=None, net_cash=None, fcf=None, ocf=None)
        self.assertEqual(yankos.build_short_case(inputs)[0], 1.5)
        with yankos.scoring_overrides(short_case_weights={"instrument_common": 1.9}):
            self.assertEqual(yankos.build_short_case(inputs)[0], 2.5)
        self.assertEqual(yankos.build_short_case(inputs)[0], 1.5)


class PeriklisOverrideTests(unittest.TestCase):
    CTX = {
        "instrument_type": "common", "risk_level": "Medium", "fresh_primary_data": True, "timing": "neutral",
        "return_20d_pct": None, "macd_histogram": None, "price_position_20d_pct": None,
        "price_position_60d_pct": None, "return_60d_pct": None, "rsi": None, "volume_ratio": None,
        "atr_14_pct": None, "candlestick_patterns": [], "candlestick_bias": "neutral", "candlestick_score": None,
        "net_cash": None, "fcf": None, "ocf": None,
    }

    def test_periklis_short_case_uses_weight_table(self) -> None:
        # 0.9 common + 0.25 medium risk + 0.3 fresh data
        self.assertEqual(periklis.build_short_case(dict(self.CTX))[0], 1.45)
        with periklis.scoring_overrides(short_case_weights={"fresh_data": 1.3}):
            self.assertEqual(periklis.build_short_case(dict(self.CTX))[0], 2.45)
        self.assertEqual(periklis.build_short_case(dict(self.CTX))[0], 1.45)

    def test_periklis_overrides_restore_and_reject_unknown_keys(self) -> None:
        original = dict(periklis.VERDICT_THRESHOLDS)
        with periklis.scoring_overrides(verdict_thresholds={"long_candidate_min": 9.0}):
            self.assertEqual(periklis.VERDICT_THRESHOLDS["long_candidate_min"], 9.0)
        self.assertEqual(periklis.VERDICT_THRESHOLDS, original)
        with self.assertRaises(ValueError):
            with periklis.scoring_overrides(short_case_weights={"not_a_weight": 1.0}):
                pass

    def test_periklis_variant_is_accepted(self) -> None:
        tmp = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8")
        json.dump({"schema": shadow_replay.VARIANT_SCHEMA, "variant_id": "periklis_test", "trader": "periklis",
                   "overrides": {"verdict_thresholds": {"long_watch_min": 6.0}}}, tmp)
        tmp.close()
        self.addCleanup(Path(tmp.name).unlink)
        self.assertEqual(shadow_replay.load_variant(Path(tmp.name))["trader"], "periklis")


class VariantFileTests(unittest.TestCase):
    def _write(self, payload: dict) -> Path:
        tmp = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8")
        json.dump(payload, tmp)
        tmp.close()
        self.addCleanup(Path(tmp.name).unlink)
        return Path(tmp.name)

    def _variant(self, **changes) -> dict:
        base = {"schema": shadow_replay.VARIANT_SCHEMA, "variant_id": "test_v1", "trader": "yankos",
                "overrides": {"verdict_thresholds": {"long_candidate_min": 8.0}}}
        base.update(changes)
        return base

    def test_valid_variant_loads_with_hash(self) -> None:
        variant = shadow_replay.load_variant(self._write(self._variant()))
        self.assertEqual(len(variant["file_sha256"]), 64)

    def test_invalid_variants_are_rejected(self) -> None:
        for bad in (self._variant(trader="sokratis"), self._variant(schema="other"),
                    self._variant(overrides={"live_orders": {}}), self._variant(overrides={}),
                    self._variant(variant_id="bad id!")):
            with self.assertRaises(ValueError):
                shadow_replay.load_variant(self._write(bad))

    def test_shipped_example_variant_is_valid(self) -> None:
        path = Path(shadow_replay.ROOT) / "shadow_variants" / "yankos_strict_long_v1.json"
        variant = shadow_replay.load_variant(path)
        with yankos.scoring_overrides(verdict_thresholds=variant["overrides"]["verdict_thresholds"]):
            pass

    def test_playbook_rule_overrides(self) -> None:
        payload = {"playbooks": [{"name": "support_bounce_swing", "rules": {"max_atr_14_pct": 8}}]}
        changed = shadow_replay.apply_playbook_rules(payload, {"support_bounce_swing": {"max_atr_14_pct": 6}})
        self.assertEqual(changed["playbooks"][0]["rules"]["max_atr_14_pct"], 6)
        self.assertEqual(payload["playbooks"][0]["rules"]["max_atr_14_pct"], 8)  # original untouched
        with self.assertRaises(ValueError):
            shadow_replay.apply_playbook_rules(payload, {"unknown_playbook": {"max_atr_14_pct": 6}})
        with self.assertRaises(ValueError):
            shadow_replay.apply_playbook_rules(payload, {"support_bounce_swing": {"new_rule": 1}})


class ComparisonTests(unittest.TestCase):
    RUN = "2026-09-01T10:00:00"

    def _signal(self, ticker: str, direction: str = "long") -> dict:
        return {"source_run_timestamp": self.RUN, "ticker": ticker, "playbook_name": "support_bounce_swing",
                "trade_direction": direction, "signal_verdict": "candidate", "signal_score": 8.0}

    def test_added_and_dropped_decisions(self) -> None:
        returns = {(self.RUN, "AAA", 1): 2.0, (self.RUN, "BBB", 1): -3.0, (self.RUN, "CCC", 1): 1.0}
        baselines = {(self.RUN, 1): 0.5}
        champion = shadow_replay.outcome_rows([self._signal("AAA"), self._signal("BBB")], returns, baselines)
        variant = shadow_replay.outcome_rows([self._signal("AAA"), self._signal("CCC")], returns, baselines)
        row = next(item for item in shadow_replay.compare(champion, variant)
                   if item["trade_direction"] == "long" and item["horizon_days"] == 1)
        self.assertEqual(row["champion"]["sample_count"], 2)
        self.assertEqual(row["variant"]["sample_count"], 2)
        self.assertEqual(row["added_by_variant"]["sample_count"], 1)    # CCC
        self.assertEqual(row["dropped_by_variant"]["sample_count"], 1)  # BBB
        self.assertEqual(row["dropped_by_variant"]["win_rate_pct"], 0.0)

    def test_signals_without_outcomes_are_ignored(self) -> None:
        rows = shadow_replay.outcome_rows([self._signal("ZZZ")], {}, {})
        self.assertEqual(rows, [])


if __name__ == "__main__":
    unittest.main()
