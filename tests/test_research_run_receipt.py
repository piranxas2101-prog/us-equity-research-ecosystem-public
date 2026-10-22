from __future__ import annotations

import sqlite3
import unittest

import research_control_plane as control
from research_run_receipt import build_receipt, render_markdown


class ResearchRunReceiptTests(unittest.TestCase):
    def test_receipt_evaluates_every_packet_in_the_run(self):
        db = sqlite3.connect(":memory:")
        db.row_factory = sqlite3.Row
        control.init_schema(db)
        source, _ = control.record_observation(
            db, subject_id="issuer:1", capability="company.profile", provider="registry",
            source_ref="profile:1", observed_at="2026-09-18T00:00:00+00:00",
            run_id="run-1", payload={},
        )
        profile, _ = control.build_packet(
            db, kind="company_profile", subject_id="issuer:1", run_id="run-1",
            producer_name="profile", producer_version="v1", quality_state="ready", payload={},
            inputs=[("observation", source["observation_id"])],
        )
        control.set_packet_freshness(
            db, packet_id=profile["packet_id"], knowledge_date="2026-09-18",
            stale_after="2026-09-19",
        )
        unchecked, _ = control.build_packet(
            db, kind="custom_evidence", subject_id="issuer:1", run_id="run-1",
            producer_name="custom", producer_version="v1", quality_state="ready",
            payload={}, inputs=[],
        )
        receipt = build_receipt(db, "run-1")
        try:
            self.assertEqual(len(receipt["packet_health"]), 2)
            custom = next(item for item in receipt["packet_health"] if item["packet_id"] == unchecked["packet_id"])
            self.assertEqual(custom["status"], "blocked")
            self.assertEqual(receipt["packet_health_summary"]["blocked"], 1)
            self.assertEqual(receipt["status"], "review_required")
            reasons = receipt["review_reasons"]
            self.assertEqual(reasons["failed_error_checks"], sum(
                row["count"] for row in receipt["asset_checks"] if row["status"] == "failed" and row["severity"] == "error"))
            self.assertEqual(sum(reasons["actionable_gaps_by_reason"].values()), receipt["actionable_gap_total"])
            self.assertIn("Why review is required", render_markdown(receipt))
        finally:
            db.close()


if __name__ == "__main__":
    unittest.main()
