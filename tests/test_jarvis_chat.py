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
        from unittest import mock
        fake = Fake()
        with mock.patch("jarvis_facts.answer_facts", return_value=None):  # here: the model's path, not the facts
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


class ExcerptTests(unittest.TestCase):
    """2026-11-16 on the owner's machine: the CPU reads ~28 tokens/s and Greek is ~0.8 tokens per character, so the
    model gets only the lines that concern the question, within a small budget."""

    def test_excerpt_keeps_headings_and_matching_lines(self):
        from jarvis_chat import excerpt
        text = "# Αναφορά\n## Αγορά\n- η αγορά πέφτει\n## Συνεδρία\n- 18 θέσεις\n" + "- άσχετο\n" * 200
        out = excerpt(text, "πώς είναι η ΑΓΟΡΑ;", 200)
        self.assertIn("η αγορά πέφτει", out)
        self.assertIn("## Αγορά", out)
        self.assertNotIn("άσχετο", out)
        self.assertLessEqual(len(out), 200)

    def test_excerpt_without_a_match_takes_the_start(self):
        from jarvis_chat import excerpt
        self.assertEqual(excerpt("α\nβ\nγ\n" * 100, "xyz", 10), ("α\nβ\nγ\n" * 100)[:10])

    def test_excerpt_respects_the_budget(self):
        from jarvis_chat import excerpt
        self.assertLessEqual(len(excerpt("- αγορά " + "χ" * 5000 + "\n", "αγορά", 300)), 300)

    def test_client_sends_threads_keep_alive_and_short_answers(self):
        from unittest import mock
        import jarvis_chat
        client = jarvis_chat.OllamaClient({"ollama_host": "http://127.0.0.1:11434", "model": "m", "num_ctx": 4096,
                                           "num_predict": 250, "num_thread": 8, "keep_alive": "30m",
                                           "timeout_seconds": 300})
        response = mock.MagicMock()
        response.__enter__.return_value.read.return_value = b'{"response": "ok"}'
        with mock.patch.object(jarvis_chat.urllib.request, "urlopen", return_value=response) as urlopen:
            self.assertEqual(client.generate("s", "p"), "ok")
        body = json.loads(urlopen.call_args.args[0].data)
        self.assertEqual((body["options"]["num_thread"], body["options"]["num_predict"], body["keep_alive"]),
                         (8, 250, "30m"))
        self.assertEqual(urlopen.call_args.kwargs["timeout"], 300)

    def test_shipped_config_fits_this_machine(self):
        config = json.loads((Path(__file__).resolve().parents[1] / "jarvis_config.json").read_text(encoding="utf-8"))
        self.assertLessEqual(config["max_total_chars"], 2000)
        self.assertGreaterEqual(config["timeout_seconds"], 300)
        self.assertEqual(config["keep_alive"], "30m")


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


class ShortAnswerTests(unittest.TestCase):
    """2026-11-16: an answer was cut mid-sentence at the token limit - the model is asked for at most three short
    sentences, and the limit leaves room for them."""

    def test_system_prompt_asks_for_three_sentences_and_correct_direction(self):
        import jarvis_chat
        self.assertIn("τρεις σύντομες προτάσεις", jarvis_chat.SYSTEM)
        self.assertIn("πάνω ή κάτω", jarvis_chat.SYSTEM)

    def test_shipped_limit_leaves_room(self):
        config = json.loads((Path(__file__).resolve().parents[1] / "jarvis_config.json").read_text(encoding="utf-8"))
        self.assertGreaterEqual(config["num_predict"], 320)


class WholeSentenceTests(ChatTests):
    def test_cut_answer_keeps_only_whole_sentences(self):
        out = answer("πώς πήγε;", root=self.root, client=Fake(text="Πρώτη πρόταση. Δεύτερη πρόταση. Η αγορά ανταμείβεται, με"))
        self.assertTrue(out["answer"].startswith("Πρώτη πρόταση. Δεύτερη πρόταση."))
        self.assertNotIn("ανταμείβεται, με", out["answer"])

    def test_answer_without_a_full_stop_is_kept(self):
        self.assertEqual(answer("x", root=self.root, client=Fake(text="ναι"))["answer"], "ναι")


class HistoryModeTests(unittest.TestCase):
    def test_history_keeps_whether_the_model_answered(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "chat.sqlite3"
            save_turn("α", {"answer": "x", "sources": [], "unverified_numbers": [], "ollama": "not_needed",
                            "mode": "facts"}, path=db)
            save_turn("β", {"answer": "y", "sources": [], "unverified_numbers": [], "ollama": "ok"}, path=db)
            self.assertEqual([t["mode"] for t in history(path=db)], ["facts", "model"])

    def test_old_chat_table_gets_the_mode_column(self):
        import sqlite3
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "chat.sqlite3"
            conn = sqlite3.connect(db)
            conn.execute("CREATE TABLE chat (at TEXT NOT NULL, question TEXT NOT NULL, answer TEXT NOT NULL,"
                         " sources_json TEXT NOT NULL, unverified_json TEXT NOT NULL, ollama TEXT NOT NULL)")
            conn.execute("INSERT INTO chat VALUES ('t','παλιά','x','[]','[]','down')")
            conn.commit()
            conn.close()
            self.assertEqual([t["mode"] for t in history(path=db)], ["model"])


class SearchInChatTests(ChatTests):
    """Owner 2026-11-17: the chat also reads the chunks closest in meaning, cited by file and heading."""

    def test_search_hits_reach_the_prompt_and_the_sources(self):
        fake = Fake(text="Οι θέσεις του σοκ: καμία καθαρή διαφορά.")
        hit = {"path": "output/paper_learning/shock_exits_latest.md", "heading": "Σοκ",
               "text": "Οι θέσεις που έκλεισε το σοκ: καμία καθαρή διαφορά.", "mode": "meaning", "score": 0.8}
        out = answer("τι λέει ο Babis για τον κλάδο;", root=self.root, client=fake, searcher=lambda q: [hit])
        self.assertIn("καμία καθαρή διαφορά", fake.prompts[0][1])
        self.assertIn("output/paper_learning/shock_exits_latest.md — Σοκ", out["sources"])

    def test_a_failing_search_does_not_stop_the_answer(self):
        def broken(q):
            raise OSError("ollama down")
        out = answer("τι λέει ο Babis για τον κλάδο;", root=self.root, client=Fake(text="εντάξει"), searcher=broken)
        self.assertEqual(out["ollama"], "ok")
