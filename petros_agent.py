from __future__ import annotations
from instrument_type_rules import infer_instrument_type, normalize_instrument_type

import argparse
import csv
import json
import math
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import market_intel_db
import deterministic_valuation
import petros_features
import priced_in_assessment
import technical_setup
import research_readiness
import unknown_data
from run_context import attach_run_context, require_compatible_runs
from common import load_json


VERDICTS = {"candidate", "manual_review", "watch", "reject"}
FEEDBACK_VERDICTS = {
    "agree",
    "disagree",
    "interested",
    "watch",
    "reject",
    "needs_filing_review",
    "needs_news_check",
    "needs_technical_review",
}
POSITIVE_FEEDBACK = {"agree", "interested", "watch"}
NEGATIVE_FEEDBACK = {"disagree", "reject"}
REVIEW_FEEDBACK = {"needs_filing_review", "needs_news_check", "needs_technical_review"}
DEFAULT_MODEL_PATH = Path("models/petros_model_latest.json")


def as_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def to_float(value: Any) -> float | None:
    return market_intel_db.to_float(value)


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def as_list(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [row for row in value if isinstance(row, dict)]


def classify_instrument(row: dict[str, Any]) -> str:
    existing = normalize_instrument_type(row.get("instrument_type"))
    if existing:
        return existing
    return infer_instrument_type(row.get("ticker"), row.get("company"))[0]


def load_safe_model(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"[PETROS] ignoring model {path}: {exc}")
        return None
    if not isinstance(payload, dict):
        print(f"[PETROS] ignoring model {path}: root is not an object")
        return None
    if payload.get("model_type") != "petros_logistic_regression_json_v1":
        print(f"[PETROS] ignoring model {path}: unsupported model_type")
        return None
    required = ["feature_names", "scaler_mean", "scaler_scale", "coefficients", "intercept"]
    if any(key not in payload for key in required):
        print(f"[PETROS] ignoring model {path}: missing required fields")
        return None
    return payload


def safe_sigmoid(value: float) -> float:
    if value >= 0:
        return 1.0 / (1.0 + math.exp(-value))
    exp_value = math.exp(value)
    return exp_value / (1.0 + exp_value)


def model_probability(model: dict[str, Any], row: dict[str, Any]) -> float | None:
    try:
        feature_names = [str(name) for name in model["feature_names"]]
        mean = [float(value) for value in model["scaler_mean"]]
        scale = [float(value) if float(value) != 0.0 else 1.0 for value in model["scaler_scale"]]
        coefficients = [float(value) for value in model["coefficients"]]
        intercept = float(model["intercept"])
    except (TypeError, ValueError, KeyError):
        return None
    if not (len(feature_names) == len(mean) == len(scale) == len(coefficients)):
        return None
    features = petros_features.build_features(row)
    values = petros_features.vectorize(features, feature_names)
    scaled = [(value - avg) / factor for value, avg, factor in zip(values, mean, scale)]
    score = intercept + sum(coef * value for coef, value in zip(coefficients, scaled))
    return safe_sigmoid(score)


def score_petros(
    giannis_row: dict[str, Any],
    babis_row: dict[str, Any] | None,
    feedback_rows: list[dict[str, Any]] | None = None,
    model: dict[str, Any] | None = None,
) -> dict[str, Any]:
    babis_row = babis_row or {}
    feedback_rows = feedback_rows or []
    ticker = str(giannis_row.get("ticker") or "").upper().strip()
    giannis_score = to_float(giannis_row.get("score_final")) or 0.0
    risk_level = str(giannis_row.get("risk_level") or "Medium")
    tier = str(giannis_row.get("giannis_tier") or "")
    catalyst = str(giannis_row.get("catalyst_bucket") or "")
    catalyst_category_id = str(giannis_row.get("catalyst_category_id") or "").strip()
    catalyst_direction = str(giannis_row.get("catalyst_direction") or "unknown").strip().lower()
    catalyst_attention_score = to_float(giannis_row.get("catalyst_attention_score"))
    catalyst_classification_confidence = str(
        giannis_row.get("catalyst_classification_confidence") or "low"
    ).strip().lower()
    instrument_type = classify_instrument({**babis_row, **giannis_row})
    complex_security = instrument_type != "common"
    flags = giannis_row.get("dilution_flags") or []
    if not isinstance(flags, list):
        flags = [str(flags)]
    unknown_fields = giannis_row.get("unknown_fields") or []
    if not isinstance(unknown_fields, list):
        unknown_fields = [str(unknown_fields)]

    rationale: list[str] = []
    score = giannis_score * 0.65

    if tier == "Core Watchlist":
        score += 1.0
        rationale.append("Giannis tier is Core.")
    elif tier == "Selective Watchlist":
        score += 0.4
        rationale.append("Giannis tier is Selective.")
    elif tier == "Complex Security Risk":
        score -= 1.0
        rationale.append("Complex security tier requires special handling.")

    if catalyst_category_id and catalyst_attention_score is not None:
        attention = clamp(catalyst_attention_score / 100.0, 0.0, 1.0)
        confidence_multiplier = {
            "high": 1.0,
            "medium": 0.75,
            "low": 0.45,
        }.get(catalyst_classification_confidence, 0.45)
        if catalyst_direction == "positive":
            adjustment = 0.55 * attention * confidence_multiplier
            score += adjustment
            rationale.append(f"Positive source-backed catalyst (+{adjustment:.2f}).")
        elif catalyst_direction == "negative":
            adjustment = 0.8 * attention * confidence_multiplier
            score -= adjustment
            rationale.append(f"Material adverse catalyst (-{adjustment:.2f}).")
        elif catalyst_direction == "mixed":
            adjustment = 0.15 * attention * confidence_multiplier
            score += adjustment
            rationale.append(f"Material catalyst has mixed direction (+{adjustment:.2f} attention only).")
        else:
            score -= 0.35
            rationale.append("Catalyst direction is not established.")
        if catalyst_category_id == "unknown":
            score -= 0.25
            rationale.append("Catalyst category remains unclassified.")
    elif catalyst == "Contract / Partnership":
        score += 0.45
        rationale.append("Historical artifact: material filing / partnership catalyst.")
    elif catalyst == "Pre-Earnings":
        score += 0.25
        rationale.append("Historical artifact: pre-earnings / primary filing catalyst.")
    elif catalyst in {"UNKNOWN", "", "Unclassified / Insufficient Evidence"}:
        score -= 0.4
        rationale.append("Catalyst quality is unclear.")

    if risk_level == "High":
        score -= 2.0
        rationale.append("High risk level.")
    elif risk_level == "Medium":
        score -= 0.7
        rationale.append("Medium risk level.")

    if complex_security:
        score -= 1.2
        rationale.append(f"Instrument is {instrument_type}; keep visible as risk.")

    fresh_primary_data = bool(giannis_row.get("fresh_primary_data", babis_row.get("fresh_primary_data", True)))
    sec_filing_age_days = to_float(giannis_row.get("sec_filing_age_days") or babis_row.get("sec_filing_age_days"))
    if not research_readiness.fundamentals_current(giannis_row, fresh_primary_data):
        score -= 1.0
        rationale.append("Last periodic SEC report is out of date (or no filing inside 30 days when unknown); stale-data penalty.")

    flag_penalties = {
        "high_debt_to_equity": 0.7,
        "weak_liquidity_current_ratio": 0.5,
        "negative_operating_cashflow": 0.55,
        "negative_free_cashflow": 0.55,
        "sub_1usd_price_instability_risk": 0.8,
        "warrant_structure": 1.0,
        "unit_structure": 0.8,
        "rights_structure": 0.8,
        "fund_or_trust_structure": 0.7,
    }
    for flag in flags:
        penalty = flag_penalties.get(str(flag), 0.2)
        score -= penalty
        rationale.append(f"Penalty for {flag}.")

    net_cash = to_float(giannis_row.get("net_cash"))
    fcf = to_float(giannis_row.get("fcf_proxy"))
    ocf = to_float(giannis_row.get("ocf_proxy"))
    smart_money_signal = str(giannis_row.get("smart_money_signal") or babis_row.get("smart_money_signal") or "neutral")
    smart_money_score = to_float(giannis_row.get("smart_money_score") or babis_row.get("smart_money_score")) or 0.0
    institution_ownership = to_float(giannis_row.get("institution_ownership") or babis_row.get("institution_ownership"))
    short_percent_of_float = to_float(giannis_row.get("short_percent_of_float") or babis_row.get("short_percent_of_float"))
    open_market_buy_count = int(to_float(giannis_row.get("open_market_buy_count") or babis_row.get("open_market_buy_count")) or 0)
    open_market_sell_count = int(to_float(giannis_row.get("open_market_sell_count") or babis_row.get("open_market_sell_count")) or 0)
    darkpool_activity_ratio = to_float(giannis_row.get("darkpool_activity_ratio") or babis_row.get("darkpool_activity_ratio"))
    if net_cash is not None and net_cash > 0:
        score += 0.6
        rationale.append("Positive net cash.")
    if fcf is not None and fcf > 0:
        score += 0.35
        rationale.append("Positive FCF proxy.")
    if ocf is not None and ocf > 0:
        score += 0.35
        rationale.append("Positive OCF proxy.")
    if smart_money_signal == "bullish":
        score += 0.45
        rationale.append("OpenBB smart-money context is supportive.")
    elif smart_money_signal == "bearish":
        score -= 0.35
        rationale.append("OpenBB smart-money context is cautious.")
    elif smart_money_signal == "mixed":
        score += 0.1
        rationale.append("OpenBB smart-money context is mixed but worth tracking.")
    score += max(-0.25, min(0.45, smart_money_score * 0.35))
    if institution_ownership is not None and institution_ownership >= 0.5:
        score += 0.15
        rationale.append("Institutional ownership base is healthy.")
    if open_market_buy_count > open_market_sell_count and open_market_buy_count > 0:
        score += 0.2
        rationale.append("Recent insider open-market buying beats selling.")
    elif open_market_sell_count > open_market_buy_count and open_market_sell_count > 0:
        score -= 0.15
        rationale.append("Recent insider open-market selling beats buying.")
    if short_percent_of_float is not None and short_percent_of_float >= 0.08:
        score += 0.1
        rationale.append("Short-interest crowding can amplify upside if thesis is right.")
    if darkpool_activity_ratio is not None and darkpool_activity_ratio >= 1.2:
        rationale.append("OTC/dark-pool activity is above recent average.")

    rsi = to_float(babis_row.get("rsi_14"))
    volume_ratio = to_float(babis_row.get("volume_ratio_20"))
    volatility = to_float(babis_row.get("volatility_20d_pct"))
    return_20d = to_float(babis_row.get("return_20d_pct"))
    return_60d = to_float(babis_row.get("return_60d_pct"))
    macd_histogram = to_float(babis_row.get("macd_histogram"))
    atr_14_pct = to_float(babis_row.get("atr_14_pct"))
    if rsi is not None:
        if rsi > 90:
            score -= 0.35
            rationale.append("RSI is very extended.")
        elif 35 <= rsi <= 75:
            score += 0.15
            rationale.append("RSI is not extreme.")
    if volume_ratio is not None and volume_ratio > 1.2:
        score += 0.2
        rationale.append("Volume is above 20-day average.")
    if volatility is not None and volatility > 100:
        score -= 0.35
        rationale.append("Very high 20-day volatility.")

    technical = technical_setup.analyze_technical_setup({**babis_row, **giannis_row})
    technical_timing = str(technical.get("technical_timing") or "neutral")
    technical_timing_score = to_float(technical.get("technical_timing_score")) or 0.0
    score += technical_timing_score
    if technical_timing == "near_lows_reversal":
        rationale.append("Near-lows reversal candidate; better timing than chasing highs.")
    elif technical_timing == "early_accumulation":
        rationale.append("Technical timing looks early / accumulation-like.")
    elif technical_timing == "pre_run_watch":
        rationale.append("Technical timing is not extended; pre-run watch.")
    elif technical_timing == "pullback_watch":
        rationale.append("Pullback setup; needs confirmation.")
    elif technical_timing == "already_extended":
        rationale.append("Already extended technically; avoid chasing.")

    score -= min(0.75, 0.2 * len(unknown_fields))
    if unknown_fields:
        rationale.append("Unknown fields reduce confidence.")

    signals_by_agent: dict[str, list[dict[str, Any]]] = {}
    agent_signal_adjustments: dict[str, float] = {}
    for agent in ("yankos", "periklis"):
        agent_signals = giannis_row.get(f"{agent}_strategy_signals") or []
        if not isinstance(agent_signals, list):
            agent_signals = []
        signals_by_agent[agent] = agent_signals
        if not agent_signals:
            continue
        best_signal = max(agent_signals, key=lambda item: to_float(item.get("signal_score")) or 0.0)
        best_score = to_float(best_signal.get("signal_score")) or 0.0
        verdict = str(best_signal.get("signal_verdict") or "")
        if verdict == "candidate":
            adjustment = 0.55 if best_score >= 7.25 else 0.35
        elif verdict == "watch":
            adjustment = 0.15
        elif verdict == "reject":
            adjustment = -0.25
        else:
            adjustment = 0.0
        agent_signal_adjustments[agent] = adjustment
        rationale.append(
            f"{agent.title()} independent strategy view={verdict or 'unknown'} "
            f"(score={best_score:.2f}, advisory={adjustment:+.2f})."
        )

    # Strategy agents are correlated because they share upstream data. Their views are
    # averaged into one bounded advisory vote instead of being added independently.
    strategy_consensus_adjustment = 0.0
    if agent_signal_adjustments:
        strategy_consensus_adjustment = clamp(
            sum(agent_signal_adjustments.values()) / len(agent_signal_adjustments), -0.55, 0.55
        )
        score += strategy_consensus_adjustment

    yankos_signals = signals_by_agent["yankos"]
    periklis_signals = signals_by_agent["periklis"]
    yankos_signal_count = len(yankos_signals)
    periklis_signal_count = len(periklis_signals)
    yankos_best_signal_score = max((to_float(item.get("signal_score")) or 0.0 for item in yankos_signals), default=0.0)
    periklis_best_signal_score = max((to_float(item.get("signal_score")) or 0.0 for item in periklis_signals), default=0.0)

    feedback_verdicts = [str(item.get("feedback_verdict") or "").lower() for item in feedback_rows]
    positive_count = sum(1 for item in feedback_verdicts if item in POSITIVE_FEEDBACK)
    negative_count = sum(1 for item in feedback_verdicts if item in NEGATIVE_FEEDBACK)
    review_count = sum(1 for item in feedback_verdicts if item in REVIEW_FEEDBACK)
    if positive_count:
        boost = min(0.9, 0.3 * positive_count)
        score += boost
        rationale.append(f"User feedback supports extra attention (+{round(boost, 2)}).")
    if negative_count:
        penalty = min(1.2, 0.4 * negative_count)
        score -= penalty
        rationale.append(f"User feedback warns against this setup (-{round(penalty, 2)}).")
    if review_count:
        rationale.append("User asked for extra filing/news review.")

    base_result = {
        "ticker": ticker,
        "company": giannis_row.get("company"),
        "rank": giannis_row.get("rank"),
        "share_price": to_float(giannis_row.get("share_price")),
        "giannis_score": giannis_score,
        "giannis_tier": tier,
        "risk_level": risk_level,
        "instrument_type": instrument_type,
        "complex_security": complex_security,
        "catalyst_bucket": catalyst,
        "catalyst_category_id": catalyst_category_id or None,
        "catalyst_direction": catalyst_direction,
        "catalyst_category_weight": to_float(giannis_row.get("catalyst_category_weight")),
        "catalyst_attention_score": catalyst_attention_score,
        "catalyst_priority": giannis_row.get("catalyst_priority"),
        "catalyst_classification_confidence": catalyst_classification_confidence,
        "catalyst_evidence_confidence_score": to_float(giannis_row.get("catalyst_evidence_confidence_score")),
        "catalyst_recency_score": to_float(giannis_row.get("catalyst_recency_score")),
        "catalyst_specificity_score": to_float(giannis_row.get("catalyst_specificity_score")),
        "long_catalyst_score": to_float(giannis_row.get("long_catalyst_score")),
        "short_catalyst_score": to_float(giannis_row.get("short_catalyst_score")),
        "catalyst_trade_bias": giannis_row.get("catalyst_trade_bias"),
        "catalyst_trade_lenses": giannis_row.get("catalyst_trade_lenses", {}),
        "catalyst_taxonomy_version": giannis_row.get("catalyst_taxonomy_version"),
        "catalyst_scoring_status": giannis_row.get("catalyst_scoring_status"),
        "fresh_primary_data": fresh_primary_data,
        "fundamentals_fresh": giannis_row.get("fundamentals_fresh"),
        "periodic_filing_age_days": to_float(giannis_row.get("periodic_filing_age_days")),
        "sec_filing_age_days": sec_filing_age_days,
        "confidence": "High",
        "rationale": rationale,
        "dilution_flags": flags,
        "rsi_14": rsi,
        "volume_ratio_20": volume_ratio,
        "volatility_20d_pct": volatility,
        "return_20d_pct": return_20d,
        "return_60d_pct": return_60d,
        "price_position_20d_pct": technical.get("price_position_20d_pct"),
        "price_position_60d_pct": technical.get("price_position_60d_pct"),
        "distance_from_20d_low_pct": technical.get("distance_from_20d_low_pct"),
        "distance_from_60d_low_pct": technical.get("distance_from_60d_low_pct"),
        "macd_histogram": macd_histogram,
        "atr_14_pct": atr_14_pct,
        "technical_timing": technical_timing,
        "technical_timing_score": technical_timing_score,
        "technical_flags": technical.get("technical_flags", []),
        "smart_money_signal": smart_money_signal,
        "smart_money_score": smart_money_score,
        "institution_ownership": institution_ownership,
        "short_percent_of_float": short_percent_of_float,
        "open_market_buy_count": open_market_buy_count,
        "open_market_sell_count": open_market_sell_count,
        "darkpool_activity_ratio": darkpool_activity_ratio,
        "feedback_count": len(feedback_rows),
        "last_feedback": feedback_rows[0] if feedback_rows else None,
        "yankos_signal_count": yankos_signal_count,
        "yankos_best_signal_score": yankos_best_signal_score,
        "yankos_support_bounce_candidate": 1.0 if any(
            item.get("playbook_name") == "support_bounce_swing" and item.get("signal_verdict") == "candidate"
            for item in yankos_signals
        ) else 0.0,
        "yankos_support_bounce_watch": 1.0 if any(
            item.get("playbook_name") == "support_bounce_swing" and item.get("signal_verdict") == "watch"
            for item in yankos_signals
        ) else 0.0,
        "yankos_support_bounce_reject": 1.0 if any(
            item.get("playbook_name") == "support_bounce_swing" and item.get("signal_verdict") == "reject"
            for item in yankos_signals
        ) else 0.0,
        "yankos_strategy_signals": yankos_signals,
        "periklis_signal_count": periklis_signal_count,
        "periklis_best_signal_score": periklis_best_signal_score,
        "periklis_support_bounce_candidate": 1.0 if any(
            item.get("playbook_name") == "support_bounce_swing" and item.get("signal_verdict") == "candidate"
            for item in periklis_signals
        ) else 0.0,
        "periklis_support_bounce_watch": 1.0 if any(
            item.get("playbook_name") == "support_bounce_swing" and item.get("signal_verdict") == "watch"
            for item in periklis_signals
        ) else 0.0,
        "periklis_support_bounce_reject": 1.0 if any(
            item.get("playbook_name") == "support_bounce_swing" and item.get("signal_verdict") == "reject"
            for item in periklis_signals
        ) else 0.0,
        "periklis_strategy_signals": periklis_signals,
        "agent_signal_adjustments": agent_signal_adjustments,
        "strategy_consensus_adjustment": round(strategy_consensus_adjustment, 3),
    }

    ml_probability = None
    ml_adjustment = 0.0
    if model:
        ml_probability = model_probability(model, base_result)
        training_rows = int(model.get("training_rows") or 0)
        if ml_probability is not None and training_rows >= 20:
            ml_adjustment = round(clamp((ml_probability - 0.5) * 1.4, -0.7, 0.7), 2)
            score += ml_adjustment
            rationale.append(
                f"Petros ML model probability {round(ml_probability * 100, 1)}% "
                f"adjustment {ml_adjustment:+.2f}."
            )

    final_score = round(clamp(score, 0.0, 10.0), 2)
    if final_score >= 8.0 and not complex_security and risk_level != "High":
        verdict = "candidate"
    elif final_score >= 6.25 and risk_level != "High":
        verdict = "manual_review"
    elif final_score >= 4.5:
        verdict = "watch"
    else:
        verdict = "reject"
    if review_count and verdict == "candidate":
        verdict = "manual_review"

    confidence = "High"
    if unknown_fields or risk_level == "Medium" or complex_security:
        confidence = "Medium"
    if risk_level == "High" or len(flags) >= 3:
        confidence = "Low"

    return {
        "ticker": ticker,
        "company": giannis_row.get("company"),
        "rank": giannis_row.get("rank"),
        "share_price": to_float(giannis_row.get("share_price")),
        "giannis_score": giannis_score,
        "giannis_tier": tier,
        "risk_level": risk_level,
        "instrument_type": instrument_type,
        "complex_security": complex_security,
        "catalyst_bucket": catalyst,
        "catalyst_category_id": catalyst_category_id or None,
        "catalyst_direction": catalyst_direction,
        "catalyst_category_weight": to_float(giannis_row.get("catalyst_category_weight")),
        "catalyst_attention_score": catalyst_attention_score,
        "catalyst_priority": giannis_row.get("catalyst_priority"),
        "catalyst_classification_confidence": catalyst_classification_confidence,
        "catalyst_evidence_confidence_score": to_float(giannis_row.get("catalyst_evidence_confidence_score")),
        "catalyst_recency_score": to_float(giannis_row.get("catalyst_recency_score")),
        "catalyst_specificity_score": to_float(giannis_row.get("catalyst_specificity_score")),
        "long_catalyst_score": to_float(giannis_row.get("long_catalyst_score")),
        "short_catalyst_score": to_float(giannis_row.get("short_catalyst_score")),
        "catalyst_trade_bias": giannis_row.get("catalyst_trade_bias"),
        "catalyst_trade_lenses": giannis_row.get("catalyst_trade_lenses", {}),
        "catalyst_taxonomy_version": giannis_row.get("catalyst_taxonomy_version"),
        "catalyst_scoring_status": giannis_row.get("catalyst_scoring_status"),
        "fresh_primary_data": fresh_primary_data,
        "fundamentals_fresh": giannis_row.get("fundamentals_fresh"),
        "periodic_filing_age_days": to_float(giannis_row.get("periodic_filing_age_days")),
        "sec_filing_age_days": sec_filing_age_days,
        "petros_score": final_score,
        "petros_verdict": verdict,
        "confidence": confidence,
        "rationale": rationale[:8],
        "dilution_flags": flags,
        "rsi_14": rsi,
        "volume_ratio_20": volume_ratio,
        "volatility_20d_pct": volatility,
        "return_20d_pct": return_20d,
        "return_60d_pct": return_60d,
        "price_position_20d_pct": technical.get("price_position_20d_pct"),
        "price_position_60d_pct": technical.get("price_position_60d_pct"),
        "distance_from_20d_low_pct": technical.get("distance_from_20d_low_pct"),
        "distance_from_60d_low_pct": technical.get("distance_from_60d_low_pct"),
        "macd_histogram": macd_histogram,
        "atr_14_pct": atr_14_pct,
        "technical_timing": technical_timing,
        "technical_timing_score": technical_timing_score,
        "technical_flags": technical.get("technical_flags", []),
        "smart_money_signal": smart_money_signal,
        "smart_money_score": smart_money_score,
        "institution_ownership": institution_ownership,
        "short_percent_of_float": short_percent_of_float,
        "open_market_buy_count": open_market_buy_count,
        "open_market_sell_count": open_market_sell_count,
        "darkpool_activity_ratio": darkpool_activity_ratio,
        "feedback_count": len(feedback_rows),
        "last_feedback": feedback_rows[0] if feedback_rows else None,
        "yankos_signal_count": yankos_signal_count,
        "yankos_best_signal_score": yankos_best_signal_score,
        "yankos_support_bounce_candidate": 1.0 if any(
            item.get("playbook_name") == "support_bounce_swing" and item.get("signal_verdict") == "candidate"
            for item in yankos_signals
        ) else 0.0,
        "yankos_support_bounce_watch": 1.0 if any(
            item.get("playbook_name") == "support_bounce_swing" and item.get("signal_verdict") == "watch"
            for item in yankos_signals
        ) else 0.0,
        "yankos_support_bounce_reject": 1.0 if any(
            item.get("playbook_name") == "support_bounce_swing" and item.get("signal_verdict") == "reject"
            for item in yankos_signals
        ) else 0.0,
        "yankos_strategy_signals": yankos_signals,
        "periklis_signal_count": periklis_signal_count,
        "periklis_best_signal_score": periklis_best_signal_score,
        "periklis_support_bounce_candidate": 1.0 if any(
            item.get("playbook_name") == "support_bounce_swing" and item.get("signal_verdict") == "candidate"
            for item in periklis_signals
        ) else 0.0,
        "periklis_support_bounce_watch": 1.0 if any(
            item.get("playbook_name") == "support_bounce_swing" and item.get("signal_verdict") == "watch"
            for item in periklis_signals
        ) else 0.0,
        "periklis_support_bounce_reject": 1.0 if any(
            item.get("playbook_name") == "support_bounce_swing" and item.get("signal_verdict") == "reject"
            for item in periklis_signals
        ) else 0.0,
        "periklis_strategy_signals": periklis_signals,
        "agent_signal_adjustments": agent_signal_adjustments,
        "strategy_consensus_adjustment": round(strategy_consensus_adjustment, 3),
        "ml_probability": None if ml_probability is None else round(ml_probability, 4),
        "ml_adjustment": ml_adjustment,
    }


def score_petros_fundamental(
    giannis_row: dict[str, Any],
    babis_row: dict[str, Any] | None,
    feedback_rows: list[dict[str, Any]] | None = None,
    peer_rows: list[dict[str, Any]] | None = None,
    *,
    run_id: str | None = None,
    run_timestamp: str | None = None,
) -> dict[str, Any]:
    """Create a research synthesis without technical or trader-derived inputs."""

    babis_row = babis_row or {}
    feedback_rows = feedback_rows or []
    merged = {**babis_row, **giannis_row}
    ticker = str(giannis_row.get("ticker") or "").upper().strip()
    score = to_float(giannis_row.get("score_final")) or 0.0
    risk_level = str(giannis_row.get("risk_level") or "Medium")
    instrument_type = classify_instrument(merged)
    fresh = bool(giannis_row.get("fresh_primary_data", babis_row.get("fresh_primary_data")))
    unknown_fields = list(giannis_row.get("unknown_fields") or babis_row.get("unknown_fields") or [])
    upstream_unknown_summary = dict(giannis_row.get("unknown_summary") or {})
    flags = list(giannis_row.get("dilution_flags") or babis_row.get("dilution_flags") or [])
    rationale: list[str] = [
        f"Giannis fundamental tier={giannis_row.get('giannis_tier') or 'unknown'} score={score:.2f}.",
    ]

    if not research_readiness.fundamentals_current(giannis_row, fresh):
        score -= 1.0
        rationale.append("Fresh primary evidence is missing.")
    if risk_level == "High":
        score -= 1.5
        rationale.append("High fundamental/structural risk blocks promotion.")
    elif risk_level == "Medium":
        score -= 0.35
        rationale.append("Medium risk requires additional fundamental diligence.")
    if instrument_type != "common":
        score -= 1.5
        rationale.append(f"Instrument {instrument_type} is outside the common-equity thesis lane.")
    if unknown_fields:
        penalty_units = float(upstream_unknown_summary.get("penalty_units") or len(unknown_fields))
        penalty = min(1.0, 0.15 * penalty_units)
        score -= penalty
        rationale.append(f"Missing fundamental fields reduce score by {penalty:.2f}.")

    catalyst_direction = str(giannis_row.get("catalyst_direction") or "unknown").lower()
    if catalyst_direction == "negative":
        rationale.append("Primary catalyst evidence is adverse.")
    elif catalyst_direction == "positive":
        rationale.append("Primary catalyst evidence is supportive.")
    elif catalyst_direction == "mixed":
        rationale.append("Catalyst evidence is mixed; both thesis directions remain open.")
    else:
        rationale.append("Catalyst direction is not established.")

    feedback_verdicts = [str(item.get("feedback_verdict") or "").lower() for item in feedback_rows]
    if any(value in NEGATIVE_FEEDBACK for value in feedback_verdicts):
        score -= 0.4
        rationale.append("Explicit user feedback requires caution.")
    if any(value in REVIEW_FEEDBACK for value in feedback_verdicts):
        rationale.append("Explicit user feedback requests additional evidence review.")

    final_score = round(clamp(score, 0.0, 10.0), 2)
    if final_score >= 8.0 and risk_level != "High" and instrument_type == "common":
        verdict = "candidate"
    elif final_score >= 6.25 and risk_level != "High" and instrument_type == "common":
        verdict = "manual_review"
    elif final_score >= 4.5:
        verdict = "watch"
    else:
        verdict = "reject"

    confidence = "Medium" if fresh else "Low"
    if risk_level == "High" or len(unknown_fields) >= 4:
        confidence = "Low"

    valuation_fields = {
        key: to_float(merged.get(key))
        for key in ("forward_pe", "trailing_pe", "price_to_sales", "price_to_book", "peg_ratio")
    }
    priced_in = priced_in_assessment.assess(
        merged,
        run_id=run_id,
        run_timestamp=run_timestamp,
    )
    valuation = deterministic_valuation.assess(merged, peers=peer_rows)
    source_base = dict(merged.get("source_base") or {})
    source_base["priced_in_analysis_attempts"] = list(priced_in.get("attempts") or [])
    unknown_reason_overrides = dict(merged.get("unknown_reason_overrides") or {})
    if priced_in.get("priced_in_status") == "not_assessed":
        unknown_reason_overrides["priced_in_status"] = str(
            priced_in.get("reason_code") or "insufficient_source_detail"
        )
    decision = {
        "ticker": ticker,
        "company": giannis_row.get("company"),
        "rank": giannis_row.get("rank"),
        "share_price": to_float(giannis_row.get("share_price")),
        "analysis_mode": "fundamental_only",
        "technical_inputs_used": False,
        "trader_inputs_used": False,
        "ml_inputs_used": False,
        "giannis_score": to_float(giannis_row.get("score_final")) or 0.0,
        "giannis_tier": giannis_row.get("giannis_tier"),
        "risk_level": risk_level,
        "instrument_type": instrument_type,
        "complex_security": instrument_type != "common",
        "catalyst_bucket": giannis_row.get("catalyst_bucket"),
        "catalyst_category_id": giannis_row.get("catalyst_category_id"),
        "catalyst_direction": catalyst_direction,
        "catalyst_attention_score": to_float(giannis_row.get("catalyst_attention_score")),
        "catalyst_classification_confidence": giannis_row.get("catalyst_classification_confidence"),
        "catalyst_evidence_confidence_score": to_float(giannis_row.get("catalyst_evidence_confidence_score")),
        "catalyst_scoring_status": giannis_row.get("catalyst_scoring_status"),
        "long_catalyst_score": to_float(giannis_row.get("long_catalyst_score")),
        "short_catalyst_score": to_float(giannis_row.get("short_catalyst_score")),
        "fresh_primary_data": fresh,
        "fundamentals_fresh": giannis_row.get("fundamentals_fresh"),
        "periodic_filing_age_days": to_float(giannis_row.get("periodic_filing_age_days")),
        "sec_filing_age_days": to_float(giannis_row.get("sec_filing_age_days")),
        "fundamental_attention_score": to_float(giannis_row.get("fundamental_attention_score")),
        "fundamental_coverage": to_float(giannis_row.get("fundamental_coverage")),
        "valuation_inputs": valuation_fields,
        "deterministic_valuation": valuation,
        "valuation_range": valuation.get("valuation_range") if valuation.get("status") == "assessed" else None,
        "forward_pe": valuation_fields["forward_pe"],
        "price_to_sales": valuation_fields["price_to_sales"],
        "price_to_book": valuation_fields["price_to_book"],
        "priced_in_status": priced_in["priced_in_status"],
        "priced_in_confidence": priced_in["confidence"],
        "priced_in_source_as_of": priced_in.get("source_as_of"),
        "expectations_burden_score": priced_in.get("expectations_burden_score"),
        "priced_in_assessment": priced_in,
        "source_base": source_base,
        "unknown_reason_overrides": unknown_reason_overrides,
        "petros_score": final_score,
        "petros_verdict": verdict,
        "confidence": confidence,
        "rationale": rationale[:10],
        "unknown_fields": unknown_fields,
        "unknown_events": list(giannis_row.get("unknown_events") or []),
        "upstream_unknown_events": list(giannis_row.get("upstream_unknown_events") or []),
        "dilution_flags": flags,
        "feedback_count": len(feedback_rows),
        "last_feedback": feedback_rows[0] if feedback_rows else None,
        "strategy_consensus_adjustment": 0.0,
        "yankos_signal_count": 0,
        "periklis_signal_count": 0,
        "ml_probability": None,
        "ml_adjustment": 0.0,
    }
    return unknown_data.annotate_row(decision, "petros", run_id=run_id)


def init_schema(conn: sqlite3.Connection) -> None:
    market_intel_db.init_db(conn)


def store_decisions(db_path: Path, run_ts: str, rows: list[dict[str, Any]]) -> None:
    now = datetime.now(timezone.utc).isoformat()
    inserted = 0
    replaced = 0
    with market_intel_db.connect(db_path) as conn:
        init_schema(conn)
        for row in rows:
            existing = conn.execute(
                "SELECT id FROM petros_decisions WHERE source_run_timestamp = ? AND ticker = ?",
                (run_ts, row["ticker"]),
            ).fetchone()
            conn.execute(
                """
                INSERT OR REPLACE INTO petros_decisions (
                    id, source_run_timestamp, ticker, company, created_at,
                    petros_score, petros_verdict, confidence, rationale_json, payload_json
                )
                VALUES (
                    COALESCE((SELECT id FROM petros_decisions WHERE source_run_timestamp = ? AND ticker = ?), NULL),
                    ?, ?, ?, ?, ?, ?, ?, ?, ?
                )
                """,
                (
                    run_ts,
                    row["ticker"],
                    run_ts,
                    row["ticker"],
                    row.get("company"),
                    now,
                    row.get("petros_score"),
                    row.get("petros_verdict"),
                    row.get("confidence"),
                    as_json(row.get("rationale", [])),
                    as_json(row),
                ),
            )
            if existing:
                replaced += 1
            else:
                inserted += 1
        conn.commit()
    print(f"[PETROS] decisions stored inserted={inserted} replaced={replaced} db={db_path}")


def load_feedback_by_ticker(db_path: Path) -> dict[str, list[dict[str, Any]]]:
    with market_intel_db.connect(db_path) as conn:
        init_schema(conn)
        rows = conn.execute(
            """
            SELECT ticker, created_at, feedback_verdict, notes, source_run_timestamp
            FROM petros_feedback
            ORDER BY id DESC
            """
        ).fetchall()
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        ticker = str(row["ticker"] or "").upper()
        grouped.setdefault(ticker, []).append(
            {
                "ticker": ticker,
                "created_at": row["created_at"],
                "feedback_verdict": row["feedback_verdict"],
                "notes": row["notes"],
                "source_run_timestamp": row["source_run_timestamp"],
            }
        )
    return grouped


def load_strategy_signals_by_ticker(
    db_path: Path,
    run_ts: str,
    allowed_agents: tuple[str, ...] = ("yankos", "periklis"),
) -> dict[str, list[dict[str, Any]]]:
    with market_intel_db.connect(db_path) as conn:
        init_schema(conn)
        placeholders = ",".join("?" for _ in allowed_agents)
        rows = conn.execute(
            f"""
            SELECT ticker, agent_name, playbook_name, signal_score, signal_verdict, confidence, created_at
            FROM strategy_signals
            WHERE source_run_timestamp = ?
              AND agent_name IN ({placeholders})
            ORDER BY signal_score DESC, id DESC
            """,
            (run_ts, *allowed_agents),
        ).fetchall()
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        ticker = str(row["ticker"] or "").upper()
        grouped.setdefault(ticker, []).append(
            {
                "ticker": ticker,
                "agent_name": row["agent_name"],
                "playbook_name": row["playbook_name"],
                "signal_score": row["signal_score"],
                "signal_verdict": row["signal_verdict"],
                "confidence": row["confidence"],
                "created_at": row["created_at"],
            }
        )
    return grouped


def render_md(run_ts: str, rows: list[dict[str, Any]], analysis_mode: str = "fundamental_only") -> str:
    verdict_counts: dict[str, int] = {}
    priced_in_counts: dict[str, int] = {}
    for row in rows:
        verdict = str(row.get("petros_verdict") or "unknown")
        verdict_counts[verdict] = verdict_counts.get(verdict, 0) + 1
        priced_in = str(row.get("priced_in_status") or "not_assessed")
        priced_in_counts[priced_in] = priced_in_counts.get(priced_in, 0) + 1

    lines = [
        "# Petros Fundamental Research Review" if analysis_mode == "fundamental_only" else "# Petros Legacy Mixed Review",
        "",
        f"- Run timestamp: {run_ts}",
        f"- Coverage: {len(rows)} symbols",
        f"- Analysis mode: {analysis_mode}",
        "",
        "## Verdict Summary",
        "",
    ]
    for verdict in ["candidate", "manual_review", "watch", "reject"]:
        lines.append(f"- {verdict}: {verdict_counts.get(verdict, 0)}")

    lines.extend(["", "## Priced-In Assessment Summary", ""])
    for status, count in sorted(priced_in_counts.items()):
        lines.append(f"- {status}: {count}")

    lines.extend(["", "## Petros Candidates", ""])
    candidates = [row for row in rows if row["petros_verdict"] == "candidate"]
    for row in sorted(candidates, key=lambda x: x["petros_score"], reverse=True)[:20]:
        lines.append(format_row(row))

    lines.extend(["", "## Independent Trader Alignment", ""])
    trader_rows = [
        row
        for row in rows
        if row.get("yankos_signal_count", 0) > 0 or row.get("periklis_signal_count", 0) > 0
    ]
    if trader_rows:
        for row in sorted(
            trader_rows,
            key=lambda x: (max(x.get("yankos_best_signal_score", 0), x.get("periklis_best_signal_score", 0)), x["petros_score"]),
            reverse=True,
        )[:20]:
            lines.append(format_row(row))
    else:
        lines.append("- No Yankos or Periklis strategy signals attached to this run.")

    lines.extend(["", "## Early Technical Setups", ""])
    early = [
        row
        for row in rows
        if row.get("technical_timing") in {"near_lows_reversal", "early_accumulation", "pre_run_watch", "pullback_watch"}
        and row.get("petros_verdict") != "reject"
    ]
    for row in sorted(early, key=lambda x: x["petros_score"], reverse=True)[:20]:
        lines.append(format_row(row))

    lines.extend(["", "## Already Extended / Do Not Chase", ""])
    extended = [row for row in rows if row.get("technical_timing") == "already_extended"]
    for row in sorted(extended, key=lambda x: x["petros_score"], reverse=True)[:20]:
        lines.append(format_row(row))

    lines.extend(["", "## Manual Review Queue", ""])
    review = [row for row in rows if row["petros_verdict"] == "manual_review"]
    for row in sorted(review, key=lambda x: x["petros_score"], reverse=True)[:20]:
        lines.append(format_row(row))

    lines.extend(["", "## Reject / Avoid", ""])
    rejected = [row for row in rows if row["petros_verdict"] == "reject"]
    for row in sorted(rejected, key=lambda x: x["petros_score"])[:20]:
        lines.append(format_row(row))

    lines.extend(
        [
            "",
            "## Method Note",
            "",
            "- Petros is the fundamental synthesis and decision-memory agent.",
            f"- Technical or trader inputs used: {'no' if analysis_mode == 'fundamental_only' else 'yes (legacy comparison mode)' }.",
            "- These are research labels, not trade instructions.",
            "- User feedback should be recorded with `python petros_agent.py feedback ...`.",
        ]
    )
    return "\n".join(lines)


def format_row(row: dict[str, Any]) -> str:
    rationale = "; ".join(row.get("rationale", [])[:3])
    return (
        f"- {row['ticker']} | Petros={row['petros_score']}/10 | verdict={row['petros_verdict']} | "
        f"Giannis={row['giannis_score']}/10 | risk={row['risk_level']} | "
        f"instrument={row['instrument_type']} | timing={row.get('technical_timing', 'neutral')} | "
        f"confidence={row['confidence']} | priced_in={row.get('priced_in_status', 'not_assessed')} | "
        f"expectations_burden={row.get('expectations_burden_score')} | "
        f"priced_in_confidence={row.get('priced_in_confidence')} | {rationale}"
    )


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    headers = list(rows[0].keys())
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=headers, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def command_review(args: argparse.Namespace) -> None:
    babis = load_json(Path(args.babis))
    giannis = load_json(Path(args.giannis))
    run_id = require_compatible_runs(babis, giannis)
    run_ts = str(giannis.get("run_timestamp") or babis.get("run_timestamp") or datetime.now(timezone.utc).isoformat())
    babis_rows = {str(row.get("ticker") or "").upper(): row for row in as_list(babis.get("shortlist"))}
    feedback_by_ticker = load_feedback_by_ticker(Path(args.db))
    legacy_mixed = bool(args.legacy_mixed_review)
    analysis_mode = "legacy_mixed" if legacy_mixed else "fundamental_only"
    strategy_signals_by_ticker = (
        load_strategy_signals_by_ticker(Path(args.db), run_ts) if legacy_mixed else {}
    )
    model = (
        load_safe_model(Path(args.model))
        if legacy_mixed and not args.no_model
        else None
    )
    rows = []
    giannis_result_rows = as_list(giannis.get("results"))
    for row in giannis_result_rows:
        ticker = str(row.get("ticker") or "").upper()
        if not ticker:
            continue
        merged_row = dict(row)
        strategy_rows = strategy_signals_by_ticker.get(ticker, [])
        by_agent = {
            agent: [item for item in strategy_rows if str(item.get("agent_name") or "").lower() == agent]
            for agent in ("yankos", "periklis")
        }
        merged_row["strategy_signals_by_agent"] = by_agent
        for agent, agent_rows in by_agent.items():
            if not agent_rows:
                continue
            best_signal = max(agent_rows, key=lambda item: to_float(item.get("signal_score")) or 0.0)
            merged_row[f"{agent}_signal_count"] = len(agent_rows)
            merged_row[f"{agent}_best_signal_score"] = to_float(best_signal.get("signal_score")) or 0.0
            for verdict in ("candidate", "watch", "reject"):
                merged_row[f"{agent}_support_bounce_{verdict}"] = 1.0 if any(
                    item.get("playbook_name") == "support_bounce_swing" and item.get("signal_verdict") == verdict
                    for item in agent_rows
                ) else 0.0
            merged_row[f"{agent}_strategy_signals"] = agent_rows
        if legacy_mixed:
            decision = score_petros(
                merged_row,
                babis_rows.get(ticker),
                feedback_by_ticker.get(ticker, []),
                model,
            )
        else:
            decision = score_petros_fundamental(
                merged_row,
                babis_rows.get(ticker),
                feedback_by_ticker.get(ticker, []),
                [peer for peer in giannis_result_rows
                 if str(peer.get("ticker") or "").upper() != ticker
                 and str(peer.get("sector_id") or peer.get("sector") or "") == str(merged_row.get("sector_id") or merged_row.get("sector") or "")],
                run_id=run_id,
                run_timestamp=run_ts,
            )
        readiness = research_readiness.assess(decision, source_row=merged_row)
        decision["research_readiness"] = readiness
        if readiness["security_thesis_readiness"] != "ready" and decision.get("confidence") == "High":
            decision["confidence"] = "Medium"
        if readiness["security_thesis_readiness"] == "not_decision_grade":
            decision["rationale"] = list(decision.get("rationale") or []) + [
                "Security thesis is not decision-grade; technical review may continue but execution is blocked."
            ]
        rows.append(decision)
    rows_sorted = sorted(rows, key=lambda x: x["petros_score"], reverse=True)
    unknown_events = [
        event for row in rows_sorted for event in (row.get("unknown_events") or [])
    ]
    unknown_summary = unknown_data.summarize_events(
        unknown_events,
        declared_fields=len(unknown_data.load_contract()["agents"]["petros"]["fields"]) * len(rows_sorted),
    )

    out_prefix = Path(args.out_prefix)
    out_json = Path(f"{out_prefix}_latest.json")
    out_md = Path(f"{out_prefix}_latest.md")
    out_csv = Path(f"{out_prefix}_latest.csv")
    payload = attach_run_context(
        {
            "run_timestamp": run_ts,
            "agent_name": "petros",
            "analysis_mode": analysis_mode,
            "technical_inputs_used": legacy_mixed,
            "trader_inputs_used": legacy_mixed,
            "count": len(rows_sorted),
            "data_quality_summary": {"unknown_data": unknown_summary},
            "results": rows_sorted,
        },
        stage="petros_review",
        run_id=run_id,
        source_run_ids=[str(babis.get("run_id") or ""), str(giannis.get("run_id") or "")],
    )
    out_json.write_text(as_json(payload), encoding="utf-8")
    out_md.write_text(render_md(run_ts, rows_sorted, analysis_mode=analysis_mode), encoding="utf-8")
    write_csv(out_csv, rows_sorted)
    store_decisions(Path(args.db), run_ts, rows_sorted)
    print(f"[OUTPUT] JSON: {out_json}")
    print(f"[OUTPUT] MD: {out_md}")
    print(f"[OUTPUT] CSV: {out_csv}")


def command_ticker(args: argparse.Namespace) -> None:
    payload = load_json(Path(args.petros))
    ticker = args.ticker.upper()
    for row in as_list(payload.get("results")):
        if str(row.get("ticker") or "").upper() == ticker:
            print(format_row(row))
            print("Rationale:")
            for item in row.get("rationale", []):
                print(f"- {item}")
            return
    print(f"{ticker} not found in latest Petros review.")


def find_ticker_in_latest(ticker: str, petros_path: Path) -> dict[str, Any] | None:
    payload = load_json(petros_path)
    ticker = ticker.upper()
    for row in as_list(payload.get("results")):
        if str(row.get("ticker") or "").upper() == ticker:
            return row
    return None


def detect_tickers(message: str, petros_path: Path) -> list[str]:
    payload = load_json(petros_path)
    known = {str(row.get("ticker") or "").upper() for row in as_list(payload.get("results"))}
    found: list[str] = []
    for token in re.findall(r"\b[A-Za-z]{1,5}W?\b", message.upper()):
        if token in known and token not in found:
            found.append(token)
    return found


def detect_feedback_verdict(message: str) -> str | None:
    text = message.lower()
    if any(term in text for term in ["filing", "sec", "10-k", "10-q", "8-k", "καταθεση", "κατάθεση"]):
        return "needs_filing_review"
    if any(term in text for term in ["news", "ειδηση", "είδηση", "ειδησεις", "ειδήσεις", "νεα", "νέα"]):
        return "needs_news_check"
    if any(term in text for term in ["διαφων", "δεν συμφων", "λαθος", "λάθος", "οχι", "όχι"]):
        return "disagree"
    if any(term in text for term in ["απορρι", "reject", "κοψ", "βγαλ", "avoid"]):
        return "reject"
    if any(term in text for term in ["παρακολουθ", "watch", "κρατα", "κράτα"]):
        return "watch"
    if any(term in text for term in ["ενδιαφερ", "interested", "μου αρεσει", "μου αρέσει"]):
        return "interested"
    if any(term in text for term in ["συμφων", "agree", "σωστο", "σωστό"]):
        return "agree"
    return None


def latest_feedback_for_ticker(db_path: Path, ticker: str, limit: int = 5) -> list[dict[str, Any]]:
    with market_intel_db.connect(db_path) as conn:
        init_schema(conn)
        rows = conn.execute(
            """
            SELECT ticker, created_at, feedback_verdict, notes, source_run_timestamp
            FROM petros_feedback
            WHERE ticker = ?
            ORDER BY id DESC
            LIMIT ?
            """,
            (ticker.upper(), limit),
        ).fetchall()
    return [
        {
            "ticker": row["ticker"],
            "created_at": row["created_at"],
            "feedback_verdict": row["feedback_verdict"],
            "notes": row["notes"],
            "source_run_timestamp": row["source_run_timestamp"],
        }
        for row in rows
    ]


def store_feedback(db_path: Path, ticker: str, verdict: str, notes: str, run_timestamp: str = "") -> None:
    payload = {
        "ticker": ticker.upper(),
        "feedback_verdict": verdict,
        "notes": notes,
        "source_run_timestamp": run_timestamp,
    }
    with market_intel_db.connect(db_path) as conn:
        init_schema(conn)
        conn.execute(
            """
            INSERT INTO petros_feedback (
                ticker, created_at, feedback_verdict, notes, source_run_timestamp, payload_json
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                ticker.upper(),
                datetime.now(timezone.utc).isoformat(),
                verdict,
                notes,
                run_timestamp,
                as_json(payload),
            ),
        )
        conn.commit()


def format_chat_response(row: dict[str, Any], feedback_rows: list[dict[str, Any]]) -> str:
    ticker = row["ticker"]
    lines = [
        f"Πέτρος για {ticker}:",
        f"- Κρίση: {row['petros_verdict']} με σκορ {row['petros_score']}/10 και εμπιστοσύνη {row['confidence']}.",
        f"- Γιάννης: {row['giannis_score']}/10, tier {row['giannis_tier']}, ρίσκο {row['risk_level']}.",
        f"- Τύπος τίτλου: {row['instrument_type']}.",
    ]
    rationale = row.get("rationale") or []
    if rationale:
        lines.append("- Γιατί: " + "; ".join(str(item) for item in rationale[:5]))
    warnings: list[str] = []
    if row.get("complex_security"):
        warnings.append("είναι σύνθετος τίτλος, άρα θέλει ειδική προσοχή")
    rsi = to_float(row.get("rsi_14"))
    if rsi is not None and rsi > 85:
        warnings.append("το RSI είναι πολύ ψηλά")
    volatility = to_float(row.get("volatility_20d_pct"))
    if volatility is not None and volatility > 100:
        warnings.append("η μεταβλητότητα είναι υπερβολικά μεγάλη")
    if warnings:
        lines.append("- Προσοχή: " + "; ".join(warnings) + ".")
    if feedback_rows:
        latest = feedback_rows[0]
        lines.append(
            f"- Θυμάμαι το τελευταίο σου feedback: {latest['feedback_verdict']} "
            f"({latest.get('notes') or 'χωρίς σημείωση'})."
        )
    else:
        lines.append("- Δεν έχω ακόμα δική σου γνώμη για αυτό το ticker.")
    lines.append("Πες μου αν συμφωνείς, διαφωνείς, αν σε ενδιαφέρει, ή αν θέλεις filing/news review.")
    return "\n".join(lines)


def command_chat(args: argparse.Namespace) -> None:
    db_path = Path(args.db)
    petros_path = Path(args.petros)
    message = args.message.strip()
    tickers = detect_tickers(message, petros_path)
    feedback_verdict = detect_feedback_verdict(message)

    if not tickers:
        payload = load_json(petros_path)
        results = as_list(payload.get("results"))
        candidates = [row for row in results if row.get("petros_verdict") == "candidate"]
        manual = [row for row in results if row.get("petros_verdict") == "manual_review"]
        print("Πέτρος: Πες μου ένα ticker από την τελευταία λίστα για να το συζητήσουμε.")
        if candidates:
            print("Οι πρώτοι υποψήφιοι μου είναι:")
            for row in candidates[:5]:
                print(f"- {row['ticker']} ({row['petros_score']}/10)")
        if manual:
            print("Για χειροκίνητο έλεγχο κοιτάω επίσης:")
            for row in manual[:5]:
                print(f"- {row['ticker']} ({row['petros_score']}/10)")
        return

    for ticker in tickers:
        row = find_ticker_in_latest(ticker, petros_path)
        if row is None:
            print(f"Πέτρος: Δεν βρίσκω το {ticker} στο τελευταίο review.")
            continue
        run_ts = ""
        try:
            run_ts = str(load_json(petros_path).get("run_timestamp") or "")
        except (OSError, json.JSONDecodeError):
            run_ts = ""
        if feedback_verdict:
            store_feedback(db_path, ticker, feedback_verdict, message, run_ts)
            print(f"[PETROS] κράτησα feedback: {ticker} -> {feedback_verdict}")
        feedback_rows = latest_feedback_for_ticker(db_path, ticker)
        print(format_chat_response(row, feedback_rows))
        print("")


def command_feedback(args: argparse.Namespace) -> None:
    verdict = args.verdict.strip().lower()
    if verdict not in FEEDBACK_VERDICTS:
        allowed = ", ".join(sorted(FEEDBACK_VERDICTS))
        raise SystemExit(f"Invalid feedback verdict. Use one of: {allowed}")
    ticker = args.ticker.upper()
    store_feedback(Path(args.db), ticker, verdict, args.notes, args.run_timestamp)
    print(f"[PETROS] feedback stored: {ticker} -> {verdict}")


def command_feedback_list(args: argparse.Namespace) -> None:
    with market_intel_db.connect(Path(args.db)) as conn:
        init_schema(conn)
        rows = conn.execute(
            """
            SELECT ticker, created_at, feedback_verdict, notes
            FROM petros_feedback
            ORDER BY id DESC
            LIMIT ?
            """,
            (args.limit,),
        ).fetchall()
    for row in rows:
        print(f"{row['created_at']} | {row['ticker']} | {row['feedback_verdict']} | {row['notes'] or ''}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Petros human-review and training-label agent.")
    parser.add_argument("--db", default=str(market_intel_db.DEFAULT_DB_PATH))
    subparsers = parser.add_subparsers(dest="command", required=True)

    review = subparsers.add_parser("review", help="Generate Petros decisions from Babis/Giannis outputs.")
    review.add_argument("--babis", default="output/babis_shortlist_latest.json")
    review.add_argument("--giannis", default="output/giannis_rule_based_latest.json")
    review.add_argument("--out-prefix", default="output/petros_review")
    review.add_argument("--model", default=str(DEFAULT_MODEL_PATH))
    review.add_argument("--no-model", action="store_true")
    review.add_argument(
        "--legacy-mixed-review",
        action="store_true",
        help="Compatibility mode that allows technical/trader/model inputs. Never used by the normal pipeline.",
    )
    review.set_defaults(func=command_review)

    ticker = subparsers.add_parser("ticker", help="Show Petros view for a ticker.")
    ticker.add_argument("ticker")
    ticker.add_argument("--petros", default="output/petros_review_latest.json")
    ticker.set_defaults(func=command_ticker)

    chat = subparsers.add_parser("chat", help="Discuss a ticker with Petros and optionally store detected feedback.")
    chat.add_argument("message")
    chat.add_argument("--petros", default="output/petros_review_latest.json")
    chat.set_defaults(func=command_chat)

    feedback = subparsers.add_parser("feedback", help="Store user feedback/training label for Petros.")
    feedback.add_argument("ticker")
    feedback.add_argument("verdict", help=f"One of: {', '.join(sorted(FEEDBACK_VERDICTS))}")
    feedback.add_argument("notes")
    feedback.add_argument("--run-timestamp", default="")
    feedback.set_defaults(func=command_feedback)

    feedback_list = subparsers.add_parser("feedback-list", help="List recent Petros feedback.")
    feedback_list.add_argument("--limit", type=int, default=20)
    feedback_list.set_defaults(func=command_feedback_list)
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    args.func(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
