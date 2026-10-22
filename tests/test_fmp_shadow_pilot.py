from __future__ import annotations

import unittest

import fmp_shadow_pilot


class FmpShadowPilotTests(unittest.TestCase):
    def test_payload_mapping_redacts_values_and_does_not_fake_forward_pe(self) -> None:
        original = fmp_shadow_pilot.query_endpoint
        try:
            fmp_shadow_pilot.query_endpoint = lambda endpoint, symbol, key: {
                "status": "ok",
                "latency_ms": 1.0,
                "row": {
                    "growthRevenue": 0.2,
                    "growthNetIncome": 0.3,
                    "grossProfitMarginTTM": 0.4,
                    "netProfitMarginTTM": 0.1,
                    "returnOnEquityTTM": 0.2,
                    "debtEquityRatioTTM": 0.5,
                    "currentRatioTTM": 1.5,
                },
            }
            result = fmp_shadow_pilot.coverage_for_symbol("ABC", "secret")
        finally:
            fmp_shadow_pilot.query_endpoint = original
        self.assertEqual(result["resolved"], 7)
        self.assertFalse(result["field_coverage"]["forward_pe"]["available"])
        self.assertTrue(
            all(item["raw_value_redacted"] for item in result["field_coverage"].values())
        )


if __name__ == "__main__":
    unittest.main()
