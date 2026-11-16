"""Jarvis learning: loss rules found on older sessions must hold on newer ones; they enter shadow and change nothing."""
from __future__ import annotations

from contextlib import closing
from datetime import date, timedelta
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

import jarvis_learning as jl

POCKET = {"agent": "periklis", "playbook": "gap_fade", "direction": "short"}
OTHERS = [("yankos", "rsi2", "long"), ("yankos", "momentum_12_1", "long"), ("periklis", "support_bounce_swing", "long")]


def make_trades(sessions: int, *, pocket_loses_until: int | None = None, start: date = date(2026, 9, 1)) -> list[dict]:
    """Every session: 2 pocket trades and 6 others that alternate win/loss. The pocket loses in the first
    ``pocket_loses_until`` sessions (all of them by default), wins afterwards."""
    out = []
    for s in range(sessions):
        day = (start + timedelta(days=s)).isoformat()
        loses = pocket_loses_until is None or s < pocket_loses_until
        for k in range(2):
            out.append({"session_date": day, **POCKET, "holding_class": "day", "market_state": "up_calm",
                        "market_state_fit": "supported", "categories": {"standing": "rest"},
                        "result_pct": -1.0 if loses else 1.0})
        for k in range(6):
            agent, playbook, direction = OTHERS[k % 3]
            out.append({"session_date": day, "agent": agent, "playbook": playbook, "direction": direction,
                        "holding_class": "day", "market_state": "up_calm", "market_state_fit": "supported",
                        "categories": {"standing": "rest"}, "result_pct": 1.0 if (s + k) % 2 else -1.0})
    return out


def write_ledger(path: Path, trades: list[dict]) -> None:
    with closing(sqlite3.connect(path)) as conn:
        conn.execute("CREATE TABLE paper_trade_ledger (record_id TEXT PRIMARY KEY, session_date TEXT NOT NULL,"
                     " kind TEXT NOT NULL, complete INTEGER NOT NULL, record_json TEXT NOT NULL, recorded_at TEXT NOT NULL)")
        for i, t in enumerate(trades):
            conn.execute("INSERT INTO paper_trade_ledger VALUES (?,?,?,?,?,?)",
                         (f"r{i}", t["session_date"], t.get("kind", "trade"), 1, json.dumps(t), "x"))
        conn.commit()


def pocket_rule(rules: list[dict]) -> dict | None:
    for rule in rules:
        conds = rule["conditions"]
        if conds and all(POCKET.get(k) == v for k, v in conds.items()):
            return rule
    return None


class LearningTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_not_enough_sessions(self):
        self.assertEqual(jl.find_rules(make_trades(9))["status"], "not_enough_data")

    def test_finds_planted_losing_pocket(self):
        out = jl.find_rules(make_trades(20))
        rule = pocket_rule(out["rules"])
        self.assertIsNotNone(rule, out["rules"])
        self.assertTrue(rule["criterion_id"].startswith("jarvis_loss_rule:"))
        self.assertGreaterEqual(rule["test"]["n"], jl.TEST_MIN)
        self.assertEqual(rule["test"]["loss_rate_pct"], 100.0)

    def test_pocket_only_in_train_is_dropped(self):
        self.assertIsNone(pocket_rule(jl.find_rules(make_trades(20, pocket_loses_until=14))["rules"]))

    def test_split_is_by_session_not_by_row(self):
        out = jl.find_rules(make_trades(20))
        self.assertLess(out["train_sessions"][1], out["test_sessions"][0])
        self.assertEqual(out["sessions"], 20)

    def test_load_trades_keeps_only_trades_with_a_result(self):
        trades = make_trades(1)
        trades[0]["result_pct"] = None
        trades.append({**trades[1], "kind": "approved_not_entered"})
        db = self.root / "ledger.db"
        write_ledger(db, trades)
        self.assertEqual(len(jl.load_trades(db)), 7)

    def test_immature_held_trades_are_left_out(self):
        # owner 2026-11-15: a held trade is judged only after its planned window; before that the closed ones are
        # mostly the stopped ones (a bias towards losses)
        base = make_trades(1)[0]
        held = {**base, "holding_horizon_sessions": 60, "session_date": "2026-11-10", "result_pct": -3.0}
        ripe = {**base, "holding_horizon_sessions": 2, "session_date": "2026-11-10", "result_pct": -3.0}
        day = {**base, "session_date": "2026-11-12"}
        db = self.root / "ledger.db"
        write_ledger(db, [held, ripe, day])
        self.assertEqual(len(jl.load_trades(db, as_of=date(2026, 11, 13))), 2)
        self.assertEqual(len(jl.load_trades(db)), 3)  # no as_of: every trade with a result

    def test_unknown_feature_stays_unknown(self):
        trades = make_trades(1)[:1]
        del trades[0]["market_state"]
        db = self.root / "ledger.db"
        write_ledger(db, trades)
        self.assertEqual(jl.load_trades(db)[0]["market_state"], "unknown")

    def test_missing_ledger_is_empty(self):
        self.assertEqual(jl.load_trades(self.root / "none.db"), [])

    def test_record_rules_puts_lesson_in_shadow_and_is_idempotent(self):
        rules = jl.find_rules(make_trades(20))["rules"]
        intel = self.root / "market_intel.db"
        first = jl.record_rules(intel, rules)
        again = jl.record_rules(intel, rules)
        self.assertEqual([r["created"] for r in first], [True] * len(rules))
        self.assertEqual([r["created"] for r in again], [False] * len(rules))
        with closing(sqlite3.connect(intel)) as conn:
            statuses = {s for (s,) in conn.execute(
                "SELECT status FROM agent_lesson_candidates WHERE task_id='jarvis_learning'")}
        self.assertEqual(statuses, {"shadow"})

    def test_follow_uses_only_sessions_after_shadow(self):
        rules = jl.find_rules(make_trades(20))["rules"]
        intel = self.root / "market_intel.db"
        jl.record_rules(intel, rules)
        old = make_trades(20)  # before the lesson entered shadow: never counted
        later = [t for t in make_trades(5, start=date(2099, 1, 1)) if t["agent"] == "periklis" and t["playbook"] == "gap_fade"]
        item = next(i for i in jl.follow(intel, old + later[:9]) if i["criterion_id"] == pocket_rule(rules)["criterion_id"])
        self.assertEqual((item["verdict"], item["n"]), ("collecting", 9))
        item = next(i for i in jl.follow(intel, old + later) if i["criterion_id"] == pocket_rule(rules)["criterion_id"])
        self.assertEqual(item["verdict"], "confirmed")
        proposal = jl.follow_proposal(item)
        self.assertEqual((proposal.source, proposal.source_key, proposal.evidence["verdict"]),
                         ("jarvis_learning", item["lesson_id"], "confirmed"))

    def test_collecting_is_not_a_proposal(self):
        self.assertIsNone(jl.follow_proposal({"verdict": "collecting", "lesson_id": "x"}))


if __name__ == "__main__":
    unittest.main()
