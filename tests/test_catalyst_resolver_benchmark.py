from __future__ import annotations

import json
import unittest
from pathlib import Path

import catalyst_content_review


ROOT = Path(__file__).resolve().parents[1]


class CatalystResolverBenchmarkTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fixtures = json.loads(
            (ROOT / "tests" / "fixtures" / "catalyst_resolver_labelled.json").read_text(
                encoding="utf-8"
            )
        )

    def test_labelled_fixture_is_classified_without_trade_authority(self) -> None:
        result = catalyst_content_review.evaluate_labelled_fixture(self.fixtures)
        self.assertEqual(result["category_accuracy_pct"], 100.0)
        self.assertEqual(result["false_direction_count"], 0)
        self.assertFalse(result["execution_authority"])
        self.assertTrue(
            all(not row["result"]["automatic_trade_direction"] for row in result["rows"])
        )

    def test_generic_source_remains_unknown_with_reason(self) -> None:
        result = catalyst_content_review.resolve_text("A report and exhibit were furnished.")
        self.assertEqual(result["status"], "remain_unknown_with_reason")
        self.assertEqual(result["reason"], "no_taxonomy_evidence")


if __name__ == "__main__":
    unittest.main()
