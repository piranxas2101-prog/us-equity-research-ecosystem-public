"""Internal-only FMP coverage pilot with redacted output.

The output contains availability and latency, never proprietary raw values.  It
is not suitable for promotion or public display without an appropriate FMP
agreement.
"""

from __future__ import annotations

import argparse
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


FIELD_ALIASES: dict[str, tuple[tuple[str, str], ...]] = {
    "revenue_growth": (("income_statement_growth", "growthRevenue"),),
    "earnings_growth": (("income_statement_growth", "growthNetIncome"),),
    "gross_margin": (("ratios_ttm", "grossProfitMarginTTM"),),
    "profit_margin": (("ratios_ttm", "netProfitMarginTTM"),),
    "roe": (("ratios_ttm", "returnOnEquityTTM"),),
    "debt_to_equity": (("ratios_ttm", "debtEquityRatioTTM"),),
    "current_ratio": (("ratios_ttm", "currentRatioTTM"),),
    # A trailing P/E is not a safe substitute for forward P/E.  This remains
    # deliberately unsupported until a point-in-time estimates dataset exists.
    "forward_pe": (),
}

ENDPOINTS = {
    "income_statement_growth": "income-statement-growth",
    "ratios_ttm": "ratios-ttm",
}


def load_env_value(path: Path, name: str) -> str:
    for raw_line in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        if key.strip() != name:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        return value.strip()
    return ""


def first_row(payload: Any) -> dict[str, Any]:
    if isinstance(payload, list) and payload and isinstance(payload[0], dict):
        return payload[0]
    return payload if isinstance(payload, dict) else {}


def query_endpoint(endpoint: str, symbol: str, api_key: str, timeout: int = 15) -> dict[str, Any]:
    query = urllib.parse.urlencode({"symbol": symbol})
    request = urllib.request.Request(
        f"https://financialmodelingprep.com/stable/{endpoint}?{query}",
        headers={"Accept": "application/json", "apikey": api_key, "User-Agent": "unknown-shadow-pilot/1.0"},
        method="GET",
    )
    started = time.monotonic()
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
        return {
            "status": "ok",
            "latency_ms": round((time.monotonic() - started) * 1000.0, 2),
            "row": first_row(payload),
        }
    except urllib.error.HTTPError as exc:
        status = {401: "auth_failed", 403: "forbidden_or_plan", 429: "rate_limited"}.get(exc.code, "http_error")
        return {"status": status, "http_status": exc.code, "latency_ms": round((time.monotonic() - started) * 1000.0, 2), "row": {}}
    except (OSError, TimeoutError, json.JSONDecodeError, UnicodeDecodeError):
        return {"status": "timeout_or_invalid", "latency_ms": round((time.monotonic() - started) * 1000.0, 2), "row": {}}


def coverage_for_symbol(symbol: str, api_key: str) -> dict[str, Any]:
    responses = {
        name: query_endpoint(endpoint, symbol, api_key)
        for name, endpoint in ENDPOINTS.items()
    }
    fields: dict[str, dict[str, Any]] = {}
    for field, aliases in FIELD_ALIASES.items():
        matched: tuple[str, str] | None = None
        for source, key in aliases:
            value = responses[source]["row"].get(key)
            if isinstance(value, (int, float)):
                matched = (source, key)
                break
        fields[field] = {
            "available": matched is not None,
            "source_endpoint": matched[0] if matched else None,
            "source_field": matched[1] if matched else None,
            "raw_value_redacted": True,
        }
    return {
        "symbol": symbol,
        "provider_status": {name: row["status"] for name, row in responses.items()},
        "latency_ms": {name: row["latency_ms"] for name, row in responses.items()},
        "field_coverage": fields,
        "resolved": sum(1 for row in fields.values() if row["available"]),
        "requested": len(fields),
    }


def run(symbols: list[str], env_file: Path) -> dict[str, Any]:
    key = load_env_value(env_file, "FMP_API_KEY")
    if not key:
        raise RuntimeError("FMP_API_KEY is not configured")
    rows = [coverage_for_symbol(symbol.upper(), key) for symbol in symbols]
    total_resolved = sum(row["resolved"] for row in rows)
    total_requested = sum(row["requested"] for row in rows)
    return {
        "schema": "ecosystem.fmp_shadow_coverage.v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "symbols": rows,
        "coverage_pct": round(total_resolved / total_requested * 100.0, 2) if total_requested else 0.0,
        "raw_values_persisted": False,
        "sec_precedence_required": True,
        "license_status": "not_approved_for_public_redistribution",
        "promotion_eligible": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Redacted FMP shadow coverage pilot")
    parser.add_argument("--symbols", nargs="+", required=True)
    parser.add_argument("--env-file", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    result = run(args.symbols, Path(args.env_file))
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"coverage_pct": result["coverage_pct"], "promotion_eligible": False}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
