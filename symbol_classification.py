from __future__ import annotations

import os
import re
import sqlite3
from functools import lru_cache
from pathlib import Path
from typing import Any


SCHEMA = "ecosystem.symbol_classification.v1"
TAXONOMY_VERSION = "1.0.0"

# Babis's registry is the single classification authority. Analysis modules
# (Giannis, information ledger, experiment registry, scorecards) read the
# effective registry node - including approved corrections - and only fall
# back to the raw provider label when the registry has no unambiguous node.
# ECOSYSTEM_CLASSIFICATION_SOURCE selects the source: "registry" or "provider".
REGISTRY_DB = Path(__file__).resolve().parent / "data" / "research_registry.db"
CORPORATE_TYPES = ("equity_candidate", "adr_candidate")
# Babis's registry is the classification source for analysis since 2026-09-28
# (owner's decision, accepting that industry_id history restarts under registry
# nodes). Set ECOSYSTEM_CLASSIFICATION_SOURCE=provider to fall back to labels.
DEFAULT_CLASSIFICATION_SOURCE = "registry"


def _registry_enabled() -> bool:
    source = os.environ.get("ECOSYSTEM_CLASSIFICATION_SOURCE", DEFAULT_CLASSIFICATION_SOURCE)
    return source.strip().lower() == "registry"


def _file_version(path: Path) -> tuple[int, int]:
    """Change marker for the database, including its write-ahead log."""
    main = path.stat().st_mtime_ns if path.exists() else 0
    wal = Path(str(path) + "-wal")
    return main, wal.stat().st_mtime_ns if wal.exists() else 0


@lru_cache(maxsize=2)
def _load_registry(db_path: str, _version: tuple[int, int]) -> dict[str, dict[str, Any]]:
    """Effective company nodes of the latest universe snapshot, keyed by ticker.

    A ticker listed on several venues with different nodes is ambiguous and is
    left out, so it falls back to the provider label instead of being guessed.
    """
    path = Path(db_path)
    if not path.exists():
        return {}
    conn = sqlite3.connect(f"file:{path.resolve().as_posix()}?mode=ro", uri=True)
    try:
        has_view = conn.execute("SELECT 1 FROM sqlite_master WHERE name='effective_listed_symbol'").fetchone()
        table = "effective_listed_symbol" if has_view else "listed_symbol"
        snapshot = conn.execute(
            "SELECT snapshot_id FROM universe_snapshot ORDER BY retrieved_at DESC, rowid DESC LIMIT 1").fetchone()
        if not snapshot:
            return {}
        marks = ",".join("?" for _ in CORPORATE_TYPES)
        rows = conn.execute(
            f"SELECT ticker, node_id, status, version FROM {table} "
            f"WHERE snapshot_id=? AND node_id IS NOT NULL AND instrument_type IN ({marks})",
            (snapshot[0], *CORPORATE_TYPES)).fetchall()
    except sqlite3.Error:
        return {}
    finally:
        conn.close()
    found: dict[str, dict[str, Any]] = {}
    ambiguous: set[str] = set()
    for ticker, node, status, version in rows:
        key = str(ticker).upper()
        if key in found and found[key]["node_id"] != node:
            ambiguous.add(key)
        found.setdefault(key, {"node_id": node, "status": status, "taxonomy_version": version})
    return {key: value for key, value in found.items() if key not in ambiguous}


def registry_classification(ticker: str, db_path: Path | None = None) -> dict[str, Any] | None:
    """The registry's effective node for a company ticker, or None."""
    if not ticker or not _registry_enabled():
        return None
    path = db_path or REGISTRY_DB
    return _load_registry(str(path), _file_version(path)).get(ticker.upper())
ASSET_SCHEMA = "ecosystem.security_asset_classification.v1"

# Stable, open internal taxonomy. Provider labels are normalized into these IDs;
# the project does not claim that this is a licensed GICS classification.
SECTORS: dict[str, str] = {
    "technology": "Technology",
    "healthcare": "Healthcare",
    "financials": "Financials",
    "consumer_discretionary": "Consumer Discretionary",
    "consumer_staples": "Consumer Staples",
    "industrials": "Industrials",
    "energy": "Energy",
    "materials": "Materials",
    "utilities": "Utilities",
    "real_estate": "Real Estate",
    "communication_services": "Communication Services",
    "unknown": "Unknown",
}

SECTOR_ALIASES = {
    "technology": "technology",
    "healthcare": "healthcare",
    "financial services": "financials",
    "financials": "financials",
    "consumer cyclical": "consumer_discretionary",
    "consumer discretionary": "consumer_discretionary",
    "consumer defensive": "consumer_staples",
    "consumer staples": "consumer_staples",
    "industrials": "industrials",
    "energy": "energy",
    "basic materials": "materials",
    "materials": "materials",
    "utilities": "utilities",
    "real estate": "real_estate",
    "communication services": "communication_services",
}


def classify_security_asset(symbol: Any, security_name: Any = None) -> dict[str, Any]:
    """Classify the tradable instrument separately from sector/industry.

    SEC's exchange ticker feed does not expose a canonical security-type field, so
    this classifier deliberately reports its evidence and confidence.  High/medium
    confidence special securities are not eligible for the common-equity universe;
    ambiguous single-letter suffixes remain eligible until an asset master confirms them.
    """
    ticker = str(symbol or "").upper().strip()
    name = str(security_name or "").strip()
    lowered = name.lower()
    spac_name = any(
        token in lowered
        for token in (
            "blank check",
            "acquisition corp",
            "acquisition corporation",
            "merger corp",
        )
    ) or bool(
        re.search(r"\bspac\b", lowered)
    ) or bool(
        re.search(
            r"\b(?:acquisition|merger)\b.{0,40}\b(?:corp(?:oration)?|co(?:mpany)?)\b",
            lowered,
        )
    )

    asset_type = "common_equity"
    confidence = "medium"
    evidence = "no special-security marker detected"

    if "warrant" in lowered or re.search(r"-(?:WT[A-Z]?|WS[A-Z]?)$", ticker) or (spac_name and ticker.endswith("W")):
        asset_type, confidence, evidence = "warrant", "high", "name or warrant symbol marker"
    elif re.search(r"-UN$", ticker) or re.search(r"\bunits?\b", lowered) or (spac_name and ticker.endswith("U")):
        asset_type, confidence, evidence = "unit", "high", "name or unit symbol marker"
    elif (re.search(r"\bpreferred\b.*\b(?:stock|shares?|securities|depositary|series)\b", lowered)
          or "depositary share" in lowered or re.search(r"-P[A-Z0-9]+$", ticker)):
        asset_type, confidence, evidence = "preferred_or_depositary", "high", "name or preferred symbol marker"
    elif (
        re.search(r"(?:^|[-,])\s*rights?\b|\brights?$", lowered)
        or re.search(r"-(?:R|RI|RT)$", ticker)
        or (spac_name and ticker.endswith("R"))
    ):
        asset_type, confidence, evidence = "right", "high", "name or SPAC right symbol marker"
    elif any(re.search(r"\b" + re.escape(token) + r"\b", lowered)
             for token in ("exchange traded fund", "etf", "etn", "fund", "trust")):
        asset_type, confidence, evidence = "fund_or_trust", "high", "fund/trust name marker"
    elif spac_name:
        asset_type, confidence, evidence = "spac_common", "high", "SPAC/acquisition name marker"
    elif ticker.endswith("W"):
        asset_type, confidence, evidence = "ambiguous_warrant_suffix", "low", "unconfirmed W suffix"
    elif ticker.endswith("U"):
        asset_type, confidence, evidence = "ambiguous_unit_suffix", "low", "unconfirmed U suffix"
    elif ticker.endswith("R"):
        asset_type, confidence, evidence = "ambiguous_right_suffix", "low", "unconfirmed R suffix"

    common_equity_eligible = asset_type in {
        "common_equity", "ambiguous_warrant_suffix", "ambiguous_unit_suffix", "ambiguous_right_suffix"
    }
    return {
        "asset_classification_schema": ASSET_SCHEMA,
        "ticker": ticker,
        "security_name": name or None,
        "asset_type": asset_type,
        "classification_confidence": confidence,
        "classification_evidence": evidence,
        "common_equity_eligible": common_equity_eligible,
        "requires_asset_master_validation": asset_type in {
            "ambiguous_warrant_suffix", "ambiguous_unit_suffix", "ambiguous_right_suffix"
        },
    }


def slugify(value: Any) -> str:
    text = str(value or "").strip().lower()
    text = re.sub(r"[^a-z0-9]+", "_", text).strip("_")
    return text or "unknown"


def classify_symbol(row: dict[str, Any]) -> dict[str, Any]:
    from research_registry.core import classify as research_classify
    ticker = str(row.get("ticker") or row.get("symbol") or "").upper().strip()
    raw_sector = str(row.get("sector") or row.get("sector_label") or "").strip()
    raw_industry = str(row.get("industry") or row.get("industry_label") or "").strip()
    registry = registry_classification(ticker)
    registry_parts = str(registry["node_id"]).split(".") if registry else []
    provider_sector = SECTOR_ALIASES.get(raw_sector.lower(), "unknown")
    # A provisional registry node can rest on a single source (the directory
    # crosswalk). It only overrides the provider's sector once it is corroborated
    # (approved_provisional); otherwise it may add depth inside the same sector or
    # fill a missing provider sector, and a disagreement stays visible below.
    registry_trusted = bool(registry_parts) and (
        registry["status"] == "approved_provisional" or provider_sector in {"unknown", registry_parts[0]})
    registry_disagrees = bool(registry_parts) and not registry_trusted

    if registry_trusted and registry_parts[0] in SECTORS and registry_parts[0] != "unknown":
        # Registry node, e.g. technology.software.applications.enterprise.
        # Industry is the node down to the third level (industry depth).
        sector_id = registry_parts[0]
        industry_id = ".".join(registry_parts[:3]) if len(registry_parts) >= 2 else f"{sector_id}.unknown"
        status = "classified" if len(registry_parts) >= 3 else "sector_only"
        confidence = "high" if registry["status"] == "approved_provisional" and len(registry_parts) >= 3 else "medium"
        source = "babis_registry"
        industry_label = raw_industry or registry_parts[-1].replace("_", " ").title()
    else:
        sector_id = provider_sector
        industry_label = raw_industry or "Unknown"
        source = "provider_metadata" if raw_sector else "missing"
        if sector_id == "unknown":
            industry_id = "unknown.unknown"
            status = "unknown"
            confidence = "low"
        elif raw_industry:
            industry_id = f"{sector_id}.{slugify(raw_industry)}"
            status = "classified"
            confidence = "high"
        else:
            industry_id = f"{sector_id}.unknown"
            status = "sector_only"
            confidence = "medium"
    sector_label = SECTORS[sector_id]

    return {
        "classification_schema": SCHEMA,
        "sector_taxonomy_version": TAXONOMY_VERSION,
        "ticker": ticker,
        "sector_id": sector_id,
        "sector_label": sector_label,
        "industry_id": industry_id,
        "industry_label": industry_label,
        "raw_sector": raw_sector or None,
        "raw_industry": raw_industry or None,
        "classification_source": source,
        "classification_confidence": confidence,
        "classification_status": status,
        "index_analysis_eligible": status == "classified",
        "registry_node_id": registry["node_id"] if registry else None,
        "registry_status": registry["status"] if registry else None,
        "registry_taxonomy_version": registry["taxonomy_version"] if registry else None,
        "registry_sector_disagreement": registry_disagrees,
        "research_classification": research_classify(row),
    }


def classification_coverage(rows: list[dict[str, Any]]) -> dict[str, Any]:
    unique: dict[str, dict[str, Any]] = {}
    for row in rows:
        classification = classify_symbol(row)
        ticker = classification["ticker"]
        if ticker:
            unique[ticker] = classification
    total = len(unique)
    classified = sum(1 for row in unique.values() if row["classification_status"] == "classified")
    sector_known = sum(1 for row in unique.values() if row["sector_id"] != "unknown")
    return {
        "symbol_count": total,
        "sector_known_count": sector_known,
        "fully_classified_count": classified,
        "sector_coverage_pct": round((sector_known / total) * 100.0, 2) if total else 0.0,
        "full_classification_coverage_pct": round((classified / total) * 100.0, 2) if total else 0.0,
    }
