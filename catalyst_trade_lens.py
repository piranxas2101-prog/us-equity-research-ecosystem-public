from __future__ import annotations

from typing import Any


def _number(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def advisory_adjustments(row: dict[str, Any], *, max_adjustment: float = 0.35) -> dict[str, Any]:
    """Translate Long/Short catalyst parents into a bounded advisory signal.

    The lens cannot create a direction or bypass strategy/risk thresholds. It only
    changes each independent strategy score by a small, recorded amount.
    """

    long_score = max(0.0, min(10.0, _number(row.get("long_catalyst_score"))))
    short_score = max(0.0, min(10.0, _number(row.get("short_catalyst_score"))))
    evidence = _number(row.get("catalyst_evidence_confidence_score"))
    if evidence > 1.0:
        evidence /= 100.0
    evidence = max(0.0, min(1.0, evidence))
    if evidence == 0.0 and (long_score or short_score):
        evidence = 0.5
    directional_edge = (long_score - short_score) / 10.0
    long_adjustment = max(-max_adjustment, min(max_adjustment, directional_edge * max_adjustment * evidence))
    return {
        "schema": "ecosystem.catalyst_trade_lens.v1",
        "role": "bounded_advisory_not_direction_authority",
        "long_catalyst_score": round(long_score, 3),
        "short_catalyst_score": round(short_score, 3),
        "evidence_multiplier": round(evidence, 3),
        "long_adjustment": round(long_adjustment, 3),
        "short_adjustment": round(-long_adjustment, 3),
        "trade_bias": row.get("catalyst_trade_bias"),
    }
