from __future__ import annotations

import unittest

import agent_run_audit
import priced_in_assessment
import unknown_data


class PricedInAssessmentTests(unittest.TestCase):
    def complete_row(self) -> dict:
        return {
            "ticker": "TEST",
            "share_price": 10.0,
            "forward_pe": 12.0,
            "trailing_pe": 15.0,
            "price_to_sales": 2.0,
            "price_to_book": 1.8,
            "revenue_growth": 0.25,
            "earnings_growth": 0.30,
            "profit_margin": 0.18,
            "operating_margin": 0.16,
            "cash": 200.0,
            "debt": 100.0,
            "catalyst_direction": "positive",
            "source_base": {
                "snapshot_time": "2026-09-02T15:00:00Z",
                "primary_data_feed": "test_provider",
            },
        }

    def test_complete_evidence_produces_hashed_assessment(self) -> None:
        result = priced_in_assessment.assess(
            self.complete_row(),
            run_id="run_test",
            run_timestamp="2026-09-02T16:00:00Z",
        )
        self.assertEqual(result["assessment_status"], "assessed")
        self.assertIn(result["priced_in_status"], priced_in_assessment.ASSESSED_STATES)
        self.assertTrue(priced_in_assessment.is_evidence_backed(result))
        self.assertGreaterEqual(len(result["evidence"]), 5)
        self.assertFalse(result["fair_value_model"])
        self.assertFalse(result["price_target_produced"])

    def test_missing_timestamp_fails_closed_but_records_attempt(self) -> None:
        row = self.complete_row()
        row["source_base"].pop("snapshot_time")
        result = priced_in_assessment.assess(
            row,
            run_id="run_test",
            run_timestamp="2026-09-02T16:00:00Z",
        )
        self.assertEqual(result["priced_in_status"], "not_assessed")
        self.assertEqual(result["reason_code"], "stale")
        self.assertEqual(len(result["attempts"]), 1)

    def test_audit_detects_unattempted_universal_required_field(self) -> None:
        contract = unknown_data.load_contract()
        payload = {
            "results": [
                {
                    "ticker": "TEST",
                    "fresh_primary_data": True,
                    "fundamental_coverage": 90.0,
                    "forward_pe": 12.0,
                    "priced_in_status": "not_assessed",
                    "unknown_events": [
                        {
                            "field": "priced_in_status",
                            "attempts": 0,
                        }
                    ],
                }
            ]
        }
        result = agent_run_audit.required_field_liveness("petros", payload, contract)
        self.assertFalse(result["ok"])
        self.assertEqual(result["issues"][0]["field"], "priced_in_status")

    def test_audit_accepts_recorded_failed_attempt(self) -> None:
        contract = unknown_data.load_contract()
        payload = {
            "results": [
                {
                    "ticker": "TEST",
                    "fresh_primary_data": True,
                    "fundamental_coverage": 90.0,
                    "forward_pe": 12.0,
                    "priced_in_status": "not_assessed",
                    "unknown_events": [
                        {
                            "field": "priced_in_status",
                            "attempts": 1,
                        }
                    ],
                }
            ]
        }
        result = agent_run_audit.required_field_liveness("petros", payload, contract)
        self.assertTrue(result["ok"])

    def test_trader_plan_fields_are_observed_in_nested_plan(self) -> None:
        contract = unknown_data.load_contract()
        payload = {
            "results": [{
                "fundamental_packet_hash": "packet_hash",
                "paper_trade_plan": {
                    "entry_confirmation": ["completed bar confirms entry"],
                    "invalidators": ["support breaks"],
                },
            }]
        }
        result = agent_run_audit.required_field_liveness("yankos", payload, contract)
        self.assertTrue(result["ok"])
        self.assertEqual(result["issue_count"], 0)

    def test_empty_trader_plan_fields_are_still_missing(self) -> None:
        contract = unknown_data.load_contract()
        payload = {
            "results": [{
                "fundamental_packet_hash": "packet_hash",
                "paper_trade_plan": {"entry_confirmation": [], "invalidators": []},
            }]
        }
        result = agent_run_audit.required_field_liveness("periklis", payload, contract)
        self.assertEqual({item["field"] for item in result["issues"]}, {"trigger", "invalidator"})


if __name__ == "__main__":
    unittest.main()
