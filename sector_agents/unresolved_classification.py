"""Evidence-only specialist for symbols without a usable sector subcategory.

The agent may propose classification candidates, but Babis remains the only
authority allowed to accept a candidate and write the production taxonomy.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from research_registry.core import digest
import agent_personality


AGENT_ID = "sector_unresolved_classification"
CATEGORY = "unresolved_classification"
SCHEMA = "ecosystem.unresolved_classification_packet.v1"
PROFILE_FIELDS = (
    "business_model", "products_services", "revenue_model", "geography",
    "value_chain_clues", "peer_clues",
)
CANDIDATE_FIELDS = ("sector_candidates", "subcategory_candidates")


def _has_value(value: Any) -> bool:
    return value not in (None, "", [], {})


def _validate_candidates(candidates: list[dict[str, Any]], source_ids: set[str], kind: str) -> None:
    for candidate in candidates:
        if not isinstance(candidate, dict):
            raise ValueError(f"Every {kind} candidate must be an object")
        if not candidate.get(kind):
            raise ValueError(f"Every {kind} candidate requires {kind}")
        cited = set(candidate.get("source_ids") or [])
        if not cited or not cited <= source_ids:
            raise ValueError(f"Every {kind} candidate requires valid source_ids")
        if candidate.get("confidence") not in {"high", "medium", "low"}:
            raise ValueError(f"Every {kind} candidate requires high, medium or low confidence")


def analyze(identity: dict[str, Any], evidence: dict[str, Any]) -> dict[str, Any]:
    """Create an immutable proposal packet without changing classification."""
    if identity.get("classification_write") or evidence.get("classification_write"):
        raise ValueError("This agent cannot write an official classification")
    sources = list(evidence.get("source_references") or [])
    if any(not row.get("source_id") or not row.get("as_of") or not row.get("evidence_label") for row in sources):
        raise ValueError("Every source reference requires source_id, as_of and evidence_label")
    source_ids = {str(row["source_id"]) for row in sources}
    claims = evidence.get("claim_sources") or {}
    profile = {field: evidence.get(field) for field in PROFILE_FIELDS}
    unsupported = [
        field for field, value in profile.items()
        if _has_value(value) and (not claims.get(field) or not set(claims[field]) <= source_ids)
    ]
    if unsupported:
        raise ValueError("Unsupported evidence fields: " + ",".join(sorted(unsupported)))

    sector_candidates = list(evidence.get("sector_candidates") or [])
    subcategory_candidates = list(evidence.get("subcategory_candidates") or [])
    _validate_candidates(sector_candidates, source_ids, "sector")
    _validate_candidates(subcategory_candidates, source_ids, "subcategory")

    unknown = set(evidence.get("unknown_fields") or [])
    for field, value in profile.items():
        if not _has_value(value):
            unknown.add(field)
    if not sector_candidates:
        unknown.add("sector_candidates")
    if not subcategory_candidates:
        unknown.add("subcategory_candidates")
    gaps = sorted(set(evidence.get("evidence_gap_ids") or []))
    confidence = "insufficient" if not sources else ("low" if gaps or unknown else "medium")
    status = "ready_for_babis_review" if sector_candidates and subcategory_candidates else "review_required"
    stamp = datetime.now(timezone.utc).isoformat()
    identity_values = [identity.get(key) for key in ("run_id", "snapshot_id", "venue_code", "symbol", "classification_id")]
    return {
        "schema": SCHEMA,
        "packet_id": "unresolved_packet_" + digest(identity_values + [evidence])[:24],
        "run_id": identity["run_id"],
        "snapshot_id": identity["snapshot_id"],
        "symbol": str(identity["symbol"]).upper(),
        "venue_code": identity["venue_code"],
        "classification_id": identity.get("classification_id"),
        "classification_state": "unresolved",
        "category": CATEGORY,
        "agent_id": AGENT_ID,
        "personality": agent_personality.personality_identity(AGENT_ID),
        "reasoning_contract": {
            "observable_fields": ["observations", "unknowns", "competing_hypotheses", "strongest_countercase", "evidence_to_change_view", "safe_next_action"],
            "decision_changed_by_personality": False,
            "authority_expanded": False,
        },
        **profile,
        "sector_candidates": sector_candidates,
        "subcategory_candidates": subcategory_candidates,
        "source_references": sources,
        "unknown_fields": sorted(unknown),
        "evidence_gap_ids": gaps,
        "confidence": confidence,
        "status": status,
        "handoff_to": "babis",
        "classification_write_allowed": False,
        "created_at": stamp,
    }
