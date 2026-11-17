"""What waits for the owner (owner 2026-11-17: Jarvis gives the approvals I have to give for the ecosystem): every
open decision in one list - Jarvis's proposals, classification reviews, trained models awaiting promotion, labs to
build - read-only, unknown when a source cannot be read."""
from __future__ import annotations

from contextlib import closing
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

import jarvis_owner
import jarvis_state
from jarvis_facts import answer_facts


class OwnerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        data = self.root / "data"
        data.mkdir()
        with closing(sqlite3.connect(data / "research_registry.db")) as conn:
            conn.execute("CREATE TABLE classification_review_item (ticker TEXT, reason TEXT, status TEXT)")
            conn.executemany("INSERT INTO classification_review_item VALUES (?,?,?)",
                             [("A", "no_two_sources_agree", "open"), ("B", "no_two_sources_agree", "open"),
                              ("C", "probable_spac_needs_second_signal", "open"), ("D", "no_two_sources_agree", "closed")])
            conn.commit()
        with closing(sqlite3.connect(data / "market_intel.db")) as conn:
            conn.execute("CREATE TABLE agent_model_registry (model_id TEXT, agent_id TEXT, state TEXT)")
            conn.executemany("INSERT INTO agent_model_registry VALUES (?,?,?)",
                             [("m1", "petros", "candidate"), ("m2", "petros", "candidate"), ("m3", "petros", "champion")])
            conn.commit()
        with closing(jarvis_state.connect(data / "jarvis" / "proposals.sqlite3")) as conn:
            jarvis_state.upsert_proposal(conn, jarvis_state.Proposal(
                source="run", source_key="resume:r1", title="Το run r1 σταμάτησε: συνέχεια;",
                evidence={"verdict": "failed"}), session_date="2026-11-17")
        report = self.root / "output" / "jarvis" / "report_latest.json"
        report.parent.mkdir(parents=True)
        report.write_text(json.dumps({"sections": {"checks": {"results": [
            {"id": "T3_volume_basis", "title": "T3: βάση του όγκου", "state": "needs_lab"},
            {"id": "flip", "title": "flip", "state": "waiting"}]}}}), encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def test_every_open_decision_is_counted(self):
        groups = {g["kind"]: g for g in jarvis_owner.pending(self.root)["groups"]}
        self.assertEqual(groups["proposals"]["count"], 1)
        self.assertEqual(groups["classification"]["count"], 3)
        self.assertEqual(groups["models"]["count"], 2)
        self.assertEqual(groups["labs"]["count"], 1)
        self.assertIn("T3: βάση του όγκου", groups["labs"]["items"])

    def test_an_unreadable_source_is_unknown(self):
        (self.root / "data" / "research_registry.db").unlink()
        groups = {g["kind"]: g for g in jarvis_owner.pending(self.root)["groups"]}
        self.assertIsNone(groups["classification"]["count"])

    def test_the_story_is_in_sentences(self):
        text = jarvis_owner.story(jarvis_owner.pending(self.root))
        self.assertTrue(text.startswith("Περιμένουν εσένα"), text)
        for part in ("1 πρόταση του Jarvis", "Το run r1 σταμάτησε", "3 σύμβολα στον έλεγχο κατηγοριοποίησης",
                     "2 όπου διαφωνούν οι πηγές", "2 εκπαιδευμένα μοντέλα", "1 εργαστήριο"):
            self.assertIn(part, text)

    def test_chat_answers_without_the_model(self):
        out = answer_facts("Τι εγκρίσεις έχω να δώσω;", self.root)
        self.assertEqual(out["mode"], "facts")
        self.assertTrue(out["answer"].startswith("Περιμένουν εσένα"))

    def test_nothing_waiting(self):
        empty = Path(self.tmp.name) / "empty"
        empty.mkdir()
        self.assertIn("άγνωστο", jarvis_owner.story(jarvis_owner.pending(empty)))


if __name__ == "__main__":
    unittest.main()
