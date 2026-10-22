"""Do the traders' confidence labels carry information? A read-only check of realised outcomes.

Yankos and Periklis attach a confidence label (High / Medium / Low) to every decision, and the locked
predictions in ``trader_experiments`` have no probability. This module joins each decision to its realised price
outcome (the same join the strategy scorecards use), counts every market decision once per agent, and asks one
question per agent and horizon: does a higher label go with a better directional result?

It only reads. It never changes a signal, a threshold or a policy, and a verdict of ``ordered`` is evidence for the
owner's review, not a promotion. Samples below ``MIN_SAMPLE`` per label are reported as insufficient, not judged.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any

import strategy_scorecards as sc

SCHEMA = "ecosystem.trader_calibration.v1"
LABELS = ("High", "Medium", "Low")
MIN_SAMPLE = sc.MIN_EDGE_SAMPLE
DEFAULT_VERDICTS = ("candidate", "watch", "manual_review")
DEFAULT_HORIZONS = (1, 3, 5, 10, 20)


def _unique_per_agent(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    kept: dict[tuple[str, tuple[str, str, str, int]], dict[str, Any]] = {}
    for row in rows:
        kept.setdefault((str(row.get("agent_name") or ""), sc.decision_key(row)), row)
    return list(kept.values())


def _tier(row: dict[str, Any]) -> str:
    """The risk tier of a decision; older rows carried it under ``confidence``."""
    tier = row.get("risk_tier") or row.get("confidence")
    return str(tier) if tier in LABELS else ""


def _ordering(cells: dict[str, dict[str, Any] | None]) -> str:
    """ordered / inverted / mixed across the labels with enough decisions, in the order High > Medium > Low.

    Only labels with at least MIN_SAMPLE decisions are compared (the traders hand few Low decisions to the
    outcome tables), and at least two are needed. Otherwise insufficient_sample.
    """
    judged = [cells[label] for label in LABELS if cells.get(label) and cells[label]["sample_count"] >= MIN_SAMPLE]
    if len(judged) < 2:
        return "insufficient_sample"
    wins = [cell["win_rate_pct"] for cell in judged]
    returns = [cell["avg_directional_return_pct"] for cell in judged]
    if all(a > b for a, b in zip(wins, wins[1:])) and all(a > b for a, b in zip(returns, returns[1:])):
        return "ordered"
    if all(a < b for a, b in zip(wins, wins[1:])) and all(a < b for a, b in zip(returns, returns[1:])):
        return "inverted"
    return "mixed"


def benchmark_adjust(rows: list[dict[str, Any]], closes: dict[Any, float] | None) -> list[dict[str, Any]]:
    """Directional return minus what the benchmark did in the same direction over the same sessions.

    Raw directional returns mostly measure the market: in a falling week every long loses and every short wins, so a
    label that holds more shorts looks "better". Rows whose excess cannot be computed are left out (unknown, not raw).
    """
    from datetime import date

    from outcome_evaluator import _baseline_session

    adjusted = []
    for row in rows:
        raw, direction = row.get("raw_symbol_return_pct"), row.get("trade_direction")
        if not closes or raw is None or direction not in ("long", "short") or not row.get("target_session_date"):
            continue
        base = _baseline_session(str(row.get("source_run_timestamp") or ""), closes)
        target = date.fromisoformat(str(row["target_session_date"]))
        if base is None or target not in closes or target <= base:
            continue
        excess = raw - (closes[target] / closes[base] - 1.0) * 100.0
        adjusted.append({**row, "raw_directional_return_pct": row.get("directional_return_pct"),
                         "directional_return_pct": sc.directional_return(excess, direction),
                         "baseline_date": base.isoformat()})
    return adjusted


def by_market_state(decisions: list[dict[str, Any]], states: Any) -> list[dict[str, Any]]:
    """Per agent, horizon and market state of the decision's baseline session: the tiers' results (no verdict)."""
    from datetime import date

    if states is None:
        return []
    lookup = {index.date(): value for index, value in states.dropna().items()}
    groups: dict[tuple[str, int, str], list[dict[str, Any]]] = {}
    for row in decisions:
        base = row.get("baseline_date")
        state = lookup.get(date.fromisoformat(base)) if base else None
        if state:
            groups.setdefault((str(row.get("agent_name") or ""), int(row["horizon_days"]), state), []).append(row)
    return [{"agent": agent, "horizon_days": horizon, "market_state": state, "decisions": len(bucket),
             "by_confidence": {label: sc.metric_row([r for r in bucket if _tier(r) == label]) for label in LABELS}}
            for (agent, horizon, state), bucket in sorted(groups.items())]


def calibrate(rows: list[dict[str, Any]], benchmark_adjusted: bool = False, states: Any = None) -> dict[str, Any]:
    decisions = _unique_per_agent([row for row in rows if row.get("directional_return_pct") is not None])
    agents = sorted({str(row.get("agent_name") or "") for row in decisions})
    horizons = sorted({int(row["horizon_days"]) for row in decisions})
    with_probability = sum(1 for row in decisions if sc.to_float(row.get("confidence_probability")) is not None)
    results: list[dict[str, Any]] = []
    for agent in agents:
        for horizon in horizons:
            bucket = [row for row in decisions if row.get("agent_name") == agent and int(row["horizon_days"]) == horizon]
            if not bucket:
                continue
            cells: dict[str, dict[str, Any] | None] = {}
            for label in LABELS:
                cells[label] = sc.metric_row([row for row in bucket if _tier(row) == label])
            results.append({
                "agent": agent, "horizon_days": horizon, "decisions": len(bucket),
                "ordering": _ordering(cells),
                "by_confidence": {label: cells[label] for label in LABELS},
            })
    return {
        "schema": SCHEMA,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "min_sample_per_label": MIN_SAMPLE,
        "unique_decisions": len(decisions),
        "benchmark_adjusted": benchmark_adjusted,
        "decisions_with_probability": with_probability,
        "probability_note": ("no confidence_probability is recorded, so only the labels can be checked"
                             if not with_probability else "probabilities are present but not yet evaluated here"),
        "results": results,
        "by_market_state": by_market_state(decisions, states),
    }


def render_markdown(payload: dict[str, Any]) -> str:
    lines = ["# Trader calibration by risk tier (read-only)", "",
             "- The High/Medium/Low label is the company risk tier (it was called confidence until 2026-10-06); "
             "the playbook track record that will become confidence is in output/playbook_track_record_latest.json.",
             f"- Unique decisions with an outcome: {payload['unique_decisions']}",
             "- Returns are " + ("directional excess over SPY (same sessions)" if payload.get("benchmark_adjusted")
                                 else "raw directional returns (benchmark unavailable): read with care"),
             f"- {payload['probability_note']}",
             f"- A label needs at least {payload['min_sample_per_label']} decisions to be judged.", ""]
    for result in payload["results"]:
        lines.append(f"## {result['agent']} · {result['horizon_days']}d · {result['ordering']} "
                     f"({result['decisions']} decisions)")
        lines.append("")
        for label in LABELS:
            cell = result["by_confidence"][label]
            if cell is None:
                lines.append(f"- {label}: no decisions")
            else:
                lines.append(f"- {label}: n={cell['sample_count']} · win {cell['win_rate_pct']}% "
                             f"(Wilson low {cell['wilson_win_rate_lower_pct']}%) · "
                             f"avg {cell['avg_directional_return_pct']}%")
        lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--db", default="data/market_intel.db")
    parser.add_argument("--out-prefix", default="output/trader_calibration")
    parser.add_argument("--horizons", type=int, nargs="+", default=list(DEFAULT_HORIZONS))
    parser.add_argument("--verdicts", nargs="+", default=list(DEFAULT_VERDICTS))
    parser.add_argument("--raw", action="store_true", help="raw directional returns, no SPY adjustment")
    parser.add_argument("--track-record", default="output/playbook_track_record_latest.json")
    args = parser.parse_args(argv)
    rows = sc.load_trade_rows(Path(args.db), sorted(set(args.horizons)), [v.lower() for v in args.verdicts])
    closes = None
    if not args.raw:
        from datetime import date, timedelta

        from outcome_evaluator import benchmark_closes
        targets = [date.fromisoformat(str(r["target_session_date"])) for r in rows if r.get("target_session_date")]
        closes = benchmark_closes(min(targets) - timedelta(days=40), max(targets)) if targets else None
    adjusted = benchmark_adjust(rows, closes) if closes else []
    from outcome_evaluator import stored_market_states
    payload = (calibrate(adjusted, benchmark_adjusted=True, states=stored_market_states()) if closes
               else calibrate(rows))  # market states need the SPY-adjusted baseline session
    if closes:  # the shadow confidence of the next run's traders; never written from raw returns
        import trader_confidence
        record = trader_confidence.build_track_record(adjusted)
        Path(args.track_record).parent.mkdir(parents=True, exist_ok=True)
        Path(args.track_record).write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    prefix = Path(args.out_prefix)
    prefix.parent.mkdir(parents=True, exist_ok=True)
    Path(f"{prefix}_latest.json").write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    Path(f"{prefix}_latest.md").write_text(render_markdown(payload), encoding="utf-8")
    print(f"[OUTPUT] Calibration: {prefix}_latest.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
