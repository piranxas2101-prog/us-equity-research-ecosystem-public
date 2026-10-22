"""Trader confidence from each playbook's own track record, in shadow (owner's decision 2026-10-06).

The old label (High/Medium/Low) was the company's risk level: a candidate on a low-risk company was "High". It is
now kept under its real name, ``risk_tier``, and the trade plan still sizes with it, so no order size changes.
``confidence`` says ``insufficient_history`` until the owner promotes the shadow value.

The shadow value is the Wilson lower bound of how often the playbook (in the same direction) beat SPY at the
trader's horizon (Yankos 3 sessions, Periklis 5), from matured outcomes only. It is judged only with at least
``MIN_DECISIONS`` decisions from ``MIN_DAYS`` different days; below that it is reported as insufficient, not guessed.
The record is written at the end of a run (``trader_calibration.py``) and read by the traders of the next run.
"""
from __future__ import annotations

from datetime import datetime, timezone
from functools import lru_cache
import json
from pathlib import Path
from typing import Any

import strategy_scorecards as sc

ROOT = Path(__file__).resolve().parent
RECORD_PATH = ROOT / "output" / "playbook_track_record_latest.json"
SCHEMA = "ecosystem.playbook_track_record.v1"
PENDING = "insufficient_history"
AGENT_HORIZON = {"yankos": 3, "periklis": 5}
MIN_DECISIONS = 30
MIN_DAYS = 20
RISK_TIERS = ("High", "Medium", "Low")


def build_track_record(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Per agent and playbook/direction: decisions, days, beat-SPY rate and its Wilson lower bound.

    ``rows`` are benchmark-adjusted decision rows (directional excess over SPY), one per decision and horizon.
    """
    unique: dict[tuple[str, str, tuple], dict[str, Any]] = {}
    for row in rows:  # one decision per agent and playbook (the same signal can be stored more than once)
        unique.setdefault((str(row.get("agent_name")), str(row.get("playbook_name")), sc.decision_key(row)), row)
    rows = list(unique.values())
    cells: dict[str, dict[str, dict[str, Any]]] = {}
    for agent, horizon in AGENT_HORIZON.items():
        groups: dict[str, list[dict[str, Any]]] = {}
        for row in rows:
            if row.get("agent_name") == agent and int(row.get("horizon_days") or 0) == horizon \
                    and row.get("trade_direction") in ("long", "short") and row.get("directional_return_pct") is not None:
                groups.setdefault(f"{row.get('playbook_name')}|{row['trade_direction']}", []).append(row)
        cells[agent] = {}
        for key, bucket in sorted(groups.items()):
            wins = sum(1 for row in bucket if float(row["directional_return_pct"]) > 0)
            days = len({str(row.get("source_run_timestamp") or "")[:10] for row in bucket})
            lower = sc.wilson_lower_bound(wins, len(bucket))
            enough = len(bucket) >= MIN_DECISIONS and days >= MIN_DAYS
            cells[agent][key] = {
                "decisions": len(bucket), "days": days, "wins": wins,
                "beat_benchmark_pct": round(100.0 * wins / len(bucket), 2),
                "wilson_lower_pct": None if lower is None else round(lower, 2),
                "status": "judged" if enough else PENDING,
            }
    return {"schema": SCHEMA, "generated_at": datetime.now(timezone.utc).isoformat(), "benchmark": "SPY",
            "horizons": AGENT_HORIZON, "minimum_decisions": MIN_DECISIONS, "minimum_days": MIN_DAYS,
            "active": False, "cells": cells}


@lru_cache(maxsize=4)
def load_track_record(path: str = str(RECORD_PATH)) -> dict[str, Any]:
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return payload if payload.get("schema") == SCHEMA else {}


def shadow_confidence(agent: str, playbook: str, direction: str, record: dict[str, Any] | None = None) -> dict[str, Any]:
    """The shadow confidence of one signal; never used for a decision or a size."""
    record = load_track_record() if record is None else record
    if direction not in ("long", "short"):
        return {"value": None, "status": "no_direction"}
    cell = ((record.get("cells") or {}).get(agent) or {}).get(f"{playbook}|{direction}")
    if not cell:
        return {"value": None, "status": PENDING, "decisions": 0, "days": 0,
                "horizon_days": AGENT_HORIZON.get(agent), "record_generated_at": record.get("generated_at")}
    judged = cell.get("status") == "judged" and cell.get("wilson_lower_pct") is not None
    return {"value": round(cell["wilson_lower_pct"] / 100.0, 4) if judged else None,
            "status": cell.get("status"), "decisions": cell.get("decisions"), "days": cell.get("days"),
            "beat_benchmark_pct": cell.get("beat_benchmark_pct"), "horizon_days": AGENT_HORIZON.get(agent),
            "record_generated_at": record.get("generated_at")}


def label_fields(agent: str, playbook: str, direction: str, risk_tier: str) -> dict[str, Any]:
    """The confidence-related fields of a trader signal."""
    return {"confidence": PENDING, "risk_tier": risk_tier,
            "confidence_shadow": shadow_confidence(agent, playbook, direction)}


def sizing_label(row: dict[str, Any]) -> str:
    """The label the trade plan sizes with: the risk tier (the old 'confidence' values), so sizes do not change."""
    tier = row.get("risk_tier")
    if tier in RISK_TIERS:
        return str(tier)
    old = row.get("confidence")
    return str(old) if old in RISK_TIERS else "Low"
