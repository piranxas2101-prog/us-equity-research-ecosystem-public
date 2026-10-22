"""Route stored company profiles through sector agents in the current research run.

This finite step makes classification context observable in the shared control
plane. It does not claim that missing sector KPIs or issuer evidence were found.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from control_plane_store import connect
import research_control_plane
from sector_research_agent import analyze_input, route_node


def build_from_profile(profile: dict[str, Any], run_id: str,
                       sec_evidence: dict[str, Any] | None = None) -> dict[str, Any] | None:
    node = str(profile.get("taxonomy_node") or "")
    try:
        route_node(node)
    except ValueError:
        return None
    sources = [
        source for source in (profile.get("source_references") or [])
        if isinstance(source, dict) and source.get("source_id")
        and source.get("as_of") and source.get("evidence_label")
    ]
    business_model = None
    claim_sources: dict[str, list[str]] = {}
    narrative = str(profile.get("business_model") or "").strip()
    if narrative and profile.get("status") in {"rich_profile", "contextual_profile"}:
        source_ids = [
            str(source["source_id"]) for source in sources
            if source.get("provider") in {"exchange_bulk_profile", "fmp", "fmp_company_profile", "polygon", "polygon_ticker_reference", "sec_submissions"}
        ]
        if source_ids:
            business_model = narrative
            claim_sources["business_model"] = source_ids
    sec_evidence = sec_evidence or {}
    if business_model is None and sec_evidence.get("business_model"):
        business_model = str(sec_evidence["business_model"])  # the 10-K's own words, labelled issuer_management_claim
    sources += [source for source in sec_evidence.get("source_references") or []
                if source.get("source_id") and source.get("as_of") and source.get("evidence_label")]
    claim_sources.update({field: ids for field, ids in (sec_evidence.get("claim_sources") or {}).items()
                          if field not in claim_sources})
    return analyze_input({
        "identity": {
            "run_id": run_id,
            "snapshot_id": str(profile["snapshot_id"]),
            "symbol": str(profile["ticker"]),
            "venue_code": str(profile["venue_code"]),
            "classification_id": str(profile["profile_id"]),
            "leaf_node": node,
        },
        "evidence": {
            "business_model": business_model,
            "sector_kpis": sec_evidence.get("sector_kpis") or {},
            "peer_group": sec_evidence.get("peer_group") or [],
            **{field: sec_evidence.get(field) or [] for field in (
                "value_chain_position", "revenue_drivers", "cost_drivers", "competitive_position",
                "industry_risks", "company_specific_risks", "catalysts")},
            "source_references": sources,
            "claim_sources": claim_sources,
            "unknown_fields": list(profile.get("unknown_fields") or []),
            "evidence_gap_ids": [],
        },
    })


def publish_sector_context(
    *, packet: dict[str, Any], control_plane_db: Path,
    subject_id: str, profile_control_packet_id: str,
) -> dict[str, Any]:
    db = connect(control_plane_db)
    try:
        observation, _ = research_control_plane.record_observation(
            db, subject_id=subject_id, capability="research.sector_context",
            provider="ecosystem_sector_agents", source_ref=packet["packet_id"],
            observed_at=packet["created_at"], as_of=packet["created_at"],
            run_id=packet["run_id"], status="available", payload=packet,
        )
        # Only an insufficient packet is a missing-evidence gap; a partial one is tracked with its own, lighter reason
        # (not actionable in the run receipt); adequate and complete packets open no gap. Packets from before the
        # tiers fall back to the old rule (anything but complete is missing).
        tier = packet.get("evidence_tier") or ("complete" if packet.get("status") == "complete" else "insufficient")
        reason_code = {"insufficient": "sector_evidence_missing", "partial": "sector_evidence_partial"}.get(tier)
        if reason_code:
            research_control_plane.record_evidence_gap(
                db, subject_id=subject_id, run_id=packet["run_id"],
                field="sector_context", reason_code=reason_code,
                owner="sector_context_handoff", source_event_id=observation["observation_id"],
                payload={"ticker": packet["symbol"], "unknown_fields": packet.get("unknown_fields") or []},
            )
        inputs = [("packet", profile_control_packet_id), ("observation", observation["observation_id"])]
        fingerprint = research_control_plane.input_fingerprint(
            stage="sector_context", subject_id=subject_id, inputs=inputs,
            config={"schema": packet["schema"], "framework_id": packet["framework_id"]},
            implementation_version="sector_context_handoff.v1",
        )
        checkpoint = research_control_plane.acquire_checkpoint(
            db, run_id=packet["run_id"], stage="sector_context", subject_id=subject_id,
            fingerprint=fingerprint, details={"sector_packet_id": packet["packet_id"]},
        )
        if checkpoint["status"] == "reused":
            return {"status": "reused", "control_packet_id": checkpoint["output_packet_id"]}
        control_packet, created = research_control_plane.build_packet(
            db, kind="sector_context", subject_id=subject_id, run_id=packet["run_id"],
            producer_name=packet["agent_id"], producer_version="v1",
            as_of=packet["created_at"], quality_state="conditional",
            inputs=inputs, payload=packet,
        )
        research_control_plane.set_packet_freshness(
            db, packet_id=control_packet["packet_id"], knowledge_date=packet["created_at"],
            stale_after=None,
            must_refresh_if=("new_filing", "sector_reclassification", "material_industry_event"),
            freshness_state="needs_refresh",
        )
        research_control_plane.record_usage_event(
            db, run_id=packet["run_id"], stage="sector_context", subject_id=subject_id,
            api_calls=0, input_tokens=0, output_tokens=0, estimated_cost_usd=0.0,
            cache_status="miss", metadata={"agent_id": packet["agent_id"]},
        )
        completed = research_control_plane.complete_checkpoint(db, checkpoint["checkpoint_id"], control_packet)
        return {"status": completed["status"], "control_packet_id": control_packet["packet_id"], "created": created}
    finally:
        db.close()


def record_unroutable_sector_context(
    *, profile: dict[str, Any], control_plane_db: Path,
    subject_id: str, profile_control_packet_id: str,
) -> dict[str, Any]:
    """Keep an unclassified issuer visible without inventing sector context."""
    db = connect(control_plane_db)
    try:
        gap, created = research_control_plane.record_evidence_gap(
            db, subject_id=subject_id, run_id=str(profile["run_id"]),
            field="sector_context", reason_code="classification_unresolved",
            owner="babis",
            payload={
                "ticker": profile.get("ticker"),
                "taxonomy_node": profile.get("taxonomy_node"),
                "profile_status": profile.get("status"),
                "profile_control_packet_id": profile_control_packet_id,
            },
        )
        return {"status": "review_required", "gap_id": gap["gap_id"], "created": created}
    finally:
        db.close()
