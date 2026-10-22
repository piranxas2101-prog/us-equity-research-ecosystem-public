from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import unknown_data
import unknown_followup_benchmark


ROOT = Path(__file__).resolve().parents[1]


class UnknownFollowupBenchmarkTests(unittest.TestCase):
    def test_retry_is_bounded_and_nonretryable_stops_immediately(self) -> None:
        retryable = unknown_followup_benchmark.run_failure_sequence(
            ["rate_limited", "rate_limited", "ok"]
        )
        auth = unknown_followup_benchmark.run_failure_sequence(["auth_failed", "ok"])
        endless = unknown_followup_benchmark.run_failure_sequence(["timeout"] * 10)
        self.assertEqual(retryable["attempt_count"], 3)
        self.assertTrue(retryable["recovered"])
        self.assertEqual(auth["attempt_count"], 1)
        self.assertEqual(endless["attempt_count"], 3)
        self.assertTrue(endless["exhausted"])

    def test_failure_matrix_covers_required_cases(self) -> None:
        result = unknown_followup_benchmark.failure_injection_report()
        self.assertEqual(result["status"], "pass")
        self.assertEqual(result["passed"], result["case_count"])
        self.assertFalse(result["live_requests_made"])
        self.assertEqual(
            {row["case_id"] for row in result["rows"]},
            set(unknown_followup_benchmark.FAILURE_CASES),
        )

    def test_shadow_grading_never_modifies_artifact(self) -> None:
        contract = unknown_data.load_contract(ROOT / "unknown_data_contract.json")
        payload = {
            "run_id": "run_fixture",
            "shortlist": [
                {
                    "ticker": "ABC",
                    "share_price": 5,
                    "market_cap": None,
                    "unknown_events": [],
                }
            ],
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "artifact.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            before = path.read_bytes()
            grade = unknown_followup_benchmark.shadow_grade(path, "babis", contract)
            self.assertGreater(grade["expected_missing_events"], 0)
            self.assertEqual(grade["observable_contract_conformance_pct"], 0.0)
            self.assertEqual(path.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
