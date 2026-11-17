"""The debate prepares the day (owner 2026-11-18): in the morning, after the nightly run, it judges every trade the
traders proposed for today (verdict 'candidate'), with no cap, stopping only at 14:30 Athens as a safety; then it is
measured against every kind of outcome the ledger keeps, not only the approvals."""
from __future__ import annotations

from contextlib import closing
from datetime import date, datetime
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from zoneinfo import ZoneInfo

import debate_shadow as ds

GOOD = ("SCORE: 3\nBEAR: weak.\nBULL: ok.\nRISK: thin.", [{"token": "3", "top_logprobs": [{"token": "3", "logprob": 0.0}]}])


class Fake:
    model = "fake"

    def __init__(self):
        self.prompts = []

    def available(self):
        return True

    def generate(self, system, prompt):
        self.prompts.append(prompt)
        return GOOD


def row(ticker: str, score: float, verdict: str = "candidate", direction: str = "long") -> dict:
    return {"ticker": ticker, "company": f"{ticker} Inc", "playbook_name": "support_bounce_swing", "signal_score": score,
            "trade_direction": direction, "signal_verdict": verdict, "rationale": [f"{ticker} near support."],
            "fundamental_long_case": {"supporting_facts": ["positive_free_cash_flow"]},
            "fundamental_short_case": {"supporting_facts": []}, "fundamental_kill_criteria": ["stale evidence"],
            "giannis_score": 8.0, "risk_level": "Low",
            "paper_trade_plan": {"catalog_holding_horizon": "1-5 sessions", "invalidators": ["reclaim low breaks"]}}


class MorningTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        out = self.root / "output"
        out.mkdir()
        (out / "yankos_strategy_latest.json").write_text(json.dumps({"results": [
            row("AAA", 7.0), row("BBB", 9.5), row("CCC", 9.9, verdict="watch")]}), encoding="utf-8")
        (out / "periklis_strategy_latest.json").write_text(json.dumps({"results": [
            row("DDD", 8.0, direction="short")]}), encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def test_all_candidates_strongest_first_and_no_watch(self):
        intents = ds.load_candidates(self.root)
        self.assertEqual([(i["agent"], i["ticker"]) for i in intents],
                         [("yankos", "BBB"), ("periklis", "DDD"), ("yankos", "AAA")])
        self.assertEqual(intents[0]["intent_id"], "yankos|BBB|support_bounce_swing|long")

    def test_candidate_carries_the_traders_reasons_and_the_cases(self):
        intent = ds.load_candidates(self.root)[0]
        prompt = ds.build_prompt(ds.build_inputs(intent, ds.candidate_thesis(intent), market=None, news=[]))
        for text in ("BBB near support.", "reclaim low breaks", "positive_free_cash_flow", "stale evidence"):
            self.assertIn(text, prompt)

    def test_morning_run_judges_them_without_a_koulis_file(self):
        out = ds.run(self.root, client=Fake(), session_date="2026-11-18", deadline=lambda: False, guard=None,
                     intents=ds.load_candidates(self.root))
        self.assertEqual(out["judged"], 3)

    def test_safety_stop_is_half_past_two_in_athens(self):
        athens = ZoneInfo("Europe/Athens")
        self.assertFalse(ds.past_morning_stop(datetime(2026, 11, 18, 14, 29, tzinfo=athens)))
        self.assertTrue(ds.past_morning_stop(datetime(2026, 11, 18, 14, 30, tzinfo=athens)))


class EveryOutcomeTests(unittest.TestCase):
    def test_shadow_candidates_count_and_a_trade_wins_over_its_shadow(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db = root / "data" / "paper_learning.db"
            db.parent.mkdir(parents=True)
            with closing(sqlite3.connect(db)) as conn:
                conn.execute("CREATE TABLE paper_trade_ledger (record_id TEXT PRIMARY KEY, session_date TEXT, kind TEXT,"
                             " complete INTEGER, record_json TEXT, recorded_at TEXT)")
                conn.execute("CREATE TABLE paper_counterfactuals (record_id TEXT PRIMARY KEY, open_close_pct REAL,"
                             " spy_open_close_pct REAL, computed_at TEXT)")
                base = {"session_date": "2026-11-18", "agent": "yankos", "playbook": "pb", "direction": "long"}
                for rid, kind, ticker, extra in (("s1", "shadow_candidate", "AAA", {}),
                                                 ("s2", "shadow_candidate", "BBB", {}),
                                                 ("t2", "trade", "BBB", {"result_pct": 4.0})):
                    conn.execute("INSERT INTO paper_trade_ledger VALUES (?,?,?,?,?,?)",
                                 (rid, "2026-11-18", kind, 1, json.dumps({**base, "ticker": ticker, **extra}), "x"))
                conn.executemany("INSERT INTO paper_counterfactuals VALUES (?,?,?,?)",
                                 [("s1", -2.0, 0.5, "x"), ("s2", 1.0, 0.5, "x")])
                conn.commit()
            with closing(ds.connect(root / ds.DB)) as conn:
                for iid, ticker in (("i1", "AAA"), ("i2", "BBB")):
                    ds.store(conn, {"session_date": "2026-11-18", "intent_id": iid, "agent": "yankos", "ticker": ticker,
                                    "playbook": "pb", "direction": "long", "rating": "oppose", "written_at": "x"})
            rows = {r["ticker"]: r for r in ds.outcomes(root, as_of=date(2026, 11, 19))}
        self.assertEqual((rows["AAA"]["kind"], rows["AAA"]["excess_pct"]), ("shadow_candidate", -2.5))
        self.assertEqual((rows["BBB"]["kind"], rows["BBB"]["excess_pct"]), ("trade", 3.5))


class JarvisMorningTests(unittest.TestCase):
    def test_jarvis_debates_in_the_morning_on_weekdays_only_and_not_at_night(self):
        from unittest import mock
        import jarvis
        calls = []
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch("jarvis.run_pass", return_value={"open_proposals": 0, "problems": 0}), \
                mock.patch("jarvis.keep_awake"), \
                mock.patch("jarvis.run_debate_morning", side_effect=lambda root, day: calls.append(day)):
            with mock.patch("jarvis.morning_session_day", return_value=date(2026, 11, 18)):
                jarvis.main(["after-nightly", "--root", tmp, "--today", "2026-11-17"])
                jarvis.main(["after-session", "--root", tmp, "--today", "2026-11-18"])
            with mock.patch("jarvis.morning_session_day", return_value=date(2026, 11, 21)):  # Saturday
                jarvis.main(["after-nightly", "--root", tmp, "--today", "2026-11-20"])
        self.assertEqual(calls, [date(2026, 11, 18)])
        self.assertFalse(hasattr(jarvis, "run_debate_after_close"))

    def test_the_morning_prepares_todays_athens_session_even_before_new_york_midnight(self):
        # the nightly ends around 06:30 Athens, when New York still has the day before
        import jarvis
        athens = ZoneInfo("Europe/Athens")
        self.assertEqual(jarvis.morning_session_day(datetime(2026, 11, 18, 6, 30, tzinfo=athens)), date(2026, 11, 18))
        self.assertEqual(jarvis.morning_session_day(datetime(2026, 11, 18, 11, 0, tzinfo=athens)), date(2026, 11, 18))
        # Saturday morning after Friday's nightly: no session is coming
        self.assertEqual(jarvis.morning_session_day(datetime(2026, 11, 21, 6, 30, tzinfo=athens)).weekday(), 5)


if __name__ == "__main__":
    unittest.main()
