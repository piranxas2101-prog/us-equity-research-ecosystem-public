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


class KnowledgeTests(ChatTests):
    """Owner 2026-11-16: Jarvis is told about everything in its work - the knowledge map routes a question to the
    ecosystem's own reports, read-only, only under output/ or the listed docs."""

    def setUp(self) -> None:
        super().setUp()
        (self.root / "jarvis_knowledge.json").write_text(json.dumps({"topics": [
            {"id": "market", "files": ["output/market_state_review_latest.md"], "keywords": ["αγορα", "spy"]},
            {"id": "rules", "files": ["docs/RULES_REVIEW.md"], "keywords": ["κανονες", "t3"]},
            {"id": "evil", "files": [".env", "output/../.env"], "keywords": ["μυστικ"]},
        ]}), encoding="utf-8")
        (self.root / "output" / "market_state_review_latest.md").write_text("ανοδική τάση, ήρεμη\n", encoding="utf-8")
        (self.root / "docs").mkdir()
        (self.root / "docs" / "RULES_REVIEW.md").write_text("T3 μετρήθηκε\n", encoding="utf-8")

    def test_topic_keyword_without_accents_picks_its_file(self):
        self.assertIn("market_state_review_latest.md", self.names("Πώς είναι η ΑΓΟΡΆ σήμερα;"))

    def test_listed_doc_outside_output_is_readable(self):
        self.assertIn("RULES_REVIEW.md", self.names("τι λέει το T3 στους κανόνες;"))

    def test_map_cannot_open_files_outside_output_or_docs(self):
        self.assertEqual(self.names("πες μου τα μυστικά"), ["report_latest.md"])

    def test_sources_are_budgeted_in_total(self):
        (self.root / "output" / "market_state_review_latest.md").write_text("α" * 50000, encoding="utf-8")
        fake = Fake()
        answer("πώς είναι η αγορά;", root=self.root, client=fake, max_total_chars=9000)
        self.assertLess(len(fake.prompts[0][1]), 9000 + 500)


class StartOnQuestionTests(ChatTests):
    """Owner 2026-11-16: Jarvis starts the Ollama server itself, only when the owner asks a question on its page."""

    class Sleepy(Fake):
        def __init__(self, wakes_after: int) -> None:
            super().__init__(available=False, text="μέσο -0.42%")
            self.calls, self.wakes_after = 0, wakes_after

        def available(self) -> bool:
            self.calls += 1
            return self.calls > self.wakes_after

    def test_starts_the_server_then_answers(self):
        started = []
        client = self.Sleepy(wakes_after=2)
        out = answer("πώς πήγε;", root=self.root, client=client, start_ollama=lambda: started.append(1) or True,
                     wait_seconds=5, poll=lambda: None)
        self.assertEqual((started, out["ollama"], out["ollama_started"]), ([1], "ok", True))

    def test_does_not_start_when_already_running(self):
        started = []
        out = answer("πώς πήγε;", root=self.root, client=Fake(), start_ollama=lambda: started.append(1) or True)
        self.assertEqual((started, out["ollama_started"]), ([], False))

    def test_server_that_never_answers_gives_plain_message(self):
        out = answer("πώς πήγε;", root=self.root, client=self.Sleepy(wakes_after=10**6),
                     start_ollama=lambda: True, wait_seconds=0.05, poll=lambda: None)
        self.assertEqual(out["ollama"], "down")
        self.assertIn("Ξεκίνησα το Ollama", out["answer"])

    def test_without_a_starter_nothing_is_started(self):
        self.assertEqual(answer("x", root=self.root, client=Fake(available=False))["ollama"], "down")

    def test_starter_runs_ollama_serve_hidden_on_localhost(self):
        from unittest import mock
        import jarvis_chat

        exe = self.root / "ollama.exe"
        exe.write_text("", encoding="utf-8")
        with mock.patch.object(jarvis_chat.subprocess, "Popen") as popen:
            self.assertTrue(jarvis_chat.start_ollama_server(exe=exe, log_dir=self.root / "logs"))
        args, kwargs = popen.call_args
        self.assertEqual(args[0], [str(exe), "serve"])
        self.assertEqual(kwargs["env"]["OLLAMA_HOST"], "127.0.0.1:11434")
        self.assertTrue(kwargs["creationflags"] & 0x08000000)  # no window

    def test_starter_without_ollama_installed_says_no(self):
        import jarvis_chat
        self.assertFalse(jarvis_chat.start_ollama_server(exe=self.root / "missing.exe", log_dir=self.root / "logs"))


if __name__ == "__main__":
    unittest.main()


class ShippedKnowledgeTests(unittest.TestCase):
    def test_shipped_map_reads_only_output_and_docs(self):
        root = Path(__file__).resolve().parents[1]
        topics = json.loads((root / "jarvis_knowledge.json").read_text(encoding="utf-8"))["topics"]
        self.assertGreaterEqual(len(topics), 10)
        for topic in topics:
            for rel in topic["files"]:
                with self.subTest(topic=topic["id"], file=rel):
                    self.assertTrue(rel.startswith(("output/", "docs/")) and ".." not in rel)
            self.assertTrue(topic["keywords"])
