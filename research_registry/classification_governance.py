"""Prepare, validate, apply and roll back reviewed Babis classification batches.

No command performs network or model calls. ``prepare`` is read-only with respect
to the registry. ``apply`` requires an approval file bound to the batch hash.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

from .core import DEFAULT_DB, PROJECT, connect, digest, taxonomy

POLICY_PATH = Path(__file__).with_name("classification_policy.json")
DEFAULT_BATCHES = PROJECT / "data/classification_batches"
VENUE_CODES = {"Nasdaq": "Q", "NYSE": "N", "NYSE American": "A", "NYSE Arca": "P", "Cboe BZX": "Z", "IEX": "V"}


def now():
    return datetime.now(timezone.utc).isoformat()


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write_json(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def file_sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def disposition(row, nodes=None):
    nodes = nodes or taxonomy()[1]
    policy = read_json(POLICY_PATH)
    status = row.get("status")
    relation = row.get("relation")
    node = row.get("proposed_node")
    basis = row.get("reason")
    source_ok = row.get("source_status") == "ok"
    source_ref = row.get("source_ref") or {}

    if status in {"retained_instrument_category", "confirmed_same_node", "confirmed_existing_more_specific"}:
        return "no_change", status
    if node and node not in nodes:
        return "rejected", "node_outside_current_taxonomy"
    if status == "human_decision":
        # A person's decision (e.g. from the control center); applying it still needs
        # an explicit approve decision with a rationale in the approval file.
        return "human_review", basis or "owner_decision"
    if status in {"reassessment_agreement", "reassessment_review"}:
        # Multi-source reassessment rows (classification_reassessment.py). Agreement of
        # independent sources is approvable when the policy enables it; the rest is review.
        auto = policy.get("automatic_agreement") or {}
        if status == "reassessment_agreement" and auto.get("enabled"):
            return "approval_candidate", basis or "multi_source_agreement"
        return "human_review", basis or "multi_source_disagreement"
    if status == "identity_review":
        return "human_review", "identity_conflict"
    if status == "asset_type_review":
        return "human_review", "asset_type_change"
    if row.get("proposed_instrument_type") and row.get("proposed_instrument_type") != row.get("old_instrument_type"):
        return "human_review", "asset_type_change"
    if relation in {"cross_sector_conflict", "different_branch_same_sector"}:
        return "human_review", relation
    if not source_ok or not all(source_ref.get(k) for k in ("provider", "url", "observed_at", "hash")):
        return "human_review", "missing_or_unavailable_evidence"
    if status == "classification_proposal_review" and relation in {"new_assignment", "deeper_assignment"}:
        observed_cik = str((source_ref.get("details") or {}).get("cik") or "").strip()
        if basis == "explicit_sic_crosswalk" and observed_cik:
            sic = str((source_ref.get("details") or {}).get("sic") or (source_ref.get("details") or {}).get("sic_code") or "").strip()
            controls = policy.get("sic_candidate_controls") or {}
            if sic in (controls.get("semantic_review_codes") or {}):
                return "human_review", "ambiguous_exact_sic_requires_semantic_review"
            if sic in set(controls.get("allowed_exact_codes") or []):
                return "approval_candidate", "allowed_exact_crosswalk_with_evidence"
            return "human_review", "exact_sic_not_in_production_allowlist"
        return "human_review", "range_mapping_or_incomplete_identity"
    return "human_review", basis or status or "unclassified_result"


def make_change(row, snapshot_id, nodes=None):
    decision, decision_reason = disposition(row, nodes)
    item = {
        "change_id": digest([snapshot_id, row.get("venue"), row.get("ticker"), row.get("old_node"),
                             row.get("proposed_node"), row.get("old_instrument_type"),
                             row.get("proposed_instrument_type")]),
        "snapshot_id": snapshot_id,
        "venue": row.get("venue"),
        "venue_code": row.get("venue_code") or VENUE_CODES.get(row.get("venue")),
        "ticker": row.get("ticker"),
        "name": row.get("name"),
        "old_node": row.get("old_node"),
        "new_node": row.get("proposed_node"),
        "old_instrument_type": row.get("old_instrument_type"),
        "new_instrument_type": row.get("proposed_instrument_type") or row.get("old_instrument_type"),
        "source_status": row.get("source_status"),
        "source_ref": row.get("source_ref"),
        "worker_status": row.get("status"),
        "relation": row.get("relation"),
        "basis": row.get("reason"),
        "disposition": decision,
        "disposition_reason": decision_reason,
    }
    item["changes_value"] = (item["old_node"] != item["new_node"] and item["new_node"] is not None) or (
        item["old_instrument_type"] != item["new_instrument_type"]) or bool(row.get("confirm_current"))
    if row.get("confirm_current"):
        # Same node, but a person confirmed it: the value written is the approval.
        item["confirm_current"] = True
    if not item["venue_code"]:
        item["disposition"], item["disposition_reason"] = "human_review", "unmapped_venue"
    return item


def prepare_batch(job, out=None):
    job = Path(job)
    state, inputs, rows = (read_json(job / name) for name in ("state.json", "input.json", "proposals.json"))
    if state.get("status") != "finished_with_review_required":
        raise ValueError("Babis job must be finished before a batch is prepared")
    tax, nodes = taxonomy()
    if inputs.get("taxonomy_sha256") != digest(tax):
        raise ValueError("Taxonomy hash differs from the completed job")
    if len(rows) != inputs.get("symbols"):
        raise ValueError("Proposal count does not reconcile to the job input")
    policy = read_json(POLICY_PATH)
    batch_id = "classification-" + job.name + "-policy-" + policy["version"]
    out = Path(out or (DEFAULT_BATCHES / batch_id))
    out.mkdir(parents=True, exist_ok=False)
    changes = [make_change(row, inputs["snapshot_id"], nodes) for row in rows]
    write_json(out / "changes.json", changes)
    review = [row for row in changes if row["disposition"] == "human_review"]
    write_json(out / "review_queue.json", review)
    planned = [row for row in changes if row["disposition"] == "approval_candidate"]
    write_json(out / "planned_changes.json", planned)
    grouped = {}
    for row in review:
        details = (row.get("source_ref") or {}).get("details") or {}
        key = (row["disposition_reason"], row.get("relation") or "none", str(details.get("sic") or details.get("sic_code") or "none"),
               str(row.get("old_node") or "none").split(".")[0], str(row.get("new_node") or "none").split(".")[0])
        group = grouped.setdefault(key, {"disposition_reason": key[0], "relation": key[1], "sic": key[2],
                                         "old_sector": key[3], "new_sector": key[4], "count": 0, "sample_tickers": []})
        group["count"] += 1
        if len(group["sample_tickers"]) < 10: group["sample_tickers"].append(row["ticker"])
    review_groups = sorted(grouped.values(), key=lambda row: (-row["count"], row["disposition_reason"], row["sic"]))
    write_json(out / "review_groups.json", review_groups)
    manifest = {
        "batch_id": batch_id,
        "created_at": now(),
        "source_job": str(job.resolve()),
        "source_job_hashes": {name: file_sha256(job / name) for name in ("state.json", "input.json", "proposals.json")},
        "snapshot_id": inputs["snapshot_id"],
        "taxonomy_version": tax["version"],
        "taxonomy_sha256": digest(tax),
        "policy_version": policy["version"],
        "policy_sha256": file_sha256(POLICY_PATH),
        "counts": dict(Counter(row["disposition"] for row in changes)),
        "changes_sha256": file_sha256(out / "changes.json"),
        "review_queue_sha256": file_sha256(out / "review_queue.json"),
        "planned_changes_sha256": file_sha256(out / "planned_changes.json"),
        "review_groups_sha256": file_sha256(out / "review_groups.json"),
        "registry_writes": 0,
    }
    manifest["manifest_sha256"] = digest(manifest)
    write_json(out / "manifest.json", manifest)
    summary = [
        "# Classification production batch", "", f"Batch: `{batch_id}`", "",
        "Το batch είναι dry-run και δεν έχει γράψει στο registry.", "",
        "| Απόφαση | Πλήθος |", "| --- | ---: |",
    ]
    summary += [f"| {key} | {value} |" for key, value in sorted(manifest["counts"].items())]
    summary += ["", "Εφαρμογή επιτρέπεται μόνο για εγκεκριμένα change IDs με approval file δεμένο στο manifest hash."]
    (out / "summary.md").write_text("\n".join(summary), encoding="utf-8")
    return manifest


def validate_batch(folder):
    folder = Path(folder)
    manifest = read_json(folder / "manifest.json")
    claimed = manifest.pop("manifest_sha256")
    if digest(manifest) != claimed:
        raise ValueError("Manifest hash mismatch")
    checks = {"changes.json": "changes_sha256", "review_queue.json": "review_queue_sha256",
              "planned_changes.json": "planned_changes_sha256", "review_groups.json": "review_groups_sha256"}
    for filename, field in checks.items():
        if file_sha256(folder / filename) != manifest[field]:
            raise ValueError(filename + " hash mismatch")
    if file_sha256(POLICY_PATH) != manifest["policy_sha256"] or digest(taxonomy()[0]) != manifest["taxonomy_sha256"]:
        raise ValueError("Policy or taxonomy changed after batch creation")
    manifest["manifest_sha256"] = claimed
    return manifest


def preflight_batch(db, folder):
    """Read-only reconciliation of planned changes against the current registry."""
    manifest = validate_batch(folder)
    if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='listed_symbol'").fetchone():
        raise ValueError("Registry has no listed_symbol snapshot table")
    has_overrides = bool(db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='listing_classification_override'").fetchone())
    planned = read_json(Path(folder) / "planned_changes.json")
    result = {"batch_id": manifest["batch_id"], "planned": len(planned), "matched": 0,
              "missing_listing": [], "stale_baseline": [], "existing_overrides": 0, "registry_writes": 0}
    for row in planned:
        key = (row["snapshot_id"], row["venue_code"], row["ticker"])
        base = db.execute("SELECT instrument_type,node_id FROM listed_symbol WHERE snapshot_id=? AND venue_code=? AND ticker=?", key).fetchone()
        if not base:
            result["missing_listing"].append({"venue_code": row["venue_code"], "ticker": row["ticker"]}); continue
        current_type, current_node = base["instrument_type"], base["node_id"]
        if has_overrides:
            override = db.execute("SELECT instrument_type,node_id FROM listing_classification_override WHERE snapshot_id=? AND venue_code=? AND ticker=?", key).fetchone()
            if override:
                result["existing_overrides"] += 1; current_type, current_node = override["instrument_type"], override["node_id"]
        if current_type != row["old_instrument_type"] or current_node != row["old_node"]:
            result["stale_baseline"].append({"venue_code": row["venue_code"], "ticker": row["ticker"],
                                             "expected_type": row["old_instrument_type"], "actual_type": current_type,
                                             "expected_node": row["old_node"], "actual_node": current_node})
        else: result["matched"] += 1
    result["ready"] = not result["missing_listing"] and not result["stale_baseline"] and result["matched"] == result["planned"]
    return result


def governance_schema(db):
    db.executescript("""
    CREATE TABLE IF NOT EXISTS classification_change_batch(
      batch_id TEXT PRIMARY KEY, manifest_sha256 TEXT NOT NULL, snapshot_id TEXT NOT NULL,
      taxonomy_version TEXT NOT NULL, approved_by TEXT NOT NULL, approved_at TEXT NOT NULL,
      applied_at TEXT NOT NULL, status TEXT NOT NULL CHECK(status IN ('applied','rolled_back')));
    CREATE TABLE IF NOT EXISTS listing_classification_override(
      snapshot_id TEXT NOT NULL, venue_code TEXT NOT NULL, ticker TEXT NOT NULL,
      instrument_type TEXT NOT NULL, node_id TEXT, status TEXT NOT NULL,
      batch_id TEXT NOT NULL REFERENCES classification_change_batch, updated_at TEXT NOT NULL,
      PRIMARY KEY(snapshot_id,venue_code,ticker));
    CREATE TABLE IF NOT EXISTS classification_change_event(
      event_id TEXT PRIMARY KEY, batch_id TEXT NOT NULL REFERENCES classification_change_batch,
      change_id TEXT NOT NULL, snapshot_id TEXT NOT NULL, venue_code TEXT NOT NULL, ticker TEXT NOT NULL,
      before_json TEXT, after_json TEXT, action TEXT NOT NULL CHECK(action IN ('apply','rollback')),
      actor TEXT NOT NULL, recorded_at TEXT NOT NULL);
    CREATE VIEW IF NOT EXISTS effective_listed_symbol AS
      SELECT l.snapshot_id,l.venue_code,l.ticker,l.name,
             CASE WHEN o.ticker IS NULL THEN l.instrument_type ELSE o.instrument_type END AS instrument_type,
             l.scheme,l.version,
             CASE WHEN o.ticker IS NULL THEN l.node_id ELSE o.node_id END AS node_id,
             CASE WHEN o.ticker IS NULL THEN l.status ELSE o.status END AS status,
             l.payload_json,o.batch_id AS override_batch_id
      FROM listed_symbol l
      LEFT JOIN listing_classification_override o
        ON o.snapshot_id=l.snapshot_id AND o.venue_code=l.venue_code AND o.ticker=l.ticker;
    """)


def _approved(folder, manifest, approval_path):
    approval = read_json(approval_path)
    if approval.get("batch_id") != manifest["batch_id"] or approval.get("manifest_sha256") != manifest["manifest_sha256"]:
        raise ValueError("Approval is not bound to this immutable batch")
    if not approval.get("approved_by") or not approval.get("approved_at"):
        raise ValueError("Approval identity and timestamp are required")
    requested = set(approval.get("approved_change_ids") or [])
    changes = {row["change_id"]: row for row in read_json(Path(folder) / "changes.json")}
    unknown = requested - set(changes)
    if unknown:
        raise ValueError("Approval contains unknown change IDs")
    selected = []
    decisions = approval.get("decisions") or {}
    for change_id in requested:
        row = changes[change_id]
        if row["disposition"] not in {"approval_candidate", "human_review"} or not row["changes_value"]:
            raise ValueError("Non-change or rejected item cannot be applied: " + change_id)
        if row["disposition"] == "human_review":
            decision = decisions.get(change_id) or {}
            if decision.get("outcome") != "approve" or not str(decision.get("rationale") or "").strip():
                raise ValueError("Human-review changes require an explicit approve decision and rationale")
        selected.append(row)
    return approval, selected


def apply_batch(db, folder, approval_path):
    manifest = validate_batch(folder)
    approval, selected = _approved(folder, manifest, approval_path)
    governance_schema(db)
    if db.execute("SELECT 1 FROM classification_change_batch WHERE batch_id=?", (manifest["batch_id"],)).fetchone():
        raise ValueError("Batch was already applied")
    stamp = now()
    with db:
        db.execute("INSERT INTO classification_change_batch VALUES(?,?,?,?,?,?,?,?)",
                   (manifest["batch_id"], manifest["manifest_sha256"], manifest["snapshot_id"],
                    manifest["taxonomy_version"], approval["approved_by"], approval["approved_at"], stamp, "applied"))
        for row in selected:
            key = (row["snapshot_id"], row["venue_code"], row["ticker"])
            previous = db.execute("SELECT * FROM listing_classification_override WHERE snapshot_id=? AND venue_code=? AND ticker=?", key).fetchone()
            before = dict(previous) if previous else None
            base_table = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='listed_symbol'").fetchone()
            if not base_table:
                raise ValueError("Registry has no listed_symbol snapshot table")
            base = db.execute("SELECT instrument_type,node_id FROM listed_symbol WHERE snapshot_id=? AND venue_code=? AND ticker=?", key).fetchone()
            if not base:
                raise ValueError("Approved change does not match a registry listing")
            current_type = before["instrument_type"] if before else base["instrument_type"]
            current_node = before["node_id"] if before else base["node_id"]
            if current_type != row["old_instrument_type"] or current_node != row["old_node"]:
                raise ValueError("Stale change: current classification differs from batch baseline")
            after = {"snapshot_id": row["snapshot_id"], "venue_code": row["venue_code"], "ticker": row["ticker"],
                     "instrument_type": row["new_instrument_type"], "node_id": row["new_node"],
                     "status": "approved_provisional", "batch_id": manifest["batch_id"], "updated_at": stamp}
            db.execute("INSERT OR REPLACE INTO listing_classification_override VALUES(?,?,?,?,?,?,?,?)", tuple(after.values()))
            db.execute("INSERT INTO classification_change_event VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                       (digest([manifest["batch_id"], row["change_id"], "apply"]), manifest["batch_id"], row["change_id"],
                        *key, json.dumps(before), json.dumps(after), "apply", approval["approved_by"], stamp))
    return {"batch_id": manifest["batch_id"], "applied": len(selected), "status": "applied"}


def rollback_batch(db, batch_id, actor):
    governance_schema(db)
    batch = db.execute("SELECT * FROM classification_change_batch WHERE batch_id=?", (batch_id,)).fetchone()
    if not batch or batch["status"] != "applied":
        raise ValueError("Batch is not in applied state")
    events = db.execute("SELECT * FROM classification_change_event WHERE batch_id=? AND action='apply' ORDER BY rowid DESC", (batch_id,)).fetchall()
    stamp = now()
    with db:
        for event in events:
            key = (event["snapshot_id"], event["venue_code"], event["ticker"])
            current = db.execute("SELECT * FROM listing_classification_override WHERE snapshot_id=? AND venue_code=? AND ticker=?", key).fetchone()
            if current and current["batch_id"] != batch_id:
                raise ValueError("A later batch depends on this classification; roll it back first")
            before = json.loads(event["before_json"]) if event["before_json"] else None
            if before:
                db.execute("INSERT OR REPLACE INTO listing_classification_override VALUES(?,?,?,?,?,?,?,?)",
                           tuple(before[key] for key in ("snapshot_id", "venue_code", "ticker", "instrument_type", "node_id", "status", "batch_id", "updated_at")))
            else:
                db.execute("DELETE FROM listing_classification_override WHERE snapshot_id=? AND venue_code=? AND ticker=?", key)
            db.execute("INSERT INTO classification_change_event VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                       (digest([batch_id, event["change_id"], "rollback"]), batch_id, event["change_id"], *key,
                        json.dumps(dict(current)) if current else None, json.dumps(before), "rollback", actor, stamp))
        db.execute("UPDATE classification_change_batch SET status='rolled_back' WHERE batch_id=?", (batch_id,))
    return {"batch_id": batch_id, "rolled_back": len(events), "status": "rolled_back"}


def carry_forward_overrides(db, target_snapshot, actor="carry_forward"):
    """Re-apply approved overrides to a newer universe snapshot.

    Overrides are keyed by snapshot, and every daily run builds a new snapshot,
    so approved corrections silently stopped applying after one day. For each
    listing, the most recent approved override is carried to ``target_snapshot``
    only when the listing's base classification there is the same one the
    override was approved against, or has since become unknown. If the provider data changed in between, the
    override is not applied blindly; it is reported for review instead. The
    carry is recorded as its own batch, so ``rollback`` undoes it.
    """
    governance_schema(db)
    target = db.execute("SELECT snapshot_id FROM universe_snapshot WHERE snapshot_id=?", (target_snapshot,)).fetchone()
    if not target:
        raise ValueError("Unknown target snapshot: " + str(target_snapshot))
    candidates = db.execute(
        "SELECT o.snapshot_id, o.venue_code, o.ticker, o.instrument_type, o.node_id, o.status, o.batch_id, o.updated_at, "
        "l.instrument_type AS base_type, l.node_id AS base_node "
        "FROM listing_classification_override o "
        "JOIN classification_change_batch b ON b.batch_id=o.batch_id AND b.status='applied' "
        "JOIN listed_symbol l ON l.snapshot_id=o.snapshot_id AND l.venue_code=o.venue_code AND l.ticker=o.ticker "
        "WHERE o.snapshot_id != ? ORDER BY o.updated_at DESC, o.rowid DESC", (target_snapshot,)).fetchall()
    latest = {}
    for row in candidates:
        latest.setdefault((row["venue_code"], row["ticker"]), row)
    carry, base_changed, missing = [], [], []
    already = 0
    for (venue, ticker), row in sorted(latest.items()):
        if db.execute("SELECT 1 FROM listing_classification_override WHERE snapshot_id=? AND venue_code=? AND ticker=?",
                      (target_snapshot, venue, ticker)).fetchone():
            already += 1
            continue
        base = db.execute("SELECT instrument_type,node_id,version FROM listed_symbol WHERE snapshot_id=? AND venue_code=? AND ticker=?",
                          (target_snapshot, venue, ticker)).fetchone()
        if not base:
            missing.append({"venue_code": venue, "ticker": ticker})
            continue
        if (base["instrument_type"], base["node_id"]) == (row["instrument_type"], row["node_id"]):
            already += 1  # the new snapshot already says what was approved
            continue
        # A base that became unknown (e.g. a guessed sector was withdrawn) cannot
        # contradict the approved value, so the approval still stands.
        withdrawn = base["node_id"] is None and base["instrument_type"] == row["base_type"]
        if (base["instrument_type"], base["node_id"]) != (row["base_type"], row["base_node"]) and not withdrawn:
            base_changed.append({"venue_code": venue, "ticker": ticker, "approved_node": row["node_id"],
                                 "approved_against": row["base_node"], "new_base": base["node_id"],
                                 "from_batch": row["batch_id"]})
            continue
        carry.append((row, base["version"]))
    result = {"target_snapshot": target_snapshot, "carried": len(carry), "already_current": already,
              "base_changed_needs_review": base_changed, "listing_missing": missing, "batch_id": None}
    if not carry:
        return result
    stamp = now()
    manifest = [[row["venue_code"], row["ticker"], row["instrument_type"], row["node_id"], row["batch_id"]] for row, _ in carry]
    batch_id = "carry-forward-" + target_snapshot[:12] + "-" + digest(manifest)[:16]
    with db:
        db.execute("INSERT INTO classification_change_batch VALUES(?,?,?,?,?,?,?,?)",
                   (batch_id, digest(manifest), target_snapshot, carry[0][1] or "", actor, stamp, stamp, "applied"))
        for row, _ in carry:
            key = (target_snapshot, row["venue_code"], row["ticker"])
            after = {"snapshot_id": target_snapshot, "venue_code": row["venue_code"], "ticker": row["ticker"],
                     "instrument_type": row["instrument_type"], "node_id": row["node_id"], "status": row["status"],
                     "batch_id": batch_id, "updated_at": stamp}
            db.execute("INSERT INTO listing_classification_override VALUES(?,?,?,?,?,?,?,?)", tuple(after.values()))
            lineage = {**after, "carried_from_batch": row["batch_id"], "carried_from_snapshot": row["snapshot_id"]}
            db.execute("INSERT INTO classification_change_event VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                       (digest([batch_id, *key, "apply"]), batch_id, digest([row["batch_id"], *key]), *key,
                        None, json.dumps(lineage), "apply", actor, stamp))
    result["batch_id"] = batch_id
    return result


def latest_snapshot(db):
    row = db.execute("SELECT snapshot_id FROM universe_snapshot ORDER BY retrieved_at DESC, rowid DESC LIMIT 1").fetchone()
    return row["snapshot_id"] if row else None


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("prepare"); p.add_argument("--job", type=Path, required=True); p.add_argument("--out", type=Path)
    p = sub.add_parser("validate"); p.add_argument("--batch", type=Path, required=True)
    p = sub.add_parser("preflight"); p.add_argument("--batch", type=Path, required=True)
    p = sub.add_parser("apply"); p.add_argument("--batch", type=Path, required=True); p.add_argument("--approval", type=Path, required=True)
    p = sub.add_parser("rollback"); p.add_argument("--batch-id", required=True); p.add_argument("--actor", required=True)
    p = sub.add_parser("carry-forward", help="Re-apply approved overrides to a newer snapshot (default: latest).")
    p.add_argument("--snapshot", default="latest")
    args = parser.parse_args(argv)
    if args.command == "carry-forward":
        db = connect(args.db)
        try:
            snapshot = latest_snapshot(db) if args.snapshot == "latest" else args.snapshot
            result = carry_forward_overrides(db, snapshot)
        finally:
            db.close()
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    if args.command == "prepare": result = prepare_batch(args.job, args.out)
    elif args.command == "validate": result = validate_batch(args.batch)
    elif args.command == "preflight":
        db = connect(args.db)
        try: result = preflight_batch(db, args.batch)
        finally: db.close()
    else:
        db = connect(args.db)
        try:
            result = apply_batch(db, args.batch, args.approval) if args.command == "apply" else rollback_batch(db, args.batch_id, args.actor)
        finally:
            db.close()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
