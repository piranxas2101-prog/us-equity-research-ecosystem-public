from __future__ import annotations

import hashlib
import json
from collections import Counter
from typing import Any

import holding_horizon
import playbook_framework
import trader_confidence


SCHEMA = "ecosystem.trader_trade_plan.v1"
POLICY_SCHEMA = "ecosystem.trader_sizing_policy.v1"


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(value, high))


def to_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def validate_policy(policy: dict[str, Any]) -> None:
    if policy.get("schema") != POLICY_SCHEMA:
        raise ValueError(f"Unsupported trader sizing policy: {policy.get('schema')!r}")
    required = {
        "base_notional_usd", "min_notional_usd", "max_notional_usd", "score_floor",
        "confidence_multipliers", "risk_multipliers", "direction_multipliers",
        "stop_atr_multiple", "min_stop_pct", "max_stop_pct", "reward_to_risk",
        "min_take_profit_pct", "max_take_profit_pct", "holding_period_rule",
    }
    missing = sorted(required - set(policy))
    if missing:
        raise ValueError(f"Trader sizing policy is missing fields: {missing}")
    minimum = to_float(policy["min_notional_usd"])
    maximum = to_float(policy["max_notional_usd"])
    base = to_float(policy["base_notional_usd"])
    if minimum <= 0 or maximum < minimum or not minimum <= base <= maximum:
        raise ValueError("Invalid notional bounds in trader sizing policy.")


def suggested_notional(row: dict[str, Any], policy: dict[str, Any]) -> tuple[float, dict[str, float]]:
    validate_policy(policy)
    score = to_float(row.get("signal_score"))
    score_floor = to_float(policy["score_floor"], 5.0)
    score_factor = 0.50 + clamp((score - score_floor) / max(0.01, 10.0 - score_floor), 0.0, 1.0)
    # Sized by the risk tier (the values the old "confidence" label carried), so no order size changes.
    confidence = trader_confidence.sizing_label(row)
    risk = str(row.get("risk_level") or "Medium")
    direction = str(row.get("trade_direction") or "flat").lower()
    confidence_factor = to_float((policy.get("confidence_multipliers") or {}).get(confidence), 0.5)
    risk_factor = to_float((policy.get("risk_multipliers") or {}).get(risk), 0.0)
    direction_factor = to_float((policy.get("direction_multipliers") or {}).get(direction), 0.0)
    raw = to_float(policy["base_notional_usd"]) * score_factor * confidence_factor * risk_factor * direction_factor
    notional = clamp(raw, to_float(policy["min_notional_usd"]), to_float(policy["max_notional_usd"]))
    return round(notional, 2), {
        "score_factor": round(score_factor, 4),
        "confidence_factor": round(confidence_factor, 4),
        "risk_factor": round(risk_factor, 4),
        "direction_factor": round(direction_factor, 4),
    }


def build_trade_plan(
    agent: str,
    row: dict[str, Any],
    playbook: dict[str, Any],
    policy: dict[str, Any],
    run_id: str,
    curve: dict[tuple[str, str], list[dict[str, Any]]] | None = None,
) -> dict[str, Any]:
    notional, factors = suggested_notional(row, policy)
    atr_pct = max(0.0, to_float(row.get("atr_14_pct"))) / 100.0
    stop_pct = clamp(
        atr_pct * to_float(policy["stop_atr_multiple"], 1.0),
        to_float(policy["min_stop_pct"]),
        to_float(policy["max_stop_pct"]),
    )
    take_profit_pct = clamp(
        stop_pct * to_float(policy["reward_to_risk"], 1.5),
        to_float(policy["min_take_profit_pct"]),
        to_float(policy["max_take_profit_pct"]),
    )
    rationale = row.get("rationale") if isinstance(row.get("rationale"), list) else [str(row.get("rationale") or "")]
    direction = str(row.get("trade_direction") or "flat").lower()
    immutable = {
        "schema": SCHEMA,
        "run_id": run_id,
        "agent": agent,
        "ticker": str(row.get("ticker") or "").upper(),
        "playbook_id": playbook["playbook_id"],
        "playbook_version": playbook["version"],
        "portfolio_status_required": "active",
        "direction": direction,
        "signal_verdict": row.get("signal_verdict"),
        "signal_score": row.get("signal_score"),
        "confidence": row.get("confidence"),
        "suggested_notional_usd": notional,
        "sizing_factors": factors,
        "sizing_policy_sha256": digest(policy),
        "stop_loss_pct": round(stop_pct, 6),
        "take_profit_pct": round(take_profit_pct, 6),
        "holding_period_rule": str(policy["holding_period_rule"]),
        "same_day_flatten_cutoff_eastern": str(policy.get("same_day_flatten_cutoff_eastern") or "15:55"),
        "entry_confirmation": list(playbook.get("entry_confirmation", [])),
        "invalidators": list(playbook.get("invalidators", [])),
        "exit_logic": list(playbook.get("exit_logic", [])),
        "catalog_holding_horizon": playbook.get("holding_horizon"),
        "short_gates": list(playbook.get("short_gates", [])),
        "unresolved_short_gates_at_signal": playbook_framework.unresolved_short_gates(playbook, row, direction),
        "initial_rationale": rationale,
        "initial_rationale_sha256": digest(rationale),
    }
    if str(policy["holding_period_rule"]) == holding_horizon.RULE:
        # owner 2026-10-23: the agent decides how long it holds, from the horizon curve; without a real edge at any
        # horizon the position stays same-day (holding_horizon.py)
        horizon = holding_horizon.decide(playbook["playbook_id"], direction, curve or {})
        immutable["holding_period_rule"] = horizon["rule"]
        immutable["holding_horizon"] = horizon
    immutable["trade_plan_id"] = "plan_" + digest(immutable)[:24]
    immutable["trade_plan_sha256"] = digest(immutable)
    immutable["execution_authority"] = False
    return immutable


def attach_trade_plans(
    rows: list[dict[str, Any]],
    catalog: dict[str, Any],
    policy: dict[str, Any],
    agent: str,
    run_id: str,
) -> dict[str, Any]:
    validate_policy(policy)
    known = playbook_framework.catalog_by_id(catalog)
    curve = holding_horizon.load_curve() if str(policy["holding_period_rule"]) == holding_horizon.RULE else None
    counts: Counter[str] = Counter()
    for row in rows:
        playbook_id = str(row.get("playbook_name") or "")
        playbook = known.get(playbook_id)
        if playbook is None:
            row["paper_trade_plan_error"] = "playbook_not_in_catalog"
            counts["error"] += 1
            continue
        plan = build_trade_plan(agent, row, playbook, policy, run_id, curve)
        row["paper_trade_plan"] = plan
        counts[str(row.get("signal_verdict") or "unknown")] += 1
    return {
        "schema": POLICY_SCHEMA,
        "agent": agent,
        "policy_sha256": digest(policy),
        "plans_attached": sum(counts.values()),
        "verdict_counts": dict(counts),
        "execution_authority": False,
    }
