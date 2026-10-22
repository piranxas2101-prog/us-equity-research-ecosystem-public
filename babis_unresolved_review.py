"""Babis review workflow for unresolved-classification agent proposals.

Only production-policy approval candidates are applied. Range mappings,
cross-sector conflicts, asset-type issues and incomplete evidence remain in the
special queue with explicit reasons. All writes use the existing immutable,
hash-bound classification governance batch.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any

from research_registry.babis_catalog_v2 import relation
from research_registry.classification_governance import (
    apply_batch, prepare_batch, preflight_batch, read_json, write_json,
)
from research_registry.core import DEFAULT_DB, PROJECT, connect, digest, taxonomy
from sector_ecosystem_integration import UNRESOLVED_AGENT_ID, reconcile


ROOT = PROJECT / "data" / "babis_unresolved_review"


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _catalog_map(results_db: Path) -> dict[tuple[str, str], dict[str, Any]]:
    import sqlite3
    source = sqlite3.connect(f"file:{Path(results_db).resolve().as_posix()}?mode=ro", uri=True)
    try:
        output = {}
        for ticker, _venue, payload in source.execute("SELECT ticker,venue,payload FROM results"):
            row = json.loads(payload)
            output[(ticker, row.get("venue_code") or "")] = row
        return output
    finally:
        source.close()


def rebase_proposal(catalog: dict[str, Any], current_type: str, current_node: str | None) -> dict[str, Any]:
    """Bind a completed SEC proposal to the current effective registry baseline."""
    row = dict(catalog)
    proposed_node = row.get("proposed_node")
    row["old_instrument_type"] = current_type
    row["proposed_instrument_type"] = current_type
    row["old_node"] = current_node
    row["relation"] = relation(current_node, proposed_node)
    if not proposed_node or len(str(proposed_node).split(".")) < 2:
        row["status"] = "unresolved"
        row["reason"] = row.get("reason") or "classification_missing_subcategory"
    elif row["relation"] == "same_node":
        row["status"] = "confirmed_same_node"
    elif row["relation"] == "existing_more_specific":
        row["status"] = "confirmed_existing_more_specific"
    elif row["relation"] in {"cross_sector_conflict", "different_branch_same_sector"}:
        row["status"] = "classification_conflict_review"
    else:
        row["status"] = "classification_proposal_review"
    return row


def prepare(activation_id: str, db_path: Path = DEFAULT_DB, root: Path = ROOT) -> dict[str, Any]:
    db = connect(db_path)
    try:
        activation = db.execute(
            "SELECT babis_pipeline_job,snapshot_id FROM sector_activation_run WHERE activation_id=?",
            (activation_id,),
        ).fetchone()
        if not activation:
            raise ValueError("Unknown sector activation")
        pipeline = json.loads((Path(activation[0]) / "state.json").read_text(encoding="utf-8"))
        catalog_job = Path(pipeline["sec_job"])
        catalog = _catalog_map(catalog_job / "results.sqlite3")
        rows = list(db.execute(
            "SELECT q.ticker,q.venue_code,v.name,v.instrument_type,v.node_id "
            "FROM sector_analysis_queue q JOIN effective_listed_symbol v "
            "ON v.snapshot_id=q.snapshot_id AND v.venue_code=q.venue_code AND v.ticker=q.ticker "
            "WHERE q.activation_id=? AND q.agent_id=? ORDER BY q.ticker,q.venue_code",
            (activation_id, UNRESOLVED_AGENT_ID),
        ))
        proposals = []
        for row in rows:
            source = catalog.get((row["ticker"], row["venue_code"]))
            if source:
                proposals.append(rebase_proposal(source, row["instrument_type"], row["node_id"]))
            else:
                proposals.append({
                    "ticker": row["ticker"], "venue_code": row["venue_code"], "name": row["name"],
                    "old_instrument_type": row["instrument_type"], "proposed_instrument_type": row["instrument_type"],
                    "old_node": row["node_id"], "proposed_node": None, "source_status": "missing",
                    "status": "unresolved", "reason": "catalog_result_missing", "relation": "no_candidate",
                })
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        review_id = "babis-unresolved-review-" + stamp
        folder = Path(root) / review_id
        source_job = folder / "source_job"
        source_job.mkdir(parents=True, exist_ok=False)
        tax = taxonomy()[0]
        write_json(source_job / "state.json", {
            "status": "finished_with_review_required", "job_id": review_id,
            "symbols": len(proposals), "created_at": now(), "network_calls": 0,
            "llm_calls": 0, "registry_writes": 0,
        })
        write_json(source_job / "input.json", {
            "snapshot_id": activation[1], "symbols": len(proposals),
            "taxonomy_version": tax["version"], "taxonomy_sha256": digest(tax),
            "source_job": str(catalog_job), "policy": {
                "current_effective_baseline": True,
                "normal_sector_subcategory_required": True,
                "cross_sector_conflicts_require_dossier": True,
            },
        })
        write_json(source_job / "proposals.json", proposals)
        batch_folder = folder / "classification_batch"
        manifest = prepare_batch(source_job, batch_folder)
        planned = read_json(batch_folder / "planned_changes.json")
        approval = {
            "batch_id": manifest["batch_id"], "manifest_sha256": manifest["manifest_sha256"],
            "approved_by": "workspace_owner_explicit_complete_all_required_babis_steps",
            "approved_at": now(), "approved_change_ids": [row["change_id"] for row in planned],
            "decisions": {},
        }
        write_json(batch_folder / "approval.json", approval)
        preflight = preflight_batch(db, batch_folder)
        if not preflight["ready"]:
            raise ValueError("Unresolved review batch failed preflight")
        review = read_json(batch_folder / "review_queue.json")
        disposition_counts = dict(Counter(row["disposition_reason"] for row in review))
        result = {
            "review_id": review_id, "folder": str(folder), "activation_id": activation_id,
            "symbols_reviewed": len(proposals), "approved_candidates": len(planned),
            "retained_for_review": len(review), "review_reasons": disposition_counts,
            "manifest_sha256": manifest["manifest_sha256"], "batch": str(batch_folder),
            "approval": str(batch_folder / "approval.json"), "preflight": preflight,
            "network_calls": 0, "llm_calls": 0, "registry_writes": 0,
        }
        write_json(folder / "PREPARE_RESULT.json", result)
        lines = [
            "# Babis unresolved proposal review", "",
            f"- Symbols reviewed: **{len(proposals)}**",
            f"- Policy-approved candidates: **{len(planned)}**",
            f"- Retained in special review queue: **{len(review)}**", "",
            "| Retained reason | Symbols |", "|---|---:|",
        ]
        lines += [f"| `{key}` | {value} |" for key, value in sorted(disposition_counts.items())]
        lines += ["", "Only exact, allowlisted SEC SIC mappings with valid identity and non-conflicting routing are approved automatically."]
        (folder / "REVIEW_REPORT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
        return result
    finally:
        db.close()


def apply(folder: Path, db_path: Path = DEFAULT_DB) -> dict[str, Any]:
    folder = Path(folder)
    prepared = read_json(folder / "PREPARE_RESULT.json")
    db = connect(db_path)
    try:
        result = apply_batch(db, Path(prepared["batch"]), Path(prepared["approval"]))
    finally:
        db.close()
    routing = reconcile(prepared["activation_id"], db_path)
    output = {
        **result,
        "review_id": prepared["review_id"],
        "routed_to_normal_sector_agents": routing["last_reconciliation"]["rerouted_this_call"],
        "normal_sector_queue": routing["queued"],
        "special_unresolved_queue": routing["unresolved_queued"],
        "removed_non_corporate": routing.get("removed_non_corporate", 0),
        "blocked": routing["blocked"],
        "classification_authority": "babis",
    }
    write_json(folder / "APPLY_RESULT.json", output)
    return output


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("prepare", "apply", "status"))
    parser.add_argument("--activation-id")
    parser.add_argument("--folder", type=Path)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    args = parser.parse_args(argv)
    if args.action == "prepare":
        if not args.activation_id:
            parser.error("--activation-id is required for prepare")
        result = prepare(args.activation_id, args.db)
    elif args.action == "apply":
        if not args.folder:
            parser.error("--folder is required for apply")
        result = apply(args.folder, args.db)
    else:
        if not args.folder:
            parser.error("--folder is required for status")
        applied = args.folder / "APPLY_RESULT.json"
        result = read_json(applied if applied.exists() else args.folder / "PREPARE_RESULT.json")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
