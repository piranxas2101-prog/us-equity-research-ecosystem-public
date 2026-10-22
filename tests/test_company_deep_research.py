import unittest

import company_deep_research as deep


class CompanyDeepResearchTests(unittest.TestCase):
    def row(self):
        return {
            "ticker": "TEST", "company": "Test Co", "petros_verdict": "candidate",
            "share_price": 100, "revenue_growth": .20, "operating_margin": .18,
            "free_cash_flow_ttm": 1000, "fresh_primary_data": True,
            "source_base": {"primary_data_feed": "SEC EDGAR", "snapshot_time": "2026-01-01"},
            "deterministic_valuation": {
                "reverse_dcf": {"status": "assessed", "implied_fcf_growth": .15,
                                "discount_rate": .11, "terminal_growth": .025},
                "valuation_range": {"low": 70, "base": 110, "high": 150},
            },
        }

    def test_build_selects_only_candidate_and_manual_review(self):
        rows = [self.row(), {**self.row(), "ticker": "MAN", "petros_verdict": "manual_review"},
                {**self.row(), "ticker": "NO", "petros_verdict": "reject"}]
        payload = deep.build({"run_id": "run_x", "results": rows})
        self.assertEqual([row["ticker"] for row in payload["results"]], ["TEST", "MAN"])
        self.assertFalse(payload["safety"]["execution_authority"])

    def test_missing_kpis_are_explicit_and_never_inferred(self):
        result = deep.analyze(self.row())
        registry = result["modules"]["kpi_registry"]
        self.assertEqual(registry["status"], "evidence_required")
        self.assertEqual(registry["observations"], [])
        self.assertIn("never infer", registry["missing_policy"])

    def test_thesis_thresholds_and_value_zones_are_deterministic(self):
        result = deep.analyze(self.row())
        zones = result["modules"]["valuation_zones"]["zones"]
        self.assertEqual(zones["margin_of_safety"]["max_price"], 82.5)
        prove = result["modules"]["thesis_tracker"]["prove_criteria"]
        self.assertTrue(any(item["metric"] == "revenue_growth" for item in prove))

    def test_reverse_dcf_does_not_claim_assessment_without_inputs(self):
        row = self.row()
        row["deterministic_valuation"] = {}
        module = deep.reverse_dcf_module(row)
        self.assertEqual(module["status"], "not_assessed")
        self.assertIn("unavailable", module["interpretation"])


if __name__ == "__main__":
    unittest.main()
