from __future__ import annotations

import sqlite3
import unittest

import research_control_plane as control
from research_asset_graph import graph_status, packet_lineage


class ResearchAssetGraphTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(":memory:")
        self.db.row_factory = sqlite3.Row
        control.init_schema(self.db)

    def tearDown(self):
        self.db.close()

    def test_graph_reports_health_and_missing_assets(self):
        observation, _ = control.record_observation(
            self.db, subject_id="issuer:1", capability="company.profile", provider="sec_edgar",
            source_ref="sec:1", observed_at="2026-09-18T00:00:00+00:00", run_id="run-1", payload={},
        )
        packet, _ = control.build_packet(
            self.db, kind="company_profile", subject_id="issuer:1", run_id="run-1",
            producer_name="company_profile_agent", producer_version="v1", quality_state="ready",
            payload={}, inputs=[("observation", observation["observation_id"])],
        )
        control.set_packet_freshness(
            self.db, packet_id=packet["packet_id"], knowledge_date="2026-09-18",
            stale_after="2026-10-18", must_refresh_if=("new_filing",),
        )
        result = graph_status(self.db)
        company = next(node for node in result["nodes"] if node["asset_key"] == "company_profile")
        self.assertEqual(company["status"], "healthy")
        self.assertEqual(company["packet_id"], packet["packet_id"])
        self.assertEqual(result["summary"]["missing"], 4)
        self.assertEqual(
            self.db.execute("SELECT COUNT(*) FROM ecosystem_asset_check_result").fetchone()[0], 6
        )

    def test_fundamental_analysis_requires_financial_packet_lineage(self):
        source, _ = control.record_observation(
            self.db, subject_id="issuer:1", capability="financial.company_facts", provider="sec_edgar",
            source_ref="sec:1", observed_at="2026-09-18T00:00:00+00:00", run_id="run-1", payload={},
        )
        financial, _ = control.build_packet(
            self.db, kind="financial_evidence", subject_id="issuer:1", run_id="run-1",
            producer_name="financial", producer_version="v1", payload={},
            inputs=[("observation", source["observation_id"])],
        )
        analysis, _ = control.build_packet(
            self.db, kind="fundamental_analysis", subject_id="issuer:1", run_id="run-1",
            producer_name="giannis", producer_version="v2", payload={},
            inputs=[("packet", financial["packet_id"])],
        )
        control.set_packet_freshness(
            self.db, packet_id=analysis["packet_id"], knowledge_date="2026-09-18",
            stale_after="2026-10-18", must_refresh_if=("new_filing",),
        )
        result = graph_status(self.db, subject_id="issuer:1", run_id="run-1")
        node = next(item for item in result["nodes"] if item["asset_key"] == "fundamental_analysis")
        self.assertEqual(node["status"], "healthy")
        lineage = packet_lineage(self.db, analysis["packet_id"])
        self.assertEqual({item["packet_id"] for item in lineage["packets"]}, {
            financial["packet_id"], analysis["packet_id"],
        })
        self.assertEqual(lineage["observations"][0]["observation_id"], source["observation_id"])

    def test_canonical_subject_lineage_rejects_cross_issuer_input(self):
        source, _ = control.record_observation(
            self.db, subject_id="issuer:2", capability="company.profile", provider="sec_edgar",
            source_ref="sec:2", observed_at="2026-09-18T00:00:00+00:00", run_id="run-1", payload={},
        )
        profile, _ = control.build_packet(
            self.db, kind="company_profile", subject_id="issuer:2", run_id="run-1",
            producer_name="profile", producer_version="v1", payload={},
            inputs=[("observation", source["observation_id"])],
        )
        financial, _ = control.build_packet(
            self.db, kind="financial_evidence", subject_id="issuer:1", run_id="run-1",
            producer_name="financial", producer_version="v1", payload={},
            inputs=[("packet", profile["packet_id"])],
        )
        control.set_packet_freshness(
            self.db, packet_id=financial["packet_id"], knowledge_date="2026-09-18",
            stale_after="2026-10-18",
        )
        node = next(
            item for item in graph_status(self.db, subject_id="issuer:1", run_id="run-1")["nodes"]
            if item["asset_key"] == "financial_evidence"
        )
        self.assertEqual(node["status"], "blocked")
        lineage_check = next(c for c in node["checks"] if c["check_name"] == "canonical_subject_lineage")
        self.assertEqual(lineage_check["status"], "failed")

    def test_graph_edges_reflect_actual_sector_handoff(self):
        common = {"subject_id": "issuer:1", "run_id": "run-1"}
        profile, _ = control.build_packet(
            self.db, kind="company_profile", producer_name="profile",
            producer_version="v1", payload={}, **common,
        )
        sector, _ = control.build_packet(
            self.db, kind="sector_context", producer_name="sector",
            producer_version="v1", payload={},
            inputs=[("packet", profile["packet_id"])], **common,
        )
        financial, _ = control.build_packet(
            self.db, kind="financial_evidence", producer_name="financial",
            producer_version="v1", payload={},
            inputs=[("packet", profile["packet_id"])], **common,
        )
        control.build_packet(
            self.db, kind="fundamental_analysis", producer_name="giannis",
            producer_version="v1", payload={},
            inputs=[("packet", financial["packet_id"]), ("packet", sector["packet_id"])],
            **common,
        )
        graph = graph_status(self.db, subject_id="issuer:1", run_id="run-1")
        self.assertEqual(
            {(edge["from"], edge["to"]) for edge in graph["edges"]},
            {
                ("company_profile", "sector_context"),
                ("company_profile", "financial_evidence"),
                ("sector_context", "fundamental_analysis"),
                ("financial_evidence", "fundamental_analysis"),
            },
        )


if __name__ == "__main__":
    unittest.main()
