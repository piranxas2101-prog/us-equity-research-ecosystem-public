"""Build a read-only, self-contained dashboard from one immutable research run.

No provider, LLM, broker, server or background process is started. The generated
HTML contains only selected research fields and works directly from disk.
"""
from __future__ import annotations

import argparse
import csv
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
from typing import Any


ROOT = Path(__file__).resolve().parent
TEMPLATE = ROOT / "dashboard_assets" / "symbol_research_dashboard.html"
ARTIFACTS = {
    "babis": "babis_shortlist_latest.json",
    "giannis": "giannis_rule_based_latest.json",
    "petros": "petros_review_latest.json",
    "deep": "company_deep_research_latest.json",
    "theses": "fundamental_research_packet_latest.json",
    "control": "fundamental_control_plane_batch_latest.json",
    "yankos": "yankos_strategy_latest.json",
    "periklis": "periklis_strategy_latest.json",
    "sokratis": "sokratis_strategy_teacher_latest.json",
    "audit": "research_only_audit_latest.json",
    "routes": "all_listed_universe_routes_latest.json",
    "instruments": "all_listed_instrument_observations_latest.json",
}
# Universe routes are prepared immediately before a run and are identified by
# the immutable universe snapshot, not by a run ID.  Every other dashboard
# artifact is produced inside the run and must carry the exact run ID.
SNAPSHOT_SCOPED_ARTIFACTS = {"routes"}


def _text(value: Any) -> str | None:
    if value is None or value == "":
        return None
    return str(value)


def _items(value: Any, *, limit: int = 12) -> list[str]:
    if not isinstance(value, list):
        return []
    result = []
    for item in value[:limit]:
        if isinstance(item, (dict, list)):
            result.append(json.dumps(item, ensure_ascii=False, sort_keys=True))
        elif item is not None:
            result.append(str(item))
    return result


def _fields(row: dict[str, Any] | None, names: tuple[str, ...]) -> dict[str, Any]:
    return {name: row[name] for name in names if isinstance(row, dict) and row.get(name) is not None}


def _by_ticker(payload: dict[str, Any], key: str) -> dict[str, dict[str, Any]]:
    rows = payload.get(key) or []
    return {
        str(row["ticker"]).upper(): row for row in rows
        if isinstance(row, dict) and row.get("ticker")
    }


def _group_by_ticker(payload: dict[str, Any], key: str) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in payload.get(key) or []:
        if isinstance(row, dict) and row.get("ticker"):
            grouped.setdefault(str(row["ticker"]).upper(), []).append(row)
    return grouped


def _safe_macro_context(raw: Any) -> dict[str, Any] | None:
    """Keep useful observations while excluding provider error text and credentials."""
    if not isinstance(raw, dict):
        return None
    series = raw.get("series") if isinstance(raw.get("series"), dict) else {}
    safe_series = {
        str(series_id): _fields(observation, ("date", "value", "age_days", "fresh", "max_age_days"))
        for series_id, observation in series.items()
        if isinstance(observation, dict) and str(series_id).replace("_", "").isalnum()
    }
    regime = raw.get("regime_assessment") if isinstance(raw.get("regime_assessment"), dict) else None
    safe_regime = _fields(regime, ("regime", "confidence", "score", "available_signals", "signals", "sector_impacts"))
    return {"provider": _text(raw.get("provider")), "as_of": _text(raw.get("as_of")),
            "series": safe_series, "regime_assessment": safe_regime or None}


def latest_manifest(project_root: Path) -> Path:
    paths = list((project_root / "output" / "runs").glob("*/manifest.json"))
    if not paths:
        raise FileNotFoundError("No run manifest found under output/runs.")
    return max(paths, key=lambda path: path.stat().st_mtime)


def load_symbol_catalog(catalog_dir: Path | None) -> tuple[list[dict[str, str]], dict[str, Any] | None]:
    """Read the dated full-market listing export without treating it as run evidence."""
    if catalog_dir is None:
        return [], None
    csv_path = catalog_dir / "all_us_listed_symbols.csv"
    coverage_path = catalog_dir / "coverage.json"
    if not csv_path.exists() and not coverage_path.exists():
        return [], None
    if not csv_path.is_file() or not coverage_path.is_file():
        raise ValueError("Incomplete full-market catalog export.")
    coverage = json.loads(coverage_path.read_text(encoding="utf-8-sig"))
    with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    catalog = [
        {"ticker": str(row.get("ticker") or "").strip().upper(),
         "name": str(row.get("name") or "").strip(),
         "venue": str(row.get("venue") or "").strip(),
         "instrument_type": str(row.get("instrument_type") or "").strip(),
         "sector": str(row.get("sector") or "").strip()}
        for row in rows
    ]
    if (len(catalog) != int(coverage.get("listings", -1))
            or len({row["ticker"] for row in catalog}) != int(coverage.get("unique_symbols", -1))
            or any(not row["ticker"] or not row["name"] for row in catalog)):
        raise ValueError("Full-market catalog counts or identities do not match its coverage export.")
    source_times = coverage.get("source_file_times") or {}
    source_dates = []
    for raw_time in source_times.values() if isinstance(source_times, dict) else []:
        try:
            source_dates.append(datetime.strptime(str(raw_time).split(": ")[-1], "%m%d%Y%H:%M").date().isoformat())
        except ValueError:
            pass
    return catalog, {"snapshot_id": coverage.get("snapshot_id"),
                     "source_date": max(source_dates) if source_dates else None,
                     "listings": len(catalog)}


def load_run(manifest_path: Path) -> tuple[dict[str, Any], dict[str, dict[str, Any]], list[str]]:
    manifest_path = manifest_path.resolve()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
    if manifest.get("schema") != "ecosystem.run_manifest.v1":
        raise ValueError("Unsupported run manifest schema.")
    run_id = str(manifest.get("run_id") or "")
    if not run_id or manifest_path.parent.name != run_id:
        raise ValueError("Manifest run ID does not match its directory.")
    records = {
        Path(str(record.get("source") or "").replace("\\", "/")).name: record
        for record in manifest.get("artifacts") or []
        if isinstance(record, dict) and record.get("status") == "captured"
    }
    loaded: dict[str, dict[str, Any]] = {}
    verified = []
    artifact_dir = manifest_path.parent / "artifacts"
    for role, name in ARTIFACTS.items():
        record = records.get(name)
        if not record:
            continue
        snapshot = artifact_dir / Path(str(record.get("snapshot") or "").replace("\\", "/")).name
        if snapshot.name != name or not snapshot.is_file():
            raise ValueError(f"Missing or misplaced snapshot: {name}")
        actual_hash = hashlib.sha256(snapshot.read_bytes()).hexdigest()
        if actual_hash != record.get("sha256"):
            raise ValueError(f"Snapshot hash mismatch: {name}")
        payload = json.loads(snapshot.read_text(encoding="utf-8-sig"))
        if not isinstance(payload, dict):
            raise ValueError(f"Snapshot root is not an object: {name}")
        if role not in SNAPSHOT_SCOPED_ARTIFACTS and payload.get("run_id") != run_id:
            raise ValueError(f"Cross-run snapshot rejected: {name}")
        loaded[role] = payload
        verified.append(name)
    if "babis" not in loaded:
        raise ValueError("The run has no captured Babis shortlist.")
    expected_snapshot = str(loaded["babis"].get("universe_snapshot_id") or "").strip()
    for role in SNAPSHOT_SCOPED_ARTIFACTS:
        payload = loaded.get(role)
        if payload is None:
            continue
        name = ARTIFACTS[role]
        artifact_run_id = str(payload.get("run_id") or "").strip()
        artifact_snapshot = str(payload.get("snapshot_id") or "").strip()
        if artifact_run_id and artifact_run_id != run_id:
            raise ValueError(f"Cross-run snapshot rejected: {name}")
        if not expected_snapshot or artifact_snapshot != expected_snapshot:
            raise ValueError(f"Cross-universe snapshot rejected: {name}")
    return manifest, loaded, verified


class LocalPacketReader:
    """Read exact packet IDs from the local control plane without creating it."""

    def __init__(self, path: Path | None, run_id: str):
        self.connection: sqlite3.Connection | None = None
        self.run_id = run_id
        if path and path.is_file():
            self.connection = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
            self.connection.execute("PRAGMA query_only=ON")

    def close(self) -> None:
        if self.connection is not None:
            self.connection.close()

    def payload(self, packet_id: Any, *, kind: str, subject_id: str, ticker: str) -> dict[str, Any] | None:
        if not self.connection or not packet_id:
            return None
        try:
            row = self.connection.execute(
                "SELECT kind,subject_id,run_id,payload_json FROM ecosystem_evidence_packet_store WHERE packet_id=?",
                (str(packet_id),),
            ).fetchone()
        except sqlite3.Error:
            return None
        if not row or (row[0], row[1], row[2]) != (kind, subject_id, self.run_id):
            return None
        try:
            payload = json.loads(row[3]).get("payload")
        except (ValueError, TypeError):
            return None
        if not isinstance(payload, dict):
            return None
        if str(payload.get("ticker") or payload.get("symbol") or "").upper() != ticker:
            return None
        return payload


def _sources(rows: Any, *, limit: int = 10) -> list[dict[str, Any]]:
    if not isinstance(rows, list):
        return []
    return [_fields(row, ("provider", "field", "concept", "accession", "filed", "period_end", "as_of", "source_id"))
            for row in rows[:limit] if isinstance(row, dict)]


def build_symbol_records(artifacts: dict[str, dict[str, Any]], reader: LocalPacketReader) -> list[dict[str, Any]]:
    babis = _by_ticker(artifacts["babis"], "shortlist")
    giannis = _by_ticker(artifacts.get("giannis", {}), "results")
    petros = _by_ticker(artifacts.get("petros", {}), "results")
    deep = _by_ticker(artifacts.get("deep", {}), "results")
    theses = _by_ticker(artifacts.get("theses", {}), "theses")
    control = _by_ticker(artifacts.get("control", {}), "results")
    yankos = _group_by_ticker(artifacts.get("yankos", {}), "results")
    periklis = _group_by_ticker(artifacts.get("periklis", {}), "results")
    sokratis_links = artifacts.get("sokratis", {}).get("decision_links") or []
    records = []
    for ticker, b in babis.items():
        g, p, thesis = giannis.get(ticker, {}), petros.get(ticker, {}), theses.get(ticker, {})
        deep_row = deep.get(ticker, {})
        c = control.get(ticker, {})
        subject = str(c.get("canonical_subject_id") or "")
        sector_ref = (c.get("sector_context_control") or {}).get("control_packet_id")
        analysis_ref = (c.get("fundamental_analysis_control") or {}).get("control_packet_id")
        sector = reader.payload(sector_ref, kind="sector_context", subject_id=subject, ticker=ticker)
        analysis = reader.payload(analysis_ref, kind="fundamental_analysis", subject_id=subject, ticker=ticker)
        financial = c.get("financial") or {}
        def trader(row: dict[str, Any]) -> dict[str, Any] | None:
            if not row:
                return None
            plan = row.get("paper_trade_plan") or {}
            return {
                **_fields(row, ("trade_direction", "signal_verdict", "fundamental_gate_status", "playbook_name", "technical_timing", "confidence")),
                "rationale": _items(row.get("rationale"), limit=8),
                "plan_rationale": _items(plan.get("initial_rationale"), limit=6),
                "execution_authority": bool(plan.get("execution_authority")),
            }
        record = {
            "ticker": ticker,
            "company": b.get("company") or ticker,
            "sector": b.get("sector"),
            "industry": b.get("industry"),
            "babis": {
                **_fields(b, ("rank", "classification_status", "classification_confidence", "fundamental_attention_score", "fundamental_coverage", "instrument_type", "main_catalyst", "fresh_primary_data")),
                "facts": _fields(b, ("revenue_ttm", "net_income_ttm", "cash", "debt", "cash_burn_proxy_fcf", "cash_burn_proxy_ocf")),
                "unknown_fields": _items(b.get("unknown_fields")),
            },
            "sector_context": {
                "status": c.get("sector_context_status") or ("not_in_run" if not c else "unavailable"),
                "packet_available": sector is not None,
                **_fields(sector, ("business_model", "revenue_drivers", "cost_drivers", "competitive_position", "sector_kpis", "company_specific_risks", "industry_risks")),
                "unknown_fields": _items((sector or {}).get("unknown_fields")),
                "sources": _sources((sector or {}).get("source_references")),
            },
            "giannis": {
                **_fields(g, ("giannis_tier", "score_final", "net_cash", "fundamental_coverage", "risk_level")),
                "control_status": c.get("status") or "not_in_run",
                "financial_blockers": _items(financial.get("blockers")),
                "sec_analysis_available": analysis is not None,
                "sec_analysis": _fields(analysis, ("confidence", "periods", "currency", "growth_analysis", "margin_analysis", "balance_sheet_analysis", "earnings_quality", "cash_flow_analysis", "capital_allocation", "dilution_analysis")),
                "sec_sources": _sources((analysis or {}).get("source_references")),
                "unknown_fields": _items(g.get("unknown_fields")),
            },
            "petros": {
                **_fields(p, ("petros_verdict", "petros_score", "priced_in_status", "priced_in_confidence")),
                "deterministic_valuation": p.get("deterministic_valuation"),
                "valuation_range": p.get("valuation_range"),
                **_fields(thesis, ("company_thesis_status", "security_thesis_readiness", "readiness_status", "primary_blocker")),
                "rationale": _items(thesis.get("rationale") or p.get("rationale")),
                "prove_criteria": _items(thesis.get("prove_criteria")),
                "kill_criteria": _items(thesis.get("kill_criteria")),
                "next_diligence": _items(thesis.get("next_diligence")),
                "decision_blockers": _items(thesis.get("decision_blockers")),
                "fundamental_summary": _fields(thesis.get("fundamental_summary"), ("valuation_status", "priced_in_status", "fundamental_coverage", "giannis_tier")),
            },
            "deep_research": {
                **_fields(deep_row, ("research_status", "pending_research")),
                "modules": deep_row.get("modules") if isinstance(deep_row.get("modules"), dict) else {},
            },
            "other_agents": {
                "yankos": [trader(row) for row in yankos.get(ticker, [])],
                "periklis": [trader(row) for row in periklis.get(ticker, [])],
                "sokratis_link_count": sum(1 for link in sokratis_links if isinstance(link, dict) and str(link.get("ticker") or "").upper() == ticker),
            },
        }
        records.append(record)
    return sorted(records, key=lambda row: (row["babis"].get("rank") or 999999, row["ticker"]))


def build_dashboard(*, manifest_path: Path, output_path: Path, flow_db: Path | None = None,
                    catalog_dir: Path | None = None) -> dict[str, Any]:
    manifest, artifacts, verified = load_run(manifest_path)
    reader = LocalPacketReader(flow_db, str(manifest["run_id"]))
    try:
        symbols = build_symbol_records(artifacts, reader)
        has_control_db = reader.connection is not None
    finally:
        reader.close()
    catalog, catalog_metadata = load_symbol_catalog(catalog_dir)
    route_rows = _by_ticker(artifacts.get("routes", {}), "routes")
    observation_rows = _by_ticker(artifacts.get("instruments", {}), "observations")
    for listing in catalog:
        route = route_rows.get(listing["ticker"], {})
        observation = observation_rows.get(listing["ticker"], {})
        listing.update({
            "research_route": route.get("research_route"),
            "company_fundamental_eligible": route.get("company_fundamental_eligible"),
            "workflow_status": observation.get("workflow_status"),
            "observed_close": observation.get("close"),
            "market_data_provider": observation.get("market_data_provider"),
        })
    catalog_tickers = {row["ticker"] for row in catalog}
    catalog.extend({"ticker": row["ticker"], "name": row["company"], "venue": "",
                    "instrument_type": "", "sector": row.get("sector") or ""}
                   for row in symbols if row["ticker"] not in catalog_tickers)
    if catalog_metadata is not None:
        catalog_metadata["run_only_symbols"] = len(catalog) - catalog_metadata["listings"]
    babis = artifacts["babis"]
    routing = babis.get("universe_routing") or {}
    run_coverage = {
        "eligible_universe": babis.get("universe_total"),
        "processed_symbols": (babis.get("runtime_summary") or {}).get("processed_symbols"),
        "shortlisted_symbols": len(symbols),
        "max_price": (babis.get("filters") or {}).get("max_price"),
    }
    optional_coverage = {
        "company_fundamental_symbols": routing.get("company_fundamental_symbols"),
        "instrument_observation_symbols": routing.get("instrument_observation_symbols"),
        "instrument_observations_completed": routing.get("instrument_observations_completed"),
    }
    run_coverage.update({key: value for key, value in optional_coverage.items() if value is not None})
    data = {
        "schema": "ecosystem.symbol_research_dashboard.v1",
        "run_id": manifest["run_id"],
        "captured_at": manifest.get("captured_at"),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "verified_artifacts": verified,
        "control_db_available": has_control_db,
        "audit_status": artifacts.get("audit", {}).get("status"),
        "macro_context": _safe_macro_context(artifacts["babis"].get("macro_context")),
        "catalog": catalog,
        "catalog_metadata": catalog_metadata,
        "run_coverage": run_coverage,
        "symbols": symbols,
    }
    serialized = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    serialized = (serialized.replace("&", "\\u0026").replace("<", "\\u003c")
                  .replace(">", "\\u003e").replace("\u2028", "\\u2028").replace("\u2029", "\\u2029"))
    html = TEMPLATE.read_text(encoding="utf-8").replace("__DASHBOARD_DATA__", serialized)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(html, encoding="utf-8")
    return {"run_id": manifest["run_id"], "symbols": len(symbols), "catalog_symbols": len(catalog),
            "verified_artifacts": len(verified),
            "control_db_available": has_control_db, "output": str(output_path.resolve())}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=ROOT)
    parser.add_argument("--manifest", type=Path, help="Exact immutable run manifest; otherwise use the latest local run")
    parser.add_argument("--flow-db", type=Path, help="Optional local SQLite control-plane DB, opened read-only")
    parser.add_argument("--output", type=Path, help="Output HTML path")
    args = parser.parse_args(argv)
    project_root = args.project_root.resolve()
    manifest_path = args.manifest or latest_manifest(project_root)
    output_path = args.output or project_root / "output" / "dashboards" / f"symbol_research_{manifest_path.parent.name}.html"
    flow_db = args.flow_db if args.flow_db is not None else project_root / "data" / "market_intel.db"
    result = build_dashboard(manifest_path=manifest_path, output_path=output_path, flow_db=flow_db,
                             catalog_dir=project_root / "data" / "research_universe_exports")
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
