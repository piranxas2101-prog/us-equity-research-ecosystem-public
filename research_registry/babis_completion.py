"""Finish the Babis v6 problem run with evidence-backed writes or deferred notes.

No network, model or trading calls are made. Preparation is read-only. Apply is
hash-bound, reversible for classification changes, and preserves every
unresolved item as a production deferred note linked to its evidence gaps.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
from typing import Any

from .classification_governance import (
    POLICY_PATH, file_sha256, governance_schema, preflight_batch, read_json,
    validate_batch, write_json,
)
from .classification_problems import enrichment_schema, schema as problem_schema
from .core import DEFAULT_DB, PROJECT, connect, digest, taxonomy


DEFAULT_RUN_ID = "babis-sec-v6-20260916T120530578065Z"
DEFAULT_JOB_ID = "babis-enrichment-v1-20260916T131125967781Z"
DEFAULT_BATCH_ROOT = PROJECT / "data" / "classification_batches"
TARGET_ASSESSMENTS = {"corroborated_instrument_type", "corroborated_taxonomy_candidate"}
DEFERRED_ASSESSMENTS = {"supplemental_source_conflict", "insufficient_supplemental_evidence"}
SECTOR_ELIGIBLE_TYPES = {"equity_candidate", "adr_candidate", "common_stock", "ordinary_share"}


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _compatible(left: str | None, right: str | None) -> bool:
    return bool(left and right) and (
        left == right or left.startswith(right + ".") or right.startswith(left + ".")
    )


def infer_instrument_type(assessment: dict[str, Any]) -> str:
    signals = set(assessment.get("instrument_type_signals") or [])
    sources = {source.get("provider"): source for source in assessment.get("source_refs") or []}
    fmp = (sources.get("fmp") or {}).get("data") or {}
    polygon = (sources.get("polygon") or {}).get("data") or {}
    text = " ".join(str(value or "") for value in (
        fmp.get("companyName"), fmp.get("description"), polygon.get("sic_code"),
        polygon.get("sic_description"), polygon.get("description"),
    )).lower()
    if "fund_candidate" in signals:
        return "fund_candidate"
    if "adr_candidate" in signals:
        return "adr_candidate"
    if "blank check" in text or "special purpose acquisition" in text or str(polygon.get("sic_code")) == "6770":
        return "spac"
    if signals == {"equity_candidate"}:
        return "equity_candidate"
    if len(signals) == 1:
        candidate = next(iter(signals))
        allowed = set(json.loads(POLICY_PATH.read_text(encoding="utf-8"))["instrument_contracts"])
        if candidate in allowed:
            return candidate
    raise ValueError(f"Unresolved instrument signals for {assessment.get('ticker')}: {sorted(signals)}")


def completion_schema(db: sqlite3.Connection) -> None:
    db.executescript(
        """
        CREATE TABLE IF NOT EXISTS babis_completion_run(
          completion_id TEXT PRIMARY KEY,problem_run_id TEXT NOT NULL,enrichment_job_id TEXT NOT NULL,
          batch_id TEXT NOT NULL,manifest_sha256 TEXT NOT NULL,status TEXT NOT NULL,
          applied_count INTEGER NOT NULL,no_change_count INTEGER NOT NULL,deferred_count INTEGER NOT NULL,
          completed_at TEXT NOT NULL,summary_json TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS classification_problem_deferred_note(
          note_id TEXT PRIMARY KEY,problem_id TEXT NOT NULL UNIQUE,
          problem_run_id TEXT NOT NULL,enrichment_job_id TEXT NOT NULL,
          assessment TEXT NOT NULL,reason_code TEXT NOT NULL,
          evidence_gap_ids_json TEXT NOT NULL,followup_condition TEXT NOT NULL,
          payload_json TEXT NOT NULL,status TEXT NOT NULL,created_at TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS classification_problem_deferred_status
          ON classification_problem_deferred_note(status,assessment);
        """
    )


def _load_rows(db: sqlite3.Connection, run_id: str, job_id: str) -> list[dict[str, Any]]:
    rows = []
    for row in db.execute(
        "SELECT i.*,e.assessment,e.payload_json AS enrichment_json,"
        "v.instrument_type AS effective_type,v.node_id AS effective_node "
        "FROM classification_problem_item i "
        "JOIN classification_problem_enrichment e ON e.problem_id=i.problem_id AND e.job_id=? "
        "JOIN effective_listed_symbol v ON v.snapshot_id=i.snapshot_id "
        "AND v.venue_code=i.venue_code AND v.ticker=i.ticker "
        "WHERE i.run_id=? AND i.status='open' ORDER BY i.problem_id",
        (job_id, run_id),
    ):
        item = dict(row)
        item["enrichment"] = json.loads(item.pop("enrichment_json"))
        rows.append(item)
    return rows


def _validate_identity(payload: dict[str, Any]) -> None:
    expected = str(payload.get("expected_sec_cik") or "").lstrip("0")
    observed = {str(value).lstrip("0") for value in payload.get("observed_ciks") or [] if str(value).strip("0")}
    if expected and any(value != expected for value in observed):
        raise ValueError(f"Identity conflict for {payload.get('ticker')}")


def _change(item: dict[str, Any], run_id: str) -> dict[str, Any]:
    payload = item["enrichment"]
    _validate_identity(payload)
    evidence = [source for source in payload.get("source_refs") or [] if source.get("status") == "ok" and source.get("hash")]
    if not evidence:
        raise ValueError(f"No usable evidence for {item['ticker']}")
    new_type = item["effective_type"]
    new_node = item["effective_node"]
    assessment = item["assessment"]
    if assessment == "corroborated_taxonomy_candidate":
        new_node = payload.get("sec_proposed_node")
        if not new_node or not _compatible(new_node, payload.get("fmp_candidate_node")):
            raise ValueError(f"Taxonomy evidence no longer corroborates {item['ticker']}")
        if new_node.split(".")[0] != str(item["effective_node"] or new_node).split(".")[0] and item["effective_node"]:
            raise ValueError(f"Cross-sector completion is forbidden for {item['ticker']}")
    elif assessment == "corroborated_instrument_type":
        new_type = infer_instrument_type(payload)
        if new_type not in SECTOR_ELIGIBLE_TYPES:
            new_node = None
    else:
        raise ValueError(f"Unsupported completion assessment: {assessment}")
    evidence_hashes = sorted(source["hash"] for source in evidence)
    change_id = digest([
        run_id, item["problem_id"], item["effective_type"], new_type,
        item["effective_node"], new_node, evidence_hashes,
    ])
    changes_value = item["effective_type"] != new_type or item["effective_node"] != new_node
    return {
        "change_id": change_id, "problem_id": item["problem_id"],
        "snapshot_id": item["snapshot_id"], "venue": item.get("venue_code"),
        "venue_code": item["venue_code"], "ticker": item["ticker"], "name": item.get("name"),
        "old_node": item["effective_node"], "new_node": new_node,
        "old_instrument_type": item["effective_type"], "new_instrument_type": new_type,
        "source_status": "ok", "source_ref": {
            "provider": "sec_edgar+supplemental_cross_check",
            "observed_at": max(str(source.get("observed_at") or "") for source in evidence),
            "hash": digest(evidence_hashes),
            "details": {"evidence_hashes": evidence_hashes, "assessment": assessment},
        },
        "worker_status": item.get("worker_status"), "relation": payload.get("taxonomy_relation"),
        "basis": assessment, "disposition": "human_review",
        "disposition_reason": "owner_approved_babis_completion",
        "changes_value": changes_value,
    }


def _unsafe_cross_sector_candidate(item: dict[str, Any]) -> bool:
    if item["assessment"] != "corroborated_taxonomy_candidate":
        return False
    proposed = item["enrichment"].get("sec_proposed_node")
    current = item.get("effective_node")
    return bool(proposed and current and proposed.split(".")[0] != current.split(".")[0])


def prepare(db: sqlite3.Connection, run_id: str, job_id: str, out: Path | None = None) -> dict[str, Any]:
    problem_schema(db); enrichment_schema(db); governance_schema(db); completion_schema(db)
    run = db.execute("SELECT * FROM classification_problem_run WHERE run_id=?", (run_id,)).fetchone()
    if not run:
        raise ValueError("Unknown Babis problem run")
    rows = _load_rows(db, run_id, job_id)
    assessments = Counter(row["assessment"] for row in rows)
    unexpected = set(assessments) - TARGET_ASSESSMENTS - DEFERRED_ASSESSMENTS
    if unexpected:
        raise ValueError(f"Unrouted assessments: {sorted(unexpected)}")
    unsafe_cross_sector = [row for row in rows if _unsafe_cross_sector_candidate(row)]
    target_rows = [
        row for row in rows
        if row["assessment"] in TARGET_ASSESSMENTS and not _unsafe_cross_sector_candidate(row)
    ]
    deferred_rows = [
        row for row in rows
        if row["assessment"] in DEFERRED_ASSESSMENTS or _unsafe_cross_sector_candidate(row)
    ]
    changes = [_change(item, run_id) for item in target_rows]
    planned = [change for change in changes if change["changes_value"]]
    no_change = [change for change in changes if not change["changes_value"]]
    policy = read_json(POLICY_PATH)
    batch_id = f"classification-babis-completion-{run_id}-policy-{policy['version']}"
    out = Path(out or (DEFAULT_BATCH_ROOT / batch_id))
    if out.exists():
        raise FileExistsError(out)
    out.mkdir(parents=True)
    write_json(out / "changes.json", changes)
    write_json(out / "review_queue.json", changes)
    write_json(out / "planned_changes.json", planned)
    write_json(out / "review_groups.json", [{
        "assessment": key, "count": value
    } for key, value in sorted(assessments.items())])
    write_json(out / "deferred_items.json", [{
        "problem_id": row["problem_id"], "ticker": row["ticker"], "category": row["category"],
        "assessment": row["assessment"], "reason": (
            "provider_or_primary_evidence_not_currently_sufficient"
            if row["assessment"] == "insufficient_supplemental_evidence"
            else "supplemental_sources_conflict_requires_company_dossier"
            if row["assessment"] == "supplemental_source_conflict"
            else "cross_sector_candidate_requires_primary_company_dossier"
        ),
    } for row in deferred_rows])
    tax = taxonomy()[0]
    manifest = {
        "batch_id": batch_id, "created_at": now(), "source_job": job_id,
        "source_job_hashes": {"assessments": digest([row["enrichment"] for row in rows])},
        "snapshot_id": run["snapshot_id"], "taxonomy_version": tax["version"],
        "taxonomy_sha256": digest(tax), "policy_version": policy["version"],
        "policy_sha256": file_sha256(POLICY_PATH),
        "counts": {
            "evidence_backed_total": len(changes), "planned_changes": len(planned),
            "resolved_no_change": len(no_change), "deferred_notes": len(deferred_rows),
            "cross_sector_deferred": len(unsafe_cross_sector),
        },
        "changes_sha256": file_sha256(out / "changes.json"),
        "review_queue_sha256": file_sha256(out / "review_queue.json"),
        "planned_changes_sha256": file_sha256(out / "planned_changes.json"),
        "review_groups_sha256": file_sha256(out / "review_groups.json"),
        "deferred_items_sha256": file_sha256(out / "deferred_items.json"),
        "registry_writes": 0,
    }
    manifest["manifest_sha256"] = digest(manifest)
    write_json(out / "manifest.json", manifest)
    approval = {
        "batch_id": batch_id, "manifest_sha256": manifest["manifest_sha256"],
        "approved_by": "workspace_owner_explicit_finish_babis_approval",
        "approved_at": now(), "approved_change_ids": [row["change_id"] for row in planned],
        "decisions": {row["change_id"]: {
            "outcome": "approve", "rationale": "SEC-led candidate corroborated by stored provider evidence; owner authorized Babis completion."
        } for row in planned},
    }
    write_json(out / "approval.json", approval)
    preflight = preflight_batch(db, out)
    write_json(out / "preflight.json", preflight)
    if not preflight["ready"]:
        raise ValueError("Completion batch failed preflight")
    return {"folder": str(out), "manifest": manifest, "preflight": preflight}


def _update_group_and_run_status(db: sqlite3.Connection, run_id: str) -> None:
    db.execute(
        "UPDATE classification_problem_group SET status=CASE "
        "WHEN EXISTS(SELECT 1 FROM classification_problem_item i WHERE i.group_id=classification_problem_group.group_id AND i.status='open') THEN 'open' "
        "WHEN EXISTS(SELECT 1 FROM classification_problem_item i WHERE i.group_id=classification_problem_group.group_id AND i.status='deferred_note') THEN 'deferred' "
        "ELSE 'resolved' END WHERE run_id=?",
        (run_id,),
    )
    remaining = db.execute(
        "SELECT COUNT(*) FROM classification_problem_item WHERE run_id=? AND status='open'", (run_id,)
    ).fetchone()[0]
    db.execute(
        "UPDATE classification_problem_run SET status=? WHERE run_id=?",
        ("completed_with_deferred_notes" if remaining == 0 else "partially_resolved", run_id),
    )


def apply(db: sqlite3.Connection, folder: Path, run_id: str, job_id: str) -> dict[str, Any]:
    folder = Path(folder)
    manifest = validate_batch(folder)
    if file_sha256(folder / "deferred_items.json") != manifest["deferred_items_sha256"]:
        raise ValueError("deferred_items.json hash mismatch")
    problem_schema(db); enrichment_schema(db); governance_schema(db); completion_schema(db)
    completion_id = "babis_completion_" + manifest["manifest_sha256"][:24]
    existing = db.execute("SELECT * FROM babis_completion_run WHERE completion_id=?", (completion_id,)).fetchone()
    if existing:
        return {**dict(existing), "result": "already_applied"}
    preflight = preflight_batch(db, folder)
    if not preflight["ready"]:
        raise ValueError("Completion batch is not ready")
    approval = read_json(folder / "approval.json")
    if approval.get("batch_id") != manifest["batch_id"] or approval.get("manifest_sha256") != manifest["manifest_sha256"]:
        raise ValueError("Approval is not bound to completion manifest")
    changes = read_json(folder / "changes.json")
    planned = {row["change_id"]: row for row in changes if row["changes_value"]}
    requested = set(approval.get("approved_change_ids") or [])
    if requested != set(planned):
        raise ValueError("Approval must exactly cover all evidence-backed changes")
    for change_id in requested:
        decision = (approval.get("decisions") or {}).get(change_id) or {}
        if decision.get("outcome") != "approve" or not decision.get("rationale"):
            raise ValueError("Every completion change requires rationale")
    current_rows = _load_rows(db, run_id, job_id)
    by_problem = {row["problem_id"]: row for row in current_rows}
    deferred = read_json(folder / "deferred_items.json")
    stamp = now()
    applied_count = 0; no_change_count = 0
    deferred_batch_id = "babis-deferred-" + manifest["manifest_sha256"][:24]
    with db:
        db.execute(
            "INSERT INTO classification_change_batch VALUES(?,?,?,?,?,?,?,?)",
            (manifest["batch_id"], manifest["manifest_sha256"], manifest["snapshot_id"],
             manifest["taxonomy_version"], approval["approved_by"], approval["approved_at"], stamp, "applied"),
        )
        for change in changes:
            item = by_problem.get(change["problem_id"])
            if not item:
                raise ValueError(f"Problem no longer open: {change['problem_id']}")
            key = (change["snapshot_id"], change["venue_code"], change["ticker"])
            if item["effective_type"] != change["old_instrument_type"] or item["effective_node"] != change["old_node"]:
                raise ValueError(f"Stale completion baseline for {change['ticker']}")
            if change["changes_value"]:
                previous = db.execute(
                    "SELECT * FROM listing_classification_override WHERE snapshot_id=? AND venue_code=? AND ticker=?", key
                ).fetchone()
                before = dict(previous) if previous else None
                after = {
                    "snapshot_id": key[0], "venue_code": key[1], "ticker": key[2],
                    "instrument_type": change["new_instrument_type"], "node_id": change["new_node"],
                    "status": "approved_provisional", "batch_id": manifest["batch_id"], "updated_at": stamp,
                }
                db.execute("INSERT OR REPLACE INTO listing_classification_override VALUES(?,?,?,?,?,?,?,?)", tuple(after.values()))
                db.execute(
                    "INSERT INTO classification_change_event VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                    (digest([manifest["batch_id"], change["change_id"], "apply"]), manifest["batch_id"],
                     change["change_id"], *key, json.dumps(before), json.dumps(after), "apply", approval["approved_by"], stamp),
                )
                new_status = "resolved_applied"; applied_count += 1
            else:
                new_status = "resolved_no_change"; no_change_count += 1
            db.execute(
                "INSERT INTO classification_problem_resolution_event VALUES(?,?,?,?,?)",
                (change["problem_id"], manifest["batch_id"], item["status"], new_status, stamp),
            )
            db.execute("UPDATE classification_problem_item SET status=? WHERE problem_id=?", (new_status, change["problem_id"]))
            db.execute(
                "UPDATE ecosystem_evidence_gap SET resolution_state='resolved',resolved_at=? "
                "WHERE subject_id=? AND resolution_state IN ('open','cooldown','retry_eligible')",
                (stamp, change["problem_id"]),
            )
        for note in deferred:
            item = by_problem.get(note["problem_id"])
            if not item:
                raise ValueError(f"Deferred problem no longer open: {note['problem_id']}")
            gaps = [row[0] for row in db.execute(
                "SELECT gap_id FROM ecosystem_evidence_gap WHERE subject_id=? ORDER BY gap_id", (note["problem_id"],)
            )]
            if note["assessment"] == "supplemental_source_conflict":
                reason_code = "conflicting_sources"
                followup = "Reopen only when a company-level primary filing dossier resolves the source conflict."
            elif note["assessment"] == "corroborated_taxonomy_candidate":
                reason_code = "cross_sector_change_requires_primary_dossier"
                followup = "Reopen only after primary business and segment evidence justifies the cross-sector move."
            else:
                reason_code = "insufficient_source_detail_or_provider_unavailable"
                followup = "Reopen only when shared provider budget or new primary evidence becomes available."
            note_id = "deferred_" + digest([run_id, note["problem_id"], reason_code])[:24]
            payload = {
                "ticker": item["ticker"], "category": item["category"],
                "assessment": note["assessment"], "original_resolution": item["resolution"],
            }
            db.execute(
                "INSERT INTO classification_problem_deferred_note VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (note_id, note["problem_id"], run_id, job_id, note["assessment"], reason_code,
                 json.dumps(gaps), followup, json.dumps(payload, ensure_ascii=False), "deferred", stamp),
            )
            db.execute(
                "INSERT INTO classification_problem_resolution_event VALUES(?,?,?,?,?)",
                (note["problem_id"], deferred_batch_id, item["status"], "deferred_note", stamp),
            )
            db.execute("UPDATE classification_problem_item SET status='deferred_note' WHERE problem_id=?", (note["problem_id"],))
        _update_group_and_run_status(db, run_id)
        summary = {
            "classification_applied": applied_count, "resolved_no_change": no_change_count,
            "deferred_notes": len(deferred), "open_remaining": db.execute(
                "SELECT COUNT(*) FROM classification_problem_item WHERE run_id=? AND status='open'", (run_id,)
            ).fetchone()[0],
        }
        db.execute(
            "INSERT INTO babis_completion_run VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (completion_id, run_id, job_id, manifest["batch_id"], manifest["manifest_sha256"],
             "completed_with_deferred_notes", applied_count, no_change_count, len(deferred), stamp,
             json.dumps(summary, ensure_ascii=False)),
        )
    return {
        "completion_id": completion_id, "batch_id": manifest["batch_id"],
        "manifest_sha256": manifest["manifest_sha256"], "status": "completed_with_deferred_notes",
        "classification_applied": applied_count, "resolved_no_change": no_change_count,
        "deferred_notes": len(deferred), "open_remaining": 0,
    }


def status(db: sqlite3.Connection, run_id: str) -> dict[str, Any]:
    problem_schema(db); completion_schema(db)
    return {
        "run_id": run_id,
        "run_status": db.execute("SELECT status FROM classification_problem_run WHERE run_id=?", (run_id,)).fetchone()[0],
        "problem_statuses": dict(db.execute(
            "SELECT status,COUNT(*) FROM classification_problem_item WHERE run_id=? GROUP BY status", (run_id,)
        )),
        "deferred_by_assessment": dict(db.execute(
            "SELECT assessment,COUNT(*) FROM classification_problem_deferred_note WHERE problem_run_id=? GROUP BY assessment", (run_id,)
        )),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("prepare"); p.add_argument("--run-id", default=DEFAULT_RUN_ID); p.add_argument("--job-id", default=DEFAULT_JOB_ID); p.add_argument("--out", type=Path)
    p = sub.add_parser("apply"); p.add_argument("--run-id", default=DEFAULT_RUN_ID); p.add_argument("--job-id", default=DEFAULT_JOB_ID); p.add_argument("--batch", type=Path, required=True)
    p = sub.add_parser("status"); p.add_argument("--run-id", default=DEFAULT_RUN_ID)
    args = parser.parse_args(argv)
    db = connect(args.db)
    try:
        if args.command == "prepare": result = prepare(db, args.run_id, args.job_id, args.out)
        elif args.command == "apply": result = apply(db, args.batch, args.run_id, args.job_id)
        else: result = status(db, args.run_id)
    finally:
        db.close()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
