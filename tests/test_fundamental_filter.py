"""Fundamental filter on the traders' entries (owner approved 2026-11-16)."""
from __future__ import annotations

import json
from pathlib import Path
import unittest

import fundamental_filter as ff
import koulis_risk_gate
import shadow_replay

ROOT = Path(__file__).resolve().parents[1]
ACTIVE = {"mode": "active", "short_max_long_quality": 80.0, "long_max_avoid": 80.0}


def signal(direction="short", verdict="candidate", eligible=True):
    return {"ticker": "AAA", "trade_direction": direction, "signal_verdict": verdict, "pre_gate_verdict": verdict,
            "post_gate_verdict": verdict, "execution_eligible": eligible, "fundamental_gate_status": "pass",
            "gate_decision_reasons": [], "rationale": ["setup"], "signal_score": 7.0}


class FilterRuleTests(unittest.TestCase):
    def test_short_on_top_quality_loses_only_its_entry(self):
        row = ff.apply(signal("short"), {"long_quality": 85.0, "avoid": 10.0}, ACTIVE)
        self.assertFalse(row["execution_eligible"])
        self.assertEqual(row["signal_verdict"], "candidate")  # the trader's verdict is kept for learning and exits
        self.assertEqual(row["trade_direction"], "short")
        self.assertEqual(row["gate_decision_reasons"], ["fundamental_filter_short_on_quality"])
        self.assertEqual(row["fundamental_filter"]["rule"], "short_max_long_quality")
        self.assertTrue(row["fundamental_filter"]["blocked"])

    def test_long_on_top_avoid_is_blocked_and_the_middle_is_not(self):
        self.assertFalse(ff.apply(signal("long"), {"long_quality": 50.0, "avoid": 90.0}, ACTIVE)["execution_eligible"])
        kept = ff.apply(signal("long"), {"long_quality": 50.0, "avoid": 79.9}, ACTIVE)
        self.assertTrue(kept["execution_eligible"])
        self.assertIsNone(kept["fundamental_filter"]["rule"])
        self.assertEqual(kept["fundamental_composites"], {"long_quality": 50.0, "avoid": 79.9})

    def test_unknown_composite_blocks_nothing(self):
        for combo in ({}, {"long_quality": None, "avoid": None}):
            self.assertTrue(ff.apply(signal("short"), combo, ACTIVE)["execution_eligible"])

    def test_a_rule_left_out_does_not_apply(self):
        short_only = {"mode": "active", "short_max_long_quality": 80.0}
        self.assertTrue(ff.apply(signal("long"), {"avoid": 99.0}, short_only)["execution_eligible"])

    def test_shadow_tags_without_blocking_and_off_does_nothing(self):
        shadow = ff.apply(signal("short"), {"long_quality": 95.0}, {**ACTIVE, "mode": "shadow"})
        self.assertTrue(shadow["execution_eligible"])
        self.assertEqual(shadow["fundamental_filter"]["rule"], "short_max_long_quality")
        self.assertFalse(shadow["fundamental_filter"]["blocked"])
        original = signal("short")
        self.assertIs(ff.apply(original, {"long_quality": 95.0}, {"mode": "off"}), original)

    def test_it_never_gives_back_an_entry_the_readiness_gate_took(self):
        row = ff.apply(signal("long", eligible=False), {"avoid": 5.0}, ACTIVE)
        self.assertFalse(row["execution_eligible"])

    def test_flat_and_reject_rows_are_not_matched(self):
        self.assertIsNone(ff.apply(signal("short", verdict="reject"), {"long_quality": 99.0}, ACTIVE)
                          ["fundamental_filter"]["rule"])

    def test_koulis_filters_the_blocked_entry(self):
        row = ff.apply(signal("short"), {"long_quality": 85.0}, ACTIVE)
        candidates, filtered = koulis_risk_gate.prefilter_strategy_rows({"results": [row, signal("long")]}, "yankos")
        self.assertEqual([r["trade_direction"] for r in candidates], ["long"])
        self.assertEqual(len(filtered), 1)

    def test_summary_counts_blocked_candidates(self):
        rows = [ff.apply(signal("short"), {"long_quality": 85.0}, ACTIVE),
                ff.apply(signal("long", verdict="watch"), {"avoid": 85.0}, ACTIVE),
                ff.apply(signal("long"), {"avoid": 10.0}, ACTIVE)]
        result = ff.summary(rows, ACTIVE)
        self.assertEqual((result["matched"], result["blocked"]), (2, 2))
        self.assertEqual(result["blocked_candidates"], ["AAA:short"])


class FilterConfigTests(unittest.TestCase):
    def test_validation(self):
        self.assertEqual(ff.validate(None), {"mode": "off"})
        for bad in ({"mode": "on", "long_max_avoid": 80}, {"mode": "active", "long_max_avoid": "80"},
                    {"mode": "active", "long_max_avoid": 120}, {"mode": "active", "other": 1}, {"mode": "active"}):
            with self.assertRaises(ValueError, msg=bad):
                ff.validate(bad)

    def test_shipped_trader_configs_are_the_approved_ones(self):
        yankos = json.loads((ROOT / "yankos_playbooks.json").read_text(encoding="utf-8"))
        periklis = json.loads((ROOT / "periklis_playbooks.json").read_text(encoding="utf-8"))
        self.assertEqual(ff.validate(yankos["fundamental_filter"]), ACTIVE)
        self.assertEqual(ff.validate(periklis["fundamental_filter"]),
                         {"mode": "active", "short_max_long_quality": 80.0})  # the long rule did not help Periklis

    def test_replay_and_live_share_one_rule(self):
        signals = [{"source_run_timestamp": "t", "ticker": "AAA", "trade_direction": "short"}]
        composites = {"t": {"AAA": {"long_quality": 85.0}}}
        self.assertEqual(shadow_replay.filter_decisions(signals, composites, ACTIVE), [])


if __name__ == "__main__":
    unittest.main()
