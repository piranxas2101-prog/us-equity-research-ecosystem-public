"""Immutable evidence packets, handoff contracts, and resumable workflow checkpoints.

This module is deliberately local and deterministic.  It is the shared information
plane for the research ecosystem: producers record source-aware observations, agents
publish immutable packets that reference them, and consumers use packet IDs/hashes
instead of copying large payloads between artifacts.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from typing import Any, Iterable

import information_registry


OBSERVATION_SCHEMA = "ecosystem.observation.v1"
PACKET_SCHEMA = "ecosystem.evidence_packet.v1"
CHECKPOINT_SCHEMA = "ecosystem.workflow_checkpoint.v1"
GAP_SCHEMA = "ecosystem.evidence_gap.v2"
ASSET_CHECK_SCHEMA = "ecosystem.asset_check_result.v1"
SUBJECT_ALIAS_SCHEMA = "ecosystem.subject_alias.v1"
FRESHNESS_SCHEMA = "ecosystem.packet_freshness.v1"
GAP_RESOLUTION_SCHEMA = "ecosystem.gap_resolution.v1"
USAGE_SCHEMA = "ecosystem.usage_event.v1"
CLAIM_SCHEMA = "ecosystem.claim.v1"
THESIS_SCHEMA = "ecosystem.thesis_revision.v1"


class ControlPlaneError(RuntimeError):
    """Raised when immutable evidence or workflow contracts are violated."""


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _required_string(payload: dict[str, Any], field: str) -> str:
    value = str(payload.get(field) or "").strip()
    if not value:
        raise ControlPlaneError(f"{field} is required")
    return value


def init_schema(conn: sqlite3.Connection) -> None:
    """Install only the additive local tables needed by the control plane."""
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS ecosystem_observation_store (
            observation_id TEXT PRIMARY KEY,
            subject_id TEXT NOT NULL,
            capability TEXT NOT NULL,
            provider TEXT NOT NULL,
            source_ref TEXT NOT NULL,
            observed_at TEXT NOT NULL,
            as_of TEXT,
            run_id TEXT,
            status TEXT NOT NULL,
            reason_code TEXT,
            content_hash TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            UNIQUE(subject_id, capability, provider, source_ref, content_hash)
        );
        CREATE INDEX IF NOT EXISTS idx_ecosystem_observation_subject
            ON ecosystem_observation_store(subject_id, capability, observed_at);

        CREATE TABLE IF NOT EXISTS ecosystem_evidence_packet_store (
            packet_id TEXT PRIMARY KEY,
            kind TEXT NOT NULL,
            subject_id TEXT NOT NULL,
            run_id TEXT NOT NULL,
            as_of TEXT,
            producer_name TEXT NOT NULL,
            producer_version TEXT NOT NULL,
            quality_state TEXT NOT NULL,
            content_hash TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            UNIQUE(kind, subject_id, run_id, content_hash)
        );
        CREATE INDEX IF NOT EXISTS idx_ecosystem_packet_subject
            ON ecosystem_evidence_packet_store(subject_id, kind, created_at);

        CREATE TABLE IF NOT EXISTS ecosystem_packet_input (
            packet_id TEXT NOT NULL REFERENCES ecosystem_evidence_packet_store(packet_id) ON DELETE CASCADE,
            input_kind TEXT NOT NULL,
            input_id TEXT NOT NULL,
            input_hash TEXT NOT NULL,
            PRIMARY KEY(packet_id, input_kind, input_id)
        );

        CREATE TABLE IF NOT EXISTS ecosystem_workflow_checkpoint (
            checkpoint_id TEXT PRIMARY KEY,
            run_id TEXT NOT NULL,
            stage TEXT NOT NULL,
            subject_id TEXT NOT NULL,
            input_fingerprint TEXT NOT NULL,
            state TEXT NOT NULL,
            output_packet_id TEXT,
            details_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            completed_at TEXT,
            UNIQUE(run_id, stage, subject_id, input_fingerprint)
        );
        CREATE INDEX IF NOT EXISTS idx_ecosystem_checkpoint_lookup
            ON ecosystem_workflow_checkpoint(stage, subject_id, input_fingerprint, state);

        CREATE TABLE IF NOT EXISTS ecosystem_control_plane_gap (
            gap_id TEXT PRIMARY KEY,
            subject_id TEXT NOT NULL,
            run_id TEXT,
            field TEXT NOT NULL,
            reason_code TEXT NOT NULL,
            owner TEXT NOT NULL,
            status TEXT NOT NULL,
            source_event_id TEXT,
            payload_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            resolved_at TEXT,
            UNIQUE(subject_id, run_id, field, reason_code, source_event_id)
        );

        CREATE TABLE IF NOT EXISTS ecosystem_asset_check_result (
            check_id TEXT PRIMARY KEY,
            packet_id TEXT NOT NULL REFERENCES ecosystem_evidence_packet_store(packet_id) ON DELETE CASCADE,
            asset_key TEXT NOT NULL,
            check_name TEXT NOT NULL,
            status TEXT NOT NULL,
            severity TEXT NOT NULL,
            metadata_json TEXT NOT NULL,
            evaluated_at TEXT NOT NULL,
            UNIQUE(packet_id, check_name)
        );
        CREATE INDEX IF NOT EXISTS idx_ecosystem_asset_check_status
            ON ecosystem_asset_check_result(asset_key, status, evaluated_at);

        CREATE TABLE IF NOT EXISTS ecosystem_subject_alias (
            alias_subject_id TEXT PRIMARY KEY,
            canonical_subject_id TEXT NOT NULL,
            alias_kind TEXT NOT NULL,
            source TEXT NOT NULL,
            content_hash TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_ecosystem_subject_alias_canonical
            ON ecosystem_subject_alias(canonical_subject_id, alias_kind);

        CREATE TABLE IF NOT EXISTS ecosystem_packet_freshness (
            packet_id TEXT PRIMARY KEY REFERENCES ecosystem_evidence_packet_store(packet_id) ON DELETE CASCADE,
            knowledge_date TEXT,
            stale_after TEXT,
            must_refresh_if_json TEXT NOT NULL,
            freshness_state TEXT NOT NULL,
            content_hash TEXT NOT NULL,
            evaluated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS ecosystem_gap_resolution_event (
            resolution_id TEXT PRIMARY KEY,
            gap_id TEXT NOT NULL REFERENCES ecosystem_control_plane_gap(gap_id),
            resolving_input_kind TEXT NOT NULL,
            resolving_input_id TEXT NOT NULL,
            resolving_input_hash TEXT NOT NULL,
            previous_status TEXT NOT NULL,
            new_status TEXT NOT NULL,
            reason TEXT NOT NULL,
            content_hash TEXT NOT NULL,
            created_at TEXT NOT NULL,
            UNIQUE(gap_id, resolving_input_kind, resolving_input_id)
        );

        CREATE TABLE IF NOT EXISTS ecosystem_usage_event (
            event_id TEXT PRIMARY KEY,
            run_id TEXT NOT NULL,
            stage TEXT NOT NULL,
            subject_id TEXT,
            provider TEXT,
            model TEXT,
            api_calls INTEGER NOT NULL,
            input_tokens INTEGER NOT NULL,
            output_tokens INTEGER NOT NULL,
            estimated_cost_usd REAL NOT NULL,
            latency_ms REAL NOT NULL,
            cache_status TEXT NOT NULL,
            metadata_json TEXT NOT NULL,
            content_hash TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_ecosystem_usage_run
            ON ecosystem_usage_event(run_id, stage, created_at);

        CREATE TABLE IF NOT EXISTS ecosystem_claim_store (
            claim_id TEXT PRIMARY KEY,
            canonical_subject_id TEXT NOT NULL,
            run_id TEXT NOT NULL,
            claim_type TEXT NOT NULL,
            claim_text TEXT NOT NULL,
            verification_status TEXT NOT NULL,
            confidence TEXT NOT NULL,
            as_of TEXT,
            content_hash TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS ecosystem_claim_evidence_link (
            claim_id TEXT NOT NULL REFERENCES ecosystem_claim_store(claim_id) ON DELETE CASCADE,
            input_kind TEXT NOT NULL,
            input_id TEXT NOT NULL,
            input_hash TEXT NOT NULL,
            stance TEXT NOT NULL,
            PRIMARY KEY(claim_id, input_kind, input_id)
        );
        CREATE TABLE IF NOT EXISTS ecosystem_thesis_revision (
            thesis_revision_id TEXT PRIMARY KEY,
            canonical_subject_id TEXT NOT NULL,
            run_id TEXT NOT NULL,
            revision INTEGER NOT NULL,
            thesis_state TEXT NOT NULL,
            thesis_text TEXT NOT NULL,
            supporting_claim_ids_json TEXT NOT NULL,
            disconfirming_claim_ids_json TEXT NOT NULL,
            expectation_map_json TEXT NOT NULL,
            stale_after TEXT,
            must_refresh_if_json TEXT NOT NULL,
            content_hash TEXT NOT NULL,
            created_at TEXT NOT NULL,
            UNIQUE(canonical_subject_id, revision)
        );
        """
    )
    information_registry.init_schema(conn)
    conn.commit()


def _insert_immutable(
    conn: sqlite3.Connection,
    *,
    table: str,
    id_column: str,
    record_id: str,
    content_hash: str,
    lookup_sql: str,
    lookup_args: tuple[Any, ...],
    insert_sql: str,
    insert_args: tuple[Any, ...],
) -> tuple[str, bool]:
    previous = conn.execute(lookup_sql, lookup_args).fetchone()
    if previous:
        if str(previous["content_hash"]) != content_hash:
            raise ControlPlaneError(f"Immutable {table} conflict for {record_id}")
        return str(previous[id_column]), False
    conn.execute(insert_sql, insert_args)
    return record_id, True


def record_observation(
    conn: sqlite3.Connection,
    *,
    subject_id: str,
    capability: str,
    provider: str,
    source_ref: str,
    observed_at: str,
    payload: dict[str, Any],
    run_id: str | None = None,
    as_of: str | None = None,
    status: str = "available",
    reason_code: str | None = None,
) -> tuple[dict[str, Any], bool]:
    """Record one source observation; identical input is idempotently reused."""
    init_schema(conn)
    fields = {
        "subject_id": subject_id,
        "capability": capability,
        "provider": provider,
        "source_ref": source_ref,
        "observed_at": observed_at,
    }
    normalized = {name: _required_string({name: value}, name) for name, value in fields.items()}
    body = {
        "schema": OBSERVATION_SCHEMA,
        **normalized,
        "run_id": str(run_id or "").strip() or None,
        "as_of": str(as_of or "").strip() or None,
        "status": str(status or "available").strip(),
        "reason_code": str(reason_code or "").strip() or None,
        "payload": payload,
    }
    content_hash = digest(body)
    observation_id = "obs_" + content_hash[:24]
    stored_id, created = _insert_immutable(
        conn,
        table="observation",
        id_column="observation_id",
        record_id=observation_id,
        content_hash=content_hash,
        lookup_sql=(
            "SELECT observation_id,content_hash FROM ecosystem_observation_store "
            "WHERE subject_id=? AND capability=? AND provider=? AND source_ref=? AND content_hash=?"
        ),
        lookup_args=(
            normalized["subject_id"], normalized["capability"], normalized["provider"],
            normalized["source_ref"], content_hash,
        ),
        insert_sql=(
            "INSERT INTO ecosystem_observation_store "
            "(observation_id,subject_id,capability,provider,source_ref,observed_at,as_of,run_id,status,reason_code,content_hash,payload_json,created_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)"
        ),
        insert_args=(
            observation_id, normalized["subject_id"], normalized["capability"], normalized["provider"],
            normalized["source_ref"], normalized["observed_at"], body["as_of"], body["run_id"],
            body["status"], body["reason_code"], content_hash, canonical_json(body), now(),
        ),
    )
    information_registry.sync_observation(
        conn, {**body, "observation_id": stored_id, "content_hash": content_hash}
    )
    conn.commit()
    return {**body, "observation_id": stored_id, "content_hash": content_hash}, created


def _read_input(conn: sqlite3.Connection, input_kind: str, input_id: str) -> str:
    if input_kind == "observation":
        row = conn.execute(
            "SELECT content_hash FROM ecosystem_observation_store WHERE observation_id=?", (input_id,)
        ).fetchone()
    elif input_kind == "packet":
        row = conn.execute(
            "SELECT content_hash FROM ecosystem_evidence_packet_store WHERE packet_id=?", (input_id,)
        ).fetchone()
    else:
        raise ControlPlaneError(f"Unsupported packet input kind: {input_kind}")
    if not row:
        raise ControlPlaneError(f"Unknown {input_kind} input: {input_id}")
    return str(row["content_hash"])


def build_packet(
    conn: sqlite3.Connection,
    *,
    kind: str,
    subject_id: str,
    run_id: str,
    producer_name: str,
    producer_version: str,
    payload: dict[str, Any],
    inputs: Iterable[tuple[str, str]] = (),
    as_of: str | None = None,
    quality_state: str = "ready",
) -> tuple[dict[str, Any], bool]:
    """Publish an immutable packet whose inputs are verified references."""
    init_schema(conn)
    values = {
        "kind": kind,
        "subject_id": subject_id,
        "run_id": run_id,
        "producer_name": producer_name,
        "producer_version": producer_version,
    }
    normalized = {name: _required_string({name: value}, name) for name, value in values.items()}
    input_refs = []
    for input_kind, input_id in inputs:
        clean_kind = _required_string({"input_kind": input_kind}, "input_kind")
        clean_id = _required_string({"input_id": input_id}, "input_id")
        input_refs.append({"kind": clean_kind, "id": clean_id, "content_hash": _read_input(conn, clean_kind, clean_id)})
    input_refs.sort(key=lambda item: (item["kind"], item["id"]))
    body = {
        "schema": PACKET_SCHEMA,
        **normalized,
        "as_of": str(as_of or "").strip() or None,
        "quality_state": str(quality_state or "ready").strip(),
        "inputs": input_refs,
        "payload": payload,
    }
    content_hash = digest(body)
    packet_id = "pkt_" + content_hash[:24]
    stored_id, created = _insert_immutable(
        conn,
        table="packet",
        id_column="packet_id",
        record_id=packet_id,
        content_hash=content_hash,
        lookup_sql=(
            "SELECT packet_id,content_hash FROM ecosystem_evidence_packet_store "
            "WHERE kind=? AND subject_id=? AND run_id=? AND content_hash=?"
        ),
        lookup_args=(normalized["kind"], normalized["subject_id"], normalized["run_id"], content_hash),
        insert_sql=(
            "INSERT INTO ecosystem_evidence_packet_store "
            "(packet_id,kind,subject_id,run_id,as_of,producer_name,producer_version,quality_state,content_hash,payload_json,created_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?)"
        ),
        insert_args=(
            packet_id, normalized["kind"], normalized["subject_id"], normalized["run_id"], body["as_of"],
            normalized["producer_name"], normalized["producer_version"], body["quality_state"],
            content_hash, canonical_json(body), now(),
        ),
    )
    if created:
        conn.executemany(
            "INSERT INTO ecosystem_packet_input(packet_id,input_kind,input_id,input_hash) VALUES(?,?,?,?)",
            [(packet_id, item["kind"], item["id"], item["content_hash"]) for item in input_refs],
        )
    information_registry.sync_packet(
        conn, {**body, "packet_id": stored_id, "content_hash": content_hash}
    )
    conn.commit()
    return {**body, "packet_id": stored_id, "content_hash": content_hash}, created


def input_fingerprint(
    *,
    stage: str,
    subject_id: str,
    inputs: Iterable[tuple[str, str]],
    config: dict[str, Any],
    implementation_version: str,
) -> str:
    return digest(
        {
            "stage": stage,
            "subject_id": subject_id,
            "inputs": sorted((str(kind), str(record_id)) for kind, record_id in inputs),
            "config": config,
            "implementation_version": implementation_version,
        }
    )


def acquire_checkpoint(
    conn: sqlite3.Connection,
    *,
    run_id: str,
    stage: str,
    subject_id: str,
    fingerprint: str,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return a reusable completion or acquire a new planned checkpoint."""
    init_schema(conn)
    for field, value in {"run_id": run_id, "stage": stage, "subject_id": subject_id, "fingerprint": fingerprint}.items():
        _required_string({field: value}, field)
    row = conn.execute(
        "SELECT * FROM ecosystem_workflow_checkpoint WHERE run_id=? AND stage=? AND subject_id=? AND input_fingerprint=?",
        (run_id, stage, subject_id, fingerprint),
    ).fetchone()
    if row:
        return {"status": "reused" if row["state"] == "completed" else str(row["state"]), **dict(row)}
    checkpoint_id = "chk_" + digest([run_id, stage, subject_id, fingerprint])[:24]
    conn.execute(
        "INSERT INTO ecosystem_workflow_checkpoint "
        "(checkpoint_id,run_id,stage,subject_id,input_fingerprint,state,output_packet_id,details_json,created_at,completed_at) "
        "VALUES(?,?,?,?,?,'planned',NULL,?,?,NULL)",
        (checkpoint_id, run_id, stage, subject_id, fingerprint, canonical_json(details or {}), now()),
    )
    information_registry.record_lineage_event(
        conn, event_type="START", job_id=stage, run_id=run_id,
        subject_id=subject_id, producer="research_control_plane",
        code_version=str((details or {}).get("implementation_version") or "").strip() or None,
        facets={"checkpoint_id": checkpoint_id, "input_fingerprint": fingerprint, "details": details or {}},
    )
    conn.commit()
    return {
        "status": "planned", "checkpoint_id": checkpoint_id, "run_id": run_id, "stage": stage,
        "subject_id": subject_id, "input_fingerprint": fingerprint, "state": "planned",
    }


def complete_checkpoint(conn: sqlite3.Connection, checkpoint_id: str, packet: dict[str, Any]) -> dict[str, Any]:
    """Bind a completed checkpoint to a packet from the same run and subject."""
    row = conn.execute(
        "SELECT * FROM ecosystem_workflow_checkpoint WHERE checkpoint_id=?", (checkpoint_id,)
    ).fetchone()
    if not row:
        raise ControlPlaneError(f"Unknown checkpoint: {checkpoint_id}")
    if str(packet.get("run_id")) != str(row["run_id"]) or str(packet.get("subject_id")) != str(row["subject_id"]):
        raise ControlPlaneError("Checkpoint output packet has incompatible run_id or subject_id")
    if str(row["state"]) == "completed":
        if str(row["output_packet_id"]) != str(packet.get("packet_id")):
            raise ControlPlaneError("Completed checkpoint cannot be rebound to another packet")
        return {"status": "reused", **dict(row)}
    conn.execute(
        "UPDATE ecosystem_workflow_checkpoint SET state='completed',output_packet_id=?,completed_at=? WHERE checkpoint_id=?",
        (packet["packet_id"], now(), checkpoint_id),
    )
    information_registry.record_lineage_event(
        conn, event_type="COMPLETE", job_id=str(row["stage"]), run_id=str(row["run_id"]),
        subject_id=str(row["subject_id"]), producer="research_control_plane",
        outputs=[{"kind": "packet", "id": packet["packet_id"], "content_hash": packet.get("content_hash")}],
        facets={"checkpoint_id": checkpoint_id, "input_fingerprint": row["input_fingerprint"]},
    )
    conn.commit()
    return {"status": "completed", "checkpoint_id": checkpoint_id, "output_packet_id": packet["packet_id"]}


def record_evidence_gap(
    conn: sqlite3.Connection,
    *,
    subject_id: str,
    field: str,
    reason_code: str,
    owner: str,
    payload: dict[str, Any],
    run_id: str | None = None,
    source_event_id: str | None = None,
) -> tuple[dict[str, Any], bool]:
    """Persist a non-silent evidence gap for a blocked or incomplete input."""
    init_schema(conn)
    body = {
        "schema": GAP_SCHEMA,
        "subject_id": _required_string({"subject_id": subject_id}, "subject_id"),
        "run_id": str(run_id or "").strip() or None,
        "field": _required_string({"field": field}, "field"),
        "reason_code": _required_string({"reason_code": reason_code}, "reason_code"),
        "owner": _required_string({"owner": owner}, "owner"),
        "source_event_id": str(source_event_id or "").strip() or None,
        "payload": payload,
    }
    gap_id = "gap_" + digest(body)[:24]
    existing = conn.execute(
        "SELECT gap_id FROM ecosystem_control_plane_gap WHERE subject_id=? AND run_id IS ? AND field=? AND reason_code=? AND source_event_id IS ?",
        (body["subject_id"], body["run_id"], body["field"], body["reason_code"], body["source_event_id"]),
    ).fetchone()
    if existing:
        return {**body, "gap_id": str(existing["gap_id"]), "status": "open"}, False
    conn.execute(
        "INSERT INTO ecosystem_control_plane_gap "
        "(gap_id,subject_id,run_id,field,reason_code,owner,status,source_event_id,payload_json,created_at,resolved_at) "
        "VALUES(?,?,?,?,?,?, 'open', ?,?,?,NULL)",
        (gap_id, body["subject_id"], body["run_id"], body["field"], body["reason_code"], body["owner"],
         body["source_event_id"], canonical_json(body), now()),
    )
    conn.commit()
    return {**body, "gap_id": gap_id, "status": "open"}, True


def record_asset_check(
    conn: sqlite3.Connection,
    *,
    packet_id: str,
    asset_key: str,
    check_name: str,
    passed: bool,
    severity: str,
    metadata: dict[str, Any],
) -> dict[str, Any]:
    """Store the latest deterministic quality result for one immutable packet."""
    init_schema(conn)
    packet_id = _required_string({"packet_id": packet_id}, "packet_id")
    asset_key = _required_string({"asset_key": asset_key}, "asset_key")
    check_name = _required_string({"check_name": check_name}, "check_name")
    severity = str(severity or "error").strip().lower()
    if severity not in {"warning", "error"}:
        raise ControlPlaneError(f"Unsupported asset-check severity: {severity}")
    if not conn.execute(
        "SELECT 1 FROM ecosystem_evidence_packet_store WHERE packet_id=?", (packet_id,)
    ).fetchone():
        raise ControlPlaneError(f"Unknown packet for asset check: {packet_id}")
    body = {
        "schema": ASSET_CHECK_SCHEMA,
        "packet_id": packet_id,
        "asset_key": asset_key,
        "check_name": check_name,
        "status": "passed" if passed else "failed",
        "severity": severity,
        "metadata": metadata,
    }
    check_id = "check_" + digest(body)[:24]
    conn.execute(
        "INSERT INTO ecosystem_asset_check_result "
        "(check_id,packet_id,asset_key,check_name,status,severity,metadata_json,evaluated_at) "
        "VALUES(?,?,?,?,?,?,?,?) "
        "ON CONFLICT(packet_id,check_name) DO UPDATE SET "
        "check_id=excluded.check_id,asset_key=excluded.asset_key,status=excluded.status,"
        "severity=excluded.severity,metadata_json=excluded.metadata_json,evaluated_at=excluded.evaluated_at",
        (
            check_id, packet_id, asset_key, check_name, body["status"], severity,
            canonical_json(body), now(),
        ),
    )
    conn.commit()
    return {**body, "check_id": check_id}


def register_subject_alias(
    conn: sqlite3.Connection, *, alias_subject_id: str, canonical_subject_id: str,
    alias_kind: str, source: str,
) -> dict[str, Any]:
    """Bind a listing/instrument alias to one canonical issuer identity."""
    init_schema(conn)
    body = {
        "schema": SUBJECT_ALIAS_SCHEMA,
        "alias_subject_id": _required_string({"alias_subject_id": alias_subject_id}, "alias_subject_id"),
        "canonical_subject_id": _required_string({"canonical_subject_id": canonical_subject_id}, "canonical_subject_id"),
        "alias_kind": _required_string({"alias_kind": alias_kind}, "alias_kind"),
        "source": _required_string({"source": source}, "source"),
    }
    content_hash = digest(body)
    previous = conn.execute(
        "SELECT canonical_subject_id,content_hash FROM ecosystem_subject_alias WHERE alias_subject_id=?",
        (body["alias_subject_id"],),
    ).fetchone()
    if previous and str(previous["canonical_subject_id"]) != body["canonical_subject_id"]:
        raise ControlPlaneError(f"Subject alias conflict for {body['alias_subject_id']}")
    conn.execute(
        "INSERT OR IGNORE INTO ecosystem_subject_alias "
        "(alias_subject_id,canonical_subject_id,alias_kind,source,content_hash,created_at) VALUES(?,?,?,?,?,?)",
        (body["alias_subject_id"], body["canonical_subject_id"], body["alias_kind"], body["source"], content_hash, now()),
    )
    conn.commit()
    return {**body, "content_hash": content_hash}


def canonical_subject(conn: sqlite3.Connection, subject_id: str) -> str:
    init_schema(conn)
    value = _required_string({"subject_id": subject_id}, "subject_id")
    row = conn.execute(
        "SELECT canonical_subject_id FROM ecosystem_subject_alias WHERE alias_subject_id=?", (value,)
    ).fetchone()
    return str(row[0]) if row else value


def set_packet_freshness(
    conn: sqlite3.Connection, *, packet_id: str, knowledge_date: str | None,
    stale_after: str | None, must_refresh_if: Iterable[str] = (),
    freshness_state: str = "current",
) -> dict[str, Any]:
    """Attach an explicit time boundary to an immutable packet."""
    init_schema(conn)
    packet_id = _required_string({"packet_id": packet_id}, "packet_id")
    if not conn.execute("SELECT 1 FROM ecosystem_evidence_packet_store WHERE packet_id=?", (packet_id,)).fetchone():
        raise ControlPlaneError(f"Unknown packet for freshness policy: {packet_id}")
    triggers = sorted({str(value).strip() for value in must_refresh_if if str(value).strip()})
    body = {
        "schema": FRESHNESS_SCHEMA, "packet_id": packet_id,
        "knowledge_date": str(knowledge_date or "").strip() or None,
        "stale_after": str(stale_after or "").strip() or None,
        "must_refresh_if": triggers,
        "freshness_state": _required_string({"freshness_state": freshness_state}, "freshness_state"),
    }
    content_hash = digest(body)
    conn.execute(
        "INSERT INTO ecosystem_packet_freshness VALUES(?,?,?,?,?,?,?) "
        "ON CONFLICT(packet_id) DO UPDATE SET knowledge_date=excluded.knowledge_date,"
        "stale_after=excluded.stale_after,must_refresh_if_json=excluded.must_refresh_if_json,"
        "freshness_state=excluded.freshness_state,content_hash=excluded.content_hash,evaluated_at=excluded.evaluated_at",
        (packet_id, body["knowledge_date"], body["stale_after"], canonical_json(triggers),
         body["freshness_state"], content_hash, now()),
    )
    if body["freshness_state"] in {"stale", "current"}:
        packet_urn = information_registry.object_urn_for_source(conn, "packet", packet_id)
        if packet_urn:
            information_registry.record_information_state(
                conn, information_urn=packet_urn,
                lifecycle_state="stale" if body["freshness_state"] == "stale" else "published",
                reason=f"packet freshness: {body['freshness_state']}",
                changed_by="research_control_plane",
            )
    conn.commit()
    return {**body, "content_hash": content_hash}


def resolve_evidence_gap(
    conn: sqlite3.Connection, *, gap_id: str, resolving_input_kind: str,
    resolving_input_id: str, reason: str,
) -> dict[str, Any]:
    """Resolve a shared gap only through a verified immutable input."""
    init_schema(conn)
    gap = conn.execute("SELECT status FROM ecosystem_control_plane_gap WHERE gap_id=?", (gap_id,)).fetchone()
    if not gap:
        raise ControlPlaneError(f"Unknown evidence gap: {gap_id}")
    input_hash = _read_input(conn, resolving_input_kind, resolving_input_id)
    body = {
        "schema": GAP_RESOLUTION_SCHEMA, "gap_id": gap_id,
        "resolving_input_kind": resolving_input_kind, "resolving_input_id": resolving_input_id,
        "resolving_input_hash": input_hash, "previous_status": str(gap["status"]),
        "new_status": "resolved", "reason": _required_string({"reason": reason}, "reason"),
    }
    content_hash = digest(body)
    resolution_id = "resolution_" + content_hash[:24]
    conn.execute(
        "INSERT OR IGNORE INTO ecosystem_gap_resolution_event "
        "(resolution_id,gap_id,resolving_input_kind,resolving_input_id,resolving_input_hash,"
        "previous_status,new_status,reason,content_hash,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
        (resolution_id, gap_id, resolving_input_kind, resolving_input_id, input_hash,
         body["previous_status"], "resolved", body["reason"], content_hash, now()),
    )
    conn.execute(
        "UPDATE ecosystem_control_plane_gap SET status='resolved',resolved_at=COALESCE(resolved_at,?) WHERE gap_id=?",
        (now(), gap_id),
    )
    conn.commit()
    return {**body, "resolution_id": resolution_id, "content_hash": content_hash}


def record_usage_event(
    conn: sqlite3.Connection, *, run_id: str, stage: str, subject_id: str | None = None,
    provider: str | None = None, model: str | None = None, api_calls: int = 0,
    input_tokens: int = 0, output_tokens: int = 0, estimated_cost_usd: float = 0.0,
    latency_ms: float = 0.0, cache_status: str = "not_applicable",
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    init_schema(conn)
    body = {
        "schema": USAGE_SCHEMA, "run_id": _required_string({"run_id": run_id}, "run_id"),
        "stage": _required_string({"stage": stage}, "stage"),
        "subject_id": str(subject_id or "").strip() or None,
        "provider": str(provider or "").strip() or None,
        "model": str(model or "").strip() or None,
        "api_calls": max(0, int(api_calls)), "input_tokens": max(0, int(input_tokens)),
        "output_tokens": max(0, int(output_tokens)),
        "estimated_cost_usd": max(0.0, float(estimated_cost_usd)),
        "latency_ms": max(0.0, float(latency_ms)),
        "cache_status": _required_string({"cache_status": cache_status}, "cache_status"),
        "metadata": metadata or {},
    }
    content_hash = digest(body)
    event_id = "usage_" + content_hash[:24]
    conn.execute(
        "INSERT OR IGNORE INTO ecosystem_usage_event VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (event_id, body["run_id"], body["stage"], body["subject_id"], body["provider"], body["model"],
         body["api_calls"], body["input_tokens"], body["output_tokens"], body["estimated_cost_usd"],
         body["latency_ms"], body["cache_status"], canonical_json(body["metadata"]), content_hash, now()),
    )
    conn.commit()
    return {**body, "event_id": event_id, "content_hash": content_hash}


def record_claim(
    conn: sqlite3.Connection, *, canonical_subject_id: str, run_id: str,
    claim_type: str, claim_text: str, verification_status: str, confidence: str,
    evidence_inputs: Iterable[tuple[str, str, str]], as_of: str | None = None,
    category_ids: Iterable[str] = (), predicate: str | None = None,
    object_value: Any = None, valid_from: str | None = None,
    valid_to: str | None = None, source_locator: dict[str, Any] | None = None,
    owner_id: str = "research_synthesizer",
) -> dict[str, Any]:
    """Persist one checkable claim and its supporting/contradicting evidence links."""
    init_schema(conn)
    category_ids = tuple(category_ids)
    if not category_ids and claim_type not in information_registry.architecture().get("claim_category_defaults", {}):
        raise ControlPlaneError(
            f"Claim type {claim_type!r} needs an approved information category"
        )
    links = []
    for input_kind, input_id, stance in evidence_inputs:
        if stance not in {"supporting", "contradicting", "context"}:
            raise ControlPlaneError(f"Unsupported claim-evidence stance: {stance}")
        links.append({"kind": input_kind, "id": input_id, "hash": _read_input(conn, input_kind, input_id), "stance": stance})
    links.sort(key=lambda item: (item["kind"], item["id"], item["stance"]))
    body = {
        "schema": CLAIM_SCHEMA,
        "canonical_subject_id": _required_string({"canonical_subject_id": canonical_subject_id}, "canonical_subject_id"),
        "run_id": _required_string({"run_id": run_id}, "run_id"),
        "claim_type": _required_string({"claim_type": claim_type}, "claim_type"),
        "claim_text": _required_string({"claim_text": claim_text}, "claim_text"),
        "verification_status": _required_string({"verification_status": verification_status}, "verification_status"),
        "confidence": _required_string({"confidence": confidence}, "confidence"),
        "as_of": str(as_of or "").strip() or None, "evidence": links,
    }
    content_hash = digest(body)
    claim_id = "claim_" + content_hash[:24]
    semantics = {
        "category_ids": sorted({str(value).strip() for value in category_ids if str(value).strip()}),
        "predicate": str(predicate or "").strip() or None,
        "object_value": object_value,
        "valid_from": str(valid_from or "").strip() or None,
        "valid_to": str(valid_to or "").strip() or None,
        "source_locator": source_locator or {},
        "owner_id": _required_string({"owner_id": owner_id}, "owner_id"),
    }
    semantics_hash = digest(semantics)
    previous_semantics = conn.execute(
        "SELECT content_hash FROM ecosystem_claim_semantics WHERE claim_id=?", (claim_id,),
    ).fetchone()
    if previous_semantics and previous_semantics[0] != semantics_hash:
        raise ControlPlaneError(f"Immutable claim semantics conflict for {claim_id}")
    conn.execute(
        "INSERT OR IGNORE INTO ecosystem_claim_store VALUES(?,?,?,?,?,?,?,?,?,?)",
        (claim_id, body["canonical_subject_id"], body["run_id"], body["claim_type"],
         body["claim_text"], body["verification_status"], body["confidence"], body["as_of"], content_hash, now()),
    )
    conn.execute(
        "INSERT OR IGNORE INTO ecosystem_claim_semantics VALUES(?,?,?,?,?,?,?,?,?)",
        (claim_id, canonical_json(semantics["category_ids"]), semantics["predicate"],
         canonical_json(object_value) if object_value is not None else None,
         semantics["valid_from"], semantics["valid_to"],
         canonical_json(semantics["source_locator"]), semantics["owner_id"], semantics_hash),
    )
    conn.executemany(
        "INSERT OR IGNORE INTO ecosystem_claim_evidence_link VALUES(?,?,?,?,?)",
        [(claim_id, item["kind"], item["id"], item["hash"], item["stance"]) for item in links],
    )
    information_registry.sync_claim(
        conn, {**body, "claim_id": claim_id, "content_hash": content_hash, "owner_id": owner_id},
        category_ids=category_ids, predicate=predicate, object_value=object_value,
        valid_from=valid_from, valid_to=valid_to, source_locator=source_locator,
    )
    conn.commit()
    return {**body, "claim_id": claim_id, "content_hash": content_hash}


def record_thesis_revision(
    conn: sqlite3.Connection, *, canonical_subject_id: str, run_id: str,
    thesis_state: str, thesis_text: str, supporting_claim_ids: Iterable[str],
    disconfirming_claim_ids: Iterable[str] = (), expectation_map: dict[str, Any] | None = None,
    stale_after: str | None = None, must_refresh_if: Iterable[str] = (),
) -> dict[str, Any]:
    """Append a durable thesis revision; previous revisions are never overwritten."""
    init_schema(conn)
    subject = _required_string({"canonical_subject_id": canonical_subject_id}, "canonical_subject_id")
    supporting = sorted(set(supporting_claim_ids)); disconfirming = sorted(set(disconfirming_claim_ids))
    known = {row[0] for row in conn.execute(
        "SELECT claim_id FROM ecosystem_claim_store WHERE claim_id IN (%s)" % ",".join("?" for _ in supporting + disconfirming),
        tuple(supporting + disconfirming),
    )} if supporting or disconfirming else set()
    missing = sorted(set(supporting + disconfirming) - known)
    if missing:
        raise ControlPlaneError(f"Unknown thesis claim IDs: {missing}")
    previous = conn.execute(
        "SELECT COALESCE(MAX(revision),0) FROM ecosystem_thesis_revision WHERE canonical_subject_id=?", (subject,)
    ).fetchone()[0]
    revision = int(previous) + 1
    triggers = sorted({str(value).strip() for value in must_refresh_if if str(value).strip()})
    body = {
        "schema": THESIS_SCHEMA, "canonical_subject_id": subject,
        "run_id": _required_string({"run_id": run_id}, "run_id"), "revision": revision,
        "thesis_state": _required_string({"thesis_state": thesis_state}, "thesis_state"),
        "thesis_text": _required_string({"thesis_text": thesis_text}, "thesis_text"),
        "supporting_claim_ids": supporting, "disconfirming_claim_ids": disconfirming,
        "expectation_map": expectation_map or {}, "stale_after": str(stale_after or "").strip() or None,
        "must_refresh_if": triggers,
    }
    content_hash = digest(body)
    revision_id = "thesis_" + content_hash[:24]
    conn.execute(
        "INSERT INTO ecosystem_thesis_revision VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (revision_id, subject, body["run_id"], revision, body["thesis_state"], body["thesis_text"],
         canonical_json(supporting), canonical_json(disconfirming), canonical_json(body["expectation_map"]),
         body["stale_after"], canonical_json(triggers), content_hash, now()),
    )
    information_registry.sync_thesis(
        conn, {**body, "thesis_revision_id": revision_id, "content_hash": content_hash}
    )
    conn.commit()
    return {**body, "thesis_revision_id": revision_id, "content_hash": content_hash}
