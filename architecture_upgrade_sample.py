"""Run one real, research-only architecture-upgrade sample after the daily pipeline.

The sample uses AAPL as a stable SEC/XBRL integration subject, publishes every
result to the shared control plane, processes the real frozen research report as
document evidence, and emits asset health/lineage diagnostics.  No broker module
is imported or called.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any

from company_profile_agent import build_profile, publish_profile_to_control_plane
from control_plane_store import DEFAULT_CONTROL_PLANE_DB, connect
from document_evidence_adapter import publish_document_evidence
from edgartools_evidence_adapter import publish_filing_catalog
from giannis_fundamental_agent import (
    analyze_financial_packet,
    load_financial_packet_from_control_plane,
    publish_fundamental_analysis_control_plane,
)
from provider_rate_limiter import SharedProviderRateLimiter
from research_asset_graph import graph_status, packet_lineage
from research_information_flow import ResearchPacketRegistry
from research_workflow_orchestrator import collect_sec_financials, load_env


AAPL_CIK = 320193
AAPL_SUBJECT = "sec:cik:0000320193"


def _load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise ValueError(f"Expected JSON object: {path}")
    return value


def run_sample(
    *, env_file: Path, db_path: Path, fundamental_packet_path: Path,
    document_path: Path, output_path: Path,
) -> dict[str, Any]:
    load_env(env_file)
    frozen = _load(fundamental_packet_path)
    run_id = str(frozen.get("run_id") or frozen.get("run_timestamp") or "").strip()
    if not run_id:
        raise ValueError("Frozen Fundamental Research Packet has no run_id")
    as_of = datetime.now(timezone.utc).date().isoformat()
    steps: dict[str, Any] = {}
    errors: list[dict[str, str]] = []

    listing = {
        "ticker": "AAPL", "venue_code": "NASDAQ", "name": "Apple Inc.",
        "instrument_type": "common_stock", "node_id": "technology.hardware_services.hardware",
        "payload": {
            "venue": "NASDAQ", "instrument_type": "common_stock",
            "raw_profile": {"country": "United States", "sector": "Technology", "industry": "Consumer Electronics"},
        },
    }
    profile = build_profile(listing, None, f"architecture-sample-{as_of}", run_id)
    steps["company_profile"] = publish_profile_to_control_plane(
        profile, db_path, canonical_subject_id=AAPL_SUBJECT
    )

    registry = ResearchPacketRegistry(db_path)
    limiter = SharedProviderRateLimiter()
    financial = collect_sec_financials(
        symbol="AAPL", cik=AAPL_CIK, as_of=as_of, run_id=run_id,
        registry=registry, limiter=limiter,
        upstream_control_packet_ids=(steps["company_profile"]["control_packet_id"],),
    )
    steps["financial_evidence"] = financial
    control = dict(financial.get("control_plane") or {})
    if financial.get("status") == "completed" and financial.get("ready_for_giannis") and control.get("control_packet_id"):
        packet, reference = load_financial_packet_from_control_plane(
            control_plane_db=db_path, control_packet_id=control["control_packet_id"],
        )
        analysis = analyze_financial_packet(packet, run_id=run_id)
        steps["fundamental_analysis"] = publish_fundamental_analysis_control_plane(
            control_plane_db=db_path, analysis=analysis,
            input_control_packet_id=control["control_packet_id"], subject_id=reference["subject_id"],
        )
    else:
        steps["fundamental_analysis"] = {
            "status": "blocked", "reason": "financial_readiness_gate",
            "blockers": financial.get("blockers", []),
        }

    try:
        steps["sec_filing_catalog"] = publish_filing_catalog(
            identifier="AAPL", subject_id=AAPL_SUBJECT, run_id=run_id,
            as_of=as_of, db_path=db_path,
        )
    except Exception as exc:
        errors.append({"step": "sec_filing_catalog", "error": str(exc)})
        steps["sec_filing_catalog"] = {"status": "failed"}

    try:
        steps["document_evidence"] = publish_document_evidence(
            source=document_path, subject_id=f"research_run:{run_id}", run_id=run_id,
            document_type="frozen_fundamental_research_report", db_path=db_path,
        )
    except Exception as exc:
        errors.append({"step": "document_evidence", "error": str(exc)})
        steps["document_evidence"] = {"status": "failed"}

    db = connect(db_path)
    try:
        graph = graph_status(db, run_id=run_id, persist_checks=True)
        lineage = None
        analysis_ref = steps.get("fundamental_analysis") or {}
        if analysis_ref.get("control_packet_id"):
            lineage = packet_lineage(db, analysis_ref["control_packet_id"])
    finally:
        db.close()
    result = {
        "schema": "ecosystem.architecture_upgrade_sample.v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "run_id": run_id, "research_only": True, "broker_calls": 0, "llm_calls": 0,
        "steps": steps, "asset_graph": graph, "fundamental_analysis_lineage": lineage,
        "errors": errors,
        "status": "completed" if not errors else "completed_with_extension_errors",
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    parser.add_argument("--db", type=Path, default=DEFAULT_CONTROL_PLANE_DB)
    parser.add_argument("--fundamental-packet", type=Path, default=Path("output/fundamental_research_packet_latest.json"))
    parser.add_argument("--document", type=Path, default=Path("output/fundamental_research_packet_latest.md"))
    parser.add_argument("--output", type=Path, default=Path("output/architecture_upgrade_sample_latest.json"))
    args = parser.parse_args(argv)
    result = run_sample(
        env_file=args.env_file, db_path=args.db, fundamental_packet_path=args.fundamental_packet,
        document_path=args.document, output_path=args.output,
    )
    print(json.dumps({
        "status": result["status"], "run_id": result["run_id"],
        "asset_summary": result["asset_graph"]["summary"], "errors": result["errors"],
    }, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "completed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
