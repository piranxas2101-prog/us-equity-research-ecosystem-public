"""The measurement period's pre-registered experiments (owner 2026-11-18, ADR-002, RULES_REVIEW section 14).

Five lessons stay in shadow for one period; ``period_experiments.json`` fixes before the period starts what each one
claims, on which candidates, with which measure and when it is judged. This module judges them, read-only:

* one yardstick for every candidate, traded or not: the excess over SPY of a fixed 5-session hold
  (``paper_holding_shadows``, class ``week``, rule ``fixed``). A trade's real fill and a simulated candidate are
  never compared with each other (the 2026-11-18 triage found that mixing them reversed the Koulis and Miltos
  lessons);
* one observation per session: each group's mean on the entry day, then the day's difference. Candidates of one day
  move with one market, so they are not independent cases;
* ``cell_vs_book``: the cell (agent, playbook, direction) against every other candidate of the same day;
  ``tag_in_cell``: inside the cell, the candidates whose category is the value against those whose category is
  known and different. An unknown or missing category is in neither group (unknown stays unknown);
* one-sided t-test on the daily differences in the lesson's direction, Holm correction over all experiments;
* before the judge date the verdict is ``collecting`` (numbers shown, nothing decided). After it: ``passed``,
  ``failed`` or ``insufficient_sample`` (fewer than ``min_sessions`` days with both groups; one extension is the
  owner's call).

Nothing here changes a score, limit, order or lesson status; a passed lesson is a proposal to the owner (Jarvis).

    python period_experiments.py                 # the period's window, as pre-registered
    python period_experiments.py --baseline      # the same test on the sessions before the period (in sample)
"""
from __future__ import annotations

import argparse
from contextlib import closing
from datetime import date, datetime, timezone
import json
import math
from pathlib import Path
import sqlite3
import statistics
from typing import Any
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent
CONFIG = ROOT / "period_experiments.json"
DB = ROOT / "data" / "paper_learning.db"
OUT_JSON = ROOT / "output" / "paper_learning" / "period_experiments_latest.json"
OUT_MD = ROOT / "output" / "paper_learning" / "period_experiments_latest.md"
NEW_YORK = ZoneInfo("America/New_York")
KINDS = {"cell_vs_book", "tag_in_cell"}
UNKNOWN = {None, "", "unknown"}


def load_config(path: Path = CONFIG) -> dict[str, Any]:
    config = json.loads(path.read_text(encoding="utf-8-sig"))
    period = config["period"]
    start, end, judge = (date.fromisoformat(period[k]) for k in ("start", "end", "judge_date"))
    if not start <= end < judge:
        raise ValueError("period: start <= end < judge_date")
    seen: set[str] = set()
    for exp in config["experiments"]:
        if exp["id"] in seen:
            raise ValueError(f"experiment {exp['id']} repeated")
        seen.add(exp["id"])
        if exp["kind"] not in KINDS:
            raise ValueError(f"experiment {exp['id']}: unknown kind {exp['kind']!r}")
        if exp["lean"] not in {"worse", "better"}:
            raise ValueError(f"experiment {exp['id']}: lean must be worse or better")
        if exp["kind"] == "tag_in_cell" and (not exp.get("tag") or exp.get("value") in UNKNOWN):
            raise ValueError(f"experiment {exp['id']}: tag_in_cell needs a tag and a known value")
    return config


def load_rows(db: Path, *, holding_class: str, rule: str) -> list[dict[str, Any]]:
    """Every ledger record with a matured shadow hold of the given class and rule (read-only)."""
    if not db.exists():
        return []
    uri = f"file:{db.as_posix()}?mode=ro"
    with closing(sqlite3.connect(uri, uri=True)) as conn:
        try:
            rows = conn.execute(
                "SELECT l.session_date, l.kind, l.record_json, h.excess_pct FROM paper_trade_ledger l"
                " JOIN paper_holding_shadows h USING(record_id) WHERE h.holding_class=? AND h.rule=?",
                (holding_class, rule)).fetchall()
        except sqlite3.OperationalError:  # tables not created yet
            return []
    out = []
    for session, kind, record_json, excess in rows:
        if excess is None:
            continue
        record = json.loads(record_json)
        out.append({"session_date": str(session), "kind": kind, "agent": record.get("agent"),
                    "playbook": record.get("playbook"), "direction": record.get("direction"),
                    "categories": record.get("categories") or {}, "excess_pct": float(excess)})
    return out


def in_cell(row: dict[str, Any], exp: dict[str, Any]) -> bool:
    return all(row.get(k) == exp[k] for k in ("agent", "playbook", "direction"))


def groups(rows: list[dict[str, Any]], exp: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """(the lesson's group, the comparison group)."""
    if exp["kind"] == "cell_vs_book":
        return [r for r in rows if in_cell(r, exp)], [r for r in rows if not in_cell(r, exp)]
    cell = [r for r in rows if in_cell(r, exp)]
    known = [r for r in cell if r["categories"].get(exp["tag"]) not in UNKNOWN]
    return ([r for r in known if r["categories"][exp["tag"]] == exp["value"]],
            [r for r in known if r["categories"][exp["tag"]] != exp["value"]])


def daily_differences(inside: list[dict[str, Any]], rest: list[dict[str, Any]]) -> list[tuple[str, float]]:
    """(session, mean excess inside - mean excess rest) for every session where both groups have candidates."""
    def by_day(items: list[dict[str, Any]]) -> dict[str, list[float]]:
        out: dict[str, list[float]] = {}
        for r in items:
            out.setdefault(r["session_date"], []).append(r["excess_pct"])
        return out
    a, b = by_day(inside), by_day(rest)
    return [(d, statistics.fmean(a[d]) - statistics.fmean(b[d])) for d in sorted(a.keys() & b.keys())]


def one_sided_p(diffs: list[float], lean: str) -> float | None:
    """One-sided t-test of the daily differences against zero, in the lesson's direction; None below 3 days or with
    no spread (the test is undefined, not passed)."""
    if len(diffs) < 3:
        return None
    sd = statistics.stdev(diffs)
    if sd == 0:
        return None
    from scipy import stats

    t = statistics.fmean(diffs) / (sd / math.sqrt(len(diffs)))
    return float(stats.t.cdf(t, len(diffs) - 1) if lean == "worse" else stats.t.sf(t, len(diffs) - 1))


def holm(pvalues: dict[str, float | None]) -> dict[str, float | None]:
    """Holm step-down adjusted p-values; an undefined p stays undefined and does not count in m."""
    known = sorted(((p, k) for k, p in pvalues.items() if p is not None))
    m, out, running = len(known), {k: None for k in pvalues}, 0.0
    for i, (p, k) in enumerate(known):
        running = max(running, min(1.0, (m - i) * p))
        out[k] = round(running, 6)
    return out


def judge(config: dict[str, Any], rows: list[dict[str, Any]], *, today: date, start: date, end: date,
          baseline: bool = False) -> dict[str, Any]:
    window = [r for r in rows if start.isoformat() <= r["session_date"] <= end.isoformat()]
    judge_date = date.fromisoformat(config["period"]["judge_date"])
    final = not baseline and today >= judge_date
    results, raw = [], {}
    for exp in config["experiments"]:
        inside, rest = groups(window, exp)
        diffs = daily_differences(inside, rest)
        values = [d for _, d in diffs]
        raw[exp["id"]] = one_sided_p(values, exp["lean"])
        results.append({"id": exp["id"], "title": exp["title"], "lesson_ids": exp["lesson_ids"], "lean": exp["lean"],
                        "inside_candidates": len(inside), "rest_candidates": len(rest), "sessions": len(diffs),
                        "mean_daily_difference_pct": round(statistics.fmean(values), 3) if values else None,
                        "days_in_lesson_direction": sum(1 for d in values if (d < 0) == (exp["lean"] == "worse")),
                        "p_one_sided": None if raw[exp["id"]] is None else round(raw[exp["id"]], 6)})
    adjusted = holm(raw)
    for item in results:
        item["p_holm"] = adjusted[item["id"]]
        direction_ok = item["mean_daily_difference_pct"] is not None and \
            (item["mean_daily_difference_pct"] < 0) == (item["lean"] == "worse")
        if baseline:
            item["verdict"] = "baseline"
        elif not final:
            item["verdict"] = "collecting"
        elif item["sessions"] < int(config["min_sessions"]):
            item["verdict"] = "insufficient_sample"
        elif direction_ok and item["p_holm"] is not None and item["p_holm"] < float(config["alpha"]):
            item["verdict"] = "passed"
        else:
            item["verdict"] = "failed"
    return {"schema": "ecosystem.period_experiments_result.v1", "generated_at": datetime.now(timezone.utc).isoformat(),
            "period": config["period"], "window": {"start": start.isoformat(), "end": end.isoformat()},
            "baseline": baseline, "yardstick": config["yardstick"], "min_sessions": config["min_sessions"],
            "alpha": config["alpha"], "verdict": "judged" if final else ("baseline" if baseline else "collecting"),
            "experiments": results}


VERDICT_GR = {"baseline": "—", "collecting": "μαζεύει δείγμα", "passed": "πέρασε", "failed": "δεν πέρασε",
              "insufficient_sample": "λίγο δείγμα (μία παράταση: απόφαση του ιδιοκτήτη)"}


def markdown(result: dict[str, Any]) -> str:
    p, w = result["period"], result["window"]
    head = ("Δείγμα πριν την περίοδο (in sample, δεν κρίνει τίποτα)" if result["baseline"]
            else f"Περίοδος {p['id']}: {p['start']} έως {p['end']}, κρίση {p['judge_date']}")
    lines = ["# Πειράματα της περιόδου μέτρησης", "", head, "",
             f"Παράθυρο: {w['start']} έως {w['end']}. Μέτρο: {result['yardstick']['description']}. "
             f"Μία παρατήρηση ανά συνεδρία, μονόπλευρο t-test, διόρθωση Holm, α = {result['alpha']}, "
             f"τουλάχιστον {result['min_sessions']} συνεδρίες.", "",
             "| Πείραμα | Συνεδρίες | Μέση διαφορά/μέρα | Μέρες προς το δίδαγμα | p | p Holm | Ετυμηγορία |",
             "|---|---:|---:|---:|---:|---:|---|"]
    for e in result["experiments"]:
        lines.append(f"| {e['id']} {e['title']} | {e['sessions']} | {e['mean_daily_difference_pct']} | "
                     f"{e['days_in_lesson_direction']} | {e['p_one_sided']} | {e['p_holm']} | "
                     f"{VERDICT_GR[e['verdict']]} |")
    lines += ["", "Τίποτα από αυτά δεν αλλάζει σκορ, όρια ή εντολές· ό,τι περάσει γίνεται πρόταση προς τον ιδιοκτήτη."]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Judge the measurement period's pre-registered experiments.")
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--baseline", action="store_true", help="the same test on the sessions before the period")
    parser.add_argument("--today", default=None, help="YYYY-MM-DD (default: today in New York)")
    parser.add_argument("--db", type=Path, default=None, help="paper ledger (default: <root>/data/paper_learning.db)")
    args = parser.parse_args(argv)
    root = args.root.resolve()
    config = load_config(root / CONFIG.name)
    today = date.fromisoformat(args.today) if args.today else datetime.now(NEW_YORK).date()
    rows = load_rows(args.db or root / "data" / DB.name, holding_class=config["yardstick"]["holding_class"],
                     rule=config["yardstick"]["rule"])
    start, end = date.fromisoformat(config["period"]["start"]), date.fromisoformat(config["period"]["end"])
    if args.baseline:
        start, end = date.min, date.fromordinal(start.toordinal() - 1)
    result = judge(config, rows, today=today, start=start, end=end, baseline=args.baseline)
    out_json = root / OUT_JSON.relative_to(ROOT)
    out_md = root / OUT_MD.relative_to(ROOT)
    if args.baseline:
        out_json = out_json.with_name("period_experiments_baseline.json")
        out_md = out_md.with_name("period_experiments_baseline.md")
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    out_md.write_text(markdown(result), encoding="utf-8")
    print(f"PERIOD {result['verdict']} " + " ".join(f"{e['id']}={e['verdict']}" for e in result["experiments"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
