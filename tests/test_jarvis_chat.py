"""Jarvis chat: answers only from its own reports, flags numbers the sources do not contain, never starts Ollama."""
from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from jarvis_chat import answer, history, save_turn, select_sources


class Fake:
    def __init__(self, *, available: bool = True, text: str = "ok") -> None:
        self._available, self.text, self.prompts = available, text, []

    def available(self) -> bool:
        return self._available

    def generate(self, system: str, prompt: str) -> str:
        self.prompts.append((system, prompt))
        return self.text


class ChatTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        jarvis = self.root / "output" / "jarvis"
        jarvis.mkdir(parents=True)
        (jarvis / "report_latest.md").write_text("# Jarvis\nμέσο αποτέλεσμα -0.42%, θέσεις 18\n", encoding="utf-8")
        (jarvis / "report_2026-11-13.md").write_text("# 13/11\nθέσεις 9\n", encoding="utf-8")
        (jarvis / "report_latest.json").write_text("{}", encoding="utf-8")
        session = self.root / "output" / "paper_session"
        session.mkdir(parents=True)
        (session / "session_report_latest.md").write_text("EFX closed at stop\n", encoding="utf-8")
        (session / "session_report_20261113.md").write_text("13/11 session\n", encoding="utf-8")
        (self.root / ".env").write_text("SECRET=1\n", encoding="utf-8")
        (self.root / "jarvis_checks.json").write_text(json.dumps({"checks": [
            {"id": "flip_shadow_s8", "due": {"kind": "every_pass"}, "producer": None,
             "output": "output/paper_learning/flip_shadow_latest.json"}]}), encoding="utf-8")
        (self.root / "output" / "paper_learning").mkdir()
        (self.root / "output" / "paper_learning" / "flip_shadow_latest.json").write_text('{"verdict": "x"}', encoding="utf-8")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def names(self, question: str) -> list[str]:
        return [p.name for p in select_sources(question, self.root)]

    def test_ollama_down_gives_plain_message(self):
        fake = Fake(available=False)
        out = answer("τι έγινε σήμερα;", root=self.root, client=fake)
        self.assertEqual(out["ollama"], "down")
        self.assertIn("Ollama", out["answer"])
        self.assertEqual(fake.prompts, [])

    def test_sources_always_include_latest_report(self):
        self.assertEqual(self.names("γεια")[0], "report_latest.md")

    def test_date_question_picks_that_days_report(self):
        self.assertIn("report_2026-11-13.md", self.names("τι έγινε στις 2026-11-13;"))
        self.assertIn("session_report_20261113.md", self.names("τι έγινε στις 2026-11-13;"))

    def test_ticker_question_picks_session_report(self):
        self.assertIn("session_report_latest.md", self.names("γιατί έκλεισε η EFX;"))

    def test_check_id_picks_its_output(self):
        self.assertIn("flip_shadow_latest.json", self.names("πώς πάει το flip_shadow_s8;"))

    def test_at_most_three_sources(self):
        self.assertLessEqual(len(self.names("EFX 2026-11-13 flip_shadow_s8 κανόνας")), 3)

    def test_sources_never_leave_output(self):
        for path in select_sources("..\\.env ../.env .env", self.root):
            self.assertIn("output", path.relative_to(self.root).parts)

    def test_invented_number_is_flagged(self):
        out = answer("πώς πήγε;", root=self.root, client=Fake(text="έχασε -3.7% σε 18 θέσεις"))
        self.assertEqual(out["unverified_numbers"], ["3.7%"])
        self.assertIn("δεν βρίσκονται στις πηγές", out["answer"])

    def test_cited_number_passes(self):
        out = answer("πώς πήγε;", root=self.root, client=Fake(text="μέσο -0.42% σε 18 θέσεις"))
        self.assertEqual(out["unverified_numbers"], [])
        self.assertEqual(out["sources"][0], "output/jarvis/report_latest.md")

    def test_prompt_carries_the_sources_and_the_rules(self):
        fake = Fake()
        answer("πώς πήγε;", root=self.root, client=fake)
        system, prompt = fake.prompts[0]
        self.assertIn("δεν το ξέρω", system)
        self.assertIn("-0.42%", prompt)
        self.assertNotIn("SECRET", prompt)

    def test_chat_history_is_kept(self):
        db = self.root / "data" / "jarvis" / "chat.sqlite3"
        save_turn("ερώτηση", {"answer": "απάντηση", "sources": ["a"], "unverified_numbers": [], "ollama": "ok"}, path=db)
        self.assertEqual([(t["question"], t["answer"]) for t in history(path=db)], [("ερώτηση", "απάντηση")])


if __name__ == "__main__":
    unittest.main()
