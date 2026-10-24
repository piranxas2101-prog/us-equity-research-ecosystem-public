from __future__ import annotations

import copy
import json
import unittest
from pathlib import Path

import koulis_risk_gate
import playbook_framework
import trader_trade_plan
from thymios_order_intent import IntentValidationError, validate_intent_payload


ROOT = Path(__file__).resolve().parents[1]


class TraderTradePlanTests(unittest.TestCase):
    def setUp(self) -> None:
        self.catalog = playbook_framework.load_catalog(ROOT / "playbook_catalog_v2.json")
        self.config = json.loads((ROOT / "yankos_playbooks.json").read_text(encoding="utf-8"))
        self.row = {
            "agent_name": "yankos", "ticker": "TEST", "company": "Test Corp",
            "trade_direction": "long", "playbook_name": "support_bounce_swing",
            "signal_score": 8.2, "signal_verdict": "candidate", "confidence": "High",
            "instrument_type": "common", "risk_level": "Low", "fresh_primary_data": True,
            "fundamental_packet_hash": "packet_hash_test",
            "fundamental_thesis_id": "thesis_run_test_TEST",
            "fundamental_thesis_hash": "thesis_hash_TEST",
            "fundamental_gate_status": "pass", "execution_eligible": True,
            "atr_14_pct": 4.0, "rationale": ["Support reclaimed.", "Volume confirmed."],
        }

    def plan(self) -> dict:
        playbook = playbook_framework.catalog_by_id(self.catalog)["support_bounce_swing"]
        return trader_trade_plan.build_trade_plan(
            "yankos", self.row, playbook, self.config["paper_sizing_policy"], "run_test"
        )

    def test_agent_plan_freezes_sizing_rationale_and_exit_rules(self) -> None:
        plan = self.plan()
        self.assertGreater(plan["suggested_notional_usd"], 0)
        self.assertGreater(plan["take_profit_pct"], plan["stop_loss_pct"])
        self.assertEqual(plan["holding_period_rule"], "same_day_flat")
        self.assertTrue(plan["invalidators"])
        self.assertTrue(plan["exit_logic"])
        self.assertFalse(plan["execution_authority"])
        basis = {key: value for key, value in plan.items() if key not in {"trade_plan_sha256", "execution_authority"}}
        self.assertEqual(plan["trade_plan_sha256"], trader_trade_plan.digest(basis))

    def test_koulis_preserves_agent_notional_and_thymios_detects_tampering(self) -> None:
        row = {**self.row, "paper_trade_plan": self.plan()}
        strategy = {"run_id": "run_test", "agent_name": "yankos", "results": [row]}
        empty = {"run_id": "run_test", "agent_name": "periklis", "results": []}
        preflight = {"block_trader_run": False, "snapshot_before": {"positions": [], "open_orders": []}}
        payload = koulis_risk_gate.build_gate(
            [("yankos", strategy), ("periklis", empty)], preflight,
            run_id="run_test", min_score=7.0, max_total_orders=10,
            notional_per_trade=250.0, max_total_notional=10000.0,
            max_short_orders=5, notional_source="agent_suggestion",
        )
        self.assertEqual(payload["approved_count"], 1)
        intent = payload["approved_intents"][0]
        self.assertEqual(intent["notional_usd"], row["paper_trade_plan"]["suggested_notional_usd"])
        validate_intent_payload(payload)

        tampered = copy.deepcopy(payload)
        tampered["approved_intents"][0]["trade_plan"]["stop_loss_pct"] = 0.99
        with self.assertRaisesRegex(IntentValidationError, "hash mismatch"):
            validate_intent_payload(tampered)

    def test_short_with_unresolved_implementation_gates_is_blocked(self) -> None:
        playbook = playbook_framework.catalog_by_id(self.catalog)["mean_reversion_overextension"]
        short_row = {**self.row, "ticker": "SHORT", "playbook_name": playbook["playbook_id"], "trade_direction": "short"}
        short_row["paper_trade_plan"] = trader_trade_plan.build_trade_plan(
            "yankos", short_row, playbook, self.config["paper_sizing_policy"], "run_test"
        )
        candidates, blocked = koulis_risk_gate.candidate_rows(
            {"results": [short_row]}, "yankos", 7.0, 250.0,
            notional_source="agent_suggestion", expected_run_id="run_test",
        )
        self.assertFalse(candidates)
        # without today's short-gate data nothing resolves; the reason now names the open gates (2026-10-24)
        self.assertTrue(blocked[0]["block_reason"].startswith("unresolved_short_implementation_gates:"))
        self.assertIn("shortability", blocked[0]["block_reason"])


if __name__ == "__main__":
    unittest.main()
