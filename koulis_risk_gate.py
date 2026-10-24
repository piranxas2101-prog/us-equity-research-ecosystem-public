from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import agent_methodology
import agent_personality
from primary_data_freshness import primary_data_fresh
from thymios_order_intent import (
    SCHEMA,
    build_validation_report,
    stable_intent_id,
    validate_intent_payload,
    write_validation_report,
)
from common import load_json
import alpaca_short_gate_data as short_gates


def as_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def source_run_id(payload: dict[str, Any]) -> str:
    return str(payload.get("run_id") or payload.get("run_timestamp") or "").strip()


def existing_exposure_symbols(preflight: dict[str, Any]) -> set[str]:
    snapshot = preflight.get("snapshot_before") or {}
    symbols: set[str] = set()
    for group in (snapshot.get("positions") or [], snapshot.get("open_orders") or []):
        for item in group:
            if not isinstance(item, dict):
                continue
            symbol = str(item.get("symbol") or item.get("ticker") or "").strip().upper()
            if symbol:
                symbols.add(symbol)
    return symbols


def _float(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number else None


HELD_LIMIT_MODES = ("none", "count")
SCANNER_CONFIG = Path(__file__).resolve().parent / "scanner_config.json"


def held_limit_mode(config: Path = SCANNER_CONFIG) -> str:
    """``paper_session.held_positions_limit``: "none" (owner 2026-10-23: no limit on held positions to begin with -
    the agents' win rate decides later) or "count" (held positions take order slots, notional and short limits)."""
    try:
        mode = (json.loads(config.read_text(encoding="utf-8-sig")).get("paper_session") or {}).get(
            "held_positions_limit")
    except (OSError, ValueError):
        mode = None
    return mode if mode in HELD_LIMIT_MODES else "count"  # unreadable switch: the stricter side


NEW_ORDER_LIMIT_MODES = ("none", "limits")
NO_LIMIT_ORDERS = 1_000_000
NO_LIMIT_NOTIONAL = 1e12  # finite, so the JSON stays standard for PowerShell and Thymios


def new_orders_limit_mode(config: Path = SCANNER_CONFIG) -> str:
    """``paper_session.new_orders_limit``: "none" (owner 2026-10-24: "let them make as many trades as they want,
    just watched by the monitors with the rules we set" - every agent decision is taken, so the win rate shows
    whether they open positions for a reason) or "limits" (the launcher's daily limits: orders, notional, shorts,
    per strategy). An unreadable switch keeps the limits."""
    try:
        mode = (json.loads(config.read_text(encoding="utf-8-sig")).get("paper_session") or {}).get("new_orders_limit")
    except (OSError, ValueError):
        mode = None
    return mode if mode in NEW_ORDER_LIMIT_MODES else "limits"


def held_multi_day_positions(preflight: dict[str, Any]) -> list[dict[str, Any]]:
    """Positions held on their agent's horizon that the preflight kept open (ADR-001 step 3). Their tickers are
    always blocked for a new order; whether they also count toward the limits is ``held_limit_mode``."""
    return [item for item in preflight.get("kept_positions") or [] if isinstance(item, dict)]


def prefilter_strategy_rows(payload: dict[str, Any], agent: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Keep research observations visible without misclassifying them as Koulis risk rejects."""

    execution_candidates: list[dict[str, Any]] = []
    filtered: list[dict[str, Any]] = []
    for raw in payload.get("results", []):
        if not isinstance(raw, dict):
            continue
        verdict = str(raw.get("signal_verdict") or "").strip().lower()
        pre_gate_verdict = str(raw.get("pre_gate_verdict") or verdict).strip().lower()
        direction = str(raw.get("trade_direction") or "").strip().lower()
        filter_reasons: list[str] = []
        downgraded_by_fundamental_gate = pre_gate_verdict == "candidate" and verdict != "candidate"
        if verdict != "candidate" and not downgraded_by_fundamental_gate:
            filter_reasons.append("signal_not_candidate")
        if raw.get("execution_eligible") is not True:
            filter_reasons.append("fundamental_not_execution_ready")
        if str(raw.get("fundamental_gate_status") or "") != "pass":
            filter_reasons.append("fundamental_gate_not_passed")
        if verdict != "candidate":
            filter_reasons.append("signal_not_candidate")
        filter_reasons.extend(str(value) for value in (raw.get("gate_decision_reasons") or []))
        filter_reasons.extend(str(value) for value in (raw.get("fundamental_decision_blockers") or []))
        filter_reasons = list(dict.fromkeys(value for value in filter_reasons if value))
        if not filter_reasons:
            execution_candidates.append(raw)
            continue
        reason = filter_reasons[0]
        filtered.append(
            {
                "agent": agent,
                "ticker": str(raw.get("ticker") or "").strip().upper(),
                "trade_direction": direction,
                "playbook_name": raw.get("playbook_name"),
                "signal_verdict": verdict,
                "pre_gate_verdict": pre_gate_verdict,
                "post_gate_verdict": str(raw.get("post_gate_verdict") or verdict).strip().lower(),
                "signal_score": round(as_float(raw.get("signal_score")), 4),
                "execution_eligible": raw.get("execution_eligible"),
                "fundamental_gate_status": raw.get("fundamental_gate_status"),
                "status": "filtered_observation",
                "filter_reason": reason,
                "filter_reasons": filter_reasons,
                "verdict_transition": raw.get("verdict_transition"),
            }
        )
    return execution_candidates, filtered


def candidate_rows(
    payload: dict[str, Any], agent: str, min_score: float, notional_usd: float,
    notional_source: str = "default", expected_run_id: str = "",
    short_gate_data: dict[str, Any] | None = None, today: str = "",
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    candidates: list[dict[str, Any]] = []
    blocked: list[dict[str, Any]] = []
    for raw in payload.get("results", []):
        if not isinstance(raw, dict):
            continue
        ticker = str(raw.get("ticker") or "").strip().upper()
        direction = str(raw.get("trade_direction") or "").strip().lower()
        playbook = str(raw.get("playbook_name") or "").strip()
        score = as_float(raw.get("signal_score"))
        trade_plan = raw.get("paper_trade_plan") if isinstance(raw.get("paper_trade_plan"), dict) else None
        selected_notional = notional_usd
        short_resolution: dict[str, Any] | None = None
        remaining_gates: list[str] = []
        if direction == "short" and trade_plan is not None and trade_plan.get("unresolved_short_gates_at_signal"):
            # owner's choice B (2026-10-24): the gates the signal left open are resolved from today's broker and
            # Giannis data (alpaca_short_gate_data.py); whatever stays unknown keeps the short blocked
            resolved, remaining_gates = short_gates.resolve(
                list(trade_plan["unresolved_short_gates_at_signal"]), short_gate_data, ticker, today)
            if not remaining_gates:
                short_resolution = {"as_of": today, "resolved": resolved}
        if notional_source == "agent_suggestion" and trade_plan is not None:
            selected_notional = as_float(trade_plan.get("suggested_notional_usd"), 0.0)
        reason = None
        if not ticker or direction not in {"long", "short"} or not playbook:
            reason = "invalid_trade_identity"
        elif raw.get("execution_eligible") is not True:
            reason = "fundamental_thesis_not_execution_ready"
        elif str(raw.get("fundamental_gate_status") or "") != "pass":
            reason = "fundamental_gate_not_passed"
        elif not str(raw.get("fundamental_thesis_hash") or "").strip():
            reason = "missing_fundamental_thesis_identity"
        elif str(raw.get("signal_verdict") or "").lower() != "candidate":
            reason = "not_candidate"
        elif str(raw.get("instrument_type") or "common").lower() != "common":
            reason = "non_common_instrument"
        elif str(raw.get("risk_level") or "Medium").lower() == "high":
            reason = "high_risk"
        elif not primary_data_fresh(raw):
            reason = "stale_primary_data"
        elif score < min_score:
            reason = "score_below_threshold"
        elif notional_source == "agent_suggestion" and trade_plan is None:
            reason = "missing_agent_trade_plan"
        elif trade_plan is not None and (
            str(trade_plan.get("run_id") or "") != expected_run_id
            or str(trade_plan.get("agent") or "").lower() != agent
            or str(trade_plan.get("ticker") or "").upper() != ticker
            or str(trade_plan.get("direction") or "").lower() != direction
            or str(trade_plan.get("playbook_id") or "") != playbook
        ):
            reason = "trade_plan_identity_mismatch"
        elif trade_plan is not None and trade_plan.get("execution_authority") is not False:
            reason = "trade_plan_authority_violation"
        elif trade_plan is not None and str(trade_plan.get("portfolio_status_required") or "") != "active":
            reason = "trade_plan_not_active_specialization"
        elif direction == "short" and remaining_gates:
            reason = "unresolved_short_implementation_gates:" + ",".join(sorted(remaining_gates))
        elif selected_notional <= 0:
            reason = "invalid_agent_notional"
        item = {
            "agent": agent,
            "ticker": ticker,
            "company": raw.get("company"),
            "trade_direction": direction,
            "playbook_name": playbook,
            "signal_score": round(score, 4),
            "notional_usd": round(selected_notional, 2),
            "notional_source": notional_source,
            "trade_plan": trade_plan,
            "short_gate_resolution": short_resolution,
            "fundamental_packet_hash": raw.get("fundamental_packet_hash"),
            "fundamental_thesis_id": raw.get("fundamental_thesis_id"),
            "fundamental_thesis_hash": raw.get("fundamental_thesis_hash"),
            "fundamental_gate_status": raw.get("fundamental_gate_status"),
            "execution_eligible": raw.get("execution_eligible"),
            "source_signal": raw,
        }
        if reason:
            blocked.append({**item, "status": "blocked", "block_reason": reason})
        else:
            candidates.append(item)
    return candidates, blocked


SELECTION_MODES = ("strategy_rank", "score")
DEFAULT_MAX_PER_STRATEGY = 2


def attach_strategy_ranks(candidates: list[dict[str, Any]]) -> None:
    """Each candidate's standing inside its own strategy (agent, playbook, direction): the mid-rank percentile of
    its score among that strategy's candidates today, 0..1.

    The strategies score on different scales (owner's decision 2026-10-15, option C): support_bounce_swing reaches
    10.0 for many names while no other strategy does, so a raw-score ranking gave it every slot on the first paper
    session. Being near the top of its own strategy is comparable across strategies; a strategy with a single
    candidate stands at 0.5 (the top of one is weaker evidence than the top of a hundred)."""
    groups: dict[tuple[str, str, str], list[float]] = {}
    for item in candidates:
        groups.setdefault((item["agent"], item["playbook_name"], item["trade_direction"]), []).append(item["signal_score"])
    for item in candidates:
        scores = groups[(item["agent"], item["playbook_name"], item["trade_direction"])]
        below = sum(1 for s in scores if s < item["signal_score"])
        equal = sum(1 for s in scores if s == item["signal_score"])
        item["strategy_rank_pct"] = round((below + 0.5 * equal) / len(scores), 4)
        item["strategy_candidates"] = len(scores)


def build_gate(
    strategies: list[tuple[str, dict[str, Any]]],
    preflight: dict[str, Any],
    *,
    run_id: str,
    min_score: float,
    max_total_orders: int,
    notional_per_trade: float,
    max_total_notional: float,
    max_short_orders: int,
    notional_source: str = "default",
    selection: str = "strategy_rank",
    max_per_strategy: int = DEFAULT_MAX_PER_STRATEGY,
    held_limit: str = "count",
    short_gate_data: dict[str, Any] | None = None,
    today: str | None = None,
) -> dict[str, Any]:
    if selection not in SELECTION_MODES:
        raise ValueError(f"selection must be one of {SELECTION_MODES}.")
    if notional_per_trade <= 0:
        raise ValueError("notional_per_trade must be positive.")
    if max_total_notional < 0:
        raise ValueError("max_total_notional cannot be negative.")
    all_candidates: list[dict[str, Any]] = []
    blocked: list[dict[str, Any]] = []
    filtered_observations: list[dict[str, Any]] = []
    source_runs: dict[str, str] = {}
    run_mismatch = False
    for agent, payload in strategies:
        agent_run = source_run_id(payload)
        source_runs[agent] = agent_run
        if run_id and agent_run and agent_run != run_id:
            run_mismatch = True
            blocked.extend(
                {
                    "agent": agent,
                    "ticker": str(row.get("ticker") or "").upper(),
                    "status": "blocked",
                    "block_reason": "run_id_mismatch",
                }
                for row in payload.get("results", [])
                if isinstance(row, dict)
            )
            continue
        execution_rows, filtered = prefilter_strategy_rows(payload, agent)
        filtered_observations.extend(filtered)
        filtered_payload = {**payload, "results": execution_rows}
        fresh, rejected = candidate_rows(
            filtered_payload, agent, min_score, notional_per_trade,
            notional_source=notional_source, expected_run_id=run_id,
            short_gate_data=short_gate_data, today=today or datetime.now(timezone.utc).date().isoformat(),
        )
        all_candidates.extend(fresh)
        blocked.extend(rejected)

    attach_strategy_ranks(all_candidates)
    if selection == "strategy_rank":
        def priority(row: dict[str, Any]) -> tuple:
            return (-row["strategy_rank_pct"], -row["signal_score"], row["ticker"], row["agent"], row["playbook_name"])
    else:
        def priority(row: dict[str, Any]) -> tuple:
            return (-row["signal_score"], row["ticker"], row["agent"], row["playbook_name"])

    exposure = existing_exposure_symbols(preflight)
    eligible: list[dict[str, Any]] = []
    by_ticker: dict[str, list[dict[str, Any]]] = {}
    for item in all_candidates:
        by_ticker.setdefault(item["ticker"], []).append(item)

    for ticker, rows in by_ticker.items():
        if ticker in exposure:
            blocked.extend({**item, "status": "blocked", "block_reason": "existing_account_exposure"} for item in rows)
            continue
        directions = {item["trade_direction"] for item in rows}
        if len(directions) > 1:
            blocked.extend({**item, "status": "blocked", "block_reason": "agent_direction_conflict"} for item in rows)
            continue
        winner = sorted(rows, key=priority)[0]
        eligible.append(winner)
        blocked.extend(
            {**item, "status": "blocked", "block_reason": "duplicate_symbol_proposal"}
            for item in rows
            if item is not winner
        )

    approved: list[dict[str, Any]] = []
    held = held_multi_day_positions(preflight)
    held_values = [_float(item.get("market_value")) for item in held]
    used_notional_held = sum(abs(value) for value in held_values if value is not None)
    counted = held_limit == "count"
    held_slots = len(held) if counted else 0
    short_count = sum(1 for item in held if str(item.get("side") or "").lower() == "short") if counted else 0
    # unknown stays unknown: a held value the limits need but cannot read stops new orders
    held_value_unknown = counted and any(value is None for value in held_values)
    used_notional = used_notional_held if counted else 0.0
    per_strategy: dict[tuple[str, str], int] = {}
    for item in sorted(eligible, key=priority):
        reason = None
        strategy_key = (item["agent"], item["playbook_name"])
        if len(approved) + held_slots >= max(0, max_total_orders):
            reason = "portfolio_order_limit"
        elif max_per_strategy > 0 and per_strategy.get(strategy_key, 0) >= max_per_strategy:
            reason = "portfolio_strategy_limit"
        elif used_notional + item["notional_usd"] > max_total_notional + 1e-9:
            reason = "portfolio_notional_limit"
        elif item["trade_direction"] == "short" and short_count >= max(0, max_short_orders):
            reason = "portfolio_short_limit"
        if reason:
            blocked.append({**item, "status": "blocked", "block_reason": reason})
            continue
        approved_item = {
            key: value for key, value in item.items() if key != "source_signal"
        }
        approved_item.update(
            {
                "intent_id": stable_intent_id(
                    run_id,
                    item["agent"],
                    item["ticker"],
                    item["trade_direction"],
                    item["playbook_name"],
                ),
                "status": "approved",
            }
        )
        approved.append(approved_item)
        per_strategy[strategy_key] = per_strategy.get(strategy_key, 0) + 1
        used_notional += item["notional_usd"]
        short_count += int(item["trade_direction"] == "short")

    gate_status = "approved"
    gate_reason = "portfolio_limits_and_cross_agent_conflicts_checked"
    if bool(preflight.get("block_trader_run")):
        approved = []
        gate_status = "blocked"
        gate_reason = "alpaca_preflight_blocked"
    elif held_value_unknown:
        approved = []
        gate_status = "blocked"
        gate_reason = "held_position_value_unknown"
    elif run_mismatch:
        approved = []
        gate_status = "blocked"
        gate_reason = "strategy_run_id_mismatch"

    if gate_status == "approved" and not approved:
        completion_status = "completed_no_eligible_intents"
        gate_reason = "no_eligible_intents_after_prefilter"
    elif gate_status == "approved":
        completion_status = "completed_with_approved_intents"
    else:
        completion_status = "blocked"

    now = datetime.now(timezone.utc)
    payload = {
        "schema": SCHEMA,
        "generated_at": now.isoformat(),
        "session_date": now.date().isoformat(),
        "run_id": run_id,
        "gate_agent": "koulis",
        "execution_validator": "thymios",
        "methodology": agent_methodology.methodology_identity("koulis"),
        "personality": agent_personality.personality_identity("koulis"),
        "gate_status": gate_status,
        "gate_reason": gate_reason,
        "completion_status": completion_status,
        "source_run_ids": source_runs,
        "portfolio_limits": {
            "max_total_orders": max(0, max_total_orders),
            "notional_per_trade_usd": round(notional_per_trade, 2),
            "notional_source": notional_source,
            "max_total_notional_usd": round(max_total_notional, 2),
            "max_short_orders": max(0, max_short_orders),
            "selection": selection,
            "max_per_strategy": max_per_strategy,
        },
        "existing_exposure_symbols": sorted(exposure),
        "held_multi_day_positions": [item.get("symbol") for item in held],
        "held_positions_limit": held_limit,
        "held_multi_day_notional": (None if any(value is None for value in held_values)
                                    else round(used_notional_held, 2)),
        "approved_count": len(approved),
        "approved_notional_usd": round(sum(item["notional_usd"] for item in approved), 2),
        "approved_intents": approved,
        "blocked_count": len(blocked),
        "blocked_intents": blocked,
        "prefiltered_count": len(filtered_observations),
        "prefiltered_observations": filtered_observations,
    }
    if gate_status == "approved":
        validate_intent_payload(payload, today=now.date())
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description="Koulis portfolio-level risk gate for agent paper-order intents.")
    parser.add_argument("--yankos", required=True)
    parser.add_argument("--periklis", required=True)
    parser.add_argument("--preflight", required=True)
    parser.add_argument("--output", default="output/koulis_order_intents_latest.json")
    parser.add_argument("--thymios-output", default="output/thymios_execution_validation_latest.json")
    parser.add_argument("--run-id", default=os.getenv("ECOSYSTEM_RUN_ID", ""))
    parser.add_argument("--min-score", type=float, default=7.0)
    parser.add_argument("--max-total-orders", type=int, default=6)
    parser.add_argument("--notional-per-trade", type=float, default=250.0)
    parser.add_argument("--notional-source", choices=("default", "user_setting", "agent_suggestion"), default="default")
    parser.add_argument("--max-total-notional", type=float)
    parser.add_argument("--max-short-orders", type=int, default=2)
    parser.add_argument("--selection", choices=SELECTION_MODES, default="strategy_rank",
                        help="strategy_rank: standing inside each strategy (owner 2026-10-15); score: raw signal score")
    parser.add_argument("--short-gate-data", default="output/short_gate_data_latest.json",
                        help="broker and short-interest data that resolves the SHORT gates (alpaca_short_gate_data.py)")
    parser.add_argument("--max-per-strategy", type=int, default=DEFAULT_MAX_PER_STRATEGY,
                        help="approved intents per (agent, playbook); 0 = no limit")
    args = parser.parse_args()

    strategies = [("yankos", load_json(Path(args.yankos))), ("periklis", load_json(Path(args.periklis)))]
    inferred_run = args.run_id or source_run_id(strategies[0][1]) or source_run_id(strategies[1][1])
    if not inferred_run:
        raise SystemExit("Koulis gate requires a run_id or strategy run timestamp.")
    max_notional = args.max_total_notional
    if max_notional is None:
        max_notional = max(0, args.max_total_orders) * max(0.0, args.notional_per_trade)
    if new_orders_limit_mode() == "none":
        # one candidate per ticker, no opposite directions and held tickers stay blocked; no count or size cap
        args.max_total_orders, args.max_short_orders, args.max_per_strategy = NO_LIMIT_ORDERS, NO_LIMIT_ORDERS, 0
        max_notional = NO_LIMIT_NOTIONAL
    payload = build_gate(
        strategies,
        load_json(Path(args.preflight)),
        run_id=inferred_run,
        min_score=args.min_score,
        max_total_orders=args.max_total_orders,
        notional_per_trade=args.notional_per_trade,
        max_total_notional=max_notional,
        max_short_orders=args.max_short_orders,
        notional_source=args.notional_source,
        selection=args.selection,
        max_per_strategy=args.max_per_strategy,
        held_limit=held_limit_mode(),
        short_gate_data=load_json(Path(args.short_gate_data)) if Path(args.short_gate_data).is_file() else None,
    )
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    thymios_report = build_validation_report(payload, source_artifact=str(output))
    write_validation_report(Path(args.thymios_output), thymios_report)
    print(
        f"[KOULIS] status={payload['gate_status']} approved={payload['approved_count']} "
        f"blocked={payload['blocked_count']} prefiltered={payload['prefiltered_count']}"
    )
    print(f"[THYMIOS] status={thymios_report['status']} validated={thymios_report['approved_intent_count']}")
    print(f"[OUTPUT] {output}")
    print(f"[OUTPUT] {args.thymios_output}")
    return 0 if payload["gate_status"] == "approved" else 2


if __name__ == "__main__":
    raise SystemExit(main())
