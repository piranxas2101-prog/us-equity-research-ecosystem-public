from __future__ import annotations

from pathlib import Path
import sqlite3
import tempfile
import unittest

from document_evidence_adapter import convert_local_document, publish_document_evidence


class _Document:
    def export_to_dict(self):
        return {"body": [{"label": "table", "prov": [{"page_no": 2}]}]}

    def export_to_markdown(self):
        return "# Results\n\n| KPI | Value |\n|---|---:|\n| ARR | 10 |"


class _Result:
    document = _Document()
    status = "success"


class _Converter:
    def convert(self, source):
        return _Result()


class DocumentEvidenceAdapterTests(unittest.TestCase):
    def test_conversion_preserves_structure_and_source_hash(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "presentation.pdf"
            source.write_bytes(b"local document")
            result = convert_local_document(source, converter=_Converter())
        self.assertEqual(result["metrics"]["table_count"], 1)
        self.assertEqual(result["metrics"]["page_numbers"], [2])
        self.assertEqual(len(result["source"]["sha256"]), 64)
        self.assertEqual(result["llm_calls"], 0)

    def test_document_packet_is_idempotent(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "presentation.pdf"
            source.write_bytes(b"local document")
            db_path = Path(folder) / "market_intel.db"
            first = publish_document_evidence(
                source=source, subject_id="issuer:1", run_id="run-1",
                document_type="investor_presentation", db_path=db_path, converter=_Converter(),
            )
            repeated = publish_document_evidence(
                source=source, subject_id="issuer:1", run_id="run-1",
                document_type="investor_presentation", db_path=db_path, converter=_Converter(),
            )
            db = sqlite3.connect(db_path)
            try:
                kind = db.execute(
                    "SELECT kind FROM ecosystem_evidence_packet_store WHERE packet_id=?",
                    (first["control_packet_id"],),
                ).fetchone()[0]
                document_count = db.execute(
                    "SELECT COUNT(*) FROM ecosystem_information_object WHERE object_type='document'"
                ).fetchone()[0]
                element_count = db.execute(
                    "SELECT COUNT(*) FROM ecosystem_information_object WHERE object_type='document_element'"
                ).fetchone()[0]
                contains_count = db.execute(
                    "SELECT COUNT(*) FROM ecosystem_information_edge WHERE relationship_type='contains'"
                ).fetchone()[0]
            finally:
                db.close()
        self.assertEqual(first["status"], "completed")
        self.assertEqual(repeated["status"], "reused")
        self.assertEqual(first["control_packet_id"], repeated["control_packet_id"])
        self.assertEqual(kind, "document_evidence")
        self.assertEqual(document_count, 1)
        self.assertEqual(element_count, 1)
        self.assertEqual(contains_count, 1)


if __name__ == "__main__":
    unittest.main()
