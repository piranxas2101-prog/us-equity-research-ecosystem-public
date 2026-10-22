from __future__ import annotations

import copy
import tempfile
import unittest
from pathlib import Path

import fundamental_research_packet
import petros_agent
import priced_in_assessment
import research_readiness
import us_equity_monitor


RUN_ID = "run_fundamental_test"


def source_payloads() -> tuple[dict, dict, dict]:
    babis_row = {
        "ticker": "TEST",
        "company": "Test Corp",
        "fresh_primary_data": True,
        "catalyst_evidence": [{"source": "primary", "date": "2026-08-27"}],
        "source_base": {
            "snapshot_time": "2026-08-27T10:00:00Z",
            "primary_data_feed": "test_fundamental_provider",
        },
    }
    giannis_row = {
        **babis_row,
        "score_final": 8.2,
        "giannis_tier": "high_priority",
        "risk_level": "Low",
        "instrument_type": "common",
        "catalyst_scoring_status": "classified",
        "catalyst_direction": "positive",
        "fundamental_attention_score": 82.0,
        "fundamental_coverage": 90.0,
        "forward_pe": 18.0,
        "trailing_pe": 20.0,
        "price_to_sales": 2.5,
        "revenue_growth": 0.20,
        "earnings_growth": 0.25,
        "profit_margin": 0.15,
    }
    priced_in = priced_in_assessment.assess(
        giannis_row,
        run_id=RUN_ID,
        run_timestamp="2026-08-27T10:00:00Z",
    )
    giannis_row["priced_in_status"] = priced_in["priced_in_status"]
    giannis_row["priced_in_assessment"] = priced_in
    readiness = research_readiness.assess(
        {
            **giannis_row,
            "petros_verdict": "candidate",
        }
    )
    petros_row = {
        **giannis_row,
        "petros_score": 8.0,
        "petros_verdict": "candidate",
        "confidence": "Medium",
        "rationale": ["Fundamental evidence is decision-grade."],
        "research_readiness": readiness,
    }
    common = {"run_id": RUN_ID, "run_timestamp": "2026-08-27T10:00:00Z"}
    babis = {**common, "shortlist": [babis_row]}
    giannis = {**common, "results": [giannis_row]}
    petros = {
        **common,
        "analysis_mode": "fundamental_only",
        "technical_inputs_used": False,
        "trader_inputs_used": False,
        "results": [petros_row],
    }
    return babis, giannis, petros


class FundamentalSeparationTests(unittest.TestCase):
    def test_missing_valuation_can_reach_technical_review_but_not_execution(self) -> None:
        readiness = research_readiness.assess(
            {
                "petros_verdict": "candidate",
                "fresh_primary_data": True,
                "risk_level": "Low",
                "instrument_type": "common",
                "catalyst_scoring_status": "classified",
            }
        )
        self.assertTrue(readiness["technical_review_eligible"])
        self.assertFalse(readiness["execution_ready"])
        self.assertIn("valuation_missing", readiness["decision_blockers"])
        self.assertIn("priced_in_not_assessed", readiness["decision_blockers"])
        self.assertIn("valuation", readiness["blocker_categories"])
        self.assertIn("market_expectations", readiness["blocker_categories"])
        self.assertIn("valuation", readiness["missing_required_fields"])
        self.assertTrue(readiness["resolution_actions"])
        self.assertEqual(readiness["readiness_status"], "blocked_with_diagnostics")

    def test_valuation_and_priced_in_state_make_security_ready(self) -> None:
        row = {
            "share_price": 10.0,
            "petros_verdict": "candidate",
            "fresh_primary_data": True,
            "risk_level": "Low",
            "instrument_type": "common",
            "catalyst_scoring_status": "classified",
            "forward_pe": 20.0,
            "trailing_pe": 22.0,
            "price_to_sales": 3.0,
            "revenue_growth": 0.20,
            "earnings_growth": 0.25,
            "source_base": {
                "snapshot_time": "2026-08-27T10:00:00Z",
                "primary_data_feed": "test_fundamental_provider",
            },
        }
        priced_in = priced_in_assessment.assess(
            row,
            run_id=RUN_ID,
            run_timestamp="2026-08-27T10:00:00Z",
        )
        row["priced_in_status"] = priced_in["priced_in_status"]
        row["priced_in_assessment"] = priced_in
        readiness = research_readiness.assess(
            row
        )
        self.assertEqual(readiness["security_thesis_readiness"], "ready")
        self.assertTrue(readiness["execution_ready"])
        self.assertEqual(readiness["decision_blockers"], [])
        self.assertEqual(readiness["readiness_status"], "execution_ready")

    def test_priced_in_label_without_evidence_cannot_unlock_execution(self) -> None:
        readiness = research_readiness.assess(
            {
                "petros_verdict": "candidate",
                "fresh_primary_data": True,
                "risk_level": "Low",
                "instrument_type": "common",
                "catalyst_scoring_status": "classified",
                "forward_pe": 20.0,
                "priced_in_status": "balanced",
            }
        )
        self.assertFalse(readiness["execution_ready"])
        self.assertIn("priced_in_evidence_invalid", readiness["decision_blockers"])

    def test_packet_aggregates_execution_blocker_diagnostics(self) -> None:
        babis, giannis, petros = source_payloads()
        petros["results"][0]["research_readiness"] = research_readiness.assess(
            {
                **petros["results"][0],
                "forward_pe": None,
                "trailing_pe": None,
                "price_to_sales": None,
                "price_to_book": None,
                "peg_ratio": None,
                "valuation_inputs": {},
                "priced_in_status": "not_assessed",
                "priced_in_assessment": {},
            }
        )
        packet = fundamental_research_packet.build_packet(babis, giannis, petros)
        self.assertEqual(packet["execution_blocker_counts"]["valuation_missing"], 1)
        self.assertEqual(packet["execution_blocker_counts"]["priced_in_not_assessed"], 1)
        self.assertEqual(packet["blocker_category_counts"]["valuation"], 1)

    def test_packet_carries_same_run_deep_research_into_frozen_thesis(self) -> None:
        babis, giannis, petros = source_payloads()
        deep = {
            "run_id": RUN_ID,
            "results": [{"ticker": "TEST", "research_status": "deep_review_complete",
                         "modules": {"reverse_dcf": {"status": "assessed"}}}],
        }
        packet = fundamental_research_packet.build_packet(
            babis, giannis, petros, deep_research=deep
        )
        self.assertEqual(packet["theses"][0]["deep_research"]["research_status"],
                         "deep_review_complete")
        self.assertIn("deep_research", packet["source_artifacts"])
        fundamental_research_packet.validate_packet(packet, expected_run_id=RUN_ID)

    def test_packet_rejects_cross_run_deep_research(self) -> None:
        babis, giannis, petros = source_payloads()
        with self.assertRaises(fundamental_research_packet.FundamentalPacketError):
            fundamental_research_packet.build_packet(
                babis, giannis, petros,
                deep_research={"run_id": "different", "results": []},
            )

    def test_packet_rejects_mixed_petros_artifact(self) -> None:
        babis, giannis, petros = source_payloads()
        petros["technical_inputs_used"] = True
        with self.assertRaises(fundamental_research_packet.FundamentalPacketError):
            fundamental_research_packet.build_packet(babis, giannis, petros)

    def test_packet_is_hashed_and_conflicting_rewrite_is_rejected(self) -> None:
        packet = fundamental_research_packet.build_packet(*source_payloads())
        fundamental_research_packet.validate_packet(packet, expected_run_id=RUN_ID)
        with tempfile.TemporaryDirectory() as temp:
            prefix = Path(temp) / "packet"
            fundamental_research_packet.write_packet(packet, prefix)
            fundamental_research_packet.write_packet(packet, prefix)
            changed = copy.deepcopy(packet)
            changed["theses"][0]["rationale"].append("changed after freeze")
            body = dict(changed)
            body.pop("packet_hash", None)
            changed["packet_hash"] = fundamental_research_packet.digest(body)
            with self.assertRaises(fundamental_research_packet.FundamentalPacketError):
                fundamental_research_packet.write_packet(changed, prefix)

    def test_frozen_packet_is_published_as_control_plane_references(self) -> None:
        packet = fundamental_research_packet.build_packet(*source_payloads())
        with tempfile.TemporaryDirectory() as temp:
            result = fundamental_research_packet.publish_control_plane(packet, Path(temp) / "control.sqlite")
            repeated = fundamental_research_packet.publish_control_plane(packet, Path(temp) / "control.sqlite")
        self.assertEqual(result["status"], "completed")
        self.assertTrue(result["control_packet_id"])
        self.assertEqual(repeated["status"], "reused")
        self.assertEqual(result["control_packet_id"], repeated["control_packet_id"])

    def test_rehashed_packet_still_rejects_a_tampered_thesis_identity(self) -> None:
        packet = fundamental_research_packet.build_packet(*source_payloads())
        packet["theses"][0]["rationale"].append("tampered")
        body = dict(packet)
        body.pop("packet_hash", None)
        packet["packet_hash"] = fundamental_research_packet.digest(body)
        with self.assertRaisesRegex(
            fundamental_research_packet.FundamentalPacketError, "thesis hash"
        ):
            fundamental_research_packet.validate_packet(packet, expected_run_id=RUN_ID)

    def test_handoff_attaches_frozen_identity_and_gate_downgrades_unready_candidate(self) -> None:
        packet = fundamental_research_packet.build_packet(*source_payloads())
        accepted, blocked = fundamental_research_packet.attach_fundamental_handoff(
            [{"ticker": "TEST"}, {"ticker": "MISSING"}], packet
        )
        self.assertEqual(len(accepted), 1)
        self.assertTrue(blocked)
        self.assertEqual(accepted[0]["fundamental_packet_hash"], packet["packet_hash"])
        accepted[0]["fundamental_execution_ready"] = False
        signal = fundamental_research_packet.enforce_execution_gate(
            {**accepted[0], "signal_verdict": "candidate"}
        )
        self.assertEqual(signal["signal_verdict"], "watch")
        self.assertFalse(signal["execution_eligible"])

    def test_petros_fundamental_score_ignores_technical_and_trader_fields(self) -> None:
        _, giannis, _ = source_payloads()
        row = giannis["results"][0]
        baseline = petros_agent.score_petros_fundamental(row, {})
        contaminated = {
            **row,
            "rsi_14": 99.0,
            "macd_histogram": -999.0,
            "yankos_signal_count": 100,
            "periklis_signal_count": 100,
            "strategy_consensus_adjustment": 10.0,
        }
        result = petros_agent.score_petros_fundamental(contaminated, {})
        self.assertEqual(result["petros_score"], baseline["petros_score"])
        self.assertEqual(result["petros_verdict"], baseline["petros_verdict"])
        self.assertFalse(result["technical_inputs_used"])
        self.assertFalse(result["trader_inputs_used"])

    def test_rule_score_is_completeness_adjusted(self) -> None:
        score, coverage, _ = us_equity_monitor.evaluate_rules(
            {"known": 10},
            [
                {"metric": "known", "operator": ">=", "value": 5, "weight": 1},
                {"metric": "missing", "operator": ">=", "value": 5, "weight": 1},
            ],
        )
        self.assertEqual(score, 50.0)
        self.assertEqual(coverage, 50.0)


if __name__ == "__main__":
    unittest.main()
