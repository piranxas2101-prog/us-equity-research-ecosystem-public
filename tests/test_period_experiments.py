"""The measurement period's pre-registered experiments: one yardstick, one observation per session, Holm, verdicts
only after the judge date, unknown categories in neither group, and the ledger opened read-only."""
from __future__ import annotations

from datetime import date
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

import period_experiments as pe

ROOT = Path(__file__).resolve().parents[1]
CELL = {"agent": "yankos", "playbook": "support_bounce_swing", "direction": "long"}
TAG = {"id": "T", "title": "t", "lesson_ids": ["l1"], "kind": "tag_in_cell", **CELL, "tag": "market_cap",
       "value": "mega", "lean": "worse"}
BOOK = {"id": "B", "title": "b", "lesson_ids": ["l2"], "kind": "cell_vs_book", **CELL, "lean": "worse"}
HOLD = {"id": "H", "title": "h", "lesson_ids": ["l3"], "kind": "hold_in_cell", **CELL, "lean": "better",
        "short": {"holding_class": "week", "rule": "fixed"}, "long": {"holding_class": "month", "rule": "fixed"},
        "judge_date": "2027-01-25"}
WEEK, MONTH = ("week", "fixed"), ("month", "fixed")


def config(*experiments: dict) -> dict:
    return {"period": {"id": "P", "start": "2026-11-23", "end": "2026-12-18", "judge_date": "2026-12-29"},
            "yardstick": {"holding_class": "week", "rule": "fixed", "description": "d"}, "min_sessions": 15,
            "alpha": 0.05, "experiments": list(experiments)}


def row(day: int, excess: float, *, cap: str | None = "mid", cell: dict | None = None, month: float | None = None,
        week: bool = True) -> dict:
    cats = {} if cap is None else {"market_cap": cap}
    holds = {WEEK: excess} if week else {}
    if month is not None:
        holds[MONTH] = month
    return {"session_date": f"2026-12-{day:02d}", "kind": "shadow_candidate", **(cell or CELL),
            "categories": cats, "holds": holds}


def days(n: int) -> list[int]:
    return list(range(1, n + 1))


class PeriodExperimentsTests(unittest.TestCase):
    def test_the_registry_is_valid_and_names_the_kept_lessons(self) -> None:
        cfg = pe.load_config(ROOT / "period_experiments.json")
        self.assertEqual(len(cfg["experiments"]), 6)
        self.assertEqual(sum(len(e["lesson_ids"]) for e in cfg["experiments"]), 6)
        self.assertLess(cfg["period"]["end"], cfg["period"]["judge_date"])
        e1a = next(e for e in cfg["experiments"] if e["id"] == "E1a")  # owner 2026-11-18: the hold, not the entry
        self.assertEqual((e1a["kind"], e1a["lean"], e1a["judge_date"]), ("hold_in_cell", "better", "2027-01-25"))
        self.assertEqual(pe.measures(cfg), {WEEK, MONTH})

    def test_bad_registries_are_refused(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "c.json"
            for bad in (config(TAG, TAG), config({**TAG, "kind": "other"}), config({**TAG, "value": "unknown"}),
                        config({**HOLD, "long": HOLD["short"]}), config({**HOLD, "long": {}}),
                        config({**HOLD, "judge_date": "2026-12-10"}),
                        {**config(TAG), "period": {"id": "P", "start": "2026-12-18", "end": "2026-12-01",
                                                   "judge_date": "2026-12-29"}}):
                path.write_text(json.dumps(bad), encoding="utf-8")
                with self.assertRaises(ValueError):
                    pe.load_config(path)

    def test_unknown_category_is_in_neither_group(self) -> None:
        rows = [row(1, -1.0, cap="mega"), row(1, 1.0, cap="mid"), row(1, 9.0, cap="unknown"), row(1, 9.0, cap=None)]
        inside, rest = pe.groups(rows, TAG)
        self.assertEqual([r["excess_pct"] for r in inside], [-1.0])
        self.assertEqual([r["excess_pct"] for r in rest], [1.0])

    def test_cell_vs_book_compares_with_every_other_candidate(self) -> None:
        other = {"agent": "periklis", "playbook": "momentum_12_1", "direction": "long"}
        inside, rest = pe.groups([row(1, 1.0), row(1, 2.0, cell=other)], BOOK)
        self.assertEqual((len(inside), len(rest)), (1, 1))

    def test_one_observation_per_session(self) -> None:
        # ten candidates on one day count as one difference, not ten cases
        rows = [row(1, -1.0, cap="mega") for _ in range(10)] + [row(1, 1.0), row(2, -1.0, cap="mega")]
        diffs = pe.daily_differences(*pe.groups(rows, TAG))
        self.assertEqual(diffs, [("2026-12-01", -2.0)])  # day 2 has no comparison group: left out

    def test_holm_adjusts_and_an_undefined_test_still_counts_in_m(self) -> None:
        out = pe.holm({"a": 0.01, "b": 0.04, "c": None, "d": 0.03})
        self.assertEqual(out["c"], None)
        self.assertAlmostEqual(out["a"], 0.04)  # m = 4: the undefined test counts as p = 1
        self.assertAlmostEqual(out["d"], 0.09)
        self.assertAlmostEqual(out["b"], 0.09)  # monotone: never below the previous step

    def test_hold_in_cell_compares_the_same_candidates_held_two_ways(self) -> None:
        rows = [row(1, 1.0, month=-1.0), row(1, 3.0, month=0.0), row(1, 9.0),  # month not matured: left out
                row(1, 5.0, month=-5.0, cell={"agent": "periklis", "playbook": "x", "direction": "long"})]
        short, long = pe.groups(rows, HOLD)
        self.assertEqual(([r["excess_pct"] for r in short], [r["excess_pct"] for r in long]), ([1.0, 3.0], [-1.0, 0.0]))
        self.assertEqual(pe.daily_differences(short, long), [("2026-12-01", 2.5)])

    def test_an_experiment_with_its_own_judge_date_waits_and_counts_as_one_for_the_others(self) -> None:
        rows = [r for d in days(18) for r in (row(d, -2.0 - d % 3, cap="mega", month=-5.0 - d % 2), row(d, 1.0))]
        cfg = config(TAG, HOLD)
        early = pe.judge(cfg, rows, today=date(2026, 12, 29), start=date(2026, 12, 1), end=date(2026, 12, 18))
        tag, hold = early["experiments"]
        self.assertEqual((early["verdict"], tag["verdict"], hold["verdict"]), ("judged", "passed", "collecting"))
        alone = pe.judge(config(TAG), rows, today=date(2026, 12, 29), start=date(2026, 12, 1), end=date(2026, 12, 18))
        self.assertGreaterEqual(tag["p_holm"], alone["experiments"][0]["p_holm"])  # m = 2 even before H is judged
        late = pe.judge(cfg, rows, today=date(2027, 1, 25), start=date(2026, 12, 1), end=date(2026, 12, 18))
        self.assertEqual(late["experiments"][1]["verdict"], "passed")  # week beat month on every day

    def test_before_the_judge_date_nothing_is_decided(self) -> None:
        rows = [r for d in days(18) for r in (row(d, -2.0 - d % 3, cap="mega"), row(d, 1.0))]
        result = pe.judge(config(TAG), rows, today=date(2026, 12, 28), start=date(2026, 12, 1), end=date(2026, 12, 18))
        self.assertEqual(result["verdict"], "collecting")
        self.assertEqual(result["experiments"][0]["verdict"], "collecting")
        self.assertEqual(result["experiments"][0]["sessions"], 18)

    def test_after_the_judge_date_a_consistent_effect_passes(self) -> None:
        rows = [r for d in days(18) for r in (row(d, -2.0 - d % 3, cap="mega"), row(d, 1.0))]
        result = pe.judge(config(TAG), rows, today=date(2026, 12, 29), start=date(2026, 12, 1), end=date(2026, 12, 18))
        self.assertEqual(result["verdict"], "judged")
        self.assertEqual(result["experiments"][0]["verdict"], "passed")

    def test_an_effect_in_the_other_direction_fails(self) -> None:
        rows = [r for d in days(18) for r in (row(d, 2.0 + d % 3, cap="mega"), row(d, 1.0))]
        result = pe.judge(config(TAG), rows, today=date(2026, 12, 29), start=date(2026, 12, 1), end=date(2026, 12, 18))
        self.assertEqual(result["experiments"][0]["verdict"], "failed")

    def test_too_few_sessions_is_insufficient_not_failed(self) -> None:
        rows = [r for d in days(10) for r in (row(d, -2.0 - d % 3, cap="mega"), row(d, 1.0))]
        result = pe.judge(config(TAG), rows, today=date(2026, 12, 29), start=date(2026, 12, 1), end=date(2026, 12, 18))
        self.assertEqual(result["experiments"][0]["verdict"], "insufficient_sample")

    def test_sessions_outside_the_window_do_not_count(self) -> None:
        rows = [r for d in days(18) for r in (row(d, -2.0 - d % 3, cap="mega"), row(d, 1.0))]
        result = pe.judge(config(TAG), rows, today=date(2026, 12, 29), start=date(2026, 12, 10), end=date(2026, 12, 18))
        self.assertEqual(result["experiments"][0]["sessions"], 9)

    def test_the_ledger_is_read_only_and_missing_tables_give_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "paper_learning.db"
            self.assertEqual(pe.load_rows(db, {WEEK}), [])  # no file: nothing created
            self.assertFalse(db.exists())
            sqlite3.connect(db).close()
            self.assertEqual(pe.load_rows(db, {WEEK}), [])
            with sqlite3.connect(db) as conn:
                conn.execute("CREATE TABLE paper_trade_ledger (record_id TEXT, session_date TEXT, kind TEXT,"
                             " complete INTEGER, record_json TEXT, recorded_at TEXT)")
                conn.execute("CREATE TABLE paper_holding_shadows (record_id TEXT, holding_class TEXT, rule TEXT,"
                             " excess_pct REAL)")
                conn.execute("INSERT INTO paper_trade_ledger VALUES ('r1','2026-12-01','trade',1,?, 't')",
                             (json.dumps({**CELL, "categories": {"market_cap": "mega"}}),))
                conn.execute("INSERT INTO paper_holding_shadows VALUES ('r1','week','fixed',-1.5)")
                conn.execute("INSERT INTO paper_holding_shadows VALUES ('r1','day','fixed',9.0)")
            conn.close()
            rows = pe.load_rows(db, {WEEK})
            self.assertEqual([(r["kind"], r["holds"]) for r in rows], [("trade", {WEEK: -1.5})])
            rows = pe.load_rows(db, {WEEK, ("day", "fixed")})
            self.assertEqual(rows[0]["holds"], {WEEK: -1.5, ("day", "fixed"): 9.0})

    def test_jarvis_runs_it_and_raises_only_a_judged_period(self) -> None:
        import jarvis_checks

        self.assertEqual(jarvis_checks.PRODUCERS["period_experiments"], ["period_experiments.py"])
        check = next(c for c in jarvis_checks.load_registry(ROOT / "jarvis_checks.json")
                     if c["id"] == "period_experiments_p1")
        self.assertTrue(check["report_only"])  # proposals come per experiment (jarvis_decisions)
        self.assertEqual(check["output"], "output/paper_learning/period_experiments_latest.json")

    def test_cli_writes_the_report(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "period_experiments.json").write_text(json.dumps(config(TAG)), encoding="utf-8")
            self.assertEqual(pe.main(["--root", str(root), "--today", "2026-11-20"]), 0)
            out = json.loads((root / "output" / "paper_learning" / "period_experiments_latest.json"
                              ).read_text(encoding="utf-8"))
            self.assertEqual(out["verdict"], "collecting")
            self.assertTrue((root / "output" / "paper_learning" / "period_experiments_latest.md").exists())
            self.assertEqual(pe.main(["--root", str(root), "--baseline"]), 0)
            base = json.loads((root / "output" / "paper_learning" / "period_experiments_baseline.json"
                               ).read_text(encoding="utf-8"))
            self.assertEqual(base["verdict"], "baseline")


if __name__ == "__main__":
    unittest.main()
