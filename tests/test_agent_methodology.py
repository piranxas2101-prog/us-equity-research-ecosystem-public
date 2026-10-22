from __future__ import annotations

import unittest
from pathlib import Path

import agent_methodology
import agent_run_audit
import run_context
from us_equity_monitor import read_prompt


ROOT = Path(__file__).resolve().parents[1]


class AgentMethodologyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.registry = agent_methodology.load_registry(ROOT / "agent_methodology_registry.json")

    def test_registry_covers_all_agents_and_contract_links(self) -> None:
        self.assertEqual(agent_methodology.validate_registry(self.registry), [])
        contract = __import__("json").loads(
            (ROOT / "ecosystem_theory_contract.json").read_text(encoding="utf-8")
        )
        self.assertEqual(agent_methodology.validate_contract_links(contract, self.registry), [])

    def test_every_methodology_has_distinct_identity_and_no_authority(self) -> None:
        identities = [
            agent_methodology.methodology_identity(agent)
            for agent in agent_methodology.EXPECTED_AGENTS
        ]
        self.assertEqual(len({item["methodology_id"] for item in identities}), 9)
        self.assertTrue(all(item["authority_expanded"] is False for item in identities))

    def test_llm_prompts_receive_only_their_own_versioned_appendix(self) -> None:
        babis = read_prompt(ROOT / "agent_prompt_agent2_catalyst_hunter.txt", agent_name="babis")
        giannis = read_prompt(ROOT / "agent_prompt.txt", agent_name="giannis")
        self.assertIn("Methodology ID: babis.discovery_evidence.v1", babis)
        self.assertNotIn("giannis.narrative_numbers_quality.v1", babis)
        self.assertIn("Methodology ID: giannis.narrative_numbers_quality.v1", giannis)
        self.assertNotIn("babis.discovery_evidence.v1", giannis)
        monitor_source = (ROOT / "us_equity_monitor.py").read_text(encoding="utf-8")
        self.assertIn('read_prompt(prompt_path, agent_name="babis")', monitor_source)
        self.assertIn('read_prompt(giannis_prompt_path, agent_name="giannis")', monitor_source)

    def test_run_context_attaches_methodology_to_all_mapped_agent_stages(self) -> None:
        for stage, agent in run_context.STAGE_AGENT.items():
            payload = run_context.attach_run_context({}, stage=stage, run_id="run_method_test")
            self.assertEqual(
                payload["methodology"]["methodology_id"],
                agent_methodology.get_profile(self.registry, agent)["methodology_id"],
            )
            self.assertFalse(payload["methodology"]["authority_expanded"])

    def test_unknown_agent_cannot_receive_a_methodology(self) -> None:
        with self.assertRaises(ValueError):
            agent_methodology.get_profile(self.registry, "broker")

    def test_run_audit_rejects_stale_or_missing_methodology_identity(self) -> None:
        good = {"methodology": agent_methodology.methodology_identity("petros")}
        checks = agent_run_audit.methodology_conformance_checks("petros", good, self.registry)
        self.assertTrue(all(checks.values()))
        stale = {"methodology": {**good["methodology"], "methodology_sha256": "stale"}}
        stale_checks = agent_run_audit.methodology_conformance_checks("petros", stale, self.registry)
        self.assertFalse(stale_checks["methodology_hash_matches"])
        missing_checks = agent_run_audit.methodology_conformance_checks("petros", {}, self.registry)
        self.assertFalse(missing_checks["methodology_identity_present"])

    def test_thymios_audit_uses_validation_identity(self) -> None:
        payload = {"validation_methodology": agent_methodology.methodology_identity("thymios")}
        checks = agent_run_audit.methodology_conformance_checks("thymios", payload, self.registry)
        self.assertTrue(all(checks.values()))


if __name__ == "__main__":
    unittest.main()
