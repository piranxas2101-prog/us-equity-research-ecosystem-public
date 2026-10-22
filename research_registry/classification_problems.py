"""Deterministic problem registry for unresolved classification work."""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path

from .core import DEFAULT_DB, connect, digest

TERMINAL = {"retained_instrument_category", "confirmed_same_node", "confirmed_existing_more_specific"}


def category(row):
    status, reason, relation = row.get("status"), row.get("reason"), row.get("relation")
    if status in TERMINAL:
        return None
    if status == "asset_type_review":
        return "instrument_type_review"
    if status == "identity_review" or reason in {"missing_unambiguous_sec_cik", "sec_cik_conflict"}:
        return "identity_resolution"
    if relation == "cross_sector_conflict":
        return "cross_sector_conflict"
    if relation == "different_branch_same_sector":
        return "branch_conflict"
    if reason == "missing_sic":
        return "missing_sic"
    if reason == "unsupported_sic":
        return "unsupported_sic"
    if reason in {"provider_profile_unavailable", "sec_submission_unavailable"}:
        return "provider_evidence_failure"
    details = (row.get("source_ref") or {}).get("details") or {}
    sic = str(details.get("sic") or details.get("sic_code") or "")
    if sic in {"2834", "6798"}:
        return "semantic_taxonomy_review"
    if str(reason or "").startswith("sic_range"):
        return "range_mapping_review"
    if status == "classification_proposal_review":
        return "classification_candidate_review"
    return "unclassified_review"


def resolution_for(problem_category):
    return {
        "instrument_type_review": "Resolve asset type before any issuer-sector decision.",
        "identity_resolution": "Reconcile ticker/share class to one issuer CIK.",
        "cross_sector_conflict": "Review grouped business-model evidence; never auto-apply across sectors.",
        "branch_conflict": "Review primary revenue driver and choose one branch or a safe ancestor.",
        "missing_sic": "Check instrument type, then use a second issuer/business source if still applicable.",
        "unsupported_sic": "Add an evidence-backed exact mapping or retain an explicit unresolved state.",
        "provider_evidence_failure": "Use a configured fallback source without retry loops.",
        "semantic_taxonomy_review": "Resolve business model; use a versioned taxonomy node or safe parent.",
        "range_mapping_review": "Replace broad range inference with exact evidence or safe ancestor.",
        "classification_candidate_review": "Validate the exact mapping and include only in an approved batch.",
        "unclassified_review": "Inspect the recorded evidence and assign a more specific problem category.",
    }[problem_category]


def build(rows, run_id, snapshot_id, taxonomy_version):
    items = []; groups = {}
    for row in rows:
        problem_category = category(row)
        if problem_category is None:
            continue
        details = (row.get("source_ref") or {}).get("details") or {}
        sic = str(details.get("sic") or details.get("sic_code") or "none")
        old_sector = str(row.get("old_node") or "none").split(".")[0]
        new_sector = str(row.get("proposed_node") or "none").split(".")[0]
        proposed_type = row.get("proposed_instrument_type") or "none"
        group_key = "|".join((problem_category, sic, old_sector, new_sector, proposed_type))
        group_id = digest([run_id, group_key])
        item = {"problem_id": digest([run_id, row.get("venue_code") or row.get("venue"), row["ticker"], group_key]),
                "run_id": run_id, "snapshot_id": snapshot_id, "taxonomy_version": taxonomy_version,
                "group_id": group_id, "category": problem_category, "group_key": group_key,
                "venue_code": row.get("venue_code"), "venue": row.get("venue"), "ticker": row["ticker"],
                "name": row.get("name"), "sic": None if sic == "none" else sic,
                "old_node": row.get("old_node"), "proposed_node": row.get("proposed_node"),
                "old_instrument_type": row.get("old_instrument_type"), "proposed_instrument_type": row.get("proposed_instrument_type"),
                "worker_status": row.get("status"), "reason": row.get("reason"), "relation": row.get("relation"),
                "resolution": resolution_for(problem_category), "status": "open"}
        items.append(item)
        group = groups.setdefault(group_id, {"group_id": group_id, "run_id": run_id, "category": problem_category,
            "group_key": group_key, "sic": item["sic"], "old_sector": old_sector, "new_sector": new_sector,
            "proposed_instrument_type": proposed_type, "resolution": item["resolution"], "status": "open",
            "count": 0, "sample_tickers": []})
        group["count"] += 1
        if len(group["sample_tickers"]) < 10: group["sample_tickers"].append(row["ticker"])
    ordered_groups = sorted(groups.values(), key=lambda value: (-value["count"], value["category"], value["group_key"]))
    return {"run_id": run_id, "snapshot_id": snapshot_id, "taxonomy_version": taxonomy_version,
            "items": items, "groups": ordered_groups, "counts": dict(Counter(item["category"] for item in items)),
            "reconciled": len(items) == sum(group["count"] for group in ordered_groups)}


def write_artifacts(folder, rows, inputs):
    from .babis_catalog_v2 import save
    payload = build(rows, folder.name, inputs["snapshot_id"], inputs["taxonomy_version"])
    save(folder / "problem_registry.json", payload)
    save(folder / "problem_groups.json", payload["groups"])
    return payload


def schema(db):
    db.executescript("""
    CREATE TABLE IF NOT EXISTS classification_problem_run(
      run_id TEXT PRIMARY KEY,snapshot_id TEXT NOT NULL,taxonomy_version TEXT NOT NULL,
      source_job TEXT NOT NULL,status TEXT NOT NULL,imported_at TEXT NOT NULL,summary_json TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS classification_problem_group(
      group_id TEXT PRIMARY KEY,run_id TEXT NOT NULL REFERENCES classification_problem_run,
      category TEXT NOT NULL,group_key TEXT NOT NULL,sic TEXT,old_sector TEXT,new_sector TEXT,
      proposed_instrument_type TEXT,resolution TEXT NOT NULL,status TEXT NOT NULL,count INTEGER NOT NULL,
      sample_json TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS classification_problem_item(
      problem_id TEXT PRIMARY KEY,run_id TEXT NOT NULL REFERENCES classification_problem_run,
      group_id TEXT NOT NULL REFERENCES classification_problem_group,snapshot_id TEXT NOT NULL,
      taxonomy_version TEXT NOT NULL,venue_code TEXT,ticker TEXT NOT NULL,name TEXT,category TEXT NOT NULL,
      sic TEXT,old_node TEXT,proposed_node TEXT,old_instrument_type TEXT,proposed_instrument_type TEXT,
      worker_status TEXT,reason TEXT,relation TEXT,resolution TEXT NOT NULL,status TEXT NOT NULL);
    CREATE INDEX IF NOT EXISTS classification_problem_open ON classification_problem_item(status,category);
    CREATE TABLE IF NOT EXISTS classification_problem_resolution_event(
      problem_id TEXT NOT NULL REFERENCES classification_problem_item,
      batch_id TEXT NOT NULL,
      previous_status TEXT NOT NULL,new_status TEXT NOT NULL,resolved_at TEXT NOT NULL,
      PRIMARY KEY(problem_id,batch_id));
    CREATE TABLE IF NOT EXISTS classification_problem_resolution_batch(
      batch_id TEXT PRIMARY KEY,run_id TEXT NOT NULL,job_id TEXT NOT NULL,
      assessment TEXT NOT NULL,manifest_sha256 TEXT NOT NULL,approved_by TEXT NOT NULL,
      applied_at TEXT NOT NULL,status TEXT NOT NULL,item_count INTEGER NOT NULL);
    """)


def enrichment_schema(db):
    db.executescript("""
    CREATE TABLE IF NOT EXISTS classification_problem_enrichment(
      problem_id TEXT NOT NULL REFERENCES classification_problem_item,
      job_id TEXT NOT NULL,assessment TEXT NOT NULL,payload_json TEXT NOT NULL,
      imported_at TEXT NOT NULL,PRIMARY KEY(problem_id,job_id));
    CREATE INDEX IF NOT EXISTS classification_problem_enrichment_assessment
      ON classification_problem_enrichment(assessment);
    """)


def import_enrichment(db, folder):
    folder = Path(folder)
    state = json.loads((folder / "state.json").read_text(encoding="utf-8"))
    inputs = json.loads((folder / "input.json").read_text(encoding="utf-8"))
    rows = json.loads((folder / "assessments.json").read_text(encoding="utf-8"))
    if state.get("status") != "finished_with_review_required":
        raise ValueError("Only a completed enrichment run can enter production")
    if len(rows) != state.get("processed_symbols") or len(rows) != inputs.get("symbols"):
        raise ValueError("Enrichment artifacts do not reconcile")
    known = {row[0] for row in db.execute(
        "SELECT problem_id FROM classification_problem_item WHERE run_id=?", (inputs["problem_run_id"],)
    )}
    unknown = {row["problem_id"] for row in rows} - known
    if unknown:
        raise ValueError("Enrichment contains unknown problem IDs")
    enrichment_schema(db)
    stamp = datetime.now(timezone.utc).isoformat()
    with db:
        db.executemany(
            "INSERT INTO classification_problem_enrichment VALUES(?,?,?,?,?)",
            [(row["problem_id"], state["job_id"], row["assessment"],
              json.dumps(row, ensure_ascii=False), stamp) for row in rows],
        )
    return {"job_id": state["job_id"], "imported": len(rows), "status": "evidence_imported"}


def reconcile_applied_batch(db, run_id, batch_id):
    """Close exact-run problem items resolved by an already applied batch."""
    schema(db)
    batch = db.execute(
        "SELECT status FROM classification_change_batch WHERE batch_id=?", (batch_id,)
    ).fetchone()
    if not batch or batch["status"] != "applied":
        raise ValueError("Only an applied classification batch can resolve problems")
    if not db.execute("SELECT 1 FROM classification_problem_run WHERE run_id=?", (run_id,)).fetchone():
        raise ValueError("Unknown problem-registry run")
    events = db.execute(
        "SELECT snapshot_id,venue_code,ticker,after_json FROM classification_change_event "
        "WHERE batch_id=? AND action='apply'", (batch_id,)
    ).fetchall()
    stamp = datetime.now(timezone.utc).isoformat()
    resolved = 0
    with db:
        for event in events:
            after = json.loads(event["after_json"] or "{}")
            node = after.get("node_id")
            rows = db.execute(
                "SELECT problem_id,status FROM classification_problem_item "
                "WHERE run_id=? AND snapshot_id=? AND venue_code=? AND ticker=? "
                "AND proposed_node IS ? AND status='open'",
                (run_id, event["snapshot_id"], event["venue_code"], event["ticker"], node),
            ).fetchall()
            for row in rows:
                db.execute(
                    "INSERT OR IGNORE INTO classification_problem_resolution_event VALUES(?,?,?,?,?)",
                    (row["problem_id"], batch_id, row["status"], "resolved_applied", stamp),
                )
                db.execute(
                    "UPDATE classification_problem_item SET status='resolved_applied' WHERE problem_id=?",
                    (row["problem_id"],),
                )
                resolved += 1
        db.execute(
            "UPDATE classification_problem_group SET status=CASE WHEN EXISTS("
            "SELECT 1 FROM classification_problem_item i WHERE i.group_id=classification_problem_group.group_id "
            "AND i.status='open') THEN 'open' ELSE 'resolved' END WHERE run_id=?",
            (run_id,),
        )
        remaining = db.execute(
            "SELECT COUNT(*) FROM classification_problem_item WHERE run_id=? AND status='open'", (run_id,)
        ).fetchone()[0]
        db.execute(
            "UPDATE classification_problem_run SET status=? WHERE run_id=?",
            ("resolved" if remaining == 0 else "partially_resolved", run_id),
        )
    return {"run_id": run_id, "batch_id": batch_id, "resolved": resolved, "remaining_open": remaining}


def resolve_no_change_assessment(db, run_id, job_id, approved_by):
    """Close evidence-backed items that affirm the effective classification.

    This writes resolution state only. It never changes a listing node or
    instrument type.
    """
    schema(db)
    enrichment_schema(db)
    existing = db.execute(
        "SELECT batch_id,status,item_count FROM classification_problem_resolution_batch "
        "WHERE run_id=? AND job_id=? AND assessment='supports_current_taxonomy' AND status='applied'",
        (run_id, job_id),
    ).fetchone()
    if existing:
        return {
            "run_id": run_id, "batch_id": existing["batch_id"], "resolved": existing["item_count"],
            "remaining_open": db.execute(
                "SELECT COUNT(*) FROM classification_problem_item WHERE run_id=? AND status='open'", (run_id,)
            ).fetchone()[0], "status": "already_applied", "classification_writes": 0,
        }
    rows = db.execute(
        "SELECT i.problem_id,i.group_id,i.snapshot_id,i.venue_code,i.ticker,i.status,e.payload_json "
        "FROM classification_problem_item i JOIN classification_problem_enrichment e "
        "ON e.problem_id=i.problem_id WHERE i.run_id=? AND i.status='open' "
        "AND e.job_id=? AND e.assessment='supports_current_taxonomy' "
        "ORDER BY i.problem_id",
        (run_id, job_id),
    ).fetchall()
    if not rows:
        raise ValueError("No open supports-current items found")
    manifest_items = []
    for row in rows:
        payload = json.loads(row["payload_json"])
        relation = payload.get("taxonomy_relation")
        if relation not in {"same_node", "deeper_assignment", "existing_more_specific"}:
            raise ValueError(f"Unsafe supports-current relation for {row['ticker']}: {relation}")
        sources = payload.get("source_refs") or []
        usable = [source for source in sources if source.get("status") == "ok" and source.get("hash")]
        if not usable:
            raise ValueError(f"No usable evidence for {row['ticker']}")
        expected = str(payload.get("expected_sec_cik") or "").lstrip("0")
        observed = {str(value).lstrip("0") for value in payload.get("observed_ciks") or [] if str(value).strip("0")}
        if expected and any(value != expected for value in observed):
            raise ValueError(f"Identity conflict for {row['ticker']}")
        current = db.execute(
            "SELECT node_id,instrument_type FROM effective_listed_symbol "
            "WHERE snapshot_id=? AND venue_code=? AND ticker=?",
            (row["snapshot_id"], row["venue_code"], row["ticker"]),
        ).fetchone()
        if not current or current["node_id"] != payload.get("current_node"):
            raise ValueError(f"Stale effective classification for {row['ticker']}")
        manifest_items.append({
            "problem_id": row["problem_id"], "ticker": row["ticker"],
            "node_id": current["node_id"], "instrument_type": current["instrument_type"],
            "relation": relation, "evidence_hashes": sorted(source["hash"] for source in usable),
        })
    manifest = {
        "run_id": run_id, "job_id": job_id, "assessment": "supports_current_taxonomy",
        "resolution": "resolved_no_change", "items": manifest_items,
    }
    manifest_sha256 = digest(manifest)
    batch_id = "problem-resolution-" + manifest_sha256[:24]
    stamp = datetime.now(timezone.utc).isoformat()
    with db:
        db.execute(
            "INSERT INTO classification_problem_resolution_batch VALUES(?,?,?,?,?,?,?,?,?)",
            (batch_id, run_id, job_id, "supports_current_taxonomy", manifest_sha256,
             approved_by, stamp, "applied", len(rows)),
        )
        for row in rows:
            db.execute(
                "INSERT INTO classification_problem_resolution_event VALUES(?,?,?,?,?)",
                (row["problem_id"], batch_id, row["status"], "resolved_no_change", stamp),
            )
            db.execute(
                "UPDATE classification_problem_item SET status='resolved_no_change' WHERE problem_id=?",
                (row["problem_id"],),
            )
        db.execute(
            "UPDATE classification_problem_group SET status=CASE WHEN EXISTS("
            "SELECT 1 FROM classification_problem_item i WHERE i.group_id=classification_problem_group.group_id "
            "AND i.status='open') THEN 'open' ELSE 'resolved' END WHERE run_id=?",
            (run_id,),
        )
        remaining = db.execute(
            "SELECT COUNT(*) FROM classification_problem_item WHERE run_id=? AND status='open'", (run_id,)
        ).fetchone()[0]
        db.execute(
            "UPDATE classification_problem_run SET status=? WHERE run_id=?",
            ("resolved" if remaining == 0 else "partially_resolved", run_id),
        )
    return {
        "run_id": run_id, "batch_id": batch_id, "manifest_sha256": manifest_sha256,
        "resolved": len(rows), "remaining_open": remaining, "status": "applied",
        "classification_writes": 0,
    }


def import_job(db, folder):
    folder = Path(folder); state = json.loads((folder / "state.json").read_text(encoding="utf-8"))
    if state.get("status") != "finished_with_review_required":
        raise ValueError("Only a complete reconciled run can enter the production problem registry")
    payload = json.loads((folder / "problem_registry.json").read_text(encoding="utf-8"))
    if not payload.get("reconciled"):
        raise ValueError("Problem artifacts do not reconcile")
    schema(db); stamp = datetime.now(timezone.utc).isoformat(); run_id = payload["run_id"]
    with db:
        db.execute("INSERT INTO classification_problem_run VALUES(?,?,?,?,?,?,?)",
                   (run_id, payload["snapshot_id"], payload["taxonomy_version"], str(folder.resolve()),
                    "imported_open", stamp, json.dumps(payload["counts"], ensure_ascii=False)))
        db.executemany("INSERT INTO classification_problem_group VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            [(g["group_id"],run_id,g["category"],g["group_key"],g["sic"],g["old_sector"],g["new_sector"],
              g["proposed_instrument_type"],g["resolution"],g["status"],g["count"],json.dumps(g["sample_tickers"])) for g in payload["groups"]])
        db.executemany("INSERT INTO classification_problem_item VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            [(i["problem_id"],run_id,i["group_id"],i["snapshot_id"],i["taxonomy_version"],i["venue_code"],i["ticker"],i["name"],
              i["category"],i["sic"],i["old_node"],i["proposed_node"],i["old_instrument_type"],i["proposed_instrument_type"],
              i["worker_status"],i["reason"],i["relation"],i["resolution"],i["status"],) for i in payload["items"]])
    return {"run_id": run_id, "groups": len(payload["groups"]), "items": len(payload["items"]), "status": "imported_open"}


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__); parser.add_argument("--db",type=Path,default=DEFAULT_DB)
    sub=parser.add_subparsers(dest="command",required=True); sub.add_parser("install")
    p=sub.add_parser("import"); p.add_argument("--job",type=Path,required=True)
    p=sub.add_parser("reconcile-batch"); p.add_argument("--run-id",required=True); p.add_argument("--batch-id",required=True)
    p=sub.add_parser("import-enrichment"); p.add_argument("--job",type=Path,required=True)
    p=sub.add_parser("resolve-no-change"); p.add_argument("--run-id",required=True); p.add_argument("--job-id",required=True); p.add_argument("--approved-by",required=True)
    args=parser.parse_args(argv); db=connect(args.db)
    try:
        if args.command == "install": schema(db); db.commit(); result={"status":"installed"}
        elif args.command == "import": result=import_job(db,args.job)
        elif args.command == "reconcile-batch": result=reconcile_applied_batch(db,args.run_id,args.batch_id)
        elif args.command == "resolve-no-change": result=resolve_no_change_assessment(db,args.run_id,args.job_id,args.approved_by)
        else: result=import_enrichment(db,args.job)
    finally: db.close()
    print(json.dumps(result,ensure_ascii=False,indent=2)); return 0


if __name__ == "__main__": raise SystemExit(main())
