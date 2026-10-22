from __future__ import annotations
from instrument_type_rules import instrument_profile, normalize_instrument_type

import argparse
import csv
import json
from datetime import datetime
from pathlib import Path
from typing import Any

from run_context import attach_run_context, resolve_run_id
import periodic_filing
import quality_factors
import sec_fundamental_ratios
import unknown_data
from symbol_classification import classify_symbol


def to_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def age_days(value: Any) -> int | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        try:
            parsed = datetime.strptime(text[:10], "%Y-%m-%d")
        except ValueError:
            return None
    return max(0, (datetime.now().date() - parsed.date()).days)


def classify_instrument(row: dict[str, Any]) -> dict[str, Any]:
    existing = normalize_instrument_type(row.get("instrument_type"))
    if existing:
        return {
            "instrument_type": existing,
            "complex_security": bool(row.get("complex_security", existing != "common")),
            "instrument_flags": row.get("instrument_flags", []),
        }
    return instrument_profile(row.get("ticker"), row.get("company"))


CATALYST_MODES = ("content_events", "legacy")
CATALYST_DIRECTION = {"positive": 1.0, "negative": -1.0}
CATALYST_CONFIDENCE = {"high": 1.0, "medium": 0.75, "low": 0.45}


def content_catalyst(row: dict[str, Any]) -> tuple[float, dict[str, Any] | None]:
    """Catalyst points from content-bearing SEC events only (rationale: docs/REDESIGN_NOTES_2026-10.md, 4δ).

    Only an 8-K item with a known direction says something about the business. A form on its own (Form 4,
    424B3, 25-NSE, EFFECT, S-3) does not: a 424B3 may be a debt prospectus, a 25-NSE may delist a note, and a
    Form 4 has no known buy/sell direction. News keywords are a secondary source and do not count either. The
    strongest event in the window decides, so a new insignificant filing no longer replaces a material one.
    No content event is no catalyst (0), not a negative one.
    """
    best: tuple[float, dict[str, Any] | None] = (0.0, None)
    for event in row.get("catalyst_evidence") or []:
        if not isinstance(event, dict) or event.get("source_kind") != "sec_item":
            continue
        direction = CATALYST_DIRECTION.get(str(event.get("direction") or "").strip().lower())
        attention = to_float(event.get("attention_score"))
        if direction is None or attention is None:
            continue
        confidence = CATALYST_CONFIDENCE.get(str(event.get("classification_confidence") or "high").lower(), 0.45)
        points = round(2.0 * clamp(attention / 100.0, 0.0, 1.0) * direction * confidence, 2)
        if abs(points) > abs(best[0]):
            best = (points, event)
    return best


def score_row(row: dict[str, Any], run_id: str | None = None, sec_first: bool = False,
              catalyst_mode: str = "content_events") -> dict[str, Any]:
    classification = classify_symbol(row)
    share_price = to_float(row.get("share_price"))
    cash = to_float(row.get("cash"))
    debt = to_float(row.get("debt"))
    fcf = to_float(row.get("cash_burn_proxy_fcf"))
    ocf = to_float(row.get("cash_burn_proxy_ocf"))
    risk_level = str(row.get("risk_level") or "Medium").strip()
    catalyst_bucket = str(row.get("catalyst_bucket") or "UNKNOWN").strip()
    catalyst_category_id = str(row.get("catalyst_category_id") or "").strip()
    catalyst_attention_score = to_float(row.get("catalyst_attention_score"))
    catalyst_direction = str(row.get("catalyst_direction") or "unknown").strip().lower()
    catalyst_classification_confidence = str(
        row.get("catalyst_classification_confidence") or "low"
    ).strip().lower()
    fundamental_attention_score = to_float(row.get("fundamental_attention_score"))
    if fundamental_attention_score is None:
        fundamental_attention_score = to_float(row.get("fundamental_score"))
    if fundamental_attention_score is None:
        # Compatibility only for historical Babis artifacts. New fundamental-first
        # handoffs always provide an explicit fundamental attention score.
        fundamental_attention_score = to_float(row.get("composite_score")) or 0.0
    # SEC-first scoring: the same rules re-evaluated with SEC values and yfinance only as a labelled fallback.
    # The SEC-first result is always reported; it drives the score only when ``sec_first`` is on (owner's switch).
    sec_values = row.get("sec_ratio_values")
    sec_first_details: list[dict[str, Any]] | None = None
    sec_first_score: float | None = None
    value_sources: dict[str, int] | None = None
    score_basis = "babis_stage1"
    if isinstance(sec_values, dict) and row.get("fundamental_rule_details"):
        sec_first_details, sec_first_score, value_sources = sec_fundamental_ratios.sec_first_rules(
            list(row["fundamental_rule_details"]), sec_values)
        if sec_first:
            fundamental_attention_score = sec_first_score
            score_basis = "sec_first"
    composite_score = to_float(row.get("composite_score")) or fundamental_attention_score
    smart_money_signal = str(row.get("smart_money_signal") or "neutral").strip()
    smart_money_score = to_float(row.get("smart_money_score")) or 0.0
    institution_ownership = to_float(row.get("institution_ownership"))
    short_percent_of_float = to_float(row.get("short_percent_of_float"))
    open_market_buy_count = to_float(row.get("open_market_buy_count")) or 0.0
    open_market_sell_count = to_float(row.get("open_market_sell_count")) or 0.0
    dilution_flags = row.get("dilution_flags", [])
    data_age_flags = row.get("data_age_flags", [])
    unknown_annotated = unknown_data.annotate_row(row, "giannis", run_id=run_id)
    unknown_fields = list(unknown_annotated.get("unknown_fields") or [])
    unknown_events = list(unknown_annotated.get("unknown_events") or [])
    upstream_unknown_events = list(unknown_annotated.get("upstream_unknown_events") or [])
    unknown_summary = dict(unknown_annotated.get("unknown_summary") or {})
    instrument = classify_instrument(row)
    instrument_type = str(instrument["instrument_type"])
    complex_security = bool(instrument["complex_security"])
    instrument_flags = instrument["instrument_flags"]
    if isinstance(dilution_flags, list):
        for flag in instrument_flags:
            if flag not in dilution_flags:
                dilution_flags.append(flag)
    if isinstance(data_age_flags, list):
        for flag in data_age_flags:
            if flag not in dilution_flags:
                dilution_flags.append(flag)

    sec_age = to_float(row.get("sec_filing_age_days"))
    if sec_age is None:
        parsed_age = age_days(row.get("catalyst_date"))
        sec_age = None if parsed_age is None else float(parsed_age)
    fresh_primary_data = bool(row.get("fresh_primary_data", True))
    if sec_age is None or sec_age > 30:
        fresh_primary_data = False
        if "no_fresh_sec_filing_30d" not in dilution_flags:
            dilution_flags.append("no_fresh_sec_filing_30d")

    # Fundamental freshness is the age of the last periodic report (10-K/10-Q/20-F/40-F), not of any filing:
    # a company that filed its 10-Q six weeks ago has current ratios even if it filed nothing in 30 days.
    # Unknown stays unknown: without the periodic date the old any-filing rule applies unchanged.
    periodic_age = to_float(row.get("periodic_filing_age_days"))
    periodic_form = str(row.get("periodic_filing_form") or "") or None
    fundamentals_fresh: bool | None = None
    if periodic_age is not None:
        fundamentals_fresh = periodic_age <= periodic_filing.freshness_limit(periodic_form)
        if not fundamentals_fresh and "stale_periodic_filing" not in dilution_flags:
            dilution_flags.append("stale_periodic_filing")

    net_cash = None if cash is None or debt is None else cash - debt
    liquidity_points = 0.0
    if net_cash is not None:
        liquidity_points += 1.5 if net_cash > 0 else 0.0
    if fcf is not None and fcf > 0:
        liquidity_points += 1.0
    if ocf is not None and ocf > 0:
        liquidity_points += 1.0
    if cash is not None and cash > 0:
        liquidity_points += 0.5
    liquidity_points = clamp(liquidity_points, 0.0, 4.0)

    if catalyst_category_id and catalyst_attention_score is not None:
        attention = clamp(catalyst_attention_score / 100.0, 0.0, 1.0)
        direction_multiplier = {
            "positive": 1.0,
            "mixed": 0.3,
            "unknown": 0.0,
            "negative": -1.0,
        }.get(catalyst_direction, 0.0)
        confidence_multiplier = {
            "high": 1.0,
            "medium": 0.75,
            "low": 0.45,
        }.get(catalyst_classification_confidence, 0.45)
        catalyst_points = round(2.0 * attention * direction_multiplier * confidence_multiplier, 2)
        # A filing that could not be classified is weak evidence and costs 0.35. A company that filed nothing in the
        # window has no catalyst at all, which is the normal state and says nothing about its fundamentals.
        filing_present = sec_age is not None or bool(row.get("catalyst_evidence"))
        if catalyst_category_id == "unknown" and filing_present:
            catalyst_points = min(catalyst_points, -0.35)
    else:
        # Compatibility path for historical shortlist artifacts created before taxonomy v1.
        catalyst_points = 0.0
        if catalyst_bucket == "Contract / Partnership":
            catalyst_points = 2.0
        elif catalyst_bucket == "Pre-Earnings":
            catalyst_points = 1.6
        elif catalyst_bucket == "Certification / Approval":
            catalyst_points = 2.0
        elif catalyst_bucket == "Financing Relief / Balance-Sheet Event":
            catalyst_points = 1.8
        elif catalyst_bucket == "Squeeze / Low-Float Flow":
            catalyst_points = 0.6
        elif catalyst_bucket in {"UNKNOWN", "Unclassified / Insufficient Evidence"}:
            catalyst_points = -0.35
    legacy_catalyst_points = catalyst_points
    catalyst_event: dict[str, Any] | None = None
    if catalyst_mode == "content_events" and catalyst_category_id:
        catalyst_points, catalyst_event = content_catalyst(row)

    smart_money_points = 0.0
    if smart_money_signal == "bullish":
        smart_money_points += 0.55
    elif smart_money_signal == "bearish":
        smart_money_points -= 0.35
    elif smart_money_signal == "mixed":
        smart_money_points += 0.1
    if institution_ownership is not None and institution_ownership >= 0.5:
        smart_money_points += 0.2
    if short_percent_of_float is not None and short_percent_of_float >= 0.08:
        smart_money_points += 0.1
    if open_market_buy_count > open_market_sell_count and open_market_buy_count > 0:
        smart_money_points += 0.2
    elif open_market_sell_count > open_market_buy_count and open_market_sell_count > 0:
        smart_money_points -= 0.15
    smart_money_points = clamp(smart_money_points + smart_money_score * 0.25, -0.5, 1.0)

    risk_penalty = 0.0
    if risk_level == "High":
        risk_penalty += 2.0
    elif risk_level == "Medium":
        risk_penalty += 1.0
    if share_price is not None and share_price < 1.0:
        risk_penalty += 1.4
    elif share_price is not None and share_price < 2.0:
        risk_penalty += 0.5
    # The downstream flags (fresh_primary_data, no_fresh_sec_filing_30d) stay as they were; only the score's
    # penalty stops counting a recent-filing gap against a company whose periodic report is current.
    penalised_flags = [f for f in dilution_flags if not (fundamentals_fresh and f == "no_fresh_sec_filing_30d")]
    risk_penalty += min(2.0, 0.35 * len(penalised_flags))
    # Same reason for the unknown-data penalty: with a known periodic report, "no filing of any kind in 30 days"
    # (fresh_primary_data) is not missing fundamental data. The unknown event itself stays reported.
    penalty_units = float(unknown_summary.get("penalty_units") or 0.0)
    if fundamentals_fresh is not None and catalyst_mode == "content_events":
        penalty_units = sum(float(event.get("penalty_weight") or 0.0) for event in unknown_events
                            if event.get("field") != "fresh_primary_data")
    risk_penalty += min(1.0, 0.25 * penalty_units)
    if fundamentals_fresh is None:
        if not fresh_primary_data:
            risk_penalty += 1.2
    elif not fundamentals_fresh:
        risk_penalty += 1.2
    if complex_security:
        risk_penalty += 1.0
    if instrument_type in {"warrant", "unit", "right"}:
        risk_penalty += 0.75

    base_points = clamp(fundamental_attention_score / 25.0, 0.0, 4.0)
    final_score = clamp(base_points + liquidity_points + catalyst_points + smart_money_points - risk_penalty, 0.0, 10.0)

    if complex_security and final_score >= 4.5:
        tier = "Complex Security Risk"
    elif final_score >= 7.5 and risk_level != "High":
        tier = "Core Watchlist"
    elif final_score >= 6.0:
        tier = "Selective Watchlist"
    elif final_score >= 4.5:
        tier = "Speculative Monitor"
    else:
        tier = "Avoid For Now"

    return {
        "ticker": row.get("ticker"),
        "company": row.get("company"),
        "sector": classification["sector_label"],
        "industry": classification["industry_label"],
        "sector_id": classification["sector_id"],
        "industry_id": classification["industry_id"],
        "classification_schema": classification["classification_schema"],
        "sector_taxonomy_version": classification["sector_taxonomy_version"],
        "classification_source": classification["classification_source"],
        "classification_confidence": classification["classification_confidence"],
        "classification_status": classification["classification_status"],
        "index_analysis_eligible": classification["index_analysis_eligible"],
        "rank": row.get("rank"),
        "share_price": share_price,
        "market_cap": to_float(row.get("market_cap")),
        "catalyst_bucket": catalyst_bucket,
        "catalyst_category_id": catalyst_category_id or None,
        "catalyst_legacy_bucket": row.get("catalyst_legacy_bucket"),
        "main_catalyst": row.get("main_catalyst"),
        "catalyst_direction": catalyst_direction,
        "catalyst_category_weight": to_float(row.get("catalyst_category_weight")),
        "catalyst_attention_score": catalyst_attention_score,
        "catalyst_priority": row.get("catalyst_priority"),
        "catalyst_classification_confidence": catalyst_classification_confidence,
        "catalyst_evidence_confidence_score": to_float(row.get("catalyst_evidence_confidence_score")),
        "catalyst_recency_score": to_float(row.get("catalyst_recency_score")),
        "catalyst_specificity_score": to_float(row.get("catalyst_specificity_score")),
        "catalyst_reader_question": row.get("catalyst_reader_question"),
        "catalyst_classification_reason": row.get("catalyst_classification_reason"),
        "catalyst_evidence": row.get("catalyst_evidence", []),
        "long_catalyst_score": to_float(row.get("long_catalyst_score")),
        "short_catalyst_score": to_float(row.get("short_catalyst_score")),
        "catalyst_trade_bias": row.get("catalyst_trade_bias"),
        "catalyst_trade_lenses": row.get("catalyst_trade_lenses", {}),
        "catalyst_taxonomy_version": row.get("catalyst_taxonomy_version"),
        "catalyst_scoring_status": row.get("catalyst_scoring_status"),
        "risk_level": risk_level,
        "instrument_type": instrument_type,
        "complex_security": complex_security,
        "instrument_flags": instrument_flags,
        "composite_score": composite_score,
        "analysis_lane": "fundamental_quality",
        "technical_inputs_used": False,
        "fundamental_attention_score": fundamental_attention_score,
        "fundamental_score_basis": score_basis,
        "fundamental_score_sec_first": sec_first_score,
        "fundamental_value_sources": value_sources,
        "fundamental_rule_details_sec_first": sec_first_details,
        "fundamental_coverage": to_float(row.get("fundamental_coverage")),
        "fundamental_rule_details": row.get("fundamental_rule_details", []),
        "revenue_ttm": to_float(row.get("revenue_ttm")),
        "enterprise_value": to_float(row.get("enterprise_value")),
        "gross_margin": to_float(row.get("gross_margin")),
        "operating_margin": to_float(row.get("operating_margin")),
        "profit_margin": to_float(row.get("profit_margin")),
        "revenue_growth": to_float(row.get("revenue_growth")),
        "earnings_growth": to_float(row.get("earnings_growth")),
        "roe": to_float(row.get("roe")),
        "roa": to_float(row.get("roa")),
        "forward_pe": to_float(row.get("forward_pe")),
        "trailing_pe": to_float(row.get("trailing_pe")),
        "price_to_sales": to_float(row.get("price_to_sales")),
        "price_to_book": to_float(row.get("price_to_book")),
        "peg_ratio": to_float(row.get("peg_ratio")),
        "current_ratio": to_float(row.get("current_ratio")),
        "quick_ratio": to_float(row.get("quick_ratio")),
        "shares_outstanding": to_float(row.get("shares_outstanding")),
        "net_income_ttm": to_float(row.get("net_income_ttm")),
        "cash": cash,
        "debt": debt,
        "net_cash": net_cash,
        "fcf_proxy": fcf,
        "ocf_proxy": ocf,
        "dilution_flags": dilution_flags,
        "fresh_primary_data": fresh_primary_data,
        "sec_filing_age_days": sec_age,
        "periodic_filing_age_days": periodic_age,
        "periodic_filing_form": periodic_form,
        "fundamentals_fresh": fundamentals_fresh,
        "data_age_flags": data_age_flags,
        "unknown_fields": unknown_fields,
        "unknown_events": unknown_events,
        "upstream_unknown_events": upstream_unknown_events,
        "unknown_summary": unknown_summary,
        "unknown_contract_schema": unknown_annotated.get("unknown_contract_schema"),
        "unknown_contract_version": unknown_annotated.get("unknown_contract_version"),
        "unknown_contract_sha256": unknown_annotated.get("unknown_contract_sha256"),
        "score_base": round(base_points, 2),
        "score_liquidity": round(liquidity_points, 2),
        "score_catalyst": round(catalyst_points, 2),
        "score_catalyst_legacy": round(legacy_catalyst_points, 2),
        "catalyst_scoring_mode": catalyst_mode if catalyst_category_id else "legacy_bucket",
        "giannis_catalyst_event": None if catalyst_event is None else {
            key: catalyst_event.get(key) for key in
            ("category_id", "subcategory_id", "event_name", "event_date", "sec_form", "sec_item", "direction",
             "attention_score", "source_url")},
        "score_smart_money": round(smart_money_points, 2),
        "score_penalty": round(risk_penalty, 2),
        "score_final": round(final_score, 2),
        "giannis_tier": tier,
        "smart_money_signal": smart_money_signal,
        "smart_money_score": round(smart_money_score, 2),
        "institution_ownership": institution_ownership,
        "short_percent_of_float": short_percent_of_float,
        "open_market_buy_count": int(open_market_buy_count),
        "open_market_sell_count": int(open_market_sell_count),
    }


def render_md(run_ts: str, source_file: str, rows: list[dict[str, Any]]) -> str:
    total = len(rows)
    core = [r for r in rows if r["giannis_tier"] == "Core Watchlist"]
    selective = [r for r in rows if r["giannis_tier"] == "Selective Watchlist"]
    speculative = [r for r in rows if r["giannis_tier"] == "Speculative Monitor"]
    avoid = [r for r in rows if r["giannis_tier"] == "Avoid For Now"]
    complex_rows = [r for r in rows if r.get("complex_security")]

    lines = [
        "# Γιαννης Rule-Based Report (No LLM)",
        "",
        f"- Run timestamp: {run_ts}",
        f"- Source shortlist: {source_file}",
        f"- Coverage: {total} symbols",
        "",
        "## Tier Summary",
        "",
        f"- Core Watchlist: {len(core)}",
        f"- Selective Watchlist: {len(selective)}",
        f"- Speculative Monitor: {len(speculative)}",
        f"- Complex Security Risk: {len([r for r in rows if r['giannis_tier'] == 'Complex Security Risk'])}",
        f"- Avoid For Now: {len(avoid)}",
        f"- Complex securities flagged: {len(complex_rows)}",
        "",
        "## Top 30 By Giannis Score",
        "",
    ]
    top30 = sorted(rows, key=lambda x: x["score_final"], reverse=True)[:30]
    for idx, r in enumerate(top30, start=1):
        lines.append(
            f"{idx}. {r['ticker']} ({r['company']}) | Score: {r['score_final']}/10 | Tier: {r['giannis_tier']} | "
            f"Risk: {r['risk_level']} | Instrument: {r.get('instrument_type', 'common')} | "
            f"Catalyst: {r['catalyst_bucket']} | direction={r.get('catalyst_direction', 'unknown')} | "
            f"attention={r.get('catalyst_attention_score', 'N/A')} | "
            f"Long/Short={r.get('long_catalyst_score', 'N/A')}/{r.get('short_catalyst_score', 'N/A')} | "
            f"SmartMoney: {r.get('smart_money_signal', 'neutral')} "
            f"| Price: {r['share_price']}"
        )

    lines.extend(["", "## Complex Securities / Instrument Risk", ""])
    for r in sorted(complex_rows, key=lambda x: x["score_final"], reverse=True)[:30]:
        flags = ", ".join(r.get("instrument_flags", [])) or "complex_security"
        lines.append(
            f"- {r['ticker']} ({r['company']}) | Score: {r['score_final']}/10 | "
            f"Instrument: {r.get('instrument_type')} | Flags: {flags} | Tier: {r['giannis_tier']}"
        )

    lines.extend(["", "## High-Risk Bucket (Top 30 to avoid)", ""])
    worst30 = sorted(rows, key=lambda x: x["score_final"])[:30]
    for r in worst30:
        flags = ", ".join(r.get("dilution_flags", [])) or "none"
        lines.append(
            f"- {r['ticker']} ({r['company']}) | Score: {r['score_final']}/10 | Risk: {r['risk_level']} | "
            f"Price: {r['share_price']} | Dilution flags: {flags}"
        )

    lines.extend(
        [
            "",
            "## Method Note",
            "",
            "- Deterministic scoring from shortlist fields only.",
            "- No LLM generation used in this report.",
            "- Treat as triage layer before deep manual due diligence.",
        ]
    )
    return "\n".join(lines)


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    headers = list(rows[0].keys())
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=headers)
        writer.writeheader()
        writer.writerows(rows)


def sec_first_enabled(choice: str, config_path: Path = Path("scanner_config.json")) -> bool:
    if choice in ("on", "off"):
        return choice == "on"
    try:
        config = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return bool((config.get("pipeline") or {}).get("giannis_sec_first", False))


def catalyst_mode_from(choice: str, config_path: Path = Path("scanner_config.json")) -> str:
    if choice in CATALYST_MODES:
        return choice
    try:
        config = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return "content_events"
    mode = str((config.get("pipeline") or {}).get("giannis_catalyst_mode") or "content_events")
    return mode if mode in CATALYST_MODES else "content_events"


def main() -> int:
    parser = argparse.ArgumentParser(description="Giannis non-LLM fundamental triage from Babis shortlist.")
    parser.add_argument("--shortlist", default="output/babis_shortlist_latest.json")
    parser.add_argument("--out-prefix", default="output/giannis_rule_based")
    parser.add_argument("--sec-first", choices=["config", "on", "off"], default="config",
                        help="score basis; config reads pipeline.giannis_sec_first in scanner_config.json")
    parser.add_argument("--catalyst-mode", choices=["config", *CATALYST_MODES], default="config",
                        help="catalyst term; config reads pipeline.giannis_catalyst_mode in scanner_config.json")
    args = parser.parse_args()

    shortlist_path = Path(args.shortlist)
    if not shortlist_path.exists():
        print(f"Missing shortlist file: {shortlist_path}")
        return 1
    data = json.loads(shortlist_path.read_text(encoding="utf-8"))
    shortlist = data.get("shortlist", [])
    if not isinstance(shortlist, list) or not shortlist:
        print("Shortlist is empty.")
        return 1

    run_id = resolve_run_id(data)
    annotated = periodic_filing.annotate_rows(shortlist)
    print(f"[INFO] periodic filing age known for {annotated} of {len(shortlist)} rows")
    with_sec = sec_fundamental_ratios.annotate_rows(shortlist)
    sec_first = sec_first_enabled(args.sec_first)
    print(f"[INFO] SEC ratios available for {with_sec} of {len(shortlist)} rows; "
          f"score basis: {'SEC-first' if sec_first else 'Babis stage-1 (SEC-first reported in shadow)'}")
    catalyst_mode = catalyst_mode_from(args.catalyst_mode)
    print(f"[INFO] catalyst term: {catalyst_mode}")
    scored = [score_row(row, run_id=run_id, sec_first=sec_first, catalyst_mode=catalyst_mode) for row in shortlist]
    # Quality factors from the accounting literature (Piotroski, Altman Z'', Beneish), in shadow: reported next to
    # the score, judged against outcomes by the learning checks, never part of the score (owner's decision 2026-10-12).
    try:
        quality = quality_factors.QualityFactorSource()
        for row in scored:
            row["quality_factors"] = quality.factors(str(row.get("ticker") or ""), str(row.get("sector") or ""))
        quality.save()
        print(f"[INFO] quality factors (shadow) for {sum(1 for r in scored if r.get('quality_factors'))} of {len(scored)} rows")
    except Exception as exc:  # noqa: BLE001 - shadow evidence never stops the scoring
        print(f"[WARN] quality factors not computed: {type(exc).__name__}")
    scored_sorted = sorted(scored, key=lambda x: x["score_final"], reverse=True)
    run_ts = data.get("run_timestamp") or ""
    unknown_events = [
        event for row in scored_sorted for event in (row.get("unknown_events") or [])
    ]
    unknown_summary = unknown_data.summarize_events(
        unknown_events,
        declared_fields=len(unknown_data.load_contract()["agents"]["giannis"]["fields"]) * len(scored_sorted),
    )

    out_prefix = Path(args.out_prefix)
    out_json = Path(f"{out_prefix}_latest.json")
    out_csv = Path(f"{out_prefix}_latest.csv")
    out_md = Path(f"{out_prefix}_latest.md")

    out_payload = attach_run_context({
        "run_timestamp": run_ts,
        "source_shortlist": str(shortlist_path),
        "count": len(scored_sorted),
        "data_quality_summary": {"unknown_data": unknown_summary},
        "results": scored_sorted,
    }, stage="giannis_rule_based", run_id=run_id, source_run_ids=[str(data.get("run_id") or "")])
    out_json.write_text(json.dumps(out_payload, ensure_ascii=False, indent=2), encoding="utf-8")
    write_csv(out_csv, scored_sorted)
    out_md.write_text(render_md(run_ts, str(shortlist_path), scored_sorted), encoding="utf-8")

    print(f"[OUTPUT] JSON: {out_json}")
    print(f"[OUTPUT] CSV: {out_csv}")
    print(f"[OUTPUT] MD: {out_md}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
