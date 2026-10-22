from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import catalyst_content_review
import symbol_identity


class UnknownIdentityAndReviewQueueTests(unittest.TestCase):
    def test_sec_identity_is_exact_and_never_guessed(self) -> None:
        identity = symbol_identity.require_sec_identity("abc", {"ABC": 1234})
        self.assertEqual(identity["ticker"], "ABC")
        self.assertEqual(identity["cik"], 1234)
        self.assertFalse(identity["guessed"])
        with self.assertRaisesRegex(symbol_identity.SymbolIdentityError, "symbol_mapping_error"):
            symbol_identity.require_sec_identity("missing", {"ABC": 1234})

    def test_unknown_filing_is_deduplicated_into_non_executing_review_queue(self) -> None:
        source = {
            "subcategory_id": "sec_form_6_k",
            "event_name": "Foreign issuer report filed; content review required",
            "event_date": "2026-08-03",
            "source_kind": "sec_form",
            "source_url": "https://www.sec.gov/example",
            "sec_form": "6-K",
        }
        row = {
            "ticker": "ABC",
            "catalyst_trade_lenses": {
                "long": {"categories": [{"category_id": "unknown", "subcategories": [source]}]},
                "short": {"categories": [{"category_id": "unknown", "subcategories": [source]}]},
            },
        }
        queue = catalyst_content_review.build_queue([row], "run_1")
        self.assertEqual(queue["item_count"], 1)
        self.assertFalse(queue["execution_authority"])
        self.assertEqual(queue["items"][0]["reason_code"], "insufficient_source_detail")
        self.assertIn("automatic_trade_direction", queue["items"][0]["prohibited_outputs"])

    def test_queue_write_preserves_identical_frozen_payload(self) -> None:
        queue = catalyst_content_review.build_queue([], "run_1")
        with tempfile.TemporaryDirectory() as directory:
            run_path = Path(directory) / "run.json"
            latest_path = Path(directory) / "latest.json"
            catalyst_content_review.write_queue(queue, run_path, latest_path)
            self.assertEqual(run_path.read_bytes(), latest_path.read_bytes())


if __name__ == "__main__":
    unittest.main()
