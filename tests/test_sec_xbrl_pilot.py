from __future__ import annotations

import json
import unittest
from pathlib import Path

import sec_xbrl_pilot


ROOT = Path(__file__).resolve().parents[1]


class SecXbrlPilotTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.payload = json.loads(
            (ROOT / "tests" / "fixtures" / "sec_companyfacts_point_in_time.json").read_text(
                encoding="utf-8"
            )
        )

    def test_cutoff_prevents_future_period_leakage(self) -> None:
        result = sec_xbrl_pilot.extract_companyfacts(self.payload, "2025-07-15")
        self.assertEqual(result["fields"]["cash"]["value"], 100)
        self.assertEqual(result["fields"]["cash"]["accession"], "0001")

    def test_exact_acceptance_time_controls_same_day_visibility(self) -> None:
        before = sec_xbrl_pilot.extract_companyfacts(
            self.payload,
            "2025-08-01T12:00:00Z",
            accepted_by_accession={"0002": "2025-08-01T13:00:00Z"},
        )
        after = sec_xbrl_pilot.extract_companyfacts(
            self.payload,
            "2025-08-01T14:00:00Z",
            accepted_by_accession={"0002": "2025-08-01T13:00:00Z"},
        )
        self.assertEqual(before["fields"]["cash"]["value"], 100)
        self.assertEqual(after["fields"]["cash"]["value"], 150)
        self.assertEqual(after["fields"]["cash"]["availability_basis"], "accepted_timestamp")

    def test_missing_concepts_remain_reason_coded_unknown(self) -> None:
        result = sec_xbrl_pilot.extract_companyfacts(self.payload, "2025-08-02")
        unknown = {item["field"]: item for item in result["unknown_events"]}
        self.assertIn("debt", unknown)
        self.assertEqual(unknown["debt"]["reason_code"], "provider_no_coverage")

    def test_sec_always_wins_and_conflict_is_visible(self) -> None:
        sec = sec_xbrl_pilot.extract_companyfacts(self.payload, "2025-08-02")
        merged = sec_xbrl_pilot.merge_sec_primary(
            sec,
            {
                "provider": "fmp",
                "as_of": "2025-08-02",
                "values": {"cash": 777, "debt": 55},
            },
        )
        self.assertEqual(merged["values"]["cash"]["value"], 150)
        self.assertEqual(merged["values"]["cash"]["provider"], "sec_edgar")
        self.assertEqual(merged["conflicts"][0]["winner"], "sec_edgar")
        self.assertTrue(merged["values"]["debt"]["shadow_only"])
        self.assertFalse(merged["promotion_eligible"])

    def test_ifrs_local_currency_core_facts_are_preserved(self) -> None:
        def fact(value: int) -> dict:
            return {"units": {"BRL": [{"val": value, "form": "20-F", "start": "2025-01-01",
                                        "end": "2025-12-31", "filed": "2026-03-01", "accn": "ifrs1"}]}}
        payload = {"cik": 123, "facts": {"ifrs-full": {
            "Revenue": fact(100), "ProfitLoss": fact(10),
            "CashAndCashEquivalents": fact(20),
            "CashFlowsFromUsedInOperatingActivities": fact(12),
            "Equity": fact(50),
        }}}
        result = sec_xbrl_pilot.extract_companyfacts(payload, "2026-09-20")
        self.assertEqual(result["reporting_currency"], "BRL")
        self.assertEqual(result["coverage"]["resolved"], 5)
        self.assertEqual({fact["unit"] for fact in result["fields"].values()}, {"BRL"})

    def test_newer_concept_wins_over_stale_legacy_concept(self) -> None:
        payload = {"facts": {"us-gaap": {
            "Revenues": {"units": {"USD": [{"val": 10, "form": "10-K", "end": "2023-12-31", "filed": "2024-02-01"}]}},
            "RevenueFromContractWithCustomerExcludingAssessedTax": {"units": {"USD": [{"val": 20, "form": "10-K", "end": "2025-12-31", "filed": "2026-02-01"}]}},
        }}}
        result = sec_xbrl_pilot.select_fact(payload, sec_xbrl_pilot.FIELD_CONCEPTS["revenue"], "2026-09-20")
        self.assertEqual(result["value"], 20)

    def test_reporting_currency_prefers_current_income_period(self) -> None:
        payload = {"facts": {"ifrs-full": {
            "Revenue": {"units": {
                "USD": [{"val": 100, "form": "20-F", "end": "2022-12-31", "filed": "2023-03-01"}],
                "EUR": [{"val": 120, "form": "20-F", "end": "2025-12-31", "filed": "2026-03-01"}],
            }},
            "CashAndCashEquivalents": {"units": {
                "USD": [{"val": 10, "form": "20-F", "end": "2022-12-31", "filed": "2023-03-01"}],
            }},
        }}}
        result = sec_xbrl_pilot.extract_companyfacts(payload, "2026-09-20")
        self.assertEqual(result["reporting_currency"], "EUR")
        self.assertEqual(result["fields"]["revenue"]["value"], 120)


if __name__ == "__main__":
    unittest.main()
