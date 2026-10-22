from __future__ import annotations

from typing import Any

import market_intel_db


CATEGORY_VALUES = {
    "risk_level": ["Low", "Medium", "High"],
    "giannis_tier": [
        "Core Watchlist",
        "Selective Watchlist",
        "Speculative Monitor",
        "Complex Security Risk",
        "Avoid For Now",
    ],
    "catalyst_bucket": ["Contract / Partnership", "Pre-Earnings", "UNKNOWN"],
    "catalyst_category_id": [
        "earnings_guidance",
        "commercial_customer",
        "product_technology",
        "clinical_healthcare",
        "regulatory_legal",
        "financing_liquidity",
        "dilution_capital_structure",
        "m_and_a_strategic",
        "listing_compliance",
        "governance_management",
        "investor_events",
        "technical_market_structure",
        "macro_sector",
        "unknown",
    ],
    "catalyst_direction": ["positive", "negative", "mixed", "unknown"],
    "catalyst_trade_bias": [
        "catalyst_only_long",
        "catalyst_only_short",
        "catalyst_only_balanced_review",
        "catalyst_only_neutral",
    ],
    "instrument_type": ["common", "warrant", "unit", "right", "preferred", "fund_or_trust"],
    "technical_timing": [
        "near_lows_reversal",
        "early_accumulation",
        "pre_run_watch",
        "pullback_watch",
        "neutral",
        "already_extended",
    ],
    "smart_money_signal": ["bullish", "mixed", "neutral", "bearish", "unavailable"],
}

FLAG_VALUES = [
    "high_debt_to_equity",
    "weak_liquidity_current_ratio",
    "negative_operating_cashflow",
    "negative_free_cashflow",
    "sub_1usd_price_instability_risk",
    "warrant_structure",
    "unit_structure",
    "rights_structure",
    "fund_or_trust_structure",
]

NUMERIC_FIELDS = [
    "giannis_score",
    "share_price",
    "rank",
    "rsi_14",
    "volume_ratio_20",
    "volatility_20d_pct",
    "return_20d_pct",
    "return_60d_pct",
    "price_position_20d_pct",
    "price_position_60d_pct",
    "distance_from_20d_low_pct",
    "distance_from_60d_low_pct",
    "macd_histogram",
    "atr_14_pct",
    "technical_timing_score",
    "smart_money_score",
    "institution_ownership",
    "short_percent_of_float",
    "open_market_buy_count",
    "open_market_sell_count",
    "darkpool_activity_ratio",
    "feedback_count",
    "yankos_signal_count",
    "yankos_best_signal_score",
    "yankos_support_bounce_candidate",
    "yankos_support_bounce_watch",
    "yankos_support_bounce_reject",
    "periklis_signal_count",
    "periklis_best_signal_score",
    "periklis_support_bounce_candidate",
    "periklis_support_bounce_watch",
    "periklis_support_bounce_reject",
    "catalyst_category_weight",
    "catalyst_attention_score",
    "catalyst_evidence_confidence_score",
    "catalyst_recency_score",
    "catalyst_specificity_score",
    "long_catalyst_score",
    "short_catalyst_score",
]


def to_float(value: Any) -> float | None:
    return market_intel_db.to_float(value)


def clean_key(value: str) -> str:
    return (
        value.lower()
        .replace(" ", "_")
        .replace("/", "_")
        .replace("-", "_")
        .replace("__", "_")
        .strip("_")
    )


def build_features(row: dict[str, Any]) -> dict[str, float]:
    features: dict[str, float] = {}
    for field in NUMERIC_FIELDS:
        value = to_float(row.get(field))
        features[field] = 0.0 if value is None else float(value)

    features["complex_security"] = 1.0 if bool(row.get("complex_security")) else 0.0

    for field, values in CATEGORY_VALUES.items():
        current = str(row.get(field) or "").strip()
        if field == "catalyst_bucket" and not current:
            current = "UNKNOWN"
        for value in values:
            features[f"{field}_{clean_key(value)}"] = 1.0 if current == value else 0.0

    flags = row.get("dilution_flags") or []
    if not isinstance(flags, list):
        flags = [str(flags)]
    flag_set = {str(flag) for flag in flags}
    features["dilution_flag_count"] = float(len(flag_set))
    for flag in FLAG_VALUES:
        features[f"flag_{flag}"] = 1.0 if flag in flag_set else 0.0

    return features


def feature_names_from_rows(rows: list[dict[str, Any]]) -> list[str]:
    names: set[str] = set()
    for row in rows:
        names.update(build_features(row).keys())
    return sorted(names)


def vectorize(features: dict[str, float], feature_names: list[str]) -> list[float]:
    return [float(features.get(name, 0.0)) for name in feature_names]
