from __future__ import annotations

import hashlib
import os
import re
from datetime import datetime, timezone
from typing import Any

import agent_methodology
import agent_personality


SCHEMA = "ecosystem.run_context.v1"
STAGE_AGENT = {
    "babis_shortlist": "babis",
    "giannis_rule_based": "giannis",
    "petros_review": "petros",
    "yankos_strategy": "yankos",
    "periklis_strategy": "periklis",
    "sokratis_strategy_teacher": "sokratis",
}


def _safe(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "_", value.strip()).strip("_.-")
    return cleaned[:96]


def _from_timestamp(value: str) -> str:
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:8]
    stamp = re.sub(r"[^0-9]", "", value)[:14] or datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    return f"run_{stamp}_{digest}"


def resolve_run_id(*payloads: dict[str, Any]) -> str:
    environment_id = _safe(os.getenv("ECOSYSTEM_RUN_ID", ""))
    if environment_id:
        return environment_id
    explicit = {_safe(str(payload.get("run_id") or "")) for payload in payloads if payload.get("run_id")}
    explicit.discard("")
    if len(explicit) > 1:
        raise ValueError(f"Mixed run_ids are not allowed: {sorted(explicit)}")
    if explicit:
        return explicit.pop()
    for payload in payloads:
        timestamp = str(payload.get("run_timestamp") or "").strip()
        if timestamp:
            return _from_timestamp(timestamp)
    return _from_timestamp(datetime.now(timezone.utc).isoformat())


def require_compatible_runs(*payloads: dict[str, Any]) -> str:
    ids = {_safe(str(payload.get("run_id") or "")) for payload in payloads if payload.get("run_id")}
    ids.discard("")
    if len(ids) > 1:
        raise ValueError(f"Agent handoff mixes incompatible run_ids: {sorted(ids)}")
    return resolve_run_id(*payloads)


def require_explicit_compatible_runs(*payloads: dict[str, Any]) -> str:
    """Require every handoff to declare the same non-empty run_id.

    Use this at synthesis/audit boundaries where deriving an id from a timestamp
    would hide a missing or mixed-run upstream contract.
    """
    if not payloads:
        raise ValueError("At least one payload is required for explicit run validation.")
    missing = [index for index, payload in enumerate(payloads) if not str(payload.get("run_id") or "").strip()]
    if missing:
        raise ValueError(f"Explicit run_id is required for every payload; missing at indexes {missing}.")
    ids = {_safe(str(payload.get("run_id") or "")) for payload in payloads}
    if "" in ids or len(ids) != 1:
        raise ValueError(f"Agent handoff mixes incompatible explicit run_ids: {sorted(ids)}")
    return ids.pop()


def attach_run_context(
    payload: dict[str, Any],
    *,
    stage: str,
    run_id: str | None = None,
    source_run_ids: list[str] | None = None,
) -> dict[str, Any]:
    payload["run_id"] = _safe(run_id or resolve_run_id(payload))
    payload["run_context"] = {
        "schema": SCHEMA,
        "run_id": payload["run_id"],
        "stage": stage,
        "source_run_ids": sorted({item for item in (source_run_ids or []) if item}),
    }
    methodology_agent = STAGE_AGENT.get(stage)
    if methodology_agent:
        payload["methodology"] = agent_methodology.methodology_identity(methodology_agent)
        payload["personality"] = agent_personality.personality_identity(methodology_agent)
    return payload
