from __future__ import annotations

import sqlite3
import unittest

import research_control_plane as control


class ResearchControlPlaneTests(unittest.TestCase):
    def setUp(self) -> None:
        self.db = sqlite3.connect(":memory:")
        self.db.row_factory = sqlite3.Row
        control.init_schema(self.db)

    def tearDown(self) -> None:
        self.db.close()

    def _observation(self) -> dict:
        observation, created = control.record_observation(
            self.db,
            subject_id="issuer:CIK:0000320193",
            capability="company.profile",
            provider="sec_edgar",
            source_ref="https://www.sec.gov/Archives/example",
            observed_at="2026-09-17T00:00:00+00:00",
            as_of="2026-09-16",
            run_id="run_test",
            payload={"name": "Example Corp"},
        )
        self.assertTrue(created)
        return observation

    def test_observation_is_idempotent_and_source_aware(self) -> None:
        first = self._observation()
        second, created = control.record_observation(
            self.db,
            subject_id="issuer:CIK:0000320193",
            capability="company.profile",
            provider="sec_edgar",
            source_ref="https://www.sec.gov/Archives/example",
            observed_at="2026-09-17T00:00:00+00:00",
            as_of="2026-09-16",
            run_id="run_test",
            payload={"name": "Example Corp"},
        )
        self.assertFalse(created)
        self.assertEqual(first["observation_id"], second["observation_id"])

    def test_packet_references_immutable_observations(self) -> None:
        observation = self._observation()
        packet, created = control.build_packet(
            self.db,
            kind="company_profile",
            subject_id=observation["subject_id"],
            run_id="run_test",
            producer_name="company_profile_agent",
            producer_version="1",
            payload={"overview": "Stored source-backed profile."},
            inputs=[("observation", observation["observation_id"])],
        )
        self.assertTrue(created)
        self.assertEqual(packet["inputs"][0]["content_hash"], observation["content_hash"])
        same, reused = control.build_packet(
            self.db,
            kind="company_profile",
            subject_id=observation["subject_id"],
            run_id="run_test",
            producer_name="company_profile_agent",
            producer_version="1",
            payload={"overview": "Stored source-backed profile."},
            inputs=[("observation", observation["observation_id"])],
        )
        self.assertFalse(reused)
        self.assertEqual(packet["packet_id"], same["packet_id"])

    def test_checkpoint_reuses_only_identical_inputs(self) -> None:
        observation = self._observation()
        fingerprint = control.input_fingerprint(
            stage="company_profile",
            subject_id=observation["subject_id"],
            inputs=[("observation", observation["observation_id"])],
            config={"schema": "v1"},
            implementation_version="1",
        )
        checkpoint = control.acquire_checkpoint(
            self.db, run_id="run_test", stage="company_profile", subject_id=observation["subject_id"], fingerprint=fingerprint
        )
        self.assertEqual(checkpoint["status"], "planned")
        packet, _ = control.build_packet(
            self.db, kind="company_profile", subject_id=observation["subject_id"], run_id="run_test",
            producer_name="company_profile_agent", producer_version="1", payload={},
            inputs=[("observation", observation["observation_id"])],
        )
        completed = control.complete_checkpoint(self.db, checkpoint["checkpoint_id"], packet)
        self.assertEqual(completed["status"], "completed")
        reused = control.acquire_checkpoint(
            self.db, run_id="run_test", stage="company_profile", subject_id=observation["subject_id"], fingerprint=fingerprint
        )
        self.assertEqual(reused["status"], "reused")

    def test_evidence_gap_is_explicit_and_idempotent(self) -> None:
        first, created = control.record_evidence_gap(
            self.db, subject_id="issuer:CIK:0000320193", run_id="run_test", field="cash_flow",
            reason_code="stale", owner="financial_evidence_engine", payload={"source": "sec_edgar"}, source_event_id="unknown_1",
        )
        second, existing = control.record_evidence_gap(
            self.db, subject_id="issuer:CIK:0000320193", run_id="run_test", field="cash_flow",
            reason_code="stale", owner="financial_evidence_engine", payload={"source": "sec_edgar"}, source_event_id="unknown_1",
        )
        self.assertTrue(created)
        self.assertFalse(existing)
        self.assertEqual(first["gap_id"], second["gap_id"])

    def test_asset_check_records_latest_result_for_immutable_packet(self) -> None:
        observation = self._observation()
        packet, _ = control.build_packet(
            self.db, kind="company_profile", subject_id=observation["subject_id"], run_id="run_test",
            producer_name="company_profile_agent", producer_version="1", payload={},
            inputs=[("observation", observation["observation_id"])],
        )
        failed = control.record_asset_check(
            self.db, packet_id=packet["packet_id"], asset_key="company_profile",
            check_name="source_lineage", passed=False, severity="error", metadata={"input_count": 0},
        )
        passed = control.record_asset_check(
            self.db, packet_id=packet["packet_id"], asset_key="company_profile",
            check_name="source_lineage", passed=True, severity="error", metadata={"input_count": 1},
        )
        row = self.db.execute(
            "SELECT check_id,status FROM ecosystem_asset_check_result WHERE packet_id=?",
            (packet["packet_id"],),
        ).fetchone()
        self.assertNotEqual(failed["check_id"], passed["check_id"])
        self.assertEqual(tuple(row), (passed["check_id"], "passed"))

    def test_identity_freshness_gap_claim_thesis_and_usage_contracts(self) -> None:
        observation = self._observation()
        packet, _ = control.build_packet(
            self.db, kind="company_profile", subject_id=observation["subject_id"], run_id="run_test",
            producer_name="company_profile_agent", producer_version="1", payload={},
            inputs=[("observation", observation["observation_id"])],
        )
        alias = "listing:NASDAQ:AAPL"
        control.register_subject_alias(
            self.db, alias_subject_id=alias, canonical_subject_id=observation["subject_id"],
            alias_kind="listing", source="test",
        )
        self.assertEqual(control.canonical_subject(self.db, alias), observation["subject_id"])
        with self.assertRaises(control.ControlPlaneError):
            control.register_subject_alias(
                self.db, alias_subject_id=alias, canonical_subject_id="issuer:other",
                alias_kind="listing", source="test",
            )

        freshness = control.set_packet_freshness(
            self.db, packet_id=packet["packet_id"], knowledge_date="2026-09-16",
            stale_after="2026-10-16", must_refresh_if=("new_filing", "new_filing"),
        )
        self.assertEqual(freshness["must_refresh_if"], ["new_filing"])

        gap, _ = control.record_evidence_gap(
            self.db, subject_id=observation["subject_id"], run_id="run_test", field="revenue",
            reason_code="missing", owner="financial", payload={}, source_event_id="gap_source",
        )
        resolution = control.resolve_evidence_gap(
            self.db, gap_id=gap["gap_id"], resolving_input_kind="packet",
            resolving_input_id=packet["packet_id"], reason="verified source now available",
        )
        self.assertEqual(resolution["new_status"], "resolved")
        self.assertEqual(
            self.db.execute("SELECT status FROM ecosystem_control_plane_gap WHERE gap_id=?", (gap["gap_id"],)).fetchone()[0],
            "resolved",
        )

        claim = control.record_claim(
            self.db, canonical_subject_id=observation["subject_id"], run_id="run_test",
            claim_type="identity", claim_text="Issuer identity is source-backed.",
            verification_status="verified", confidence="high",
            evidence_inputs=[("packet", packet["packet_id"], "supporting")], as_of="2026-09-16",
        )
        thesis = control.record_thesis_revision(
            self.db, canonical_subject_id=observation["subject_id"], run_id="run_test",
            thesis_state="monitor", thesis_text="Monitor until financial evidence is complete.",
            supporting_claim_ids=[claim["claim_id"]], expectation_map={"next": "financial_evidence"},
            stale_after="2026-10-16", must_refresh_if=("new_filing",),
        )
        self.assertEqual(thesis["revision"], 1)

        usage = control.record_usage_event(
            self.db, run_id="run_test", stage="company_profile", subject_id=observation["subject_id"],
            provider="sec_edgar", api_calls=1, cache_status="miss", metadata={"rate_limited": True},
        )
        self.assertEqual(usage["api_calls"], 1)
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM ecosystem_usage_event").fetchone()[0], 1)


if __name__ == "__main__":
    unittest.main()
