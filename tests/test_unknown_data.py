from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import giannis_rule_based_from_shortlist
import unknown_data
import unknown_inventory


ROOT = Path(__file__).resolve().parents[1]


class UnknownDataTests(unittest.TestCase):
    def setUp(self) -> None:
        self.contract = unknown_data.load_contract(ROOT / "unknown_data_contract.json")

    def test_contract_covers_all_agents_and_reason_codes(self) -> None:
        self.assertEqual(unknown_data.validate_contract(self.contract), [])
        self.assertEqual(set(self.contract["agents"]), set(unknown_data.EXPECTED_AGENTS))

    def test_not_applicable_forward_pe_does_not_reduce_coverage_or_score(self) -> None:
        row = {
            "ticker": "LOSS",
            "net_income_ttm": -10.0,
            "forward_pe": None,
            "revenue_growth": 0.1,
            "earnings_growth": -0.2,
            "gross_margin": 0.4,
            "profit_margin": -0.1,
            "roe": -0.2,
            "debt_to_equity": 0.5,
            "current_ratio": 2.0,
            "cash": 100.0,
            "debt": 10.0,
            "fresh_primary_data": True,
        }
        annotated = unknown_data.annotate_row(row, "giannis", contract=self.contract)
        event = next(item for item in annotated["unknown_events"] if item["field"] == "forward_pe")
        self.assertEqual(event["reason_code"], "not_applicable")
        self.assertFalse(event["coverage_included"])
        self.assertEqual(event["penalty_weight"], 0.0)
        self.assertNotIn("forward_pe", annotated["unknown_fields"])

    def test_missing_provider_field_is_actionable_and_reason_coded(self) -> None:
        row = {"ticker": "MISS", "share_price": 10.0, "market_cap": None}
        annotated = unknown_data.annotate_row(row, "babis", contract=self.contract)
        event = next(item for item in annotated["unknown_events"] if item["field"] == "market_cap")
        self.assertEqual(event["reason_code"], "provider_no_coverage")
        self.assertTrue(event["retryable"])
        self.assertTrue(event["coverage_included"])
        self.assertTrue(event["unknown_event_id"].startswith("unknown_"))

    def test_rate_limit_is_shared_nonretryable_gap(self) -> None:
        row = {
            "ticker": "LIMIT",
            "share_price": None,
            "source_base": {
                "market_data_provider_errors": [
                    {"provider": "polygon", "status": "shared_cooldown:rate_limited"}
                ]
            },
        }
        annotated = unknown_data.annotate_row(row, "babis", contract=self.contract)
        event = next(item for item in annotated["unknown_events"] if item["field"] == "share_price")
        self.assertEqual(event["reason_code"], "provider_rate_limited")
        self.assertFalse(event["retryable"])
        self.assertEqual(event["default_action"], "respect_shared_cooldown_then_targeted_retry")

    def test_daily_budget_exhaustion_waits_for_reset(self) -> None:
        row = {
            "ticker": "BUDGET",
            "share_price": None,
            "source_base": {
                "market_data_provider_errors": [
                    {"provider": "fmp", "status": "shared_daily_budget_exhausted"}
                ]
            },
        }
        annotated = unknown_data.annotate_row(row, "babis", contract=self.contract)
        event = next(item for item in annotated["unknown_events"] if item["field"] == "share_price")
        self.assertEqual(event["reason_code"], "provider_daily_budget_exhausted")
        self.assertFalse(event["retryable"])

    def test_babis_literal_unknowns_are_distinguished(self) -> None:
        row = {
            "ticker": "TEST", "share_price": 5.0, "market_cap": 100.0,
            "cash": 10.0, "debt": 1.0, "sector_id": "technology",
            "industry_id": "technology.software", "catalyst_category_id": "unknown",
            "catalyst_direction": "unknown", "catalyst_evidence": [{"source_kind": "sec_form"}],
            "atm_shelf_warrants_convertibles": "UNKNOWN", "concentration_risk": "UNKNOWN",
        }
        annotated = unknown_data.annotate_row(row, "babis", contract=self.contract)
        reasons = {item["field"]: item["reason_code"] for item in annotated["unknown_events"]}
        self.assertEqual(reasons["catalyst_category_id"], "insufficient_source_detail")
        self.assertEqual(reasons["atm_shelf_warrants_convertibles"], "insufficient_source_detail")
        concentration = next(item for item in annotated["unknown_events"] if item["field"] == "concentration_risk")
        self.assertEqual(concentration["requirement"], "diagnostic_only")
        self.assertFalse(concentration["coverage_included"])

    def test_giannis_penalty_uses_weighted_events_not_raw_unknown_count(self) -> None:
        base = {
            "ticker": "LOSS", "share_price": 5.0, "risk_level": "Low",
            "fundamental_attention_score": 75.0, "cash": 10.0, "debt": 1.0,
            "cash_burn_proxy_fcf": 1.0, "cash_burn_proxy_ocf": 1.0,
            "revenue_growth": 0.1, "earnings_growth": -0.2, "gross_margin": 0.4,
            "profit_margin": -0.1, "roe": -0.2, "debt_to_equity": 0.5,
            "current_ratio": 2.0, "forward_pe": None, "net_income_ttm": -10.0,
            "fresh_primary_data": True, "catalyst_category_id": "unknown",
            "catalyst_direction": "unknown", "catalyst_classification_confidence": "low",
        }
        result = giannis_rule_based_from_shortlist.score_row(base, run_id="run_unknown_test")
        pe = next(item for item in result["unknown_events"] if item["field"] == "forward_pe")
        self.assertEqual(pe["reason_code"], "not_applicable")
        self.assertNotIn("forward_pe", result["unknown_fields"])
        self.assertEqual(result["unknown_summary"]["penalty_units"], 0.0)

    def test_inventory_reconstructs_historical_events_without_rewrite(self) -> None:
        babis = {"run_id": "run_1", "shortlist": [{
            "ticker": "ABC", "share_price": 2.0, "market_cap": None,
            "cash": 1.0, "debt": 0.0, "sector_id": "technology",
            "industry_id": "technology.software", "catalyst_category_id": "unknown",
            "catalyst_direction": "unknown", "atm_shelf_warrants_convertibles": "UNKNOWN",
            "concentration_risk": "UNKNOWN"
        }]}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "babis.json"
            original = json.dumps(babis, sort_keys=True)
            path.write_text(original, encoding="utf-8")
            records, summary = unknown_inventory.collect_artifact_events(path, "babis", self.contract)
            self.assertGreaterEqual(len(records), 4)
            self.assertEqual(summary["row_count"], 1)
            self.assertEqual(path.read_text(encoding="utf-8"), original)


if __name__ == "__main__":
    unittest.main()
