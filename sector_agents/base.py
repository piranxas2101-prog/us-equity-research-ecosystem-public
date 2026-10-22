"""Evidence-preserving implementation shared by every sector agent."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from research_registry.core import digest
from sector_research_agent import load_registry, route_node, validate_packet
import agent_personality


ANALYSIS_FIELDS = (
    "business_model", "value_chain_position", "revenue_drivers", "cost_drivers",
    "peer_group", "competitive_position", "sector_kpis", "industry_risks",
    "company_specific_risks", "catalysts",
)


def _has_value(value: Any) -> bool:
    return value not in (None, "", [], {})


# Evidence tiers, approved by the owner 2026-10-06 (docs/REDESIGN_NOTES_2026-10.md 4θ): coverage of the framework
# (nine qualitative fields plus the required KPIs), with "adequate" also needing a sourced business model and a KPI.
PARTIAL_MIN_COVERAGE = 0.20
ADEQUATE_MIN_COVERAGE = 0.50


def evidence_tier(answered: int, required: int, *, has_business_model: bool, kpi_count: int) -> str:
    share = answered / required if required else 0.0
    if required and answered >= required:
        return "complete"
    if share >= ADEQUATE_MIN_COVERAGE and has_business_model and kpi_count >= 1:
        return "adequate"
    if share >= PARTIAL_MIN_COVERAGE:
        return "partial"
    return "insufficient"


def analyze_sector_evidence(expected_sector: str, identity: dict[str, Any], evidence: dict[str, Any]) -> dict[str, Any]:
    """Turn sourced sector evidence into a validated immutable handoff packet."""
    route = route_node(identity.get("leaf_node"))
    if route["sector"] != expected_sector:
        raise ValueError(f"Agent {expected_sector} cannot analyze {route['sector']} node")
    sources = list(evidence.get("source_references") or [])
    source_ids = {str(row.get("source_id")) for row in sources if row.get("source_id")}
    if any(not row.get("as_of") or not row.get("evidence_label") for row in sources):
        raise ValueError("Every source reference requires as_of and evidence_label")
    claims = evidence.get("claim_sources") or {}
    supplied = {field: evidence.get(field) for field in ANALYSIS_FIELDS}
    unsupported = [field for field, value in supplied.items() if _has_value(value) and not set(claims.get(field) or []) <= source_ids]
    unsupported += [field for field, value in supplied.items() if _has_value(value) and not claims.get(field)]
    if unsupported:
        raise ValueError("Unsupported evidence fields: " + ",".join(sorted(set(unsupported))))

    required_kpis = route["required_sector_kpis"]
    sector_kpis = dict(evidence.get("sector_kpis") or {})
    unknown = set(evidence.get("unknown_fields") or [])
    for field, value in supplied.items():
        if not _has_value(value):
            unknown.add(field)
    for kpi in required_kpis:
        if not _has_value(sector_kpis.get(kpi)):
            unknown.add("sector_kpis." + kpi)
    gaps = sorted(set(evidence.get("evidence_gap_ids") or []))
    if not sources:
        confidence = "insufficient"
    elif gaps or unknown:
        confidence = "low"
    elif len(sources) == 1:
        confidence = "medium"
    else:
        confidence = "high"
    coverage = {
        "answered": sum(_has_value(supplied[f]) for f in ANALYSIS_FIELDS if f != "sector_kpis")
        + sum(_has_value(sector_kpis.get(kpi)) for kpi in required_kpis),
        "required": len(ANALYSIS_FIELDS) - 1 + len(required_kpis)}
    stamp = datetime.now(timezone.utc).isoformat()
    identity_hash = [identity.get(key) for key in ("run_id", "snapshot_id", "venue_code", "symbol", "classification_id", "leaf_node")]
    packet = {
        "schema": "ecosystem.sector_context_packet.v1",
        "packet_id": "sector_packet_" + digest(identity_hash + [route["framework_id"], evidence])[:24],
        "run_id": identity["run_id"], "snapshot_id": identity["snapshot_id"],
        "symbol": str(identity["symbol"]).upper(), "venue_code": identity["venue_code"],
        "classification_id": identity["classification_id"], "sector": route["sector"],
        "subcategory": route["subcategory"], "leaf_node": identity["leaf_node"],
        "agent_id": route["agent_id"], "framework_id": route["framework_id"],
        "personality": agent_personality.personality_identity(route["agent_id"]),
        "reasoning_contract": {
            "observable_fields": ["observations", "unknowns", "competing_hypotheses", "strongest_countercase", "evidence_to_change_view", "safe_next_action"],
            "decision_changed_by_personality": False,
            "authority_expanded": False,
        },
        "required_sector_kpis": required_kpis,
        **supplied,
        "source_references": sources,
        "unknown_fields": sorted(unknown), "evidence_gap_ids": gaps,
        # how much of the framework is answered: the nine qualitative fields plus each required KPI
        "evidence_coverage": coverage,
        "evidence_tier": evidence_tier(coverage["answered"], coverage["required"],
                                       has_business_model=_has_value(supplied["business_model"]),
                                       kpi_count=sum(_has_value(sector_kpis.get(kpi)) for kpi in required_kpis)),
        "confidence": confidence,
        "status": "complete" if confidence in {"high", "medium"} else "review_required",
        "created_at": stamp,
    }
    result = validate_packet(packet, load_registry())
    if result["status"] != "valid":
        raise ValueError("Invalid sector packet: " + ",".join(result["errors"]))
    return packet
