import unittest

import agent_personality
from agent_cognition import build_reasoning_note, validate_reasoning_note
from agent_personality_practical_exam import run


class AgentCognitionTests(unittest.TestCase):
    def test_registry_covers_main_and_sector_agents(self):
        registry = agent_personality.load_registry(__import__('pathlib').Path('agent_personality_registry.json'))
        self.assertEqual([], agent_personality.validate_registry(registry))
        self.assertEqual(12, len(registry['sub_agents']))

    def test_reasoning_note_is_sourced_and_bounded(self):
        note = build_reasoning_note('sector_technology', observations=[{'claim':'ARR grew','source_id':'filing'}], unknowns=['retention'], competing_hypotheses=['durable growth','temporary pull-forward'], strongest_countercase='growth is acquisition-led', evidence_to_change_view=['cohort retention'], safe_next_action='handoff with gap', source_ids=['filing'])
        self.assertEqual([], validate_reasoning_note(note))
        self.assertFalse(note['decision_changed_by_personality'])
        self.assertFalse(note['authority_expanded'])

    def test_practical_exam_covers_every_identity(self):
        result = run(__import__('pathlib').Path('agent_personality_registry.json'))
        self.assertEqual('pass', result['status'])
        self.assertEqual(21, len(result['reports']))
        self.assertEqual(756, result['total_checks'])


if __name__ == '__main__':
    unittest.main()
