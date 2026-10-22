"""Local asset graph, lineage inspection and deterministic packet quality checks.

The module is the framework-neutral layer beneath the optional Dagster adapter.
It reads the existing control-plane packet store and never calls a provider,
an LLM, or a broker.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
from pathlib import Path
import sqlite3
from typing import Any

from control_plane_store import DEFAULT_CONTROL_PLANE_DB, connect
import research_control_plane


@dataclass(frozen=True)
class AssetSpec:
    key: str
    packet_kind: str
    upstream: tuple[str, ...]
    required_packet_inputs: tuple[str, ...] = ()
    scope: str = "subject"


ASSETS: tuple[AssetSpec, ...] = (
    AssetSpec("company_profile", "company_profile", ()),
    AssetSpec(
        "sector_context", "sector_context", ("company_profile",),
        required_packet_inputs=("company_profile",),
    ),
    AssetSpec(
        "financial_evidence", "financial_evidence", ("company_profile",),
        required_packet_inputs=("company_profile",),
    ),
    AssetSpec(
        "fundamental_analysis", "fundamental_analysis", ("financial_evidence",),
        required_packet_inputs=("financial_evidence",),
    ),
    AssetSpec(
        "fundamental_research_packet", "fundamental_research_packet", (), scope="run",
    ),
)
ASSET_BY_KEY = {asset.key: asset for asset in ASSETS}
ASSET_BY_KIND = {asset.packet_kind: asset for asset in ASSETS}


def _decode(row: sqlite3.Row) -> dict[str, Any]:
    body = json.loads(row["payload_json"])
    return {
        "packet_id": row["packet_id"], "kind": row["kind"], "subject_id": row["subject_id"],
        "run_id": row["run_id"], "as_of": row["as_of"], "producer_name": row["producer_name"],
        "producer_version": row["producer_version"], "quality_state": row["quality_state"],
        "content_hash": row["content_hash"], "created_at": row["created_at"], "body": body,
    }


def latest_packet(
    db: sqlite3.Connection, packet_kind: str, *, subject_id: str | None = None,
    run_id: str | None = None,
) -> dict[str, Any] | None:
    clauses, values = ["kind=?"], [packet_kind]
    if subject_id:
        clauses.append("subject_id=?"); values.append(subject_id)
    if run_id:
        clauses.append("run_id=?"); values.append(run_id)
    row = db.execute(
        "SELECT * FROM ecosystem_evidence_packet_store WHERE " + " AND ".join(clauses)
        + " ORDER BY created_at DESC,packet_id DESC LIMIT 1", values,
    ).fetchone()
    return _decode(row) if row else None


def _packet_inputs(db: sqlite3.Connection, packet_id: str) -> list[dict[str, Any]]:
    rows = db.execute(
        "SELECT i.input_kind,i.input_id,i.input_hash,p.kind AS packet_kind "
        "FROM ecosystem_packet_input i LEFT JOIN ecosystem_evidence_packet_store p "
        "ON i.input_kind='packet' AND i.input_id=p.packet_id "
        "WHERE i.packet_id=? ORDER BY i.input_kind,i.input_id", (packet_id,),
    ).fetchall()
    return [dict(row) for row in rows]


def evaluate_packet(
    db: sqlite3.Connection, packet: dict[str, Any], *, persist: bool = True,
) -> list[dict[str, Any]]:
    """Evaluate the portable packet contract and asset-specific handoff rules."""
    spec = ASSET_BY_KIND.get(packet["kind"], AssetSpec(packet["kind"], packet["kind"], ()))
    body = packet["body"]
    inputs = _packet_inputs(db, packet["packet_id"])
    packet_input_kinds = {str(item.get("packet_kind")) for item in inputs if item.get("packet_kind")}
    packet_subjects = []
    for item in inputs:
        if item.get("input_kind") != "packet":
            continue
        row = db.execute(
            "SELECT subject_id FROM ecosystem_evidence_packet_store WHERE packet_id=?", (item["input_id"],)
        ).fetchone()
        if row:
            packet_subjects.append(research_control_plane.canonical_subject(db, str(row[0])))
    canonical_subject = research_control_plane.canonical_subject(db, packet["subject_id"])
    freshness = db.execute(
        "SELECT knowledge_date,stale_after,freshness_state FROM ecosystem_packet_freshness WHERE packet_id=?",
        (packet["packet_id"],),
    ).fetchone()
    open_gaps = db.execute(
        "SELECT COUNT(*) FROM ecosystem_control_plane_gap "
        "WHERE subject_id=? AND run_id IS ? AND status='open'",
        (packet["subject_id"], packet["run_id"]),
    ).fetchone()[0]
    checks = [
        {
            "name": "envelope_contract", "passed": bool(
                body.get("schema") == research_control_plane.PACKET_SCHEMA
                and body.get("kind") == packet["kind"]
                and body.get("subject_id") == packet["subject_id"]
                and body.get("run_id") == packet["run_id"]
                and body.get("producer_name") == packet["producer_name"]
            ), "severity": "error",
            "metadata": {"schema": body.get("schema"), "content_hash": packet["content_hash"]},
        },
        {
            "name": "source_lineage", "passed": bool(inputs), "severity": "error",
            "metadata": {"input_count": len(inputs), "inputs": inputs},
        },
        {
            "name": "quality_state", "passed": packet["quality_state"] == "ready",
            "severity": "warning", "metadata": {"quality_state": packet["quality_state"]},
        },
        {
            "name": "open_evidence_gaps", "passed": open_gaps == 0, "severity": "warning",
            "metadata": {"open_gap_count": open_gaps},
        },
        {
            "name": "canonical_subject_lineage",
            "passed": spec.scope == "run" or all(value == canonical_subject for value in packet_subjects),
            "severity": "error",
            "metadata": {"canonical_subject": canonical_subject, "upstream_subjects": packet_subjects},
        },
        {
            "name": "freshness_policy", "passed": freshness is not None, "severity": "warning",
            "metadata": dict(freshness) if freshness else {"reason": "missing_freshness_policy"},
        },
    ]
    for required_kind in spec.required_packet_inputs:
        checks.append({
            "name": f"upstream_{required_kind}", "passed": required_kind in packet_input_kinds,
            "severity": "error", "metadata": {"packet_input_kinds": sorted(packet_input_kinds)},
        })
    results = []
    for check in checks:
        result = {
            "asset_key": spec.key, "packet_id": packet["packet_id"],
            "check_name": check["name"], "status": "passed" if check["passed"] else "failed",
            "severity": check["severity"], "metadata": check["metadata"],
        }
        if persist:
            stored = research_control_plane.record_asset_check(
                db, packet_id=packet["packet_id"], asset_key=spec.key,
                check_name=check["name"], passed=check["passed"],
                severity=check["severity"], metadata=check["metadata"],
            )
            result["check_id"] = stored["check_id"]
        results.append(result)
    return results


def graph_status(
    db: sqlite3.Connection, *, subject_id: str | None = None,
    run_id: str | None = None, persist_checks: bool = True,
) -> dict[str, Any]:
    nodes = []
    for spec in ASSETS:
        packet = latest_packet(db, spec.packet_kind, subject_id=subject_id, run_id=run_id)
        if not packet:
            nodes.append({
                "asset_key": spec.key, "packet_kind": spec.packet_kind, "status": "missing",
                "upstream": list(spec.upstream), "packet_id": None, "checks": [],
            })
            continue
        checks = evaluate_packet(db, packet, persist=persist_checks)
        error_failed = any(c["status"] == "failed" and c["severity"] == "error" for c in checks)
        warning_failed = any(c["status"] == "failed" and c["severity"] == "warning" for c in checks)
        status = "blocked" if error_failed else "degraded" if warning_failed else "healthy"
        nodes.append({
            "asset_key": spec.key, "packet_kind": spec.packet_kind, "status": status,
            "upstream": list(spec.upstream), "scope": spec.scope, "packet_id": packet["packet_id"],
            "subject_id": packet["subject_id"], "run_id": packet["run_id"],
            "quality_state": packet["quality_state"], "as_of": packet["as_of"],
            "content_hash": packet["content_hash"], "checks": checks,
        })
    selected_packet_keys = {
        node["packet_id"]: node["asset_key"]
        for node in nodes if node.get("packet_id")
    }
    actual_edges = set()
    for node in nodes:
        packet_id = node.get("packet_id")
        if not packet_id:
            continue
        for item in _packet_inputs(db, packet_id):
            upstream = selected_packet_keys.get(item["input_id"])
            if item["input_kind"] == "packet" and upstream:
                actual_edges.add((upstream, node["asset_key"]))
    edges = [
        {"from": upstream, "to": downstream}
        for upstream, downstream in sorted(actual_edges)
    ]
    return {
        "schema": "ecosystem.research_asset_graph.v1", "database": "local_control_plane",
        "filters": {"subject_id": subject_id, "run_id": run_id}, "nodes": nodes, "edges": edges,
        "summary": {
            status: sum(node["status"] == status for node in nodes)
            for status in ("healthy", "degraded", "blocked", "missing")
        },
    }


def packet_lineage(db: sqlite3.Connection, packet_id: str) -> dict[str, Any]:
    """Return recursive packet ancestry plus terminal source observations."""
    seen_packets: set[str] = set()
    packets: list[dict[str, Any]] = []
    observations: list[dict[str, Any]] = []

    def visit(current: str) -> None:
        if current in seen_packets:
            return
        row = db.execute(
            "SELECT * FROM ecosystem_evidence_packet_store WHERE packet_id=?", (current,)
        ).fetchone()
        if not row:
            raise ValueError(f"Unknown packet: {current}")
        seen_packets.add(current)
        packet = _decode(row)
        packet["inputs"] = _packet_inputs(db, current)
        packets.append({key: value for key, value in packet.items() if key != "body"})
        for item in packet["inputs"]:
            if item["input_kind"] == "packet":
                visit(item["input_id"])
            elif item["input_kind"] == "observation":
                source = db.execute(
                    "SELECT observation_id,subject_id,capability,provider,source_ref,observed_at,as_of,"
                    "run_id,status,reason_code,content_hash FROM ecosystem_observation_store WHERE observation_id=?",
                    (item["input_id"],),
                ).fetchone()
                if source:
                    observations.append(dict(source))

    visit(packet_id)
    return {
        "schema": "ecosystem.packet_lineage.v1", "root_packet_id": packet_id,
        "packets": packets, "observations": observations,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_CONTROL_PLANE_DB)
    sub = parser.add_subparsers(dest="command", required=True)
    status = sub.add_parser("status")
    status.add_argument("--subject-id"); status.add_argument("--run-id")
    check = sub.add_parser("check")
    check.add_argument("--subject-id"); check.add_argument("--run-id")
    lineage = sub.add_parser("lineage"); lineage.add_argument("packet_id")
    args = parser.parse_args(argv)
    db = connect(args.db)
    try:
        if args.command == "lineage":
            result = packet_lineage(db, args.packet_id)
        else:
            result = graph_status(
                db, subject_id=args.subject_id, run_id=args.run_id,
                persist_checks=args.command == "check",
            )
        print(json.dumps(result, ensure_ascii=False, indent=2))
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
