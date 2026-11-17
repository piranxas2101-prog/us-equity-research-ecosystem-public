"""Jarvis's document search (owner 2026-11-17, the OpenJarvis deep-research idea): the ecosystem's reports cut into
chunks, embedded locally (bge-m3 through Ollama), searched by meaning; words only when the model is not there.
Read-only, local, cites the file and heading."""
from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

import jarvis_search as js


class FakeEmbedder:
    """Maps a text to a 3-d vector by the topics it mentions, so meaning is testable without a model."""
    model = "fake"

    def __init__(self, up: bool = True):
        self.up, self.calls = up, 0

    def available(self) -> bool:
        return self.up

    def embed(self, texts: list[str]) -> list[list[float]]:
        self.calls += 1
        out = []
        for t in texts:
            t = t.lower()
            out.append([1.0 if any(w in t for w in ("αγορ", "spy", "market")) else 0.0,
                        1.0 if any(w in t for w in ("stop", "σοκ", "έξοδ", "shock")) else 0.0,
                        1.0 if any(w in t for w in ("giannis", "θεμελιώδ", "fundamental")) else 0.0])
        return out


class SearchTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        out = self.root / "output"
        (out / "paper_learning").mkdir(parents=True)
        (out / "market_state_review_latest.md").write_text(
            "# Αγορά\n\nΗ αγορά (SPY) πέφτει εδώ και 11 συνεδρίες.\n\n## Τομείς\n\nΤα υλικά πάνε καλύτερα.\n", encoding="utf-8")
        (out / "paper_learning" / "shock_exits_latest.md").write_text(
            "# Σοκ\n\nΟι θέσεις που έκλεισε το σοκ: καμία καθαρή διαφορά.\n", encoding="utf-8")
        (out / "alpaca_preflight_latest.md").write_text("# account\n\nsecret-ish broker details\n", encoding="utf-8")
        (self.root / "docs").mkdir()
        (self.root / "docs" / "RULES_REVIEW.md").write_text("# Rules\n\n## T3\n\nGiannis fundamental score.\n",
                                                             encoding="utf-8")
        (self.root / ".env").write_text("SECRET=1\n", encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def test_chunks_keep_their_heading(self):
        chunks = js.chunk("# Α\n\nένα\n\n## Β\n\nδύο\n", max_chars=50)
        self.assertEqual([(c["heading"], c["text"]) for c in chunks], [("Α", "ένα"), ("Β", "δύο")])

    def test_long_paragraphs_are_split_to_the_budget(self):
        chunks = js.chunk("# Α\n\n" + "λέξη " * 400, max_chars=300)
        self.assertTrue(all(len(c["text"]) <= 300 for c in chunks))
        self.assertGreater(len(chunks), 3)

    def test_sources_skip_the_broker_and_secrets(self):
        names = {p.name for p in js.source_files(self.root)}
        self.assertIn("market_state_review_latest.md", names)
        self.assertIn("RULES_REVIEW.md", names)
        self.assertNotIn("alpaca_preflight_latest.md", names)
        self.assertNotIn(".env", names)

    def test_index_then_search_by_meaning(self):
        embedder = FakeEmbedder()
        stats = js.build(self.root, embedder)
        self.assertGreater(stats["chunks"], 0)
        hits = js.search("τι έγινε με τις εξόδους μετά το shock;", self.root, embedder, k=1)
        self.assertEqual(hits[0]["path"], "output/paper_learning/shock_exits_latest.md")
        self.assertEqual(hits[0]["heading"], "Σοκ")

    def test_rebuild_only_changed_files(self):
        embedder = FakeEmbedder()
        js.build(self.root, embedder)
        calls = embedder.calls
        self.assertEqual(js.build(self.root, embedder)["embedded_files"], 0)
        self.assertEqual(embedder.calls, calls)
        (self.root / "output" / "market_state_review_latest.md").write_text("# Αγορά\n\nνέο\n", encoding="utf-8")
        self.assertEqual(js.build(self.root, embedder)["embedded_files"], 1)

    def test_deleted_files_leave_the_index(self):
        embedder = FakeEmbedder()
        js.build(self.root, embedder)
        (self.root / "output" / "paper_learning" / "shock_exits_latest.md").unlink()
        js.build(self.root, embedder)
        paths = {h["path"] for h in js.search("σοκ", self.root, embedder, k=10)}
        self.assertNotIn("output/paper_learning/shock_exits_latest.md", paths)

    def test_without_the_model_search_falls_back_to_words(self):
        hits = js.search("Giannis fundamental", self.root, FakeEmbedder(up=False), k=1)
        self.assertEqual((hits[0]["path"], hits[0]["mode"]), ("docs/RULES_REVIEW.md", "words"))

    def test_build_respects_its_time_budget(self):
        stats = js.build(self.root, FakeEmbedder(), budget_seconds=0)
        self.assertEqual(stats["embedded_files"], 0)
        self.assertTrue(stats["stopped_for_time"])


if __name__ == "__main__":
    unittest.main()
