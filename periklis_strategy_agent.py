from __future__ import annotations
from instrument_type_rules import infer_instrument_type, normalize_instrument_type

import argparse
import csv
import json
from collections.abc import Iterator
from contextlib import closing, contextmanager
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse
from typing import Any

import market_intel_db
import playbook_framework
import trader_trade_plan
import technical_setup
import catalyst_trade_lens
import strategy_signals
import trader_confidence
import fundamental_research_packet
from run_context import attach_run_context, require_compatible_runs
from common import load_json


DEFAULT_ALLOWLIST_PATH = Path("strategy_sources_allowlist.json")
DEFAULT_PLAYBOOK_PATH = Path("periklis_playbooks.json")
DEFAULT_DB_PATH = market_intel_db.DEFAULT_DB_PATH


def as_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def as_list(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [row for row in value if isinstance(row, dict)]


def to_float(value: Any) -> float | None:
    return market_intel_db.to_float(value)


def candlestick_patterns(technical: dict[str, Any]) -> list[str]:
    values = technical.get("candlestick_patterns") or []
    if not isinstance(values, list):
        return []
    return [str(value) for value in values if str(value).strip()]


# Score deltas for the short-case scorer, gathered in one place so they are
# visible and tunable (shadow replays can test other values). Thresholds stay
# inline in build_short_case; only the weights live here.
SHORT_CASE_WEIGHTS: dict[str, float] = {
    "instrument_common": 0.9,
    "instrument_other": -2.2,
    "risk_low": 0.7,
    "risk_medium": 0.25,
    "risk_high": -0.9,
    "fresh_data": 0.3,
    "stale_data": -0.45,
    "downside_compression": 1.4,
    "timing_near_highs_reversal": 2.0,
    "timing_already_extended": 1.15,
    "timing_long_friendly": -0.95,
    "pp20_compression_low": 0.8,
    "pp20_ge_96": 1.1,
    "pp20_ge_88": 0.8,
    "pp20_le_55": -0.45,
    "pp60_ge_88": 0.85,
    "pp60_ge_76": 0.4,
    "ret20_compression_down": 1.2,
    "ret20_ge_28": 0.95,
    "ret20_ge_18": 0.65,
    "ret20_le_5": -0.35,
    "ret60_ge_30": 0.45,
    "rsi_compression_band": 0.25,
    "rsi_ge_78": 1.0,
    "rsi_ge_69": 0.6,
    "rsi_le_58": -0.45,
    "macd_lt_neg_005": 0.7,
    "macd_lt_0": 0.45,
    "macd_gt_008": -0.35,
    "vol_compression_expansion": 0.6,
    "vol_ge_35": 0.45,
    "vol_normal_band": 0.1,
    "atr_le_85": 0.2,
    "atr_gt_12": -0.55,
    "compression_confirmed": 1.5,
    "compression_unconfirmed": -1.0,
    "candle_bearish_reversal": 0.9,
    "candle_doji_near_high": 0.15,
    "candle_bullish_reversal": -0.8,
    "candle_bias_bullish": -0.3,
    "fund_net_cash": 0.2,
    "fund_fcf": 0.15,
    "fund_ocf": 0.15,
}

# Score cut-offs that turn long/short scores into candidate or watch verdicts.
VERDICT_THRESHOLDS: dict[str, float] = {
    "long_candidate_min": 7.2,
    "long_candidate_margin": 0.35,
    "short_candidate_min": 7.3,
    "long_watch_min": 5.5,
    "short_watch_min": 5.7,
}


@contextmanager
def scoring_overrides(
    *,
    short_case_weights: dict[str, float] | None = None,
    verdict_thresholds: dict[str, float] | None = None,
) -> Iterator[None]:
    """Temporarily replace scoring weights/thresholds, restoring them afterwards.

    Used only by shadow replays, which run in their own process and never write
    live outputs. Unknown keys are rejected so a typo cannot silently do nothing.
    """
    global SHORT_CASE_WEIGHTS, VERDICT_THRESHOLDS
    for name, overrides, base in (
        ("short_case_weights", short_case_weights, SHORT_CASE_WEIGHTS),
        ("verdict_thresholds", verdict_thresholds, VERDICT_THRESHOLDS),
    ):
        unknown = sorted(set(overrides or {}) - set(base))
        if unknown:
            raise ValueError(f"Unknown {name} keys: {', '.join(unknown)}")
    saved = (SHORT_CASE_WEIGHTS, VERDICT_THRESHOLDS)
    try:
        SHORT_CASE_WEIGHTS = {**SHORT_CASE_WEIGHTS, **{k: float(v) for k, v in (short_case_weights or {}).items()}}
        VERDICT_THRESHOLDS = {**VERDICT_THRESHOLDS, **{k: float(v) for k, v in (verdict_thresholds or {}).items()}}
        yield
    finally:
        SHORT_CASE_WEIGHTS, VERDICT_THRESHOLDS = saved


def build_short_case(ctx: dict[str, Any], playbook_name: str = "") -> tuple[float, list[str]]:
    w = SHORT_CASE_WEIGHTS
    rationale: list[str] = []
    score = 0.0

    if ctx["instrument_type"] == "common":
        score += w["instrument_common"]
        rationale.append("Common stock structure is suitable for directional short review.")
    else:
        score += w["instrument_other"]
        rationale.append(f"Instrument type {ctx['instrument_type']} is outside the short playbook.")

    if ctx["risk_level"] == "Low":
        score += w["risk_low"]
        rationale.append("Low risk profile supports a cleaner short plan.")
    elif ctx["risk_level"] == "Medium":
        score += w["risk_medium"]
        rationale.append("Medium risk is manageable with discipline.")
    else:
        score += w["risk_high"]
        rationale.append("High risk profile is too unstable for a controlled short.")

    if ctx["fresh_primary_data"]:
        score += w["fresh_data"]
        rationale.append("Fresh primary data is available.")
    else:
        score += w["stale_data"]
        rationale.append("Short thesis is weaker without fresh primary support.")

    downside_compression = (
        playbook_name == "pre_run_breakout_confirmation"
        and ctx["timing"] == "pre_run_watch"
        and ctx["return_20d_pct"] is not None
        and ctx["return_20d_pct"] <= -4
        and ctx["macd_histogram"] is not None
        and ctx["macd_histogram"] < 0
    )

    if downside_compression:
        score += w["downside_compression"]
        rationale.append("Pre-run compression is resolving downward rather than forming a bullish setup.")
    elif ctx["timing"] == "near_highs_reversal":
        score += w["timing_near_highs_reversal"]
        rationale.append("Technical timing points to a near-highs reversal risk.")
    elif ctx["timing"] == "already_extended":
        score += w["timing_already_extended"]
        rationale.append("Technical timing already flags the name as extended.")
    elif ctx["timing"] in {"early_accumulation", "pre_run_watch", "near_lows_reversal"}:
        score += w["timing_long_friendly"]
        rationale.append(f"Technical timing {ctx['timing']} still leans too bullish for a fresh short.")
    else:
        rationale.append("Technical timing is neutral for a short.")

    position_20d = ctx["price_position_20d_pct"]
    if position_20d is not None:
        if downside_compression and position_20d <= 45:
            score += w["pp20_compression_low"]
            rationale.append("Price is pressing the lower side of the compression range.")
        elif position_20d >= 96:
            score += w["pp20_ge_96"]
            rationale.append("Price is pinned near the top of the 20-day range.")
        elif position_20d >= 88:
            score += w["pp20_ge_88"]
            rationale.append("Price is very high in the 20-day range.")
        elif position_20d <= 55:
            score += w["pp20_le_55"]
            rationale.append("Price is no longer high enough in the short-term range for a fade.")

    position_60d = ctx["price_position_60d_pct"]
    if position_60d is not None:
        if position_60d >= 88:
            score += w["pp60_ge_88"]
            rationale.append("Price is also near the top of the 60-day range.")
        elif position_60d >= 76:
            score += w["pp60_ge_76"]
            rationale.append("Price remains elevated in the 60-day range.")

    return_20d = ctx["return_20d_pct"]
    if return_20d is not None:
        if downside_compression and return_20d <= -4:
            score += w["ret20_compression_down"]
            rationale.append("Negative 20-day direction agrees with the downside break.")
        elif return_20d >= 28:
            score += w["ret20_ge_28"]
            rationale.append("20-day return looks stretched enough for mean-reversion pressure.")
        elif return_20d >= 18:
            score += w["ret20_ge_18"]
            rationale.append("20-day return is elevated and vulnerable to a fade.")
        elif return_20d <= 5:
            score += w["ret20_le_5"]
            rationale.append("20-day return is not stretched enough yet for a fresh short.")

    return_60d = ctx["return_60d_pct"]
    if return_60d is not None and return_60d >= 30:
        score += w["ret60_ge_30"]
        rationale.append("60-day run confirms the move is mature.")

    rsi = ctx["rsi"]
    if rsi is not None:
        if downside_compression and 35 <= rsi <= 55:
            score += w["rsi_compression_band"]
            rationale.append("RSI supports controlled downside momentum without capitulation.")
        elif rsi >= 78:
            score += w["rsi_ge_78"]
            rationale.append("RSI is overheated.")
        elif rsi >= 69:
            score += w["rsi_ge_69"]
            rationale.append("RSI is hot enough to support a fade thesis.")
        elif rsi <= 58:
            score += w["rsi_le_58"]
            rationale.append("RSI is not extended enough for a new short.")

    macd_histogram = ctx["macd_histogram"]
    if macd_histogram is not None:
        if macd_histogram < -0.05:
            score += w["macd_lt_neg_005"]
            rationale.append("MACD histogram is already negative.")
        elif macd_histogram < 0:
            score += w["macd_lt_0"]
            rationale.append("MACD histogram is rolling under zero.")
        elif macd_histogram > 0.08:
            score += w["macd_gt_008"]
            rationale.append("MACD histogram is still too positive for a clean short.")

    volume_ratio = ctx["volume_ratio"]
    if volume_ratio is not None:
        if downside_compression and volume_ratio >= 1.2:
            score += w["vol_compression_expansion"]
            rationale.append("Relative volume confirms the downside expansion.")
        elif volume_ratio >= 3.5:
            score += w["vol_ge_35"]
            rationale.append("Volume spike increases exhaustion risk.")
        elif 0.85 <= volume_ratio <= 1.8:
            score += w["vol_normal_band"]
            rationale.append("Volume is controlled enough for a patient short review.")

    atr_14_pct = ctx["atr_14_pct"]
    if atr_14_pct is not None:
        if atr_14_pct <= 8.5:
            score += w["atr_le_85"]
            rationale.append("ATR remains manageable for a short setup.")
        elif atr_14_pct > 12:
            score += w["atr_gt_12"]
            rationale.append("ATR is too high for a clean short.")

    if downside_compression:
        required = {"completed range break", "volume expansion", "current-session direction agrees"}
        observed = {str(item) for item in ctx.get("confirmed_entry_conditions", [])}
        if required.issubset(observed):
            score += w["compression_confirmed"]
            rationale.append("Completed range break, volume and session direction confirm the SHORT setup.")
        else:
            score += w["compression_unconfirmed"]
            rationale.append("Downside compression remains watch-only until every entry confirmation is observed.")

    if any(pattern in {"shooting_star", "bearish_engulfing", "evening_star"} for pattern in ctx["candlestick_patterns"]):
        score += w["candle_bearish_reversal"]
        rationale.append("Bearish candlestick reversal supports the short case.")
    elif "doji" in ctx["candlestick_patterns"] and position_20d is not None and position_20d >= 88:
        score += w["candle_doji_near_high"]
        rationale.append("Doji near the highs suggests buyer hesitation.")

    if any(pattern in {"hammer", "bullish_engulfing", "morning_star"} for pattern in ctx["candlestick_patterns"]):
        score += w["candle_bullish_reversal"]
        rationale.append("Bullish reversal candle is a warning against the short.")
    elif ctx["candlestick_bias"] == "bullish" and ctx["candlestick_score"] is not None and ctx["candlestick_score"] > 0:
        score += w["candle_bias_bullish"]
        rationale.append("Recent candlestick bias still leans bullish.")

    fundamental_strength_penalty = 0.0
    if ctx["net_cash"] is not None and ctx["net_cash"] > 0:
        fundamental_strength_penalty += w["fund_net_cash"]
    if ctx["fcf"] is not None and ctx["fcf"] > 0:
        fundamental_strength_penalty += w["fund_fcf"]
    if ctx["ocf"] is not None and ctx["ocf"] > 0:
        fundamental_strength_penalty += w["fund_ocf"]
    if fundamental_strength_penalty > 0:
        score -= fundamental_strength_penalty
        rationale.append("Fundamentals are still decent enough to make the short thesis less effortless.")

    return round(max(0.0, min(score, 10.0)), 2), rationale[:8]


def classify_instrument(row: dict[str, Any]) -> str:
    existing = normalize_instrument_type(row.get("instrument_type"))
    if existing:
        return existing
    return infer_instrument_type(row.get("ticker") or row.get("symbol"), row.get("company") or row.get("company_name"))[0]


def verify_sources(playbook: dict[str, Any], allowlist: dict[str, Any]) -> list[str]:
    allowed = {str(domain).lower() for domain in allowlist.get("approved_domains", [])}
    approved: list[str] = []
    for raw_url in playbook.get("approved_sources", []):
        url = str(raw_url).strip()
        if not url:
            continue
        host = urlparse(url).netloc.lower()
        if host.startswith("www."):
            host = host[4:]
        if host not in allowed:
            raise SystemExit(f"Playbook source {url} is not in allowlist {sorted(allowed)}")
        approved.append(url)
    return approved


def common_row_context(row: dict[str, Any]) -> dict[str, Any]:
    technical = technical_setup.analyze_technical_setup(row)
    declared_technical = row.get("technical")
    if isinstance(declared_technical, dict):
        technical.update({key: value for key, value in declared_technical.items() if value is not None})
    return {
        "ticker": str(row.get("ticker") or "").upper().strip(),
        "technical": technical,
        "instrument_type": classify_instrument(row),
        "risk_level": str(row.get("risk_level") or "Medium"),
        "fresh_primary_data": bool(row.get("fresh_primary_data", True)),
        "flags": row.get("dilution_flags") if isinstance(row.get("dilution_flags"), list) else [str(item) for item in (row.get("dilution_flags") or [])],
        "rsi": to_float(row.get("rsi_14")),
        "volume_ratio": to_float(row.get("volume_ratio_20")),
        "macd_histogram": to_float(row.get("macd_histogram")),
        "atr_14_pct": to_float(row.get("atr_14_pct")),
        "net_cash": to_float(row.get("net_cash")),
        "fcf": to_float(row.get("fcf_proxy")),
        "ocf": to_float(row.get("ocf_proxy")),
        "return_20d_pct": to_float(row.get("return_20d_pct")),
        "return_60d_pct": to_float(row.get("return_60d_pct")),
        "price_position_20d_pct": to_float(technical.get("price_position_20d_pct")),
        "price_position_60d_pct": to_float(technical.get("price_position_60d_pct")),
        "distance_from_20d_low_pct": to_float(technical.get("distance_from_20d_low_pct")),
        "distance_from_60d_low_pct": to_float(technical.get("distance_from_60d_low_pct")),
        "timing": str(technical.get("technical_timing") or "neutral"),
        "candlestick_patterns": candlestick_patterns(technical),
        "candlestick_bias": str(technical.get("candlestick_bias") or "neutral"),
        "candlestick_score": to_float(technical.get("candlestick_score")),
        "confirmed_entry_conditions": [
            str(item) for item in row.get("confirmed_entry_conditions", [])
        ] if isinstance(row.get("confirmed_entry_conditions"), list) else [],
    }


def final_signal_payload(
    playbook: dict[str, Any],
    row: dict[str, Any],
    score: float,
    rationale: list[str],
    context: dict[str, Any],
    short_override: tuple[float, list[str]] | None = None,
) -> dict[str, Any]:
    catalyst_lens = catalyst_trade_lens.advisory_adjustments(row)
    final_score = round(max(0.0, min(score + float(catalyst_lens["long_adjustment"]), 10.0)), 2)
    short_score, short_rationale = (short_override if short_override is not None
                                    else build_short_case(context, str(playbook.get("name") or "")))
    short_score = round(max(0.0, min(short_score + float(catalyst_lens["short_adjustment"]), 10.0)), 2)
    instrument_type = str(context["instrument_type"])
    risk_level = str(context["risk_level"])
    direction = "flat"
    verdict = "reject"
    confidence = "Low"
    selected_score = final_score
    selected_rationale = rationale[:8]

    t = VERDICT_THRESHOLDS
    if (final_score >= t["long_candidate_min"] and instrument_type == "common" and risk_level != "High"
            and final_score >= short_score + t["long_candidate_margin"]):
        direction = "long"
        verdict = "candidate"
        confidence = "High" if risk_level == "Low" else "Medium"
    elif (short_score >= t["short_candidate_min"] and instrument_type == "common" and risk_level != "High"
            and short_score > final_score):
        direction = "short"
        verdict = "candidate"
        confidence = "High" if risk_level == "Low" else "Medium"
        selected_score = short_score
        selected_rationale = short_rationale
    elif final_score >= t["long_watch_min"] and final_score >= short_score:
        direction = "long"
        verdict = "watch"
        confidence = "Medium"
    elif short_score >= t["short_watch_min"]:
        direction = "short"
        verdict = "watch"
        confidence = "Medium"
        selected_score = short_score
        selected_rationale = short_rationale

    technical = context["technical"]
    return {
        "ticker": context["ticker"],
        "company": row.get("company"),
        "sector": row.get("sector"),
        "industry": row.get("industry"),
        "sector_id": row.get("sector_id"),
        "industry_id": row.get("industry_id"),
        "classification_status": row.get("classification_status"),
        "catalyst_category_id": row.get("catalyst_category_id"),
        "fundamental_thesis_id": row.get("fundamental_thesis_id"),
        "fundamental_thesis_hash": row.get("fundamental_thesis_hash"),
        "fundamental_packet_hash": row.get("fundamental_packet_hash"),
        "fundamental_security_readiness": row.get("fundamental_security_readiness"),
        "fundamental_execution_ready": bool(row.get("fundamental_execution_ready")),
        "fundamental_decision_blockers": list(row.get("fundamental_decision_blockers") or []),
        "fundamental_long_case": row.get("fundamental_long_case", {}),
        "fundamental_short_case": row.get("fundamental_short_case", {}),
        "fundamental_prove_criteria": list(row.get("fundamental_prove_criteria") or []),
        "fundamental_kill_criteria": list(row.get("fundamental_kill_criteria") or []),
        "agent_name": "periklis",
        "playbook_name": playbook["name"],
        "style": playbook.get("style", "strategy"),
        "signal_score": round(max(0.0, min(selected_score, 10.0)), 2),
        "signal_verdict": verdict,
        "trade_direction": direction,
        "entry_side": "buy" if direction == "long" else ("sell" if direction == "short" else "none"),
        "long_signal_score": final_score,
        "short_signal_score": short_score,
        # The old High/Medium/Low label is the company risk tier, not confidence (docs/REDESIGN_NOTES_2026-10.md 4θ).
        **trader_confidence.label_fields("periklis", str(playbook["name"]), direction, confidence),
        "risk_level": risk_level,
        "instrument_type": instrument_type,
        "fresh_primary_data": context["fresh_primary_data"],
        "giannis_score": to_float(row.get("score_final")),
        "technical_timing": context["timing"],
        "rsi_14": context["rsi"],
        "volume_ratio_20": context["volume_ratio"],
        "macd_histogram": context["macd_histogram"],
        "atr_14_pct": context["atr_14_pct"],
        "price_position_20d_pct": context["price_position_20d_pct"],
        "price_position_60d_pct": context["price_position_60d_pct"],
        "distance_from_20d_low_pct": context["distance_from_20d_low_pct"],
        "distance_from_60d_low_pct": context["distance_from_60d_low_pct"],
        "candlestick_patterns": context["candlestick_patterns"],
        "candlestick_bias": context["candlestick_bias"],
        "candlestick_score": context["candlestick_score"],
        "catalyst_trade_lens": catalyst_lens,
        "long_catalyst_score": catalyst_lens["long_catalyst_score"],
        "short_catalyst_score": catalyst_lens["short_catalyst_score"],
        "catalyst_trade_bias": catalyst_lens["trade_bias"],
        "rationale": selected_rationale,
        "technical_flags": technical.get("technical_flags", []),
    }


def early_accumulation_swing(playbook: dict[str, Any], row: dict[str, Any]) -> dict[str, Any]:
    rules = playbook.get("rules", {})
    ctx = common_row_context(row)
    rationale: list[str] = []
    score = 0.0

    if ctx["instrument_type"] == "common":
        score += 1.1
        rationale.append("Common stock structure fits the early-accumulation swing plan.")
    else:
        score -= 2.0
        rationale.append(f"Instrument type {ctx['instrument_type']} is outside this swing plan.")

    if ctx["risk_level"] == "Low":
        score += 0.9
        rationale.append("Low risk profile supports a cleaner early build.")
    elif ctx["risk_level"] == "Medium":
        score += 0.2
        rationale.append("Medium risk is acceptable but not ideal.")
    else:
        score -= 1.4
        rationale.append("High risk profile is too unstable for this setup.")

    if ctx["fresh_primary_data"]:
        score += 0.8
        rationale.append("Fresh primary filing data is available.")
    else:
        score -= 1.0
        rationale.append("Fresh filing support is missing.")

    if ctx["timing"] == "early_accumulation":
        score += 1.6
        rationale.append("Technical timing fits early accumulation.")
    elif ctx["timing"] == "near_lows_reversal":
        score += 0.6
        rationale.append("Near-lows reversal can still evolve into early accumulation.")
    elif ctx["timing"] == "pre_run_watch":
        score += 0.4
        rationale.append("Pre-run watch is acceptable if the base is still controlled.")
    elif ctx["timing"] == "already_extended":
        score -= 1.7
        rationale.append("Already extended names do not fit early accumulation.")
    else:
        score -= 0.2
        rationale.append(f"Technical timing {ctx['timing']} is weaker for early accumulation.")

    price_position_60d = ctx["price_position_60d_pct"]
    if price_position_60d is not None:
        soft_max = float(rules.get("soft_price_position_60d_max", 76))
        if price_position_60d <= 65:
            score += 0.6
            rationale.append("Price is still inside a manageable 60-day range position.")
        elif price_position_60d <= soft_max:
            score += 0.35
            rationale.append("Price is still in a workable 60-day accumulation region.")
        else:
            score -= 0.7
            rationale.append("Price is too high in the 60-day range for this setup.")

    price_position_20d = ctx["price_position_20d_pct"]
    if price_position_20d is not None:
        soft_max = float(rules.get("soft_price_position_20d_max", 85))
        if price_position_20d <= 78:
            score += 0.5
            rationale.append("Price is not yet overstretched inside the 20-day range.")
        elif price_position_20d <= soft_max:
            score += 0.2
            rationale.append("Price remains acceptable inside the 20-day range.")
        else:
            score -= 0.75
            rationale.append("Price is too close to the short-term high.")

    distance_from_20d_low = ctx["distance_from_20d_low_pct"]
    if distance_from_20d_low is not None:
        soft_max = float(rules.get("soft_distance_from_20d_low_max", 15))
        if distance_from_20d_low <= 10:
            score += 0.6
            rationale.append("Support remains reasonably close.")
        elif distance_from_20d_low <= soft_max:
            score += 0.25
            rationale.append("Support is still close enough for an early build.")
        else:
            score -= 0.6
            rationale.append("Support has drifted too far away.")

    rsi = ctx["rsi"]
    if rsi is not None:
        rsi_min = float(rules.get("preferred_rsi_min", 40))
        rsi_max = float(rules.get("preferred_rsi_max", 64))
        if rsi_min <= rsi <= rsi_max:
            score += 0.8
            rationale.append("RSI sits in the preferred accumulation range.")
        elif 35 <= rsi < rsi_min:
            score += 0.3
            rationale.append("RSI is still constructive for an early build.")
        elif rsi > 70:
            score -= 0.8
            rationale.append("RSI is too hot for an early-entry profile.")

    volume_ratio = ctx["volume_ratio"]
    if volume_ratio is not None:
        if volume_ratio >= float(rules.get("min_volume_ratio_strong", 1.15)):
            score += 0.7
            rationale.append("Volume confirms real participation.")
        elif volume_ratio >= float(rules.get("min_volume_ratio_soft", 0.95)):
            score += 0.3
            rationale.append("Volume is good enough for a developing setup.")
        else:
            score -= 0.7
            rationale.append("Volume is too weak for a confident early accumulation read.")

    macd_histogram = ctx["macd_histogram"]
    if macd_histogram is not None:
        if macd_histogram > 0:
            score += 0.5
            rationale.append("MACD histogram is positive.")
        elif macd_histogram >= float(rules.get("macd_histogram_floor", -0.05)):
            score += 0.2
            rationale.append("MACD histogram is close to turning positive.")
        else:
            score -= 0.5
            rationale.append("MACD histogram remains too weak.")

    atr_14_pct = ctx["atr_14_pct"]
    if atr_14_pct is not None:
        if atr_14_pct <= 5:
            score += 0.5
            rationale.append("ATR is manageable for a controlled swing.")
        elif atr_14_pct <= float(rules.get("max_atr_14_pct", 8.0)):
            score += 0.2
            rationale.append("ATR is acceptable.")
        else:
            score -= 0.7
            rationale.append("ATR is too high for a clean accumulation setup.")

    if any(pattern in {"hammer", "bullish_engulfing", "morning_star"} for pattern in ctx["candlestick_patterns"]):
        score += 0.45
        rationale.append("Bullish candlestick pattern supports the early-entry read.")
    elif "inside_bar" in ctx["candlestick_patterns"]:
        score += 0.25
        rationale.append("Inside-bar compression supports a patient early build.")
    elif "doji" in ctx["candlestick_patterns"]:
        score += 0.05
        rationale.append("Doji keeps the setup early and watchful.")

    if any(pattern in {"shooting_star", "bearish_engulfing", "evening_star"} for pattern in ctx["candlestick_patterns"]):
        score -= 0.65
        rationale.append("Bearish candlestick warning weakens the early-entry setup.")
    elif ctx["candlestick_bias"] == "bearish" and ctx["candlestick_score"] is not None and ctx["candlestick_score"] < 0:
        score -= 0.2
        rationale.append("Recent candlestick bias leans bearish.")

    if ctx["net_cash"] is not None and ctx["net_cash"] > 0:
        score += 0.5
        rationale.append("Positive net cash improves resilience.")
    if ctx["fcf"] is not None and ctx["fcf"] > 0:
        score += 0.25
        rationale.append("Positive free cash flow supports the setup.")
    if ctx["ocf"] is not None and ctx["ocf"] > 0:
        score += 0.25
        rationale.append("Positive operating cash flow supports the setup.")

    forbidden_flags = {str(flag) for flag in rules.get("forbidden_flags", [])}
    for flag in ctx["flags"]:
        if flag in forbidden_flags:
            score -= 1.0
            rationale.append(f"Forbidden flag for this playbook: {flag}.")
        elif flag in {"negative_operating_cashflow", "negative_free_cashflow", "high_debt_to_equity"}:
            score -= 0.6
            rationale.append(f"Balance-sheet penalty: {flag}.")
        elif flag == "weak_liquidity_current_ratio":
            score -= 0.4
            rationale.append("Liquidity quality is weaker than preferred.")

    return final_signal_payload(playbook, row, score, rationale, ctx)


def pre_run_breakout_confirmation(playbook: dict[str, Any], row: dict[str, Any]) -> dict[str, Any]:
    rules = playbook.get("rules", {})
    ctx = common_row_context(row)
    rationale: list[str] = []
    score = 0.0

    if ctx["instrument_type"] == "common":
        score += 1.1
        rationale.append("Common stock structure fits the breakout-confirmation swing plan.")
    else:
        score -= 2.0
        rationale.append(f"Instrument type {ctx['instrument_type']} is outside this breakout plan.")

    if ctx["risk_level"] == "Low":
        score += 0.8
        rationale.append("Low risk profile supports cleaner breakout behavior.")
    elif ctx["risk_level"] == "Medium":
        score += 0.2
        rationale.append("Medium risk is acceptable but not ideal.")
    else:
        score -= 1.4
        rationale.append("High risk profile is too unstable for breakout confirmation.")

    if ctx["fresh_primary_data"]:
        score += 0.8
        rationale.append("Fresh primary filing data is available.")
    else:
        score -= 1.0
        rationale.append("Fresh filing support is missing.")

    if ctx["timing"] == "pre_run_watch":
        score += 1.5
        rationale.append("Technical timing fits a pre-run setup.")
    elif ctx["timing"] == "early_accumulation":
        score += 1.0
        rationale.append("Early accumulation can transition into breakout confirmation.")
    elif ctx["timing"] == "pullback_watch":
        score += 0.5
        rationale.append("A controlled pullback can still lead into a valid breakout confirmation.")
    elif ctx["timing"] == "already_extended":
        score -= 1.6
        rationale.append("Already extended names are not controlled breakout entries.")
    else:
        score -= 0.2
        rationale.append(f"Technical timing {ctx['timing']} is weaker for breakout confirmation.")

    price_position_20d = ctx["price_position_20d_pct"]
    if price_position_20d is not None:
        min_pos = float(rules.get("preferred_price_position_20d_min", 62))
        max_pos = float(rules.get("preferred_price_position_20d_max", 92))
        early_buffer = float(rules.get("early_zone_buffer_20d", 8))
        if min_pos <= price_position_20d <= max_pos:
            score += 0.8
            rationale.append("Price is pressing the upper part of the short-term range without being overextended.")
        elif price_position_20d > max_pos:
            score -= 0.8
            rationale.append("Price already looks too stretched for a clean confirmation.")
        elif price_position_20d >= max(0.0, min_pos - early_buffer):
            score += 0.1
            rationale.append("Price is just under the trigger zone, which still fits an early breakout review.")
        else:
            score -= 0.3
            rationale.append("Price is not yet close enough to the trigger zone.")

    price_position_60d = ctx["price_position_60d_pct"]
    if price_position_60d is not None:
        min_pos = float(rules.get("preferred_price_position_60d_min", 38))
        max_pos = float(rules.get("preferred_price_position_60d_max", 85))
        early_buffer = float(rules.get("early_zone_buffer_60d", 6))
        if min_pos <= price_position_60d <= max_pos:
            score += 0.5
            rationale.append("Price still sits in a tradable 60-day breakout region.")
        elif price_position_60d > max_pos:
            score -= 0.6
            rationale.append("Price is too high in the 60-day range for a fresh breakout entry.")
        elif price_position_60d >= max(0.0, min_pos - early_buffer):
            score += 0.1
            rationale.append("Price is slightly below the ideal 60-day breakout band but still close enough.")

    return_20d_pct = ctx["return_20d_pct"]
    if return_20d_pct is not None:
        max_return = float(rules.get("max_return_20d_pct", 22))
        if 0 <= return_20d_pct <= max_return:
            score += 0.5
            rationale.append("The move has started but is not yet overextended.")
        elif return_20d_pct > max_return:
            score -= 0.7
            rationale.append("The 20-day move already looks too stretched.")

    rsi = ctx["rsi"]
    if rsi is not None:
        rsi_min = float(rules.get("preferred_rsi_min", 46))
        rsi_max = float(rules.get("preferred_rsi_max", 68))
        if rsi_min <= rsi <= rsi_max:
            score += 0.7
            rationale.append("RSI supports controlled momentum.")
        elif rsi > 72:
            score -= 0.6
            rationale.append("RSI is too hot for a breakout-first entry.")
        elif rsi < 40:
            score -= 0.3
            rationale.append("RSI is too weak for a breakout confirmation read.")

    volume_ratio = ctx["volume_ratio"]
    if volume_ratio is not None:
        if volume_ratio >= float(rules.get("min_volume_ratio_strong", 1.25)):
            score += 0.8
            rationale.append("Volume shows strong confirmation.")
        elif volume_ratio >= float(rules.get("min_volume_ratio_soft", 0.95)):
            score += 0.35
            rationale.append("Volume is good enough for an early breakout confirmation.")
        else:
            score -= 0.5
            rationale.append("Volume is too weak for breakout confirmation.")

    macd_histogram = ctx["macd_histogram"]
    if macd_histogram is not None:
        if macd_histogram > 0:
            score += 0.6
            rationale.append("MACD histogram is positive.")
        elif macd_histogram >= float(rules.get("macd_histogram_floor", -0.04)):
            score += 0.2
            rationale.append("MACD histogram is close to positive.")
        else:
            score -= 0.4
            rationale.append("MACD histogram remains too weak.")

    atr_14_pct = ctx["atr_14_pct"]
    if atr_14_pct is not None:
        if atr_14_pct <= 5.5:
            score += 0.4
            rationale.append("ATR is manageable for a breakout trade.")
        elif atr_14_pct <= float(rules.get("max_atr_14_pct", 8.5)):
            score += 0.1
            rationale.append("ATR is acceptable.")
        else:
            score -= 0.7
            rationale.append("ATR is too high for a clean breakout setup.")

    if any(pattern in {"bullish_engulfing", "morning_star"} for pattern in ctx["candlestick_patterns"]):
        score += 0.5
        rationale.append("Bullish candlestick confirmation supports the pre-run breakout read.")
    elif "inside_bar" in ctx["candlestick_patterns"]:
        score += 0.35
        rationale.append("Inside-bar compression can precede a cleaner breakout confirmation.")
    elif "doji" in ctx["candlestick_patterns"]:
        score += 0.05
        rationale.append("Doji keeps the breakout setup alive but unresolved.")

    if any(pattern in {"shooting_star", "bearish_engulfing", "evening_star"} for pattern in ctx["candlestick_patterns"]):
        score -= 0.75
        rationale.append("Bearish candlestick warning weakens breakout confirmation.")
    elif ctx["candlestick_bias"] == "bearish" and ctx["candlestick_score"] is not None and ctx["candlestick_score"] < 0:
        score -= 0.2
        rationale.append("Recent candlestick bias leans bearish.")

    if ctx["net_cash"] is not None and ctx["net_cash"] > 0:
        score += 0.4
        rationale.append("Positive net cash helps support follow-through.")
    if ctx["ocf"] is not None and ctx["ocf"] > 0:
        score += 0.3
        rationale.append("Positive operating cash flow supports the setup.")

    forbidden_flags = {str(flag) for flag in rules.get("forbidden_flags", [])}
    for flag in ctx["flags"]:
        if flag in forbidden_flags:
            score -= 1.0
            rationale.append(f"Forbidden flag for this playbook: {flag}.")
        elif flag in {"negative_operating_cashflow", "negative_free_cashflow", "high_debt_to_equity"}:
            score -= 0.6
            rationale.append(f"Balance-sheet penalty: {flag}.")
        elif flag == "weak_liquidity_current_ratio":
            score -= 0.4
            rationale.append("Liquidity quality is weaker than preferred.")

    return final_signal_payload(playbook, row, score, rationale, ctx)


def daily_strategy_signal(playbook: dict[str, Any], row: dict[str, Any]) -> dict[str, Any]:
    """A library strategy scored on stored daily bars, finalized like every Periklis signal."""
    scored = strategy_signals.score(playbook, row)
    signal = final_signal_payload(playbook, row, scored.long_score, scored.long_rationale, common_row_context(row),
                                  short_override=(scored.short_score, scored.short_rationale))
    signal.update(scored.meta)
    return signal


def evaluate_playbook(playbook: dict[str, Any], row: dict[str, Any]) -> dict[str, Any]:
    if strategy_signals.is_daily_strategy(playbook):
        return daily_strategy_signal(playbook, row)
    name = str(playbook.get("name") or "")
    if name == "early_accumulation_swing":
        return early_accumulation_swing(playbook, row)
    if name == "pre_run_breakout_confirmation":
        return pre_run_breakout_confirmation(playbook, row)
    raise SystemExit(f"Unsupported playbook: {name}")


def store_signals(db_path: Path, run_ts: str, rows: list[dict[str, Any]], agent_name: str) -> None:
    now = datetime.now(timezone.utc).isoformat()
    inserted = 0
    replaced = 0
    with closing(market_intel_db.connect(db_path)) as conn:
        market_intel_db.init_db(conn)
        for row in rows:
            existing = conn.execute(
                """
                SELECT id
                FROM strategy_signals
                WHERE source_run_timestamp = ? AND ticker = ? AND agent_name = ? AND playbook_name = ?
                """,
                (run_ts, row["ticker"], agent_name, row["playbook_name"]),
            ).fetchone()
            conn.execute(
                """
                INSERT OR REPLACE INTO strategy_signals (
                    id, agent_name, source_run_timestamp, ticker, playbook_name, created_at,
                    signal_score, signal_verdict, confidence, trade_direction, sector_id,
                    industry_id, catalyst_category_id, rationale_json, payload_json
                )
                VALUES (
                    COALESCE((
                        SELECT id
                        FROM strategy_signals
                        WHERE source_run_timestamp = ? AND ticker = ? AND agent_name = ? AND playbook_name = ?
                    ), NULL),
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                )
                """,
                (
                    run_ts,
                    row["ticker"],
                    agent_name,
                    row["playbook_name"],
                    agent_name,
                    run_ts,
                    row["ticker"],
                    row["playbook_name"],
                    now,
                    row.get("signal_score"),
                    row.get("signal_verdict"),
                    row.get("confidence"),
                    row.get("trade_direction"),
                    row.get("sector_id"),
                    row.get("industry_id"),
                    row.get("catalyst_category_id"),
                    as_json(row.get("rationale", [])),
                    as_json(row),
                ),
            )
            if existing:
                replaced += 1
            else:
                inserted += 1
        conn.commit()
    print(f"[PERIKLIS] signals stored inserted={inserted} replaced={replaced} db={db_path}")


def render_md(
    run_ts: str,
    agent_name: str,
    rows: list[dict[str, Any]],
    approved_sources: list[str],
    framework: dict[str, Any] | None = None,
    perspectives: list[dict[str, Any]] | None = None,
) -> str:
    counts: dict[str, int] = {}
    directions: dict[str, int] = {}
    for row in rows:
        verdict = str(row.get("signal_verdict") or "unknown")
        counts[verdict] = counts.get(verdict, 0) + 1
        direction = str(row.get("trade_direction") or "flat")
        directions[direction] = directions.get(direction, 0) + 1

    def format_row(row: dict[str, Any]) -> str:
        rationale = "; ".join(row.get("rationale", [])[:3])
        return (
            f"- {row['ticker']} | Periklis={row['signal_score']}/10 | verdict={row['signal_verdict']} | "
            f"direction={row.get('trade_direction')} | "
            f"playbook={row['playbook_name']} | risk={row['risk_level']} | timing={row['technical_timing']} | "
            f"confidence={row['confidence']} | {rationale}"
        )

    lines = [
        "# Periklis Strategy Report",
        "",
        f"- Run timestamp: {run_ts}",
        f"- Agent: {agent_name}",
        f"- Coverage: {len(rows)} symbols",
        "",
        "## Verdict Summary",
        "",
        f"- candidate: {counts.get('candidate', 0)}",
        f"- watch: {counts.get('watch', 0)}",
        f"- reject: {counts.get('reject', 0)}",
        f"- long bias: {directions.get('long', 0)}",
        f"- short bias: {directions.get('short', 0)}",
        "",
        "## Strongest Swing Candidates",
        "",
    ]
    candidates = [row for row in rows if row.get("signal_verdict") == "candidate"]
    if candidates:
        for row in sorted(candidates, key=lambda item: item["signal_score"], reverse=True)[:20]:
            lines.append(format_row(row))
    else:
        lines.append("- No high-confidence Periklis swing candidates.")

    lines.extend(["", "## Watch Queue", ""])
    watch_rows = [row for row in rows if row.get("signal_verdict") == "watch"]
    if watch_rows:
        for row in sorted(watch_rows, key=lambda item: item["signal_score"], reverse=True)[:20]:
            lines.append(format_row(row))
    else:
        lines.append("- No Periklis watch names right now.")

    lines.extend(["", "## Approved Sources", ""])
    for source in approved_sources:
        lines.append(f"- {source}")

    lines.extend(
        [
            "",
            "## Method Note",
            "",
            "- Periklis is a rule-based swing strategy specialist using approved institutional sources only.",
            "- He focuses on early accumulation and pre-run breakout confirmation, now expressed as directional trader setups.",
            "- Periklis can now express long or short bias when the structure supports it, while staying more selective than Yankos.",
            "- Periklis consumes the frozen same-run fundamental thesis and cannot modify it.",
            "- His output is an independent technical plan, not a replacement for the upstream analyst layer.",
        ]
    )
    if framework:
        lines.extend(
            [
                "",
                "## Playbook Framework v2",
                "",
                f"- Catalog playbooks: {framework.get('catalog_playbook_count')}",
                f"- Assigned portfolio: {framework.get('portfolio_playbook_count')}",
                f"- Advisory perspectives evaluated: {framework.get('perspective_count')}",
                f"- Portfolio statuses: {framework.get('portfolio_status_counts')}",
                f"- LONG/SHORT/FLAT views: {framework.get('perspective_side_counts')}",
                "- Perspective rows have no execution authority; Koulis/Thymios and an active specialized evaluator remain mandatory.",
                "",
                "### Strong alternative views",
                "",
            ]
        )
        strong = sorted(
            [item for item in (perspectives or []) if item.get("readiness") == "strong_match"],
            key=lambda item: item.get("agent_priority_score", item.get("match_score", 0)),
            reverse=True,
        )[:12]
        if strong:
            for item in strong:
                lines.append(
                    f"- {item.get('ticker')} | {item.get('playbook_id')} | {item.get('preferred_side')} | "
                    f"match={item.get('match_score')} | priority={item.get('agent_priority_score')} | "
                    f"status={item.get('portfolio_status')}"
                )
        else:
            lines.append("- No strong advisory match with complete data.")
    return "\n".join(lines)


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    headers = list(rows[0].keys())
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=headers, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def evaluate_run(
    *,
    babis: dict[str, Any],
    giannis: dict[str, Any],
    fundamental_packet: dict[str, Any],
    playbook_payload: dict[str, Any],
) -> dict[str, Any]:
    """Score every playbook against one run's inputs. Pure: writes nothing.

    The live review and shadow replays share this path, so a replay with no
    overrides reproduces exactly what the live agent would decide.
    """
    playbooks = as_list(playbook_payload.get("playbooks"))
    if not playbooks:
        raise SystemExit("No playbooks found for Periklis.")
    if playbook_payload.get("daily_strategies_enabled"):
        playbooks = playbooks + strategy_signals.playbooks_for("periklis")
    run_id = require_compatible_runs(babis, giannis)
    fundamental_research_packet.validate_packet(fundamental_packet, expected_run_id=run_id)
    run_ts = str(giannis.get("run_timestamp") or babis.get("run_timestamp") or datetime.now(timezone.utc).isoformat())
    babis_rows = {str(row.get("ticker") or "").upper(): row for row in as_list(babis.get("shortlist"))}

    merged_rows: list[dict[str, Any]] = []
    for giannis_row in as_list(giannis.get("results")):
        ticker = str(giannis_row.get("ticker") or "").upper()
        if ticker:
            merged_rows.append({**babis_rows.get(ticker, {}), **giannis_row})
    merged_rows, fundamental_blocked = fundamental_research_packet.attach_fundamental_handoff(
        merged_rows, fundamental_packet
    )
    rows = [
        # Giannis's periodic-report freshness travels with the row for Koulis (primary_data_freshness.py)
        {**fundamental_research_packet.enforce_execution_gate(evaluate_playbook(playbook, merged)),
         "fundamentals_fresh": merged.get("fundamentals_fresh")}
        for playbook in playbooks
        for merged in merged_rows
    ]
    return {"run_id": run_id, "run_timestamp": run_ts, "merged_rows": merged_rows,
            "fundamental_blocked": fundamental_blocked, "rows": rows}


def command_review(args: argparse.Namespace) -> None:
    allowlist = load_json(Path(args.allowlist))
    playbook_path = Path(args.playbooks)
    playbook_payload = load_json(playbook_path)
    playbooks = as_list(playbook_payload.get("playbooks"))
    if not playbooks:
        raise SystemExit("No playbooks found for Periklis.")

    babis = load_json(Path(args.babis))
    giannis = load_json(Path(args.giannis))
    fundamental_packet = load_json(Path(args.fundamental_packet))
    evaluation = evaluate_run(babis=babis, giannis=giannis, fundamental_packet=fundamental_packet,
                              playbook_payload=playbook_payload)
    run_id = evaluation["run_id"]
    run_ts = evaluation["run_timestamp"]
    merged_rows = evaluation["merged_rows"]
    fundamental_blocked = evaluation["fundamental_blocked"]

    catalog_path = Path(str(playbook_payload.get("catalog_file") or "playbook_catalog_v2.json"))
    if not catalog_path.is_absolute():
        catalog_path = playbook_path.parent / catalog_path
    catalog = playbook_framework.load_catalog(catalog_path)
    portfolio = as_list(playbook_payload.get("playbook_portfolio"))
    perspectives = playbook_framework.evaluate_portfolio(catalog, portfolio, merged_rows, args.agent_name)

    approved_sources: list[str] = []
    for playbook in playbooks:
        approved_sources.extend(verify_sources(playbook, allowlist))
    rows = evaluation["rows"]

    rows_sorted = sorted(rows, key=lambda item: item["signal_score"], reverse=True)
    trade_plan_summary = trader_trade_plan.attach_trade_plans(
        rows_sorted,
        catalog,
        playbook_payload.get("paper_sizing_policy") or {},
        args.agent_name,
        run_id,
    )
    playbook_framework.attach_alternative_perspectives(rows_sorted, perspectives)
    framework = playbook_framework.framework_summary(catalog, portfolio, perspectives)
    out_prefix = Path(args.out_prefix)
    out_json = Path(f"{out_prefix}_latest.json")
    out_md = Path(f"{out_prefix}_latest.md")
    out_csv = Path(f"{out_prefix}_latest.csv")
    payload = attach_run_context(
        {
            "run_timestamp": run_ts,
            "agent_name": args.agent_name,
            "count": len(rows_sorted),
            "results": rows_sorted,
            "playbook_framework": framework,
            "playbook_perspectives": perspectives,
            "paper_trade_plan_summary": trade_plan_summary,
            "fundamental_packet_hash": fundamental_packet.get("packet_hash"),
            "fundamental_gate": {
                "schema": "ecosystem.fundamental_to_technical_gate.v1",
                "input_count": len(as_list(giannis.get("results"))),
                "technical_review_count": len(merged_rows),
                "blocked_count": len(fundamental_blocked),
                "blocked": fundamental_blocked,
            },
        },
        stage="periklis_strategy",
        run_id=run_id,
        source_run_ids=[
            str(babis.get("run_id") or ""),
            str(giannis.get("run_id") or ""),
            str(fundamental_packet.get("run_id") or ""),
        ],
    )
    out_json.write_text(as_json(payload), encoding="utf-8")
    out_md.write_text(
        render_md(
            run_ts,
            args.agent_name,
            rows_sorted,
            sorted(set(approved_sources)),
            framework=framework,
            perspectives=perspectives,
        ),
        encoding="utf-8",
    )
    write_csv(out_csv, rows_sorted)
    store_signals(Path(args.db), run_ts, rows_sorted, args.agent_name)
    print(f"[OUTPUT] JSON: {out_json}")
    print(f"[OUTPUT] MD: {out_md}")
    print(f"[OUTPUT] CSV: {out_csv}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Periklis strategy agent for swing-only technical confirmation.")
    parser.add_argument("--db", default=str(DEFAULT_DB_PATH))
    subparsers = parser.add_subparsers(dest="command", required=True)

    review = subparsers.add_parser("review", help="Evaluate the latest watchlist against Periklis playbooks.")
    review.add_argument("--babis", default="output/babis_shortlist_latest.json")
    review.add_argument("--giannis", default="output/giannis_rule_based_latest.json")
    review.add_argument(
        "--fundamental-packet",
        default="output/fundamental_research_packet_latest.json",
    )
    review.add_argument("--allowlist", default=str(DEFAULT_ALLOWLIST_PATH))
    review.add_argument("--playbooks", default=str(DEFAULT_PLAYBOOK_PATH))
    review.add_argument("--out-prefix", default="output/periklis_strategy")
    review.add_argument("--agent-name", default="periklis")
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    if args.command == "review":
        command_review(args)
        return 0
    parser.error("unknown command")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
