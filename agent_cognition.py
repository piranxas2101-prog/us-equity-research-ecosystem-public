"""Observable, bounded decision notes for agents and sub-agents.

This is not hidden chain-of-thought. It stores reviewable evidence, alternatives,
revision conditions and handoff intent while leaving scores and authority untouched.
"""
from __future__ import annotations

from typing import Any

import agent_personality


NOTE_SCHEMA = "ecosystem.observable_reasoning_note.v1"
REQUIRED_NOTE_FIELDS = (
    "observations", "unknowns", "competing_hypotheses", "strongest_countercase",
    "evidence_to_change_view", "safe_next_action",
)


def build_reasoning_note(agent_id: str, *, observations: list[dict[str, Any]],
                         unknowns: list[str], competing_hypotheses: list[str],
                         strongest_countercase: str, evidence_to_change_view: list[str],
                         safe_next_action: str, source_ids: list[str],
                         registry_path=None) -> dict[str, Any]:
    identity = agent_personality.personality_identity(agent_id, registry_path)
    note = {
        "schema": NOTE_SCHEMA,
        "agent_id": agent_id,
        "personality": identity,
        "observations": observations,
        "unknowns": sorted(set(unknowns)),
        "competing_hypotheses": competing_hypotheses,
        "strongest_countercase": strongest_countercase,
        "evidence_to_change_view": evidence_to_change_view,
        "safe_next_action": safe_next_action,
        "source_ids": sorted(set(source_ids)),
        "decision_changed_by_personality": False,
        "authority_expanded": False,
    }
    errors = validate_reasoning_note(note)
    if errors:
        raise ValueError("; ".join(errors))
    return note


def validate_reasoning_note(note: dict[str, Any]) -> list[str]:
    errors = []
    if note.get("schema") != NOTE_SCHEMA:
        errors.append("unsupported reasoning-note schema")
    for field in REQUIRED_NOTE_FIELDS:
        if note.get(field) in (None, "", []):
            errors.append(f"missing:{field}")
    source_ids = set(note.get("source_ids") or [])
    for index, observation in enumerate(note.get("observations") or []):
        if not observation.get("claim") or not observation.get("source_id"):
            errors.append(f"observation:{index}:missing_claim_or_source")
        elif observation["source_id"] not in source_ids:
            errors.append(f"observation:{index}:unknown_source")
    if note.get("decision_changed_by_personality") is not False:
        errors.append("personality_must_not_change_decision")
    if note.get("authority_expanded") is not False:
        errors.append("personality_must_not_expand_authority")
    return errors
