"""Deterministic macro regime classifier from point-in-time FRED observations."""
from __future__ import annotations

from typing import Any

SECTOR_SENSITIVITIES = {
    "financials": {"expansion": 1, "contraction_risk": -1, "rising_rates": 1},
    "technology": {"expansion": 1, "contraction_risk": -1, "rising_rates": -1},
    "real_estate": {"expansion": 0, "contraction_risk": -1, "rising_rates": -1},
    "utilities": {"expansion": 0, "contraction_risk": 1, "rising_rates": -1},
    "consumer_discretionary": {"expansion": 1, "contraction_risk": -1, "rising_rates": -1},
    "consumer_staples": {"expansion": 0, "contraction_risk": 1, "rising_rates": 0},
    "industrials": {"expansion": 1, "contraction_risk": -1, "rising_rates": 0},
    "healthcare": {"expansion": 0, "contraction_risk": 1, "rising_rates": 0},
    "energy": {"expansion": 1, "contraction_risk": -1, "rising_rates": 0},
}


def sector_impacts(regime: str, policy_rate_direction: str = "unknown") -> dict[str, Any]:
    impacts = {}
    for sector, rules in SECTOR_SENSITIVITIES.items():
        score = rules.get(regime, 0)
        if policy_rate_direction == "rising":
            score += rules.get("rising_rates", 0)
        impacts[sector] = {"score": score, "stance": "tailwind" if score > 0 else "headwind" if score < 0 else "neutral"}
    return impacts


def _values(series: dict[str, Any], key: str) -> list[float]:
    row = series.get(key) if isinstance(series, dict) else None
    if not isinstance(row, dict):
        return []
    observations = row.get("observations") or []
    values = []
    for item in observations:
        try:
            values.append(float(item["value"]))
        except (KeyError, TypeError, ValueError):
            continue
    if not values:
        try:
            values = [float(row["value"])]
        except (KeyError, TypeError, ValueError):
            pass
    return values


def classify(macro_context: dict[str, Any]) -> dict[str, Any]:
    series = macro_context.get("series") if isinstance(macro_context, dict) else {}
    signals: dict[str, Any] = {}
    score = 0
    available = 0
    curve = _values(series, "T10Y2Y")
    if curve:
        signals["yield_curve"] = "inverted" if curve[0] < 0 else "positive"
        score += -1 if curve[0] < 0 else 1
        available += 1
    unemployment = _values(series, "UNRATE")
    if len(unemployment) >= 2:
        change = unemployment[0] - unemployment[-1]
        signals["unemployment_trend"] = "deteriorating" if change >= 0.3 else "stable_or_improving"
        score += -1 if change >= 0.3 else 1
        available += 1
    industrial = _values(series, "INDPRO")
    if len(industrial) >= 2:
        change = industrial[0] / industrial[-1] - 1.0 if industrial[-1] else 0.0
        signals["industrial_production"] = "contracting" if change < 0 else "expanding"
        score += -1 if change < 0 else 1
        available += 1
    credit = _values(series, "BAA10Y")
    if credit:
        signals["credit_stress"] = "high" if credit[0] >= 3.0 else "normal"
        score += -1 if credit[0] >= 3.0 else 1
        available += 1
    inflation = _values(series, "CPIAUCSL")
    if len(inflation) >= 2 and inflation[-1]:
        inflation_change = inflation[0] / inflation[-1] - 1.0
        signals["inflation_direction"] = "rising" if inflation_change > 0.01 else "stable_or_falling"
        available += 1
    policy = _values(series, "FEDFUNDS")
    policy_direction = "unknown"
    if len(policy) >= 2:
        policy_direction = "rising" if policy[0] > policy[-1] else "falling_or_flat"
    if available < 3:
        regime, confidence = "insufficient_data", "Low"
    elif score >= 2:
        regime, confidence = "expansion", "Medium"
    elif score <= -2:
        regime, confidence = "contraction_risk", "Medium"
    else:
        regime, confidence = "transition", "Low"
    return {"schema": "ecosystem.macro_regime.v1", "regime": regime,
            "confidence": confidence, "score": score, "available_signals": available,
            "signals": signals, "sector_impacts": sector_impacts(regime, policy_direction),
            "research_only": True}
