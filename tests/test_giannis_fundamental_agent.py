import unittest
import sqlite3
from pathlib import Path
import tempfile
from financial_evidence_packet import build_financial_evidence_packet, readiness_gate
from giannis_fundamental_agent import (
    analyze_financial_packet,
    load_financial_packet_from_control_plane,
    publish_fundamental_analysis_control_plane,
)
from research_information_flow import ObservationEnvelope, ResearchPacketRegistry
from research_workflow_orchestrator import publish_financial_evidence_control_plane

class GiannisFundamentalAgentTests(unittest.TestCase):
    def packet(self):
        facts = {field: {"value": value, "unit": "USD", "period_end": "2025-12-31", "accession": "a"}
                 for field, value in {"revenue": 100, "net_income": 10, "cash": 30,
                     "debt": 5, "operating_cash_flow": 12, "stockholders_equity": 50}.items()}
        packet = build_financial_evidence_packet(subject_id="sec:cik:1", ticker="ABC",
            sec_observation={"payload": {"fields": facts, "as_of": "2026-01-01"}})
        packet["readiness"] = readiness_gate(packet)
        return packet
    def test_emits_v2_packet_without_llm_or_technical_inputs(self):
        result = analyze_financial_packet(self.packet(), run_id="run-1", sector_context_packet_id="sector-1")
        self.assertEqual(result["schema"], "ecosystem.fundamental_analysis_packet.v2")
        self.assertEqual(result["balance_sheet_analysis"]["net_cash"], 25)
        self.assertEqual(result["llm_calls"], 0)
        self.assertFalse(result["technical_inputs_used"])
        self.assertTrue(result["claim_source_links"])

    def test_consumes_financial_evidence_by_control_packet_id_and_publishes_handoff(self):
        with tempfile.TemporaryDirectory() as folder:
            db_path = Path(folder) / "flow.sqlite3"
            registry = ResearchPacketRegistry(db_path)
            financial = self.packet()
            observation = ObservationEnvelope.create(
                provider="sec", capability="company_facts", subject_id="sec:cik:1", ticker="ABC",
                payload_schema="test.v1", payload={"fields": financial["values"]},
                observed_at="2026-09-18T00:00:00+00:00", as_of="2026-01-01",
            )
            registry.put_observation(observation)
            registry_packet_id = registry.put_packet(
                financial["schema"], observation.subject_id, financial, [observation.observation_id]
            )
            source = publish_financial_evidence_control_plane(
                registry=registry, observation=observation, financial_packet=financial,
                registry_packet_id=registry_packet_id, readiness=financial["readiness"], run_id="run-1",
            )
            loaded, reference = load_financial_packet_from_control_plane(
                control_plane_db=db_path, control_packet_id=source["control_packet_id"]
            )
            self.assertEqual(loaded["ticker"], "ABC")
            self.assertEqual(reference["subject_id"], "sec:cik:1")
            analysis = analyze_financial_packet(loaded, run_id="run-1")
            first = publish_fundamental_analysis_control_plane(
                control_plane_db=db_path, analysis=analysis,
                input_control_packet_id=source["control_packet_id"], subject_id=reference["subject_id"],
            )
            repeated = publish_fundamental_analysis_control_plane(
                control_plane_db=db_path, analysis=analysis,
                input_control_packet_id=source["control_packet_id"], subject_id=reference["subject_id"],
            )
            self.assertEqual(first["status"], "completed")
            self.assertEqual(repeated["status"], "reused")
            self.assertEqual(first["control_packet_id"], repeated["control_packet_id"])
            db = sqlite3.connect(db_path)
            try:
                structured = db.execute(
                    "SELECT predicate,object_value_json,source_locator_json "
                    "FROM ecosystem_claim_semantics WHERE predicate='reported_net_cash'"
                ).fetchone()
                self.assertEqual(structured[0], "reported_net_cash")
                self.assertEqual(float(structured[1]), 25.0)
                self.assertIn(first["control_packet_id"], structured[2])
            finally:
                db.close()

if __name__ == "__main__": unittest.main()
