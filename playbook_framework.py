from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


SCHEMA = "ecosystem.playbook_catalog.v2"
ROUTER_SCHEMA = "ecosystem.strategy_router.v2"
ALLOWED_STATUSES = {"research", "shadow", "paper_candidate", "active", "paused", "retired"}
ALLOWED_SIDES = {"long", "short", "both", "flat"}


def as_list(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def to_float(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def load_catalog(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    validate_catalog(payload)
    return payload


def validate_catalog(payload: dict[str, Any]) -> None:
    if payload.get("schema") != SCHEMA:
        raise ValueError(f"Unsupported playbook catalog schema: {payload.get('schema')!r}")
    playbooks = as_list(payload.get("playbooks"))
    if not playbooks:
        raise ValueError("Playbook catalog is empty.")
    seen: set[str] = set()
    required = {
        "playbook_id",
        "version",
        "family",
        "sides",
        "holding_horizon",
        "required_data",
        "entry_confirmation",
        "invalidators",
        "exit_logic",
        "evaluation_metrics",
    }
    for playbook in playbooks:
        missing = sorted(required - set(playbook))
        if missing:
            raise ValueError(f"Playbook is missing required fields {missing}: {playbook}")
        playbook_id = str(playbook.get("playbook_id") or "").strip()
        if not playbook_id or playbook_id in seen:
            raise ValueError(f"Duplicate or empty playbook_id: {playbook_id!r}")
        seen.add(playbook_id)
        sides = {str(item).lower() for item in playbook.get("sides", [])}
        if not sides or not sides.issubset(ALLOWED_SIDES):
            raise ValueError(f"Invalid sides for {playbook_id}: {sorted(sides)}")


def catalog_by_id(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {str(item["playbook_id"]): item for item in as_list(payload.get("playbooks"))}


def validate_portfolio(catalog: dict[str, Any], portfolio: list[dict[str, Any]]) -> None:
    known = catalog_by_id(catalog)
    seen: set[str] = set()
    for assignment in portfolio:
        playbook_id = str(assignment.get("playbook_id") or "")
        status = str(assignment.get("status") or "research")
        if playbook_id not in known:
            raise ValueError(f"Portfolio references unknown playbook: {playbook_id}")
        if playbook_id in seen:
            raise ValueError(f"Portfolio repeats playbook: {playbook_id}")
        if status not in ALLOWED_STATUSES:
            raise ValueError(f"Portfolio has invalid status {status!r} for {playbook_id}")
        seen.add(playbook_id)


def infer_market_state(row: dict[str, Any]) -> dict[str, Any]:
    rsi = to_float(row.get("rsi_14"))
    return_20d = to_float(row.get("return_20d_pct"))
    price_position_20d = to_float(row.get("price_position_20d_pct"))
    price_position_60d = to_float(row.get("price_position_60d_pct"))
    macd = to_float(row.get("macd_histogram"))
    atr = to_float(row.get("atr_14_pct"))
    bb_width = to_float(row.get("bb_width_20_pct"))
    timing = str(row.get("technical_timing") or "neutral")
    regimes: set[str] = {"neutral"}

    if (return_20d is not None and return_20d > 4) or (macd is not None and macd > 0):
        regimes.add("trend_up")
    if (return_20d is not None and return_20d < -4) or (macd is not None and macd < 0):
        regimes.add("trend_down")
    if atr is not None and atr >= 6:
        regimes.add("high_volatility")
    if (bb_width is not None and bb_width <= 10) or timing in {"early_accumulation", "pre_run_watch"}:
        regimes.add("compression")
    if (rsi is not None and rsi >= 68) or (price_position_20d is not None and price_position_20d >= 88):
        regimes.add("extended_up")
    if (rsi is not None and rsi <= 38) or (price_position_20d is not None and price_position_20d <= 25):
        regimes.add("depressed")
    if timing in {"near_lows_reversal", "pullback_watch"}:
        regimes.add("reversal_zone")

    return {
        "regimes": sorted(regimes),
        "technical_timing": timing,
        "rsi_14": rsi,
        "return_20d_pct": return_20d,
        "price_position_20d_pct": price_position_20d,
        "price_position_60d_pct": price_position_60d,
        "macd_histogram": macd,
        "atr_14_pct": atr,
    }


def missing_required_data(playbook: dict[str, Any], row: dict[str, Any]) -> list[str]:
    aliases = {
        "current_session_15m_bars": ("current_session_15m_bars", "latest_intraday_bar_time"),
        "vwap": ("vwap", "session_vwap"),
        "gap_pct": ("gap_pct",),
        "sector_relative_strength": ("sector_relative_strength", "relative_strength_sector"),
        "fundamental_revision": ("fundamental_revision", "estimate_revision"),
    }
    missing: list[str] = []
    for field in playbook.get("required_data", []):
        name = str(field)
        if name == "opening_range":
            if row.get("opening_range_high") in (None, "") or row.get("opening_range_low") in (None, ""):
                missing.append(name)
            continue
        if name == "borrow_shortability":
            if row.get("shortable") is not True and row.get("borrow_available") is not True:
                missing.append(name)
            continue
        if name == "fresh_primary_data":
            if row.get("fresh_primary_data") is not True:
                missing.append(name)
            continue
        if name == "earnings_event":
            catalyst = str(row.get("catalyst_category_id") or "").lower()
            if row.get("earnings_event") in (None, "", []) and "earn" not in catalyst:
                missing.append(name)
            continue
        options = aliases.get(name, (name,))
        values = [row.get(option) for option in options]
        if not any(value is not None and value != "" and value != [] for value in values):
            missing.append(name)
    return missing


def unresolved_short_gates(playbook: dict[str, Any], row: dict[str, Any], side: str) -> list[str]:
    """Return implementation checks that remain unknown for a SHORT perspective.

    A catalog match is an analytical view, not permission to short.  These checks
    intentionally remain separate from ``required_data`` so that a useful SHORT
    counterview can still be recorded while execution stays blocked.
    """
    if side != "short":
        return []
    gates = " ".join(str(item).lower() for item in playbook.get("short_gates", []))
    unresolved: list[str] = []

    def has_value(*names: str) -> bool:
        return any(name in row and row.get(name) not in (None, "", []) for name in names)

    # Global SHORT contract: these gates apply even when a catalog entry omitted
    # them.  A missing declaration must never become an accidental permission.
    if row.get("shortable") is not True and row.get("borrow_available") is not True:
        unresolved.append("shortability")
    if not has_value("borrow_fee_pct", "borrow_cost_pct", "borrow_rate"):
        unresolved.append("borrow_carry")
    if not has_value("squeeze_risk", "low_float_risk", "buyback_risk", "float_shares"):
        unresolved.append("squeeze_buyback_low_float_risk")
    if "catalyst timing" in gates or "dated catalyst" in gates:
        if not has_value("catalyst_timing", "catalyst_date"):
            unresolved.append("catalyst_timing")
    cover_defined = any(
        any(token in str(item).lower() for token in ("cover", "exit", "time stop", "thesis"))
        for item in playbook.get("short_gates", []) + playbook.get("exit_logic", [])
    )
    if not cover_defined:
        unresolved.append("cover_rule")
    return sorted(set(unresolved))


def data_quality_blockers(playbook: dict[str, Any], row: dict[str, Any], side: str) -> list[str]:
    blockers: list[str] = []
    if "fresh_primary_data" in playbook.get("required_data", []) and row.get("fresh_primary_data") is not True:
        blockers.append("fresh_primary_data_not_true")
    blockers.extend(unresolved_short_gates(playbook, row, side))
    return sorted(set(blockers))


def directional_scores(playbook: dict[str, Any], row: dict[str, Any], state: dict[str, Any]) -> tuple[float, float, list[str]]:
    family = str(playbook.get("family") or "")
    regimes = set(state["regimes"])
    rsi = state["rsi_14"]
    ret = state["return_20d_pct"]
    pos = state["price_position_20d_pct"]
    macd = state["macd_histogram"]
    long_score = 2.5
    short_score = 2.5
    reasons: list[str] = []

    if "trend_up" in regimes:
        long_score += 1.4
        short_score -= 0.4
    if "trend_down" in regimes:
        short_score += 1.4
        long_score -= 0.4
    if macd is not None:
        if macd > 0:
            long_score += 0.8
        elif macd < 0:
            short_score += 0.8
    if ret is not None:
        if ret > 5:
            long_score += 0.6
        elif ret < -5:
            short_score += 0.6

    if family in {"support_reversal", "failed_breakdown"}:
        if "depressed" in regimes or "reversal_zone" in regimes:
            long_score += 2.2
            reasons.append("Price state supports a LONG reversal/reclaim perspective.")
    if family in {"breakout", "continuation", "compression"}:
        if "trend_up" in regimes or "compression" in regimes:
            long_score += 1.8
        if "trend_down" in regimes or "compression" in regimes:
            short_score += 1.3
        reasons.append("Trend/compression state is relevant to continuation analysis.")
    if family in {"failed_breakout", "mean_reversion", "gap_fade"}:
        if "extended_up" in regimes:
            short_score += 2.3
            reasons.append("Extension supports a SHORT fade/failure perspective if rejection confirms.")
        if "depressed" in regimes:
            long_score += 1.5
    if family in {"gap_momentum", "opening_range", "vwap"}:
        reasons.append("Current-session data is required before this tactical view is actionable.")
    if family == "relative_strength":
        if to_float(row.get("sector_relative_strength") or row.get("relative_strength_sector")) is not None:
            relative = to_float(row.get("sector_relative_strength") or row.get("relative_strength_sector")) or 0.0
            long_score += max(0.0, relative)
            short_score += max(0.0, -relative)
    if family == "fundamental_revision":
        growth = to_float(row.get("earnings_growth"))
        if growth is not None:
            if growth > 0:
                long_score += min(2.0, growth * 4)
            elif growth < 0:
                short_score += min(2.0, abs(growth) * 4)
    if family in {"catalyst_momentum", "post_earnings"}:
        long_catalyst = to_float(row.get("long_catalyst_score")) or 0.0
        short_catalyst = to_float(row.get("short_catalyst_score")) or 0.0
        long_score += min(2.0, long_catalyst / 5.0)
        short_score += min(2.0, short_catalyst / 5.0)
    if family == "defensive":
        risk = str(row.get("risk_level") or "Medium")
        flags = row.get("dilution_flags") or []
        flat_score = 7.0 if risk == "High" or flags else 4.0
        return 0.0, 0.0, [f"No-trade protection score={flat_score:.1f}; risk={risk}; flags={len(flags)}."]

    if rsi is not None:
        if 45 <= rsi <= 67:
            long_score += 0.5
        if rsi >= 70:
            short_score += 0.8
        if rsi <= 35:
            long_score += 0.8
    if pos is not None:
        if pos >= 75:
            long_score += 0.3
            short_score += 0.4
        elif pos <= 30:
            long_score += 0.5
            short_score += 0.2

    return min(10.0, max(0.0, long_score)), min(10.0, max(0.0, short_score)), reasons


def evaluate_perspective(
    playbook: dict[str, Any],
    assignment: dict[str, Any],
    row: dict[str, Any],
    agent_name: str,
) -> dict[str, Any]:
    state = infer_market_state(row)
    missing = missing_required_data(playbook, row)
    long_score, short_score, reasons = directional_scores(playbook, row, state)
    sides = {str(item) for item in playbook.get("sides", [])}
    if "long" not in sides and "both" not in sides:
        long_score = 0.0
    if "short" not in sides and "both" not in sides:
        short_score = 0.0
    if "flat" in sides:
        preferred_side = "flat"
        match_score = 7.0 if reasons and "score=7.0" in reasons[0] else 4.0
    elif long_score >= short_score:
        preferred_side, match_score = "long", long_score
    else:
        preferred_side, match_score = "short", short_score

    preferred_regimes = {str(item) for item in playbook.get("route_hints", {}).get("preferred_regimes", [])}
    if preferred_regimes and preferred_regimes.intersection(state["regimes"]):
        match_score = min(10.0, match_score + 0.75)
        reasons.append("Current inferred regime matches the playbook routing hints.")
    preferred_timings = {str(item) for item in playbook.get("route_hints", {}).get("preferred_timings", [])}
    if preferred_timings and state["technical_timing"] in preferred_timings:
        match_score = min(10.0, match_score + 0.75)
        reasons.append("Current technical timing matches the playbook profile.")

    blockers = data_quality_blockers(playbook, row, preferred_side)
    readiness = "not_applicable"
    if blockers:
        readiness = "blocked"
    elif missing:
        readiness = "needs_data"
    elif match_score >= 6.5:
        readiness = "strong_match"
    elif match_score >= 4.5:
        readiness = "watch_match"

    status = str(assignment.get("status") or "research")
    specialization_weight = to_float(assignment.get("weight")) or 1.0
    agent_priority_score = min(10.0, max(0.0, match_score * specialization_weight))
    return {
        "router_schema": ROUTER_SCHEMA,
        "ticker": str(row.get("ticker") or row.get("symbol") or "").upper(),
        "agent_name": agent_name,
        "playbook_id": playbook["playbook_id"],
        "playbook_version": playbook["version"],
        "family": playbook["family"],
        "portfolio_status": status,
        "specialization_weight": specialization_weight,
        "preferred_side": preferred_side,
        "long_view_score": round(long_score, 2),
        "short_view_score": round(short_score, 2),
        "match_score": round(match_score, 2),
        "agent_priority_score": round(agent_priority_score, 2),
        "readiness": readiness,
        "execution_authority": False,
        "execution_note": "Perspective only; active specialized evaluator plus Koulis/Thymios gates remain required.",
        "market_state": state,
        "missing_required_data": missing,
        "data_quality_blockers": blockers,
        "short_gates": list(playbook.get("short_gates", [])),
        "unresolved_short_gates": unresolved_short_gates(playbook, row, preferred_side),
        "rationale": reasons[:6],
        "entry_confirmation": playbook["entry_confirmation"],
        "invalidators": playbook["invalidators"],
        "exit_logic": playbook["exit_logic"],
        "holding_horizon": playbook["holding_horizon"],
        "evaluation_metrics": playbook["evaluation_metrics"],
    }


def evaluate_portfolio(
    catalog: dict[str, Any],
    portfolio: list[dict[str, Any]],
    rows: list[dict[str, Any]],
    agent_name: str,
) -> list[dict[str, Any]]:
    validate_portfolio(catalog, portfolio)
    known = catalog_by_id(catalog)
    perspectives: list[dict[str, Any]] = []
    for row in rows:
        for assignment in portfolio:
            if str(assignment.get("status")) in {"paused", "retired"}:
                continue
            playbook = known[str(assignment["playbook_id"])]
            perspectives.append(evaluate_perspective(playbook, assignment, row, agent_name))
    return perspectives


def attach_alternative_perspectives(
    executable_rows: list[dict[str, Any]],
    perspectives: list[dict[str, Any]],
    limit: int = 6,
) -> None:
    by_ticker: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for perspective in perspectives:
        by_ticker[str(perspective.get("ticker") or "")].append(perspective)
    for row in executable_rows:
        ticker = str(row.get("ticker") or "")
        direction = str(row.get("trade_direction") or "flat")
        ranked = sorted(
            by_ticker.get(ticker, []),
            key=lambda item: (
                item.get("preferred_side") != direction,
                item.get("agent_priority_score", item.get("match_score", 0)),
            ),
            reverse=True,
        )
        row["alternative_perspectives"] = [
            {
                "playbook_id": item["playbook_id"],
                "status": item["portfolio_status"],
                "side": item["preferred_side"],
                "match_score": item["match_score"],
                "agent_priority_score": item.get("agent_priority_score"),
                "readiness": item["readiness"],
                "conflicts_with_primary": item["preferred_side"] not in {direction, "flat"},
                "missing_required_data": item["missing_required_data"],
            }
            for item in ranked[:limit]
        ]
        row["playbook_breadth"] = {
            "perspectives_reviewed": len(by_ticker.get(ticker, [])),
            "long_perspectives": sum(item.get("preferred_side") == "long" for item in by_ticker.get(ticker, [])),
            "short_perspectives": sum(item.get("preferred_side") == "short" for item in by_ticker.get(ticker, [])),
            "flat_perspectives": sum(item.get("preferred_side") == "flat" for item in by_ticker.get(ticker, [])),
        }


def framework_summary(
    catalog: dict[str, Any], portfolio: list[dict[str, Any]], perspectives: list[dict[str, Any]]
) -> dict[str, Any]:
    status_counts = Counter(str(item.get("status") or "research") for item in portfolio)
    side_counts = Counter(str(item.get("preferred_side") or "flat") for item in perspectives)
    readiness_counts = Counter(str(item.get("readiness") or "unknown") for item in perspectives)
    return {
        "schema": ROUTER_SCHEMA,
        "catalog_schema": catalog.get("schema"),
        "catalog_version": catalog.get("version"),
        "catalog_playbook_count": len(as_list(catalog.get("playbooks"))),
        "portfolio_playbook_count": len(portfolio),
        "portfolio_status_counts": dict(status_counts),
        "perspective_count": len(perspectives),
        "perspective_side_counts": dict(side_counts),
        "readiness_counts": dict(readiness_counts),
        "execution_authority": False,
    }
