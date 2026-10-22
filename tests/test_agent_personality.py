from __future__ import annotations

import json
import unittest
from pathlib import Path

import agent_personality
import agent_run_audit
import run_context
from us_equity_monitor import read_prompt


ROOT = Path(__file__).resolve().parents[1]


class AgentPersonalityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.registry = agent_personality.load_registry(ROOT / "agent_personality_registry.json")

    def test_registry_covers_all_agents_and_contract_links(self) -> None:
        self.assertEqual(agent_personality.validate_registry(self.registry), [])
        contract = json.loads((ROOT / "ecosystem_theory_contract.json").read_text(encoding="utf-8"))
        self.assertEqual(agent_personality.validate_contract_links(contract, self.registry), [])

    def test_all_personalities_are_distinct_and_non_decisional(self) -> None:
        identities = [
            agent_personality.personality_identity(agent)
            for agent in agent_personality.EXPECTED_AGENTS
        ]
        self.assertEqual(len({item["personality_id"] for item in identities}), 9)
        self.assertTrue(all(item["decision_influence_allowed"] is False for item in identities))
        self.assertTrue(all(item["authority_expanded"] is False for item in identities))

    def test_llm_prompts_receive_methodology_and_only_their_own_personality(self) -> None:
        babis = read_prompt(ROOT / "agent_prompt_agent2_catalyst_hunter.txt", agent_name="babis")
        giannis = read_prompt(ROOT / "agent_prompt.txt", agent_name="giannis")
        self.assertIn("Methodology ID: babis.discovery_evidence.v1", babis)
        self.assertIn("Personality ID: babis.curious_evidence_scout.v1", babis)
        self.assertNotIn("giannis.calm_quality_examiner.v1", babis)
        self.assertIn("Methodology ID: giannis.narrative_numbers_quality.v1", giannis)
        self.assertIn("Personality ID: giannis.calm_quality_examiner.v1", giannis)
        self.assertNotIn("babis.curious_evidence_scout.v1", giannis)

    def test_run_context_attaches_personality_without_decision_authority(self) -> None:
        for stage, agent in run_context.STAGE_AGENT.items():
            payload = run_context.attach_run_context({}, stage=stage, run_id="run_personality_test")
            identity = payload["personality"]
            expected = agent_personality.get_profile(self.registry, agent)
            self.assertEqual(identity["personality_id"], expected["personality_id"])
            self.assertFalse(identity["decision_influence_allowed"])
            self.assertFalse(identity["authority_expanded"])

    def test_audit_rejects_stale_personality(self) -> None:
        good = {"personality": agent_personality.personality_identity("petros")}
        checks = agent_run_audit.personality_conformance_checks("petros", good, self.registry)
        self.assertTrue(all(checks.values()))
        stale = {"personality": {**good["personality"], "personality_sha256": "stale"}}
        stale_checks = agent_run_audit.personality_conformance_checks("petros", stale, self.registry)
        self.assertFalse(stale_checks["personality_hash_matches"])

    def test_thymios_uses_validation_personality(self) -> None:
        payload = {"validation_personality": agent_personality.personality_identity("thymios")}
        checks = agent_run_audit.personality_conformance_checks("thymios", payload, self.registry)
        self.assertTrue(all(checks.values()))


if __name__ == "__main__":
    unittest.main()
