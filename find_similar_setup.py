from __future__ import annotations

import argparse
import math
from pathlib import Path
from typing import Any
from common import load_json


def as_list(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [row for row in value if isinstance(row, dict)]


def to_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(numeric):
        return None
    return numeric


def similarity_score(target: dict[str, Any], row: dict[str, Any]) -> tuple[float, list[str]]:
    score = 0.0
    reasons: list[str] = []

    if row.get("instrument_type") != "common":
        return -999.0, ["not common"]

    if row.get("risk_level") == "Low":
        score += 2.0
        reasons.append("Low risk")
    elif row.get("risk_level") == "Medium":
        score += 0.6
        reasons.append("Medium risk")
    else:
        score -= 3.0
        reasons.append("High risk")

    if bool(row.get("fresh_primary_data", True)):
        score += 1.0
        reasons.append("fresh data")
    else:
        score -= 2.0
        reasons.append("stale data")

    if row.get("catalyst_bucket") == target.get("catalyst_bucket"):
        score += 1.2
        reasons.append("same catalyst type")

    timing = str(row.get("technical_timing") or "neutral")
    if timing in {"near_lows_reversal", "early_accumulation", "pre_run_watch", "pullback_watch"}:
        score += 1.4
        reasons.append(timing)
    elif timing == "neutral":
        score += 0.8
        reasons.append("neutral timing")
    elif timing == "already_extended":
        score -= 2.0
        reasons.append("already extended")

    comparisons = [
        ("rsi_14", 0.8, 25.0),
        ("return_20d_pct", 0.8, 25.0),
        ("price_position_60d_pct", 0.7, 50.0),
        ("volume_ratio_20", 0.4, 2.0),
    ]
    for name, weight, scale in comparisons:
        current = to_float(row.get(name))
        target_value = to_float(target.get(name))
        if current is None or target_value is None:
            continue
        score += max(0.0, weight * (1.0 - abs(current - target_value) / scale))

    giannis = to_float(row.get("score_final"))
    petros = to_float(row.get("petros_score"))
    if giannis is not None:
        score += min(1.5, giannis / 10.0)
    if petros is not None:
        score += min(1.5, petros / 10.0)

    flags = row.get("dilution_flags") or []
    if isinstance(flags, list) and flags:
        score -= min(1.5, 0.35 * len(flags))
        reasons.append("flags=" + ",".join(str(flag) for flag in flags[:3]))

    return score, reasons


def fmt(value: Any) -> str:
    numeric = to_float(value)
    if numeric is None:
        return "N/A" if value in {None, ""} else str(value)
    return f"{numeric:.2f}"


def main() -> int:
    parser = argparse.ArgumentParser(description="Find setups similar to a target ticker.")
    parser.add_argument("ticker")
    parser.add_argument("--babis", default="output/babis_shortlist_latest.json")
    parser.add_argument("--giannis", default="output/giannis_rule_based_latest.json")
    parser.add_argument("--petros", default="output/petros_review_latest.json")
    parser.add_argument("--limit", type=int, default=15)
    args = parser.parse_args()

    ticker = args.ticker.upper()
    babis_payload = load_json(Path(args.babis))
    giannis_payload = load_json(Path(args.giannis))
    petros_payload = load_json(Path(args.petros))

    babis = {str(row.get("ticker") or "").upper(): row for row in as_list(babis_payload.get("shortlist"))}
    giannis = {str(row.get("ticker") or "").upper(): row for row in as_list(giannis_payload.get("results"))}
    petros = {str(row.get("ticker") or "").upper(): row for row in as_list(petros_payload.get("results"))}

    if ticker not in giannis:
        print(f"{ticker} not found in latest Giannis output.")
        return 1

    target = {**babis.get(ticker, {}), **giannis.get(ticker, {}), **petros.get(ticker, {})}
    print(f"Target: {ticker} {target.get('company') or ''}")
    print(
        "Profile: "
        f"risk={target.get('risk_level')} "
        f"timing={target.get('technical_timing')} "
        f"fresh={target.get('fresh_primary_data', True)} "
        f"catalyst={target.get('catalyst_bucket')} "
        f"petros={fmt(target.get('petros_score'))} "
        f"giannis={fmt(target.get('score_final'))}"
    )
    print("")

    scored: list[tuple[float, str, dict[str, Any], list[str]]] = []
    for symbol, row in giannis.items():
        if symbol == ticker:
            continue
        merged = {**babis.get(symbol, {}), **row, **petros.get(symbol, {})}
        score, reasons = similarity_score(target, merged)
        if score > -900:
            scored.append((score, symbol, merged, reasons))

    for score, symbol, row, reasons in sorted(scored, key=lambda item: item[0], reverse=True)[: args.limit]:
        flags = row.get("dilution_flags") or []
        print(
            f"{symbol:6} sim={score:5.2f} "
            f"petros={fmt(row.get('petros_score'))} "
            f"giannis={fmt(row.get('score_final'))} "
            f"risk={row.get('risk_level')} "
            f"timing={row.get('technical_timing')} "
            f"fresh={row.get('fresh_primary_data', True)} "
            f"price={fmt(row.get('share_price'))} "
            f"rsi={fmt(row.get('rsi_14'))} "
            f"20d={fmt(row.get('return_20d_pct'))}% "
            f"60d_pos={fmt(row.get('price_position_60d_pct'))} "
            f"flags={flags} "
            f"{row.get('company') or ''} | {'; '.join(reasons[:4])}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
