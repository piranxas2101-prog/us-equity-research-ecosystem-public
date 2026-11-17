"""Risk tiers for Jarvis's own changes (owner 2026-11-17, after OpenJarvis): a fixed table decides AUTO or REVIEW;
AUTO only for Jarvis's chat settings inside bounds, logged, undone when it makes timeouts worse."""
from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile
import unittest

import jarvis_chat
import jarvis_tiers as jt


class TierTableTests(unittest.TestCase):
    def test_only_chat_settings_are_auto(self):
        self.assertEqual(jt.tier_for("chat_setting"), "AUTO")
        for source in ("check", "paper_learning", "jarvis_learning", "run", "debate", "something_new"):
            self.assertEqual(jt.tier_for(source), "REVIEW", source)

    def test_trading_kinds_can_never_be_auto(self):
        for kind in jt.NEVER_AUTO:
            self.assertEqual(jt.tier_for(kind), "REVIEW")
        self.assertTrue({"score", "limit", "strategy", "order", "position"} <= set(jt.NEVER_AUTO))

    def test_settings_are_clamped_to_bounds(self):
        self.assertEqual(jt.clamp("max_total_chars", 99999), 2400)
        self.assertEqual(jt.clamp("max_total_chars", 10), 800)
        with self.assertRaises(KeyError):
            jt.clamp("num_thread", 12)  # not an AUTO setting


class AutoTuneTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.db = self.root / "data" / "jarvis" / "chat.sqlite3"
        self.t0 = datetime(2026, 11, 17, 10, 0, tzinfo=timezone.utc)
        self.n = 0

    def tearDown(self):
        self.tmp.cleanup()

    def turns(self, outcomes: list[str], seconds: float = 40.0):
        for outcome in outcomes:
            self.n += 1
            jarvis_chat.save_turn(f"q{self.n}", {"answer": "a", "sources": [], "unverified_numbers": [],
                                                  "ollama": "down" if outcome == "timeout" else "ok", "mode": "model",
                                                  "seconds": seconds, "outcome": outcome}, path=self.db)

    def settings(self):
        return json.loads((self.root / "data" / "jarvis" / "settings.json").read_text(encoding="utf-8"))

    def test_too_few_answers_change_nothing(self):
        self.turns(["timeout"] * 5)
        self.assertEqual(jt.tune(self.root, base=1800), [])

    def test_timeouts_lower_the_budget(self):
        self.turns(["ok"] * 16 + ["timeout"] * 4)
        changes = jt.tune(self.root, base=1800)
        self.assertEqual((changes[0]["setting"], changes[0]["old"], changes[0]["new"], changes[0]["tier"]),
                         ("max_total_chars", 1800, 1440, "AUTO"))
        self.assertEqual(jt.effective(self.root, "max_total_chars", 1800), 1440)

    def test_fast_clean_answers_raise_the_budget(self):
        self.turns(["ok"] * 20, seconds=15.0)
        self.assertEqual(jt.tune(self.root, base=1800)[0]["new"], 1980)

    def test_a_change_that_made_timeouts_worse_is_undone(self):
        self.turns(["ok"] * 17 + ["timeout"] * 3)
        jt.tune(self.root, base=1800)                    # 1800 -> 1440 (3 of 20 timed out)
        self.turns(["ok"] * 14 + ["timeout"] * 6)        # worse after the change
        changes = jt.tune(self.root, base=1800)
        self.assertEqual((changes[0]["action"], changes[0]["new"]), ("undone", 1800))
        self.assertEqual(jt.effective(self.root, "max_total_chars", 1800), 1800)

    def test_a_change_waits_for_twenty_new_answers(self):
        self.turns(["ok"] * 16 + ["timeout"] * 4)
        jt.tune(self.root, base=1800)
        self.turns(["timeout"] * 5)
        self.assertEqual(jt.tune(self.root, base=1800), [])

    def test_every_change_is_logged(self):
        self.turns(["ok"] * 16 + ["timeout"] * 4)
        jt.tune(self.root, base=1800)
        self.assertEqual(len(self.settings()["history"]), 1)

    def test_facts_answers_are_not_counted(self):
        for _ in range(25):
            jarvis_chat.save_turn("q", {"answer": "a", "sources": [], "unverified_numbers": [], "ollama": "not_needed",
                                        "mode": "facts", "seconds": 0.1, "outcome": "ok"}, path=self.db)
        self.assertEqual(jt.tune(self.root, base=1800), [])


class ChatTimingTests(unittest.TestCase):
    def test_answer_is_timed_and_a_timeout_is_marked(self):
        class Slow:
            def available(self):
                return True

            def generate(self, system, prompt):
                raise TimeoutError("timed out")

        with tempfile.TemporaryDirectory() as tmp:
            out = jarvis_chat.answer("γιατί έκλεισε η EFX;", root=Path(tmp), client=Slow())
        self.assertEqual(out["outcome"], "timeout")
        self.assertIsInstance(out["seconds"], float)

    def test_chat_uses_the_tuned_budget(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "data" / "jarvis").mkdir(parents=True)
            (root / "data" / "jarvis" / "settings.json").write_text(
                json.dumps({"max_total_chars": 1000, "history": []}), encoding="utf-8")
            self.assertEqual(jt.effective(root, "max_total_chars", 1800), 1000)


if __name__ == "__main__":
    unittest.main()
