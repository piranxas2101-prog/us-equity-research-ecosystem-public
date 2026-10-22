"""Install and activate sector agents after a completed canonical Babis run.

Activation is local, idempotent and network-free. It registers eleven normal
sector agents plus one unresolved-classification specialist, installs the
information taxonomy, and creates work queues from the exact Babis snapshot.
It does not fabricate research or call APIs.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
from typing import Any

from research_registry.core import DEFAULT_DB, PROJECT, connect, digest
from sector_research_agent import load_registry, route_node, validate_registry


ROOT = PROJECT / "data" / "sector_ecosystem"
INFO_TAXONOMY = PROJECT / "research_information_taxonomy.json"
CORPORATE_TYPES = {"equity_candidate", "adr_candidate", "common_stock", "ordinary_share"}
UNRESOLVED_AGENT_ID = "sector_unresolved_classification"
UNRESOLVED_CATEGORY = "unresolved_classification"
UNRESOLVED_STATUS = "ready_for_unresolved_evidence"
BABIS_AGENT_ID = "babis"
BABIS_CONTRACT_VERSION = "classification_pipeline.v6"


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def schema(db: sqlite3.Connection) -> None:
    db.executescript("""
      CREATE TABLE IF NOT EXISTS ecosystem_agent_registration(
        agent_id TEXT PRIMARY KEY,agent_type TEXT NOT NULL,owned_sector TEXT,
        module TEXT NOT NULL,contract_version TEXT NOT NULL,status TEXT NOT NULL,
        activated_at TEXT NOT NULL);
      CREATE TABLE IF NOT EXISTS research_information_category(
        category_id TEXT PRIMARY KEY,owner TEXT NOT NULL,layer TEXT NOT NULL,
        description TEXT NOT NULL,taxonomy_version TEXT NOT NULL);
      CREATE TABLE IF NOT EXISTS sector_activation_run(
        activation_id TEXT PRIMARY KEY,babis_pipeline_job TEXT NOT NULL UNIQUE,
        babis_run_id TEXT NOT NULL,snapshot_id TEXT NOT NULL,registry_version TEXT NOT NULL,
        information_taxonomy_version TEXT NOT NULL,status TEXT NOT NULL,
        queued_count INTEGER NOT NULL,blocked_count INTEGER NOT NULL,created_at TEXT NOT NULL,
        summary_json TEXT NOT NULL);
      CREATE TABLE IF NOT EXISTS sector_analysis_queue(
        queue_id TEXT PRIMARY KEY,activation_id TEXT NOT NULL REFERENCES sector_activation_run,
        agent_id TEXT,snapshot_id TEXT NOT NULL,venue_code TEXT,ticker TEXT NOT NULL,
        instrument_type TEXT,node_id TEXT,subcategory TEXT,status TEXT NOT NULL,
        block_reason TEXT,created_at TEXT NOT NULL,UNIQUE(activation_id,venue_code,ticker));
      CREATE INDEX IF NOT EXISTS sector_queue_status ON sector_analysis_queue(status,agent_id);
      CREATE TABLE IF NOT EXISTS sector_context_packet_store(
        packet_id TEXT PRIMARY KEY,activation_id TEXT NOT NULL,queue_id TEXT NOT NULL UNIQUE,
        agent_id TEXT NOT NULL,run_id TEXT NOT NULL,ticker TEXT NOT NULL,
        packet_json TEXT NOT NULL,status TEXT NOT NULL,created_at TEXT NOT NULL);
      CREATE TABLE IF NOT EXISTS giannis_financial_handoff_queue(
        handoff_id TEXT PRIMARY KEY,sector_packet_id TEXT NOT NULL UNIQUE,
        activation_id TEXT NOT NULL,ticker TEXT NOT NULL,status TEXT NOT NULL,
        financial_contract_version TEXT NOT NULL,created_at TEXT NOT NULL);
    """)


def register_production_agents(db: sqlite3.Connection, registry: dict[str, Any],
                               information_version: str, stamp: str) -> None:
    """Install the complete production ownership chain idempotently."""
    db.execute("INSERT OR REPLACE INTO ecosystem_agent_registration VALUES(?,?,?,?,?,?,?)", (
        BABIS_AGENT_ID, "classification", None, "research_registry.babis_pipeline",
        BABIS_CONTRACT_VERSION, "production_ready", stamp,
    ))
    for sector, details in registry["agents"].items():
        db.execute("INSERT OR REPLACE INTO ecosystem_agent_registration VALUES(?,?,?,?,?,?,?)", (
            details["agent_id"], "sector_research", sector, "sector_agents." + sector,
            registry["version"], "active", stamp,
        ))
    for category, details in registry.get("special_agents", {}).items():
        db.execute("INSERT OR REPLACE INTO ecosystem_agent_registration VALUES(?,?,?,?,?,?,?)", (
            details["agent_id"], "classification_resolution", category, details["module"],
            registry["version"], "active", stamp,
        ))
    db.execute("INSERT OR REPLACE INTO ecosystem_agent_registration VALUES(?,?,?,?,?,?,?)", (
        "giannis", "financial_analysis", None, "giannis_rule_based_from_shortlist.py",
        information_version, "active_contract_pending_financial_runtime_upgrade", stamp,
    ))


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _babis_state(folder: Path) -> dict[str, Any]:
    state = _load_json(Path(folder) / "state.json")
    if state.get("status") != "finished_with_review_required":
        raise ValueError(f"Babis pipeline is not complete: {state.get('status')}")
    if not state.get("sec_job_id"):
        raise ValueError("Completed Babis state has no SEC run identity")
    return state


def activate(babis_job: Path, db_path: Path = DEFAULT_DB, root: Path = ROOT) -> dict[str, Any]:
    babis_job = Path(babis_job).resolve()
    state = _babis_state(babis_job)
    registry = load_registry()
    validation = validate_registry(registry)
    if validation["status"] != "valid":
        raise ValueError("Sector registry is invalid")
    info = _load_json(INFO_TAXONOMY)
    db = connect(db_path)
    try:
        schema(db)
        existing = db.execute("SELECT summary_json FROM sector_activation_run WHERE babis_pipeline_job=?", (str(babis_job),)).fetchone()
        if existing:
            result = json.loads(existing[0]); result["status"] = "already_activated"; return result
        run = db.execute("SELECT snapshot_id FROM classification_problem_run WHERE run_id=?", (state["sec_job_id"],)).fetchone()
        if not run:
            raise ValueError("Babis problem-registry run is not imported")
        snapshot_id = run[0]
        activation_id = "sector_activation_" + digest([state["sec_job_id"], registry["version"], info["version"]])[:24]
        stamp = now()
        queues: list[dict[str, Any]] = []
        for row in db.execute(
            "SELECT snapshot_id,venue_code,ticker,instrument_type,node_id,status FROM effective_listed_symbol "
            "WHERE snapshot_id=? ORDER BY ticker,venue_code", (snapshot_id,)
        ):
            instrument_type, node_id = row[3], row[4]
            if instrument_type not in CORPORATE_TYPES:
                continue
            agent_id = subcategory = reason = None
            queue_status = "ready_for_sector_evidence"
            if not node_id or len(str(node_id).split(".")) < 2:
                agent_id = UNRESOLVED_AGENT_ID
                subcategory = UNRESOLVED_CATEGORY
                queue_status, reason = UNRESOLVED_STATUS, "classification_missing_subcategory"
            else:
                try:
                    route = route_node(node_id, registry)
                    agent_id, subcategory = route["agent_id"], route["subcategory"]
                except ValueError as exc:
                    queue_status, reason = "blocked", "routing_error:" + str(exc)
            queues.append({
                "queue_id": "sector_queue_" + digest([activation_id, row[1], row[2]])[:24],
                "activation_id": activation_id, "agent_id": agent_id, "snapshot_id": row[0],
                "venue_code": row[1], "ticker": row[2], "instrument_type": instrument_type,
                "node_id": node_id, "subcategory": subcategory, "status": queue_status,
                "block_reason": reason, "created_at": stamp,
            })
        counts = Counter(row["status"] for row in queues)
        by_agent = Counter(row["agent_id"] for row in queues if row["agent_id"])
        summary = {
            "activation_id": activation_id, "babis_pipeline_job": str(babis_job),
            "babis_run_id": state["sec_job_id"], "snapshot_id": snapshot_id,
            "registry_version": registry["version"], "information_taxonomy_version": info["version"],
            "registered_agents": len(registry["agents"]) + len(registry.get("special_agents", {})),
            "classification_agents": 1,
            "classification_authority": BABIS_AGENT_ID,
            "classification_agent_status": "production_ready",
            "sector_agents": len(registry["agents"]),
            "special_agents": len(registry.get("special_agents", {})),
            "information_categories": len(info["categories"]),
            "queued": counts.get("ready_for_sector_evidence", 0), "blocked": counts.get("blocked", 0),
            "unresolved_queued": counts.get(UNRESOLVED_STATUS, 0),
            "by_agent": dict(sorted(by_agent.items())),
            "status": "activated_with_blocked_items" if counts.get("blocked") else (
                "activated_with_unresolved_queue" if counts.get(UNRESOLVED_STATUS) else "activated"
            ),
            "automatic_analysis_claims": 0, "network_calls": 0, "created_at": stamp,
        }
        with db:
            register_production_agents(db, registry, info["version"], stamp)
            for category in info["categories"]:
                db.execute("INSERT OR REPLACE INTO research_information_category VALUES(?,?,?,?,?)", (
                    category["id"], category["owner"], category["layer"], category["description"], info["version"],
                ))
            db.execute("INSERT INTO sector_activation_run VALUES(?,?,?,?,?,?,?,?,?,?,?)", (
                activation_id, str(babis_job), state["sec_job_id"], snapshot_id, registry["version"], info["version"],
                summary["status"], summary["queued"], summary["blocked"], stamp,
                json.dumps(summary, ensure_ascii=False, sort_keys=True),
            ))
            db.executemany("INSERT INTO sector_analysis_queue VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", [tuple(row[key] for key in (
                "queue_id","activation_id","agent_id","snapshot_id","venue_code","ticker","instrument_type","node_id",
                "subcategory","status","block_reason","created_at")) for row in queues])
        folder = Path(root) / activation_id
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        for agent_id, count in sorted(by_agent.items()):
            rows = [row for row in queues if row["agent_id"] == agent_id]
            (folder / f"{agent_id}_queue.json").write_text(json.dumps({"agent_id":agent_id,"count":count,"items":rows}, ensure_ascii=False, indent=2), encoding="utf-8")
        blocked = [row for row in queues if row["status"] == "blocked"]
        (folder / "blocked_queue.json").write_text(json.dumps(blocked, ensure_ascii=False, indent=2), encoding="utf-8")
        (Path(root) / "latest.json").write_text(json.dumps({"activation":str(folder),"activation_id":activation_id}, ensure_ascii=False, indent=2), encoding="utf-8")
        return summary
    finally:
        db.close()


def migrate_unresolved(activation_id: str | None = None, db_path: Path = DEFAULT_DB,
                       root: Path = ROOT) -> dict[str, Any]:
    """Move missing-subcategory rows into the dedicated resolution queue.

    This is an idempotent production migration. It preserves the original
    block reason for auditability and never assigns a guessed sector.
    """
    registry = load_registry()
    validation = validate_registry(registry)
    if validation["status"] != "valid":
        raise ValueError("Sector registry is invalid")
    info = _load_json(INFO_TAXONOMY)
    special = registry.get("special_agents", {}).get(UNRESOLVED_CATEGORY)
    if not special:
        raise ValueError("Unresolved-classification specialist is not configured")
    db = connect(db_path)
    try:
        schema(db)
        if activation_id:
            row = db.execute(
                "SELECT activation_id,summary_json,created_at FROM sector_activation_run WHERE activation_id=?",
                (activation_id,),
            ).fetchone()
        else:
            row = db.execute(
                "SELECT activation_id,summary_json,created_at FROM sector_activation_run ORDER BY created_at DESC LIMIT 1"
            ).fetchone()
        if not row:
            raise ValueError("No sector activation exists")
        activation_id = row[0]
        summary = json.loads(row[1])
        stamp = now()
        with db:
            register_production_agents(db, registry, info["version"], stamp)
            for category in info["categories"]:
                db.execute("INSERT OR REPLACE INTO research_information_category VALUES(?,?,?,?,?)", (
                    category["id"], category["owner"], category["layer"],
                    category["description"], info["version"],
                ))
            changed = db.execute(
                "UPDATE sector_analysis_queue SET agent_id=?,subcategory=?,status=? "
                "WHERE activation_id=? AND status='blocked' AND block_reason='classification_missing_subcategory'",
                (UNRESOLVED_AGENT_ID, UNRESOLVED_CATEGORY, UNRESOLVED_STATUS, activation_id),
            ).rowcount

            counts = dict(db.execute(
                "SELECT status,COUNT(*) FROM sector_analysis_queue WHERE activation_id=? GROUP BY status",
                (activation_id,),
            ))
            by_agent = dict(db.execute(
                "SELECT agent_id,COUNT(*) FROM sector_analysis_queue WHERE activation_id=? AND agent_id IS NOT NULL GROUP BY agent_id ORDER BY agent_id",
                (activation_id,),
            ))
            blocked = counts.get("blocked", 0)
            unresolved = counts.get(UNRESOLVED_STATUS, 0)
            summary.update({
                "registry_version": registry["version"],
                "information_taxonomy_version": info["version"],
                "registered_agents": len(registry["agents"]) + len(registry.get("special_agents", {})),
                "classification_agents": 1,
                "classification_authority": BABIS_AGENT_ID,
                "classification_agent_status": "production_ready",
                "sector_agents": len(registry["agents"]),
                "special_agents": len(registry.get("special_agents", {})),
                "information_categories": len(info["categories"]),
                "queued": counts.get("ready_for_sector_evidence", 0),
                "unresolved_queued": unresolved,
                "blocked": blocked,
                "by_agent": by_agent,
                "status": "activated_with_blocked_items" if blocked else (
                    "activated_with_unresolved_queue" if unresolved else "activated"
                ),
                "unresolved_migration": {
                    "migrated_this_call": changed,
                    "queue_total": unresolved,
                    "category": UNRESOLVED_CATEGORY,
                    "agent_id": UNRESOLVED_AGENT_ID,
                    "migrated_at": stamp,
                    "classification_writes": 0,
                },
            })
            db.execute(
                "UPDATE sector_activation_run SET registry_version=?,information_taxonomy_version=?,status=?,"
                "queued_count=?,blocked_count=?,summary_json=? WHERE activation_id=?",
                (registry["version"], info["version"], summary["status"], summary["queued"],
                 summary["blocked"], json.dumps(summary, ensure_ascii=False, sort_keys=True), activation_id),
            )

        columns = (
            "queue_id", "activation_id", "agent_id", "snapshot_id", "venue_code", "ticker",
            "instrument_type", "node_id", "subcategory", "status", "block_reason", "created_at",
        )
        queue_rows = [dict(zip(columns, item)) for item in db.execute(
            "SELECT " + ",".join(columns) + " FROM sector_analysis_queue WHERE activation_id=? AND agent_id=? ORDER BY ticker,venue_code",
            (activation_id, UNRESOLVED_AGENT_ID),
        )]
        blocked_rows = [dict(zip(columns, item)) for item in db.execute(
            "SELECT " + ",".join(columns) + " FROM sector_analysis_queue WHERE activation_id=? AND status='blocked' ORDER BY ticker,venue_code",
            (activation_id,),
        )]
        folder = Path(root) / activation_id
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        (folder / f"{UNRESOLVED_AGENT_ID}_queue.json").write_text(json.dumps({
            "agent_id": UNRESOLVED_AGENT_ID,
            "category": UNRESOLVED_CATEGORY,
            "count": len(queue_rows),
            "classification_writes_allowed": False,
            "handoff_to": "babis",
            "items": queue_rows,
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        (folder / "blocked_queue.json").write_text(json.dumps(blocked_rows, ensure_ascii=False, indent=2), encoding="utf-8")
        return summary
    finally:
        db.close()


def reconcile(activation_id: str | None = None, db_path: Path = DEFAULT_DB,
              root: Path = ROOT) -> dict[str, Any]:
    """Rebuild queue routing from the current effective classifications."""
    registry = load_registry()
    info = _load_json(INFO_TAXONOMY)
    db = connect(db_path)
    try:
        schema(db)
        if activation_id:
            activation = db.execute(
                "SELECT activation_id,summary_json FROM sector_activation_run WHERE activation_id=?", (activation_id,)
            ).fetchone()
        else:
            activation = db.execute(
                "SELECT activation_id,summary_json FROM sector_activation_run ORDER BY created_at DESC LIMIT 1"
            ).fetchone()
        if not activation:
            raise ValueError("No sector activation exists")
        activation_id = activation[0]
        summary = json.loads(activation[1])
        rows = list(db.execute(
            "SELECT q.queue_id,q.status,v.instrument_type,v.node_id FROM sector_analysis_queue q "
            "JOIN effective_listed_symbol v ON v.snapshot_id=q.snapshot_id AND v.venue_code=q.venue_code AND v.ticker=q.ticker "
            "WHERE q.activation_id=? ORDER BY q.ticker,q.venue_code", (activation_id,)
        ))
        rerouted = removed = unresolved = 0
        with db:
            register_production_agents(db, registry, info["version"], now())
            for row in rows:
                queue_id, previous_status, instrument_type, node_id = row
                if instrument_type not in CORPORATE_TYPES:
                    db.execute(
                        "UPDATE sector_analysis_queue SET agent_id=NULL,instrument_type=?,node_id=?,subcategory=NULL,"
                        "status='removed_non_corporate_instrument',block_reason=? WHERE queue_id=?",
                        (instrument_type, node_id, "instrument_reclassified:" + str(instrument_type), queue_id),
                    )
                    removed += 1
                elif node_id and len(str(node_id).split(".")) >= 2:
                    route = route_node(node_id, registry)
                    db.execute(
                        "UPDATE sector_analysis_queue SET agent_id=?,instrument_type=?,node_id=?,subcategory=?,"
                        "status='ready_for_sector_evidence',block_reason=NULL WHERE queue_id=?",
                        (route["agent_id"], instrument_type, node_id, route["subcategory"], queue_id),
                    )
                    rerouted += int(previous_status != "ready_for_sector_evidence")
                else:
                    preserved = previous_status if previous_status in {
                        "unresolved_analysis_complete", "unresolved_analysis_review_required"
                    } else UNRESOLVED_STATUS
                    db.execute(
                        "UPDATE sector_analysis_queue SET agent_id=?,instrument_type=?,node_id=?,subcategory=?,"
                        "status=?,block_reason='classification_missing_subcategory' WHERE queue_id=?",
                        (UNRESOLVED_AGENT_ID, instrument_type, node_id, UNRESOLVED_CATEGORY, preserved, queue_id),
                    )
                    unresolved += 1
            status_counts = dict(db.execute(
                "SELECT status,COUNT(*) FROM sector_analysis_queue WHERE activation_id=? GROUP BY status",
                (activation_id,),
            ))
            by_agent = dict(db.execute(
                "SELECT agent_id,COUNT(*) FROM sector_analysis_queue WHERE activation_id=? AND agent_id IS NOT NULL "
                "GROUP BY agent_id ORDER BY agent_id", (activation_id,),
            ))
            blocked = status_counts.get("blocked", 0)
            unresolved_total = sum(status_counts.get(value, 0) for value in (
                UNRESOLVED_STATUS, "unresolved_analysis_complete", "unresolved_analysis_review_required"
            ))
            summary.update({
                "registry_version": registry["version"],
                "information_taxonomy_version": info["version"],
                "classification_agents": 1,
                "classification_authority": BABIS_AGENT_ID,
                "classification_agent_status": "production_ready",
                "queued": status_counts.get("ready_for_sector_evidence", 0),
                "unresolved_queued": unresolved_total,
                "removed_non_corporate": status_counts.get("removed_non_corporate_instrument", 0),
                "blocked": blocked,
                "by_agent": by_agent,
                "status": "activated_with_blocked_items" if blocked else (
                    "activated_with_unresolved_queue" if unresolved_total else "activated"
                ),
                "last_reconciliation": {
                    "rerouted_this_call": rerouted,
                    "removed_non_corporate_this_call": removed,
                    "unresolved_total": unresolved_total,
                    "reconciled_at": now(),
                },
            })
            db.execute(
                "UPDATE sector_activation_run SET registry_version=?,information_taxonomy_version=?,status=?,"
                "queued_count=?,blocked_count=?,summary_json=? WHERE activation_id=?",
                (registry["version"], info["version"], summary["status"], summary["queued"],
                 summary["blocked"], json.dumps(summary, ensure_ascii=False, sort_keys=True), activation_id),
            )
        columns = (
            "queue_id", "activation_id", "agent_id", "snapshot_id", "venue_code", "ticker",
            "instrument_type", "node_id", "subcategory", "status", "block_reason", "created_at",
        )
        queue_rows = [dict(zip(columns, item)) for item in db.execute(
            "SELECT " + ",".join(columns) + " FROM sector_analysis_queue WHERE activation_id=? ORDER BY ticker,venue_code",
            (activation_id,),
        )]
        folder = Path(root) / activation_id
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        for agent_id, count in sorted(by_agent.items()):
            items = [item for item in queue_rows if item["agent_id"] == agent_id]
            (folder / f"{agent_id}_queue.json").write_text(json.dumps({
                "agent_id": agent_id, "count": count, "items": items,
            }, ensure_ascii=False, indent=2), encoding="utf-8")
        (folder / "removed_non_corporate_queue.json").write_text(json.dumps([
            item for item in queue_rows if item["status"] == "removed_non_corporate_instrument"
        ], ensure_ascii=False, indent=2), encoding="utf-8")
        return summary
    finally:
        db.close()


def status(db_path: Path = DEFAULT_DB) -> dict[str, Any]:
    db = connect(db_path)
    try:
        schema(db)
        row = db.execute("SELECT summary_json FROM sector_activation_run ORDER BY created_at DESC LIMIT 1").fetchone()
        return json.loads(row[0]) if row else {"status":"not_activated"}
    finally:
        db.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("activate", "migrate-unresolved", "reconcile", "status"))
    parser.add_argument("--babis-job", type=Path)
    parser.add_argument("--activation-id")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    args = parser.parse_args(argv)
    if args.action == "activate":
        if not args.babis_job:
            parser.error("--babis-job is required for activate")
        result = activate(args.babis_job, args.db)
    elif args.action == "migrate-unresolved":
        result = migrate_unresolved(args.activation_id, args.db)
    elif args.action == "reconcile":
        result = reconcile(args.activation_id, args.db)
    else:
        result = status(args.db)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
