from __future__ import annotations

import json
import sqlite3
import unittest

import information_registry as registry
import research_control_plane as control


class InformationRegistryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.db = sqlite3.connect(":memory:")
        self.db.row_factory = sqlite3.Row
        control.init_schema(self.db)

    def tearDown(self) -> None:
        self.db.close()

    def _observation_and_packet(self) -> tuple[dict, dict]:
        observation, _ = control.record_observation(
            self.db, subject_id="issuer:CIK:0000320193", capability="company.profile",
            provider="sec_edgar", source_ref="https://www.sec.gov/example",
            observed_at="2026-09-18T10:00:00+00:00", as_of="2026-09-17",
            run_id="run_info", payload={"name": "Example Corp"},
        )
        packet, _ = control.build_packet(
            self.db, kind="company_profile", subject_id=observation["subject_id"],
            run_id="run_info", producer_name="company_profile_agent", producer_version="1",
            payload={"overview": "Industrial software company."},
            inputs=[("observation", observation["observation_id"])], as_of="2026-09-17",
        )
        return observation, packet

    def _search_rows(self) -> list[tuple]:
        return [tuple(r) for r in self.db.execute(
            "SELECT rowid, information_urn, title FROM ecosystem_information_search ORDER BY rowid")]

    def test_search_index_is_updated_by_rowid_without_scanning_the_urn_column(self) -> None:
        statements: list[str] = []
        self.db.set_trace_callback(statements.append)
        self._observation_and_packet()
        self.db.set_trace_callback(None)
        # The FTS5 column information_urn is UNINDEXED: deleting by it scans the whole table.
        self.assertFalse([s for s in statements if "DELETE FROM ecosystem_information_search" in s
                          and "information_urn" in s])
        objects = {u: r for r, u in self.db.execute("SELECT rowid, information_urn FROM ecosystem_information_object")}
        searched = {u: r for r, u, _ in self._search_rows()}
        self.assertEqual(objects, searched)  # one search row per object, carrying the object's rowid

    def test_search_upsert_replaces_the_row_of_the_same_object(self) -> None:
        self._observation_and_packet()
        urn, rowid = self.db.execute("SELECT information_urn, rowid FROM ecosystem_information_object LIMIT 1").fetchone()
        before = len(self._search_rows())
        registry._search_upsert(self.db, urn, "New title", "New summary", "new text")
        rows = self._search_rows()
        self.assertEqual(len(rows), before)
        self.assertIn((rowid, urn, "New title"), rows)

    def test_a_rowid_held_by_another_urn_falls_back_to_the_exact_path_without_losing_it(self) -> None:
        self._observation_and_packet()
        urn, rowid = self.db.execute("SELECT information_urn, rowid FROM ecosystem_information_object LIMIT 1").fetchone()
        self.db.execute("DELETE FROM ecosystem_information_search WHERE rowid=?", (rowid,))
        self.db.execute("INSERT INTO ecosystem_information_search(rowid,information_urn,title,summary,search_text) "
                        "VALUES(?,?,?,?,?)", (rowid, "urn:someone-else", "other", "", "other"))
        registry._search_upsert(self.db, urn, "Mine", "", "mine")
        rows = self._search_rows()
        self.assertIn("urn:someone-else", [r[1] for r in rows])  # not deleted
        self.assertEqual([r[2] for r in rows if r[1] == urn], ["Mine"])  # exactly one row for the object

    def test_control_plane_artifacts_are_automatically_registered_and_linked(self) -> None:
        observation, packet = self._observation_and_packet()
        observation_urn = registry.object_urn_for_source(self.db, "observation", observation["observation_id"])
        packet_urn = registry.object_urn_for_source(self.db, "packet", packet["packet_id"])
        self.assertTrue(observation_urn)
        self.assertTrue(packet_urn)
        edge = self.db.execute(
            "SELECT relationship_type FROM ecosystem_information_edge WHERE source_urn=? AND target_urn=?",
            (packet_urn, observation_urn),
        ).fetchone()
        self.assertEqual(edge[0], "derived_from")
        categories = {
            row[0] for row in self.db.execute(
                "SELECT term_id FROM ecosystem_information_classification WHERE information_urn=?",
                (packet_urn,),
            )
        }
        self.assertEqual(categories, {"security_identity", "business_model"})

    def test_claim_uses_governed_category_and_exact_evidence_edge(self) -> None:
        _, packet = self._observation_and_packet()
        claim = control.record_claim(
            self.db, canonical_subject_id=packet["subject_id"], run_id="run_info",
            claim_type="identity", claim_text="The issuer identity is source-backed.",
            verification_status="verified", confidence="high",
            evidence_inputs=[("packet", packet["packet_id"], "supporting")],
            predicate="has_identity_status", object_value="source_backed",
            source_locator={"section": "company profile"},
        )
        claim_urn = registry.object_urn_for_source(self.db, "claim", claim["claim_id"])
        packet_urn = registry.object_urn_for_source(self.db, "packet", packet["packet_id"])
        category = self.db.execute(
            "SELECT term_id FROM ecosystem_information_classification WHERE information_urn=?",
            (claim_urn,),
        ).fetchone()[0]
        relation = self.db.execute(
            "SELECT relationship_type FROM ecosystem_information_edge WHERE source_urn=? AND target_urn=?",
            (packet_urn, claim_urn),
        ).fetchone()[0]
        self.assertEqual(category, "security_identity")
        self.assertEqual(relation, "supports")
        result = registry.search_information(
            self.db, subject_id=packet["subject_id"], domain_id="issuer",
            object_type="claim", category_id="security_identity",
            verification_states=("supported",), query="source-backed",
        )
        self.assertEqual([row["information_urn"] for row in result], [claim_urn])

    def test_information_objects_are_idempotent_and_immutable(self) -> None:
        kwargs = dict(
            object_type="event", canonical_subject_id="issuer:test", domain_id="issuer",
            owner_id="test", verification_state="observed", lifecycle_state="raw",
            source_kind="test_event", source_id="event-1", payload={"value": 1},
            schema_version="test.v1", observed_at="2026-09-18T00:00:00+00:00",
        )
        first, created = registry.register_information_object(self.db, **kwargs)
        second, reused = registry.register_information_object(self.db, **kwargs)
        self.assertTrue(created)
        self.assertFalse(reused)
        self.assertEqual(first["information_urn"], second["information_urn"])
        with self.assertRaises(registry.InformationRegistryError):
            registry.register_information_object(self.db, **{**kwargs, "payload": {"value": 2}})

    def test_taxonomy_changes_require_a_reviewed_proposal(self) -> None:
        version = registry.information_taxonomy()["version"]
        proposal = registry.propose_taxonomy_change(
            self.db, scheme_id=registry.CLASSIFICATION_SCHEME, base_version=version,
            operation="create", term_id="customer_concentration",
            proposal={"definition": "Revenue exposure to major customers."},
            rationale="Needed for issuer risk classification", proposed_by="sector_agent",
        )
        reviewed = registry.review_taxonomy_proposal(
            self.db, proposal_id=proposal["proposal_id"], decision="approved",
            reviewed_by="babis", review_note="Definition and ownership accepted.",
        )
        self.assertEqual(reviewed["status"], "approved")
        with self.assertRaises(registry.InformationRegistryError):
            registry.review_taxonomy_proposal(
                self.db, proposal_id=proposal["proposal_id"], decision="rejected",
                reviewed_by="babis", review_note="Second decision is invalid.",
            )

    def test_checkpoint_emits_start_and_complete_lineage(self) -> None:
        observation, packet = self._observation_and_packet()
        fingerprint = control.input_fingerprint(
            stage="profile", subject_id=observation["subject_id"],
            inputs=[("observation", observation["observation_id"])],
            config={}, implementation_version="1",
        )
        checkpoint = control.acquire_checkpoint(
            self.db, run_id="lineage_run", stage="profile",
            subject_id=observation["subject_id"], fingerprint=fingerprint,
        )
        output, _ = control.build_packet(
            self.db, kind="company_profile", subject_id=observation["subject_id"],
            run_id="lineage_run", producer_name="company_profile_agent", producer_version="1",
            payload={"overview": "Updated"}, inputs=[("packet", packet["packet_id"])],
        )
        control.complete_checkpoint(self.db, checkpoint["checkpoint_id"], output)
        events = [
            row[0] for row in self.db.execute(
                "SELECT event_type FROM ecosystem_lineage_event WHERE run_id='lineage_run' ORDER BY event_time"
            )
        ]
        self.assertEqual(events, ["START", "COMPLETE"])

    def test_backfill_is_idempotent_after_live_projection(self) -> None:
        observation, packet = self._observation_and_packet()
        claim = control.record_claim(
            self.db, canonical_subject_id=observation["subject_id"], run_id="run_info",
            claim_type="identity", claim_text="Identity is source-backed.",
            verification_status="verified", confidence="high",
            evidence_inputs=[("packet", packet["packet_id"], "supporting")],
        )
        control.record_thesis_revision(
            self.db, canonical_subject_id=observation["subject_id"], run_id="run_info",
            thesis_state="monitor", thesis_text="Monitor this issuer.",
            supporting_claim_ids=[claim["claim_id"]],
        )
        before = registry.registry_summary(self.db)
        registry.backfill_control_plane(self.db)
        registry.backfill_control_plane(self.db)
        after = registry.registry_summary(self.db)
        self.assertEqual(before["objects"], after["objects"])
        self.assertEqual(before["edges"], after["edges"])
        self.assertEqual(before["classifications"], after["classifications"])
        self.assertEqual(registry.validate_registry(self.db)["status"], "pass")

    def test_structured_conflicts_are_review_candidates_only_for_shared_periods(self) -> None:
        _, packet = self._observation_and_packet()
        common = dict(
            canonical_subject_id=packet["subject_id"], run_id="run_conflict",
            claim_type="financial", verification_status="verified", confidence="high",
            evidence_inputs=[("packet", packet["packet_id"], "supporting")],
            predicate="net_cash_usd", as_of="2026-09-17",
        )
        first = control.record_claim(
            self.db, claim_text="Net cash is 10 million USD.", object_value=10_000_000,
            **common,
        )
        second = control.record_claim(
            self.db, claim_text="Net cash is 12 million USD.", object_value=12_000_000,
            **common,
        )
        control.record_claim(
            self.db, claim_text="Net cash was 9 million USD last year.",
            object_value=9_000_000, **{**common, "as_of": "2025-09-17"},
        )
        conflicts = self.db.execute("SELECT * FROM ecosystem_claim_conflict").fetchall()
        self.assertEqual(len(conflicts), 1)
        self.assertEqual(conflicts[0]["status"], "open")
        self.assertEqual(conflicts[0]["predicate"], "net_cash_usd")
        first_urn = registry.object_urn_for_source(self.db, "claim", first["claim_id"])
        second_urn = registry.object_urn_for_source(self.db, "claim", second["claim_id"])
        self.assertEqual({conflicts[0]["claim_urn_a"], conflicts[0]["claim_urn_b"]},
                         {first_urn, second_urn})
        dossier = registry.subject_dossier(self.db, packet["subject_id"])
        self.assertEqual(len(dossier["open_conflicts"]), 1)
        self.assertEqual(len(dossier["review_claims"]), 2)
        registry.review_claim_conflict(
            self.db, conflict_id=conflicts[0]["conflict_id"], decision="dismissed",
            reviewed_by="analyst", review_note="Different accounting scope.",
        )
        self.assertEqual(len(registry.subject_dossier(self.db, packet["subject_id"])["open_conflicts"]), 0)

    def test_stale_packet_identifies_downstream_claim_and_thesis(self) -> None:
        _, packet = self._observation_and_packet()
        claim = control.record_claim(
            self.db, canonical_subject_id=packet["subject_id"], run_id="run_impact",
            claim_type="identity", claim_text="Issuer identity is verified.",
            verification_status="verified", confidence="high",
            evidence_inputs=[("packet", packet["packet_id"], "supporting")],
        )
        thesis = control.record_thesis_revision(
            self.db, canonical_subject_id=packet["subject_id"], run_id="run_impact",
            thesis_state="monitor", thesis_text="Watch the issuer.",
            supporting_claim_ids=[claim["claim_id"]],
        )
        packet_urn = registry.object_urn_for_source(self.db, "packet", packet["packet_id"])
        claim_urn = registry.object_urn_for_source(self.db, "claim", claim["claim_id"])
        thesis_urn = registry.object_urn_for_source(self.db, "thesis", thesis["thesis_revision_id"])
        control.set_packet_freshness(
            self.db, packet_id=packet["packet_id"], knowledge_date="2026-09-17",
            stale_after="2026-09-18", freshness_state="stale",
        )
        self.assertEqual(registry.effective_information_state(self.db, packet_urn)["lifecycle_state"], "stale")
        impact = registry.information_impact(self.db, packet_urn)
        self.assertEqual({item["information_urn"] for item in impact["affected"]},
                         {claim_urn, thesis_urn})
        dossier = registry.subject_dossier(self.db, packet["subject_id"])
        self.assertFalse(dossier["current_verified_claims"])
        self.assertTrue(dossier["review_claims"][0]["affected_by_stale_input"])
        control.set_packet_freshness(
            self.db, packet_id=packet["packet_id"], knowledge_date="2026-09-19",
            stale_after=None, freshness_state="current",
        )
        restored = registry.subject_dossier(self.db, packet["subject_id"])
        self.assertEqual(len(restored["current_verified_claims"]), 1)
        self.assertFalse(restored["affected_by_stale_inputs"])
        control.set_packet_freshness(
            self.db, packet_id=packet["packet_id"], knowledge_date="2026-09-19",
            stale_after="2026-09-20", freshness_state="stale",
        )
        self.assertEqual(registry.effective_information_state(self.db, packet_urn)["lifecycle_state"], "stale")
        self.assertEqual(self.db.execute(
            "SELECT COUNT(*) FROM ecosystem_information_state_event WHERE information_urn=?",
            (packet_urn,),
        ).fetchone()[0], 3)

    def test_document_element_locator_is_linked_to_claim(self) -> None:
        observation, packet = self._observation_and_packet()
        extraction = {
            "schema": "ecosystem.document_extraction.v1", "provider": "test_parser",
            "source": {"sha256": "test-sha-1", "uri": "https://example.test/filing", "name": "Filing"},
            "markdown": "Revenue grew.",
            "structure": {"elements": [{"label": "narrative_text", "text": "Revenue grew.",
                                        "page_number": 4}]},
        }
        document = registry.register_document_extraction(
            self.db, extraction=extraction, subject_id=observation["subject_id"],
            run_id="run_doc", document_type="sec_filing",
            observation_id=observation["observation_id"],
        )
        element_urn = document["element_urns"][0]
        claim = control.record_claim(
            self.db, canonical_subject_id=packet["subject_id"], run_id="run_doc",
            claim_type="growth", claim_text="Revenue grew.",
            verification_status="verified", confidence="high",
            evidence_inputs=[("packet", packet["packet_id"], "supporting")],
            source_locator={"document_element_urn": element_urn, "page_number": 4},
        )
        claim_urn = registry.object_urn_for_source(self.db, "claim", claim["claim_id"])
        relation = self.db.execute(
            "SELECT relationship_type FROM ecosystem_information_edge WHERE source_urn=? AND target_urn=?",
            (element_urn, claim_urn),
        ).fetchone()
        self.assertEqual(relation[0], "supports")
        dossier = registry.subject_dossier(self.db, packet["subject_id"])
        self.assertEqual(dossier["current_verified_claims"][0]["source_locator"]["page_number"], 4)

    def test_unknown_claim_category_is_rejected_before_source_write(self) -> None:
        _, packet = self._observation_and_packet()
        with self.assertRaises(control.ControlPlaneError):
            control.record_claim(
                self.db, canonical_subject_id=packet["subject_id"], run_id="run_info",
                claim_type="unmapped_type", claim_text="Unknown category.",
                verification_status="verified", confidence="high", evidence_inputs=[],
            )
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM ecosystem_claim_store").fetchone()[0], 0)

    def test_structured_claim_semantics_survive_registry_rebuild(self) -> None:
        _, packet = self._observation_and_packet()
        claim = control.record_claim(
            self.db, canonical_subject_id=packet["subject_id"], run_id="run_rebuild",
            claim_type="financial", claim_text="Net cash is 10 million USD.",
            verification_status="verified", confidence="high",
            evidence_inputs=[("packet", packet["packet_id"], "supporting")],
            predicate="net_cash_usd", object_value=10_000_000,
            source_locator={"section": "balance sheet"}, as_of="2026-09-17",
        )
        urn = registry.object_urn_for_source(self.db, "claim", claim["claim_id"])
        self.assertEqual(self.db.execute(
            "SELECT predicate FROM ecosystem_claim_semantics WHERE claim_id=?",
            (claim["claim_id"],),
        ).fetchone()[0], "net_cash_usd")
        self.db.execute("DELETE FROM ecosystem_information_object WHERE information_urn=?", (urn,))
        self.db.commit()
        registry.backfill_control_plane(self.db)
        rebuilt = self.db.execute(
            "SELECT payload_json,source_locator_json FROM ecosystem_information_object WHERE information_urn=?",
            (urn,),
        ).fetchone()
        self.assertEqual(json.loads(rebuilt[0])["object_value"], 10_000_000)
        self.assertEqual(json.loads(rebuilt[1])["section"], "balance sheet")

    def test_exact_legacy_fundamental_template_gets_structured_sidecar(self) -> None:
        _, source_packet = self._observation_and_packet()
        analysis_packet, _ = control.build_packet(
            self.db, kind="fundamental_analysis", subject_id=source_packet["subject_id"],
            run_id="legacy-run", producer_name="giannis_fundamental_agent",
            producer_version="v2", payload={"balance_sheet_analysis": {"net_cash": 25.0}},
            inputs=[("packet", source_packet["packet_id"])],
        )
        claim = control.record_claim(
            self.db, canonical_subject_id=source_packet["subject_id"], run_id="legacy-run",
            claim_type="balance_sheet", claim_text="Reported net cash is 25.0.",
            verification_status="source_linked", confidence="high",
            evidence_inputs=[("packet", source_packet["packet_id"], "supporting")],
            as_of="2026-09-17",
        )
        self.db.execute("DELETE FROM ecosystem_claim_semantics WHERE claim_id=?", (claim["claim_id"],))
        self.db.commit()
        counts = registry.backfill_control_plane(self.db)
        self.assertEqual(counts["legacy_claim_semantics"], 1)
        semantics = self.db.execute(
            "SELECT predicate,object_value_json,source_locator_json FROM ecosystem_claim_semantics "
            "WHERE claim_id=?", (claim["claim_id"],),
        ).fetchone()
        self.assertEqual(semantics[0], "reported_net_cash")
        self.assertEqual(json.loads(semantics[1]), 25.0)
        self.assertEqual(json.loads(semantics[2])["packet_id"], analysis_packet["packet_id"])
        dossier = registry.subject_dossier(self.db, source_packet["subject_id"])
        self.assertEqual(dossier["review_claims"][0]["source_locator"]["packet_id"],
                         analysis_packet["packet_id"])
        self.assertEqual(registry.backfill_control_plane(self.db)["legacy_claim_semantics"], 0)


if __name__ == "__main__":
    unittest.main()
