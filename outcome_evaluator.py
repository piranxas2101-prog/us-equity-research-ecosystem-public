"""Do Giannis's tiers and Petros's verdicts separate what the market did afterwards? A read-only check.

The fundamental evaluator grades completeness only, so every episode passes and nothing is learned from outcomes.
This module joins each learning episode to its matured market outcome (``agent_learning_outcomes``), subtracts what
the benchmark (SPY) did over the same sessions, and asks per agent and horizon: does a better label go with a better
excess return? A label is judged only with at least ``MIN_SAMPLE`` outcomes from at least ``MIN_DATES`` different
baseline dates, because the same companies repeat every night and a few market days are not a sample.

It never changes a decision, score, threshold or model. An ``inverted`` ordering with enough sample is written as a
lesson *candidate* (manual promotion, shadow validation first); everything else is a report for the owner.
Unknown stays unknown: without the benchmark, excess return is unknown and nothing is judged.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from contextlib import closing
from datetime import date, datetime, timedelta, timezone
import json
from pathlib import Path
import statistics
from typing import Any, Callable

import agent_learning_control_plane as learning
import market_intel_db
from outcome_tracker import SESSION_BENCHMARK, completed_through

SCHEMA = "ecosystem.outcome_evaluation.v1"
RUBRIC_VERSION = "outcome-ordering.v1"
EVALUATOR_ID = "outcome_ordering_evaluator"
MIN_SAMPLE = 30
MIN_DATES = 20
# Best label first. Labels outside the order (Petros manual_review = evidence missing, Giannis Complex Security
# Risk = instrument type) are reported but not part of the ordering test.
LABEL_ORDER = {
    "giannis": ("giannis_tier", ("Core Watchlist", "Selective Watchlist", "Speculative Monitor", "Avoid For Now")),
    "petros": ("petros_verdict", ("candidate", "watch", "reject")),
    # quality factors recorded in shadow next to Giannis's score (quality_factors.py): judged the same way
    "giannis_piotroski": ("piotroski_f_score", ("strong (7-9)", "middle (4-6)", "weak (0-3)")),
    "giannis_altman": ("altman_z2_zone", ("safe", "grey", "distress")),
    "giannis_beneish": ("beneish_m_score", ("no warning", "manipulation warning")),
}
TASKS = {"giannis": "fundamental_quality", "petros": "fundamental_synthesis",
         "giannis_piotroski": "fundamental_quality", "giannis_altman": "fundamental_quality",
         "giannis_beneish": "fundamental_quality"}
SOURCE_AGENTS = ("giannis", "petros")


def factor_labels(decision: dict[str, Any]) -> dict[str, str | None]:
    """Bucket labels of the shadow quality factors in a Giannis decision (None when the factor is unknown)."""
    factors = decision.get("quality_factors") or {}
    f_score = (factors.get("piotroski") or {}).get("score")
    z = factors.get("altman_z2") or {}
    m = factors.get("beneish") or {}
    return {
        "giannis_piotroski": None if f_score is None else "strong (7-9)" if f_score >= 7 else "middle (4-6)" if f_score >= 4 else "weak (0-3)",
        "giannis_altman": z.get("zone"),
        "giannis_beneish": None if m.get("flag") is None else "manipulation warning" if m["flag"] else "no warning",
    }


def load_outcomes(conn: Any, agents: tuple[str, ...] = SOURCE_AGENTS) -> list[dict[str, Any]]:
    marks = ",".join("?" for _ in agents)
    rows = conn.execute(
        "SELECT e.agent_id,e.subject_id,e.run_id,e.observed_at,e.decision_json,o.horizon_days,o.label,o.evidence_json "
        "FROM agent_learning_outcomes o JOIN agent_learning_episodes e ON e.episode_id=o.episode_id "
        f"WHERE o.objective_id='market_return_pct' AND e.agent_id IN ({marks})", agents).fetchall()
    out = []
    for row in rows:
        field = LABEL_ORDER[str(row["agent_id"])][0]
        decision = json.loads(row["decision_json"] or "{}")
        evidence = json.loads(row["evidence_json"] or "{}")
        base = {"subject_id": row["subject_id"], "run_id": row["run_id"], "observed_at": row["observed_at"],
                "horizon_days": int(row["horizon_days"]), "return_pct": float(row["label"]),
                "target_session_date": evidence.get("target_session_date")}
        out.append({**base, "agent_id": str(row["agent_id"]), "label": decision.get(field)})
        if row["agent_id"] == "giannis":
            for group, label in factor_labels(decision).items():
                if label is not None:
                    out.append({**base, "agent_id": group, "label": label})
    return out


def stored_market_states() -> Any:
    """Market state per session from the SPY history in the price store (None when it is missing)."""
    import macro_environment
    import price_history_store

    states = macro_environment.market_state_history(price_history_store.load("SPY"))
    return states if len(states) else None


def benchmark_closes(start: date, end: date, benchmark: str = SESSION_BENCHMARK) -> dict[date, float] | None:
    import yfinance as yf

    try:
        history = yf.Ticker(benchmark).history(start=start.isoformat(), end=(end + timedelta(days=1)).isoformat(),
                                               interval="1d", auto_adjust=False, actions=False)
    except Exception:
        return None
    if history.empty or "Close" not in history.columns:
        return None
    return {index.date(): float(close) for index, close in history["Close"].dropna().items()}


def _baseline_session(observed_at: str, closes: dict[date, float]) -> date | None:
    """The benchmark session a setup's baseline price belongs to: the last final session at the observation time."""
    try:
        moment = datetime.fromisoformat(str(observed_at).replace("Z", "+00:00"))
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    last = completed_through(moment)
    earlier = [day for day in closes if day <= last]
    return max(earlier) if earlier else None


def add_excess(rows: list[dict[str, Any]], closes: dict[date, float] | None) -> None:
    for row in rows:
        row["excess_return_pct"] = None
        row["baseline_date"] = None
        if not closes or not row.get("target_session_date"):
            continue
        base = _baseline_session(row["observed_at"], closes)
        target = date.fromisoformat(row["target_session_date"])
        if base is None or target not in closes or target <= base:
            continue
        bench = (closes[target] / closes[base] - 1.0) * 100.0
        row["baseline_date"] = base.isoformat()
        row["excess_return_pct"] = row["return_pct"] - bench


def _cell(values: list[dict[str, Any]]) -> dict[str, Any]:
    excess = [row["excess_return_pct"] for row in values]
    return {
        "sample_count": len(values),
        "distinct_dates": len({row["baseline_date"] for row in values}),
        "median_excess_pct": round(statistics.median(excess), 3),
        "mean_excess_pct": round(statistics.fmean(excess), 3),
        "beat_benchmark_pct": round(100.0 * sum(1 for x in excess if x > 0) / len(excess), 1),
    }


def _ordering(cells: list[dict[str, Any] | None]) -> str:
    """Strictly monotonic medians and a beat-the-benchmark rate that never goes the other way."""
    judged = [c for c in cells if c and c["sample_count"] >= MIN_SAMPLE and c["distinct_dates"] >= MIN_DATES]
    if len(judged) < 2:
        return "insufficient_sample"
    medians = [c["median_excess_pct"] for c in judged]
    beats = [c["beat_benchmark_pct"] for c in judged]
    if all(a > b for a, b in zip(medians, medians[1:])) and all(a >= b for a, b in zip(beats, beats[1:])):
        return "ordered"
    if all(a < b for a, b in zip(medians, medians[1:])) and all(a <= b for a, b in zip(beats, beats[1:])):
        return "inverted"
    return "mixed"


def attach_market_states(rows: list[dict[str, Any]], states: Any) -> None:
    """The market state (macro_environment.market_state_history) of each outcome's baseline session, or None."""
    lookup = {} if states is None else {index.date(): value for index, value in states.dropna().items()}
    for row in rows:
        base = row.get("baseline_date")
        row["market_state"] = lookup.get(date.fromisoformat(base)) if base else None


def evaluate_by_market_state(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The same label test inside each market state: does a label hold up in rising and in falling markets?"""
    groups: dict[tuple[str, int, str], dict[str, list[dict[str, Any]]]] = defaultdict(lambda: defaultdict(list))
    for row in rows:
        if row.get("excess_return_pct") is not None and row.get("market_state"):
            groups[(row["agent_id"], row["horizon_days"], row["market_state"])][str(row["label"])].append(row)
    results = []
    for (agent, horizon, state), by_label in sorted(groups.items()):
        order = LABEL_ORDER[agent][1]
        cells = {label: _cell(values) for label, values in sorted(by_label.items())}
        results.append({"agent_id": agent, "horizon_days": horizon, "market_state": state, "cells": cells,
                        "ordering": _ordering([cells.get(label) for label in order])})
    return results


def evaluate(rows: list[dict[str, Any]], benchmark_available: bool) -> dict[str, Any]:
    usable = [row for row in rows if row.get("excess_return_pct") is not None]
    groups: dict[tuple[str, int], dict[str, list[dict[str, Any]]]] = defaultdict(lambda: defaultdict(list))
    for row in usable:
        groups[(row["agent_id"], row["horizon_days"])][str(row["label"])].append(row)
    results = []
    for (agent, horizon), by_label in sorted(groups.items()):
        order = LABEL_ORDER[agent][1]
        cells = {label: _cell(values) for label, values in sorted(by_label.items())}
        results.append({
            "agent_id": agent, "horizon_days": horizon, "label_field": LABEL_ORDER[agent][0],
            "label_order": list(order), "cells": cells,
            "ordering": _ordering([cells.get(label) for label in order]),
        })
    return {
        "schema": SCHEMA, "generated_at": datetime.now(timezone.utc).isoformat(),
        "benchmark": SESSION_BENCHMARK, "benchmark_available": benchmark_available,
        "minimum_sample": MIN_SAMPLE, "minimum_distinct_dates": MIN_DATES,
        "outcomes_total": len(rows), "outcomes_with_excess": len(usable),
        "outcomes_without_excess": len(rows) - len(usable),
        "active_decisions_changed": False, "model_states_changed": False,
        "results": results,
        "by_market_state": evaluate_by_market_state(rows),
    }


def record_lessons(conn: Any, payload: dict[str, Any]) -> list[dict[str, Any]]:
    lessons = []
    for result in payload["results"]:
        if result["ordering"] != "inverted":
            continue
        agent, horizon = result["agent_id"], result["horizon_days"]
        proposal = {
            "schema": "ecosystem.agent_lesson_candidate.v1", "target_agent": agent,
            "criterion_id": f"{result['label_field']}_outcome_order_h{horizon}",
            "proposed_change": (f"Better {result['label_field']} labels went with worse excess returns at {horizon} "
                                f"sessions; review the scoring inputs behind the labels."),
            "activation": "candidate_only", "changes_active_scores": False,
            "requires_shadow_validation": True, "requires_manual_promotion": True,
        }
        support = sum(cell["sample_count"] for cell in result["cells"].values())
        lesson_id, created = learning.upsert_lesson_candidate(
            conn, target_agent=agent.split("_")[0], task_id=TASKS[agent], rubric_version=RUBRIC_VERSION,
            criterion_id=proposal["criterion_id"], support_count=support, proposal=proposal,
            evidence={"cells": result["cells"], "generated_at": payload["generated_at"]})
        lessons.append({"lesson_id": lesson_id, "created": created, "agent_id": agent, "horizon_days": horizon})
    return lessons


def render_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# Αποτελέσματα αγοράς ανά ετικέτα (Giannis, Petros)", "",
        f"Υπέρβαση απόδοσης έναντι {payload['benchmark']} στις ίδιες συνεδριάσεις. Κρίση μόνο με ≥{payload['minimum_sample']} "
        f"αποτελέσματα από ≥{payload['minimum_distinct_dates']} διαφορετικές ημερομηνίες· αλλιώς «ανεπαρκές δείγμα».",
        f"Αποτελέσματα: {payload['outcomes_total']} · με benchmark: {payload['outcomes_with_excess']}"
        + ("" if payload["benchmark_available"] else " · **benchmark μη διαθέσιμο: τίποτα δεν κρίθηκε**"), "",
    ]
    for result in payload["results"]:
        lines += [f"## {result['agent_id']} · {result['horizon_days']} συνεδριάσεις · {result['ordering']}", "",
                  "| ετικέτα | n | ημερομηνίες | διάμεση υπέρβαση % | μέση υπέρβαση % | νίκησε το SPY % |", "|---|---|---|---|---|---|"]
        labels = result["label_order"] + sorted(set(result["cells"]) - set(result["label_order"]))
        for label in labels:
            cell = result["cells"].get(label)
            if cell:
                lines.append(f"| {label} | {cell['sample_count']} | {cell['distinct_dates']} | {cell['median_excess_pct']} | "
                             f"{cell['mean_excess_pct']} | {cell['beat_benchmark_pct']} |")
        lines.append("")
    if payload.get("by_market_state"):
        lines += ["## Ανά κατάσταση αγοράς (S&P πάνω/κάτω από τον μέσο 200 ημερών, ήρεμη/ταραγμένη)", "",
                  "| agent | συνεδριάσεις | κατάσταση | αποτελέσματα | σειρά ετικετών |", "|---|---|---|---|---|"]
        for result in payload["by_market_state"]:
            total = sum(cell["sample_count"] for cell in result["cells"].values())
            lines.append(f"| {result['agent_id']} | {result['horizon_days']} | {result['market_state']} | {total} | "
                         f"{result['ordering']} |")
        lines.append("")
    if payload.get("lesson_candidates"):
        lines.append(f"Υποψήφια μαθήματα (χειροκίνητη έγκριση): {len(payload['lesson_candidates'])}")
    return "\n".join(lines) + "\n"


def run(db_path: Path, out_prefix: Path, *, record: bool = True,
        closes_fn: Callable[[date, date], dict[date, float] | None] = benchmark_closes,
        states_fn: Callable[[], Any] = lambda: stored_market_states()) -> dict[str, Any]:
    with closing(market_intel_db.connect(db_path)) as conn:
        learning.init_schema(conn)
        rows = load_outcomes(conn)
        targets = [date.fromisoformat(r["target_session_date"]) for r in rows if r.get("target_session_date")]
        closes = closes_fn(min(targets) - timedelta(days=40), max(targets)) if targets else None
        add_excess(rows, closes)
        attach_market_states(rows, states_fn())
        payload = evaluate(rows, benchmark_available=closes is not None)
        payload["lesson_candidates"] = record_lessons(conn, payload) if record else []
    out_prefix.parent.mkdir(parents=True, exist_ok=True)
    Path(f"{out_prefix}_latest.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    Path(f"{out_prefix}_latest.md").write_text(render_markdown(payload), encoding="utf-8")
    return payload


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--db", default="data/market_intel.db")
    parser.add_argument("--out-prefix", default="output/outcome_evaluation")
    parser.add_argument("--no-lessons", action="store_true", help="report only, write no lesson candidates")
    args = parser.parse_args(argv)
    payload = run(Path(args.db), Path(args.out_prefix), record=not args.no_lessons)
    verdicts = {f"{r['agent_id']}_{r['horizon_days']}d": r["ordering"] for r in payload["results"]}
    print(f"[OUTCOMES] evaluated={payload['outcomes_with_excess']}/{payload['outcomes_total']} {verdicts}")
    print(f"[OUTPUT] {args.out_prefix}_latest.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
