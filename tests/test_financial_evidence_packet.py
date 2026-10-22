from __future__ import annotations

import unittest

from financial_evidence_packet import readiness_gate


class FinancialReadinessTests(unittest.TestCase):
    def packet(self, period_end: str, units: list[str] | None = None) -> dict:
        return {
            "subject_id": "sec:cik:0000000001", "ticker": "ABC",
            "as_of": "2026-09-20T00:00:00+00:00", "periods": [period_end],
            "units": units or ["BRL"], "values": {
                "revenue": {"value": 100, "period_end": period_end},
                "net_income": {"value": 10, "period_end": period_end},
                "cash": {"value": 20, "period_end": period_end},
                "operating_cash_flow": {"value": 12, "period_end": period_end},
                "stockholders_equity": {"value": 50, "period_end": period_end},
            },
        }

    def test_current_non_usd_filing_can_pass(self) -> None:
        self.assertTrue(readiness_gate(self.packet("2025-12-31"))["ready_for_fundamental_synthesis"])

    def test_stale_income_statement_is_blocked(self) -> None:
        result = readiness_gate(self.packet("2024-12-31"))
        self.assertIn("stale_income_statement_period", result["blockers"])

    def test_mixed_reporting_currencies_are_blocked(self) -> None:
        result = readiness_gate(self.packet("2025-12-31", ["USD", "BRL"]))
        self.assertIn("mixed_reporting_currencies", result["blockers"])


if __name__ == "__main__":
    unittest.main()
