from __future__ import annotations

import unittest
from collections import Counter
import hashlib
import json
import sqlite3

from software_filing_evidence_runner import extract_candidates, index_candidate_filing


class SoftwareFilingEvidenceTests(unittest.TestCase):
    def test_keyword_windows_are_candidates_not_metrics(self):
        html = "<html><body><p>Annual recurring revenue was discussed by management.</p><p>Net revenue retention remained an important metric.</p></body></html>"
        result = extract_candidates(html)
        self.assertIn("arr", result)
        self.assertIn("retention", result)
        self.assertIsInstance(result["arr"][0], str)

    def test_scripts_are_not_indexed(self):
        self.assertEqual(extract_candidates("<script>annual recurring revenue</script><p>No metric here.</p>"), {})

    def test_candidate_field_counter_counts_symbols_not_snippets(self):
        candidates = {"arr": ["one", "two"], "risk_factors": ["three"]}
        counts = Counter()
        counts.update(candidates.keys())
        self.assertEqual(counts, {"arr": 1, "risk_factors": 1})

    def test_candidate_excerpt_is_indexed_with_source_position_and_provisional_state(self):
        html = "<html><body><p>Annual recurring revenue was discussed by management.</p></body></html>"
        db = sqlite3.connect(":memory:")
        db.row_factory = sqlite3.Row
        try:
            result = index_candidate_filing(
                db, record={"ticker": "TEST", "cik": "123"},
                filing={"url": "https://www.sec.gov/example", "form": "10-K",
                        "filing_date": "2026-09-17"},
                fetched={"content": html,
                         "sha256": hashlib.sha256(html.encode()).hexdigest(),
                         "observed_at": "2026-09-18T00:00:00+00:00"},
                candidates=extract_candidates(html), run_id="candidate-run",
            )
            self.assertEqual(result["element_count"], 1)
            document = db.execute(
                "SELECT verification_state FROM ecosystem_information_object WHERE information_urn=?",
                (result["document_urn"],),
            ).fetchone()
            self.assertEqual(document[0], "provisional")
            element = db.execute(
                "SELECT source_locator_json FROM ecosystem_information_object WHERE information_urn=?",
                (result["element_urns"][0],),
            ).fetchone()
            locator = json.loads(element[0])
            self.assertEqual(locator["char_start"], 0)
            self.assertEqual(locator["field"], "arr")
        finally:
            db.close()


if __name__ == "__main__": unittest.main()
