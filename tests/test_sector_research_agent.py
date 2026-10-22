from __future__ import annotations

import unittest
import importlib

from research_registry.core import taxonomy
from sector_agents import AGENT_MODULES
from sector_research_agent import analyze_input, analyze_unresolved_input, scaffold_packet, validate_packet, validate_registry, route_node


class SectorResearchAgentTests(unittest.TestCase):
    def test_registry_covers_every_taxonomy_leaf(self):
        result = validate_registry()
        self.assertEqual(result["status"], "valid", result["errors"])
        self.assertEqual(result["sector_agents"], 11)
        self.assertEqual(result["special_agents"], 1)

    def test_every_leaf_routes_to_one_agent_and_framework(self):
        tax, _ = taxonomy()
        for row in tax["rows"]:
            node = ".".join(row[::2])
            route = route_node(node)
            self.assertEqual(route["sector"], row[0])
            self.assertEqual(route["subcategory"], row[2])
            self.assertTrue(route["required_sector_kpis"])

    def test_scaffold_is_explicitly_unknown_not_invented(self):
        packet = scaffold_packet("MSFT", "Q", "technology.software.applications.enterprise", "r1", "s1", "c1")
        self.assertEqual(packet["agent_id"], "sector_technology")
        self.assertEqual(packet["confidence"], "insufficient")
        self.assertEqual(packet["status"], "awaiting_sector_evidence")
        self.assertIsNone(packet["business_model"])
        self.assertEqual(validate_packet(packet)["status"], "valid")

    def test_all_eleven_agent_modules_are_operational(self):
        self.assertEqual(len(AGENT_MODULES), 11)
        for sector, module_name in AGENT_MODULES.items():
            module = importlib.import_module(module_name)
            self.assertEqual(module.SECTOR, sector)
            self.assertTrue(callable(module.analyze))

    def test_dispatcher_builds_sourced_packet(self):
        payload = {
            "identity": {"run_id":"r1","snapshot_id":"s1","symbol":"MSFT","venue_code":"Q",
                         "classification_id":"c1","leaf_node":"technology.software.applications.enterprise"},
            "evidence": {
                "business_model":"Subscription software",
                "value_chain_position":"Application platform",
                "revenue_drivers":["Subscriptions"], "cost_drivers":["Cloud infrastructure"],
                "peer_group":["ORCL"], "competitive_position":"Established vendor",
                "sector_kpis":{"arr_or_subscription_growth":"reported"},
                "industry_risks":["Competition"], "company_specific_risks":["Execution"],
                "catalysts":["Product release"],
                "source_references":[{"source_id":"src1","as_of":"2026-06-30","evidence_label":"fact_source_reported"}],
                "claim_sources": {field:["src1"] for field in (
                    "business_model","value_chain_position","revenue_drivers","cost_drivers","peer_group",
                    "competitive_position","sector_kpis","industry_risks","company_specific_risks","catalysts")},
                "unknown_fields":["sector_kpis.retention"], "evidence_gap_ids":["gap1"]
            }
        }
        packet = analyze_input(payload)
        self.assertEqual(packet["agent_id"], "sector_technology")
        self.assertEqual(packet["confidence"], "low")
        self.assertEqual(packet["status"], "review_required")

    def test_agent_rejects_wrong_sector(self):
        module = importlib.import_module("sector_agents.energy")
        with self.assertRaises(ValueError):
            module.analyze({"leaf_node":"technology.software.applications.enterprise"}, {})

    def test_unresolved_agent_builds_proposal_for_babis_without_writing_taxonomy(self):
        payload = {
            "identity": {"run_id":"r1","snapshot_id":"s1","symbol":"TOP","venue_code":"A","classification_id":"c1"},
            "evidence": {
                "business_model":"Specialty care services",
                "products_services":["Clinical services"],
                "revenue_model":"Service fees",
                "geography":["United States"],
                "value_chain_clues":["Care delivery"],
                "peer_clues":["PEER"],
                "sector_candidates":[{"sector":"healthcare","source_ids":["src1"],"confidence":"medium"}],
                "subcategory_candidates":[{"subcategory":"care","source_ids":["src1"],"confidence":"medium"}],
                "source_references":[{"source_id":"src1","as_of":"2026-09-17","evidence_label":"fact_source_reported"}],
                "claim_sources": {field:["src1"] for field in (
                    "business_model","products_services","revenue_model","geography","value_chain_clues","peer_clues")},
                "unknown_fields":[], "evidence_gap_ids":[]
            }
        }
        packet = analyze_unresolved_input(payload)
        self.assertEqual(packet["agent_id"], "sector_unresolved_classification")
        self.assertEqual(packet["handoff_to"], "babis")
        self.assertFalse(packet["classification_write_allowed"])
        self.assertEqual(packet["status"], "ready_for_babis_review")

    def test_unresolved_agent_rejects_classification_write(self):
        with self.assertRaises(ValueError):
            analyze_unresolved_input({"identity":{"classification_write":True},"evidence":{}})


if __name__ == "__main__":
    unittest.main()
