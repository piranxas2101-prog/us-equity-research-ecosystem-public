"""Pure, side-effect-free helpers shared by the market scanner.

These functions were extracted verbatim from ``us_equity_monitor`` as the first
step of splitting that module into smaller pieces. They perform no network,
filesystem or wall-clock-dependent work beyond ``now_athens``/``age_days``
(which read the current time by design), so they are safe to import from
anywhere without pulling in the scanner's heavy provider dependencies.

``us_equity_monitor`` re-imports every name defined here, so existing callers
that do ``from us_equity_monitor import to_float`` keep working unchanged.
"""
from __future__ import annotations
from instrument_type_rules import instrument_profile

import copy
import math
import re
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

ATHENS_TZ = ZoneInfo("Europe/Athens")


def to_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(numeric):
        return None
    return numeric


def normalize_symbol(symbol: str) -> str:
    return symbol.strip().upper()


def provider_symbol_candidates(symbol: str, provider: str) -> list[str]:
    """Return provider-native aliases while preserving the canonical ticker."""
    ticker = normalize_symbol(symbol)
    name = str(provider or "").strip().lower()
    candidates: list[str] = []
    if "$" in ticker:
        base, suffix = ticker.split("$", 1)
        if base and suffix:
            if name in {"alpaca", "polygon"}:
                candidates.append(f"{base}.PR{suffix}")
            elif name in {"yfinance", "yahoo", "openbb:yfinance"}:
                candidates.append(f"{base}-P{suffix}")
    elif ticker.endswith(".W"):
        base = ticker[:-2]
        candidates.append(f"{base}.WS" if name in {"alpaca", "polygon"} else f"{base}-WT")
    elif ticker.endswith(".R"):
        base = ticker[:-2]
        candidates.append(f"{base}.RT" if name in {"alpaca", "polygon"} else f"{base}-RT")
    elif ticker.endswith(".U"):
        base = ticker[:-2]
        candidates.append(ticker if name in {"alpaca", "polygon"} else f"{base}-UN")
    elif "." in ticker and name in {"yfinance", "yahoo", "openbb:yfinance"}:
        base, suffix = ticker.rsplit(".", 1)
        if base and suffix:
            candidates.append(f"{base}-{suffix}")
    candidates.append(ticker)
    return list(dict.fromkeys(value for value in candidates if value))



def yahoo_symbol(symbol: str) -> str:
    """The ticker as Yahoo writes it (BRK.B -> BRK-B, ABR$D -> ABR-PD); callers keep the canonical ticker as the key."""
    return (provider_symbol_candidates(symbol, "yfinance") or [symbol])[0]


def deep_merge_dict(base: dict[str, Any], overrides: dict[str, Any]) -> dict[str, Any]:
    merged = copy.deepcopy(base)
    for key, value in overrides.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = deep_merge_dict(merged[key], value)
        else:
            merged[key] = copy.deepcopy(value)
    return merged


def now_athens() -> datetime:
    return datetime.now(ATHENS_TZ)


def parse_iso_date(value: Any) -> datetime | None:
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
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=ATHENS_TZ)
    return parsed.astimezone(ATHENS_TZ)


def age_days(value: Any) -> int | None:
    parsed = parse_iso_date(value)
    if parsed is None:
        return None
    return max(0, (now_athens().date() - parsed.date()).days)


def valid_symbol(symbol: str) -> bool:
    return bool(re.fullmatch(r"[A-Z][A-Z0-9.\-]{0,9}", symbol))


def format_datetime_from_epoch(epoch_seconds: Any) -> str | None:
    try:
        ts = int(epoch_seconds)
    except (TypeError, ValueError):
        return None
    return datetime.fromtimestamp(ts, tz=ATHENS_TZ).isoformat()


def weighted_score(
    ma_score: float,
    fundamental_score: float,
    ma_weight: float,
    fundamental_weight: float,
) -> float:
    denominator = ma_weight + fundamental_weight
    if denominator <= 0:
        return 0.0
    composite = (ma_score * ma_weight + fundamental_score * fundamental_weight) / denominator
    return round(composite, 2)


def format_metric_line(label: str, value: Any) -> str:
    numeric = to_float(value)
    if numeric is None:
        return f"- {label}: N/A"
    if abs(numeric) >= 1_000_000_000:
        return f"- {label}: {numeric / 1_000_000_000:.2f}B"
    if abs(numeric) >= 1_000_000:
        return f"- {label}: {numeric / 1_000_000:.2f}M"
    if abs(numeric) >= 1000:
        return f"- {label}: {numeric:,.2f}"
    return f"- {label}: {numeric:.4f}"


def evaluate_risk_level(row: dict[str, Any]) -> tuple[str, list[str]]:
    flags: list[str] = []
    dte = to_float(row.get("debt_to_equity"))
    current_ratio = to_float(row.get("current_ratio"))
    close = to_float(row.get("close"))
    ocf = to_float(row.get("operating_cashflow"))
    fcf = to_float(row.get("free_cashflow"))

    if dte is not None and dte > 150:
        flags.append("high_debt_to_equity")
    if current_ratio is not None and current_ratio < 1.0:
        flags.append("weak_liquidity_current_ratio")
    if ocf is not None and ocf < 0:
        flags.append("negative_operating_cashflow")
    if fcf is not None and fcf < 0:
        flags.append("negative_free_cashflow")
    if close is not None and close < 1.0:
        flags.append("sub_1usd_price_instability_risk")

    if len(flags) >= 3:
        return "High", flags
    if len(flags) >= 1:
        return "Medium", flags
    return "Low", flags


def classify_instrument(row: dict[str, Any]) -> dict[str, Any]:
    """Security type of a row: the registry's type when the row carries one, else a name/ticker heuristic."""
    return instrument_profile(
        row.get("symbol") or row.get("ticker"), row.get("company_name") or row.get("company"),
        row.get("instrument_type"))
