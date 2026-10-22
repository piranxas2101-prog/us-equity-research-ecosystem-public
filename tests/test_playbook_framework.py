from __future__ import annotations

import json
import unittest
from pathlib import Path

import playbook_framework


ROOT = Path(__file__).resolve().parents[1]


class PlaybookFrameworkTests(unittest.TestCase):
    def setUp(self) -> None:
        self.catalog = playbook_framework.load_catalog(ROOT / "playbook_catalog_v2.json")

    def test_catalog_is_broad_and_symmetric(self) -> None:
        playbooks = self.catalog["playbooks"]
        sides = {side for item in playbooks for side in item["sides"]}
        families = {item["family"] for item in playbooks}
        self.assertGreaterEqual(len(playbooks), 16)
        self.assertTrue({"long", "short", "flat"}.issubset(sides))
        self.assertGreaterEqual(len(families), 12)

    def test_both_trader_portfolios_cover_the_full_catalog(self) -> None:
        catalog_ids = {item["playbook_id"] for item in self.catalog["playbooks"]}
        for filename in ("yankos_playbooks.json", "periklis_playbooks.json"):
            payload = json.loads((ROOT / filename).read_text(encoding="utf-8"))
            portfolio = payload["playbook_portfolio"]
            playbook_framework.validate_portfolio(self.catalog, portfolio)
            self.assertEqual({item["playbook_id"] for item in portfolio}, catalog_ids)

    def test_overextended_symbol_gets_a_short_counterview_without_execution_authority(self) -> None:
        playbook = playbook_framework.catalog_by_id(self.catalog)["mean_reversion_overextension"]
        perspective = playbook_framework.evaluate_perspective(
            playbook,
            {"playbook_id": playbook["playbook_id"], "status": "shadow", "weight": 1.2},
            {
                "ticker": "TEST",
                "instrument_type": "common",
                "risk_level": "Medium",
                "rsi_14": 76,
                "return_20d_pct": 24,
                "price_position_20d_pct": 96,
                "atr_14_pct": 5,
                "macd_histogram": -0.1,
                "technical_timing": "already_extended",
                "shortable": True,
                "borrow_fee_pct": 1.0,
                "squeeze_risk": "low",
                "catalyst_timing": "none scheduled",
            },
            "yankos",
        )
        self.assertEqual(perspective["preferred_side"], "short")
        self.assertEqual(perspective["readiness"], "strong_match")
        self.assertFalse(perspective["execution_authority"])

    def test_missing_intraday_inputs_remain_visible_instead_of_becoming_a_signal(self) -> None:
        playbook = playbook_framework.catalog_by_id(self.catalog)["vwap_reclaim_rejection"]
        perspective = playbook_framework.evaluate_perspective(
            playbook,
            {"playbook_id": playbook["playbook_id"], "status": "shadow"},
            {"ticker": "TEST", "risk_level": "Medium", "technical_timing": "neutral"},
            "periklis",
        )
        self.assertEqual(perspective["readiness"], "needs_data")
        self.assertIn("current_session_15m_bars", perspective["missing_required_data"])
        self.assertIn("vwap", perspective["missing_required_data"])

    def test_agent_specialization_changes_priority_without_changing_raw_market_view(self) -> None:
        playbook = playbook_framework.catalog_by_id(self.catalog)["support_bounce_swing"]
        row = {
            "ticker": "TEST", "risk_level": "Medium", "rsi_14": 34,
            "price_position_20d_pct": 20, "atr_14_pct": 4,
            "macd_histogram": 0.1, "technical_timing": "near_lows_reversal",
        }
        specialist = playbook_framework.evaluate_perspective(
            playbook, {"playbook_id": playbook["playbook_id"], "status": "active", "weight": 1.3}, row, "yankos"
        )
        observer = playbook_framework.evaluate_perspective(
            playbook, {"playbook_id": playbook["playbook_id"], "status": "research", "weight": 0.7}, row, "periklis"
        )
        self.assertEqual(specialist["match_score"], observer["match_score"])
        self.assertGreater(specialist["agent_priority_score"], observer["agent_priority_score"])

    def test_executable_row_receives_conflicting_alternative_views(self) -> None:
        executable = [{"ticker": "TEST", "trade_direction": "long"}]
        perspectives = [
            {
                "ticker": "TEST", "playbook_id": "long_view", "portfolio_status": "active",
                "preferred_side": "long", "match_score": 7.0, "readiness": "strong_match",
                "missing_required_data": [],
            },
            {
                "ticker": "TEST", "playbook_id": "short_view", "portfolio_status": "shadow",
                "preferred_side": "short", "match_score": 8.0, "readiness": "strong_match",
                "missing_required_data": [],
            },
        ]
        playbook_framework.attach_alternative_perspectives(executable, perspectives)
        self.assertEqual(executable[0]["alternative_perspectives"][0]["playbook_id"], "short_view")
        self.assertTrue(executable[0]["alternative_perspectives"][0]["conflicts_with_primary"])
        self.assertEqual(executable[0]["playbook_breadth"]["perspectives_reviewed"], 2)


if __name__ == "__main__":
    unittest.main()
