from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import alpaca_paper_trader
import koulis_risk_gate
import miltos_entry_stalker
from thymios_order_intent import build_validation_report, intents_for_agent, validate_intent_payload


def signal(agent: str, ticker: str, direction: str, score: float, playbook: str = "swing") -> dict:
    return {
        "agent_name": agent,
        "ticker": ticker,
        "company": ticker,
        "trade_direction": direction,
        "playbook_name": playbook,
        "signal_score": score,
        "signal_verdict": "candidate",
        "instrument_type": "common",
        "risk_level": "Low",
        "fresh_primary_data": True,
        "fundamental_packet_hash": "packet_hash_test",
        "fundamental_thesis_id": f"thesis_run_test_{ticker}",
        "fundamental_thesis_hash": f"thesis_hash_{ticker}",
        "fundamental_gate_status": "pass",
        "execution_eligible": True,
    }


def strategy(agent: str, rows: list[dict]) -> dict:
    return {"run_id": "run_test", "run_timestamp": "2026-08-25T10:00:00Z", "agent_name": agent, "results": rows}


def clean_preflight() -> dict:
    return {"block_trader_run": False, "snapshot_before": {"positions": [], "open_orders": []}}


class KoulisRiskGateTests(unittest.TestCase):
    def build(self, yankos_rows: list[dict], periklis_rows: list[dict], preflight: dict | None = None) -> dict:
        return koulis_risk_gate.build_gate(
            [("yankos", strategy("yankos", yankos_rows)), ("periklis", strategy("periklis", periklis_rows))],
            preflight or clean_preflight(),
            run_id="run_test",
            min_score=7.0,
            max_total_orders=3,
            notional_per_trade=250.0,
            max_total_notional=750.0,
            max_short_orders=1,
        )

    def test_opposing_agent_directions_block_symbol(self) -> None:
        payload = self.build([signal("yankos", "ABC", "long", 8.0)], [signal("periklis", "ABC", "short", 8.5)])
        self.assertEqual(payload["approved_count"], 0)
        self.assertEqual({item["block_reason"] for item in payload["blocked_intents"]}, {"agent_direction_conflict"})

    def test_duplicate_same_direction_approves_only_best(self) -> None:
        payload = self.build([signal("yankos", "ABC", "long", 8.0)], [signal("periklis", "ABC", "long", 8.5)])
        self.assertEqual(payload["approved_count"], 1)
        self.assertEqual(payload["approved_intents"][0]["agent"], "periklis")
        self.assertIn("duplicate_symbol_proposal", {item["block_reason"] for item in payload["blocked_intents"]})
        validate_intent_payload(payload)

    def test_strategy_rank_spreads_slots_across_strategies_with_different_scales(self) -> None:
        # support_bounce scores up to 10.0 on many names; momentum never reaches 10 (first session, 2026-10-15)
        bounce = [signal("yankos", f"B{i}", "long", 10.0 - i * 0.1, "support_bounce_swing") for i in range(6)]
        momentum = [signal("periklis", f"M{i}", "long", 9.0 - i * 0.2, "momentum_12_1") for i in range(6)]
        rsi = [signal("yankos", f"R{i}", "long", 8.5 - i * 0.1, "rsi2_mean_reversion") for i in range(4)]
        payload = koulis_risk_gate.build_gate(
            [("yankos", strategy("yankos", bounce + rsi)), ("periklis", strategy("periklis", momentum))],
            clean_preflight(), run_id="run_test", min_score=7.0, max_total_orders=8, notional_per_trade=250.0,
            max_total_notional=2000.0, max_short_orders=2)
        playbooks = [item["playbook_name"] for item in payload["approved_intents"]]
        self.assertEqual(sorted(playbooks), sorted(["support_bounce_swing"] * 2 + ["momentum_12_1"] * 2
                                                   + ["rsi2_mean_reversion"] * 2))
        self.assertIn("B0", {item["ticker"] for item in payload["approved_intents"]})    # each strategy's best first
        self.assertIn("portfolio_strategy_limit", {item["block_reason"] for item in payload["blocked_intents"]})
        self.assertEqual(payload["portfolio_limits"]["selection"], "strategy_rank")
        validate_intent_payload(payload)

    def test_freshness_follows_the_periodic_report_and_falls_back_when_unknown(self) -> None:
        quiet = signal("yankos", "QUIET", "long", 8.0)          # no filing in 30 days, current 10-Q
        quiet.update(fresh_primary_data=False, fundamentals_fresh=True)
        old = signal("yankos", "OLD", "long", 8.0)              # filed something lately, but the 10-Q is stale
        old.update(fresh_primary_data=True, fundamentals_fresh=False)
        unknown = signal("yankos", "UNK", "long", 8.0)          # periodic age unknown: the old flag decides
        unknown.update(fresh_primary_data=False, fundamentals_fresh=None)
        payload = self.build([quiet, old, unknown], [])
        self.assertEqual([item["ticker"] for item in payload["approved_intents"]], ["QUIET"])
        reasons = {item["ticker"]: item["block_reason"] for item in payload["blocked_intents"]}
        self.assertEqual(reasons, {"OLD": "stale_primary_data", "UNK": "stale_primary_data"})

    def test_score_mode_keeps_the_old_raw_score_order(self) -> None:
        bounce = [signal("yankos", f"B{i}", "long", 10.0, "support_bounce_swing") for i in range(4)]
        momentum = [signal("periklis", "M0", "long", 9.0, "momentum_12_1")]
        payload = koulis_risk_gate.build_gate(
            [("yankos", strategy("yankos", bounce)), ("periklis", strategy("periklis", momentum))],
            clean_preflight(), run_id="run_test", min_score=7.0, max_total_orders=3, notional_per_trade=250.0,
            max_total_notional=750.0, max_short_orders=1, selection="score", max_per_strategy=0)
        self.assertEqual({item["playbook_name"] for item in payload["approved_intents"]}, {"support_bounce_swing"})

    def test_existing_exposure_blocks_matching_symbol(self) -> None:
        preflight = clean_preflight()
        preflight["snapshot_before"]["positions"] = [{"symbol": "ABC"}]
        payload = self.build([signal("yankos", "ABC", "long", 8.0)], [], preflight)
        self.assertEqual(payload["approved_count"], 0)
        self.assertIn("existing_account_exposure", {item["block_reason"] for item in payload["blocked_intents"]})

    def test_non_candidate_is_prefiltered_before_koulis(self) -> None:
        row = signal("yankos", "ABC", "long", 8.0)
        row.update(signal_verdict="watch", trade_direction="flat", execution_eligible=False, fundamental_gate_status="technical_review_only")
        payload = self.build([row], [])
        self.assertEqual(payload["approved_count"], 0)
        self.assertEqual(payload["blocked_count"], 0)
        self.assertEqual(payload["prefiltered_count"], 1)
        self.assertEqual(payload["prefiltered_observations"][0]["filter_reason"], "signal_not_candidate")
        self.assertEqual(payload["completion_status"], "completed_no_eligible_intents")

    def test_fundamental_downgrade_preserves_pre_gate_verdict_and_all_reasons(self) -> None:
        row = signal("yankos", "ABC", "long", 8.0)
        row.update(
            signal_verdict="watch",
            pre_gate_verdict="candidate",
            post_gate_verdict="watch",
            execution_eligible=False,
            fundamental_gate_status="technical_review_only",
            gate_decision_reasons=["fundamental_not_execution_ready", "priced_in_not_assessed"],
            fundamental_decision_blockers=["priced_in_not_assessed"],
            verdict_transition={"from": "candidate", "to": "watch", "changed": True},
        )
        payload = self.build([row], [])
        observation = payload["prefiltered_observations"][0]
        self.assertEqual(observation["pre_gate_verdict"], "candidate")
        self.assertEqual(observation["post_gate_verdict"], "watch")
        self.assertEqual(observation["filter_reason"], "fundamental_not_execution_ready")
        self.assertIn("priced_in_not_assessed", observation["filter_reasons"])

    def test_thymios_writes_valid_zero_intent_decision(self) -> None:
        payload = self.build([], [])
        report = build_validation_report(payload)
        self.assertEqual(report["schema"], "ecosystem.thymios_validation.v1")
        self.assertEqual(report["status"], "validated_no_intents")
        self.assertTrue(report["validated"])
        self.assertEqual(report["approved_intent_count"], 0)

    def test_miltos_noop_report_has_current_run_identity(self) -> None:
        report = miltos_entry_stalker.build_noop_report("run_test", "no_approved_intents")
        self.assertEqual(report["run_id"], "run_test")
        self.assertEqual(report["status"], "completed_noop")
        self.assertEqual(report["submitted"], [])
        self.assertIn("methodology_sha256", report["methodology"])
        self.assertIn("personality_sha256", report["personality"])

    def test_any_strategy_run_mismatch_blocks_whole_gate(self) -> None:
        bad = strategy("periklis", [signal("periklis", "XYZ", "long", 8.5)])
        bad["run_id"] = "run_other"
        payload = koulis_risk_gate.build_gate(
            [("yankos", strategy("yankos", [signal("yankos", "ABC", "long", 8.0)])), ("periklis", bad)],
            clean_preflight(),
            run_id="run_test",
            min_score=7.0,
            max_total_orders=3,
            notional_per_trade=250.0,
            max_total_notional=750.0,
            max_short_orders=1,
        )
        self.assertEqual(payload["gate_status"], "blocked")
        self.assertEqual(payload["approved_count"], 0)
        self.assertEqual(payload["gate_reason"], "strategy_run_id_mismatch")

    def test_trader_filters_to_validated_intents(self) -> None:
        payload = self.build(
            [signal("yankos", "ABC", "long", 8.0), signal("yankos", "XYZ", "long", 7.5)],
            [],
        )
        approved = intents_for_agent(payload, "yankos")
        selected, skipped = alpaca_paper_trader.choose_trade_rows(
            strategy("yankos", [signal("yankos", "ABC", "long", 8.0), signal("yankos", "NOPE", "long", 9.0)]),
            max_orders=3,
            min_score=7.0,
            precheck_shortable=False,
            approved_intents=approved,
        )
        self.assertEqual([row["ticker"] for row in selected], ["ABC"])
        self.assertIn("not_approved_by_koulis_thymios", {item["reason"] for item in skipped})

    def test_submit_without_intent_file_fails_before_broker(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            strategy_path = Path(temp) / "strategy.json"
            strategy_path.write_text(json.dumps(strategy("yankos", [])), encoding="utf-8")
            args = SimpleNamespace(strategy=str(strategy_path), agent_name=None, submit=True, approved_intents=None)
            with self.assertRaises(SystemExit):
                alpaca_paper_trader.run(args)

    def test_low_level_submit_without_intent_id_fails_before_broker(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "approved_intent_id"):
            alpaca_paper_trader.submit_paper_order(
                "yankos",
                signal("yankos", "ABC", "long", 8.0),
                250.0,
            )
