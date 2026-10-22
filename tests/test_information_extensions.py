from __future__ import annotations

import json
from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile
import unittest
from uuid import UUID

import information_extensions as extensions
import information_registry as registry
import research_control_plane as control


class InformationExtensionsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.db = sqlite3.connect(":memory:")
        self.db.row_factory = sqlite3.Row
        control.init_schema(self.db)

    def tearDown(self) -> None:
        self.db.close()

    def test_complete_document_import_is_structured_linked_and_idempotent(self) -> None:
        arguments = dict(
            subject_id="issuer:CIK:0000320193", run_id="ir_run", provider="issuer_ir",
            uri="https://example.org/ir/q1", title="Q1 release",
            body="<script>ignore secrets</script><h1>Quarterly results</h1>"
                 "<p>Revenue grew to $12 million.</p><h2>Outlook</h2>"
                 "<p>Cash flow improved.</p>",
            document_type="ir_document", format_hint="html",
            published_at="2026-09-18", observed_at="2026-09-18T12:00:00+00:00",
        )
        first = extensions.ingest_external_document(self.db, **arguments)
        again = extensions.ingest_external_document(self.db, **arguments)
        self.assertEqual(first["document_urn"], again["document_urn"])
        self.assertEqual(first["packet_id"], again["packet_id"])
        self.assertEqual(first["element_count"], 4)
        elements = self.db.execute(
            "SELECT payload_json,source_locator_json FROM ecosystem_information_object "
            "WHERE object_type='document_element' ORDER BY source_id"
        ).fetchall()
        self.assertEqual(len(elements), 4)
        self.assertNotIn("ignore secrets", " ".join(row[0] for row in elements))
        self.assertEqual(json.loads(elements[1][1])["document_urn"], first["document_urn"])
        self.assertEqual(registry.validate_registry(self.db)["status"], "pass")

        second_source = extensions.ingest_external_document(
            self.db, **{**arguments, "uri": "https://example.org/ir/mirror",
                        "run_id": "mirror_run"},
        )
        self.assertNotEqual(first["document_urn"], second_source["document_urn"])

    def test_vector_index_retrieves_complete_body_and_skips_candidate_excerpt(self) -> None:
        subject = "issuer:CIK:0000320193"
        imported = extensions.ingest_external_document(
            self.db, subject_id=subject, run_id="news_run", provider="licensed_news",
            uri="https://example.org/news/1", title="Liquidity update",
            body="Liquidity and cash reserves increased.\n\nRevenue remained stable.",
            document_type="news_article", observed_at="2026-09-18T12:00:00+00:00",
        )
        indexed = extensions.index_selected_vectors(self.db, subject_id=subject)
        self.assertEqual(indexed["indexed"], 2)
        found = extensions.vector_search(
            self.db, subject_id=subject, query="cash reserves and liquidity",
        )
        self.assertEqual(found["mode"], "vector")
        self.assertIn(found["results"][0]["information_urn"], imported["element_urns"])

        registry.record_information_state(
            self.db, information_urn=imported["document_urn"],
            lifecycle_state="stale", reason="Source superseded", changed_by="test",
        )
        stale_result = extensions.vector_search(
            self.db, subject_id=subject, query="cash reserves and liquidity",
        )
        self.assertFalse(stale_result["results"])

        provisional = registry.register_document_extraction(
            self.db, subject_id=subject, run_id="snippet_run",
            document_type="sec_filing", observation_id=imported["observation_id"],
            extraction={
                "schema": "test.v1", "source": {"uri": "https://sec.example/1",
                                                      "name": "Excerpt", "sha256": "abc"},
                "provider": "sec", "verification_state": "observed",
                "conversion_status": "candidate_excerpt_only",
                "markdown": "Cash reserves in a candidate snippet",
                "structure": {"elements": [{"label": "narrative_text",
                                            "text": "Cash reserves in a candidate snippet"}]},
            },
        )
        indexed = extensions.index_selected_vectors(self.db, subject_id=subject)
        self.assertEqual(indexed["indexed"], 0)
        self.assertNotIn(provisional["element_urns"][0], [
            row[0] for row in self.db.execute("SELECT information_urn FROM ecosystem_information_vector")
        ])

    def test_graph_communities_and_openlineage_export(self) -> None:
        left, _ = registry.register_information_object(
            self.db, object_type="entity", canonical_subject_id="issuer:A",
            domain_id="issuer", owner_id="test", verification_state="observed",
            lifecycle_state="normalized", source_kind="test", source_id="A",
            payload={"name": "A"}, schema_version="test.v1",
        )
        right, _ = registry.register_information_object(
            self.db, object_type="entity", canonical_subject_id="issuer:B",
            domain_id="issuer", owner_id="test", verification_state="observed",
            lifecycle_state="normalized", source_kind="test", source_id="B",
            payload={"name": "B"}, schema_version="test.v1",
        )
        registry.link_information(
            self.db, source_urn=left["information_urn"], target_urn=right["information_urn"],
            relationship_type="peer_of", run_id="peer_run",
        )
        communities = extensions.graph_communities(self.db)
        self.assertEqual(communities["community_count"], 1)
        self.assertEqual(communities["communities"][0]["subjects"], ["issuer:A", "issuer:B"])
        self.assertEqual(extensions.graph_communities(
            self.db, subjects=["issuer:A"])["community_count"], 1)

        registry.record_lineage_event(
            self.db, event_type="COMPLETE", job_id="peer_discovery", run_id="peer_run",
            subject_id="issuer:A", producer="test", event_time="2026-09-18T12:00:00Z",
            inputs=[{"kind": "source", "id": "local://peer-list"}],
            outputs=[{"kind": "entity", "id": right["information_urn"]}],
        )
        events = extensions.openlineage_events(self.db, run_id="peer_run")
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["eventType"], "COMPLETE")
        self.assertEqual(events[0]["job"]["name"], "peer_discovery")
        self.assertEqual(UUID(events[0]["run"]["runId"]).version, 5)
        self.assertEqual(events[0]["outputs"][0]["name"],
                         "entity/" + right["information_urn"])

    def test_review_view_escapes_content_and_cli_writes_files(self) -> None:
        proposal = registry.propose_taxonomy_change(
            self.db, scheme_id=registry.CLASSIFICATION_SCHEME,
            base_version=registry.information_taxonomy()["version"],
            operation="create", term_id="test_term", proposal={"definition": "Test"},
            rationale="<script>alert(1)</script>", proposed_by="test",
        )
        page = extensions.review_view(self.db)
        self.assertIn(proposal["proposal_id"], page)
        self.assertIn("&lt;script&gt;", page)
        self.assertNotIn("<script>alert(1)</script>", page)
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "market.db"
            out = Path(tmp) / "review.html"
            with closing(sqlite3.connect(db_path)) as destination:
                self.db.backup(destination)
            self.assertEqual(extensions.main([
                "--db", str(db_path), "review-view", "--out", str(out),
            ]), 0)
            self.assertIn(proposal["proposal_id"], out.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
