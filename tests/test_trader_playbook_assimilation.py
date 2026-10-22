from __future__ import annotations

import unittest
from pathlib import Path

import playbook_framework
import trader_playbook_assimilation


ROOT = Path(__file__).resolve().parents[1]


class TraderPlaybookAssimilationTests(unittest.TestCase):
    def test_exam_contract_and_both_agents_pass_without_execution_authority(self) -> None:
        exam_path = ROOT / "trader_playbook_exam_v1.json"
        for agent, filename in trader_playbook_assimilation.TRADERS.items():
            report = trader_playbook_assimilation.run_agent_exam(ROOT, agent, exam_path, ROOT / filename)
            self.assertEqual(report["status"], "pass")
            self.assertGreaterEqual(report["score"], 85)
            self.assertEqual(report["catalog_coverage_pct"], 100)
            self.assertFalse(report["critical_failures"])
            self.assertFalse(report["broker_execution_authorized"])
            self.assertTrue(all(response["execution_authority"] is False for response in report["responses"]))

    def test_opening_range_requires_both_boundaries(self) -> None:
        catalog = playbook_framework.load_catalog(ROOT / "playbook_catalog_v2.json")
        playbook = playbook_framework.catalog_by_id(catalog)["opening_range_momentum"]
        missing = playbook_framework.missing_required_data(
            playbook,
            {
                "current_session_15m_bars": [{"close": 10}],
                "opening_range_high": 10.5,
                "volume_ratio_20": 1.2,
            },
        )
        self.assertIn("opening_range", missing)

    def test_false_shortability_blocks_every_short_perspective(self) -> None:
        catalog = playbook_framework.load_catalog(ROOT / "playbook_catalog_v2.json")
        playbook = playbook_framework.catalog_by_id(catalog)["failed_breakout_reversal"]
        perspective = playbook_framework.evaluate_perspective(
            playbook,
            {"playbook_id": playbook["playbook_id"], "status": "shadow", "weight": 1.0},
            {
                "ticker": "TEST", "rsi_14": 75, "price_position_20d_pct": 95,
                "return_20d_pct": 18, "macd_histogram": -0.2,
                "current_session_15m_bars": [{"close": 10}], "vwap": 10.5,
                "shortable": False, "borrow_fee_pct": 1.0, "squeeze_risk": "low",
            },
            "yankos",
        )
        self.assertEqual(perspective["preferred_side"], "short")
        self.assertEqual(perspective["readiness"], "blocked")
        self.assertIn("shortability", perspective["unresolved_short_gates"])

    def test_stale_primary_data_is_not_valid_evidence(self) -> None:
        catalog = playbook_framework.load_catalog(ROOT / "playbook_catalog_v2.json")
        playbook = playbook_framework.catalog_by_id(catalog)["fundamental_revision_trend"]
        perspective = playbook_framework.evaluate_perspective(
            playbook,
            {"playbook_id": playbook["playbook_id"], "status": "shadow", "weight": 1.0},
            {
                "ticker": "TEST", "fresh_primary_data": False,
                "revenue_growth": 0.3, "earnings_growth": 0.4,
                "catalyst_category_id": "old_filing",
            },
            "periklis",
        )
        self.assertIn("fresh_primary_data", perspective["missing_required_data"])
        self.assertIn("fundamental_revision", perspective["missing_required_data"])
        self.assertEqual(perspective["readiness"], "blocked")

    def test_shadow_or_research_playbook_never_escalates(self) -> None:
        exam = trader_playbook_assimilation.load_exam(ROOT / "trader_playbook_exam_v1.json")
        case = next(item for item in exam["cases"] if item["case_id"] == "vwap_short_shadow_only")
        for agent, filename in trader_playbook_assimilation.TRADERS.items():
            config = trader_playbook_assimilation.load_json(ROOT / filename)
            catalog = playbook_framework.load_catalog(ROOT / "playbook_catalog_v2.json")
            response = trader_playbook_assimilation.build_response(
                agent, case, catalog, config["playbook_portfolio"], config["playbooks"]
            )
            self.assertNotEqual(response["portfolio_status"], "active")
            self.assertEqual(response["action"], "perspective_only")
            self.assertFalse(response["execution_authority"])


if __name__ == "__main__":
    unittest.main()
