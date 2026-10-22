from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import agent_personality
import agent_personality_behavior


ROOT = Path(__file__).resolve().parents[1]


class AgentPersonalityBehaviorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.exam = agent_personality_behavior.load_json(ROOT / "agent_personality_behavior_exam_v1.json")
        self.registry = agent_personality.load_registry(ROOT / "agent_personality_registry.json")

    def test_exam_is_complete_and_independent(self) -> None:
        self.assertEqual(agent_personality_behavior.validate_exam(self.exam), [])
        self.assertEqual(len(self.exam["cases"]), 5)
        self.assertTrue(all(len(case["agent_expectations"]) == 9 for case in self.exam["cases"]))

    def test_all_nine_agents_pass_forty_five_frozen_cases(self) -> None:
        reports = [agent_personality_behavior.run_agent_exam(agent, self.exam, self.registry) for agent in agent_personality.EXPECTED_AGENTS]
        self.assertEqual(len(reports), 9)
        self.assertEqual(sum(len(report["case_grades"]) for report in reports), 45)
        for report in reports:
            self.assertEqual(report["status"], "pass")
            self.assertEqual(report["score"], 100.0)
            self.assertFalse(report["critical_failures"])
            self.assertFalse(report["broker_execution_authorized"])
            self.assertFalse(report["free_form_llm_consistency_proven"])

    def test_personality_never_changes_decision_or_authority(self) -> None:
        for agent in agent_personality.EXPECTED_AGENTS:
            for case in self.exam["cases"]:
                response = agent_personality_behavior.build_response(agent, case, self.registry)
                self.assertFalse(response["decision_changed_by_personality"])
                self.assertFalse(response["authority_expanded"])
                self.assertFalse(response["broker_execution_authorized"])
                self.assertFalse(response["silent_repair"])

    def test_expected_handoffs_are_role_correct(self) -> None:
        case = next(item for item in self.exam["cases"] if item["type"] == "collaboration")
        for agent in agent_personality.EXPECTED_AGENTS:
            response = agent_personality_behavior.build_response(agent, case, self.registry)
            self.assertEqual(response["handoff_to"], case["agent_expectations"][agent]["handoff_to"])

    def test_gate_detects_personality_decision_or_authority_drift(self) -> None:
        case = next(item for item in self.exam["cases"] if item["type"] == "authority_pressure")
        response = agent_personality_behavior.build_response("koulis", case, self.registry)
        response["decision_changed_by_personality"] = True
        response["authority_expanded"] = True
        grade = agent_personality_behavior.grade_response("koulis", case, response, self.registry)
        self.assertFalse(grade["checks"]["decision_invariant"])
        self.assertFalse(grade["checks"]["authority_boundary"])
        self.assertIn("decision_invariant", grade["critical_failures"])
        self.assertIn("authority_boundary", grade["critical_failures"])

    def test_gate_detects_silent_repair_and_wrong_handoff(self) -> None:
        error_case = next(item for item in self.exam["cases"] if item["type"] == "error")
        response = agent_personality_behavior.build_response("thymios", error_case, self.registry)
        response["silent_repair"] = True
        grade = agent_personality_behavior.grade_response("thymios", error_case, response, self.registry)
        self.assertFalse(grade["checks"]["no_silent_repair"])
        self.assertIn("no_silent_repair", grade["critical_failures"])

        handoff_case = next(item for item in self.exam["cases"] if item["type"] == "collaboration")
        response = agent_personality_behavior.build_response("babis", handoff_case, self.registry)
        response["handoff_to"] = "broker_boundary"
        grade = agent_personality_behavior.grade_response("babis", handoff_case, response, self.registry)
        self.assertFalse(grade["checks"]["correct_handoff"])

    def test_run_all_writes_a_non_execution_gate(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            result = agent_personality_behavior.run_all(ROOT, ROOT / "agent_personality_behavior_exam_v1.json", Path(directory))
            self.assertEqual(result["gate"]["status"], "pass")
            self.assertEqual(result["gate"]["passed_cases"], 45)
            self.assertFalse(result["gate"]["pipeline_started"])
            self.assertFalse(result["gate"]["broker_execution_authorized"])
            self.assertTrue((Path(directory) / "agent_personality_behavior_summary.md").exists())


if __name__ == "__main__":
    unittest.main()
