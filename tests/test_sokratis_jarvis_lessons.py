"""Sokratis teaches Jarvis's shadow lessons too (owner 2026-11-16): each trader the rules about him, a rule naming no
agent to both; advisory only, nothing is switched on or off."""
from __future__ import annotations

import unittest

from sokratis_strategy_teacher import build_paper_trade_lesson, jarvis_shadow_lessons

FOLLOW = [
    {"lesson_id": "lesson_y", "criterion_id": "jarvis_loss_rule:agent=yankos|playbook=rsi2",
     "conditions": {"agent": "yankos", "playbook": "rsi2"}, "proposed_change": "Προσοχή rsi2",
     "shadow_since": "2026-11-16", "verdict": "collecting", "n": 4, "losses": 3},
    {"lesson_id": "lesson_all", "criterion_id": "jarvis_loss_rule:holding_class=quarter",
     "conditions": {"holding_class": "quarter"}, "proposed_change": "Προσοχή quarter",
     "shadow_since": "2026-11-16", "verdict": "confirmed", "n": 12, "losses": 9},
]
REPORT = {"sections": {"learning": {"follow": FOLLOW}}}


class SokratisJarvisLessonsTests(unittest.TestCase):
    def test_reads_follow_from_the_jarvis_report(self):
        self.assertEqual([i["lesson_id"] for i in jarvis_shadow_lessons(REPORT)], ["lesson_y", "lesson_all"])
        self.assertEqual(jarvis_shadow_lessons({}), [])
        self.assertEqual(jarvis_shadow_lessons({"sections": {"learning": {"error": "x"}}}), [])

    def test_each_trader_is_taught_his_rules_and_the_general_ones(self):
        lesson = build_paper_trade_lesson({"sessions": 20}, jarvis=REPORT)
        yankos = [i["lesson_id"] for i in lesson["traders"]["yankos"]["shadow_lessons"]]
        periklis = [i["lesson_id"] for i in lesson["traders"]["periklis"]["shadow_lessons"]]
        self.assertEqual((yankos, periklis), (["lesson_y", "lesson_all"], ["lesson_all"]))

    def test_jarvis_lesson_says_where_it_comes_from(self):
        lesson = build_paper_trade_lesson({"sessions": 20}, jarvis=REPORT)
        item = lesson["traders"]["periklis"]["shadow_lessons"][0]
        self.assertEqual((item["source"], item["verdict"], item["proposed_change"]),
                         ("jarvis", "confirmed", "Προσοχή quarter"))
        self.assertTrue(lesson["authority"].startswith("advisory_only"))

    def test_without_paper_evaluation_jarvis_lessons_are_still_taught(self):
        lesson = build_paper_trade_lesson({}, jarvis=REPORT)
        self.assertEqual(lesson["status"], "ok")
        self.assertIn("yankos", lesson["traders"])

    def test_nothing_at_all_stays_unavailable(self):
        self.assertEqual(build_paper_trade_lesson({}, jarvis={})["status"], "unavailable")


if __name__ == "__main__":
    unittest.main()
