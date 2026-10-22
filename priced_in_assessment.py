from __future__ import annotations

import hashlib
import json
import math
from datetime import datetime, timezone
from typing import Any


SCHEMA = "ecosystem.priced_in_assessment.v1"
ASSESSED_STATES = {"underpricing_candidate", "balanced", "overpricing_candidate"}


def _number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _parse_timestamp(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _contribution(metric: str, value: float) -> float:
    """Return a transparent expectations-burden contribution.

    Positive values mean the security price requires more optimistic fundamental
    delivery. Negative values mean the observed valuation is less demanding.
    This is a triage heuristic, not a fair-value model or price target.
    """

    if metric == "forward_pe":
        if value >= 40:
            return 25.0
        if value >= 25:
            return 15.0
        if value >= 15:
            return 5.0
        if value <= 10:
            return -20.0
        return -10.0
    if metric == "trailing_pe":
        if value >= 50:
            return 12.0
        if value >= 30:
            return 8.0
        if value <= 10:
            return -10.0
        if value <= 15:
            return -5.0
        return 0.0
    if metric == "price_to_sales":
        if value >= 12:
            return 20.0
        if value >= 6:
            return 12.0
        if value <= 2:
            return -12.0
        if value <= 4:
            return -5.0
        return 3.0
    if metric == "price_to_book":
        if value >= 6:
            return 10.0
        if value >= 4:
            return 5.0
        if value <= 1.5:
            return -6.0
        return 0.0
    if metric == "peg_ratio":
        if value >= 2:
            return 10.0
        if 0 < value <= 1:
            return -8.0
        return 0.0
    if metric == "revenue_growth":
        if value >= 1:
            return -15.0
        if value >= 0.25:
            return -10.0
        if value >= 0.10:
            return -5.0
        if value < 0:
            return 10.0
        return 2.0
    if metric == "earnings_growth":
        if value >= 0.30:
            return -10.0
        if value >= 0.10:
            return -5.0
        if value < 0:
            return 10.0
        return 1.0
    if metric == "profit_margin":
        if value >= 0.20:
            return -6.0
        if value < 0:
            return 10.0
        return 0.0
    if metric == "operating_margin":
        if value >= 0.20:
            return -4.0
        if value < 0:
            return 7.0
        return 0.0
    if metric == "roe":
        if value >= 0.20:
            return -4.0
        if value < 0:
            return 6.0
    return 0.0


def _scenarios(row: dict[str, Any]) -> dict[str, Any]:
    revenue_growth = _number(row.get("revenue_growth"))
    operating_margin = _number(row.get("operating_margin"))
    base_growth = revenue_growth if revenue_growth is not None else 0.10
    base_margin = operating_margin if operating_margin is not None else 0.10
    return {
        "basis": "analyst_sensitivity_not_price_target",
        "bear": {
            "revenue_growth": round(min(0.0, base_growth * 0.25), 4),
            "operating_margin": round(base_margin - 0.05, 4),
            "interpretation": "Growth and margin delivery fall below the observed fundamental run-rate.",
        },
        "base": {
            "revenue_growth": round(base_growth, 4),
            "operating_margin": round(base_margin, 4),
            "interpretation": "The company broadly sustains the observed run-rate.",
        },
        "bull": {
            "revenue_growth": round(max(0.15, base_growth * 1.25), 4),
            "operating_margin": round(base_margin + 0.05, 4),
            "interpretation": "Growth and margins exceed the observed run-rate.",
        },
    }


def assess(
    row: dict[str, Any],
    *,
    run_id: str | None = None,
    run_timestamp: str | None = None,
    max_market_data_age_days: float = 7.0,
) -> dict[str, Any]:
    """Classify the expectations burden using only fundamental/valuation inputs."""

    source_base = row.get("source_base") if isinstance(row.get("source_base"), dict) else {}
    source_as_of = str(source_base.get("snapshot_time") or "").strip()
    source_id = str(source_base.get("primary_data_feed") or "provider-routed fundamental data").strip()
    reference_time = _parse_timestamp(run_timestamp) or datetime.now(timezone.utc)
    source_time = _parse_timestamp(source_as_of)
    current_price = _number(row.get("share_price"))
    valuation_names = ("forward_pe", "trailing_pe", "price_to_sales", "price_to_book", "peg_ratio")
    fundamental_names = (
        "revenue_growth", "earnings_growth", "profit_margin", "operating_margin", "roe"
    )
    valuation = {name: _number(row.get(name)) for name in valuation_names}
    fundamentals = {name: _number(row.get(name)) for name in fundamental_names}
    valuation = {key: value for key, value in valuation.items() if value is not None and value > 0}
    fundamentals = {key: value for key, value in fundamentals.items() if value is not None}
    failures: list[str] = []
    reason_code = None
    if current_price is None or current_price <= 0:
        failures.append("current_price_missing")
        reason_code = "insufficient_source_detail"
    if source_time is None:
        failures.append("market_price_timestamp_missing")
        reason_code = "stale"
    elif (reference_time - source_time).total_seconds() / 86400.0 > max_market_data_age_days:
        failures.append("market_price_is_stale")
        reason_code = "stale"
    if len(valuation) < 2:
        failures.append("insufficient_valuation_observations")
        reason_code = reason_code or "insufficient_source_detail"
    if len(fundamentals) < 2:
        failures.append("insufficient_fundamental_expectation_inputs")
        reason_code = reason_code or "insufficient_source_detail"

    attempt = {
        "provider": "petros_analysis",
        "status": "assessed" if not failures else "not_assessed",
        "source_as_of": source_as_of or None,
        "reason_code": reason_code,
    }
    base = {
        "schema": SCHEMA,
        "run_id": run_id,
        "assessed_at": run_timestamp or reference_time.isoformat(),
        "source_as_of": source_as_of or None,
        "source_id": source_id,
        "current_price": current_price,
        "method": "fundamental_expectations_burden_v1",
        "assessment_grade": "screen_grade",
        "execution_use": "paper_only",
        "fair_value_model": False,
        "price_target_produced": False,
        "attempts": [attempt],
        "failures": failures,
        "valuation_inputs": valuation,
        "fundamental_inputs": fundamentals,
    }
    if failures:
        result = {
            **base,
            "assessment_status": "not_assessed",
            "priced_in_status": "not_assessed",
            "confidence": "Low",
            "reason_code": reason_code,
            "rationale": [
                "The expectations burden was not classified because required evidence or freshness was insufficient."
            ],
            "scenario_expectations": _scenarios(row),
        }
        result["evidence_sha256"] = _digest(
            {key: result[key] for key in ("source_as_of", "source_id", "current_price", "valuation_inputs", "fundamental_inputs")}
        )
        return result

    contributions = {
        **{name: _contribution(name, value) for name, value in valuation.items()},
        **{name: _contribution(name, value) for name, value in fundamentals.items()},
    }
    cash = _number(row.get("cash"))
    debt = _number(row.get("debt"))
    if cash is not None and debt is not None:
        contributions["balance_sheet"] = -4.0 if cash > debt else (6.0 if debt > max(cash * 2.0, 1.0) else 0.0)
    catalyst_direction = str(row.get("catalyst_direction") or "unknown").strip().lower()
    contributions["catalyst_direction"] = {"positive": -3.0, "negative": 3.0}.get(catalyst_direction, 0.0)
    burden = max(0.0, min(100.0, 50.0 + sum(contributions.values())))
    cheap_signals = sum(
        (
            valuation.get("forward_pe", math.inf) <= 15.0,
            valuation.get("trailing_pe", math.inf) <= 15.0,
            valuation.get("price_to_sales", math.inf) <= 4.0,
            valuation.get("price_to_book", math.inf) <= 1.5,
            0 < valuation.get("peg_ratio", math.inf) <= 1.0,
        )
    )
    expensive_signals = sum(
        (
            valuation.get("forward_pe", -math.inf) >= 35.0,
            valuation.get("trailing_pe", -math.inf) >= 40.0,
            valuation.get("price_to_sales", -math.inf) >= 10.0,
            valuation.get("price_to_book", -math.inf) >= 6.0,
            valuation.get("peg_ratio", -math.inf) >= 2.0,
        )
    )
    quality_support = bool(
        fundamentals.get("revenue_growth", 0.0) > 0.0
        and fundamentals.get("profit_margin", fundamentals.get("operating_margin", -1.0)) > 0.0
    )
    if burden <= 35.0 and cheap_signals >= 2 and quality_support and catalyst_direction != "negative":
        status = "underpricing_candidate"
    elif burden >= 65.0 and expensive_signals >= 2:
        status = "overpricing_candidate"
    else:
        status = "balanced"
    input_count = len(valuation) + len(fundamentals)
    confidence = "Medium" if input_count >= 5 and len(valuation) >= 2 else "Low"
    evidence = [
        {
            "field": "share_price",
            "value": current_price,
            "source_id": source_id,
            "source_as_of": source_as_of,
        }
    ]
    evidence.extend(
        {
            "field": field,
            "value": value,
            "source_id": source_id,
            "source_as_of": source_as_of,
        }
        for field, value in {**valuation, **fundamentals}.items()
    )
    result = {
        **base,
        "assessment_status": "assessed",
        "priced_in_status": status,
        "confidence": confidence,
        "reason_code": None,
        "expectations_burden_score": round(burden, 2),
        "expectations_burden_band": "low" if burden <= 35 else ("high" if burden >= 65 else "balanced"),
        "cheap_signal_count": cheap_signals,
        "expensive_signal_count": expensive_signals,
        "quality_support": quality_support,
        "contributions": {key: round(value, 2) for key, value in contributions.items()},
        "evidence": evidence,
        "scenario_expectations": _scenarios(row),
        "rationale": [
            f"Current price {current_price:.4g} was anchored to dated provider evidence.",
            f"Expectations burden scored {burden:.1f}/100 from {len(valuation)} valuation and {len(fundamentals)} fundamental observations.",
            "The result is a valuation-expectations triage classification, not a fair-value estimate or price target.",
        ],
    }
    result["evidence_sha256"] = _digest(evidence)
    return result


def is_evidence_backed(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    return bool(
        value.get("schema") == SCHEMA
        and value.get("assessment_status") == "assessed"
        and value.get("priced_in_status") in ASSESSED_STATES
        and value.get("source_as_of")
        and value.get("current_price")
        and value.get("evidence")
        and value.get("evidence_sha256") == _digest(value.get("evidence"))
    )
