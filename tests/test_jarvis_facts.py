"""Jarvis facts: questions about numbers are answered straight from the ecosystem's files, without the model
(owner 2026-11-16: the small model rephrased a number wrongly)."""
from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from jarvis_facts import answer_facts

MARKET = {"session": "2026-11-13", "trend": "down", "mood": "anxious",
          "paragraph": "Η αγορά (SPY) πέφτει: +3.1% από τον μέσο όρο 200 ημερών, -2.4% σε 3 μήνες. Η διάθεση είναι "
                       "ανήσυχη (μεταβλητότητα 20 ημερών 22%, 60 ημερών 15%). Η συμμετοχή είναι στενή: 38% των "
                       "μετοχών πάνω από τον μέσο όρο 50 ημερών. Τέταρτη πρόταση."}
SESSION = {"session_date": "2026-11-13", "spy_open_to_close_pct": -0.77,
           "day_result": {"positions": 18, "with_result": 18, "average_result_pct": -0.42, "winners": 8, "losers": 10},
           "per_strategy": [{"agent": "periklis", "playbook": "momentum_12_1", "direction": "long", "closed": 7,
                             "average_pct": -1.13, "winners": 3}]}
EVALUATION = {"shadow_follow": [
    {"lesson_id": "a", "target_agent": "miltos", "criterion_id": "control_team:miltos",
     "proposed_change": "Loosen Miltos's 15-minute confirmation: the approvals he did not enter did better.",
     "shadow_since": "2026-11-04", "sessions_after": 7, "minimum_sessions": 10, "verdict": "collecting"},
    {"lesson_id": "b", "target_agent": "koulis", "criterion_id": "live_strategy_underperforms:yankos:x:long",
     "proposed_change": "Give yankos x at most one slot.", "shadow_since": "2026-11-04", "sessions_after": 7,
     "minimum_sessions": 10, "verdict": "collecting"}]}


class FactsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def write(self, rel: str, payload: dict) -> None:
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8-sig")

    def test_market_answer_is_the_files_own_sentences(self):
        self.write("output/market_state_latest.json", MARKET)
        out = answer_facts("Πώς είναι η ΑΓΟΡΆ;", self.root)
        self.assertEqual(out["mode"], "facts")
        self.assertIn("+3.1% από τον μέσο όρο 200 ημερών, -2.4% σε 3 μήνες", out["answer"])
        self.assertIn("38%", out["answer"])
        self.assertNotIn("Τέταρτη πρόταση", out["answer"])
        self.assertEqual(out["sources"], ["output/market_state_latest.json"])

    def test_session_answer_has_the_day_numbers(self):
        self.write("output/paper_session/session_report_latest.json", SESSION)
        out = answer_facts("πώς πήγε η συνεδρία;", self.root)
        for text in ("13/11", "18 θέσεις", "-0.42%", "οι 8 κέρδισαν", "οι 10 έχασαν", "-0.77%",
                     "Periklis", "momentum_12_1", "-1.13%"):
            self.assertIn(text, out["answer"])

    def test_session_is_told_as_a_story(self):
        """Owner 2026-11-16: say it naturally, not as a list of the agents' results."""
        report = {**SESSION, "per_strategy": [
            {"agent": "periklis", "playbook": "momentum_12_1", "direction": "long", "closed": 7, "average_pct": -1.13, "winners": 3},
            {"agent": "yankos", "playbook": "support_bounce_swing", "direction": "long", "closed": 6, "average_pct": -3.35, "winners": 2},
            {"agent": "periklis", "playbook": "momentum_12_1", "direction": "short", "closed": 3, "average_pct": 6.08, "winners": 2},
            {"agent": "yankos", "playbook": "failed_breakdown_reclaim", "direction": "long", "closed": 1, "average_pct": 2.61, "winners": 1}]}
        self.write("output/paper_session/session_report_latest.json", report)
        text = answer_facts("πώς πήγε η συνεδρία;", self.root)["answer"]
        self.assertTrue(text.startswith("Την Παρασκευή 13/11 κλείσαμε 18 θέσεις"), text)
        self.assertIn("πήγαμε καλύτερα από την αγορά", text)  # -0.42% against SPY -0.77%
        self.assertIn("Το καλύτερο ήταν τα short του Periklis στο momentum_12_1: 3 θέσεις με μέσο +6.08%", text)
        self.assertIn("Το χειρότερο ήταν τα long του Yankos στο support_bounce_swing: 6 θέσεις με μέσο -3.35%", text)
        self.assertIn("1 θέση με +2.61%, κερδισμένη", text)
        self.assertNotIn("\n- ", text)  # sentences, not a list

    def test_session_without_strategies_or_spy_is_still_a_sentence(self):
        self.write("output/paper_session/session_report_latest.json",
                   {"session_date": "2026-11-16", "day_result": {"positions": 0}})
        text = answer_facts("πώς πήγε η συνεδρία;", self.root)["answer"]
        self.assertTrue(text.startswith("Τη Δευτέρα 16/11 δεν κλείσαμε καμία θέση"), text)

    def test_lessons_of_one_agent(self):
        self.write("output/paper_learning/evaluation_latest.json", EVALUATION)
        out = answer_facts("Τι μαθαίνει ο Miltos;", self.root)
        self.assertIn("did better", out["answer"])
        self.assertIn("7/10 συνεδρίες", out["answer"])
        self.assertNotIn("one slot", out["answer"])

    def test_lessons_about_a_trader_named_in_the_criterion(self):
        self.write("output/paper_learning/evaluation_latest.json", EVALUATION)
        self.assertIn("one slot", answer_facts("τι διδάγματα έχει ο yankos;", self.root)["answer"])

    def test_lessons_summary_without_an_agent(self):
        self.write("output/paper_learning/evaluation_latest.json", EVALUATION)
        self.assertIn("2 διδάγματα σε shadow: 2 μαζεύουν δείγμα", answer_facts("τι διδάγματα υπάρχουν;", self.root)["answer"])

    def test_nightly_answer_from_the_run_pointer(self):
        self.write("output/full_research_control_plane_background_latest.json",
                   {"run_id": "full_research_x", "status": "completed", "technical_errors": 0,
                    "agent_audit_status": "pass"})
        self.assertIn("full_research_x", answer_facts("πώς πήγε το nightly;", self.root)["answer"])

    def test_missing_file_is_unknown_not_a_guess(self):
        out = answer_facts("πώς είναι η αγορά;", self.root)
        self.assertEqual(out["mode"], "facts")
        self.assertIn("άγνωστο", out["answer"])

    def test_other_questions_go_to_the_model(self):
        self.assertIsNone(answer_facts("γιατί έκλεισε η EFX;", self.root))
        self.assertIsNone(answer_facts("τι έγινε;", self.root))


class ChatUsesFactsTests(unittest.TestCase):
    def test_fact_question_needs_no_model_and_starts_nothing(self):
        import jarvis_chat

        class Never:
            def available(self) -> bool:
                raise AssertionError("the model must not be asked")

            def generate(self, system: str, prompt: str) -> str:
                raise AssertionError("the model must not be asked")

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "output" / "market_state_latest.json"
            path.parent.mkdir(parents=True)
            path.write_text(json.dumps(MARKET, ensure_ascii=False), encoding="utf-8")
            started = []
            out = jarvis_chat.answer("πώς είναι η αγορά;", root=root, client=Never(),
                                     start_ollama=lambda: started.append(1) or True)
        self.assertEqual((out["mode"], out["ollama"], out["ollama_started"], started), ("facts", "not_needed", False, []))
        self.assertIn("-2.4% σε 3 μήνες", out["answer"])


if __name__ == "__main__":
    unittest.main()


class RoutingTests(unittest.TestCase):
    """2026-11-17 on real questions: 'τι αποφάσισα' is about a past decision and 'ο κανόνας' about a rule - both
    belong to the model with the document search, not to the owner list or the lessons list."""

    def test_past_decisions_and_rules_go_to_the_model(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.assertIsNone(answer_facts("Τι αποφάσισα για το flip;", root))
            self.assertIsNone(answer_facts("Τι κάνει ο κανόνας του σοκ αγοράς;", root))
            self.assertIsNotNone(answer_facts("Τι εγκρίσεις έχω να δώσω;", root))
            self.assertIsNotNone(answer_facts("Τι αποφάσεις περιμένουν από μένα;", root))
