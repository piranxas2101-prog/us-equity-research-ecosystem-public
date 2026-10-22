from __future__ import annotations
from common import load_json

# Catalogue mode is isolated from scanner/trading imports and filters.
if __name__ == '__main__':
    import sys as _catalog_sys
    if len(_catalog_sys.argv) > 1 and _catalog_sys.argv[1] == 'babis':
        from research_registry.babis_pipeline import main as _babis_main
        raise SystemExit(_babis_main(_catalog_sys.argv[2:]))
    if len(_catalog_sys.argv) > 1 and _catalog_sys.argv[1] == 'babis-gaps':
        from research_registry.babis_gap_followup import main as _babis_gaps_main
        raise SystemExit(_babis_gaps_main(_catalog_sys.argv[2:]))
    if len(_catalog_sys.argv) > 1 and _catalog_sys.argv[1] == 'sector-agent':
        from sector_research_agent import main as _sector_agent_main
        raise SystemExit(_sector_agent_main(_catalog_sys.argv[2:]))
    if len(_catalog_sys.argv) > 1 and _catalog_sys.argv[1] == 'sector-ecosystem':
        from sector_ecosystem_integration import main as _sector_ecosystem_main
        raise SystemExit(_sector_ecosystem_main(_catalog_sys.argv[2:]))
    if len(_catalog_sys.argv) > 1 and _catalog_sys.argv[1] == 'unresolved-classification':
        from unresolved_classification_runner import main as _unresolved_classification_main
        raise SystemExit(_unresolved_classification_main(_catalog_sys.argv[2:]))
    if len(_catalog_sys.argv) > 1 and _catalog_sys.argv[1] == 'babis-unresolved-review':
        from babis_unresolved_review import main as _babis_unresolved_review_main
        raise SystemExit(_babis_unresolved_review_main(_catalog_sys.argv[2:]))
    if len(_catalog_sys.argv) > 1 and _catalog_sys.argv[1] == 'sector-agents-run':
        from sector_agent_batch_runner import main as _sector_agents_run_main
        raise SystemExit(_sector_agents_run_main(_catalog_sys.argv[2:]))
    if len(_catalog_sys.argv) > 1 and _catalog_sys.argv[1] == 'sector-evidence-run':
        from sector_agent_local_evidence_runner import main as _sector_evidence_run_main
        raise SystemExit(_sector_evidence_run_main(_catalog_sys.argv[2:]))
    if len(_catalog_sys.argv) > 1 and _catalog_sys.argv[1] == 'sector-sec-ir-pilot':
        from sector_sec_ir_pilot import main as _sector_sec_ir_pilot_main
        raise SystemExit(_sector_sec_ir_pilot_main(_catalog_sys.argv[2:]))
    if len(_catalog_sys.argv) > 1 and _catalog_sys.argv[1] == 'software-filing-evidence':
        from software_filing_evidence_runner import main as _software_filing_evidence_main
        raise SystemExit(_software_filing_evidence_main(_catalog_sys.argv[2:]))
    if len(_catalog_sys.argv) > 1 and _catalog_sys.argv[1] == 'company-profiles':
        from company_profile_agent import main as _company_profile_main
        raise SystemExit(_company_profile_main(_catalog_sys.argv[2:]))
    if len(_catalog_sys.argv) > 1 and _catalog_sys.argv[1] == 'research-flow':
        from research_workflow_orchestrator import main as _research_flow_main
        raise SystemExit(_research_flow_main(_catalog_sys.argv[2:]))
    if len(_catalog_sys.argv) > 1 and _catalog_sys.argv[1] == 'giannis-fundamental':
        from giannis_fundamental_agent import main as _giannis_fundamental_main
        raise SystemExit(_giannis_fundamental_main(_catalog_sys.argv[2:]))
    if len(_catalog_sys.argv) > 1 and _catalog_sys.argv[1] == 'catalog':
        from research_registry.babis_catalog import main as _catalog_main
        raise SystemExit(_catalog_main(_catalog_sys.argv[2:]))
    if len(_catalog_sys.argv) > 1 and _catalog_sys.argv[1] == 'catalog-enrich':
        from research_registry.babis_problem_enrichment import main as _catalog_enrich_main
        raise SystemExit(_catalog_enrich_main(_catalog_sys.argv[2:]))

import argparse
import copy
from collections import Counter
import hashlib
import json
import os
import re
import sqlite3
import socket
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable
from urllib import parse as urlparse
from urllib import error as urlerror
from urllib import request as urlrequest

import pandas as pd
import yfinance as yf

import candlestick_signals
import catalyst_taxonomy
import market_intel_db
import sec_fundamental_ratios
import valuation_gaps
import macro_regime
import openbb_adapter
import shortlist_membership
from run_context import attach_run_context, resolve_run_id
from symbol_classification import classify_security_asset, classify_symbol
from research_registry.core import DEFAULT_DB as DEFAULT_REGISTRY_DB
import agent_methodology
import agent_personality
import unknown_data
import catalyst_content_review
import symbol_identity
from provider_rate_limiter import SharedProviderRateLimiter
from monitor_core import (
    ATHENS_TZ,
    age_days,
    classify_instrument,
    deep_merge_dict,
    evaluate_risk_level,
    format_datetime_from_epoch,
    format_metric_line,
    normalize_symbol,
    now_athens,
    parse_iso_date,  # noqa: F401  re-exported for backward compatibility
    provider_symbol_candidates,
    to_float,
    valid_symbol,
    weighted_score,
)


DEFAULT_CONFIG_PATH = "scanner_config.json"
DEFAULT_PROMPT_PATH = "agent_prompt.txt"
SEC_TICKERS_URL = "https://www.sec.gov/files/company_tickers_exchange.json"
SEC_TICKER_CIK_URL = "https://www.sec.gov/files/company_tickers.json"
DEFAULT_SECURITY_EXCLUDE_KEYWORDS = [
    "warrant",
    "right",
    "unit",
    "depositary share",
    "depositary shares",
    "etf",
    "exchange traded fund",
    "etn",
    "fund",
    "blank check",
    "acquisition corp",
    "acquisition corporation",
    "merger corp",
    "merger corporation",
    "spac",
]

HISTORY_ELIGIBILITY_CODES = frozenset(
    {
        "all_providers_insufficient_history",
        "provider_partial_history",
    }
)

EXPECTED_INSTRUMENT_COVERAGE_TYPES = frozenset(
    {"warrant", "preferred", "right", "debt", "unit", "unknown_instrument"}
)

OPERATORS: dict[str, Callable[[float, float], bool]] = {
    ">": lambda x, y: x > y,
    ">=": lambda x, y: x >= y,
    "<": lambda x, y: x < y,
    "<=": lambda x, y: x <= y,
    "==": lambda x, y: x == y,
    "!=": lambda x, y: x != y,
}


class MarketDataCollectionError(ValueError):
    def __init__(
        self,
        message: str,
        *,
        code: str,
        attempts: list[dict[str, Any]] | None = None,
        retryable: bool = True,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.attempts = attempts or []
        self.retryable = retryable
        self.details = details or {}

    def as_record(self, symbol: str) -> dict[str, Any]:
        return {
            "symbol": symbol,
            "error_code": self.code,
            "error": str(self),
            "retryable": self.retryable,
            "provider_attempts": self.attempts,
            "details": self.details,
        }


def classify_provider_error(exc: Exception) -> str:
    text = str(exc).lower()
    if "api_key" in text or "api key" in text or "unauthorized" in text or "forbidden" in text:
        return "provider_auth_error"
    if "rate limit" in text or "too many requests" in text or "429" in text:
        return "provider_rate_limit"
    if "no bars" in text or "empty" in text:
        return "provider_empty_response"
    if "missing column" in text or "invalid" in text or "decode" in text:
        return "data_validation_error"
    return "provider_transport_error"


def data_error_record(symbol: str, exc: Exception) -> dict[str, Any]:
    if isinstance(exc, MarketDataCollectionError):
        return exc.as_record(symbol)
    return {
        "symbol": symbol,
        "error_code": "unclassified_processing_error",
        "error": str(exc),
        "retryable": False,
        "provider_attempts": [],
        "details": {},
    }


def is_history_eligibility_record(record: dict[str, Any]) -> bool:
    return str(record.get("error_code") or "") in HISTORY_ELIGIBILITY_CODES or str(
        (record.get("details") or {}).get("failure_class") or ""
    ) == "eligibility_gap"


def is_expected_instrument_coverage_record(record: dict[str, Any]) -> bool:
    """Separate provider coverage gaps from genuine scanner failures."""
    if str(record.get("error_code") or "") != "all_providers_exhausted":
        return False
    failure_class = str((record.get("details") or {}).get("failure_class") or "")
    if failure_class == "provider_coverage_gap":
        return True
    return (not bool(record.get("company_fundamental_eligible", True))
            and str(record.get("instrument_type") or "") in EXPECTED_INSTRUMENT_COVERAGE_TYPES)


def load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip("'").strip('"')
        if not key:
            continue
        os.environ[key] = value


def database_config(config: dict[str, Any]) -> dict[str, Any]:
    raw = config.get("database", {})
    return raw if isinstance(raw, dict) else {}


def database_enabled(config: dict[str, Any]) -> bool:
    db_cfg = database_config(config)
    return bool(db_cfg.get("enabled", True))


def database_path(config: dict[str, Any]) -> Path:
    db_cfg = database_config(config)
    return Path(str(db_cfg.get("path", market_intel_db.DEFAULT_DB_PATH)))


def store_scan_payload(config: dict[str, Any], source_file: Path, payload: dict[str, Any]) -> None:
    if not database_enabled(config):
        return
    db_path = database_path(config)
    try:
        with market_intel_db.connect(db_path) as conn:
            market_intel_db.init_db(conn)
            rows = market_intel_db.ingest_scan(conn, source_file, payload)
            conn.commit()
    except Exception as exc:
        print(f"[DB] Warning: failed to store scan results in {db_path}: {exc}")
        return
    if rows:
        print(f"[DB] Stored scan rows={rows} in {db_path}")
    else:
        print(f"[DB] Scan already stored or empty: {source_file}")


def store_shortlist_payload(config: dict[str, Any], source_file: Path, payload: dict[str, Any]) -> None:
    if not database_enabled(config):
        return
    db_path = database_path(config)
    try:
        with market_intel_db.connect(db_path) as conn:
            market_intel_db.init_db(conn)
            rows = market_intel_db.ingest_shortlist(conn, source_file, payload)
            conn.commit()
    except Exception as exc:
        print(f"[DB] Warning: failed to store shortlist in {db_path}: {exc}")
        return
    if rows:
        print(f"[DB] Stored shortlist rows={rows} in {db_path}")
    else:
        print(f"[DB] Shortlist already stored or empty: {source_file}")


def utc_today() -> datetime:
    return datetime.now(timezone.utc)


def sec_user_agent() -> str:
    candidate = os.getenv("SEC_USER_AGENT", "").strip()
    if candidate:
        return candidate
    return "us-equity-monitor/1.0 research@example.com"


def fetch_json_url(url: str, timeout_seconds: int = 60, headers: dict[str, str] | None = None) -> dict[str, Any]:
    req_headers = {
        "User-Agent": "us-equity-monitor/1.0 (research workflow)",
        "Accept": "application/json",
    }
    if headers:
        req_headers.update(headers)
    req = urlrequest.Request(
        url,
        headers=req_headers,
        method="GET",
    )
    with urlrequest.urlopen(req, timeout=timeout_seconds) as response:
        return json.loads(response.read().decode("utf-8"))


def build_stage1_data_sources_cfg(config: dict[str, Any]) -> tuple[dict[str, Any], str]:
    data_sources_cfg = copy.deepcopy(config.get("data_sources", {}))
    pipeline_cfg = config.get("pipeline", {})
    profile = str(pipeline_cfg.get("stage1_data_profile", "default")).strip().lower()
    if profile == "light":
        data_sources_cfg = deep_merge_dict(
            data_sources_cfg,
            {
                "sec": {
                    "max_recent_filings": 3,
                    "max_filing_age_days": 14,
                },
                "openbb_research": {
                    "enabled": False,
                    "share_statistics_enabled": False,
                    "insider_trading_enabled": False,
                    "darkpool_enabled": False,
                },
            },
        )
    overrides = pipeline_cfg.get("stage1_source_overrides", {})
    if isinstance(overrides, dict) and overrides:
        data_sources_cfg = deep_merge_dict(data_sources_cfg, overrides)
    return data_sources_cfg, profile


def security_name_excluded(name: str, universe_cfg: dict[str, Any]) -> bool:
    text = (name or "").strip().lower()
    if not text:
        return False
    keywords = universe_cfg.get("exclude_name_keywords", DEFAULT_SECURITY_EXCLUDE_KEYWORDS)
    for keyword in keywords:
        token = str(keyword).strip().lower()
        if token and re.search(r"(?<![a-z0-9])" + re.escape(token) + r"(?![a-z0-9])", text):
            return True
    return False


def security_asset_excluded(symbol: str, name: str, universe_cfg: dict[str, Any]) -> bool:
    classification = classify_security_asset(symbol, name)
    excluded_types = {
        str(item).strip().lower()
        for item in universe_cfg.get(
            "exclude_asset_types",
            [
                "warrant",
                "warrant_like",
                "unit",
                "unit_like",
                "preferred_or_depositary",
                "right",
                "fund_or_trust",
                "spac_common",
            ],
        )
        if str(item).strip()
    }
    return str(classification.get("asset_type") or "").lower() in excluded_types


def local_listing_instruments(path: Path) -> tuple[dict[tuple[str, str], str], str | None]:
    """Use the latest dated exchange directory as an instrument cross-check."""
    if not path.is_file():
        return {}, None
    db = sqlite3.connect(str(path))
    try:
        row = db.execute(
            "SELECT snapshot_id FROM universe_snapshot ORDER BY retrieved_at DESC,rowid DESC LIMIT 1"
        ).fetchone()
        if not row:
            return {}, None
        snapshot_id = str(row[0])
        values = db.execute(
            "SELECT ticker,venue_code,instrument_type FROM effective_listed_symbol WHERE snapshot_id=?",
            (snapshot_id,),
        )
        return {(str(ticker), str(venue)): str(kind) for ticker, venue, kind in values}, snapshot_id
    except sqlite3.DatabaseError:
        return {}, None
    finally:
        db.close()


INSTRUMENT_RESEARCH_ROUTES = {
    "etf": "fund_or_etp_observation",
    "fund_candidate": "fund_or_etp_observation",
    "etn": "exchange_traded_note_observation",
    "warrant": "listed_derivative_observation",
    "right": "listed_derivative_observation",
    "unit": "listed_derivative_observation",
    "preferred": "capital_structure_observation",
    "debt": "capital_structure_observation",
    "spac": "acquisition_vehicle_observation",
    "unknown_instrument": "classification_review",
}


def deferred_instrument_observation(symbol: str, listing_context: dict[str, Any]) -> dict[str, Any]:
    """Record an instrument the central run did not scan; its market values stay unknown.

    It is either linked to its issuer (shown on the issuer's card) or routed to a
    separate lane such as ETFs or SPAC shells.
    """
    issuer = listing_context.get("issuer_ticker")
    lane = listing_context.get("separate_lane")
    return {
        "ticker": symbol,
        "name": listing_context.get("name"),
        "venue_code": listing_context.get("venue_code"),
        "venue": listing_context.get("venue"),
        "instrument_type": listing_context.get("instrument_type"),
        "research_route": listing_context.get("research_route"),
        "workflow_status": "deferred_to_issuer_card" if issuer else f"deferred_to_{lane}",
        "issuer_ticker": issuer,
        "issuer_link_method": listing_context.get("issuer_link_method"),
        "separate_lane": None if issuer else lane,
        "market_data_provider": None,
        "close": None,
        "ma_score": None,
        "ma_coverage": None,
        "return_20d_pct": None,
        "volatility_20d_pct": None,
        "volume_ratio_20": None,
        "unknown_reason": ("not_scanned_separately_linked_to_issuer" if issuer
                           else "not_scanned_in_central_run_separate_lane"),
    }


def load_all_listed_routes(universe_cfg: dict[str, Any]) -> tuple[dict[str, dict[str, Any]], str | None]:
    """Return every current exchange-directory symbol with an explicit research lane."""
    registry_path = Path(universe_cfg.get("registry_db_path", DEFAULT_REGISTRY_DB))
    receipt_path = Path(
        universe_cfg.get("eligibility_receipt_path", "output/universe_eligibility_latest.json")
    )
    if not registry_path.is_file():
        raise FileNotFoundError(f"Missing listed-symbol registry: {registry_path}")
    if not receipt_path.is_file():
        raise FileNotFoundError(f"Missing company eligibility receipt: {receipt_path}")
    receipt = json.loads(receipt_path.read_text(encoding="utf-8-sig"))
    eligible_values = receipt.get("eligible_symbols")
    if not isinstance(eligible_values, list):
        raise ValueError("Eligibility receipt must include eligible_symbols before an all-listed run")
    eligible = {normalize_symbol(str(value)) for value in eligible_values if normalize_symbol(str(value))}
    canonical_eligible: dict[str, set[str]] = {}
    for ticker in eligible:
        canonical_eligible.setdefault(re.sub(r"[^A-Z0-9]", "", ticker), set()).add(ticker)

    db = sqlite3.connect(str(registry_path))
    db.row_factory = sqlite3.Row
    try:
        latest = db.execute(
            "SELECT snapshot_id FROM universe_snapshot ORDER BY retrieved_at DESC,rowid DESC LIMIT 1"
        ).fetchone()
        if not latest:
            raise ValueError("Listed-symbol registry has no snapshot")
        snapshot_id = str(latest[0])
        if receipt.get("local_snapshot_id") != snapshot_id:
            raise ValueError("Company eligibility receipt does not match the current listed-symbol snapshot")
        rows = db.execute(
            "SELECT ticker,venue_code,name,instrument_type,node_id,payload_json "
            "FROM effective_listed_symbol WHERE snapshot_id=? ORDER BY ticker,venue_code",
            (snapshot_id,),
        ).fetchall()
        issuer_links: dict[str, sqlite3.Row] = {}
        if db.execute("SELECT 1 FROM sqlite_master WHERE name='instrument_parent_link'").fetchone():
            issuer_links = {
                str(row["ticker"]): row for row in db.execute(
                    "SELECT ticker,parent_ticker,method FROM instrument_parent_link "
                    "WHERE snapshot_id=? AND status='linked'", (snapshot_id,))
            }
    finally:
        db.close()
    # Warrants/preferreds/units/rights linked to their issuer belong on the issuer's
    # symbol card; with this switch Babis records them without a separate market scan.
    defer_linked = bool(universe_cfg.get("defer_issuer_linked_instruments", False))
    # Routes that no research agent consumes (ETFs, SPAC shells) can be moved to their
    # own lane: they stay routed and counted, but the central run does not fetch them.
    separate_lanes = {str(route): str(lane) for route, lane in
                      dict(universe_cfg.get("separate_lane_routes") or {}).items()}

    routes: dict[str, dict[str, Any]] = {}
    local_canonical_counts = Counter(
        re.sub(r"[^A-Z0-9]", "", normalize_symbol(str(row["ticker"])))
        for row in rows if normalize_symbol(str(row["ticker"]))
    )
    for row in rows:
        ticker = normalize_symbol(str(row["ticker"]))
        if not ticker:
            continue
        if ticker in routes:
            raise ValueError(f"Duplicate listed ticker cannot be routed safely: {ticker}")
        instrument_type = str(row["instrument_type"] or "unknown_instrument")
        sec_ticker = ticker if ticker in eligible else None
        if sec_ticker is None:
            canonical = re.sub(r"[^A-Z0-9]", "", ticker)
            aliases = canonical_eligible.get(canonical, set())
            if len(aliases) == 1 and local_canonical_counts[canonical] == 1:
                sec_ticker = next(iter(aliases))
        company_eligible = sec_ticker is not None
        route = (
            "company_fundamental_research"
            if company_eligible
            else INSTRUMENT_RESEARCH_ROUTES.get(instrument_type, "classification_review")
        )
        payload = json.loads(row["payload_json"] or "{}")
        link = issuer_links.get(str(row["ticker"]))
        issuer_ticker = str(link["parent_ticker"]) if link is not None and not company_eligible else None
        lane = separate_lanes.get(route) if not company_eligible else None
        routes[ticker] = {
            "ticker": ticker,
            "venue_code": str(row["venue_code"] or ""),
            "venue": payload.get("venue"),
            "name": str(row["name"] or ticker),
            "instrument_type": instrument_type,
            "node_id": row["node_id"],
            "research_route": route,
            "company_fundamental_eligible": company_eligible,
            "sec_eligibility_ticker": sec_ticker,
            "issuer_ticker": issuer_ticker,
            "issuer_link_method": str(link["method"]) if issuer_ticker else None,
            "separate_lane": lane,
            "defer_market_scan": bool((defer_linked and issuer_ticker) or lane),
        }
    if not routes:
        raise ValueError("Listed-symbol registry resolved zero symbols")
    return routes, snapshot_id


def load_all_listed_universe(universe_cfg: dict[str, Any]) -> list[str]:
    routes, _ = load_all_listed_routes(universe_cfg)
    symbols = sorted(routes)
    max_symbols = int(universe_cfg.get("max_symbols", 0))
    return symbols if max_symbols <= 0 else symbols[:max_symbols]


def current_universe_overrides(path: Path, today: str) -> dict[str, dict[str, Any]]:
    if not path.is_file():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("schema") != "ecosystem.universe_eligibility_overrides.v1":
        raise ValueError("Unsupported universe eligibility override schema")
    return {
        str(item["ticker"]).upper(): item
        for item in payload.get("exclusions", [])
        if str(item.get("reviewed_at") or "") <= today <= str(item.get("valid_until") or "")
        and item.get("reason_code") and item.get("source_url")
    }


def load_us_universe_from_sec(universe_cfg: dict[str, Any]) -> list[str]:
    include_exchanges = set(universe_cfg.get("include_exchanges", [
        "Nasdaq", "NYSE", "NYSE American", "NYSE Arca", "CBOE", "Cboe BZX", "IEX",
        "TXSE", "Texas Stock Exchange"
    ]))
    max_symbols = int(universe_cfg.get("max_symbols", 350))
    cache_file = Path(universe_cfg.get("cache_file", "cache/sec_company_tickers_exchange.json"))
    cache_max_age_hours = int(universe_cfg.get("cache_max_age_hours", 24))

    raw: dict[str, Any] | None = None

    if cache_file.exists():
        age_seconds = (datetime.now() - datetime.fromtimestamp(cache_file.stat().st_mtime)).total_seconds()
        if age_seconds <= cache_max_age_hours * 3600:
            try:
                raw = json.loads(cache_file.read_text(encoding="utf-8"))
            except Exception:
                raw = None

    if raw is None:
        raw = fetch_json_url(
            SEC_TICKERS_URL,
            headers={"User-Agent": sec_user_agent(), "Accept": "application/json"},
        )
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        cache_file.write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")

    fields = raw.get("fields", [])
    data_rows = raw.get("data", [])
    tickers: list[str] = []
    registry_path = Path(universe_cfg.get("registry_db_path", DEFAULT_REGISTRY_DB))
    local_instruments, local_snapshot_id = local_listing_instruments(registry_path)
    overrides = current_universe_overrides(
        Path(universe_cfg.get("eligibility_overrides_path", "universe_eligibility_overrides.json")),
        datetime.now(ATHENS_TZ).date().isoformat(),
    )
    venue_codes = {"Nasdaq": "Q", "NYSE": "N", "NYSE American": "A",
                   "NYSE Arca": "P", "CBOE": "Z", "Cboe BZX": "Z", "IEX": "V",
                   "TXSE": "F", "Texas Stock Exchange": "F"}
    # fund_candidate is deliberately omitted: the dated directory includes
    # listed investment companies and BDC common shares under that label.
    excluded_local_types = {"warrant", "preferred", "unit", "right", "etf", "etn", "debt", "spac"}
    special_exclusions: list[dict[str, Any]] = []
    asset_classifier_exclusions: list[dict[str, Any]] = []

    if isinstance(fields, list) and isinstance(data_rows, list) and fields and data_rows:
        idx_ticker = fields.index("ticker") if "ticker" in fields else None
        idx_exchange = fields.index("exchange") if "exchange" in fields else None
        idx_name = fields.index("name") if "name" in fields else None
        for row in data_rows:
            if not isinstance(row, list):
                continue
            ticker = normalize_symbol(str(row[idx_ticker])) if idx_ticker is not None and idx_ticker < len(row) else ""
            exchange = str(row[idx_exchange]) if idx_exchange is not None and idx_exchange < len(row) else ""
            sec_name = str(row[idx_name]) if idx_name is not None and idx_name < len(row) else ""
            if not ticker or not valid_symbol(ticker):
                continue
            if include_exchanges and exchange not in include_exchanges:
                continue
            local_type = local_instruments.get((ticker, venue_codes.get(exchange, "")))
            if local_type in excluded_local_types:
                special_exclusions.append({"ticker": ticker, "reason_code": "local_instrument_excluded", "instrument_type": local_type})
                continue
            # A dated listing that identifies an operating equity/ADR outranks
            # broad words in the issuer name (e.g. United, Wright, Preferred Bank).
            if local_type not in {"equity_candidate", "adr_candidate"}:
                if security_name_excluded(sec_name, universe_cfg):
                    continue
                if security_asset_excluded(ticker, sec_name, universe_cfg):
                    classification = classify_security_asset(ticker, sec_name)
                    asset_classifier_exclusions.append({"ticker": ticker, "asset_type": classification["asset_type"], "evidence": classification["classification_evidence"]})
                    continue
            override = overrides.get(ticker)
            if override:
                special_exclusions.append({"ticker": ticker, "reason_code": override["reason_code"], "source_url": override["source_url"], "valid_until": override["valid_until"]})
                continue
            tickers.append(ticker)

    unique_sorted = sorted(set(tickers))
    if max_symbols > 0:
        unique_sorted = unique_sorted[:max_symbols]
    receipt_path = universe_cfg.get("eligibility_receipt_path")
    if receipt_path:
        target = Path(receipt_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps({
            "schema": "ecosystem.universe_eligibility_receipt.v1",
            "source": SEC_TICKERS_URL,
            "local_snapshot_id": local_snapshot_id,
            "eligible_count": len(unique_sorted),
            "eligible_symbols": unique_sorted,
            "asset_classifier_exclusions": asset_classifier_exclusions,
            "special_exclusions": special_exclusions,
        }, ensure_ascii=False, indent=2), encoding="utf-8")
    return unique_sorted


def resolve_universe(args: argparse.Namespace, config: dict[str, Any]) -> list[str]:
    if args.tickers:
        return [normalize_symbol(s) for s in args.tickers if normalize_symbol(s)]

    universe_cfg = config.get("universe", {})
    mode = str(universe_cfg.get("mode", "sec_us_list")).strip().lower()

    if mode == "sec_us_list":
        return load_us_universe_from_sec(universe_cfg)

    if mode == "registry_all_listed":
        return load_all_listed_universe(universe_cfg)

    if mode == "symbols_file":
        path = Path(universe_cfg.get("symbols_file", "symbols.txt"))
        if not path.exists():
            return []
        symbols = [normalize_symbol(line) for line in path.read_text(encoding="utf-8").splitlines()]
        return [s for s in symbols if s and valid_symbol(s)]

    symbols = [normalize_symbol(s) for s in universe_cfg.get("symbols", [])]
    return [s for s in symbols if s and valid_symbol(s)]


def load_sec_ticker_cik_map(data_sources_cfg: dict[str, Any]) -> dict[str, int]:
    sec_cfg = data_sources_cfg.get("sec", {})
    cache_file = Path(sec_cfg.get("ticker_cik_cache_file", "cache/sec_company_tickers.json"))
    cache_max_age_hours = int(sec_cfg.get("cache_max_age_hours", 24))

    raw: dict[str, Any] | None = None
    if cache_file.exists():
        age_seconds = (datetime.now() - datetime.fromtimestamp(cache_file.stat().st_mtime)).total_seconds()
        if age_seconds <= cache_max_age_hours * 3600:
            try:
                raw = json.loads(cache_file.read_text(encoding="utf-8"))
            except Exception:
                raw = None

    if raw is None:
        raw = fetch_json_url(
            SEC_TICKER_CIK_URL,
            headers={"User-Agent": sec_user_agent(), "Accept": "application/json"},
        )
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        cache_file.write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")

    mapping: dict[str, int] = {}
    for _, row in raw.items():
        if not isinstance(row, dict):
            continue
        ticker = normalize_symbol(str(row.get("ticker", "")))
        cik = row.get("cik_str")
        if not ticker:
            continue
        try:
            mapping[ticker] = int(cik)
        except (TypeError, ValueError):
            continue
    return mapping


# SEC fair access allows 10 requests a second per client. With parallel stage-1 workers every SEC call passes through
# one in-process gate that spaces them out; with one worker the interval is 0 and nothing waits.
SEC_MIN_INTERVAL_SECONDS = 0.0
_sec_gate_lock = threading.Lock()
_sec_next_slot = 0.0


def _sec_throttle() -> None:
    global _sec_next_slot
    if SEC_MIN_INTERVAL_SECONDS <= 0:
        return
    with _sec_gate_lock:
        now = time.monotonic()
        slot = max(now, _sec_next_slot)
        _sec_next_slot = slot + SEC_MIN_INTERVAL_SECONDS
    if slot > now:
        time.sleep(slot - now)


def fetch_sec_recent_filings(symbol: str, data_sources_cfg: dict[str, Any]) -> list[dict[str, Any]]:
    sec_cfg = data_sources_cfg.get("sec", {})
    if not bool(sec_cfg.get("enabled", True)):
        return []

    include_forms = set(sec_cfg.get("include_forms", ["10-K", "10-Q", "8-K", "20-F", "6-K"]))
    max_items = int(sec_cfg.get("max_recent_filings", 10))
    max_age = int(sec_cfg.get("max_filing_age_days", 30))
    ticker_to_cik = load_sec_ticker_cik_map(data_sources_cfg)
    identity = symbol_identity.require_sec_identity(symbol, ticker_to_cik)
    cik = int(identity["cik"])

    cik_padded = f"{cik:010d}"
    url = f"https://data.sec.gov/submissions/CIK{cik_padded}.json"
    _sec_throttle()
    payload = fetch_json_url(url, headers={"User-Agent": sec_user_agent(), "Accept": "application/json"})
    recent = payload.get("filings", {}).get("recent", {})
    forms = recent.get("form", [])
    dates = recent.get("filingDate", [])
    accessions = recent.get("accessionNumber", [])
    docs = recent.get("primaryDocument", [])
    filing_items = recent.get("items", [])
    report_dates = recent.get("reportDate", [])
    descriptions = recent.get("primaryDocDescription", [])

    items: list[dict[str, Any]] = []
    n = min(len(forms), len(dates), len(accessions), len(docs))
    for i in range(n):
        form = str(forms[i])
        if include_forms and form not in include_forms:
            continue
        accession = str(accessions[i])
        filing_age = age_days(dates[i])
        if filing_age is None or filing_age > max_age:
            continue
        accession_nodash = accession.replace("-", "")
        primary_doc = str(docs[i])
        doc_url = f"https://www.sec.gov/Archives/edgar/data/{cik}/{accession_nodash}/{primary_doc}"
        items.append(
            {
                "issuer": "U.S. SEC EDGAR",
                "form": form,
                "filing_date": str(dates[i]),
                "filing_age_days": filing_age,
                "fresh": True,
                "max_age_days": max_age,
                "accession_number": accession,
                "report_date": str(report_dates[i]) if i < len(report_dates) else None,
                "items": catalyst_taxonomy.normalize_sec_items(filing_items[i]) if i < len(filing_items) else [],
                "primary_document": primary_doc,
                "primary_document_description": str(descriptions[i]) if i < len(descriptions) else None,
                "url": doc_url,
            }
        )
        if len(items) >= max_items:
            break
    return items


def history_from_ohlc_rows(rows: list[dict[str, Any]], ts_key: str, o_key: str, h_key: str, l_key: str, c_key: str, v_key: str) -> pd.DataFrame:
    if not rows:
        raise ValueError("No bars in provider response.")
    frame = pd.DataFrame(rows)
    required = [ts_key, o_key, h_key, l_key, c_key]
    for key in required:
        if key not in frame.columns:
            raise ValueError(f"Missing column in bars response: {key}")

    ts_series = frame[ts_key]
    if pd.api.types.is_numeric_dtype(ts_series):
        # Provider timestamps are usually epoch milliseconds.
        dt = pd.to_datetime(ts_series, unit="ms", utc=True, errors="coerce")
    else:
        dt = pd.to_datetime(ts_series, utc=True, errors="coerce")
    frame["Date"] = dt
    frame["Open"] = pd.to_numeric(frame[o_key], errors="coerce")
    frame["High"] = pd.to_numeric(frame[h_key], errors="coerce")
    frame["Low"] = pd.to_numeric(frame[l_key], errors="coerce")
    frame["Close"] = pd.to_numeric(frame[c_key], errors="coerce")
    frame["Volume"] = pd.to_numeric(frame[v_key], errors="coerce") if v_key in frame.columns else None
    frame = frame.dropna(subset=["Date", "Close"]).sort_values("Date")
    if frame.empty:
        raise ValueError("Bars parsed but no usable rows.")
    return frame[["Date", "Open", "High", "Low", "Close", "Volume"]].set_index("Date")


def fetch_history_yfinance(symbol: str, lookback_days: int) -> pd.DataFrame:
    ticker = yf.Ticker(symbol)
    history = ticker.history(period=f"{lookback_days}d", interval="1d", auto_adjust=False)
    if history.empty:
        raise ValueError("No price history returned from yfinance.")
    history = history.dropna(subset=["Close"])
    if history.empty:
        raise ValueError("YFinance history has no valid Close values.")
    return history


def fetch_history_alpaca(symbol: str, lookback_days: int, market_cfg: dict[str, Any]) -> pd.DataFrame:
    api_key = os.getenv("ALPACA_API_KEY_ID", "").strip()
    api_secret = os.getenv("ALPACA_API_SECRET_KEY", "").strip()
    if not api_key or not api_secret:
        raise ValueError("Missing ALPACA_API_KEY_ID or ALPACA_API_SECRET_KEY.")

    end_dt = utc_today()
    start_dt = end_dt - timedelta(days=max(lookback_days * 2, 365))
    params = {
        "symbols": symbol,
        "timeframe": "1Day",
        "start": start_dt.strftime("%Y-%m-%dT00:00:00Z"),
        "end": end_dt.strftime("%Y-%m-%dT23:59:59Z"),
        "limit": str(int(market_cfg.get("bars_limit", 10000))),
        "adjustment": str(market_cfg.get("adjustment", "raw")),
        "feed": str(market_cfg.get("alpaca_feed", "iex")),
    }
    query = urlparse.urlencode(params)
    url = f"https://data.alpaca.markets/v2/stocks/bars?{query}"
    payload = fetch_json_url(
        url,
        headers={
            "APCA-API-KEY-ID": api_key,
            "APCA-API-SECRET-KEY": api_secret,
            "Accept": "application/json",
        },
    )
    bars_map = payload.get("bars", {})
    bars = bars_map.get(symbol, []) if isinstance(bars_map, dict) else []
    return history_from_ohlc_rows(bars, ts_key="t", o_key="o", h_key="h", l_key="l", c_key="c", v_key="v")


def fetch_history_polygon(symbol: str, lookback_days: int, market_cfg: dict[str, Any]) -> pd.DataFrame:
    api_key = os.getenv("POLYGON_API_KEY", "").strip()
    if not api_key:
        raise ValueError("Missing POLYGON_API_KEY.")
    end_dt = utc_today().date()
    start_dt = end_dt - timedelta(days=max(lookback_days * 2, 365))
    encoded_symbol = urlparse.quote(symbol, safe="")
    url = (
        f"https://api.polygon.io/v2/aggs/ticker/{encoded_symbol}/range/1/day/{start_dt.isoformat()}/{end_dt.isoformat()}"
        f"?adjusted=true&sort=asc&limit={int(market_cfg.get('bars_limit', 50000))}&apiKey={api_key}"
    )
    payload = fetch_json_url(url)
    bars = payload.get("results", [])
    return history_from_ohlc_rows(bars, ts_key="t", o_key="o", h_key="h", l_key="l", c_key="c", v_key="v")


def fetch_history_openbb(symbol: str, lookback_days: int, market_cfg: dict[str, Any], provider: str | None = None) -> pd.DataFrame:
    return openbb_adapter.fetch_equity_history(symbol, lookback_days, market_cfg, provider=provider)


def fetch_history_with_routing(
    symbol: str,
    lookback_days: int,
    data_sources_cfg: dict[str, Any],
    min_history_days: int = 0,
    rate_limiter: SharedProviderRateLimiter | None = None,
) -> tuple[pd.DataFrame, str, list[dict[str, Any]]]:
    market_cfg = data_sources_cfg.get("market_data", {})
    providers = market_cfg.get("provider_priority", ["alpaca", "polygon", "yfinance"])
    attempts: list[dict[str, Any]] = []
    best_partial: tuple[pd.DataFrame, str] | None = None
    for provider in providers:
        name = str(provider).strip().lower()
        resolved_provider = None
        resolved_name = name
        if name == "openbb":
            resolved_provider = str(market_cfg.get("openbb_provider", "yfinance")).strip().lower() or "yfinance"
            resolved_name = f"openbb:{resolved_provider}"
        elif name.startswith("openbb:"):
            resolved_provider = name.split(":", 1)[1].strip().lower() or str(
                market_cfg.get("openbb_provider", "yfinance")
            ).strip().lower() or "yfinance"
            resolved_name = f"openbb:{resolved_provider}"
        elif name not in {"alpaca", "polygon", "yfinance"}:
            attempts.append({
                "provider": name, "status": "unsupported_provider", "row_count": 0,
                "latency_ms": 0.0, "retryable": False, "requested_symbol": symbol,
            })
            continue

        for provider_symbol in provider_symbol_candidates(symbol, resolved_name):
            started_at = time.perf_counter()
            if rate_limiter is not None:
                decision = rate_limiter.permit(
                    resolved_name,
                    max_interval_wait_seconds=float(market_cfg.get("rate_limit_max_wait_seconds", 3.0)),
                )
                if not decision.permitted:
                    attempts.append({
                        "provider": resolved_name,
                        "status": "provider_rate_policy_blocked",
                        "reason_code": decision.reason_code,
                        "row_count": 0,
                        "latency_ms": round((time.perf_counter() - started_at) * 1000.0, 2),
                        "retryable": decision.reason_code != "daily_budget_exhausted",
                        "next_eligible_at": decision.next_eligible_at,
                        "requested_symbol": symbol,
                        "provider_symbol": provider_symbol,
                    })
                    break
            try:
                # Same time limit as the other yfinance calls: a hung price request must not stop the scan.
                if name == "alpaca":
                    history = fetch_history_alpaca(provider_symbol, lookback_days, market_cfg)
                elif name == "polygon":
                    history = fetch_history_polygon(provider_symbol, lookback_days, market_cfg)
                elif name == "yfinance":
                    history = call_with_timeout(
                        lambda symbol_=provider_symbol: fetch_history_yfinance(symbol_, lookback_days),
                        provider_call_timeout(data_sources_cfg), f"history {provider_symbol}")
                else:
                    history = call_with_timeout(
                        lambda symbol_=provider_symbol, provider_=resolved_provider: fetch_history_openbb(
                            symbol_, lookback_days, market_cfg, provider=provider_),
                        provider_call_timeout(data_sources_cfg), f"history {provider_symbol}")
                if rate_limiter is not None:
                    rate_limiter.record_result(resolved_name, "success")

                row_count = len(history)
                attempt = {
                    "provider": resolved_name,
                    "status": "success" if row_count >= min_history_days else "insufficient_result",
                    "row_count": row_count,
                    "required_rows": min_history_days,
                    "latency_ms": round((time.perf_counter() - started_at) * 1000.0, 2),
                    "retryable": False,
                    "requested_symbol": symbol,
                    "provider_symbol": provider_symbol,
                }
                if row_count < min_history_days:
                    attempt["retry_policy"] = "retry_after_more_trading_history"
                    attempt["retry_after_trading_sessions"] = max(0, min_history_days - row_count)
                if row_count:
                    attempt["first_bar_at"] = str(history.index.min())
                    attempt["last_bar_at"] = str(history.index.max())
                attempts.append(attempt)
                if min_history_days <= 0 or row_count >= min_history_days:
                    return history, resolved_name, attempts
                if best_partial is None or row_count > len(best_partial[0]):
                    best_partial = (history, resolved_name)
                break
            except Exception as exc:
                error_code = "provider_timeout" if isinstance(exc, ProviderCallTimeout) else classify_provider_error(exc)
                if rate_limiter is not None:
                    rate_limiter.record_result(
                        resolved_name,
                        "rate_limited" if error_code == "provider_rate_limit" else "failed",
                    )
                attempts.append({
                    "provider": resolved_name,
                    "status": error_code,
                    "row_count": 0,
                    "latency_ms": round((time.perf_counter() - started_at) * 1000.0, 2),
                    "retryable": error_code != "provider_auth_error",
                    "error": str(exc),
                    "requested_symbol": symbol,
                    "provider_symbol": provider_symbol,
                })
                continue

    if best_partial is not None:
        best_history, best_provider = best_partial
        history_gap_rows = max(0, min_history_days - len(best_history))
        raise MarketDataCollectionError(
            f"All providers returned insufficient history; best result was {len(best_history)} rows "
            f"from {best_provider}, expected at least {min_history_days}.",
            code="all_providers_insufficient_history",
            attempts=attempts,
            retryable=False,
            details={
                "best_provider": best_provider,
                "best_row_count": len(best_history),
                "required_rows": min_history_days,
                "history_gap_rows": history_gap_rows,
                "failure_class": "eligibility_gap",
                "retry_policy": "retry_after_more_trading_history",
                "retry_after_trading_sessions": history_gap_rows,
                "immediate_retry_recommended": False,
            },
        )
    raise MarketDataCollectionError(
        "All configured market-data providers failed.",
        code="all_providers_exhausted",
        attempts=attempts,
        retryable=any(bool(item.get("retryable")) for item in attempts),
        details={
            "required_rows": min_history_days,
            "failure_class": "provider_coverage_gap"
            if any(
                item.get("status") in {"provider_empty_response", "provider_rate_policy_blocked"}
                for item in attempts
            )
            else "technical_failure",
        },
    )


def fetch_fred_macro_context(data_sources_cfg: dict[str, Any]) -> dict[str, Any]:
    macro_cfg = data_sources_cfg.get("macro", {}).get("fred", {})
    if not bool(macro_cfg.get("enabled", True)):
        return {}
    api_key = os.getenv("FRED_API_KEY", "").strip()
    if not api_key:
        return {"provider": "fred", "status": "skipped", "reason": "missing_api_key"}
    series_ids = macro_cfg.get("series_ids", ["FEDFUNDS", "DGS10", "CPIAUCSL"])
    max_age = int(macro_cfg.get("max_observation_age_days", 30))
    out: dict[str, Any] = {"provider": "fred", "series": {}, "as_of": now_athens().isoformat()}
    for series_id in series_ids:
        sid = str(series_id).strip()
        if not sid:
            continue
        url = (
            "https://api.stlouisfed.org/fred/series/observations?"
            + urlparse.urlencode(
                {
                    "series_id": sid,
                    "api_key": api_key,
                    "file_type": "json",
                    "sort_order": "desc",
                    "limit": "13",
                }
            )
        )
        try:
            payload = fetch_json_url(url)
            observations = payload.get("observations", [])
            if observations:
                obs = observations[0]
                obs_age = age_days(obs.get("date"))
                out["series"][sid] = {
                    "date": obs.get("date"),
                    "value": obs.get("value"),
                    "age_days": obs_age,
                    "fresh": obs_age is not None and obs_age <= max_age,
                    "max_age_days": max_age,
                    "observations": [
                        {"date": item.get("date"), "value": item.get("value")}
                        for item in observations if item.get("value") not in (None, ".")
                    ],
                }
        except Exception as exc:
            out["series"][sid] = {"error": str(exc)}
    out["regime_assessment"] = macro_regime.classify(out)
    return out


def evaluate_rules(
    metrics: dict[str, Any], rules: list[dict[str, Any]]
) -> tuple[float, float, list[dict[str, Any]]]:
    total_weight = 0.0
    available_weight = 0.0
    matched_weight = 0.0
    details: list[dict[str, Any]] = []

    for rule in rules:
        metric_key = str(rule.get("metric", "")).strip()
        operator = str(rule.get("operator", "")).strip()
        weight = to_float(rule.get("weight")) or 1.0
        threshold = to_float(rule.get("value"))
        observed = to_float(metrics.get(metric_key))

        total_weight += max(weight, 0.0)

        if operator not in OPERATORS or threshold is None or observed is None:
            details.append(
                {
                    "metric": metric_key,
                    "status": "missing_data",
                    "operator": operator,
                    "threshold": threshold,
                    "observed": observed,
                    "weight": weight,
                }
            )
            continue

        available_weight += max(weight, 0.0)
        # a value without meaning (a negative forward P/E: losses expected) fails and opens a gap to be filled
        # (owner 2026-10-22, valuation_gaps.py); it never passes a "<=" rule by being negative
        without_meaning = valuation_gaps.invalid(rule, observed)
        passed = False if without_meaning else OPERATORS[operator](observed, threshold)
        if passed:
            matched_weight += weight

        details.append(
            {
                "metric": metric_key,
                "status": "passed" if passed else "failed",
                "operator": operator,
                "threshold": threshold,
                "observed": observed,
                "weight": weight,
                **({"invalid_at_or_below": rule["invalid_at_or_below"]} if "invalid_at_or_below" in rule else {}),
                **({"gap": valuation_gaps.REASON} if without_meaning else {}),
            }
        )

    coverage = (available_weight / total_weight * 100.0) if total_weight > 0 else 0.0
    # Missing data must not improve a score by disappearing from the denominator.
    # Coverage is still reported separately, while the score is explicitly
    # completeness-adjusted against the full configured rule set.
    score = (matched_weight / total_weight * 100.0) if total_weight > 0 else 0.0
    return round(score, 2), round(coverage, 2), details


def moving_average_metrics(history: pd.DataFrame) -> dict[str, Any]:
    close = pd.to_numeric(history["Close"], errors="coerce").dropna()

    sma_20 = close.rolling(20).mean().iloc[-1] if len(close) >= 20 else None
    sma_50 = close.rolling(50).mean().iloc[-1] if len(close) >= 50 else None
    sma_200 = close.rolling(200).mean().iloc[-1] if len(close) >= 200 else None
    last_close = close.iloc[-1]

    def bool_metric(flag: bool | None) -> float | None:
        if flag is None:
            return None
        return 1.0 if flag else 0.0

    close_above_sma_20 = bool_metric(last_close > sma_20) if sma_20 is not None else None
    close_above_sma_50 = bool_metric(last_close > sma_50) if sma_50 is not None else None
    close_above_sma_200 = bool_metric(last_close > sma_200) if sma_200 is not None else None
    sma_20_above_sma_50 = bool_metric(sma_20 > sma_50) if sma_20 is not None and sma_50 is not None else None
    sma_50_above_sma_200 = bool_metric(sma_50 > sma_200) if sma_50 is not None and sma_200 is not None else None

    close_to_sma_200_pct = None
    if sma_200 is not None and sma_200 != 0:
        close_to_sma_200_pct = (last_close / sma_200 - 1.0) * 100.0

    ema_12 = close.ewm(span=12, adjust=False).mean().iloc[-1] if len(close) >= 12 else None
    ema_26_series = close.ewm(span=26, adjust=False).mean() if len(close) >= 26 else None
    ema_26 = ema_26_series.iloc[-1] if ema_26_series is not None else None
    macd_series = None
    macd = None
    macd_signal = None
    macd_histogram = None
    if len(close) >= 35 and ema_26_series is not None:
        macd_series = close.ewm(span=12, adjust=False).mean() - ema_26_series
        signal_series = macd_series.ewm(span=9, adjust=False).mean()
        macd = macd_series.iloc[-1]
        macd_signal = signal_series.iloc[-1]
        macd_histogram = macd - macd_signal

    rsi_14 = None
    if len(close) >= 15:
        delta = close.diff()
        gain = delta.clip(lower=0).rolling(14).mean()
        loss = (-delta.clip(upper=0)).rolling(14).mean()
        relative_strength = gain / loss.replace(0, pd.NA)
        rsi = 100.0 - (100.0 / (1.0 + relative_strength))
        rsi_14 = rsi.iloc[-1]
        if pd.isna(rsi_14):
            last_loss = loss.iloc[-1]
            last_gain = gain.iloc[-1]
            if last_loss == 0 and last_gain and last_gain > 0:
                rsi_14 = 100.0

    bb_upper = None
    bb_lower = None
    bb_width_pct = None
    if len(close) >= 20 and sma_20 is not None:
        bb_std = close.rolling(20).std().iloc[-1]
        bb_upper = sma_20 + (2.0 * bb_std)
        bb_lower = sma_20 - (2.0 * bb_std)
        if sma_20 != 0:
            bb_width_pct = ((bb_upper - bb_lower) / sma_20) * 100.0

    atr_14 = None
    atr_14_pct = None
    if {"High", "Low"}.issubset(history.columns) and len(history) >= 15:
        high = pd.to_numeric(history["High"], errors="coerce")
        low = pd.to_numeric(history["Low"], errors="coerce")
        previous_close = pd.to_numeric(history["Close"], errors="coerce").shift(1)
        true_range = pd.concat(
            [(high - low), (high - previous_close).abs(), (low - previous_close).abs()],
            axis=1,
        ).max(axis=1)
        atr_14 = true_range.rolling(14).mean().iloc[-1]
        if last_close != 0:
            atr_14_pct = (atr_14 / last_close) * 100.0

    volume_sma_20 = None
    volume_ratio_20 = None
    if "Volume" in history.columns and len(history) >= 20:
        volume = pd.to_numeric(history["Volume"], errors="coerce")
        volume_sma_20 = volume.rolling(20).mean().iloc[-1]
        last_volume = volume.iloc[-1]
        if volume_sma_20 and volume_sma_20 != 0:
            volume_ratio_20 = last_volume / volume_sma_20

    returns = close.pct_change()
    return_20d_pct = ((last_close / close.iloc[-21]) - 1.0) * 100.0 if len(close) >= 21 and close.iloc[-21] != 0 else None
    return_60d_pct = ((last_close / close.iloc[-61]) - 1.0) * 100.0 if len(close) >= 61 and close.iloc[-61] != 0 else None
    volatility_20d_pct = returns.rolling(20).std().iloc[-1] * (252.0**0.5) * 100.0 if len(close) >= 21 else None
    low_20d = close.tail(20).min() if len(close) >= 20 else None
    high_20d = close.tail(20).max() if len(close) >= 20 else None
    low_60d = close.tail(60).min() if len(close) >= 60 else None
    high_60d = close.tail(60).max() if len(close) >= 60 else None

    def range_position(low_value: Any, high_value: Any) -> float | None:
        low_num = to_float(low_value)
        high_num = to_float(high_value)
        if low_num is None or high_num is None or high_num == low_num:
            return None
        return ((last_close - low_num) / (high_num - low_num)) * 100.0

    def distance_from_low(low_value: Any) -> float | None:
        low_num = to_float(low_value)
        if low_num is None or low_num == 0:
            return None
        return ((last_close / low_num) - 1.0) * 100.0

    def distance_from_high(high_value: Any) -> float | None:
        high_num = to_float(high_value)
        if high_num is None or high_num == 0:
            return None
        return ((last_close / high_num) - 1.0) * 100.0

    candlesticks = candlestick_signals.analyze_candlestick_patterns(history)

    return {
        "close": to_float(last_close),
        "sma_20": to_float(sma_20),
        "sma_50": to_float(sma_50),
        "sma_200": to_float(sma_200),
        "close_above_sma_20": close_above_sma_20,
        "close_above_sma_50": close_above_sma_50,
        "close_above_sma_200": close_above_sma_200,
        "sma_20_above_sma_50": sma_20_above_sma_50,
        "sma_50_above_sma_200": sma_50_above_sma_200,
        "close_to_sma_200_pct": to_float(close_to_sma_200_pct),
        "ema_12": to_float(ema_12),
        "ema_26": to_float(ema_26),
        "macd": to_float(macd),
        "macd_signal": to_float(macd_signal),
        "macd_histogram": to_float(macd_histogram),
        "rsi_14": to_float(rsi_14),
        "bb_upper_20": to_float(bb_upper),
        "bb_lower_20": to_float(bb_lower),
        "bb_width_20_pct": to_float(bb_width_pct),
        "atr_14": to_float(atr_14),
        "atr_14_pct": to_float(atr_14_pct),
        "volume_sma_20": to_float(volume_sma_20),
        "volume_ratio_20": to_float(volume_ratio_20),
        "return_20d_pct": to_float(return_20d_pct),
        "return_60d_pct": to_float(return_60d_pct),
        "low_20d": to_float(low_20d),
        "high_20d": to_float(high_20d),
        "low_60d": to_float(low_60d),
        "high_60d": to_float(high_60d),
        "price_position_20d_pct": to_float(range_position(low_20d, high_20d)),
        "price_position_60d_pct": to_float(range_position(low_60d, high_60d)),
        "distance_from_20d_low_pct": to_float(distance_from_low(low_20d)),
        "distance_from_60d_low_pct": to_float(distance_from_low(low_60d)),
        "distance_from_20d_high_pct": to_float(distance_from_high(high_20d)),
        "distance_from_60d_high_pct": to_float(distance_from_high(high_60d)),
        "volatility_20d_pct": to_float(volatility_20d_pct),
        "candlestick_patterns": candlesticks.get("candlestick_patterns", []),
        "candlestick_bias": candlesticks.get("candlestick_bias"),
        "candlestick_score": to_float(candlesticks.get("candlestick_score")),
        "candlestick_last_signal": candlesticks.get("candlestick_last_signal"),
        "candlestick_bullish_count": int(candlesticks.get("candlestick_bullish_count", 0) or 0),
        "candlestick_bearish_count": int(candlesticks.get("candlestick_bearish_count", 0) or 0),
    }


class ProviderCallTimeout(Exception):
    """A provider call did not return within its time limit (the result is unknown, not zero)."""


_HUNG_PROVIDER_THREADS: list[threading.Thread] = []
MAX_HUNG_PROVIDER_THREADS = 12


def call_with_timeout(func: Any, timeout_seconds: float | None, label: str) -> Any:
    """Run a provider call that has no timeout of its own and give up after ``timeout_seconds``.

    A yfinance request that hangs once stopped the Babis scan for ~9 hours without any error
    (2026-10-02). The call runs in a daemon thread; on timeout the thread is abandoned (it cannot be
    killed) and ``ProviderCallTimeout`` is raised so the caller records the value as unknown. When
    too many abandoned calls are still hanging, further calls fail at once instead of piling up.
    ``timeout_seconds`` of 0/None disables the limit.
    """
    if not timeout_seconds or timeout_seconds <= 0:
        return func()
    _HUNG_PROVIDER_THREADS[:] = [thread for thread in _HUNG_PROVIDER_THREADS if thread.is_alive()]
    if len(_HUNG_PROVIDER_THREADS) >= MAX_HUNG_PROVIDER_THREADS:
        raise ProviderCallTimeout(f"{label}: {len(_HUNG_PROVIDER_THREADS)} earlier calls are still hanging")
    box: dict[str, Any] = {}

    def target() -> None:
        try:
            box["value"] = func()
        except BaseException as exc:  # noqa: BLE001 - re-raised in the calling thread
            box["error"] = exc

    worker = threading.Thread(target=target, name=f"provider-call-{label}", daemon=True)
    worker.start()
    worker.join(timeout_seconds)
    if worker.is_alive():
        _HUNG_PROVIDER_THREADS.append(worker)
        raise ProviderCallTimeout(f"{label} did not answer within {timeout_seconds:g}s")
    if "error" in box:
        raise box["error"]
    return box.get("value")


def provider_call_timeout(data_sources_cfg: dict[str, Any] | None) -> float:
    market = (data_sources_cfg or {}).get("market_data") or {}
    try:
        return float(market.get("yfinance_call_timeout_seconds", 45))
    except (TypeError, ValueError):
        return 45.0


def fetch_snapshot(
    symbol: str,
    lookback_days: int,
    min_history_days: int,
    data_sources_cfg: dict[str, Any] | None = None,
    snapshot_profile: str = "full",
    listing_context: dict[str, Any] | None = None,
    rate_limiter: SharedProviderRateLimiter | None = None,
    info_cache: Any = None,
) -> dict[str, Any]:
    cfg = data_sources_cfg or {}
    listing_context = dict(listing_context or {})
    company_fundamental_eligible = bool(
        listing_context.get("company_fundamental_eligible", True)
    )
    snapshot_started_at = time.perf_counter()
    market_started_at = time.perf_counter()
    history, market_data_provider, market_data_attempts = fetch_history_with_routing(
        symbol,
        lookback_days,
        cfg,
        min_history_days=min_history_days,
        rate_limiter=rate_limiter,
    )
    market_elapsed = time.perf_counter() - market_started_at
    ticker = yf.Ticker(symbol)
    if len(history) < min_history_days:
        history_gap_rows = max(0, min_history_days - len(history))
        raise MarketDataCollectionError(
            f"Selected provider returned {len(history)} rows, expected at least {min_history_days}.",
            code="provider_partial_history",
            attempts=market_data_attempts,
            retryable=False,
            details={
                "selected_provider": market_data_provider,
                "best_row_count": len(history),
                "required_rows": min_history_days,
                "history_gap_rows": history_gap_rows,
                "failure_class": "eligibility_gap",
                "retry_policy": "retry_after_more_trading_history",
                "retry_after_trading_sessions": history_gap_rows,
                "immediate_retry_recommended": False,
            },
        )

    metrics = moving_average_metrics(history)

    info_started_at = time.perf_counter()
    call_limit = provider_call_timeout(cfg)
    provider_timeouts: list[str] = []
    info: dict[str, Any] = {}
    fast_info: dict[str, Any] = {}
    info_source: dict[str, Any] = {"source": "yfinance"}
    cached_info = None
    if company_fundamental_eligible and info_cache is not None:
        try:
            close_today = to_float(pd.to_numeric(history["Close"], errors="coerce").dropna().iloc[-1])
        except (KeyError, IndexError):
            close_today = None
        cached_info, decision = info_cache.decide(symbol, close_today)
        info_source = decision if cached_info is not None else {"source": "yfinance",
                                                                "cache_decision": decision.get("reason")}
    if cached_info is not None:
        info = cached_info
    elif company_fundamental_eligible or info_cache is None or not info_cache.lean:
        try:
            info = call_with_timeout(ticker.get_info, call_limit, f"get_info {symbol}")
        except ProviderCallTimeout as exc:
            info, info_source = {}, {"source": "yfinance", "status": "provider_timeout", "detail": str(exc)}
            provider_timeouts.append("get_info")
        except Exception:
            info = {}
        if not isinstance(info, dict):
            info = {}
        try:
            fast_info = dict(call_with_timeout(lambda: dict(ticker.fast_info), call_limit, f"fast_info {symbol}"))
        except ProviderCallTimeout:
            provider_timeouts.append("fast_info")
        except Exception:
            fast_info = {}
        if company_fundamental_eligible and info_cache is not None and info:
            # a field Yahoo left out keeps the stored value when no report was filed since (stage1_info_cache)
            info, carried = info_cache.fill_gaps(symbol, info)
            if carried:
                info_source["carried_fields"] = carried
            info_cache.store(symbol, info, carried)
    else:
        info_source = {"source": "skipped_not_a_company"}

    raw_news = []
    news_status = "fetched"
    if info_cache is None or info_cache.wants_news(symbol):
        try:
            raw_news = call_with_timeout(lambda: ticker.news or [], call_limit, f"news {symbol}") or []
        except ProviderCallTimeout:
            raw_news, news_status = [], "provider_timeout"
            provider_timeouts.append("news")
        except Exception:
            raw_news = []
    else:
        news_status = "not_fetched_outside_watchlist"
    info_elapsed = time.perf_counter() - info_started_at

    news_items: list[dict[str, Any]] = []
    for item in raw_news[:6]:
        news_items.append(
            {
                "title": item.get("title"),
                "publisher": item.get("publisher"),
                "link": item.get("link"),
                "published_at": format_datetime_from_epoch(item.get("providerPublishTime")),
            }
        )

    fundamentals = {
        "market_cap": to_float(info.get("marketCap") or fast_info.get("market_cap") or fast_info.get("marketCap")),
        "enterprise_value": to_float(info.get("enterpriseValue")),
        "revenue_ttm": to_float(info.get("totalRevenue")),
        "gross_margin": to_float(info.get("grossMargins")),
        "operating_margin": to_float(info.get("operatingMargins")),
        "profit_margin": to_float(info.get("profitMargins")),
        "revenue_growth": to_float(info.get("revenueGrowth")),
        "earnings_growth": to_float(info.get("earningsGrowth")),
        "roe": to_float(info.get("returnOnEquity")),
        "roa": to_float(info.get("returnOnAssets")),
        "forward_pe": forward_pe_from(info),
        "trailing_pe": to_float(info.get("trailingPE")),
        "price_to_sales": to_float(info.get("priceToSalesTrailing12Months")),
        "price_to_book": to_float(info.get("priceToBook")),
        "peg_ratio": to_float(info.get("pegRatio")),
        "total_cash": to_float(info.get("totalCash")),
        "total_debt": to_float(info.get("totalDebt")),
        "debt_to_equity": to_float(info.get("debtToEquity")),
        "free_cashflow": to_float(info.get("freeCashflow")),
        "operating_cashflow": to_float(info.get("operatingCashflow")),
        "current_ratio": to_float(info.get("currentRatio")),
        "quick_ratio": to_float(info.get("quickRatio")),
        "shares_outstanding": to_float(info.get("sharesOutstanding")),
        "net_income_ttm": to_float(info.get("netIncomeToCommon")),
    } if company_fundamental_eligible else {}

    sec_filings: list[dict[str, Any]] = []
    sec_error: str | None = None
    sec_started_at = time.perf_counter()
    if company_fundamental_eligible:
        try:
            sec_filings = fetch_sec_recent_filings(symbol, cfg)
        except Exception as exc:
            sec_error = str(exc)
    else:
        sec_error = "not_applicable_for_instrument_route"
    sec_elapsed = time.perf_counter() - sec_started_at

    openbb_started_at = time.perf_counter()
    if company_fundamental_eligible:
        openbb_research = openbb_adapter.fetch_research_context(
            symbol,
            cfg.get("openbb_research", {}) if isinstance(cfg.get("openbb_research", {}), dict) else {},
        )
    else:
        openbb_research = {"status": "skipped", "reason": "not_applicable_for_instrument_route"}
    openbb_elapsed = time.perf_counter() - openbb_started_at
    snapshot_elapsed = time.perf_counter() - snapshot_started_at

    return {
        "symbol": symbol,
        "company_name": info.get("longName") or info.get("shortName") or listing_context.get("name") or symbol,
        "sector": info.get("sector") or listing_context.get("node_id"),
        "industry": info.get("industry"),
        "currency": info.get("currency") or fast_info.get("currency"),
        "listing_context": listing_context,
        "history_rows": int(len(history)),
        "price_metrics": metrics,
        "fundamentals": fundamentals,
        "news": news_items,
        "sec_recent_filings": sec_filings,
        "openbb_research": openbb_research,
        "source_base": {
            "primary_data_feed": "SEC EDGAR filings + provider-routed market data",
            "market_data_provider": market_data_provider,
            "market_data_provider_attempts": market_data_attempts,
            "market_data_provider_errors": [
                item
                for item in market_data_attempts
                if item.get("status") not in {"success", "insufficient_result"}
            ],
            "fundamentals_provider": "yfinance",
            "fundamentals_source": info_source,
            "news_status": news_status,
            "provider_timeouts": provider_timeouts,
            "openbb_research_status": openbb_research.get("status"),
            "openbb_research_errors": openbb_research.get("errors", []),
            "sec_user_agent_set": bool(os.getenv("SEC_USER_AGENT", "").strip()),
            "sec_filings_error": sec_error,
            "snapshot_profile": snapshot_profile,
            "research_route": listing_context.get("research_route", "company_fundamental_research"),
            "company_fundamental_eligible": company_fundamental_eligible,
            "snapshot_time": now_athens().isoformat(),
            "timing_seconds": {
                "market_data": round(market_elapsed, 4),
                "fundamentals_and_news": round(info_elapsed, 4),
                "sec": round(sec_elapsed, 4),
                "openbb_research": round(openbb_elapsed, 4),
                "snapshot_total": round(snapshot_elapsed, 4),
            },
            "limitations": [
                "Coverage varies by issuer.",
                "Some fields may be stale or missing.",
                "Fundamental point-in-time values are not fully standardized across all issuers.",
                "OpenBB ownership and OTC flow data are supplementary context, not primary filings.",
            ] + ([] if company_fundamental_eligible else [
                "Company fundamentals, SEC filings and ownership research are intentionally skipped for this instrument route."
            ]),
        },
    }


def forward_pe_from(info: dict[str, Any]) -> float | None:
    """Yahoo's forward P/E; without it, price / forward EPS when the forward EPS is positive (another way to find
    the value an open valuation gap is waiting for). Otherwise unknown."""
    value = to_float(info.get("forwardPE"))
    if value is not None:
        return value
    eps, price = to_float(info.get("forwardEps")), to_float(info.get("currentPrice"))
    return round(price / eps, 4) if eps and eps > 0 and price and price > 0 else None


def build_scan_row(snapshot: dict[str, Any]) -> dict[str, Any]:
    source_base = snapshot.get("source_base", {})
    smart_money = ((snapshot.get("openbb_research") or {}).get("summary") or {})
    listing_context = snapshot.get("listing_context") or {}
    row = {
        "symbol": snapshot["symbol"],
        "company_name": snapshot.get("company_name"),
        "sector": snapshot.get("sector"),
        "industry": snapshot.get("industry"),
        "currency": snapshot.get("currency"),
        "market_data_provider": source_base.get("market_data_provider"),
        "openbb_research_status": source_base.get("openbb_research_status"),
        "venue_code": listing_context.get("venue_code"),
        "venue": listing_context.get("venue"),
        "instrument_type": listing_context.get("instrument_type"),
        "node_id": listing_context.get("node_id"),
        "research_route": listing_context.get("research_route", "company_fundamental_research"),
        "company_fundamental_eligible": bool(
            listing_context.get("company_fundamental_eligible", True)
        ),
    }
    row.update(snapshot.get("price_metrics", {}))
    row.update(snapshot.get("fundamentals", {}))
    row.update(
        {
            "smart_money_signal": smart_money.get("smart_money_signal"),
            "smart_money_score": to_float(smart_money.get("smart_money_score")),
            "smart_money_notes": smart_money.get("smart_money_notes", []),
            "institution_ownership": to_float(smart_money.get("institution_ownership")),
            "institutions_count": to_float(smart_money.get("institutions_count")),
            "insider_ownership": to_float(smart_money.get("insider_ownership")),
            "institution_float_ownership": to_float(smart_money.get("institution_float_ownership")),
            "short_percent_of_float": to_float(smart_money.get("short_percent_of_float")),
            "days_to_cover": to_float(smart_money.get("days_to_cover")),
            "open_market_buy_count": to_float(smart_money.get("open_market_buy_count")),
            "open_market_sell_count": to_float(smart_money.get("open_market_sell_count")),
            "open_market_buy_value": to_float(smart_money.get("open_market_buy_value")),
            "open_market_sell_value": to_float(smart_money.get("open_market_sell_value")),
            "net_open_market_value": to_float(smart_money.get("net_open_market_value")),
            "darkpool_activity_ratio": to_float(smart_money.get("darkpool_activity_ratio")),
            "darkpool_last_week_shares": to_float(smart_money.get("darkpool_last_week_shares")),
            "darkpool_last_week_date": smart_money.get("darkpool_last_week_date"),
            "latest_insider_filing_date": smart_money.get("latest_insider_filing_date"),
            "latest_insider_transaction_date": smart_money.get("latest_insider_transaction_date"),
            "recent_insider_names": smart_money.get("recent_insider_names", []),
        }
    )
    return row


def merge_context_into_scan_row(row: dict[str, Any], snapshot: dict[str, Any]) -> None:
    source_base = snapshot.get("source_base", {})
    smart_money = ((snapshot.get("openbb_research") or {}).get("summary") or {})
    row["news"] = snapshot.get("news", [])
    row["sec_recent_filings"] = snapshot.get("sec_recent_filings", [])
    row["source_base"] = source_base
    row["market_data_provider"] = source_base.get("market_data_provider", row.get("market_data_provider"))
    row["openbb_research_status"] = source_base.get("openbb_research_status")
    row["smart_money_signal"] = smart_money.get("smart_money_signal")
    row["smart_money_score"] = to_float(smart_money.get("smart_money_score"))
    row["smart_money_notes"] = smart_money.get("smart_money_notes", [])
    row["institution_ownership"] = to_float(smart_money.get("institution_ownership"))
    row["institutions_count"] = to_float(smart_money.get("institutions_count"))
    row["insider_ownership"] = to_float(smart_money.get("insider_ownership"))
    row["institution_float_ownership"] = to_float(smart_money.get("institution_float_ownership"))
    row["short_percent_of_float"] = to_float(smart_money.get("short_percent_of_float"))
    row["days_to_cover"] = to_float(smart_money.get("days_to_cover"))
    row["open_market_buy_count"] = to_float(smart_money.get("open_market_buy_count"))
    row["open_market_sell_count"] = to_float(smart_money.get("open_market_sell_count"))
    row["open_market_buy_value"] = to_float(smart_money.get("open_market_buy_value"))
    row["open_market_sell_value"] = to_float(smart_money.get("open_market_sell_value"))
    row["net_open_market_value"] = to_float(smart_money.get("net_open_market_value"))
    row["darkpool_activity_ratio"] = to_float(smart_money.get("darkpool_activity_ratio"))
    row["darkpool_last_week_shares"] = to_float(smart_money.get("darkpool_last_week_shares"))
    row["darkpool_last_week_date"] = smart_money.get("darkpool_last_week_date")
    row["latest_insider_filing_date"] = smart_money.get("latest_insider_filing_date")
    row["latest_insider_transaction_date"] = smart_money.get("latest_insider_transaction_date")
    row["recent_insider_names"] = smart_money.get("recent_insider_names", [])


def empty_runtime_summary(profile: str, total_symbols: int) -> dict[str, Any]:
    return {
        "profile": profile,
        "symbols_total": total_symbols,
        "processed_symbols": 0,
        "elapsed_seconds": 0.0,
        "avg_snapshot_seconds": 0.0,
        "market_data_seconds": 0.0,
        "fundamentals_and_news_seconds": 0.0,
        "sec_seconds": 0.0,
        "openbb_research_seconds": 0.0,
    }


def read_prompt(prompt_path: Path, agent_name: str | None = None) -> str:
    if not prompt_path.exists():
        return ""
    base_prompt = prompt_path.read_text(encoding="utf-8").strip()
    if not base_prompt or not agent_name:
        return base_prompt
    root = Path(__file__).resolve().parent
    with_methodology = agent_methodology.attach_methodology(
        base_prompt,
        agent_name,
        root / "agent_methodology_registry.json",
    )
    return agent_personality.attach_personality(
        with_methodology,
        agent_name,
        root / "agent_personality_registry.json",
    )


def call_ollama(
    host: str,
    model: str,
    system_prompt: str,
    user_prompt: str,
    temperature: float,
    num_predict: int,
    timeout_seconds: int = 120,
    output_json: bool = False,
) -> str:
    endpoint = host.rstrip("/") + "/api/generate"
    payload = {
        "model": model,
        "system": system_prompt,
        "prompt": user_prompt,
        "stream": False,
        "options": {
            "temperature": temperature,
            "num_predict": num_predict,
        },
    }
    if output_json:
        payload["format"] = "json"

    data = json.dumps(payload).encode("utf-8")
    req = urlrequest.Request(endpoint, data=data, headers={"Content-Type": "application/json"}, method="POST")

    try:
        with urlrequest.urlopen(req, timeout=timeout_seconds) as response:
            parsed = json.loads(response.read().decode("utf-8"))
    except urlerror.URLError as exc:
        return f"[OLLAMA_ERROR] {exc}"
    except (TimeoutError, socket.timeout):
        return f"[OLLAMA_ERROR] timeout after {timeout_seconds}s"
    except json.JSONDecodeError as exc:
        return f"[OLLAMA_ERROR] Invalid JSON response: {exc}"

    text = parsed.get("response", "")
    return str(text).strip()


def build_llm_prompt(snapshot: dict[str, Any], extra_question: str | None) -> str:
    fm = snapshot.get("fundamentals", {})
    pm = snapshot.get("price_metrics", {})
    news = snapshot.get("news", [])
    sec_filings = snapshot.get("sec_recent_filings", [])
    openbb_research = snapshot.get("openbb_research", {})
    smart_money = openbb_research.get("summary", {}) if isinstance(openbb_research, dict) else {}
    source_base = snapshot.get("source_base", {})

    lines = [
        f"Ticker: {snapshot.get('symbol')}",
        f"Company: {snapshot.get('company_name')}",
        f"Sector: {snapshot.get('sector')}",
        f"Industry: {snapshot.get('industry')}",
        "",
        "Data snapshot for analysis:",
        format_metric_line("Market Cap", fm.get("market_cap")),
        format_metric_line("Revenue TTM", fm.get("revenue_ttm")),
        format_metric_line("Gross Margin", fm.get("gross_margin")),
        format_metric_line("Operating Margin", fm.get("operating_margin")),
        format_metric_line("Profit Margin", fm.get("profit_margin")),
        format_metric_line("Revenue Growth", fm.get("revenue_growth")),
        format_metric_line("Earnings Growth", fm.get("earnings_growth")),
        format_metric_line("ROE", fm.get("roe")),
        format_metric_line("Total Cash", fm.get("total_cash")),
        format_metric_line("Total Debt", fm.get("total_debt")),
        format_metric_line("Debt To Equity", fm.get("debt_to_equity")),
        format_metric_line("Free Cash Flow", fm.get("free_cashflow")),
        format_metric_line("Current Ratio", fm.get("current_ratio")),
        format_metric_line("Forward PE", fm.get("forward_pe")),
        "",
        "Technical context (timing/risk support only):",
        format_metric_line("Close", pm.get("close")),
        format_metric_line("RSI 14", pm.get("rsi_14")),
        format_metric_line("MACD Histogram", pm.get("macd_histogram")),
        format_metric_line("ATR 14 %", pm.get("atr_14_pct")),
        format_metric_line("Bollinger Width 20 %", pm.get("bb_width_20_pct")),
        format_metric_line("Volume Ratio 20", pm.get("volume_ratio_20")),
        format_metric_line("Return 20D %", pm.get("return_20d_pct")),
        format_metric_line("Volatility 20D %", pm.get("volatility_20d_pct")),
        "",
        "Recent headlines (secondary sources):",
    ]

    if not news:
        lines.append("- No recent headlines in snapshot.")
    else:
        for item in news:
            lines.append(
                f"- {item.get('title')} | Publisher: {item.get('publisher')} | Date: {item.get('published_at')} | Link: {item.get('link')}"
            )

    lines.append("")
    lines.append("Primary-source SEC filings (most recent):")
    if not sec_filings:
        lines.append("- No recent SEC filing entries available in snapshot.")
    else:
        for filing in sec_filings:
            lines.append(
                f"- {filing.get('form')} | Date: {filing.get('filing_date')} | Accession: {filing.get('accession_number')} | Source: {filing.get('issuer')} | Link: {filing.get('url')}"
            )

    lines.append("")
    lines.append("OpenBB ownership / large-player context (supplementary):")
    if str(openbb_research.get("status") or "") in {"error", "disabled"} or not smart_money:
        lines.append("- No OpenBB supplementary ownership/flow context available in snapshot.")
    else:
        lines.append(f"- Smart Money Signal: {smart_money.get('smart_money_signal')}")
        lines.append(format_metric_line("Smart Money Score", smart_money.get("smart_money_score")))
        lines.append(format_metric_line("Institution Ownership", smart_money.get("institution_ownership")))
        lines.append(format_metric_line("Insider Ownership", smart_money.get("insider_ownership")))
        lines.append(format_metric_line("Short % Float", smart_money.get("short_percent_of_float")))
        lines.append(format_metric_line("Days To Cover", smart_money.get("days_to_cover")))
        lines.append(format_metric_line("Open-Market Buys", smart_money.get("open_market_buy_count")))
        lines.append(format_metric_line("Open-Market Sells", smart_money.get("open_market_sell_count")))
        lines.append(format_metric_line("Net Insider Value", smart_money.get("net_open_market_value")))
        lines.append(format_metric_line("Darkpool Activity Ratio", smart_money.get("darkpool_activity_ratio")))
        if smart_money.get("recent_insider_names"):
            lines.append(f"- Recent Insider Names: {', '.join(smart_money.get('recent_insider_names', [])[:5])}")
        notes = smart_money.get("smart_money_notes", [])
        if notes:
            for note in notes[:4]:
                lines.append(f"- Note: {note}")

    lines.extend(
        [
            "",
            "Source-Base note:",
            f"- Primary feed: {source_base.get('primary_data_feed')}",
            f"- Market data provider: {source_base.get('market_data_provider')}",
            f"- OpenBB research status: {source_base.get('openbb_research_status')}",
            f"- SEC user-agent configured: {source_base.get('sec_user_agent_set')}",
            f"- Snapshot time: {source_base.get('snapshot_time')}",
            f"- Limitations: {', '.join(source_base.get('limitations', []))}",
            "",
            "Instructions for this run:",
            "- Work in Greek.",
            "- Use only provided evidence and explicitly state insufficiency when required.",
            "- Separate [ΓΕΓΟΝΟΣ], [ΑΝΑΛΥΣΗ], [ΕΚΤΙΜΗΣΗ].",
            "- Treat technical indicators as timing/risk context, not as a standalone investment thesis.",
            "- Include sources for critical facts using available data fields.",
        ]
    )

    if extra_question:
        lines.append("")
        lines.append(f"User focus: {extra_question}")

    return "\n".join(lines)


def to_markdown_table(rows: list[dict[str, Any]]) -> str:
    headers = [
        "symbol",
        "composite_score",
        "fundamental_score",
        "ma_score",
        "close",
        "sma_50",
        "sma_200",
        "rsi_14",
        "macd_histogram",
        "atr_14_pct",
        "volume_ratio_20",
        "revenue_growth",
        "roe",
        "debt_to_equity",
    ]
    md_lines = ["| " + " | ".join(headers) + " |", "| " + " | ".join(["---"] * len(headers)) + " |"]
    for row in rows:
        values = []
        for key in headers:
            value = row.get(key)
            if isinstance(value, str):
                values.append(value)
                continue
            numeric = to_float(value)
            if numeric is None:
                values.append("N/A")
            else:
                values.append(f"{numeric:.2f}")
        md_lines.append("| " + " | ".join(values) + " |")
    return "\n".join(md_lines)


def mini_report_text(
    run_timestamp: str,
    top_rows: list[dict[str, Any]],
    macro_context: dict[str, Any],
    max_price: float | None,
    min_composite_score: float | None,
) -> str:
    def fmt(value: Any, decimals: int = 2) -> str:
        num = to_float(value)
        if num is None:
            return "N/A"
        return f"{num:.{decimals}f}"

    lines = [
        "# Mini Fundamental Report",
        "",
        f"- Run timestamp: {run_timestamp}",
        f"- Max price filter: {fmt(max_price)}" if max_price is not None else "- Max price filter: disabled",
        f"- Min composite filter: {fmt(min_composite_score)}"
        if min_composite_score is not None
        else "- Min composite filter: disabled",
        "",
        "## Macro Snapshot",
        "",
    ]

    series = macro_context.get("series", {}) if isinstance(macro_context, dict) else {}
    if not series:
        lines.append("- Macro context unavailable.")
    else:
        for sid, payload in series.items():
            if isinstance(payload, dict):
                lines.append(f"- {sid}: {payload.get('value')} (date: {payload.get('date')})")

    lines.extend(["", "## Quick Take On Top Results", ""])

    if not top_rows:
        lines.append("- No top results available.")
        return "\n".join(lines)

    for row in top_rows[:5]:
        symbol = row.get("symbol")
        company = row.get("company_name")
        comp = fmt(row.get("composite_score"))
        fscore = fmt(row.get("fundamental_score"))
        mscore = fmt(row.get("ma_score"))
        revenue_growth = fmt(row.get("revenue_growth") * 100 if to_float(row.get("revenue_growth")) is not None else None)
        earnings_growth = fmt(row.get("earnings_growth") * 100 if to_float(row.get("earnings_growth")) is not None else None)
        forward_pe = fmt(row.get("forward_pe"))
        current_ratio = to_float(row.get("current_ratio"))
        dte = to_float(row.get("debt_to_equity"))
        cash = to_float(row.get("total_cash"))
        debt = to_float(row.get("total_debt"))
        net_cash = None if cash is None or debt is None else cash - debt

        ma_details = row.get("ma_rule_details", [])
        f_details = row.get("fundamental_rule_details", [])
        passed_ma = sum(1 for x in ma_details if x.get("status") == "passed")
        total_ma = sum(1 for x in ma_details if x.get("status") != "missing_data")
        passed_f = sum(1 for x in f_details if x.get("status") == "passed")
        total_f = sum(1 for x in f_details if x.get("status") != "missing_data")

        why_selected: list[str] = []
        close = to_float(row.get("close"))
        comp_score = to_float(row.get("composite_score"))
        if max_price is not None and close is not None and close <= max_price:
            why_selected.append(f"price {fmt(close)} <= {fmt(max_price)}")
        if min_composite_score is not None and comp_score is not None and comp_score >= min_composite_score:
            why_selected.append(f"composite {fmt(comp_score)} >= {fmt(min_composite_score)}")
        if total_f > 0:
            why_selected.append(f"fundamental rules passed {passed_f}/{total_f}")
        if total_ma > 0:
            why_selected.append(f"market-structure rules passed {passed_ma}/{total_ma}")
        if row.get("sec_recent_filings"):
            why_selected.append("recent SEC filings available")

        flags: list[str] = []
        if current_ratio is not None and current_ratio < 1.0:
            flags.append("liquidity below 1.0 current ratio")
        if dte is not None and dte > 150:
            flags.append("elevated debt-to-equity")
        if to_float(row.get("revenue_growth")) is not None and to_float(row.get("revenue_growth")) < 0:
            flags.append("negative revenue growth")
        if not row.get("sec_recent_filings"):
            flags.append("limited primary filing evidence in snapshot")

        lines.append(f"### {symbol} - {company}")
        lines.append(f"- Composite/Fundamental/MA score: {comp} / {fscore} / {mscore}")
        lines.append(f"- Revenue growth: {revenue_growth}% | Earnings growth: {earnings_growth}% | Forward PE: {forward_pe}")
        lines.append(
            f"- Cash vs Debt: {fmt(cash, 0)} vs {fmt(debt, 0)} | Net cash/debt: {fmt(net_cash, 0)}"
        )
        if why_selected:
            lines.append(f"- Why selected: {', '.join(why_selected)}")
        if flags:
            lines.append(f"- Key caution flags: {', '.join(flags)}")
        else:
            lines.append("- Key caution flags: none triggered by baseline thresholds.")
        lines.append("")

    lines.append("## Bottom Line")
    lines.append("")
    lines.append(
        "- Use this as a first-pass triage note. Full investment decisions should rely on the full brief and primary filings."
    )
    return "\n".join(lines)


def stage1_signature(
    symbols: list[str],
    lookback_days: int,
    min_history_days: int,
    max_price: float | None,
    min_composite_score: float | None,
    min_fundamental_coverage: float | None = None,
    snapshot_profile: str = "full",
    data_sources_cfg: dict[str, Any] | None = None,
    scoring_mode: str = "mixed",
    symbol_routes: dict[str, dict[str, Any]] | None = None,
) -> str:
    payload = {
        "symbols": symbols,
        "lookback_days": lookback_days,
        "min_history_days": min_history_days,
        "max_price": max_price,
        "min_composite_score": min_composite_score,
        "min_fundamental_coverage": min_fundamental_coverage,
        "snapshot_profile": snapshot_profile,
        "data_sources_cfg": data_sources_cfg or {},
        "scoring_mode": scoring_mode,
        "symbol_routes": [
            (
                symbol,
                str((symbol_routes or {}).get(symbol, {}).get("instrument_type") or ""),
                str((symbol_routes or {}).get(symbol, {}).get("research_route") or ""),
                bool((symbol_routes or {}).get(symbol, {}).get("company_fundamental_eligible", True)),
            )
            # Whether a symbol is scanned or recorded in a lane changes what a checkpoint
            # holds, so it is part of the signature (only when deferred, so plain routes
            # keep their existing signature).
            + ((str((symbol_routes or {}).get(symbol, {}).get("separate_lane")
                    or (symbol_routes or {}).get(symbol, {}).get("issuer_ticker") or ""),)
               if (symbol_routes or {}).get(symbol, {}).get("defer_market_scan") else ())
            for symbol in symbols
        ],
    }
    raw = json.dumps(payload, ensure_ascii=True, sort_keys=True)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def write_checkpoint(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def load_checkpoint(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def load_json_file(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def watchlist_index(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    indexed: dict[str, dict[str, Any]] = {}
    for row in rows:
        ticker = normalize_symbol(str(row.get("ticker") or ""))
        if not ticker:
            continue
        indexed[ticker] = row
    return indexed


def to_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def build_watchlist_delta(
    previous_rows: list[dict[str, Any]],
    current_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    prev_map = watchlist_index(previous_rows)
    curr_map = watchlist_index(current_rows)

    prev_tickers = set(prev_map.keys())
    curr_tickers = set(curr_map.keys())

    added_tickers = sorted(
        list(curr_tickers - prev_tickers),
        key=lambda ticker: to_int(curr_map[ticker].get("rank")) or 10_000,
    )
    removed_tickers = sorted(
        list(prev_tickers - curr_tickers),
        key=lambda ticker: to_int(prev_map[ticker].get("rank")) or 10_000,
    )

    added = [curr_map[ticker] for ticker in added_tickers]
    removed = [prev_map[ticker] for ticker in removed_tickers]

    moved_up: list[dict[str, Any]] = []
    moved_down: list[dict[str, Any]] = []
    unchanged_rank: list[dict[str, Any]] = []
    profile_changed: list[dict[str, Any]] = []

    common_tickers = sorted(
        list(curr_tickers & prev_tickers),
        key=lambda ticker: to_int(curr_map[ticker].get("rank")) or 10_000,
    )
    for ticker in common_tickers:
        current = curr_map[ticker]
        previous = prev_map[ticker]
        current_rank = to_int(current.get("rank"))
        previous_rank = to_int(previous.get("rank"))
        if current_rank is None or previous_rank is None:
            continue

        rank_change = previous_rank - current_rank
        change_row = {
            "ticker": ticker,
            "company": current.get("company"),
            "previous_rank": previous_rank,
            "current_rank": current_rank,
            "rank_change": rank_change,
            "current_risk_level": current.get("risk_level"),
            "current_catalyst_bucket": current.get("catalyst_bucket"),
            "current_verdict": current.get("verdict"),
        }

        if rank_change > 0:
            moved_up.append(change_row)
        elif rank_change < 0:
            moved_down.append(change_row)
        else:
            unchanged_rank.append(change_row)

        changed_fields: list[str] = []
        for field in ("risk_level", "catalyst_bucket", "main_catalyst", "verdict"):
            if str(previous.get(field) or "") != str(current.get(field) or ""):
                changed_fields.append(field)
        if changed_fields:
            profile_changed.append(
                {
                    "ticker": ticker,
                    "company": current.get("company"),
                    "changed_fields": changed_fields,
                    "previous": {
                        "risk_level": previous.get("risk_level"),
                        "catalyst_bucket": previous.get("catalyst_bucket"),
                        "main_catalyst": previous.get("main_catalyst"),
                        "verdict": previous.get("verdict"),
                    },
                    "current": {
                        "risk_level": current.get("risk_level"),
                        "catalyst_bucket": current.get("catalyst_bucket"),
                        "main_catalyst": current.get("main_catalyst"),
                        "verdict": current.get("verdict"),
                    },
                }
            )

    return {
        "summary": {
            "previous_count": len(previous_rows),
            "current_count": len(current_rows),
            "added_count": len(added),
            "removed_count": len(removed),
            "moved_up_count": len(moved_up),
            "moved_down_count": len(moved_down),
            "profile_changed_count": len(profile_changed),
        },
        "added": added,
        "removed": removed,
        "moved_up": moved_up,
        "moved_down": moved_down,
        "unchanged_rank": unchanged_rank,
        "profile_changed": profile_changed,
    }


def scan_market_core(
    symbols: list[str],
    lookback_days: int,
    min_history_days: int,
    max_price: float | None,
    min_composite_score: float | None,
    min_fundamental_coverage: float | None,
    ma_rules: list[dict[str, Any]],
    fundamental_rules: list[dict[str, Any]],
    ma_weight: float,
    fundamental_weight: float,
    data_sources_cfg: dict[str, Any],
    snapshot_profile: str = "full",
    checkpoint_path: Path | None = None,
    checkpoint_every: int = 0,
    checkpoint_signature: str | None = None,
    resume_state: dict[str, Any] | None = None,
    symbol_routes: dict[str, dict[str, Any]] | None = None,
    info_cache: Any = None,
    membership_active: set[str] | None = None,
    exit_composite_score: float | None = None,
    workers: int = 1,
    sec_ratios: Any = None,
    valuation_gap_state: Any = None,
) -> dict[str, Any]:
    symbol_routes = symbol_routes or {}
    membership_active = membership_active or set()
    market_cfg = data_sources_cfg.get("market_data", {})
    rate_limiter = (
        SharedProviderRateLimiter()
        if bool(market_cfg.get("shared_rate_limit_enabled", False))
        else None
    )
    if resume_state:
        results = list(resume_state.get("results", []))
        instrument_observations = list(resume_state.get("instrument_observations", []))
        history_exclusions = list(resume_state.get("history_exclusions", []))
        coverage_exclusions = list(resume_state.get("coverage_exclusions", []))
        errors = []
        for record in list(resume_state.get("errors", [])):
            if is_history_eligibility_record(record):
                history_exclusions.append(record)
            elif is_expected_instrument_coverage_record(record):
                coverage_exclusions.append(record)
            else:
                errors.append(record)
        snapshot_success_count = int(
            resume_state.get("snapshot_success_count", len(results) + len(instrument_observations))
        )
        excluded_counts = resume_state.get("excluded_counts", {})
        excluded_price_count = int(excluded_counts.get("price_above_max", 0))
        excluded_score_count = int(excluded_counts.get("below_min_composite", 0))
        excluded_missing_price_count = int(excluded_counts.get("missing_price_for_filter", 0))
        excluded_fundamental_coverage_count = int(
            excluded_counts.get("below_min_fundamental_coverage", 0)
        )
        start_index = int(resume_state.get("processed_count", 0))
        if start_index < 0:
            start_index = 0
        if start_index > len(symbols):
            start_index = len(symbols)
        excluded_reasons = dict(resume_state.get("excluded_tickers") or {})
        macro_context = resume_state.get("macro_context")
        if not isinstance(macro_context, dict):
            macro_context = fetch_fred_macro_context(data_sources_cfg)
    else:
        results = []
        instrument_observations = []
        errors = []
        history_exclusions = []
        coverage_exclusions = []
        snapshot_success_count = 0
        excluded_price_count = 0
        excluded_score_count = 0
        excluded_missing_price_count = 0
        excluded_fundamental_coverage_count = 0
        start_index = 0
        excluded_reasons = {}
        macro_context = fetch_fred_macro_context(data_sources_cfg)

    checkpoint_every = max(0, int(checkpoint_every))
    processed_count = start_index
    total_symbols = len(symbols)
    runtime_summary = empty_runtime_summary(snapshot_profile, total_symbols)
    previous_elapsed_seconds = 0.0
    if resume_state:
        existing_runtime = resume_state.get("runtime_summary", {})
        if isinstance(existing_runtime, dict):
            runtime_summary.update(existing_runtime)
            runtime_summary["profile"] = snapshot_profile
            runtime_summary["symbols_total"] = total_symbols
            runtime_summary["processed_symbols"] = start_index
            previous_elapsed_seconds = max(0.0, to_float(existing_runtime.get("elapsed_seconds")) or 0.0)
    loop_started_at = time.perf_counter()

    def context_for(symbol: str) -> dict[str, Any]:
        return symbol_routes.get(symbol, {
            "ticker": symbol,
            "research_route": "company_fundamental_research",
            "company_fundamental_eligible": True,
        })

    def snapshot_call(symbol: str, listing_context: dict[str, Any]) -> dict[str, Any]:
        return fetch_snapshot(
            symbol,
            lookback_days,
            min_history_days,
            data_sources_cfg=data_sources_cfg,
            snapshot_profile=snapshot_profile,
            listing_context=listing_context,
            rate_limiter=rate_limiter,
            **({"info_cache": info_cache} if info_cache is not None else {}),
        )

    # Parallel fetch: snapshots are requested ahead by a small pool, but every result is consumed here in symbol
    # order, so rules, filters, checkpoints and the output are exactly those of the sequential loop. An exception
    # raised by a worker is re-raised at the symbol's own turn and handled by the same except block.
    workers = max(1, int(workers))
    executor = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="stage1") if workers > 1 else None
    prefetched: dict[int, Future] = {}
    global SEC_MIN_INTERVAL_SECONDS
    previous_sec_interval = SEC_MIN_INTERVAL_SECONDS
    if executor is not None:
        SEC_MIN_INTERVAL_SECONDS = 0.15

    def prefetch(from_index: int) -> None:
        if executor is None:
            return
        for ahead in range(from_index, min(total_symbols, from_index + workers * 2)):
            context = context_for(symbols[ahead])
            if ahead not in prefetched and not context.get("defer_market_scan"):
                prefetched[ahead] = executor.submit(snapshot_call, symbols[ahead], context)

    try:
        for idx in range(start_index, total_symbols):
            symbol = symbols[idx]
            listing_context = context_for(symbol)
            prefetch(idx)
            processed_count = idx + 1
            if listing_context.get("defer_market_scan"):
                instrument_observations.append(deferred_instrument_observation(symbol, listing_context))
                excluded_reasons[symbol] = "deferred_route"
                runtime_summary["processed_symbols"] = processed_count
                where = (f"linked to issuer {listing_context['issuer_ticker']}" if listing_context.get("issuer_ticker")
                         else f"separate lane {listing_context.get('separate_lane')}")
                print(f"[DEFER] {symbol} | {where} | not scanned in the central run")
            else:
                try:
                    pending_snapshot = prefetched.pop(idx, None)
                    snapshot = (pending_snapshot.result() if pending_snapshot is not None
                                else snapshot_call(symbol, listing_context))
                    snapshot_success_count += 1
                    row = build_scan_row(snapshot)
                    timing = (snapshot.get("source_base", {}) or {}).get("timing_seconds", {})
                    runtime_summary["processed_symbols"] = processed_count
                    runtime_summary["market_data_seconds"] += to_float(timing.get("market_data")) or 0.0
                    runtime_summary["fundamentals_and_news_seconds"] += to_float(timing.get("fundamentals_and_news")) or 0.0
                    runtime_summary["sec_seconds"] += to_float(timing.get("sec")) or 0.0
                    runtime_summary["openbb_research_seconds"] += to_float(timing.get("openbb_research")) or 0.0
                    runtime_summary["elapsed_seconds"] = previous_elapsed_seconds + time.perf_counter() - loop_started_at

                    ma_score, ma_coverage, ma_details = evaluate_rules(row, ma_rules)
                    company_eligible = bool(row.get("company_fundamental_eligible", True))
                    if company_eligible:
                        fundamental_score, fundamental_coverage, fundamental_details = evaluate_rules(
                            row, fundamental_rules
                        )
                        row["fundamental_score_basis"] = "yfinance"
                        sec_values = sec_ratios.values(symbol) if sec_ratios is not None else None
                        if sec_values:
                            # SEC first (owner's decision 2026-10-12): the same rules with the SEC value where there is
                            # one, yfinance only as a labelled fallback (forward P/E has no SEC equivalent).
                            fundamental_details, fundamental_score, value_sources = sec_fundamental_ratios.sec_first_rules(
                                fundamental_details, sec_values)
                            total = sum(max(to_float(d.get("weight")) or 1.0, 0.0) for d in fundamental_details)
                            known = sum(max(to_float(d.get("weight")) or 1.0, 0.0) for d in fundamental_details
                                        if d.get("status") != "missing_data")
                            fundamental_coverage = round(known / total * 100.0, 2) if total > 0 else 0.0
                            row["fundamental_score_basis"] = "sec_first"
                            row["fundamental_value_sources"] = value_sources
                        composite_score = weighted_score(
                            ma_score, fundamental_score, ma_weight, fundamental_weight
                        )
                    else:
                        fundamental_score, fundamental_coverage, fundamental_details = 0.0, 0.0, []
                        composite_score = ma_score

                    row["ma_score"] = ma_score
                    row["ma_coverage"] = ma_coverage
                    row["fundamental_score"] = fundamental_score
                    row["fundamental_coverage"] = fundamental_coverage
                    row["composite_score"] = composite_score
                    row["ma_rule_details"] = ma_details
                    row["fundamental_rule_details"] = fundamental_details
                    if valuation_gap_state is not None:
                        valuation_gap_state.observe(symbol, fundamental_details)
                    row["news"] = snapshot.get("news", [])
                    row["sec_recent_filings"] = snapshot.get("sec_recent_filings", [])
                    row["source_base"] = snapshot.get("source_base", {})

                    if not company_eligible:
                        excluded_reasons[symbol] = "not_company_route"
                        instrument_observations.append({
                            "ticker": symbol,
                            "name": row.get("company_name"),
                            "venue_code": row.get("venue_code"),
                            "venue": row.get("venue"),
                            "instrument_type": row.get("instrument_type"),
                            "research_route": row.get("research_route"),
                            "workflow_status": "market_observation_complete",
                            "market_data_provider": row.get("market_data_provider"),
                            "close": row.get("close"),
                            "ma_score": row.get("ma_score"),
                            "ma_coverage": row.get("ma_coverage"),
                            "return_20d_pct": row.get("return_20d_pct"),
                            "volatility_20d_pct": row.get("volatility_20d_pct"),
                            "volume_ratio_20": row.get("volume_ratio_20"),
                        })
                        print(
                            f"[OBSERVE] {symbol} | Route {row.get('research_route')} | "
                            f"MA {ma_score:.2f} | Close {row.get('close') if row.get('close') is not None else 'N/A'}"
                        )
                    else:
                        close = to_float(row.get("close"))
                        skip_row = False
                        if max_price is not None:
                            if close is None:
                                excluded_missing_price_count += 1
                                excluded_reasons[symbol] = "missing_price"
                                print(f"[SKIP] {symbol} | Missing close price for max_price filter.")
                                skip_row = True
                            elif close > max_price:
                                excluded_price_count += 1
                                excluded_reasons[symbol] = "price_above_max"
                                print(f"[SKIP] {symbol} | Close {close:.2f} > max_price {max_price:.2f}")
                                skip_row = True

                        required_score = shortlist_membership.threshold_for(
                            symbol in membership_active, min_composite_score, exit_composite_score
                        )
                        if not skip_row and required_score is not None and composite_score < required_score:
                            excluded_score_count += 1
                            excluded_reasons[symbol] = (
                                "below_exit_composite" if symbol in membership_active else "below_min_composite"
                            )
                            print(
                                f"[SKIP] {symbol} | Composite {composite_score:.2f} < required score {required_score:.2f}"
                                f"{' (member: exit threshold)' if symbol in membership_active else ''}"
                            )
                            skip_row = True

                        if (
                            not skip_row
                            and min_fundamental_coverage is not None
                            and fundamental_coverage < min_fundamental_coverage
                        ):
                            excluded_fundamental_coverage_count += 1
                            excluded_reasons[symbol] = "below_min_fundamental_coverage"
                            print(
                                f"[SKIP] {symbol} | Fundamental coverage {fundamental_coverage:.2f}% "
                                f"< minimum {min_fundamental_coverage:.2f}%"
                            )
                            skip_row = True

                        if not skip_row:
                            results.append(row)
                            print(f"[OK] {symbol} | Composite {composite_score:.2f} | Close {close if close is not None else 'N/A'}")
                except Exception as exc:
                    record = data_error_record(symbol, exc)
                    record.update({
                        "instrument_type": listing_context.get("instrument_type"),
                        "research_route": listing_context.get("research_route"),
                        "company_fundamental_eligible": bool(
                            listing_context.get("company_fundamental_eligible", True)
                        ),
                    })
                    if is_history_eligibility_record(record):
                        history_exclusions.append(record)
                        excluded_reasons[symbol] = "insufficient_history"
                        print(f"[SKIP] {symbol} | Insufficient trading history for the active eligibility gate.")
                    elif is_expected_instrument_coverage_record(record):
                        coverage_exclusions.append(record)
                        excluded_reasons[symbol] = "no_provider_coverage"
                        print(f"[COVERAGE] {symbol} | No provider coverage for this instrument route.")
                    else:
                        errors.append(record)
                        print(f"[ERROR] {symbol} | {exc}")

            if checkpoint_path is not None and checkpoint_every > 0 and processed_count % checkpoint_every == 0:
                checkpoint_payload = {
                    "status": "running",
                    "signature": checkpoint_signature,
                    "run_timestamp": now_athens().isoformat(),
                    "processed_count": processed_count,
                    "symbols_total": total_symbols,
                    "filters_applied": {
                        "max_price": max_price,
                        "min_composite_score": min_composite_score,
                        "min_fundamental_coverage": min_fundamental_coverage,
                    },
                    "excluded_counts": {
                        "price_above_max": excluded_price_count,
                        "below_min_composite": excluded_score_count,
                        "missing_price_for_filter": excluded_missing_price_count,
                        "below_min_fundamental_coverage": excluded_fundamental_coverage_count,
                        "insufficient_history": len(history_exclusions),
                    },
                    "success_count": len(results),
                    "snapshot_success_count": snapshot_success_count,
                    "data_coverage_pct": round((snapshot_success_count / processed_count) * 100.0, 2)
                    if processed_count
                    else 0.0,
                    "error_count": len(errors),
                    "history_exclusion_count": len(history_exclusions),
                    "coverage_exclusion_count": len(coverage_exclusions),
                    "macro_context": macro_context,
                    "runtime_summary": {
                        **runtime_summary,
                        "elapsed_seconds": round(runtime_summary.get("elapsed_seconds", 0.0), 2),
                        "avg_snapshot_seconds": round(
                            (runtime_summary.get("elapsed_seconds", 0.0) / processed_count) if processed_count else 0.0,
                            4,
                        ),
                        "market_data_seconds": round(runtime_summary.get("market_data_seconds", 0.0), 2),
                        "fundamentals_and_news_seconds": round(
                            runtime_summary.get("fundamentals_and_news_seconds", 0.0), 2
                        ),
                        "sec_seconds": round(runtime_summary.get("sec_seconds", 0.0), 2),
                        "openbb_research_seconds": round(runtime_summary.get("openbb_research_seconds", 0.0), 2),
                    },
                    "results": results,
                    "instrument_observations": instrument_observations,
                    "history_exclusions": history_exclusions,
                    "coverage_exclusions": coverage_exclusions,
                    "excluded_tickers": excluded_reasons,
                    "errors": errors,
                }
                write_checkpoint(checkpoint_path, checkpoint_payload)

    finally:
        SEC_MIN_INTERVAL_SECONDS = previous_sec_interval
        if executor is not None:
            executor.shutdown(wait=False, cancel_futures=True)

    runtime_summary["processed_symbols"] = processed_count
    runtime_summary["elapsed_seconds"] = previous_elapsed_seconds + time.perf_counter() - loop_started_at
    runtime_summary["avg_snapshot_seconds"] = (
        runtime_summary["elapsed_seconds"] / processed_count if processed_count else 0.0
    )
    sorted_rows = sorted(results, key=lambda x: x.get("composite_score", 0.0), reverse=True)
    final_payload = {
        "run_timestamp": now_athens().isoformat(),
        "processed_count": processed_count,
        "symbols_total": total_symbols,
        "filters_applied": {
            "max_price": max_price,
            "min_composite_score": min_composite_score,
            "min_fundamental_coverage": min_fundamental_coverage,
        },
        "excluded_counts": {
            "price_above_max": excluded_price_count,
            "below_min_composite": excluded_score_count,
            "missing_price_for_filter": excluded_missing_price_count,
            "below_min_fundamental_coverage": excluded_fundamental_coverage_count,
            "insufficient_history": len(history_exclusions),
        },
        "success_count": len(sorted_rows),
        "snapshot_success_count": snapshot_success_count,
        "data_coverage_pct": round((snapshot_success_count / processed_count) * 100.0, 2)
        if processed_count
        else 0.0,
        "error_count": len(errors),
        "history_exclusion_count": len(history_exclusions),
        "coverage_exclusion_count": len(coverage_exclusions),
        "provider_retry_backlog": [
            {"symbol": item.get("symbol"), "research_route": item.get("research_route"),
             "instrument_type": item.get("instrument_type"), "retryable": item.get("retryable"),
             "provider_attempts": item.get("provider_attempts", [])}
            for item in coverage_exclusions if item.get("retryable")
        ],
        "macro_context": macro_context,
        "runtime_summary": {
            "profile": runtime_summary.get("profile"),
            "symbols_total": total_symbols,
            "processed_symbols": processed_count,
            "elapsed_seconds": round(runtime_summary.get("elapsed_seconds", 0.0), 2),
            "avg_snapshot_seconds": round(runtime_summary.get("avg_snapshot_seconds", 0.0), 4),
            "market_data_seconds": round(runtime_summary.get("market_data_seconds", 0.0), 2),
            "fundamentals_and_news_seconds": round(runtime_summary.get("fundamentals_and_news_seconds", 0.0), 2),
            "sec_seconds": round(runtime_summary.get("sec_seconds", 0.0), 2),
            "openbb_research_seconds": round(runtime_summary.get("openbb_research_seconds", 0.0), 2),
        },
        "results": sorted_rows,
        "instrument_observations": instrument_observations,
        "universe_routing": {
            "assigned_symbols": len(symbols),
            "company_fundamental_symbols": sum(
                bool(symbol_routes.get(symbol, {}).get("company_fundamental_eligible", True))
                for symbol in symbols
            ),
            "instrument_observation_symbols": sum(
                not bool(symbol_routes.get(symbol, {}).get("company_fundamental_eligible", True))
                for symbol in symbols
            ),
            "instrument_observations_completed": len(instrument_observations),
            "instrument_observations_deferred_to_lane": dict(sorted(Counter(
                str(item["separate_lane"]) for item in instrument_observations if item.get("separate_lane")
            ).items())),
            "instrument_observations_deferred_to_issuer": sum(
                item.get("workflow_status") == "deferred_to_issuer_card" for item in instrument_observations
            ),
            "route_counts": dict(sorted(Counter(
                str(symbol_routes.get(symbol, {}).get("research_route", "company_fundamental_research"))
                for symbol in symbols
            ).items())),
        },
        "history_exclusions": history_exclusions,
        "coverage_exclusions": coverage_exclusions,
        "excluded_tickers": excluded_reasons,
        "errors": errors,
    }
    if checkpoint_path is not None:
        completed_checkpoint = dict(final_payload)
        completed_checkpoint["status"] = "complete"
        completed_checkpoint["signature"] = checkpoint_signature
        write_checkpoint(checkpoint_path, completed_checkpoint)
    return final_payload


def enrich_stage1_rows_for_shortlist(
    rows: list[dict[str, Any]],
    lookback_days: int,
    min_history_days: int,
    data_sources_cfg: dict[str, Any],
    limit: int,
) -> dict[str, Any]:
    target_count = max(0, min(int(limit), len(rows)))
    started_at = time.perf_counter()
    enriched = 0
    errors: list[dict[str, str]] = []
    timing_totals = {
        "market_data_seconds": 0.0,
        "fundamentals_and_news_seconds": 0.0,
        "sec_seconds": 0.0,
        "openbb_research_seconds": 0.0,
    }

    for row in rows[:target_count]:
        symbol = str(row.get("symbol") or "")
        if not symbol:
            continue
        try:
            snapshot = fetch_snapshot(
                symbol,
                lookback_days,
                min_history_days,
                data_sources_cfg=data_sources_cfg,
                snapshot_profile="deep_shortlist",
            )
            merge_context_into_scan_row(row, snapshot)
            timing = (snapshot.get("source_base", {}) or {}).get("timing_seconds", {})
            timing_totals["market_data_seconds"] += to_float(timing.get("market_data")) or 0.0
            timing_totals["fundamentals_and_news_seconds"] += to_float(timing.get("fundamentals_and_news")) or 0.0
            timing_totals["sec_seconds"] += to_float(timing.get("sec")) or 0.0
            timing_totals["openbb_research_seconds"] += to_float(timing.get("openbb_research")) or 0.0
            enriched += 1
        except Exception as exc:
            errors.append(data_error_record(symbol, exc))

    elapsed_seconds = time.perf_counter() - started_at
    return {
        "enabled": target_count > 0,
        "target_count": target_count,
        "enriched_count": enriched,
        "error_count": len(errors),
        "elapsed_seconds": round(elapsed_seconds, 2),
        "avg_symbol_seconds": round((elapsed_seconds / target_count) if target_count else 0.0, 4),
        "market_data_seconds": round(timing_totals["market_data_seconds"], 2),
        "fundamentals_and_news_seconds": round(timing_totals["fundamentals_and_news_seconds"], 2),
        "sec_seconds": round(timing_totals["sec_seconds"], 2),
        "openbb_research_seconds": round(timing_totals["openbb_research_seconds"], 2),
        "errors": errors,
    }


def write_pipeline_runtime_report(
    output_dir: Path,
    timestamp: str,
    stage1_runtime: dict[str, Any],
    shortlist_enrichment: dict[str, Any],
) -> tuple[Path, Path]:
    runtime_payload = {
        "run_timestamp": now_athens().isoformat(),
        "stage1_runtime": stage1_runtime,
        "shortlist_deep_enrichment": shortlist_enrichment,
    }
    json_path = output_dir / f"pipeline_runtime_{timestamp}.json"
    latest_json_path = output_dir / "pipeline_runtime_latest.json"
    md_path = output_dir / f"pipeline_runtime_{timestamp}.md"
    latest_md_path = output_dir / "pipeline_runtime_latest.md"
    json_text = json.dumps(runtime_payload, ensure_ascii=False, indent=2)
    json_path.write_text(json_text, encoding="utf-8")
    latest_json_path.write_text(json_text, encoding="utf-8")

    stage1_profile = stage1_runtime.get("profile", "unknown")
    md_lines = [
        "# Pipeline Runtime Report",
        "",
        f"- Run timestamp: {runtime_payload['run_timestamp']}",
        f"- Stage-1 profile: {stage1_profile}",
        f"- Stage-1 processed symbols: {stage1_runtime.get('processed_symbols', 0)} / {stage1_runtime.get('symbols_total', 0)}",
        f"- Stage-1 elapsed: {stage1_runtime.get('elapsed_seconds', 0)}s",
        f"- Stage-1 avg per symbol: {stage1_runtime.get('avg_snapshot_seconds', 0)}s",
        f"- Stage-1 market data: {stage1_runtime.get('market_data_seconds', 0)}s",
        f"- Stage-1 fundamentals/news: {stage1_runtime.get('fundamentals_and_news_seconds', 0)}s",
        f"- Stage-1 SEC: {stage1_runtime.get('sec_seconds', 0)}s",
        f"- Stage-1 OpenBB research: {stage1_runtime.get('openbb_research_seconds', 0)}s",
        "",
        "## Shortlist Deep Enrichment",
        "",
        f"- Enabled: {shortlist_enrichment.get('enabled', False)}",
        f"- Target count: {shortlist_enrichment.get('target_count', 0)}",
        f"- Enriched count: {shortlist_enrichment.get('enriched_count', 0)}",
        f"- Errors: {shortlist_enrichment.get('error_count', 0)}",
        f"- Elapsed: {shortlist_enrichment.get('elapsed_seconds', 0)}s",
        f"- Avg per symbol: {shortlist_enrichment.get('avg_symbol_seconds', 0)}s",
        f"- Market data: {shortlist_enrichment.get('market_data_seconds', 0)}s",
        f"- Fundamentals/news: {shortlist_enrichment.get('fundamentals_and_news_seconds', 0)}s",
        f"- SEC: {shortlist_enrichment.get('sec_seconds', 0)}s",
        f"- OpenBB research: {shortlist_enrichment.get('openbb_research_seconds', 0)}s",
        "",
    ]
    if shortlist_enrichment.get("errors"):
        md_lines.extend(["## Enrichment Errors", ""])
        for item in shortlist_enrichment["errors"]:
            md_lines.append(f"- {item.get('symbol')}: {item.get('error')}")
        md_lines.append("")
    md_text = "\n".join(md_lines)
    md_path.write_text(md_text, encoding="utf-8")
    latest_md_path.write_text(md_text, encoding="utf-8")
    return md_path, json_path


def write_scan_outputs(
    payload: dict[str, Any],
    output_dir: Path,
    top_n: int,
    filename_prefix: str = "scan",
    write_mini: bool = True,
) -> dict[str, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = now_athens().strftime("%Y%m%d_%H%M%S")
    csv_path = output_dir / f"{filename_prefix}_{timestamp}.csv"
    json_path = output_dir / f"{filename_prefix}_{timestamp}.json"
    md_path = output_dir / f"{filename_prefix}_{timestamp}.md"

    sorted_rows = payload.get("results", [])
    top_rows = sorted_rows[:top_n]
    payload["top_symbols"] = [row.get("symbol") for row in top_rows]

    export_rows: list[dict[str, Any]] = []
    for row in sorted_rows:
        clean = {
            k: v
            for k, v in row.items()
            if k not in {"ma_rule_details", "fundamental_rule_details", "news", "sec_recent_filings", "source_base"}
        }
        export_rows.append(clean)
    pd.DataFrame(export_rows).to_csv(csv_path, index=False)

    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    markdown_lines = [
        "# US Equity Daily Monitor",
        "",
        f"- Run timestamp: {payload.get('run_timestamp')}",
        f"- Universe size: {payload.get('symbols_total')}",
        f"- Successful snapshots: {payload.get('success_count')}",
        f"- Technical/provider errors: {payload.get('error_count')}",
        f"- Insufficient-history eligibility exclusions: {payload.get('history_exclusion_count', 0)}",
        f"- Expected instrument coverage exclusions: {payload.get('coverage_exclusion_count', 0)}",
        f"- Excluded by price filter: {payload.get('excluded_counts', {}).get('price_above_max', 0)}",
        f"- Excluded by min composite: {payload.get('excluded_counts', {}).get('below_min_composite', 0)}",
        f"- Excluded due missing price for filter: {payload.get('excluded_counts', {}).get('missing_price_for_filter', 0)}",
        f"- Max price filter: {payload.get('filters_applied', {}).get('max_price', 'disabled')}",
        f"- Min composite filter: {payload.get('filters_applied', {}).get('min_composite_score', 'disabled')}",
        "",
        "## Macro Context",
        "",
        f"- {json.dumps(payload.get('macro_context', {}), ensure_ascii=False)}",
        "",
        "## Top Candidates",
        "",
    ]
    if top_rows:
        markdown_lines.extend([to_markdown_table(top_rows), ""])
    else:
        markdown_lines.extend(["- No companies matched active filters in this run.", ""])

    if payload.get("errors"):
        markdown_lines.extend(["## Errors", ""])
        for item in payload["errors"]:
            markdown_lines.append(f"- {item['symbol']}: {item['error']}")
        markdown_lines.append("")
    if payload.get("history_exclusions"):
        markdown_lines.extend(["## Insufficient-History Eligibility Exclusions", ""])
        for item in payload["history_exclusions"]:
            details = item.get("details") or {}
            markdown_lines.append(
                f"- {item['symbol']}: {details.get('best_row_count', 0)}/"
                f"{details.get('required_rows', 0)} rows; retry after approximately "
                f"{details.get('retry_after_trading_sessions', 'unknown')} trading sessions."
            )
        markdown_lines.append("")
    if payload.get("coverage_exclusions"):
        markdown_lines.extend(["## Expected Instrument Coverage Exclusions", ""])
        for item in payload["coverage_exclusions"]:
            markdown_lines.append(
                f"- {item['symbol']}: {item.get('instrument_type') or 'unknown'} / "
                f"{item.get('research_route') or 'unassigned'}"
            )
        markdown_lines.append("")
    md_path.write_text("\n".join(markdown_lines), encoding="utf-8")

    out: dict[str, Path] = {"csv": csv_path, "json": json_path, "md": md_path}
    if write_mini:
        mini_path = output_dir / f"mini_report_{timestamp}.md"
        mini_path.write_text(
            mini_report_text(
                run_timestamp=str(payload.get("run_timestamp")),
                top_rows=top_rows,
                macro_context=payload.get("macro_context", {}),
                max_price=payload.get("filters_applied", {}).get("max_price"),
                min_composite_score=payload.get("filters_applied", {}).get("min_composite_score"),
            ),
            encoding="utf-8",
        )
        out["mini"] = mini_path
    return out


def detect_main_catalyst_details(row: dict[str, Any]) -> dict[str, Any]:
    """Return an evidence-aware catalyst classification and attention score.

    Materiality is intentionally separate from direction: a delisting or dilution
    event can deserve urgent attention without being a positive opportunity.
    """

    return catalyst_taxonomy.classify_catalyst(row)


def detect_main_catalyst(row: dict[str, Any]) -> tuple[str, str, str | None]:
    """Compatibility wrapper for older callers expecting a three-value tuple."""

    details = detect_main_catalyst_details(row)
    return (
        str(details.get("category_label") or "Unclassified / Insufficient Evidence"),
        str(details.get("event_name") or "No source-backed catalyst classified"),
        str(details.get("event_date") or "") or None,
    )


def build_babis_handoff_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    handoff: list[dict[str, Any]] = []
    for rank, row in enumerate(rows, start=1):
        risk_level, dilution_flags = evaluate_risk_level(row)
        instrument = classify_instrument(row)
        for flag in instrument["instrument_flags"]:
            if flag not in dilution_flags:
                dilution_flags.append(flag)
        catalyst_details = detect_main_catalyst_details(row)
        classification = classify_symbol(row)
        bucket = str(catalyst_details.get("category_label") or "Unclassified / Insufficient Evidence")
        catalyst = str(catalyst_details.get("event_name") or "No source-backed catalyst classified")
        catalyst_date = str(catalyst_details.get("event_date") or "") or None
        raw_sec_filings = row.get("sec_recent_filings") or []
        sec_filings = []
        for filing in raw_sec_filings:
            filing_age = filing.get("filing_age_days")
            if filing_age is None:
                filing_age = age_days(filing.get("filing_date"))
            if filing_age is not None and filing_age <= 30:
                copied = dict(filing)
                copied["filing_age_days"] = filing_age
                copied["fresh"] = True
                sec_filings.append(copied)
        latest_sec_age = to_float(sec_filings[0].get("filing_age_days")) if sec_filings else None
        data_age_flags: list[str] = []
        if not sec_filings:
            data_age_flags.append("no_fresh_sec_filing_30d")
        cash = to_float(row.get("total_cash"))
        debt = to_float(row.get("total_debt"))
        unknown_fields: list[str] = []
        for key, val in {
            "share_price": row.get("close"),
            "market_cap": row.get("market_cap"),
            "cash": cash,
            "debt": debt,
        }.items():
            if to_float(val) is None:
                unknown_fields.append(key)

        handoff.append(
            {
                "rank": rank,
                "ticker": row.get("symbol"),
                "company": row.get("company_name"),
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
                "share_price": to_float(row.get("close")),
                "market_cap": to_float(row.get("market_cap")),
                "catalyst_bucket": bucket,
                "catalyst_category_id": catalyst_details.get("category_id"),
                "catalyst_legacy_bucket": catalyst_details.get("legacy_bucket"),
                "main_catalyst": catalyst,
                "catalyst_date": catalyst_date,
                "catalyst_direction": catalyst_details.get("direction"),
                "catalyst_category_weight": catalyst_details.get("category_weight"),
                "catalyst_attention_score": catalyst_details.get("attention_score"),
                "catalyst_priority": catalyst_details.get("priority"),
                "catalyst_classification_confidence": catalyst_details.get("classification_confidence"),
                "catalyst_evidence_confidence_score": catalyst_details.get("evidence_confidence_score"),
                "catalyst_recency_score": catalyst_details.get("recency_score"),
                "catalyst_specificity_score": catalyst_details.get("specificity_score"),
                "catalyst_reader_question": catalyst_details.get("reader_question"),
                "catalyst_classification_reason": catalyst_details.get("classification_reason"),
                "catalyst_evidence": catalyst_details.get("evidence", []),
                "long_catalyst_score": catalyst_details.get("long_catalyst_score"),
                "short_catalyst_score": catalyst_details.get("short_catalyst_score"),
                "catalyst_trade_bias": catalyst_details.get("catalyst_trade_bias"),
                "catalyst_trade_lenses": catalyst_details.get("trade_lenses", {}),
                "catalyst_taxonomy_version": catalyst_details.get("taxonomy_version"),
                "catalyst_scoring_status": catalyst_details.get("scoring_status"),
                "fresh_primary_data": bool(sec_filings),
                "sec_filing_age_days": latest_sec_age,
                "data_age_flags": data_age_flags,
                "time_horizon": "Immediate / 1-4 τρίμηνα",
                "risk_level": risk_level,
                "verdict": "Watchlist candidate" if risk_level != "High" else "Fragile setup",
                "instrument_type": instrument["instrument_type"],
                "complex_security": instrument["complex_security"],
                "instrument_flags": instrument["instrument_flags"],
                "composite_score": to_float(row.get("composite_score")),
                "rsi_14": to_float(row.get("rsi_14")),
                "macd_histogram": to_float(row.get("macd_histogram")),
                "atr_14_pct": to_float(row.get("atr_14_pct")),
                "volume_ratio_20": to_float(row.get("volume_ratio_20")),
                "return_20d_pct": to_float(row.get("return_20d_pct")),
                "return_60d_pct": to_float(row.get("return_60d_pct")),
                "price_position_20d_pct": to_float(row.get("price_position_20d_pct")),
                "price_position_60d_pct": to_float(row.get("price_position_60d_pct")),
                "distance_from_20d_low_pct": to_float(row.get("distance_from_20d_low_pct")),
                "distance_from_60d_low_pct": to_float(row.get("distance_from_60d_low_pct")),
                "distance_from_20d_high_pct": to_float(row.get("distance_from_20d_high_pct")),
                "distance_from_60d_high_pct": to_float(row.get("distance_from_60d_high_pct")),
                "volatility_20d_pct": to_float(row.get("volatility_20d_pct")),
                "candlestick_patterns": row.get("candlestick_patterns", []),
                "candlestick_bias": row.get("candlestick_bias"),
                "candlestick_score": to_float(row.get("candlestick_score")),
                "candlestick_last_signal": row.get("candlestick_last_signal"),
                "candlestick_bullish_count": int(to_float(row.get("candlestick_bullish_count")) or 0),
                "candlestick_bearish_count": int(to_float(row.get("candlestick_bearish_count")) or 0),
                "smart_money_signal": row.get("smart_money_signal"),
                "smart_money_score": to_float(row.get("smart_money_score")),
                "smart_money_notes": row.get("smart_money_notes", []),
                "institution_ownership": to_float(row.get("institution_ownership")),
                "insider_ownership": to_float(row.get("insider_ownership")),
                "institution_float_ownership": to_float(row.get("institution_float_ownership")),
                "institutions_count": int(to_float(row.get("institutions_count")) or 0),
                "short_percent_of_float": to_float(row.get("short_percent_of_float")),
                "days_to_cover": to_float(row.get("days_to_cover")),
                "open_market_buy_count": int(to_float(row.get("open_market_buy_count")) or 0),
                "open_market_sell_count": int(to_float(row.get("open_market_sell_count")) or 0),
                "open_market_buy_value": to_float(row.get("open_market_buy_value")),
                "open_market_sell_value": to_float(row.get("open_market_sell_value")),
                "net_open_market_value": to_float(row.get("net_open_market_value")),
                "darkpool_activity_ratio": to_float(row.get("darkpool_activity_ratio")),
                "darkpool_last_week_shares": to_float(row.get("darkpool_last_week_shares")),
                "darkpool_last_week_date": row.get("darkpool_last_week_date"),
                "latest_insider_filing_date": row.get("latest_insider_filing_date"),
                "latest_insider_transaction_date": row.get("latest_insider_transaction_date"),
                "recent_insider_names": row.get("recent_insider_names", []),
                "cash": cash,
                "debt": debt,
                "cash_burn_proxy_fcf": to_float(row.get("free_cashflow")),
                "cash_burn_proxy_ocf": to_float(row.get("operating_cashflow")),
                "dilution_flags": dilution_flags,
                "atm_shelf_warrants_convertibles": "UNKNOWN",
                "reverse_split_compliance_risk": "Potential" if to_float(row.get("close")) and to_float(row.get("close")) < 1 else "Not flagged",
                "concentration_risk": "UNKNOWN",
                "sec_links": [item.get("url") for item in sec_filings[:3] if item.get("url")],
                "confidence": "Low" if len(unknown_fields) >= 2 else "Medium",
                "unknown_fields": unknown_fields,
                "analysis_lane": "fundamental_discovery",
                "technical_fields_advisory_only": True,
                "fundamental_attention_score": to_float(row.get("fundamental_score")),
                "fundamental_coverage": to_float(row.get("fundamental_coverage")),
                "fundamental_rule_details": row.get("fundamental_rule_details", []),
                "technical_attention_score": to_float(row.get("ma_score")),
                "technical_coverage": to_float(row.get("ma_coverage")),
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
                "source_base": row.get("source_base", {}),
            }
        )
    return handoff


def select_traps(handoff_rows: list[dict[str, Any]], limit: int = 5) -> list[dict[str, Any]]:
    traps = [x for x in handoff_rows if x.get("risk_level") == "High" or len(x.get("dilution_flags", [])) >= 2]
    traps_sorted = sorted(
        traps,
        key=lambda x: (len(x.get("dilution_flags", [])), -(to_float(x.get("share_price")) or 0.0)),
        reverse=True,
    )
    return traps_sorted[:limit]


def resolve_agent_prompt_path(config: dict[str, Any], agent_key: str, fallback_prompt: str) -> Path:
    agents_cfg = config.get("agents", {})
    agent_cfg = agents_cfg.get(agent_key, {}) if isinstance(agents_cfg, dict) else {}
    candidate = str(agent_cfg.get("prompt_file", "")).strip()
    if candidate:
        return Path(candidate)
    return Path(fallback_prompt)


def extract_first_json_object(text: str) -> dict[str, Any] | None:
    raw = (text or "").strip()
    if not raw:
        return None
    candidates: list[str] = [raw]
    fenced = re.findall(r"```(?:json)?\s*(\{.*?\})\s*```", raw, flags=re.DOTALL | re.IGNORECASE)
    candidates.extend(fenced)
    loose = re.findall(r"(\{[\s\S]*\})", raw)
    candidates.extend(loose[:1])

    for candidate in candidates:
        candidate = candidate.strip()
        if not candidate.startswith("{") or not candidate.endswith("}"):
            continue
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed
    return None


def normalize_level(text: Any, allowed: set[str], fallback: str) -> str:
    candidate = str(text or "").strip().lower()
    for item in allowed:
        if candidate == item.lower():
            return item
    return fallback


def refine_babis_handoff_with_llm(
    handoff_rows: list[dict[str, Any]],
    stage1_rows: list[dict[str, Any]],
    config: dict[str, Any],
    limit: int,
    disabled: bool,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    rows = [dict(x) for x in handoff_rows]
    row_map = {str(r.get("symbol")): r for r in stage1_rows}

    for row in rows:
        base = (to_float(row.get("composite_score")) or 0.0) / 10.0
        row["pipeline_rank_score"] = round(base, 2)
        row["llm_refined"] = False

    if limit <= 0:
        ranked = sorted(rows, key=lambda x: (to_float(x.get("pipeline_rank_score")) or 0.0), reverse=True)
        for idx, row in enumerate(ranked, start=1):
            row["rank"] = idx
        return ranked, {"enabled": False, "reason": "disabled_by_limit", "requested_limit": limit, "refined_count": 0}

    if disabled:
        ranked = sorted(rows, key=lambda x: (to_float(x.get("pipeline_rank_score")) or 0.0), reverse=True)
        for idx, row in enumerate(ranked, start=1):
            row["rank"] = idx
        return ranked, {"enabled": False, "reason": "disabled_by_flag", "requested_limit": limit, "refined_count": 0}

    ollama_cfg = config.get("ollama", {})
    if not bool(ollama_cfg.get("enabled", False)):
        ranked = sorted(rows, key=lambda x: (to_float(x.get("pipeline_rank_score")) or 0.0), reverse=True)
        for idx, row in enumerate(ranked, start=1):
            row["rank"] = idx
        return ranked, {"enabled": False, "reason": "ollama_disabled", "requested_limit": limit, "refined_count": 0}

    prompt_path = resolve_agent_prompt_path(config, "agent_2", str(ollama_cfg.get("agent_prompt_file", DEFAULT_PROMPT_PATH)))
    system_prompt = read_prompt(prompt_path, agent_name="babis")
    if not system_prompt:
        ranked = sorted(rows, key=lambda x: (to_float(x.get("pipeline_rank_score")) or 0.0), reverse=True)
        for idx, row in enumerate(ranked, start=1):
            row["rank"] = idx
        return ranked, {"enabled": False, "reason": f"missing_prompt:{prompt_path}", "requested_limit": limit, "refined_count": 0}

    pipeline_cfg = config.get("pipeline", {})
    stage1_num_predict = int(pipeline_cfg.get("stage1_llm_num_predict", 160))
    stage1_timeout_seconds = int(pipeline_cfg.get("stage1_llm_timeout_seconds", 180))
    effective_limit = max(0, min(limit, len(rows)))
    refined_count = 0
    parse_failures = 0
    model_errors = 0

    for row in rows[:effective_limit]:
        ticker = str(row.get("ticker") or "").strip()
        stage_row = row_map.get(ticker)
        if not stage_row:
            row["llm_refine_error"] = "missing_stage_row"
            continue

        sec_filings = stage_row.get("sec_recent_filings") or []
        sec_compact = []
        for filing in sec_filings[:3]:
            sec_compact.append(
                {
                    "form": filing.get("form"),
                    "date": filing.get("filing_date"),
                    "items": filing.get("items", []),
                    "url": filing.get("url"),
                }
            )
        user_prompt = "\n".join(
            [
                "Refine this candidate for evidence-led company research ranking, regardless of share price.",
                f"Ticker: {ticker}",
                f"Company: {row.get('company')}",
                f"Share Price: {row.get('share_price')}",
                f"Market Cap: {row.get('market_cap')}",
                f"Composite Score: {row.get('composite_score')}",
                f"Current Risk Level: {row.get('risk_level')}",
                f"Cash: {row.get('cash')} | Debt: {row.get('debt')}",
                f"Operating CF: {row.get('cash_burn_proxy_ocf')} | Free CF: {row.get('cash_burn_proxy_fcf')}",
                f"Smart Money: signal={row.get('smart_money_signal')} score={row.get('smart_money_score')} "
                f"institution_ownership={row.get('institution_ownership')} "
                f"open_market_buys={row.get('open_market_buy_count')} open_market_sells={row.get('open_market_sell_count')} "
                f"darkpool_ratio={row.get('darkpool_activity_ratio')}",
                f"Dilution Flags: {', '.join(row.get('dilution_flags', [])) or 'none'}",
                f"Deterministic Catalyst Taxonomy: category={row.get('catalyst_category_id')} "
                f"direction={row.get('catalyst_direction')} attention={row.get('catalyst_attention_score')} "
                f"confidence={row.get('catalyst_classification_confidence')}",
                f"Recent SEC Filings: {json.dumps(sec_compact, ensure_ascii=False)}",
                "UNKNOWN where evidence is insufficient.",
                "Do not reinterpret a generic 8-K as a contract unless an SEC item or source text supports it.",
                "Return STRICT JSON only (no markdown, no prose).",
                "{",
                '  "ticker": "TICKER",',
                '  "catalyst_category_id": "earnings_guidance|commercial_customer|product_technology|clinical_healthcare|regulatory_legal|financing_liquidity|dilution_capital_structure|m_and_a_strategic|listing_compliance|governance_management|investor_events|technical_market_structure|macro_sector|unknown",',
                '  "main_catalyst": "short text",',
                '  "risk_level": "Low|Medium|High",',
                '  "classification": "Investable Improving Story|High-Conviction Operational Catalyst|Strong Watchlist Candidate|Tactical Event-Driven Trade|Credible Speculative Setup|Flow-Driven Low-Float Move|Financing Trap Risk|Dilution Trap Risk|Avoid / Insufficient Evidence",',
                '  "overall_opportunity": 0,',
                '  "confidence": "Low|Medium|High",',
                '  "confidence_note": "short text"',
                "}",
            ]
        )

        llm_text = call_ollama(
            host=str(ollama_cfg.get("host", "http://127.0.0.1:11434")),
            model=str(ollama_cfg.get("model", "gemma:7b")),
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            temperature=to_float(ollama_cfg.get("temperature")) or 0.2,
            num_predict=max(64, stage1_num_predict),
            timeout_seconds=max(60, stage1_timeout_seconds),
            output_json=True,
        )
        if llm_text.startswith("[OLLAMA_ERROR]"):
            model_errors += 1
            row["llm_refine_error"] = llm_text
            continue
        parsed = extract_first_json_object(llm_text)
        if not parsed:
            parse_failures += 1
            row["llm_refine_error"] = "json_parse_failed"
            continue

        refined_count += 1
        row["llm_refined"] = True
        row["llm_catalyst_category_id"] = str(parsed.get("catalyst_category_id") or "unknown")
        row["llm_main_catalyst"] = str(parsed.get("main_catalyst") or "")
        row["risk_level"] = normalize_level(parsed.get("risk_level"), {"Low", "Medium", "High"}, str(row.get("risk_level") or "Medium"))
        row["classification"] = str(parsed.get("classification") or "Strong Watchlist Candidate")
        row["llm_confidence"] = normalize_level(parsed.get("confidence"), {"Low", "Medium", "High"}, "Low")
        row["llm_confidence_note"] = str(parsed.get("confidence_note") or "")
        llm_opp = to_float(parsed.get("overall_opportunity"))
        if llm_opp is not None:
            llm_opp = max(0.0, min(10.0, llm_opp))
        row["llm_overall_opportunity"] = llm_opp

    for row in rows:
        base = (to_float(row.get("composite_score")) or 0.0) / 10.0
        llm_opp = to_float(row.get("llm_overall_opportunity"))
        score = base if llm_opp is None else (0.6 * base + 0.4 * llm_opp)
        if str(row.get("risk_level")) == "High":
            score -= 0.5
        row["pipeline_rank_score"] = round(max(0.0, min(10.0, score)), 2)

    ranked = sorted(
        rows,
        key=lambda x: (
            to_float(x.get("pipeline_rank_score")) or 0.0,
            to_float(x.get("composite_score")) or 0.0,
        ),
        reverse=True,
    )
    for idx, row in enumerate(ranked, start=1):
        row["rank"] = idx

    meta = {
        "enabled": True,
        "requested_limit": limit,
        "effective_limit": effective_limit,
        "refined_count": refined_count,
        "parse_failures": parse_failures,
        "model_errors": model_errors,
        "prompt_file": str(prompt_path),
        "num_predict": stage1_num_predict,
        "timeout_seconds": stage1_timeout_seconds,
    }
    return ranked, meta


def row_to_snapshot_for_llm(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "symbol": row.get("symbol"),
        "company_name": row.get("company_name"),
        "sector": row.get("sector"),
        "industry": row.get("industry"),
        "price_metrics": {},
        "fundamentals": {
            k: row.get(k)
            for k in row.keys()
            if k
            in {
                "market_cap",
                "revenue_ttm",
                "gross_margin",
                "operating_margin",
                "profit_margin",
                "revenue_growth",
                "earnings_growth",
                "roe",
                "total_cash",
                "total_debt",
                "debt_to_equity",
                "free_cashflow",
                "current_ratio",
                "forward_pe",
            }
        },
        "news": row.get("news", []),
        "sec_recent_filings": row.get("sec_recent_filings", []),
        "source_base": row.get("source_base", {}),
    }


def pipeline_uses_full_universe(args: argparse.Namespace, pipeline_cfg: dict[str, Any]) -> bool:
    if bool(getattr(args, "full_universe", False)):
        return True
    if bool(getattr(args, "respect_universe_cap", False)):
        return False
    return bool(pipeline_cfg.get("full_universe", True))


def run_pipeline(args: argparse.Namespace, config: dict[str, Any]) -> int:
    pipeline_cfg = config.get("pipeline", {})
    pipeline_config = copy.deepcopy(config)

    if pipeline_uses_full_universe(args, pipeline_cfg):
        pipeline_config.setdefault("universe", {})["max_symbols"] = 0

    symbols = resolve_universe(args, pipeline_config)
    if not symbols:
        print("No symbols resolved for pipeline run.")
        return 1
    universe_mode = str(
        pipeline_config.get("universe", {}).get("mode", "sec_us_list")
    ).strip().lower()
    symbol_routes: dict[str, dict[str, Any]] = {}
    universe_snapshot_id = None
    if universe_mode == "registry_all_listed":
        all_routes, universe_snapshot_id = load_all_listed_routes(
            pipeline_config.get("universe", {})
        )
        symbol_routes = {symbol: all_routes[symbol] for symbol in symbols}

    scan_cfg = pipeline_config.get("scan", {})
    lookback_days = int(scan_cfg.get("lookback_days", 420))
    min_history_days = int(scan_cfg.get("min_history_days", 220))
    stage1_limit = int(args.stage1_limit or pipeline_cfg.get("stage1_limit", 300))
    stage1_llm_refine_limit = int(args.stage1_llm_refine_limit or pipeline_cfg.get("stage1_llm_refine_limit", 0))
    stage2_limit = int(args.stage2_limit or pipeline_cfg.get("stage2_limit", 30))
    stage2_llm_limit = int(args.stage2_llm_limit or pipeline_cfg.get("stage2_llm_limit", 10))

    configured_max_price = to_float(scan_cfg.get("max_price"))
    stage1_max_price = to_float(args.max_price) if args.max_price is not None else configured_max_price

    stage1_min_composite = None
    if args.stage1_min_composite_score is not None:
        stage1_min_composite = to_float(args.stage1_min_composite_score)
    elif "stage1_min_composite_score" in pipeline_cfg:
        stage1_min_composite = to_float(pipeline_cfg.get("stage1_min_composite_score"))

    configured_min_fundamental_coverage = to_float(scan_cfg.get("min_fundamental_coverage"))
    stage1_min_fundamental_coverage = to_float(
        pipeline_cfg.get("stage1_min_fundamental_coverage")
    )
    if stage1_min_fundamental_coverage is None:
        stage1_min_fundamental_coverage = configured_min_fundamental_coverage

    ma_rules = pipeline_config.get("moving_average_rules", pipeline_config.get("technical_rules", []))
    fundamental_rules = pipeline_config.get("fundamental_rules", [])
    data_sources_cfg = pipeline_config.get("data_sources", {})
    stage1_data_sources_cfg, stage1_data_profile = build_stage1_data_sources_cfg(pipeline_config)
    weights = pipeline_config.get("weights", {})
    ma_weight = to_float(weights.get("moving_average"))
    if ma_weight is None:
        ma_weight = to_float(weights.get("technical"))
    if ma_weight is None:
        ma_weight = 0.5
    fundamental_weight = to_float(weights.get("fundamental")) or 0.5
    membership_cfg = pipeline_cfg.get("stage1_membership") or {}
    membership_enabled = bool(membership_cfg.get("enabled", False))
    membership_state: dict[str, Any] | None = None
    membership_active: set[str] = set()
    membership_exit = to_float(membership_cfg.get("exit_composite_score")) if membership_enabled else None
    membership_path = Path(str(membership_cfg.get("state_file") or "output/shortlist_membership.json"))
    if membership_enabled:
        membership_state = shortlist_membership.load(membership_path)
        membership_active = shortlist_membership.active_tickers(membership_state)
    fundamental_first = bool(pipeline_cfg.get("fundamental_first", True))
    stage1_scoring_mode = "fundamental_only" if fundamental_first else "mixed"
    stage1_ma_weight = 0.0 if fundamental_first else ma_weight
    stage1_fundamental_weight = 1.0 if fundamental_first else fundamental_weight

    output_dir = Path(config.get("output", {}).get("dir", "output"))
    output_dir.mkdir(parents=True, exist_ok=True)

    checkpoint_enabled = bool(pipeline_cfg.get("checkpoint_enabled", True))
    checkpoint_every = int(pipeline_cfg.get("checkpoint_every", 25))
    checkpoint_file_raw = str(pipeline_cfg.get("checkpoint_file", "output/babis_stage1_checkpoint.json")).strip()
    checkpoint_path = Path(checkpoint_file_raw)
    if not checkpoint_path.is_absolute():
        checkpoint_path = Path(checkpoint_file_raw)

    signature = stage1_signature(
        symbols=symbols,
        lookback_days=lookback_days,
        min_history_days=min_history_days,
        max_price=stage1_max_price,
        min_composite_score=stage1_min_composite,
        min_fundamental_coverage=stage1_min_fundamental_coverage,
        snapshot_profile=stage1_data_profile,
        data_sources_cfg=stage1_data_sources_cfg,
        scoring_mode=stage1_scoring_mode + (
            f"|membership:{membership_exit}:"
            + hashlib.sha256(",".join(sorted(membership_active)).encode("utf-8")).hexdigest()[:16]
            if membership_enabled else ""
        ),
        symbol_routes=symbol_routes,
    )
    resume_state = None
    resumed_from = 0
    if checkpoint_enabled:
        existing_checkpoint = load_checkpoint(checkpoint_path)
        if (
            existing_checkpoint
            and existing_checkpoint.get("status") == "running"
            and existing_checkpoint.get("signature") == signature
        ):
            processed = int(existing_checkpoint.get("processed_count", 0))
            if 0 < processed < len(symbols):
                resume_state = existing_checkpoint
                resumed_from = processed
                print(f"[PIPELINE] Resuming Stage-1 from checkpoint {processed}/{len(symbols)}")
        elif existing_checkpoint and existing_checkpoint.get("status") == "running":
            print("[PIPELINE] Ignoring stale checkpoint due to signature mismatch.")

    print(
        f"[PIPELINE] Stage-1 μπαμπης scanning {len(symbols)} symbols | max_price={stage1_max_price} | "
        f"min_composite={stage1_min_composite} | min_fundamental_coverage={stage1_min_fundamental_coverage} | "
        f"scoring_mode={stage1_scoring_mode}"
    )
    info_cache = None
    if str((pipeline_cfg.get("stage1_info_cache") or {}).get("mode", "off")) != "off":
        from stage1_info_cache import Stage1InfoCache
        info_cache = Stage1InfoCache.from_config(pipeline_config, load_sec_ticker_cik_map(stage1_data_sources_cfg))
        print(f"[PIPELINE] Stage-1 info cache mode={info_cache.mode} news_watchlist={len(info_cache.news_watchlist)}")
    # companies whose valuation has no meaning yet (owner 2026-10-22): looked at afresh every night until filled
    valuation_gap_state = valuation_gaps.Gaps()
    if info_cache is not None:
        info_cache.always_fresh = valuation_gap_state.open_tickers()
    sec_ratio_source = None
    if bool(pipeline_cfg.get("babis_sec_first", False)):
        sec_ratio_source = sec_fundamental_ratios.SecRatioSource()
        print("[PIPELINE] Stage-1 rules SEC-first (yfinance as labelled fallback)")
    stage1_payload = scan_market_core(
        symbols=symbols,
        lookback_days=lookback_days,
        min_history_days=min_history_days,
        max_price=stage1_max_price,
        min_composite_score=stage1_min_composite,
        min_fundamental_coverage=stage1_min_fundamental_coverage,
        ma_rules=ma_rules,
        fundamental_rules=fundamental_rules,
        ma_weight=stage1_ma_weight,
        fundamental_weight=stage1_fundamental_weight,
        data_sources_cfg=stage1_data_sources_cfg,
        snapshot_profile=stage1_data_profile,
        checkpoint_path=checkpoint_path if checkpoint_enabled else None,
        checkpoint_every=checkpoint_every if checkpoint_enabled else 0,
        checkpoint_signature=signature if checkpoint_enabled else None,
        resume_state=resume_state,
        symbol_routes=symbol_routes,
        info_cache=info_cache,
        membership_active=membership_active,
        exit_composite_score=membership_exit,
        workers=max(1, int(pipeline_cfg.get("stage1_workers", 1) or 1)),
        sec_ratios=sec_ratio_source,
        valuation_gap_state=valuation_gap_state,
    )
    if valuation_gap_state is not None:
        print(f"[PIPELINE] Valuation gaps: {valuation_gap_state.save()}")
    if sec_ratio_source is not None:
        sec_ratio_source.save()
    membership_events: list[dict[str, Any]] = []
    if membership_enabled and membership_state is not None:
        membership_events = shortlist_membership.update(
            membership_state,
            [str(row.get("ticker")) for row in stage1_payload["results"] if row.get("ticker")],
            dict(stage1_payload.get("excluded_tickers") or {}),
            now_athens().isoformat(),
        )
        shortlist_membership.save(membership_path, membership_state)
        print(f"[PIPELINE] Shortlist membership: {sum(e['event'] == 'added' for e in membership_events)} added, "
              f"{sum(e['event'] == 'removed' for e in membership_events)} removed, "
              f"{len(shortlist_membership.active_tickers(membership_state))} active")
    if not stage1_payload["results"] and (
        stage1_payload["errors"] or stage1_payload.get("history_exclusions")
    ):
        print("Stage-1 produced no usable results.")
        return 2

    babis_rows = stage1_payload["results"][:stage1_limit]
    shortlist_enrichment_cfg = pipeline_cfg.get("shortlist_deep_enrichment", {})
    if not isinstance(shortlist_enrichment_cfg, dict):
        shortlist_enrichment_cfg = {}
    shortlist_enrichment_enabled = bool(shortlist_enrichment_cfg.get("enabled", True))
    shortlist_enrichment_limit = int(shortlist_enrichment_cfg.get("limit", stage1_limit))
    shortlist_enrichment_meta = {
        "enabled": False,
        "target_count": 0,
        "enriched_count": 0,
        "error_count": 0,
        "elapsed_seconds": 0.0,
        "avg_symbol_seconds": 0.0,
        "market_data_seconds": 0.0,
        "fundamentals_and_news_seconds": 0.0,
        "sec_seconds": 0.0,
        "openbb_research_seconds": 0.0,
        "errors": [],
    }
    if shortlist_enrichment_enabled and babis_rows:
        shortlist_enrichment_meta = enrich_stage1_rows_for_shortlist(
            rows=babis_rows,
            lookback_days=lookback_days,
            min_history_days=min_history_days,
            data_sources_cfg=data_sources_cfg,
            limit=shortlist_enrichment_limit,
        )
    babis_handoff = build_babis_handoff_rows(babis_rows)
    babis_handoff, babis_refine_meta = refine_babis_handoff_with_llm(
        handoff_rows=babis_handoff,
        stage1_rows=babis_rows,
        config=config,
        limit=stage1_llm_refine_limit,
        disabled=bool(args.no_stage1_llm),
    )
    ecosystem_run_id = resolve_run_id()
    babis_handoff = [
        unknown_data.annotate_row(row, "babis", run_id=ecosystem_run_id)
        for row in babis_handoff
    ]
    traps = select_traps(babis_handoff, limit=5)

    babis_row_map = {str(row.get("symbol")): row for row in babis_rows}
    giannis_rows: list[dict[str, Any]] = []
    for item in babis_handoff[:stage2_limit]:
        ticker = str(item.get("ticker") or "")
        row = babis_row_map.get(ticker)
        if row:
            giannis_rows.append(row)

    timestamp = now_athens().strftime("%Y%m%d_%H%M%S")

    stage1_path = output_dir / f"babis_shortlist_{timestamp}.json"
    stage1_latest_path = output_dir / "babis_shortlist_latest.json"
    stage1_delta_path = output_dir / f"babis_shortlist_delta_{timestamp}.json"
    stage1_delta_latest_path = output_dir / "babis_shortlist_delta_latest.json"
    catalyst_review_path = output_dir / f"catalyst_content_review_queue_{timestamp}.json"
    catalyst_review_latest_path = output_dir / "catalyst_content_review_queue_latest.json"
    instrument_observations_path = output_dir / f"all_listed_instrument_observations_{timestamp}.json"
    instrument_observations_latest_path = output_dir / "all_listed_instrument_observations_latest.json"
    babis_unknown_events = [
        event for row in babis_handoff for event in (row.get("unknown_events") or [])
    ]
    babis_unknown_summary = unknown_data.summarize_events(
        babis_unknown_events,
        declared_fields=len(unknown_data.load_contract()["agents"]["babis"]["fields"]) * len(babis_handoff),
    )
    catalyst_review_queue = catalyst_content_review.build_queue(
        babis_handoff, ecosystem_run_id
    )
    stage1_payload_out = attach_run_context({
        "run_timestamp": now_athens().isoformat(),
        "agent": "μπαμπης",
        "scan_scope": "best-effort full universe"
        if pipeline_config.get("universe", {}).get("max_symbols", 0) == 0
        else "capped universe",
        "filters": {
            "max_price": stage1_max_price,
            "min_composite_score": stage1_min_composite,
            "analysis_lane": "fundamental_discovery",
            "scoring_mode": stage1_scoring_mode,
            "technical_weight": stage1_ma_weight,
            "fundamental_weight": stage1_fundamental_weight,
            "excluded_security_name_keywords": pipeline_config.get("universe", {}).get(
                "exclude_name_keywords", DEFAULT_SECURITY_EXCLUDE_KEYWORDS
            ),
        },
        "source_summary": {
            "market_data_provider_priority": data_sources_cfg.get("market_data", {}).get(
                "provider_priority", ["alpaca", "polygon", "yfinance"]
            ),
            "primary_filings_source": "SEC EDGAR",
            "macro_source": "FRED",
            "supplementary_ownership_flow_source": "OpenBB (SEC/yfinance/FINRA adapters)",
            "stage1_snapshot_profile": stage1_data_profile,
            "stage1_source_overrides_applied": stage1_data_sources_cfg != data_sources_cfg,
        },
        "universe_total": stage1_payload.get("symbols_total", 0),
        "universe_snapshot_id": universe_snapshot_id,
        "universe_mode": universe_mode,
        "universe_routing": stage1_payload.get("universe_routing", {}),
        "instrument_observations": {
            "path": str(instrument_observations_path),
            "latest_path": str(instrument_observations_latest_path),
            "count": len(stage1_payload.get("instrument_observations", [])),
        },
        "candidates_total": len(stage1_payload.get("results", [])),
        "shortlist_total": len(babis_handoff),
        "membership": {
            "enabled": membership_enabled,
            "entry_composite_score": stage1_min_composite,
            "exit_composite_score": membership_exit,
            "active_members": len(shortlist_membership.active_tickers(membership_state)) if membership_state else 0,
            "added": [e["ticker"] for e in membership_events if e["event"] == "added"],
            "removed": [{"ticker": e["ticker"], "reason": e.get("reason")}
                        for e in membership_events if e["event"] == "removed"],
        },
        "runtime_summary": stage1_payload.get("runtime_summary", {}),
        "shortlist_deep_enrichment": shortlist_enrichment_meta,
        "checkpoint_meta": {
            "enabled": checkpoint_enabled,
            "checkpoint_file": str(checkpoint_path),
            "checkpoint_every": checkpoint_every if checkpoint_enabled else 0,
            "resumed_from": resumed_from,
            "processed_count": stage1_payload.get("processed_count", len(symbols)),
        },
        "llm_refine_meta": babis_refine_meta,
        "data_quality_summary": {
            "snapshot_success_count": stage1_payload.get("snapshot_success_count", 0),
            "data_coverage_pct": stage1_payload.get("data_coverage_pct", 0.0),
            "technical_error_count": stage1_payload.get("error_count", 0),
            "history_exclusion_count": stage1_payload.get("history_exclusion_count", 0),
            "coverage_exclusion_count": stage1_payload.get("coverage_exclusion_count", 0),
            "unknown_data": babis_unknown_summary,
        },
        "catalyst_content_review_queue": {
            "path": str(catalyst_review_path),
            "latest_path": str(catalyst_review_latest_path),
            "item_count": catalyst_review_queue["item_count"],
            "queue_sha256": catalyst_review_queue["queue_sha256"],
            "execution_authority": False,
        },
        "shortlist": babis_handoff,
        "traps": traps,
        "history_exclusions": stage1_payload.get("history_exclusions", []),
        "coverage_exclusions": stage1_payload.get("coverage_exclusions", []),
        "provider_retry_backlog": stage1_payload.get("provider_retry_backlog", []),
        "errors": stage1_payload.get("errors", []),
    }, stage="babis_shortlist", run_id=ecosystem_run_id)
    catalyst_content_review.write_queue(
        catalyst_review_queue, catalyst_review_path, catalyst_review_latest_path
    )
    instrument_observation_payload = attach_run_context({
        "schema": "ecosystem.all_listed_instrument_observations.v1",
        "run_timestamp": now_athens().isoformat(),
        "snapshot_id": universe_snapshot_id,
        "universe_total": stage1_payload.get("symbols_total", 0),
        "routing": stage1_payload.get("universe_routing", {}),
        "observations": stage1_payload.get("instrument_observations", []),
        "history_exclusions": [
            item for item in stage1_payload.get("history_exclusions", [])
            if not item.get("company_fundamental_eligible", True)
        ],
        "coverage_exclusions": stage1_payload.get("coverage_exclusions", []),
        "errors": [
            item for item in stage1_payload.get("errors", [])
            if not item.get("company_fundamental_eligible", True)
        ],
    }, stage="all_listed_instrument_observations", run_id=ecosystem_run_id)
    instrument_observations_path.write_text(
        json.dumps(instrument_observation_payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    instrument_observations_latest_path.write_text(
        json.dumps(instrument_observation_payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    stage1_path.write_text(json.dumps(stage1_payload_out, ensure_ascii=False, indent=2), encoding="utf-8")
    previous_latest = load_json_file(stage1_latest_path)
    previous_rows = []
    if previous_latest:
        previous_rows = list(previous_latest.get("shortlist", []))
    watchlist_delta = build_watchlist_delta(previous_rows, babis_handoff)
    delta_payload = {
        "run_id": ecosystem_run_id,
        "run_timestamp": stage1_payload_out.get("run_timestamp"),
        "agent": "μπαμπης",
        "previous_run_timestamp": (previous_latest or {}).get("run_timestamp"),
        "current_run_file": str(stage1_path),
        "previous_run_file": str(stage1_latest_path) if previous_latest else None,
        "delta": watchlist_delta,
    }
    stage1_delta_path.write_text(json.dumps(delta_payload, ensure_ascii=False, indent=2), encoding="utf-8")
    stage1_delta_latest_path.write_text(json.dumps(delta_payload, ensure_ascii=False, indent=2), encoding="utf-8")
    stage1_latest_path.write_text(json.dumps(stage1_payload_out, ensure_ascii=False, indent=2), encoding="utf-8")

    traps_path = output_dir / f"babis_traps_{timestamp}.json"
    traps_path.write_text(
        json.dumps({"run_id": ecosystem_run_id, "run_timestamp": now_athens().isoformat(), "agent": "μπαμπης", "traps": traps}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    runtime_md_path, runtime_json_path = write_pipeline_runtime_report(
        output_dir=output_dir,
        timestamp=timestamp,
        stage1_runtime=stage1_payload.get("runtime_summary", {}),
        shortlist_enrichment=shortlist_enrichment_meta,
    )

    store_shortlist_payload(config, stage1_path, stage1_payload_out)

    giannis_texts: list[dict[str, str]] = []
    if not args.no_stage2_llm and config.get("ollama", {}).get("enabled", False):
        ollama_cfg = config.get("ollama", {})
        giannis_prompt_path = resolve_agent_prompt_path(
            config, "agent_1", str(ollama_cfg.get("agent_prompt_file", DEFAULT_PROMPT_PATH))
        )
        giannis_system_prompt = read_prompt(giannis_prompt_path, agent_name="giannis")
        if giannis_system_prompt:
            limit = max(0, min(stage2_llm_limit, len(giannis_rows)))
            print(f"[PIPELINE] Stage-2 Γιαννης generating briefs for {limit} symbols")
            for row in giannis_rows[:limit]:
                snapshot = row_to_snapshot_for_llm(row)
                user_prompt = build_llm_prompt(snapshot, "Pipeline handoff from μπαμπης: strict fundamental validation.")
                llm_text = call_ollama(
                    host=str(ollama_cfg.get("host", "http://127.0.0.1:11434")),
                    model=str(ollama_cfg.get("model", "gemma:7b")),
                    system_prompt=giannis_system_prompt,
                    user_prompt=user_prompt,
                    temperature=to_float(ollama_cfg.get("temperature")) or 0.2,
                    num_predict=int(ollama_cfg.get("num_predict", 350)),
                    timeout_seconds=int(ollama_cfg.get("timeout_seconds", 300)),
                )
                giannis_texts.append(
                    {
                        "ticker": str(row.get("symbol")),
                        "company": str(row.get("company_name")),
                        "analysis": llm_text or "No response from model.",
                    }
                )

    giannis_md_path = output_dir / f"giannis_briefs_{timestamp}.md"
    giannis_lines = [
        "# Γιαννης - Fundamental Validation Briefs",
        "",
        f"- Candidate count from μπαμπης: {len(giannis_rows)}",
        f"- LLM briefs generated: {len(giannis_texts)}",
        "",
    ]
    if giannis_texts:
        for item in giannis_texts:
            giannis_lines.append(f"## {item['ticker']} - {item['company']}")
            giannis_lines.append("")
            giannis_lines.append(item["analysis"])
            giannis_lines.append("")
    else:
        giannis_lines.append("- No LLM briefs generated in this run.")
    giannis_md_path.write_text("\n".join(giannis_lines), encoding="utf-8")

    pipeline_md_path = output_dir / f"pipeline_report_{timestamp}.md"
    pipeline_lines = [
        "# Pipeline Report (μπαμπης -> Γιαννης)",
        "",
        f"- Run timestamp: {now_athens().isoformat()}",
        f"- Universe scanned: {stage1_payload.get('symbols_total', 0)}",
        f"- Stage-1 shortlist: {len(babis_handoff)}",
        f"- Stage-1 LLM refined: {babis_refine_meta.get('refined_count', 0)} / {babis_refine_meta.get('effective_limit', 0)}",
        f"- Stage-1 snapshot profile: {stage1_data_profile}",
        f"- Stage-1 runtime report: {runtime_md_path.name}",
        f"- Shortlist deep enrichment: {shortlist_enrichment_meta.get('enriched_count', 0)} / {shortlist_enrichment_meta.get('target_count', 0)}",
        f"- Stage-2 deep-review queue: {len(giannis_rows)}",
        f"- Stage-2 LLM briefs generated: {len(giannis_texts)}",
        "",
        "## Top Stage-1 Candidates",
        "",
    ]
    for row in babis_handoff[:10]:
        pipeline_lines.append(
            f"- #{row.get('rank')} {row.get('ticker')} | Catalyst: {row.get('catalyst_bucket')} | Risk: {row.get('risk_level')} | Verdict: {row.get('verdict')}"
        )
    pipeline_lines.extend(["", "## Stage-1 Traps", ""])
    if traps:
        for item in traps:
            pipeline_lines.append(
                f"- {item.get('ticker')} | Risk: {item.get('risk_level')} | Flags: {', '.join(item.get('dilution_flags', [])) or 'none'}"
            )
    else:
        pipeline_lines.append("- No high-risk traps flagged in shortlisted names.")
    pipeline_lines.append("")
    pipeline_md_path.write_text("\n".join(pipeline_lines), encoding="utf-8")

    print(f"[OUTPUT] BABIS SHORTLIST: {stage1_path}")
    print(f"[OUTPUT] BABIS SHORTLIST LATEST: {stage1_latest_path}")
    print(f"[OUTPUT] BABIS DELTA: {stage1_delta_path}")
    print(f"[OUTPUT] BABIS DELTA LATEST: {stage1_delta_latest_path}")
    print(f"[OUTPUT] BABIS TRAPS: {traps_path}")
    print(f"[OUTPUT] GIANNIS BRIEFS: {giannis_md_path}")
    print(f"[OUTPUT] PIPELINE REPORT: {pipeline_md_path}")
    print(f"[OUTPUT] PIPELINE RUNTIME REPORT: {runtime_md_path}")
    print(f"[OUTPUT] PIPELINE RUNTIME JSON: {runtime_json_path}")
    return 0


def run_scan(args: argparse.Namespace, config: dict[str, Any]) -> int:
    symbols = resolve_universe(args, config)
    if not symbols:
        print("No symbols provided. Add symbols in scanner_config.json or pass --tickers.")
        return 1

    scan_cfg = config.get("scan", {})
    lookback_days = int(scan_cfg.get("lookback_days", 420))
    min_history_days = int(scan_cfg.get("min_history_days", 220))
    top_n = int(args.top_n or scan_cfg.get("top_n", 15))
    cfg_max_price = to_float(scan_cfg.get("max_price"))
    max_price = to_float(args.max_price) if args.max_price is not None else cfg_max_price
    cfg_min_composite = to_float(scan_cfg.get("min_composite_score"))
    min_composite_score = (
        to_float(args.min_composite_score) if args.min_composite_score is not None else cfg_min_composite
    )
    min_fundamental_coverage = to_float(scan_cfg.get("min_fundamental_coverage"))

    ma_rules = config.get("moving_average_rules", config.get("technical_rules", []))
    fundamental_rules = config.get("fundamental_rules", [])
    data_sources_cfg = config.get("data_sources", {})
    weights = config.get("weights", {})
    ma_weight = to_float(weights.get("moving_average"))
    if ma_weight is None:
        ma_weight = to_float(weights.get("technical"))
    if ma_weight is None:
        ma_weight = 0.5
    fundamental_weight = to_float(weights.get("fundamental")) or 0.5

    payload = scan_market_core(
        symbols=symbols,
        lookback_days=lookback_days,
        min_history_days=min_history_days,
        max_price=max_price,
        min_composite_score=min_composite_score,
        min_fundamental_coverage=min_fundamental_coverage,
        ma_rules=ma_rules,
        fundamental_rules=fundamental_rules,
        ma_weight=ma_weight,
        fundamental_weight=fundamental_weight,
        data_sources_cfg=data_sources_cfg,
    )

    if not payload["results"] and (payload["errors"] or payload.get("history_exclusions")):
        print("No successful snapshots. Check your symbol list and network access.")
        return 2

    output_dir = Path(config.get("output", {}).get("dir", "output"))
    paths = write_scan_outputs(
        payload=payload,
        output_dir=output_dir,
        top_n=top_n,
        filename_prefix="scan",
        write_mini=True,
    )
    store_scan_payload(config, paths["json"], payload)
    sorted_rows = payload.get("results", [])
    top_rows = sorted_rows[:top_n]

    if not args.no_llm and config.get("ollama", {}).get("enabled", False):
        prompt_path = Path(config.get("ollama", {}).get("agent_prompt_file", DEFAULT_PROMPT_PATH))
        system_prompt = read_prompt(prompt_path, agent_name="giannis")
        ollama_cfg = config.get("ollama", {})
        llm_path = output_dir / f"llm_briefs_{now_athens().strftime('%Y%m%d_%H%M%S')}.md"
        llm_lines = ["# Daily Fundamental Briefs", ""]
        for row in top_rows:
            snapshot = {
                "symbol": row.get("symbol"),
                "company_name": row.get("company_name"),
                "sector": row.get("sector"),
                "industry": row.get("industry"),
                "price_metrics": {},
                "fundamentals": {k: row.get(k) for k in row.keys() if k in {
                    "market_cap",
                    "revenue_ttm",
                    "gross_margin",
                    "operating_margin",
                    "profit_margin",
                    "revenue_growth",
                    "earnings_growth",
                    "roe",
                    "total_cash",
                    "total_debt",
                    "debt_to_equity",
                    "free_cashflow",
                    "current_ratio",
                    "forward_pe",
                }},
                "news": row.get("news", []),
                "sec_recent_filings": row.get("sec_recent_filings", []),
                "source_base": row.get("source_base", {}),
            }
            user_prompt = build_llm_prompt(snapshot, "Daily monitoring brief for investment research desk.")
            llm_text = call_ollama(
                host=str(ollama_cfg.get("host", "http://127.0.0.1:11434")),
                model=str(ollama_cfg.get("model", "gemma4:e4b")),
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                temperature=to_float(ollama_cfg.get("temperature")) or 0.2,
                num_predict=int(ollama_cfg.get("num_predict", 500)),
                timeout_seconds=int(ollama_cfg.get("timeout_seconds", 240)),
            )
            llm_lines.append(f"## {row.get('symbol')} - {row.get('company_name')}")
            llm_lines.append("")
            llm_lines.append(llm_text or "No response from Ollama.")
            llm_lines.append("")
        llm_path.write_text("\n".join(llm_lines), encoding="utf-8")
        print(f"[LLM] Saved briefs to {llm_path}")

    print(f"[OUTPUT] CSV: {paths['csv']}")
    print(f"[OUTPUT] JSON: {paths['json']}")
    print(f"[OUTPUT] MD: {paths['md']}")
    if "mini" in paths:
        print(f"[OUTPUT] MINI: {paths['mini']}")
    return 0


def run_brief(args: argparse.Namespace, config: dict[str, Any]) -> int:
    symbol = normalize_symbol(args.ticker)
    scan_cfg = config.get("scan", {})
    data_sources_cfg = config.get("data_sources", {})
    lookback_days = int(scan_cfg.get("lookback_days", 420))
    min_history_days = int(scan_cfg.get("min_history_days", 220))

    snapshot = fetch_snapshot(symbol, lookback_days, min_history_days, data_sources_cfg=data_sources_cfg)

    ollama_cfg = config.get("ollama", {})
    if not ollama_cfg.get("enabled", False):
        print("Ollama is disabled in config.")
        return 1

    prompt_path = Path(ollama_cfg.get("agent_prompt_file", DEFAULT_PROMPT_PATH))
    system_prompt = read_prompt(prompt_path, agent_name="giannis")
    if not system_prompt:
        print(f"Prompt file is empty or missing: {prompt_path}")
        return 1

    user_prompt = build_llm_prompt(snapshot, args.question)
    response = call_ollama(
        host=str(ollama_cfg.get("host", "http://127.0.0.1:11434")),
        model=str(ollama_cfg.get("model", "gemma4:e4b")),
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        temperature=to_float(ollama_cfg.get("temperature")) or 0.2,
        num_predict=int(ollama_cfg.get("num_predict", 800)),
        timeout_seconds=int(ollama_cfg.get("timeout_seconds", 240)),
    )

    print("")
    print(f"===== BRIEF {symbol} =====")
    print(response)
    print("")

    output_dir = Path(config.get("output", {}).get("dir", "output"))
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = now_athens().strftime("%Y%m%d_%H%M%S")
    path = output_dir / f"brief_{symbol}_{timestamp}.md"
    path.write_text(response, encoding="utf-8")
    print(f"[OUTPUT] Brief saved to {path}")
    return 0


def make_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="US equity fundamental monitoring workflow with Ollama.")
    parser.add_argument("--config", default=DEFAULT_CONFIG_PATH, help="Path to scanner config JSON.")
    parser.add_argument("--env-file", default=".env", help="Path to .env file with API keys.")

    subparsers = parser.add_subparsers(dest="command", required=True)

    scan_parser = subparsers.add_parser("scan", help="Run full market scan.")
    scan_parser.add_argument("--tickers", nargs="+", help="Optional override ticker list.")
    scan_parser.add_argument("--top-n", type=int, help="Number of top results to keep.")
    scan_parser.add_argument("--max-price", type=float, help="Keep only symbols with close <= this price.")
    scan_parser.add_argument(
        "--min-composite-score",
        type=float,
        help="Keep only symbols with composite score >= this threshold.",
    )
    scan_parser.add_argument("--no-llm", action="store_true", help="Disable Ollama briefs for this run.")

    brief_parser = subparsers.add_parser("brief", help="Run one focused brief for a single ticker.")
    brief_parser.add_argument("--ticker", required=True, help="Ticker symbol, e.g. AAPL.")
    brief_parser.add_argument("--question", help="Optional focus question for discussion/hypothesis mode.")

    pipeline_parser = subparsers.add_parser("pipeline", help="Run collaborative pipeline: μπαμπης -> Γιαννης.")
    pipeline_parser.add_argument("--tickers", nargs="+", help="Optional override ticker list.")
    pipeline_parser.add_argument("--max-price", type=float, help="Stage-1 max price filter (default: scan.max_price).")
    pipeline_parser.add_argument(
        "--stage1-min-composite-score",
        type=float,
        help="Optional Stage-1 min composite threshold. Omit to disable in pipeline mode.",
    )
    pipeline_parser.add_argument("--stage1-limit", type=int, help="How many names μπαμπης forwards to handoff.")
    pipeline_parser.add_argument(
        "--stage1-llm-refine-limit",
        type=int,
        help="How many Stage-1 names get μπαμπης LLM refine before handoff ranking.",
    )
    pipeline_parser.add_argument("--no-stage1-llm", action="store_true", help="Skip μπαμπης LLM refine stage.")
    pipeline_parser.add_argument("--stage2-limit", type=int, help="How many names enter Γιαννης deep-review queue.")
    pipeline_parser.add_argument("--stage2-llm-limit", type=int, help="How many Stage-2 names get LLM full briefs.")
    pipeline_parser.add_argument("--no-stage2-llm", action="store_true", help="Skip Γιαννης LLM stage.")
    pipeline_parser.add_argument(
        "--respect-universe-cap",
        action="store_true",
        help="Use universe.max_symbols cap from config instead of full-universe pipeline mode.",
    )
    pipeline_parser.add_argument(
        "--full-universe",
        action="store_true",
        help="Explicitly scan the full configured SEC exchange universe, ignoring universe.max_symbols.",
    )
    return parser


def main() -> int:
    parser = make_parser()
    args = parser.parse_args()
    load_dotenv(Path(args.env_file))
    config_path = Path(args.config)
    if not config_path.exists():
        print(f"Missing config: {config_path}")
        return 1
    config = load_json(config_path)

    if args.command == "scan":
        return run_scan(args, config)
    if args.command == "brief":
        return run_brief(args, config)
    if args.command == "pipeline":
        return run_pipeline(args, config)

    parser.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
