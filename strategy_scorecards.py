from __future__ import annotations

import csv
import json
import math
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from statistics import median, pstdev
from typing import Any

import market_intel_db
from technical_research_lab import ExecutionCosts

# One entry and one exit, each paying commission and slippage. Taken from the
# technical research lab's defaults so both reports use the same cost model.
# Short borrow fees are not modeled.
_COSTS = ExecutionCosts()
ROUND_TRIP_COST_PCT = 2 * (_COSTS.commission_bps + _COSTS.slippage_bps) / 100.0

# Minimum unique decisions before a scorecard row can be judged at all.
MIN_EDGE_SAMPLE = 30


def to_float(value: Any) -> float | None:
    return market_intel_db.to_float(value)


def safe_payload(raw: Any) -> dict[str, Any]:
    try:
        value = json.loads(str(raw or "{}"))
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}


def directional_return(raw_return: float | None, direction: str) -> float | None:
    if raw_return is None:
        return None
    return raw_return if direction == "long" else (-raw_return if direction == "short" else None)


def load_trade_rows(db_path: Path, horizons: list[int], verdicts: list[str]) -> list[dict[str, Any]]:
    hs = ",".join("?" for _ in horizons)
    vs = ",".join("?" for _ in verdicts)
    query = f"""
        SELECT ss.agent_name, ss.playbook_name, ss.signal_verdict, ss.signal_score,
               ss.confidence, ss.source_run_timestamp, ss.ticker, ss.payload_json AS signal_payload,
               ss.trade_direction, ss.sector_id AS signal_sector_id,
               ss.industry_id AS signal_industry_id, ss.catalyst_category_id AS signal_catalyst_id,
               cs.company, cs.baseline_price, cs.giannis_score, cs.giannis_tier, cs.risk_level,
               cs.sector, cs.industry, cs.sector_id, cs.industry_id, cs.classification_status,
               cs.catalyst_category_id, cs.payload_json AS setup_payload,
               po.horizon_days, po.price, po.return_pct, po.target_session_date
        FROM strategy_signals ss
        INNER JOIN candidate_setups cs
          ON cs.source_run_timestamp = ss.source_run_timestamp AND cs.ticker = ss.ticker
        INNER JOIN price_outcomes po ON po.setup_id = cs.id
        WHERE ss.signal_verdict IN ({vs}) AND po.horizon_days IN ({hs})
    """
    with closing(market_intel_db.connect(db_path)) as conn:
        market_intel_db.init_db(conn)
        rows = conn.execute(query, (*verdicts, *horizons)).fetchall()
    result: list[dict[str, Any]] = []
    for row in rows:
        signal = safe_payload(row["signal_payload"])
        setup = safe_payload(row["setup_payload"])
        direction = str(row["trade_direction"] or signal.get("trade_direction") or "flat").lower()
        raw_return = to_float(row["return_pct"])
        result.append({
            "agent_name": row["agent_name"], "playbook_name": row["playbook_name"],
            "signal_verdict": row["signal_verdict"], "signal_score": to_float(row["signal_score"]),
            "confidence": row["confidence"], "confidence_probability": to_float(signal.get("confidence_probability")),
            # older signals carried the risk tier under "confidence"; newer ones name it risk_tier
            "risk_tier": signal.get("risk_tier") or (row["confidence"] if row["confidence"] in ("High", "Medium", "Low") else None),
            "source_run_timestamp": row["source_run_timestamp"], "ticker": row["ticker"],
            "company": row["company"], "baseline_price": to_float(row["baseline_price"]),
            "giannis_score": to_float(row["giannis_score"]), "giannis_tier": row["giannis_tier"],
            "risk_level": row["risk_level"], "trade_direction": direction,
            "sector": row["sector"] or setup.get("sector") or "Unknown",
            "industry": row["industry"] or setup.get("industry") or "Unknown",
            "sector_id": row["signal_sector_id"] or row["sector_id"] or setup.get("sector_id") or "unknown",
            "industry_id": row["signal_industry_id"] or row["industry_id"] or setup.get("industry_id") or "unknown.unknown",
            "classification_status": row["classification_status"] or setup.get("classification_status") or "unknown",
            "catalyst_category_id": row["signal_catalyst_id"] or row["catalyst_category_id"] or setup.get("catalyst_category_id") or "unknown",
            "horizon_days": int(row["horizon_days"]), "price": to_float(row["price"]),
            "target_session_date": row["target_session_date"],
            "raw_symbol_return_pct": raw_return,
            "directional_return_pct": directional_return(raw_return, direction),
        })
    return result


def load_shortlist_baselines(db_path: Path, horizons: list[int]) -> dict[tuple[str, int], float]:
    """Average raw return of every setup the research team handed over in a run.

    This is the "do nothing clever" baseline: holding the whole shortlist of that
    day for the same horizon. A trader signal adds value only if it beats it.
    """
    hs = ",".join("?" for _ in horizons)
    query = f"""
        SELECT cs.source_run_timestamp, po.horizon_days, AVG(po.return_pct) AS mean_return
        FROM candidate_setups cs
        INNER JOIN price_outcomes po ON po.setup_id = cs.id
        WHERE po.horizon_days IN ({hs}) AND po.return_pct IS NOT NULL
        GROUP BY cs.source_run_timestamp, po.horizon_days
    """
    with closing(market_intel_db.connect(db_path)) as conn:
        market_intel_db.init_db(conn)
        rows = conn.execute(query, tuple(horizons)).fetchall()
    return {
        (str(row["source_run_timestamp"]), int(row["horizon_days"])): float(row["mean_return"])
        for row in rows
        if row["mean_return"] is not None
    }


def attach_baselines(rows: list[dict[str, Any]], baselines: dict[tuple[str, int], float]) -> None:
    """Add net-of-cost and baseline-relative returns to each outcome row in place."""
    for row in rows:
        directional = to_float(row.get("directional_return_pct"))
        raw = to_float(row.get("raw_symbol_return_pct"))
        base = baselines.get((str(row.get("source_run_timestamp") or ""), int(row.get("horizon_days") or 0)))
        row["net_return_pct"] = None if directional is None else directional - ROUND_TRIP_COST_PCT
        row["shortlist_baseline_return_pct"] = base
        row["excess_return_pct"] = (
            None if raw is None or base is None
            else directional_return(raw - base, str(row.get("trade_direction") or ""))
        )


def edge_status(metrics: dict[str, Any]) -> str:
    """Plain verdict on whether a group of decisions shows an edge worth pursuing.

    It needs enough decisions, a win rate whose 95% lower bound is above a coin
    flip, a positive average after costs, and a positive average against the
    shortlist baseline. Anything less is reported as no edge yet.
    """
    if metrics["sample_count"] < MIN_EDGE_SAMPLE:
        return "insufficient_sample"
    net = metrics.get("net_expectancy_pct")
    excess = metrics.get("avg_excess_return_pct")
    if metrics["wilson_win_rate_lower_pct"] <= 50.0 or net is None or net <= 0 or excess is None or excess <= 0:
        return "no_edge"
    return "candidate_edge"


def evidence_status(count: int) -> str:
    if count < 10:
        return "exploratory"
    if count < 30:
        return "developing"
    if count < 100:
        return "informative"
    return "established"


def wilson_lower_bound(wins: int, total: int, z: float = 1.96) -> float | None:
    if total <= 0:
        return None
    p = wins / total
    denominator = 1 + z * z / total
    centre = p + z * z / (2 * total)
    margin = z * math.sqrt((p * (1 - p) + z * z / (4 * total)) / total)
    return ((centre - margin) / denominator) * 100.0


def metric_row(bucket: list[dict[str, Any]]) -> dict[str, Any] | None:
    values = [value for value in (to_float(row.get("directional_return_pct")) for row in bucket) if value is not None]
    if not values:
        return None
    gains = [value for value in values if value > 0]
    losses = [value for value in values if value < 0]
    count = len(values)
    avg_gain = sum(gains) / len(gains) if gains else None
    avg_loss = sum(losses) / len(losses) if losses else None
    payoff = avg_gain / abs(avg_loss) if avg_gain is not None and avg_loss not in (None, 0) else None
    profit_factor = sum(gains) / abs(sum(losses)) if gains and losses and sum(losses) else None
    net_values = [value for value in (to_float(row.get("net_return_pct")) for row in bucket) if value is not None]
    excess_values = [value for value in (to_float(row.get("excess_return_pct")) for row in bucket) if value is not None]
    metrics = {
        "sample_count": count,
        "distinct_symbols": len({str(row.get("ticker")) for row in bucket}),
        "distinct_run_dates": len({str(row.get("source_run_timestamp") or "")[:10] for row in bucket}),
        "wins": len(gains), "losses": len(losses), "breakeven": count - len(gains) - len(losses),
        "win_rate_pct": round(len(gains) / count * 100.0, 2),
        "wilson_win_rate_lower_pct": round(wilson_lower_bound(len(gains), count) or 0.0, 2),
        "expectancy_pct": round(sum(values) / count, 4),
        "avg_directional_return_pct": round(sum(values) / count, 4),
        "median_directional_return_pct": round(median(values), 4),
        "return_stddev_pct": round(pstdev(values), 4) if count > 1 else 0.0,
        "avg_gain_pct": None if avg_gain is None else round(avg_gain, 4),
        "avg_loss_pct": None if avg_loss is None else round(avg_loss, 4),
        "payoff_ratio": None if payoff is None else round(payoff, 4),
        "profit_factor": None if profit_factor is None else round(profit_factor, 4),
        "best_return_pct": round(max(values), 4), "worst_return_pct": round(min(values), 4),
        "evidence_status": evidence_status(count),
        "net_expectancy_pct": round(sum(net_values) / len(net_values), 4) if net_values else None,
        "avg_excess_return_pct": round(sum(excess_values) / len(excess_values), 4) if excess_values else None,
        "beat_baseline_rate_pct": (
            round(sum(1 for value in excess_values if value > 0) / len(excess_values) * 100.0, 2)
            if excess_values else None
        ),
    }
    metrics["edge_status"] = edge_status(metrics)
    return metrics


def decision_key(row: dict[str, Any]) -> tuple[str, str, str, int]:
    """Identity of one market decision: the same symbol, direction and horizon in the same run.

    Several agents or playbooks can emit the same decision; they share one price outcome.
    """
    return (
        str(row.get("source_run_timestamp") or ""),
        str(row.get("ticker") or ""),
        str(row.get("trade_direction") or ""),
        int(row.get("horizon_days") or 0),
    )


def decision_emitters(rows: list[dict[str, Any]]) -> dict[tuple[str, str, str, int], set[tuple[str, str]]]:
    emitters: dict[tuple[str, str, str, int], set[tuple[str, str]]] = {}
    for row in rows:
        emitter = (str(row.get("agent_name") or ""), str(row.get("playbook_name") or ""))
        emitters.setdefault(decision_key(row), set()).add(emitter)
    return emitters


def unique_decisions(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep one row per decision so cross-agent aggregates never count a shared decision twice."""
    kept: dict[tuple[str, str, str, int], dict[str, Any]] = {}
    for row in rows:
        kept.setdefault(decision_key(row), row)
    return list(kept.values())


def build_grouped_scorecards(
    rows: list[dict[str, Any]],
    dimensions: list[str],
    emitters: dict[tuple[str, str, str, int], set[tuple[str, str]]] | None = None,
) -> list[dict[str, Any]]:
    grouped: dict[tuple[Any, ...], list[dict[str, Any]]] = {}
    for row in rows:
        grouped.setdefault(tuple(row.get(name) for name in dimensions), []).append(row)
    result = []
    for key, bucket in grouped.items():
        metrics = metric_row(bucket)
        if metrics:
            entry = {**dict(zip(dimensions, key)), **metrics}
            if emitters is not None:
                shared = sum(
                    1 for row in bucket
                    if to_float(row.get("directional_return_pct")) is not None
                    and len(emitters.get(decision_key(row), ())) > 1
                )
                entry["shared_decision_count"] = shared
                entry["shared_decision_pct"] = round(shared / metrics["sample_count"] * 100.0, 2)
            result.append(entry)
    return sorted(result, key=lambda row: (row["evidence_status"] != "exploratory", row["expectancy_pct"], row["sample_count"]), reverse=True)


def build_index_readiness(rows: list[dict[str, Any]], sectors: list[dict[str, Any]]) -> dict[str, Any]:
    directional = [row for row in rows if row.get("directional_return_pct") is not None]
    symbols = {str(row.get("ticker")) for row in directional}
    classified = {str(row.get("ticker")) for row in directional if row.get("classification_status") == "classified"}
    coverage = len(classified) / len(symbols) * 100.0 if symbols else 0.0
    primary = [row for row in sectors if row.get("horizon_days") == 20 and row.get("sample_count", 0) >= 30 and row.get("sector_id") != "unknown"]
    dates = {str(row.get("source_run_timestamp") or "")[:10] for row in directional}
    gates = {
        "classification_coverage_at_least_95pct": coverage >= 95.0,
        "at_least_8_evaluable_sectors": len({row.get("sector_id") for row in primary}) >= 8,
        "at_least_500_outcome_rows": len(directional) >= 500,
        "at_least_60_symbols": len(symbols) >= 60,
        "at_least_40_run_dates": len(dates) >= 40,
        "benchmark_relative_returns_available": False,
    }
    return {
        "status": "ready" if all(gates.values()) else "not_ready",
        "purpose": "Gate for a later index-analysis phase; this report does not infer index performance.",
        "classification_coverage_pct": round(coverage, 2), "distinct_symbols": len(symbols),
        "distinct_run_dates": len(dates), "evaluable_sector_count": len({row.get("sector_id") for row in primary}),
        "gates": gates, "missing_gates": [key for key, passed in gates.items() if not passed],
    }


def load_signal_coverage(db_path: Path, verdicts: list[str]) -> list[dict[str, Any]]:
    marks = ",".join("?" for _ in verdicts)
    with closing(market_intel_db.connect(db_path)) as conn:
        market_intel_db.init_db(conn)
        rows = conn.execute(f"SELECT agent_name, playbook_name, signal_verdict, COUNT(*) signal_count FROM strategy_signals WHERE signal_verdict IN ({marks}) GROUP BY agent_name, playbook_name, signal_verdict", tuple(verdicts)).fetchall()
    return [dict(row) for row in rows]


def build_payload(db_path: Path, rows: list[dict[str, Any]], horizons: list[int], verdicts: list[str]) -> dict[str, Any]:
    base_dims = ["agent_name", "playbook_name", "signal_verdict", "trade_direction", "horizon_days"]
    emitters = decision_emitters(rows)
    decisions = unique_decisions(rows)
    # Per-emitter scorecards keep every agent/playbook's own signals and report how many
    # of them another emitter also made. Everything aggregated across emitters uses the
    # de-duplicated decisions, so a decision shared by four playbooks counts once.
    scorecards = build_grouped_scorecards(rows, base_dims, emitters=emitters)
    sectors = build_grouped_scorecards(decisions, ["sector_id", "sector", "trade_direction", "horizon_days"])
    coverage = load_signal_coverage(db_path, verdicts)
    return {
        "analysis_type": "direction_aware_signal_outcome_scorecard", "schema": "ecosystem.strategy_scorecard.v3",
        "execution_backtest": False, "generated_at": datetime.now(timezone.utc).astimezone().isoformat(),
        "db_path": str(db_path), "horizons": horizons, "verdicts": verdicts,
        "notes": [
            "Short returns are the negative of the underlying symbol return.",
            "Win rate is paired with expectancy, payoff ratio, profit factor, dispersion, and sample sufficiency.",
            "This is not an execution backtest; slippage, commissions, MFE, MAE, and borrow availability are not modeled.",
            "Sector groups below 30 observations remain exploratory.",
            "A decision is one (run, ticker, direction, horizon). Agent/playbook scorecards count their own signals; "
            "shared_decision_pct shows how many of those decisions another agent or playbook also made.",
            "Decision, sector, industry, catalyst and index-readiness figures count each decision once.",
            f"net_expectancy_pct subtracts a {ROUND_TRIP_COST_PCT:.2f}% round-trip cost (commission + slippage "
            "from the technical research lab); short borrow fees are not modeled.",
            "avg_excess_return_pct compares each decision with the average return of the whole shortlist "
            "from the same run and horizon, in the decision's direction.",
            f"edge_status is candidate_edge only with at least {MIN_EDGE_SAMPLE} decisions, a Wilson 95% lower "
            "win-rate bound above 50%, positive net expectancy and positive excess return; otherwise "
            "insufficient_sample or no_edge.",
        ],
        "round_trip_cost_pct": ROUND_TRIP_COST_PCT,
        "min_edge_sample": MIN_EDGE_SAMPLE,
        "signal_coverage": coverage, "tracked_signal_count": sum(int(row["signal_count"]) for row in coverage),
        "trade_row_count": len(rows),
        "decision_row_count": len(decisions),
        "duplicate_row_count": len(rows) - len(decisions),
        "scorecards": scorecards,
        "playbook_summary": scorecards,
        "decision_scorecards": build_grouped_scorecards(decisions, ["trade_direction", "horizon_days"]),
        "sector_scorecards": sectors,
        "industry_scorecards": build_grouped_scorecards(decisions, ["sector_id", "industry_id", "industry", "trade_direction", "horizon_days"]),
        "catalyst_scorecards": build_grouped_scorecards(decisions, ["catalyst_category_id", "trade_direction", "horizon_days"]),
        "index_readiness": build_index_readiness(decisions, sectors),
    }


def render_markdown(payload: dict[str, Any]) -> str:
    ready = payload["index_readiness"]
    lines = [
        "# Direction-Aware Strategy & Sector Scorecard", "",
        f"- Outcome rows: {payload['trade_row_count']}",
        f"- Unique decisions: {payload['decision_row_count']} ({payload['duplicate_row_count']} rows repeat a decision another agent or playbook already made)",
        f"- Index phase: {ready['status']}", "", "## Method", "",
    ]
    lines.extend(f"- {note}" for note in payload["notes"])
    lines.extend(["", "## Unique decisions", ""])
    for row in payload["decision_scorecards"]:
        lines.append(
            f"- {row['trade_direction']} / {row['horizon_days']}d | n={row['sample_count']} | "
            f"edge={row['edge_status']} | win={row['win_rate_pct']}% (Wilson low {row['wilson_win_rate_lower_pct']}%) | "
            f"expectancy={row['expectancy_pct']}% | net={row['net_expectancy_pct']}% | "
            f"vs shortlist={row['avg_excess_return_pct']}% (beat {row['beat_baseline_rate_pct']}%)"
        )
    if not payload["decision_scorecards"]:
        lines.append("- No completed decision outcomes yet.")
    lines.extend(["", "## Agent / playbook overlap", ""])
    for row in payload["scorecards"]:
        if row.get("horizon_days") == 20 and row.get("shared_decision_pct"):
            lines.append(f"- {row['agent_name']} / {row['playbook_name']} / {row['trade_direction']} / 20d | n={row['sample_count']} | shared with another emitter: {row['shared_decision_pct']}%")
    lines.extend(["", "## Sector comparison", ""])
    for row in payload["sector_scorecards"][:20]:
        lines.append(f"- {row['sector']} / {row['trade_direction']} / {row['horizon_days']}d | n={row['sample_count']} ({row['evidence_status']}) | win={row['win_rate_pct']}% | expectancy={row['expectancy_pct']}% | payoff={row['payoff_ratio']}")
    if not payload["sector_scorecards"]:
        lines.append("- No completed sector outcomes yet.")
    lines.extend(["", "## Index readiness", "", f"- Classification coverage: {ready['classification_coverage_pct']}%"])
    lines.extend(f"- Missing gate: {gate}" for gate in ready["missing_gates"])
    return "\n".join(lines)


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]), extrasaction="ignore")
        writer.writeheader(); writer.writerows(rows)


def command_build(args: Any) -> None:
    db_path = Path(args.db)
    horizons = sorted(set(args.horizons)); verdicts = [value.lower() for value in args.verdicts]
    rows = load_trade_rows(db_path, horizons, verdicts)
    attach_baselines(rows, load_shortlist_baselines(db_path, horizons))
    payload = build_payload(db_path, rows, horizons, verdicts)
    prefix = Path(args.out_prefix); prefix.parent.mkdir(parents=True, exist_ok=True)
    Path(f"{prefix}_latest.json").write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    Path(f"{prefix}_latest.md").write_text(render_markdown(payload), encoding="utf-8")
    write_csv(Path(f"{prefix}_scorecards_latest.csv"), payload["scorecards"])
    write_csv(Path(f"{prefix}_sector_scorecards_latest.csv"), payload["sector_scorecards"])
    write_csv(Path(f"{prefix}_industry_scorecards_latest.csv"), payload["industry_scorecards"])
    write_csv(Path(f"{prefix}_trades_latest.csv"), rows)
    print(f"[OUTPUT] Direction-aware scorecard: {prefix}_latest.json")
