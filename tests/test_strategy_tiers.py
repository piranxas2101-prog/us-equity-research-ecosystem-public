"""T5, owner's choice 3 (2026-10-26): three tiers of strategies and the review of the same-day ones."""
from __future__ import annotations

from datetime import date, timedelta
import json
from pathlib import Path
import tempfile
import unittest

import koulis_risk_gate
import strategy_tiers as st

ROOT = Path(__file__).resolve().parents[1]
CURVE = {("gap_fade", "short"): [{"sessions": 5, "edge_pct": 1.9, "edge_ci": [1, 2], "years": "5/5", "n": 1}]}
SHADOW = {"gap_continuation:long", "mean_reversion_overextension:long", "gap_fade:long"}


def record(kind, day, beat, playbook="momentum_12_1", direction="long", agent="periklis"):
    return {"kind": kind, "agent": agent, "playbook": playbook, "direction": direction, "session_date": day,
            "excess_pct": 0.5 if beat else -0.5, "exit_reason": "end_of_day"}


def days(n):
    return [(date(2026, 9, 1) + timedelta(days=k)).isoformat() for k in range(n)]


class TierTests(unittest.TestCase):
    def test_owner_choice_three_strategies_shadow_only(self):
        self.assertEqual(st.shadow_only(), SHADOW)
        with tempfile.TemporaryDirectory() as folder:
            self.assertEqual(st.shadow_only(Path(folder) / "missing.json"), set())

    def test_tier_of_each_kind(self):
        self.assertEqual(st.tier("gap_fade", "short", CURVE, SHADOW), "horizon")
        self.assertEqual(st.tier("gap_fade", "long", CURVE, SHADOW), "shadow_only")
        self.assertEqual(st.tier("momentum_12_1", "long", CURVE, SHADOW), "same_day_measured")

    def test_koulis_places_no_order_for_a_shadow_only_strategy(self):
        from tests.test_risk_gate import signal, strategy
        rows = [signal("yankos", "AAA", "long", 9.0, playbook="gap_continuation"),
                signal("yankos", "BBB", "long", 9.0, playbook="rsi2_mean_reversion")]
        payload = koulis_risk_gate.build_gate(
            [("yankos", strategy("yankos", rows))],
            {"block_trader_run": False, "snapshot_before": {"positions": [], "open_orders": []}},
            run_id="run_test", min_score=7.0, max_total_orders=5, notional_per_trade=250.0,
            max_total_notional=5000.0, max_short_orders=2, shadow_only=SHADOW)
        self.assertEqual([i["ticker"] for i in payload["approved_intents"]], ["BBB"])
        self.assertEqual({b["ticker"]: b["block_reason"] for b in payload["blocked_intents"]}["AAA"],
                         "shadow_only_strategy")


class ReviewTests(unittest.TestCase):
    def review(self, trade_beats, signal_beats, n_days=20, per_day=2):
        trades, signals = [], []
        for k, day in enumerate(days(n_days)):
            for j in range(per_day):
                trades.append(record("trade", day, (k * per_day + j) % 10 < trade_beats))
                signals.append(record("shadow_candidate", day, (k * per_day + j) % 10 < signal_beats))
        return st.review(trades, signals, curve=CURVE, shadow=SHADOW)[0]

    def test_measuring_until_enough_trades_and_sessions(self):
        self.assertEqual(self.review(3, 6, n_days=10, per_day=4)["verdict"], "measuring")  # 40 trades, 10 sessions
        self.assertEqual(self.review(3, 6, n_days=20, per_day=1)["verdict"], "measuring")  # 20 sessions, 20 trades

    def test_clearly_worse_than_its_candidates_proposes_shadow_only(self):
        entry = self.review(2, 7)
        self.assertEqual(entry["verdict"], "propose_shadow_only")
        self.assertLess(entry["p_value"], 0.05)

    def test_clearly_better_proposes_a_lab_check_and_close_is_no_difference(self):
        self.assertEqual(self.review(8, 3)["verdict"], "propose_lab_check")
        self.assertEqual(self.review(5, 5)["verdict"], "no_clear_difference")

    def test_held_trades_are_not_judged_as_same_day(self):
        held = [{**record("trade", d, True, "gap_fade", "short", "yankos"), "exit_reason": "held_on_horizon"}
                for d in days(25)]
        entry = st.review(held, [], curve=CURVE, shadow=SHADOW)[0]
        self.assertEqual((entry["tier"], entry["verdict"], entry["trades"]), ("horizon", "horizon", 0))

    def test_report_section_lists_every_strategy(self):
        lines = st.render([self.review(2, 7)])
        self.assertTrue(any("ΠΡΟΤΑΣΗ: μόνο καταγραφή" in line for line in lines))

    def test_live_config_is_valid_json_with_the_switch(self):
        config = json.loads((ROOT / "scanner_config.json").read_text(encoding="utf-8-sig"))
        self.assertEqual(set(config["paper_session"]["shadow_only_strategies"]), SHADOW)


if __name__ == "__main__":
    unittest.main()
