"""Structured fundamental synthesis for canonical evidence packets.

This production path is deterministic and uses no LLM. A future on-demand LLM
synthesis may consume the same packet, but the contract caps that at one call.
"""
from __future__ import annotations
import argparse
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from financial_evidence_packet import readiness_gate
import research_control_plane
from research_information_flow import ResearchPacketRegistry, digest

SCHEMA = "ecosystem.fundamental_analysis_packet.v2"


def load_financial_packet_from_control_plane(
    *, control_plane_db: Path, control_packet_id: str
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Resolve a Financial Evidence handoff by immutable control-packet ID."""
    db = sqlite3.connect(control_plane_db)
    try:
        row = db.execute(
            "SELECT kind,subject_id,run_id,payload_json FROM ecosystem_evidence_packet_store WHERE packet_id=?",
            (control_packet_id,),
        ).fetchone()
    finally:
        db.close()
    if not row:
        raise ValueError(f"Unknown financial control packet: {control_packet_id}")
    kind, subject_id, source_run_id, payload_json = row
    if kind != "financial_evidence":
        raise ValueError(f"Control packet {control_packet_id} is {kind}, not financial_evidence")
    control_payload = json.loads(payload_json)
    upstream_packet_id = str((control_payload.get("payload") or {}).get("financial_packet_id") or "").strip()
    if not upstream_packet_id:
        raise ValueError(f"Financial control packet {control_packet_id} has no upstream packet reference")
    financial_packet = ResearchPacketRegistry(Path(control_plane_db)).get_packet(upstream_packet_id)
    if not financial_packet:
        raise ValueError(f"Missing upstream financial packet: {upstream_packet_id}")
    return financial_packet, {
        "control_packet_id": control_packet_id,
        "subject_id": subject_id,
        "source_run_id": source_run_id,
        "financial_packet_id": upstream_packet_id,
    }


def publish_fundamental_analysis_control_plane(
    *, control_plane_db: Path, analysis: dict[str, Any], input_control_packet_id: str,
    subject_id: str, sector_control_packet_id: str | None = None,
) -> dict[str, Any]:
    """Publish the deterministic Giannis result as a downstream immutable packet."""
    db = sqlite3.connect(control_plane_db)
    db.row_factory = sqlite3.Row
    try:
        research_control_plane.init_schema(db)
        inputs = [("packet", input_control_packet_id)]
        if sector_control_packet_id:
            sector = db.execute(
                "SELECT kind,subject_id,run_id FROM ecosystem_evidence_packet_store WHERE packet_id=?",
                (sector_control_packet_id,),
            ).fetchone()
            if not sector or tuple(sector) != ("sector_context", subject_id, str(analysis["run_id"])):
                raise ValueError("Sector context must match the financial subject and current run")
            inputs.append(("packet", sector_control_packet_id))
        fingerprint = research_control_plane.input_fingerprint(
            stage="fundamental_analysis",
            subject_id=subject_id,
            inputs=inputs,
            config={"analysis_schema": analysis.get("schema"), "input_signature": analysis.get("input_signature")},
            implementation_version="giannis_fundamental_agent.v2",
        )
        checkpoint = research_control_plane.acquire_checkpoint(
            db,
            run_id=str(analysis["run_id"]),
            stage="fundamental_analysis",
            subject_id=subject_id,
            fingerprint=fingerprint,
            details={"analysis_packet_id": analysis.get("packet_id")},
        )
        if checkpoint["status"] == "reused":
            return {
                "status": "reused", "control_packet_id": checkpoint["output_packet_id"],
                "checkpoint_id": checkpoint["checkpoint_id"],
            }
        control_packet, created = research_control_plane.build_packet(
            db,
            kind="fundamental_analysis",
            subject_id=subject_id,
            run_id=str(analysis["run_id"]),
            producer_name="giannis_fundamental_agent",
            producer_version="v2",
            as_of=(analysis.get("periods") or [None])[-1],
            quality_state="ready" if analysis.get("confidence") in {"high", "medium"} else "conditional",
            inputs=inputs,
            payload=analysis,
        )
        research_control_plane.set_packet_freshness(
            db, packet_id=control_packet["packet_id"],
            knowledge_date=control_packet.get("as_of") or analysis.get("created_at"),
            stale_after=None,
            must_refresh_if=("new_financial_evidence", "new_sector_context", "material_source_conflict"),
            freshness_state="current" if analysis.get("confidence") in {"high", "medium"} else "needs_refresh",
        )
        claims = []
        claim_specs = [
            ("financial_coverage", "financial_evidence_confidence", analysis.get("confidence"),
             f"Financial evidence coverage produced {analysis.get('confidence')} confidence.", "confidence"),
            ("reported_margin", "reported_net_margin", (analysis.get("margin_analysis") or {}).get("reported_net_margin"),
             f"Reported net margin is {(analysis.get('margin_analysis') or {}).get('reported_net_margin')}.",
             "margin_analysis.reported_net_margin"),
            ("balance_sheet", "reported_net_cash", (analysis.get("balance_sheet_analysis") or {}).get("net_cash"),
             f"Reported net cash is {(analysis.get('balance_sheet_analysis') or {}).get('net_cash')}.",
             "balance_sheet_analysis.net_cash"),
        ]
        for claim_type, predicate, object_value, claim_text, field_path in claim_specs:
            if object_value is None:
                continue
            claims.append(research_control_plane.record_claim(
                db, canonical_subject_id=subject_id, run_id=str(analysis["run_id"]),
                claim_type=claim_type, claim_text=claim_text,
                verification_status="source_linked", confidence=str(analysis.get("confidence") or "low"),
                as_of=control_packet.get("as_of"),
                evidence_inputs=(("packet", input_control_packet_id, "supporting"),),
                predicate=predicate, object_value=object_value,
                source_locator={"packet_id": control_packet["packet_id"], "field_path": field_path},
            ))
        research_control_plane.record_thesis_revision(
            db, canonical_subject_id=subject_id, run_id=str(analysis["run_id"]),
            thesis_state="watch" if analysis.get("unknown_fields") else "active",
            thesis_text=(
                f"{analysis.get('symbol')} has a source-linked deterministic financial assessment; "
                "security attractiveness remains separate and requires valuation and expectation evidence."
            ),
            supporting_claim_ids=[claim["claim_id"] for claim in claims],
            expectation_map={"status": "source_gap", "reason": "consensus_not_in_financial_packet"},
            stale_after=None,
            must_refresh_if=("new_10_k", "new_10_q", "guidance_change", "financial_restated"),
        )
        research_control_plane.record_usage_event(
            db, run_id=str(analysis["run_id"]), stage="fundamental_analysis",
            subject_id=subject_id, api_calls=0, input_tokens=0, output_tokens=0,
            estimated_cost_usd=0.0, cache_status="miss",
            metadata={"implementation": "deterministic", "claim_count": len(claims)},
        )
        result = research_control_plane.complete_checkpoint(db, checkpoint["checkpoint_id"], control_packet)
    finally:
        db.close()
    return {
        "status": result["status"], "control_packet_id": control_packet["packet_id"],
        "checkpoint_id": result["checkpoint_id"], "created": created,
    }

def _value(packet: dict[str, Any], field: str) -> float | None:
    raw = (packet.get("values") or {}).get(field)
    raw = raw.get("value") if isinstance(raw, dict) else raw
    try: return float(raw) if raw is not None else None
    except (TypeError, ValueError): return None

def analyze_financial_packet(packet: dict[str, Any], *, run_id: str,
                             sector_context_packet_id: str | None = None) -> dict[str, Any]:
    readiness = dict(packet.get("readiness") or readiness_gate(packet))
    if not readiness.get("ready_for_fundamental_synthesis"):
        raise ValueError("Financial readiness gate did not pass")
    revenue, net_income = _value(packet, "revenue"), _value(packet, "net_income")
    cash, debt, ocf = _value(packet, "cash"), _value(packet, "debt"), _value(packet, "operating_cash_flow")
    equity = _value(packet, "stockholders_equity")
    net_cash = cash - debt if cash is not None and debt is not None else None
    net_margin = net_income / revenue if revenue not in (None, 0) and net_income is not None else None
    coverage = len(packet.get("values") or {})
    confidence = "high" if coverage >= 8 and not packet.get("conflicts") else "medium" if coverage >= 5 else "low"
    source_refs = list(packet.get("source_references") or [])
    claim_links = [{"claim": f"reported_{ref.get('field')}", "source_reference": ref}
                   for ref in source_refs]
    body = {
        "schema": SCHEMA, "run_id": run_id, "symbol": packet.get("ticker"),
        "sector_context_packet_id": sector_context_packet_id,
        "periods": list(packet.get("periods") or []), "currency": packet.get("currency"),
        "units": list(packet.get("units") or []), "normalized_financials_ref": packet.get("packet_id"),
        "growth_analysis": {"status": "insufficient_period_history", "revenue": revenue},
        "margin_analysis": {"reported_net_margin": net_margin, "status": "observed" if net_margin is not None else "unknown"},
        "earnings_quality": {"net_income": net_income, "operating_cash_flow": ocf,
                             "status": "positive_cash_support" if net_income is not None and ocf is not None and net_income > 0 and ocf > 0 else "requires_review"},
        "cash_flow_analysis": {"operating_cash_flow": ocf},
        "balance_sheet_analysis": {"cash": cash, "debt": debt, "net_cash": net_cash, "stockholders_equity": equity},
        "capital_allocation": {"status": "unknown", "reason": "capital_allocation_evidence_not_in_packet"},
        "dilution_analysis": {"status": "unknown", "reason": "share_history_not_in_packet"},
        "sector_kpi_reconciliation": {"status": "pending" if sector_context_packet_id else "not_available"},
        "financial_risks": list(readiness.get("warnings") or []),
        "source_references": source_refs, "unknown_fields": list(packet.get("unknown_fields") or []),
        "evidence_gap_ids": [], "confidence": confidence, "claim_source_links": claim_links,
        "input_packet_ids": [packet.get("packet_id")], "input_signature": digest({"packet": packet.get("payload_sha256"), "sector": sector_context_packet_id, "contract": "v2"}),
        "created_at": datetime.now(timezone.utc).isoformat(), "llm_calls": 0,
        "technical_inputs_used": False, "trade_instruction": False,
    }
    return {**body, "packet_id": "fund_" + digest(body)[:24]}

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Giannis structured fundamental agent")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--financial-packet")
    source.add_argument("--financial-control-packet-id")
    parser.add_argument("--flow-db", type=Path, help="Shared flow database for --financial-control-packet-id")
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--sector-context-packet-id"); parser.add_argument("--output", required=True)
    args = parser.parse_args(argv)
    input_control = None
    if args.financial_packet:
        packet = json.loads(Path(args.financial_packet).read_text(encoding="utf-8-sig"))
    else:
        if args.flow_db is None:
            parser.error("--flow-db is required with --financial-control-packet-id")
        packet, input_control = load_financial_packet_from_control_plane(
            control_plane_db=args.flow_db, control_packet_id=args.financial_control_packet_id
        )
    result = analyze_financial_packet(packet, run_id=args.run_id, sector_context_packet_id=args.sector_context_packet_id)
    if input_control:
        result["input_control_packet_id"] = input_control["control_packet_id"]
        result["input_subject_id"] = input_control["subject_id"]
        result["control_plane"] = publish_fundamental_analysis_control_plane(
            control_plane_db=args.flow_db,
            analysis=result,
            input_control_packet_id=input_control["control_packet_id"],
            subject_id=input_control["subject_id"],
        )
    target = Path(args.output); target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"packet_id": result["packet_id"], "symbol": result["symbol"], "llm_calls": 0}))
    return 0

if __name__ == "__main__": raise SystemExit(main())
