"""Debate shadow (owner 2026-11-16, RULES_REVIEW section 12): a compact local-LLM debate on every approved trade,
in shadow. It reads, judges and records; it never touches an order."""
from __future__ import annotations

import ast
from datetime import date
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

import debate_shadow as ds

ROOT = Path(__file__).resolve().parents[1]
INTENT = {"intent_id": "i1", "agent": "yankos", "ticker": "MSGM", "company": "Motorsport Games Inc.",
          "trade_direction": "long", "playbook_name": "support_bounce_swing", "signal_score": 10.0,
          "strategy_rank_pct": 0.97, "market_state_fit": {"fit": "supported"},
          "trade_plan": {"initial_rationale": ["Price is near the 20-day low."], "invalidators": ["reclaim low breaks"],
                         "holding_horizon": {"sessions": 60}, "catalog_holding_horizon": "1-5 sessions"}}
THESIS = {"ticker": "MSGM", "fundamental_summary": {"giannis_tier": "Core Watchlist", "giannis_score": 9.0},
          "long_case": {"supporting_facts": ["positive_free_cash_flow"]},
          "short_case": {"supporting_facts": ["shrinking_revenue"]},
          "kill_criteria": ["Primary evidence becomes stale."], "readiness_status": "execution_ready"}
GOOD_TEXT = "SCORE: 3\nBEAR: Revenue shrinks.\nBULL: Cash flow is positive.\nRISK: Thin volume."
GOOD_LOGPROBS = [{"token": "3", "top_logprobs": [{"token": "3", "logprob": -0.105}, {"token": "4", "logprob": -2.303},
                                                {"token": "x", "logprob": -5.0}]}]
GOOD = (GOOD_TEXT, GOOD_LOGPROBS)


class Fake:
    def __init__(self, texts=None, fail=False):
        self.texts, self.fail, self.prompts = list(texts or []), fail, []

    def available(self):
        return True

    def generate(self, system, prompt):
        self.prompts.append(prompt)
        if self.fail:
            raise OSError("connection refused")
        return self.texts.pop(0) if self.texts else GOOD


class DebateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, rel, payload):
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    def koulis(self, intents, day="2026-11-17"):
        self.write("output/koulis_order_intents_latest.json", {"session_date": day, "approved_intents": intents})

    def rows(self):
        with closing(sqlite3.connect(self.root / "data" / "debate_shadow.sqlite3")) as conn:
            return conn.execute("SELECT intent_id, rating, bull FROM opinions ORDER BY priority").fetchall()

    def test_inputs_carry_both_cases_and_the_traders_reasons(self):
        inputs = ds.build_inputs(INTENT, THESIS, market={"trend": "down", "mood": "anxious"}, news=["Headline A"])
        prompt = ds.build_prompt(inputs)
        for text in ("positive_free_cash_flow", "shrinking_revenue", "Primary evidence becomes stale.",
                     "Price is near the 20-day low.", "reclaim low breaks", "Core Watchlist", "Headline A", "down"):
            self.assertIn(text, prompt)
        self.assertEqual(len(ds.inputs_hash(inputs)), 64)

    def test_missing_thesis_is_stated_not_invented(self):
        self.assertIn("unknown", ds.build_prompt(ds.build_inputs(INTENT, None, market=None, news=[])))

    def test_parse_good_and_bad_answers(self):
        good = ds.parse_opinion(*GOOD)
        self.assertEqual((good["score"], good["bear"], good["bull"], good["main_risk"]),
                         (3, "Revenue shrinks.", "Cash flow is positive.", "Thin volume."))
        self.assertAlmostEqual(good["expected_score"], 3.1, places=1)  # 0.9 on 3, 0.1 on 4
        self.assertEqual(ds.parse_opinion("no score here", [])["rating"], "unknown")
        self.assertEqual(ds.parse_opinion("", [])["rating"], "unknown")
        no_probs = ds.parse_opinion(GOOD_TEXT, [])
        self.assertEqual((no_probs["score"], no_probs["expected_score"]), (3, 3.0))  # the written digit

    def test_run_judges_in_koulis_order_and_skips_judged(self):
        self.koulis([INTENT, {**INTENT, "intent_id": "i2", "ticker": "AAA"}])
        fake = Fake()
        out = ds.run(self.root, client=fake, session_date="2026-11-17", deadline=lambda: False, guard=None)
        self.assertEqual(out["judged"], 2)
        self.assertEqual([r[0] for r in self.rows()], ["i1", "i2"])
        again = ds.run(self.root, client=Fake(), session_date="2026-11-17", deadline=lambda: False, guard=None)
        self.assertEqual((again["judged"], len(self.rows())), (0, 2))

    def test_another_days_file_is_not_debated(self):
        self.koulis([INTENT], day="2026-11-16")
        out = ds.run(self.root, client=Fake(), session_date="2026-11-17", deadline=lambda: False, guard=None)
        self.assertEqual(out["status"], "no_approvals_for_session")

    def test_deadline_stops_the_run(self):
        self.koulis([INTENT, {**INTENT, "intent_id": "i2"}])
        calls = iter([False, True])
        out = ds.run(self.root, client=Fake(), session_date="2026-11-17", deadline=lambda: next(calls), guard=None)
        self.assertEqual((out["judged"], out["stopped"]), (1, "deadline"))

    def test_model_failure_is_unknown_and_the_run_goes_on(self):
        self.koulis([INTENT])
        ds.run(self.root, client=Fake(fail=True), session_date="2026-11-17", deadline=lambda: False, guard=None)
        self.assertEqual(self.rows()[0][1], "unknown")

    def test_rows_are_immutable(self):
        self.koulis([INTENT])
        ds.run(self.root, client=Fake(), session_date="2026-11-17", deadline=lambda: False, guard=None)
        with closing(ds.connect(self.root / "data" / "debate_shadow.sqlite3")) as conn:
            self.assertFalse(ds.store(conn, {"session_date": "2026-11-17", "intent_id": "i1", "rating": "support"}))

    def test_no_broker_imports(self):
        tree = ast.parse((ROOT / "debate_shadow.py").read_text(encoding="utf-8"))
        names = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names |= {a.name.split(".")[0] for a in node.names}
            elif isinstance(node, ast.ImportFrom):
                names.add((node.module or "").split(".")[0])
        self.assertFalse({n for n in names if n.startswith("alpaca")} |
                         (names & {"paper_trade_ledger", "koulis_risk_gate", "thymios_order_intent", "paper_learning"}))


if __name__ == "__main__":
    unittest.main()


class GuardTests(unittest.TestCase):
    """The debate must never slow the live session: Miltos late twice in a row -> the debate stops for the day."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "output").mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    def miltos(self, iso):
        (self.root / "output" / "miltos_entry_stalker_latest.json").write_text(json.dumps({"run_timestamp": iso}),
                                                                               encoding="utf-8")

    def guard(self, now):
        return ds.SessionGuard(self.root, now=lambda: now, started_at=ds.parse_time("2026-11-17T09:30:00-05:00"))

    def test_two_late_checks_stop(self):
        self.miltos("2026-11-17T10:00:00-05:00")
        g = self.guard(ds.parse_time("2026-11-17T10:07:00-05:00"))  # 420 s > 300 + 60
        self.assertFalse(g.should_stop())
        self.assertTrue(g.should_stop())
        self.assertIn("Miltos", g.reason)

    def test_one_late_check_then_on_time_does_not_stop(self):
        self.miltos("2026-11-17T10:00:00-05:00")
        late = self.guard(ds.parse_time("2026-11-17T10:07:00-05:00"))
        self.assertFalse(late.should_stop())
        self.miltos("2026-11-17T10:06:30-05:00")
        self.assertFalse(late.should_stop())
        self.assertFalse(late.should_stop())

    def test_no_miltos_file_is_not_late(self):
        self.assertFalse(self.guard(ds.parse_time("2026-11-17T12:00:00-05:00")).should_stop())

    def test_a_miltos_file_from_before_the_start_is_not_late(self):
        self.miltos("2026-11-16T15:55:00-05:00")  # yesterday's, today's Miltos not started yet
        g = self.guard(ds.parse_time("2026-11-17T09:40:00-05:00"))
        self.assertFalse(g.should_stop() or g.should_stop())


class OllamaTests(unittest.TestCase):
    def test_starts_ollama_once_when_down(self):
        class Down:
            calls = 0

            def available(self):
                Down.calls += 1
                return Down.calls > 2

        started = []
        self.assertTrue(ds.ensure_ollama(Down(), starter=lambda: started.append(1) or True, wait_seconds=5,
                                         poll=lambda: None))
        self.assertEqual(started, [1])

    def test_ollama_that_never_comes_up(self):
        class Never:
            def available(self):
                return False

        self.assertFalse(ds.ensure_ollama(Never(), starter=lambda: True, wait_seconds=0.05, poll=lambda: None))

    def test_lowering_priority_never_raises(self):
        ds.lower_ollama_priority(runner=lambda *a, **k: (_ for _ in ()).throw(OSError("no powershell")))

    def test_deadline_is_four_pm_new_york(self):
        self.assertFalse(ds.past_stop(ds.parse_time("2026-11-17T15:59:00-05:00")))
        self.assertTrue(ds.past_stop(ds.parse_time("2026-11-17T16:00:00-05:00")))


def ledger(root, records, counterfactuals):
    path = root / "data" / "paper_learning.db"
    path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(path)) as conn:
        conn.execute("CREATE TABLE paper_trade_ledger (record_id TEXT PRIMARY KEY, session_date TEXT NOT NULL,"
                     " kind TEXT NOT NULL, complete INTEGER NOT NULL, record_json TEXT NOT NULL, recorded_at TEXT NOT NULL)")
        conn.execute("CREATE TABLE paper_counterfactuals (record_id TEXT PRIMARY KEY, open_close_pct REAL,"
                     " spy_open_close_pct REAL, computed_at TEXT NOT NULL)")
        for r in records:
            conn.execute("INSERT INTO paper_trade_ledger VALUES (?,?,?,?,?,?)",
                         (r["record_id"], r["session_date"], r["kind"], 1, json.dumps(r), "x"))
        for rid, oc, spy in counterfactuals:
            conn.execute("INSERT INTO paper_counterfactuals VALUES (?,?,?,?)", (rid, oc, spy, "x"))
        conn.commit()


class EvaluateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def opinion(self, conn, day, iid, ticker, rating, written="2026-11-17T15:00:00+00:00"):
        ds.store(conn, {"session_date": day, "intent_id": iid, "agent": "yankos", "ticker": ticker,
                        "playbook": "pb", "direction": "long", "rating": rating, "written_at": written})

    def test_outcomes_join_trades_and_counterfactuals_as_excess_over_spy(self):
        ledger(self.root, [
            {"record_id": "t1", "session_date": "2026-11-17", "kind": "trade", "agent": "yankos", "ticker": "AAA",
             "playbook": "pb", "direction": "long", "result_pct": 2.0, "entry_at": "2026-11-17T14:40:00+00:00"},
            {"record_id": "n1", "session_date": "2026-11-17", "kind": "approved_not_entered", "agent": "yankos",
             "ticker": "BBB", "playbook": "pb", "direction": "long"},
            {"record_id": "h1", "session_date": "2026-11-17", "kind": "trade", "agent": "yankos", "ticker": "CCC",
             "playbook": "pb", "direction": "long", "result_pct": -5.0, "holding_horizon_sessions": 60}],
            [("n1", -1.0, 0.5)])
        with closing(ds.connect(self.root / ds.DB)) as conn:
            self.opinion(conn, "2026-11-17", "i1", "AAA", "support")
            self.opinion(conn, "2026-11-17", "i2", "BBB", "oppose")
            self.opinion(conn, "2026-11-17", "i3", "CCC", "oppose")
            self.opinion(conn, "2026-11-17", "i4", "DDD", "unknown")
        rows = {r["ticker"]: r for r in ds.outcomes(self.root, as_of=date(2026, 11, 18))}
        self.assertEqual(rows["AAA"]["excess_pct"], 1.5)
        self.assertIs(rows["AAA"]["entered_before"], True)  # entered 14:40, opinion only at 15:00
        self.assertEqual(rows["BBB"]["excess_pct"], -1.5)
        self.assertIsNone(rows["CCC"]["excess_pct"])  # immature held trade
        self.assertIsNone(rows["DDD"]["excess_pct"])

    def test_verdicts(self):
        def rows(gap):
            out = []
            for d in range(10):
                for k in range(3):
                    day = f"2026-10-{d + 10:02d}"
                    out.append({"session_date": day, "rating": "support", "excess_pct": 1.0 + 0.1 * k})
                    out.append({"session_date": day, "rating": "oppose", "excess_pct": 1.0 - gap + 0.1 * k})
            return out
        self.assertEqual(ds.judge(rows(1.0))["verdict"], "veto_would_help")
        self.assertEqual(ds.judge(rows(0.2))["verdict"], "no_clear_difference")
        self.assertEqual(ds.judge(rows(1.0)[:20])["verdict"], "insufficient_sample")

    def test_evaluate_without_data_writes_insufficient(self):
        out = ds.evaluate(self.root, as_of=date(2026, 11, 18))
        self.assertEqual(out["verdict"], "insufficient_sample")
        self.assertTrue((self.root / "output" / "paper_learning" / "debate_shadow_latest.md").exists())
        data = json.loads((self.root / "output" / "paper_learning" / "debate_shadow_latest.json").read_text("utf-8"))
        self.assertFalse(data["active_decisions_changed"])



class RelativeRatingTests(unittest.TestCase):
    """2026-11-17: the 3b model said neutral to everything, then 8-9 to everything when asked for odds; asked to compare
    with a typical approved trade it spreads around 5. The rating comes from the score's place among the previous
    sessions' scores (only the past, so it could act in real time), not from the bare number."""

    def test_cold_start_uses_fixed_bounds(self):
        self.assertEqual(ds.rate(4.2, history=[]), ("oppose", "cold_start"))
        self.assertEqual(ds.rate(5.0, history=[]), ("neutral", "cold_start"))
        self.assertEqual(ds.rate(5.7, history=[]), ("support", "cold_start"))

    def test_terciles_of_the_previous_sessions(self):
        history = [3.0 + 0.04 * i for i in range(60)]  # 3.0 .. 5.36
        self.assertEqual(ds.rate(3.1, history=history)[0], "oppose")
        self.assertEqual(ds.rate(4.2, history=history)[0], "neutral")
        self.assertEqual(ds.rate(5.3, history=history), ("support", "terciles_of_60"))

    def test_unknown_score_stays_unknown(self):
        self.assertEqual(ds.rate(None, history=[]), ("unknown", None))

    def test_run_stores_score_and_relative_rating(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "output").mkdir()
            (root / "output" / "koulis_order_intents_latest.json").write_text(
                json.dumps({"session_date": "2026-11-17", "approved_intents": [INTENT]}), encoding="utf-8")
            ds.run(root, client=Fake(), session_date="2026-11-17", deadline=lambda: False, guard=None)
            with closing(sqlite3.connect(root / "data" / "debate_shadow.sqlite3")) as conn:
                row = conn.execute("SELECT rating, score, expected_score, rating_basis, prompt_version FROM opinions").fetchone()
        self.assertEqual(row[0], "oppose")
        self.assertEqual(row[1], 3)
        self.assertAlmostEqual(row[2], 3.1, places=1)
        self.assertEqual((row[3], row[4]), ("cold_start", "debate.v2"))

    def test_old_table_gets_the_new_columns(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "old.sqlite3"
            with closing(sqlite3.connect(path)) as conn:
                conn.execute("CREATE TABLE opinions (session_date TEXT NOT NULL, intent_id TEXT NOT NULL, rating TEXT NOT NULL,"
                             " PRIMARY KEY (session_date, intent_id))")
                conn.commit()
            with closing(ds.connect(path)) as conn:
                cols = {r[1] for r in conn.execute("PRAGMA table_info(opinions)")}
        self.assertTrue({"score", "expected_score", "rating_basis", "priority"} <= cols)
