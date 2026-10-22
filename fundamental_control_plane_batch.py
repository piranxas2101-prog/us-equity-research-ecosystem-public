"""Populate the production research control plane for a bounded shortlist.

The batch is deterministic except for the rate-limited SEC collection step. It
publishes Company Profile -> Sector Context / Financial Evidence -> Giannis Fundamental Analysis
with one canonical SEC issuer identity and never calls an LLM or broker.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sqlite3
from typing import Any
import hashlib

from company_profile_agent import build_profile, publish_profile_to_control_plane
from companyfacts_cache import CompanyFactsCache
from control_plane_store import DEFAULT_CONTROL_PLANE_DB
from giannis_fundamental_agent import (
    analyze_financial_packet,
    load_financial_packet_from_control_plane,
    publish_fundamental_analysis_control_plane,
)
from provider_rate_limiter import SharedProviderRateLimiter
from sector_context_handoff import (
    build_from_profile, publish_sector_context, record_unroutable_sector_context,
)
from research_information_flow import ResearchPacketRegistry
from sector_research_agent import route_node
from research_registry.classification_reassessment import load_sec
from research_registry.core import DEFAULT_DB as DEFAULT_REGISTRY_DB
import filing_business_description
import sector_evidence_sec
from research_workflow_orchestrator import collect_sec_financials, load_env


ROOT = Path(__file__).resolve().parent
DEFAULT_SEC_MAP = ROOT / "cache" / "sec_company_tickers.json"


def _load(path: Path) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def _batch_signature(rows: list[dict[str, Any]], run_id: str, as_of: str) -> str:
    # A run interrupted across UTC midnight must resume with its original
    # point-in-time date.  Run identity plus the exact ordered input is the
    # stable resume key; ``as_of`` remains in the signature API for clarity.
    stable = json.dumps({"run_id": run_id, "rows": rows}, sort_keys=True,
                        separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(stable.encode("utf-8")).hexdigest()


def _rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    values = payload.get("shortlist") or payload.get("results") or []
    return [row for row in values if isinstance(row, dict)]


def _bounded_rows(payload: dict[str, Any], limit: int) -> list[dict[str, Any]]:
    rows = _rows(payload)
    return rows if limit <= 0 else rows[:limit]


def _ticker_to_cik(path: Path) -> dict[str, int]:
    payload = _load(path)
    values = payload.values() if isinstance(payload, dict) else payload
    result: dict[str, int] = {}
    for row in values:
        if not isinstance(row, dict):
            continue
        ticker = str(row.get("ticker") or "").upper().strip()
        raw = row.get("cik_str", row.get("cik"))
        try:
            if ticker and raw is not None:
                result[ticker] = int(raw)
        except (TypeError, ValueError):
            continue
    return result


def _stored_profile(db_path: Path, ticker: str) -> dict[str, Any] | None:
    db = sqlite3.connect(db_path)
    try:
        row = db.execute(
            "SELECT profile_json FROM company_profile_store WHERE ticker=? ORDER BY created_at DESC LIMIT 1",
            (ticker,),
        ).fetchone()
    finally:
        db.close()
    return json.loads(row[0]) if row else None


def _current_classification(db_path: Path) -> dict[str, list[dict[str, Any]]]:
    """Ticker -> the current effective classification rows of the newest universe snapshot."""
    if not Path(db_path).is_file():
        return {}
    db = sqlite3.connect(db_path)
    try:
        snapshot = db.execute(
            "SELECT snapshot_id FROM universe_snapshot ORDER BY retrieved_at DESC, rowid DESC LIMIT 1").fetchone()
        if not snapshot:
            return {}
        rows = db.execute("SELECT ticker, venue_code, node_id, status FROM effective_listed_symbol WHERE snapshot_id=?",
                          (snapshot[0],)).fetchall()
    except sqlite3.OperationalError:
        return {}
    finally:
        db.close()
    current: dict[str, list[dict[str, Any]]] = {}
    for ticker, venue_code, node_id, status in rows:
        current.setdefault(str(ticker).upper(), []).append(
            {"snapshot_id": snapshot[0], "venue_code": venue_code, "node_id": node_id, "status": status})
    return current


def with_current_classification(profile: dict[str, Any], current: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    """Route by today's approved classification, not by the node frozen in the stored profile.

    The stored profiles were built once (2026-09-17) and kept their node: on 2026-10-06, 158 of 957 routed companies
    carried an outdated node (73 in another sector: NFLX as retail, LRCX as machinery) and 108 were unroutable although
    95 of them have an approved node now. A company with no current node stays unknown (unroutable), never the old one.
    """
    rows = current.get(str(profile.get("ticker") or "").upper())
    if not rows:
        return profile
    venue_rows = [row for row in rows if row["venue_code"] == profile.get("venue_code")]
    if len(venue_rows) != 1 and len(rows) != 1:
        return profile  # ambiguous listing: keep the stored profile as it was
    row = (venue_rows or rows)[0]
    return {**profile, "taxonomy_node": row["node_id"], "classification_status": row["status"],
            "classification_snapshot_id": row["snapshot_id"],
            "stored_taxonomy_node": profile.get("taxonomy_node"), "taxonomy_node_source": "current_classification"}


def _framework_kpis(node: str) -> list[str]:
    try:
        return list(route_node(node)["required_sector_kpis"])
    except ValueError:
        return []


def _sec_submissions() -> dict[str, dict[str, Any]]:
    """SEC submission summaries (SIC code) per ticker for the peer groups; empty when unavailable."""
    try:
        return load_sec(ROOT)
    except Exception:  # noqa: BLE001 - peer groups are optional evidence, never a reason to stop the batch
        return {}


def _fallback_profile(row: dict[str, Any], run_id: str) -> dict[str, Any]:
    ticker = str(row.get("ticker") or row.get("symbol") or "").upper().strip()
    listing = {
        "ticker": ticker,
        "venue_code": str(row.get("venue_code") or row.get("exchange") or "UNKNOWN"),
        "name": str(row.get("name") or ticker),
        "instrument_type": str(row.get("instrument_type") or "equity_candidate"),
        "node_id": row.get("node_id") or row.get("taxonomy_node"),
        "payload": {"raw_profile": {}},
    }
    return build_profile(listing, None, "shortlist-fallback", run_id)


def run_batch(
    *, babis_path: Path, run_id: str, limit: int, flow_db: Path,
    registry_db: Path, sec_map_path: Path, output_path: Path,
    checkpoint_path: Path | None = None, checkpoint_every: int = 25,
    companyfacts_cache: CompanyFactsCache | None = None,
    business_descriptions: "filing_business_description.BusinessDescriptions | None" = None,
) -> dict[str, Any]:
    rows = _bounded_rows(_load(babis_path), limit)
    cik_map = _ticker_to_cik(sec_map_path)
    current_classification = _current_classification(registry_db)
    sec_submissions = _sec_submissions()
    shortlist_tickers = {str(row.get("ticker") or row.get("symbol") or "").upper() for row in rows}
    companyfacts_dir = companyfacts_cache.directory if companyfacts_cache else sector_evidence_sec.DEFAULT_COMPANYFACTS_DIR
    packet_registry = ResearchPacketRegistry(flow_db)
    limiter = SharedProviderRateLimiter()
    as_of = datetime.now(timezone.utc).date().isoformat()
    checkpoint_path = checkpoint_path or output_path.with_suffix(".checkpoint.json")
    signature = _batch_signature(rows, run_id, as_of)
    results: list[dict[str, Any]] = []
    counts = {"completed": 0, "reused": 0, "blocked": 0, "identity_gap": 0, "failed": 0,
              "sector_context_published": 0, "sector_context_unroutable": 0,
              "companyfacts_from_store": 0, "sector_kpis_from_sec": 0, "sector_peer_groups": 0,
              "business_descriptions": 0}
    start_index = 0
    if checkpoint_path.is_file():
        saved = _load(checkpoint_path)
        if (saved.get("status") == "running" and saved.get("signature") == signature
                and isinstance(saved.get("results"), list)):
            as_of = str(saved.get("as_of") or as_of)
            results = list(saved["results"])
            saved_counts = saved.get("counts")
            if isinstance(saved_counts, dict):
                counts.update({key: int(saved_counts.get(key, value)) for key, value in counts.items()})
            start_index = min(max(0, int(saved.get("processed_count", len(results)))), len(rows))
    checkpoint_every = max(1, int(checkpoint_every))
    for row_index in range(start_index, len(rows)):
        row = rows[row_index]
        ticker = str(row.get("ticker") or row.get("symbol") or "").upper().strip()
        cik = cik_map.get(ticker)
        if not cik:
            counts["identity_gap"] += 1
            results.append({"ticker": ticker, "status": "identity_gap", "reason": "missing_sec_cik"})
            continue
        subject_id = f"sec:cik:{cik:010d}"
        try:
            profile = _stored_profile(registry_db, ticker) or _fallback_profile(row, run_id)
            profile = {**with_current_classification(profile, current_classification), "run_id": run_id}
            profile_control = publish_profile_to_control_plane(
                profile, flow_db, canonical_subject_id=subject_id
            )
            sec_evidence = sector_evidence_sec.build_evidence(
                ticker=ticker, cik=cik, leaf_node=str(profile.get("taxonomy_node") or ""),
                sec_by_ticker=sec_submissions, preferred=shortlist_tickers, companyfacts_dir=companyfacts_dir)
            framework_kpis = _framework_kpis(str(profile.get("taxonomy_node") or ""))
            description = business_descriptions.get(cik) if business_descriptions is not None else None
            sec_evidence = filing_business_description.merge_evidence(
                sec_evidence, filing_business_description.as_evidence(description, framework_kpis),
                sector_evidence_sec.catalyst_evidence(row))
            sector_packet = build_from_profile(profile, run_id, sec_evidence)
            sector_control = None
            sector_gap = None
            if sector_packet is None:
                counts["sector_context_unroutable"] += 1
                sector_gap = record_unroutable_sector_context(
                    profile=profile, control_plane_db=flow_db,
                    subject_id=subject_id,
                    profile_control_packet_id=profile_control["control_packet_id"],
                )
            else:
                sector_control = publish_sector_context(
                    packet=sector_packet, control_plane_db=flow_db,
                    subject_id=subject_id,
                    profile_control_packet_id=profile_control["control_packet_id"],
                )
                counts["sector_context_published"] += 1
                counts["sector_kpis_from_sec"] += int(bool(sector_packet.get("sector_kpis")))
                counts["sector_peer_groups"] += int(bool(sector_packet.get("peer_group")))
                counts["business_descriptions"] += int(any(
                    source.get("provider") == "sec_10k" for source in sector_packet.get("source_references") or []))
            financial = collect_sec_financials(
                symbol=ticker, cik=cik, as_of=as_of, run_id=run_id,
                registry=packet_registry, limiter=limiter,
                upstream_control_packet_ids=(profile_control["control_packet_id"],),
                companyfacts_cache=companyfacts_cache,
            )
            financial_control = dict(financial.get("control_plane") or {})
            analysis_control = None
            if financial.get("ready_for_giannis") and financial_control.get("control_packet_id"):
                packet, reference = load_financial_packet_from_control_plane(
                    control_plane_db=flow_db,
                    control_packet_id=financial_control["control_packet_id"],
                )
                sector_id = (sector_control or {}).get("control_packet_id")
                analysis = analyze_financial_packet(
                    packet, run_id=run_id, sector_context_packet_id=sector_id
                )
                analysis_control = publish_fundamental_analysis_control_plane(
                    control_plane_db=flow_db, analysis=analysis,
                    input_control_packet_id=financial_control["control_packet_id"],
                    subject_id=reference["subject_id"],
                    sector_control_packet_id=sector_id,
                )
            status = "reused" if "reused" in str(financial.get("status")) else (
                "completed" if analysis_control else "blocked"
            )
            counts[status] += 1
            if financial.get("companyfacts_source") == "cached_companyfacts":
                counts["companyfacts_from_store"] += 1
            results.append({
                "ticker": ticker, "canonical_subject_id": subject_id, "status": status,
                "profile_control": profile_control, "financial": financial,
                "sector_context_control": sector_control,
                "sector_context_gap": sector_gap,
                "sector_context_status": sector_packet["status"] if sector_packet else "unroutable",
                "sector_evidence_coverage": (sector_packet or {}).get("evidence_coverage"),
                "sector_evidence_tier": (sector_packet or {}).get("evidence_tier"),
                "fundamental_analysis_control": analysis_control,
            })
        except Exception as exc:
            counts["failed"] += 1
            results.append({
                "ticker": ticker, "canonical_subject_id": subject_id, "status": "failed",
                "error_type": type(exc).__name__, "error": str(exc),
            })
        processed_count = row_index + 1
        if processed_count % checkpoint_every == 0:
            _atomic_json(checkpoint_path, {
                "schema": "ecosystem.fundamental_control_plane_batch_checkpoint.v1",
                "status": "running", "signature": signature, "run_id": run_id,
                "as_of": as_of, "processed_count": processed_count,
                "requested": len(rows), "counts": counts, "results": results,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            })
    result = {
        "schema": "ecosystem.fundamental_control_plane_batch.v1",
        "run_id": run_id, "as_of": as_of, "requested": len(rows),
        "counts": counts, "llm_calls": 0, "broker_calls": 0, "results": results,
        "finished_at": datetime.now(timezone.utc).isoformat(),
    }
    _atomic_json(output_path, result)
    _atomic_json(checkpoint_path, {
        "schema": "ecosystem.fundamental_control_plane_batch_checkpoint.v1",
        "status": "complete", "signature": signature, "run_id": run_id,
        "as_of": as_of, "processed_count": len(rows), "requested": len(rows),
        "counts": counts, "output_path": str(output_path),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    })
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    parser.add_argument("--babis", type=Path, default=Path("output/babis_shortlist_latest.json"))
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--limit", type=int, default=5)
    parser.add_argument("--flow-db", type=Path, default=DEFAULT_CONTROL_PLANE_DB)
    parser.add_argument("--registry-db", type=Path, default=DEFAULT_REGISTRY_DB)
    parser.add_argument("--sec-map", type=Path, default=DEFAULT_SEC_MAP)
    parser.add_argument("--output", type=Path, default=Path("output/fundamental_control_plane_batch_latest.json"))
    parser.add_argument("--checkpoint", type=Path)
    parser.add_argument("--checkpoint-every", type=int, default=25)
    parser.add_argument("--config", type=Path, default=ROOT / "scanner_config.json")
    args = parser.parse_args(argv)
    load_env(args.env_file)
    config = _load(args.config) if args.config.is_file() else {}
    companyfacts_cache = CompanyFactsCache.from_config(config, filing_db=args.registry_db)
    business_descriptions = filing_business_description.BusinessDescriptions(
        user_agent=os.getenv("SEC_USER_AGENT", "").strip(), limiter=SharedProviderRateLimiter(),
        max_new_per_run=filing_business_description.config_limit(config), filing_db=args.registry_db)
    result = run_batch(
        babis_path=args.babis, run_id=args.run_id, limit=args.limit,
        flow_db=args.flow_db, registry_db=args.registry_db,
        sec_map_path=args.sec_map, output_path=args.output,
        checkpoint_path=args.checkpoint, checkpoint_every=args.checkpoint_every,
        companyfacts_cache=companyfacts_cache,
        business_descriptions=business_descriptions,
    )
    print(json.dumps({"run_id": result["run_id"], "counts": result["counts"]}, ensure_ascii=False))
    return 1 if result["counts"]["failed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
