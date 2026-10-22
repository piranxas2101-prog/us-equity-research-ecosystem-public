from __future__ import annotations

from pathlib import Path
import sqlite3
import tempfile
import unittest

from edgartools_evidence_adapter import normalize_filing_catalog, publish_filing_catalog


class _Client:
    def filing_catalog(self, identifier, forms):
        return [
            {"form": "10-K", "filing_date": "2025-02-01", "accession_number": "a1", "is_xbrl": True},
            {"form": "10-Q", "filing_date": "2026-11-01", "accession_number": "future"},
            {"form": "S-1", "filing_date": "2025-01-01", "accession_number": "excluded"},
        ]


class EdgarToolsEvidenceAdapterTests(unittest.TestCase):
    def test_catalog_is_point_in_time_and_form_limited(self):
        result = normalize_filing_catalog(
            _Client().filing_catalog("ABC", ("10-K", "10-Q")), identifier="ABC",
            as_of="2025-12-31", forms=("10-K", "10-Q"),
        )
        self.assertEqual([row["accession_number"] for row in result["filings"]], ["a1"])
        self.assertTrue(result["sec_precedence_enforced"])

    def test_catalog_publishes_reusable_control_packet(self):
        with tempfile.TemporaryDirectory() as folder:
            db_path = Path(folder) / "market_intel.db"
            first = publish_filing_catalog(
                identifier="ABC", subject_id="issuer:1", run_id="run-1", as_of="2025-12-31",
                db_path=db_path, client=_Client(), forms=("10-K", "10-Q"),
            )
            repeated = publish_filing_catalog(
                identifier="ABC", subject_id="issuer:1", run_id="run-1", as_of="2025-12-31",
                db_path=db_path, client=_Client(), forms=("10-K", "10-Q"),
            )
            db = sqlite3.connect(db_path)
            try:
                stored = db.execute(
                    "SELECT kind,quality_state FROM ecosystem_evidence_packet_store WHERE packet_id=?",
                    (first["control_packet_id"],),
                ).fetchone()
            finally:
                db.close()
        self.assertEqual(first["status"], "completed")
        self.assertEqual(repeated["status"], "reused")
        self.assertEqual(stored, ("sec_filing_catalog", "ready"))


if __name__ == "__main__":
    unittest.main()
