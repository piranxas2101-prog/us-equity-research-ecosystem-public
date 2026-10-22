from __future__ import annotations

from typing import Any

import market_intel_db


def to_float(value: Any) -> float | None:
    return market_intel_db.to_float(value)


def analyze_technical_setup(row: dict[str, Any]) -> dict[str, Any]:
    rsi = to_float(row.get("rsi_14"))
    return_20d = to_float(row.get("return_20d_pct"))
    volume_ratio = to_float(row.get("volume_ratio_20"))
    volatility = to_float(row.get("volatility_20d_pct"))
    macd_histogram = to_float(row.get("macd_histogram"))
    return_60d = to_float(row.get("return_60d_pct"))
    position_20d = to_float(row.get("price_position_20d_pct"))
    position_60d = to_float(row.get("price_position_60d_pct"))
    distance_from_20d_low = to_float(row.get("distance_from_20d_low_pct"))
    distance_from_60d_low = to_float(row.get("distance_from_60d_low_pct"))
    candlestick_score = to_float(row.get("candlestick_score"))
    candlestick_bias = str(row.get("candlestick_bias") or "neutral")
    candlestick_patterns = row.get("candlestick_patterns") or []
    if not isinstance(candlestick_patterns, list):
        candlestick_patterns = []

    flags: list[str] = []
    score = 0.0
    timing = "neutral"

    if rsi is not None and rsi >= 85:
        flags.append("rsi_extended")
        score -= 0.5
    elif rsi is not None and rsi >= 70:
        flags.append("rsi_hot")
        score -= 0.2
    elif rsi is not None and 40 <= rsi <= 68:
        flags.append("rsi_constructive")
        score += 0.25
    elif rsi is not None and 30 <= rsi < 40:
        flags.append("rsi_early_recovery_zone")
        score += 0.15

    if return_20d is not None and return_20d >= 30:
        flags.append("already_ran_20d")
        score -= 0.65
    elif return_20d is not None and return_20d >= 18:
        flags.append("strong_run_20d")
        score -= 0.25
    elif return_20d is not None and -12 <= return_20d <= 12:
        flags.append("not_extended_20d")
        score += 0.3
    elif return_20d is not None and -25 <= return_20d < -12:
        flags.append("pullback_watch")
        score += 0.15

    if return_60d is not None and return_60d >= 45:
        flags.append("already_ran_60d")
        score -= 0.45
    elif return_60d is not None and return_60d >= 28:
        flags.append("strong_run_60d")
        score -= 0.15
    elif return_60d is not None and -35 <= return_60d <= 8:
        flags.append("low_or_base_60d")
        score += 0.35

    if position_20d is not None:
        if position_20d >= 82:
            flags.append("near_20d_high")
            score -= 0.45
            if position_20d >= 93:
                flags.append("pressing_20d_high")
        elif position_20d <= 38:
            flags.append("near_20d_low")
            score += 0.35

    if position_60d is not None:
        if position_60d >= 82:
            flags.append("near_60d_high")
            score -= 0.55
            if position_60d >= 90:
                flags.append("pressing_60d_high")
        elif position_60d <= 38:
            flags.append("near_60d_low")
            score += 0.45

    if volume_ratio is not None:
        if 1.05 <= volume_ratio <= 2.25:
            flags.append("volume_building")
            score += 0.25
        elif volume_ratio > 3.5:
            flags.append("volume_spike_chase_risk")
            score -= 0.25

    if macd_histogram is not None:
        if macd_histogram > 0:
            flags.append("macd_positive")
            score += 0.2
        elif macd_histogram > -0.05:
            flags.append("macd_near_turn")
            score += 0.1

    bullish_patterns = {"hammer", "bullish_engulfing", "morning_star"}
    bearish_patterns = {"shooting_star", "bearish_engulfing", "evening_star"}
    if any(pattern in bullish_patterns for pattern in candlestick_patterns) or candlestick_bias == "bullish":
        flags.append("bullish_candlestick_confirmation")
        score += 0.2
    if "inside_bar" in candlestick_patterns:
        flags.append("inside_bar_coil")
        score += 0.1
    if "doji" in candlestick_patterns:
        flags.append("doji_indecision")
    if any(pattern in bearish_patterns for pattern in candlestick_patterns) or candlestick_bias == "bearish":
        flags.append("bearish_candlestick_warning")
        score -= 0.25

    if volatility is not None and volatility > 120:
        flags.append("very_high_volatility")
        score -= 0.35

    near_highs_pressure = "near_20d_high" in flags or "near_60d_high" in flags
    bearish_reversal_pressure = (
        "bearish_candlestick_warning" in flags
        or "rsi_extended" in flags
        or "rsi_hot" in flags
        or "volume_spike_chase_risk" in flags
        or (macd_histogram is not None and macd_histogram < 0)
        or "already_ran_20d" in flags
    )

    if near_highs_pressure and bearish_reversal_pressure:
        timing = "near_highs_reversal"
    elif "rsi_extended" in flags or "already_ran_20d" in flags or "near_60d_high" in flags:
        timing = "already_extended"
    elif (
        ("near_20d_low" in flags or "near_60d_low" in flags or "low_or_base_60d" in flags)
        and ("rsi_constructive" in flags or "rsi_early_recovery_zone" in flags)
        and ("volume_building" in flags or "macd_positive" in flags or "macd_near_turn" in flags)
    ):
        timing = "near_lows_reversal"
    elif (
        "not_extended_20d" in flags
        and ("rsi_constructive" in flags or "rsi_early_recovery_zone" in flags)
        and ("volume_building" in flags or "macd_positive" in flags or "macd_near_turn" in flags)
    ):
        timing = "early_accumulation"
    elif "not_extended_20d" in flags and ("rsi_constructive" in flags or "macd_near_turn" in flags):
        timing = "pre_run_watch"
    elif "pullback_watch" in flags and ("macd_near_turn" in flags or "volume_building" in flags):
        timing = "pullback_watch"

    return {
        "technical_timing": timing,
        "technical_timing_score": round(max(-1.5, min(1.0, score)), 2),
        "technical_flags": flags,
        "rsi_14": rsi,
        "return_20d_pct": return_20d,
        "return_60d_pct": return_60d,
        "price_position_20d_pct": position_20d,
        "price_position_60d_pct": position_60d,
        "distance_from_20d_low_pct": distance_from_20d_low,
        "distance_from_60d_low_pct": distance_from_60d_low,
        "volume_ratio_20": volume_ratio,
        "volatility_20d_pct": volatility,
        "macd_histogram": macd_histogram,
        "candlestick_patterns": candlestick_patterns,
        "candlestick_bias": candlestick_bias,
        "candlestick_score": candlestick_score,
    }
