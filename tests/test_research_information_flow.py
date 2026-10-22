import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

import research_control_plane as control
from financial_evidence_packet import build_financial_evidence_packet, readiness_gate
from research_information_flow import ObservationEnvelope, ResearchPacketRegistry, digest
from research_workflow_orchestrator import (
    collect_sec_financials,
    publish_financial_block_control_plane,
    publish_financial_evidence_control_plane,
)
from provider_rate_limiter import SharedProviderRateLimiter


class InformationFlowTests(unittest.TestCase):
    def test_observation_packet_and_checkpoint_are_idempotent(self):
        with tempfile.TemporaryDirectory() as folder:
            registry = ResearchPacketRegistry(Path(folder) / "flow.sqlite3")
            obs = ObservationEnvelope.create(provider="sec", capability="company_facts",
                subject_id="sec:cik:1", ticker="ABC", payload_schema="test.v1",
                payload={"fields": {"revenue": {"value": 1}}})
            self.assertEqual(registry.put_observation(obs), registry.put_observation(obs))
            packet_id = registry.put_packet("packet.v1", "sec:cik:1", {"value": 1}, [obs.observation_id])
            registry.set_packet_ref("run-1", "sec:cik:1", "financial_evidence_packet_id", packet_id)
            signature = digest({"input": obs.observation_id})
            registry.complete_stage("run-1", "sec:cik:1", "financial_evidence", signature, packet_id)
            self.assertTrue(registry.checkpoint_matches("run-1", "sec:cik:1", "financial_evidence", signature))
            self.assertFalse(registry.checkpoint_matches("run-1", "sec:cik:1", "financial_evidence", "changed"))

    def test_financial_packet_enforces_sec_precedence_and_gate(self):
        facts = {field: {"value": index + 1, "unit": "USD", "period_end": "2025-12-31", "accession": "a"}
                 for index, field in enumerate(("revenue", "net_income", "cash", "operating_cash_flow", "stockholders_equity"))}
        packet = build_financial_evidence_packet(subject_id="sec:cik:1", ticker="ABC",
            sec_observation={"payload": {"fields": facts, "as_of": "2026-01-01"}},
            supplementary_observations=[{"provider": "fmp", "payload": {"values": {"revenue": 999}}}])
        self.assertEqual(packet["values"]["revenue"]["value"], 1)
        self.assertEqual(packet["conflicts"][0]["winner"], "sec_edgar")
        self.assertTrue(readiness_gate(packet)["ready_for_fundamental_synthesis"])
        self.assertEqual(packet["llm_calls"], 0)

    def test_registry_contains_names_not_secret_values(self):
        registry = json.loads((Path(__file__).parents[1] / "research_capability_registry.json").read_text())
        text = json.dumps(registry)
        self.assertIn("FMP_API_KEY", text)
        self.assertNotIn("api_key_value", text)
        self.assertEqual(registry["credentials"]["storage"], "environment_only")

    def test_financial_evidence_projects_to_shared_control_plane_with_explicit_gaps(self):
        with tempfile.TemporaryDirectory() as folder:
            registry = ResearchPacketRegistry(Path(folder) / "flow.sqlite3")
            facts = {
                "revenue": {"value": 100, "unit": "USD", "period_end": "2025-12-31", "accession": "a"},
                "net_income": {"value": 10, "unit": "USD", "period_end": "2025-12-31", "accession": "a"},
            }
            observation = ObservationEnvelope.create(
                provider="sec", capability="company_facts", subject_id="sec:cik:0000000001",
                ticker="ABC", source_url="https://data.sec.gov/", observed_at="2026-09-18T00:00:00+00:00",
                as_of="2026-01-01", payload_schema="ecosystem.sec_companyfacts_extract.v1",
                payload={"fields": facts, "as_of": "2026-01-01"},
            )
            packet = build_financial_evidence_packet(
                subject_id=observation.subject_id, ticker="ABC", sec_observation=observation.as_dict()
            )
            readiness = readiness_gate(packet)
            registry.put_observation(observation)
            registry_packet_id = registry.put_packet(
                packet["schema"], observation.subject_id, {**packet, "readiness": readiness},
                [observation.observation_id],
            )
            first = publish_financial_evidence_control_plane(
                registry=registry, observation=observation, financial_packet=packet,
                registry_packet_id=registry_packet_id, readiness=readiness, run_id="run-1",
            )
            repeated = publish_financial_evidence_control_plane(
                registry=registry, observation=observation, financial_packet=packet,
                registry_packet_id=registry_packet_id, readiness=readiness, run_id="run-1",
            )
            self.assertEqual(first["status"], "completed")
            self.assertEqual(repeated["status"], "reused")
            self.assertEqual(first["control_packet_id"], repeated["control_packet_id"])
            self.assertGreater(first["new_evidence_gaps"], 0)
            self.assertEqual(repeated["new_evidence_gaps"], 0)

    def test_blocked_financial_input_is_projected_as_a_shared_gap(self):
        with tempfile.TemporaryDirectory() as folder:
            registry = ResearchPacketRegistry(Path(folder) / "flow.sqlite3")
            observation = ObservationEnvelope.create(
                provider="sec", capability="company_facts", subject_id="sec:cik:0000000001",
                ticker="ABC", payload_schema="ecosystem.provider_block.v1", payload={},
                observed_at="2026-09-18T00:00:00+00:00", status="blocked", reason_code="not_configured",
            )
            first = publish_financial_block_control_plane(registry=registry, observation=observation, run_id="run-1")
            repeated = publish_financial_block_control_plane(registry=registry, observation=observation, run_id="run-1")
            self.assertTrue(first["created"])
            self.assertFalse(repeated["created"])
            self.assertEqual(first["evidence_gap_id"], repeated["evidence_gap_id"])

    def test_collection_exposes_missing_sec_configuration_to_control_plane(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {"SEC_USER_AGENT": ""}, clear=False):
            registry = ResearchPacketRegistry(Path(folder) / "flow.sqlite3")
            result = collect_sec_financials(
                symbol="ABC", cik=1, as_of="2026-01-01", run_id="run-1",
                registry=registry, limiter=SharedProviderRateLimiter(),
            )
            self.assertEqual(result["status"], "blocked")
            self.assertEqual(result["reason_code"], "not_configured")
            self.assertIn("control_plane", result)
            self.assertTrue(result["control_plane"]["evidence_gap_id"])

    def test_cross_run_cache_creates_current_run_receipt_packet(self):
        with tempfile.TemporaryDirectory() as folder:
            db_path = Path(folder) / "flow.sqlite3"
            registry = ResearchPacketRegistry(db_path)
            facts = {field: {
                "value": index + 1, "unit": "USD", "period_end": "2025-12-31", "accession": "a"
            } for index, field in enumerate((
                "revenue", "net_income", "cash", "operating_cash_flow", "stockholders_equity"
            ))}
            observation = ObservationEnvelope.create(
                provider="sec", capability="company_facts", subject_id="sec:cik:0000000001",
                ticker="ABC", source_url="https://data.sec.gov/",
                observed_at="2026-09-18T00:00:00+00:00", as_of="2026-09-18",
                payload_schema="ecosystem.sec_companyfacts_extract.v1",
                payload={"fields": facts, "as_of": "2026-09-18"},
            )
            packet = build_financial_evidence_packet(
                subject_id=observation.subject_id, ticker="ABC", sec_observation=observation.as_dict()
            )
            readiness = readiness_gate(packet)
            registry.put_observation(observation)
            registry_packet_id = registry.put_packet(
                packet["schema"], observation.subject_id, {**packet, "readiness": readiness},
                [observation.observation_id],
            )
            old = publish_financial_evidence_control_plane(
                registry=registry, observation=observation, financial_packet=packet,
                registry_packet_id=registry_packet_id, readiness=readiness, run_id="run-old",
            )
            db = sqlite3.connect(db_path)
            db.row_factory = sqlite3.Row
            try:
                profile_source, _ = control.record_observation(
                    db, subject_id=observation.subject_id, capability="company.profile",
                    provider="registry", source_ref="profile:ABC",
                    observed_at="2026-09-18T00:00:00+00:00", run_id="run-new", payload={},
                )
                profile, _ = control.build_packet(
                    db, kind="company_profile", subject_id=observation.subject_id, run_id="run-new",
                    producer_name="company_profile_agent", producer_version="v1", payload={},
                    inputs=[("observation", profile_source["observation_id"])],
                )
            finally:
                db.close()
            result = collect_sec_financials(
                symbol="ABC", cik=1, as_of="2026-09-18", run_id="run-new",
                registry=registry, limiter=SharedProviderRateLimiter(),
                upstream_control_packet_ids=(profile["packet_id"],),
            )
            self.assertEqual(result["status"], "cross_run_reused")
            current_id = result["control_plane"]["control_packet_id"]
            self.assertNotEqual(current_id, old["control_packet_id"])
            db = sqlite3.connect(db_path)
            try:
                current_run = db.execute(
                    "SELECT run_id FROM ecosystem_evidence_packet_store WHERE packet_id=?",
                    (current_id,),
                ).fetchone()[0]
                input_kinds = {row[0] for row in db.execute(
                    "SELECT p.kind FROM ecosystem_packet_input i "
                    "JOIN ecosystem_evidence_packet_store p ON p.packet_id=i.input_id "
                    "WHERE i.packet_id=? AND i.input_kind='packet'",
                    (current_id,),
                )}
            finally:
                db.close()
            self.assertEqual(current_run, "run-new")
            self.assertEqual(input_kinds, {"company_profile", "financial_evidence"})

    def test_financial_publish_resolves_only_financial_owned_gaps(self):
        with tempfile.TemporaryDirectory() as folder:
            db_path = Path(folder) / "flow.sqlite3"
            registry = ResearchPacketRegistry(db_path)
            facts = {field: {
                "value": index + 1, "unit": "USD", "period_end": "2025-12-31", "accession": "a"
            } for index, field in enumerate((
                "revenue", "net_income", "cash", "operating_cash_flow", "stockholders_equity"
            ))}
            observation = ObservationEnvelope.create(
                provider="sec", capability="company_facts", subject_id="sec:cik:0000000001",
                ticker="ABC", source_url="https://data.sec.gov/",
                observed_at="2026-09-18T00:00:00+00:00", as_of="2026-09-18",
                payload_schema="ecosystem.sec_companyfacts_extract.v1",
                payload={"fields": facts, "as_of": "2026-09-18"},
            )
            packet = build_financial_evidence_packet(
                subject_id=observation.subject_id, ticker="ABC", sec_observation=observation.as_dict()
            )
            readiness = readiness_gate(packet)
            registry.put_observation(observation)
            registry_packet_id = registry.put_packet(
                packet["schema"], observation.subject_id, {**packet, "readiness": readiness},
                [observation.observation_id],
            )
            db = sqlite3.connect(db_path)
            db.row_factory = sqlite3.Row
            try:
                financial_gap, _ = control.record_evidence_gap(
                    db, subject_id=observation.subject_id, run_id="run-old", field="cash",
                    reason_code="no_data", owner="financial_evidence_engine", payload={},
                    source_event_id="old-financial",
                )
                unrelated_gap, _ = control.record_evidence_gap(
                    db, subject_id=observation.subject_id, run_id="run-1", field="valuation",
                    reason_code="no_data", owner="petros", payload={},
                    source_event_id="unrelated",
                )
            finally:
                db.close()
            publish_financial_evidence_control_plane(
                registry=registry, observation=observation, financial_packet=packet,
                registry_packet_id=registry_packet_id, readiness=readiness, run_id="run-1",
            )
            db = sqlite3.connect(db_path)
            try:
                statuses = dict(db.execute(
                    "SELECT gap_id,status FROM ecosystem_control_plane_gap WHERE gap_id IN (?,?)",
                    (financial_gap["gap_id"], unrelated_gap["gap_id"]),
                ))
            finally:
                db.close()
            self.assertEqual(statuses[financial_gap["gap_id"]], "resolved")
            self.assertEqual(statuses[unrelated_gap["gap_id"]], "open")


if __name__ == "__main__": unittest.main()
