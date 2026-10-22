"""Multi-source classification reassessment: close Babis's evidence loop.

Babis collects evidence every day (FMP profiles through the babis-gaps task,
SEC submissions through the SEC catalog), but nothing turned that evidence
back into classification decisions, so problems stayed "deferred" forever.
This pass re-decides every company of a snapshot from independent sources that
are already stored locally - no network and no model calls:

- ``directory``: the listing's base node from the Nasdaq directory/screener crosswalk;
- ``profile``: the commercial company profile (FMP) sector/industry, mapped by
  ``research_registry.core.classify``;
- ``sec``: the SEC SIC code mapped by ``babis_catalog_v2.sic_candidate``; codes the
  policy marks as ambiguous (2834 pharma/biotech, 6798 REIT type) only vote up
  to their parent node;
- ``description``: optional, a node proposed from the company description by a
  local model (added by a later pass; this module accepts it as an extra vote).

Decision (policy ``automatic_agreement``): walk down the taxonomy while at least
two independent sources agree and no other pair disagrees. That node is
corroborated. The current node is kept when it is more specific and no source
contradicts it; otherwise the corroborated (safe ancestor) node is used. Nothing
is decided when fewer than two sources agree on a sector, or when the change
would contradict an earlier approved correction - those go to review with their
evidence. Output is a regular governance batch (prepare -> validate -> apply),
so every automatic decision stays hash-bound, auditable and reversible.
"""
from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
import glob
import json
from pathlib import Path
import re
import sqlite3
from typing import Any

from .babis_catalog_v2 import relation, sic_candidate
from .classification_governance import (
    POLICY_PATH, apply_batch, latest_snapshot, prepare_batch, read_json, validate_batch, write_json,
)
from .core import DEFAULT_DB, PROJECT, classify, connect, digest, taxonomy

JOBS_ROOT = PROJECT / "data" / "babis_reassessment"
CORPORATE = ("equity_candidate", "adr_candidate")
PROFILE_GLOBS = (
    "data/babis_catalog/*/evidence.sqlite3",
    "data/babis_enrichment/*/provider_evidence.sqlite3",
    "data/babis_gap_followup/*/provider_evidence.sqlite3",
)
SEC_GLOB = "data/babis_catalog/*/sec_evidence.sqlite3"
SPAC_NAME = re.compile(r"\b(acquisition|merger|blank check|spac)\b", re.IGNORECASE)
FUND_NAME = re.compile(r"\b(fund|trust|income|opportunities|municipal|closed[- ]end)\b", re.IGNORECASE)


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------- local evidence


def _payloads(pattern: str, root: Path):
    for path in sorted(glob.glob(str(root / pattern))):
        conn = sqlite3.connect(f"file:{Path(path).resolve().as_posix()}?mode=ro", uri=True)
        try:
            for (blob,) in conn.execute("SELECT payload FROM evidence"):
                try:
                    yield json.loads(blob)
                except json.JSONDecodeError:
                    continue
        except sqlite3.Error:
            continue
        finally:
            conn.close()


def load_profiles(root: Path = PROJECT) -> dict[str, dict[str, Any]]:
    """Newest successful company profile per ticker from every Babis cache."""
    profiles: dict[str, dict[str, Any]] = {}
    for payload in _payloads_multi(PROFILE_GLOBS, root):
        data = payload.get("data")
        if payload.get("status") != "ok" or not isinstance(data, dict) or not data.get("symbol"):
            continue
        if not (data.get("sector") or data.get("description")):
            continue
        ticker = str(data["symbol"]).upper()
        record = {"sector": data.get("sector"), "industry": data.get("industry"), "description": data.get("description"),
                  "is_fund": str(data.get("isFund")).lower() == "true", "is_etf": str(data.get("isEtf")).lower() == "true",
                  "cik": data.get("cik"), "observed_at": payload.get("observed_at"), "hash": payload.get("evidence_sha256"),
                  "provider": payload.get("provider") or "fmp"}
        if ticker not in profiles or str(record["observed_at"]) > str(profiles[ticker]["observed_at"]):
            profiles[ticker] = record
    return profiles


def _payloads_multi(patterns, root):
    for pattern in patterns:
        yield from _payloads(pattern, root)


def load_sec(root: Path = PROJECT) -> dict[str, dict[str, Any]]:
    """Newest SEC submission summary per ticker (via the tickers SEC lists for the CIK)."""
    by_ticker: dict[str, dict[str, Any]] = {}
    for payload in _payloads(SEC_GLOB, root):
        data = payload.get("data")
        if payload.get("status") != "ok" or not isinstance(data, dict):
            continue
        record = {"cik": data.get("cik"), "sic": str(data.get("sic") or "").strip(), "sic_description": data.get("sicDescription"),
                  "entity_type": str(data.get("entityType") or ""), "name": data.get("name"),
                  "observed_at": payload.get("observed_at"), "hash": payload.get("evidence_sha256")}
        for ticker in data.get("tickers") or []:
            key = str(ticker).upper()
            if key not in by_ticker or str(record["observed_at"]) > str(by_ticker[key]["observed_at"]):
                by_ticker[key] = record
    return by_ticker


# ---------------------------------------------------------------- votes and decision


@dataclass(frozen=True)
class Vote:
    source: str
    node: str
    basis: str


def _parent(node: str) -> str | None:
    return node.rsplit(".", 1)[0] if "." in node else None


CATCH_ALL_SIC = re.compile(r"\bNEC\b|\bmisc(ellaneous)?\b|non-?operating", re.IGNORECASE)


def sec_vote(sec: dict[str, Any] | None, policy: dict[str, Any], nodes: dict) -> Vote | None:
    if not sec or not sec.get("sic") or sec["sic"] == "6770":
        return None
    if CATCH_ALL_SIC.search(str(sec.get("sic_description") or "")) or sec["sic"] == "9995":
        # "Services-Business Services, NEC" (7389) and similar catch-all codes say
        # what a company is not, not what it is; they must not break a tie.
        return None
    node, basis = sic_candidate(sec["sic"])
    if not node or node not in nodes:
        return None
    if sec["sic"] in (policy.get("sic_candidate_controls") or {}).get("semantic_review_codes", {}):
        node = _parent(node)  # e.g. 2834 cannot tell pharma from biotech; 6798 cannot tell REIT type
        basis = "ambiguous_sic_parent_only"
        if not node:
            return None
    return Vote("sec", node, basis)


def profile_vote(profile: dict[str, Any] | None) -> Vote | None:
    if not profile or not profile.get("sector"):
        return None
    result = classify({"sector": profile.get("sector"), "industry": profile.get("industry") or ""})
    return Vote("profile", result["node_id"], result["basis"]) if result.get("node_id") else None


def _supports(vote_node: str, node: str) -> bool:
    return vote_node == node or vote_node.startswith(node + ".")


def corroborated_node(votes: list[Vote]) -> tuple[str | None, str]:
    """Deepest node that at least two independent sources support, and why the walk stopped."""
    current: str | None = None
    while True:
        children = set()
        for vote in votes:
            if current is None:
                children.add(vote.node.split(".")[0])
            elif vote.node.startswith(current + "."):
                children.add(current + "." + vote.node[len(current) + 1:].split(".")[0])
        strong = sorted(child for child in children if sum(_supports(v.node, child) for v in votes) >= 2)
        if len(strong) == 1:
            current = strong[0]
            continue
        if len(strong) > 1:
            return current, "competing_pairs"
        return current, "agreement_ends" if current else "no_two_sources_agree"


def decide(votes: list[Vote], current: str | None, current_status: str | None) -> dict[str, Any]:
    corroborated, why = corroborated_node(votes)
    if corroborated is None:
        if current and current_status == "approved_provisional":
            # Already approved (by agreement or a person); disagreeing sources are no
            # reason to reopen it. Two sources agreeing on something else would be.
            return {"decision": "confirmed", "reason": "approved_value_no_new_agreement", "node": current,
                    "corroborated": None}
        return {"decision": "review", "reason": why, "node": None, "corroborated": None}
    final = corroborated
    # Only votes on the corroborated path can veto extra depth: a source already
    # outvoted on the sector does not get to undo detail inside the winning sector.
    on_path = [v for v in votes if _supports(v.node, corroborated) or _supports(corroborated, v.node)]
    if current and current.startswith(corroborated + ".") and all(
            _supports(current, v.node) or _supports(v.node, current) for v in on_path):
        final = current  # more specific and contradicted by no agreeing source: keep it
    if final == current:
        return {"decision": "confirmed", "reason": "sources_agree_with_current", "node": final, "corroborated": corroborated}
    if current_status == "approved_provisional" and current and not final.startswith(current + "."):
        return {"decision": "review", "reason": "would_contradict_prior_approval", "node": final, "corroborated": corroborated}
    return {"decision": "change", "reason": f"agreement_{len(votes)}_sources_{why}", "node": final, "corroborated": corroborated}


def instrument_decision(listing: dict[str, Any], sec: dict[str, Any] | None,
                        profile: dict[str, Any] | None) -> tuple[str | None, str]:
    """Proposed corporate -> special instrument type, only when two signals agree."""
    name = str(listing.get("name") or "")
    spac_name = bool(SPAC_NAME.search(name))
    if sec and sec.get("sic") == "6770":
        return ("spac", "sic_6770_and_name") if spac_name else (None, "sic_6770_without_name_signal")
    if spac_name and profile and str(profile.get("industry") or "").lower() == "shell companies":
        # Many SPACs file under their intended target's SIC, so SEC alone misses them;
        # the provider's "Shell Companies" industry is the independent second signal.
        return "spac", "shell_company_profile_and_name"
    if spac_name:
        return None, "probable_spac_needs_second_signal"
    signals = sum([
        bool(sec and ("investment" in sec.get("entity_type", "").lower() or sec.get("sic") in {"6722", "6726"})),
        bool(FUND_NAME.search(name)),
        bool(profile and profile.get("is_fund")),
    ])
    if signals >= 2:
        return "fund_candidate", f"fund_signals_{signals}_of_3"
    return None, "no_type_change"


# ---------------------------------------------------------------- job + batch


def _effective_listings(db: sqlite3.Connection, snapshot: str) -> list[dict[str, Any]]:
    rows = db.execute(
        "SELECT e.venue_code, e.ticker, e.name, e.instrument_type, e.node_id, e.status, l.node_id AS base_node, "
        "json_extract(l.payload_json,'$.venue') AS venue, json_extract(l.payload_json,'$.reason') AS base_reason "
        "FROM effective_listed_symbol e JOIN listed_symbol l "
        "ON l.snapshot_id=e.snapshot_id AND l.venue_code=e.venue_code AND l.ticker=e.ticker "
        "WHERE e.snapshot_id=? ORDER BY e.ticker, e.venue_code", (snapshot,)).fetchall()
    return [dict(row) for row in rows]


def reassess_listing(listing: dict[str, Any], profile: dict[str, Any] | None, sec: dict[str, Any] | None,
                     policy: dict[str, Any], nodes: dict, extra_votes: list[Vote] = ()) -> dict[str, Any]:
    """One proposal row in the format classification_governance.make_change expects."""
    base = {"ticker": listing["ticker"], "venue": listing.get("venue"), "venue_code": listing["venue_code"],
            "name": listing.get("name"), "old_instrument_type": listing["instrument_type"], "old_node": listing["node_id"],
            "verified": False, "applied_to_registry": False, "source_status": "ok"}
    votes: list[Vote] = []
    if listing.get("base_node"):
        votes.append(Vote("directory", listing["base_node"], str(listing.get("base_reason") or "directory_crosswalk")))
    for vote in (profile_vote(profile), sec_vote(sec, policy, nodes)):
        if vote:
            votes.append(vote)
    # Local-LLM votes are evidence for the reviewer, never a decision: they are
    # shown next to the real sources but take no part in the agreement.
    advisory = [vote.__dict__ for vote in extra_votes if vote]
    details = {"votes": [vote.__dict__ for vote in votes], "advisory_votes": advisory, "sic": (sec or {}).get("sic"),
               "sic_description": (sec or {}).get("sic_description"), "cik": (sec or {}).get("cik"),
               "profile_sector": (profile or {}).get("sector"), "profile_industry": (profile or {}).get("industry"),
               "current_status": listing.get("status")}
    evidence_hashes = [(profile or {}).get("hash"), (sec or {}).get("hash")]
    source_ref = {"provider": "multi_source_reassessment", "url": "local-evidence://babis",
                  "observed_at": max(str((profile or {}).get("observed_at") or ""), str((sec or {}).get("observed_at") or "")) or now(),
                  "hash": digest([details, evidence_hashes]), "details": details}

    proposed_type, type_reason = instrument_decision(listing, sec, profile)
    if proposed_type and proposed_type != listing["instrument_type"]:
        return {**base, "proposed_instrument_type": proposed_type, "proposed_node": None, "relation": "no_candidate",
                "status": "reassessment_agreement", "reason": type_reason, "source_ref": source_ref}
    if type_reason in {"sic_6770_without_name_signal", "probable_spac_needs_second_signal"}:
        # One SPAC signal is not enough to change the type, and a probable shell
        # company must not be moved between issuer sectors either.
        return {**base, "proposed_instrument_type": "spac", "proposed_node": None, "relation": "no_candidate",
                "status": "reassessment_review", "reason": type_reason, "source_ref": source_ref}

    outcome = decide(votes, listing["node_id"], listing.get("status"))
    details["decision"] = outcome
    row = {**base, "proposed_instrument_type": listing["instrument_type"], "source_ref": source_ref,
           "relation": relation(listing["node_id"], outcome["node"])}
    if outcome["decision"] == "confirmed":
        return {**row, "status": "confirmed_same_node", "reason": outcome["reason"], "proposed_node": listing["node_id"]}
    if outcome["decision"] == "change":
        return {**row, "status": "reassessment_agreement", "reason": outcome["reason"], "proposed_node": outcome["node"]}
    return {**row, "status": "reassessment_review", "reason": outcome["reason"], "proposed_node": outcome["node"]}


def build_job(db: sqlite3.Connection, snapshot: str, *, root: Path = PROJECT, out_root: Path = JOBS_ROOT,
              extra_votes: dict[str, list[Vote]] | None = None, keep: set[str] | None = None) -> Path | None:
    """Write a governance job for ``snapshot``. With ``keep``, only rows with those
    statuses are written (the daily hook keeps just the changes), and no folder is
    created when none remain."""
    rows, meta = reassess_rows(db, snapshot, root=root, extra_votes=extra_votes)
    return write_job(rows, snapshot, meta, out_root=out_root, keep=keep)


def reassess_rows(db: sqlite3.Connection, snapshot: str, *, root: Path = PROJECT,
                  extra_votes: dict[str, list[Vote]] | None = None) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Proposal rows for every listing of ``snapshot`` (nothing is written)."""
    policy = read_json(POLICY_PATH)
    tax, nodes = taxonomy()
    profiles, secs = load_profiles(root), load_sec(root)
    rows = []
    for listing in _effective_listings(db, snapshot):
        if listing["instrument_type"] not in CORPORATE:
            rows.append({"ticker": listing["ticker"], "venue": listing.get("venue"), "venue_code": listing["venue_code"],
                         "name": listing.get("name"), "old_instrument_type": listing["instrument_type"],
                         "old_node": listing["node_id"], "proposed_node": None, "proposed_instrument_type": None,
                         "status": "retained_instrument_category", "reason": "separate_asset_classification",
                         "relation": "no_candidate"})
            continue
        ticker = listing["ticker"].upper()
        rows.append(reassess_listing(listing, profiles.get(ticker), secs.get(ticker), policy, nodes,
                                     (extra_votes or {}).get(ticker, [])))
    meta = {"taxonomy": tax, "policy_version": policy["version"], "profiles_available": len(profiles),
            "sec_records_available": len(secs)}
    return rows, meta


def write_job(rows: list[dict[str, Any]], snapshot: str, meta: dict[str, Any], *, out_root: Path = JOBS_ROOT,
              keep: set[str] | None = None) -> Path | None:
    tax = meta["taxonomy"]
    counts = Counter(row["status"] for row in rows)
    if keep is not None:
        rows = [row for row in rows if row["status"] in keep]
        if not rows:
            return None
    job_id = "babis-reassessment-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    folder = out_root / job_id
    folder.mkdir(parents=True)
    write_json(folder / "input.json", {"snapshot_id": snapshot, "taxonomy_version": tax["version"],
                                       "taxonomy_sha256": digest(tax), "symbols": len(rows),
                                       "policy_version": meta["policy_version"],
                                       "profiles_available": meta["profiles_available"],
                                       "sec_records_available": meta["sec_records_available"],
                                       "network_calls": 0, "model_calls": 0})
    write_json(folder / "proposals.json", rows)
    write_json(folder / "state.json", {"status": "finished_with_review_required", "job_id": job_id,
                                       "symbols": len(rows), "status_counts": dict(counts), "finished_at": now()})
    return folder


def approve_automatically(batch_folder: Path) -> Path:
    """Approval file for the changes the automatic-agreement policy allows, and nothing else."""
    manifest = validate_batch(batch_folder)
    policy = read_json(POLICY_PATH)
    if not (policy.get("automatic_agreement") or {}).get("enabled"):
        raise ValueError("Automatic agreement approvals are disabled in the policy")
    changes = read_json(batch_folder / "changes.json")
    approved = [row["change_id"] for row in changes if row["disposition"] == "approval_candidate" and row["changes_value"]]
    approval = {"batch_id": manifest["batch_id"], "manifest_sha256": manifest["manifest_sha256"],
                "approved_by": "automatic_agreement_policy_" + policy["version"], "approved_at": now(),
                "approved_change_ids": approved,
                "rule": (policy.get("automatic_agreement") or {}).get("rule")}
    path = batch_folder / "automatic_approval.json"
    write_json(path, approval)
    return path


def summarize(job: Path, batch: dict[str, Any] | None = None) -> dict[str, Any]:
    rows = read_json(job / "proposals.json")
    corporate = [row for row in rows if row.get("old_instrument_type") in CORPORATE]
    return {"job": str(job), "companies": len(corporate),
            "status_counts": dict(Counter(row["status"] for row in corporate)),
            "review_reasons": dict(Counter(row["reason"] for row in corporate if row["status"] == "reassessment_review")),
            "batch": batch}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Re-decide company classifications from local multi-source evidence.")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--snapshot", default="latest")
    parser.add_argument("--apply", action="store_true", help="Apply the policy-approved changes (reversible batch).")
    parser.add_argument("--with-description-votes", action="store_true",
                        help="Add the cached local-LLM description votes (research_registry.description_vote).")
    args = parser.parse_args(argv)
    extra = None
    if args.with_description_votes:
        from .description_vote import cached_votes

        extra = cached_votes(load_profiles(PROJECT))
    db = connect(args.db)
    try:
        snapshot = latest_snapshot(db) if args.snapshot == "latest" else args.snapshot
        job = build_job(db, snapshot, extra_votes=extra)
        result = _batch(db, job, args.apply)
    finally:
        db.close()
    print(json.dumps(summarize(job, result), ensure_ascii=False, indent=2))
    return 0


def _batch(db: sqlite3.Connection, job: Path, apply: bool) -> dict[str, Any]:
    manifest = prepare_batch(job)
    batch_folder = PROJECT / "data" / "classification_batches" / manifest["batch_id"]
    result: dict[str, Any] = {"batch_id": manifest["batch_id"], "counts": manifest["counts"], "applied": 0}
    if apply:
        approval = approve_automatically(batch_folder)
        result["applied"] = apply_batch(db, batch_folder, approval)["applied"]
    return result


def daily_apply(db: sqlite3.Connection, snapshot: str, *, root: Path = PROJECT,
                report_dir: Path | None = None) -> dict[str, Any]:
    """Daily hook: apply only what independent sources agree on, and hand every
    disagreement to the review desk. Job files are written only when something changes."""
    advisory: dict[str, list[Vote]] = {}
    try:
        from .description_vote import cached_votes

        advisory = cached_votes(load_profiles(root))
    except Exception:  # noqa: BLE001 - advisory evidence is optional
        advisory = {}
    rows, meta = reassess_rows(db, snapshot, root=root, extra_votes=advisory)
    desk = update_review_desk(db, snapshot, rows)
    if report_dir is not None:
        write_review_report(db, report_dir)
    summary = {"status": "ok", "applied": 0, "batch_id": None, "review_desk": desk}
    job = write_job(rows, snapshot, meta, keep={"reassessment_agreement"})
    if job is not None:
        result = _batch(db, job, apply=True)
        summary.update(applied=result["applied"], batch_id=result["batch_id"])
    return summary


# ---------------------------------------------------------------- review desk
#
# Symbols whose sources disagree form their own category. The desk keeps one
# open item per listing with the latest evidence, notices when a later run
# resolves it (e.g. a provider profile arrives and two sources now agree), and
# never changes a classification itself: resolution is either automatic
# agreement through the governed batch or a person's approval.

REVIEW_DESK = "babis_review_desk"


def review_desk_schema(db: sqlite3.Connection) -> None:
    db.execute("""CREATE TABLE IF NOT EXISTS classification_review_item(
        venue_code TEXT NOT NULL, ticker TEXT NOT NULL, name TEXT, status TEXT NOT NULL, reason TEXT NOT NULL,
        current_node TEXT, votes_json TEXT NOT NULL, advisory_json TEXT NOT NULL,
        first_seen_snapshot TEXT NOT NULL, first_seen_at TEXT NOT NULL,
        last_seen_snapshot TEXT NOT NULL, last_seen_at TEXT NOT NULL, times_seen INTEGER NOT NULL,
        resolved_at TEXT, resolution TEXT, PRIMARY KEY(venue_code, ticker))""")


def update_review_desk(db: sqlite3.Connection, snapshot: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
    review_desk_schema(db)
    stamp = now()
    seen = {(row["venue_code"], row["ticker"]): row for row in rows}
    opened = refreshed = resolved = 0
    with db:
        for key, row in seen.items():
            if row["status"] != "reassessment_review":
                continue
            details = (row.get("source_ref") or {}).get("details") or {}
            values = (row.get("name"), row["reason"], row.get("old_node"),
                      json.dumps(details.get("votes") or [], sort_keys=True),
                      json.dumps(details.get("advisory_votes") or [], sort_keys=True))
            existing = db.execute("SELECT status FROM classification_review_item WHERE venue_code=? AND ticker=?", key).fetchone()
            if existing and existing[0] == "open":
                db.execute("UPDATE classification_review_item SET name=?, reason=?, current_node=?, votes_json=?, advisory_json=?, "
                           "last_seen_snapshot=?, last_seen_at=?, times_seen=times_seen+1 WHERE venue_code=? AND ticker=?",
                           (*values, snapshot, stamp, *key))
                refreshed += 1
            else:
                db.execute("INSERT OR REPLACE INTO classification_review_item VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                           (*key, values[0], "open", *values[1:], snapshot, stamp, snapshot, stamp, 1, None, None))
                opened += 1
        for venue, ticker in db.execute("SELECT venue_code, ticker FROM classification_review_item WHERE status='open'").fetchall():
            row = seen.get((venue, ticker))
            if row is not None and row["status"] == "reassessment_review":
                continue
            resolution = ("listing_missing" if row is None
                          else "sources_now_agree" if row["status"] in {"reassessment_agreement", "confirmed_same_node"}
                          else "no_longer_a_company_classification")
            db.execute("UPDATE classification_review_item SET status='resolved', resolved_at=?, resolution=? "
                       "WHERE venue_code=? AND ticker=?", (stamp, resolution, venue, ticker))
            resolved += 1
    open_count = db.execute("SELECT COUNT(*) FROM classification_review_item WHERE status='open'").fetchone()[0]
    return {"desk": REVIEW_DESK, "open": open_count, "opened": opened, "still_open": refreshed, "resolved": resolved}


def owner_decision(db: sqlite3.Connection, venue_code: str, ticker: str, *, node: str | None, note: str,
                   actor: str = "owner (control center)", jobs_root: Path = JOBS_ROOT,
                   batches_root: Path | None = None) -> dict[str, Any]:
    """Apply a person's decision on one review-desk item through the governed batch.

    ``node`` chooses a taxonomy node; ``None`` confirms the current one. Either way the
    result is an approved_provisional override that can be rolled back like any batch.
    """
    note = str(note or "").strip()
    if not note:
        raise ValueError("A short reason is required for an owner decision")
    review_desk_schema(db)
    snapshot = latest_snapshot(db)
    listing = next((row for row in _effective_listings(db, snapshot)
                    if row["venue_code"] == venue_code and row["ticker"] == ticker), None)
    if listing is None:
        raise ValueError("Listing is not in the latest snapshot")
    if listing["instrument_type"] not in CORPORATE:
        raise ValueError("Only company classifications are decided here")
    tax, nodes = taxonomy()
    confirm = node is None
    if confirm and not listing["node_id"]:
        raise ValueError("There is no current node to confirm; choose a node")
    target = listing["node_id"] if confirm else node
    if target not in nodes:
        raise ValueError("Node is not in the current taxonomy")
    item = db.execute("SELECT votes_json, advisory_json FROM classification_review_item WHERE venue_code=? AND ticker=?",
                      (venue_code, ticker)).fetchone()
    details = {"note": note, "decided_by": actor, "confirm_current": confirm,
               "votes": json.loads(item[0]) if item else [], "advisory_votes": json.loads(item[1]) if item else []}
    stamp = now()
    row = {"ticker": ticker, "venue": listing.get("venue"), "venue_code": venue_code, "name": listing.get("name"),
           "old_instrument_type": listing["instrument_type"], "old_node": listing["node_id"],
           "proposed_instrument_type": listing["instrument_type"], "proposed_node": target,
           "status": "human_decision", "reason": "owner_decision", "relation": relation(listing["node_id"], target),
           "source_status": "ok", "confirm_current": confirm, "verified": False, "applied_to_registry": False,
           "source_ref": {"provider": "owner", "url": "local-control-center://review", "observed_at": stamp,
                          "hash": digest(details), "details": details}}
    job_id = "owner-decision-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ") + "-" + ticker.replace(".", "_")
    job = jobs_root / job_id
    job.mkdir(parents=True)
    write_json(job / "input.json", {"snapshot_id": snapshot, "taxonomy_version": tax["version"],
                                    "taxonomy_sha256": digest(tax), "symbols": 1, "network_calls": 0, "model_calls": 0})
    write_json(job / "proposals.json", [row])
    write_json(job / "state.json", {"status": "finished_with_review_required", "job_id": job_id, "finished_at": stamp})
    manifest = prepare_batch(job, (batches_root / ("classification-" + job_id)) if batches_root else None)
    batch_folder = (batches_root / ("classification-" + job_id)) if batches_root else (
        PROJECT / "data" / "classification_batches" / manifest["batch_id"])
    change = read_json(batch_folder / "changes.json")[0]
    approval = {"batch_id": manifest["batch_id"], "manifest_sha256": manifest["manifest_sha256"],
                "approved_by": actor, "approved_at": stamp, "approved_change_ids": [change["change_id"]],
                "decisions": {change["change_id"]: {"outcome": "approve", "rationale": note}}}
    write_json(batch_folder / "owner_approval.json", approval)
    result = apply_batch(db, batch_folder, batch_folder / "owner_approval.json")
    with db:
        db.execute("UPDATE classification_review_item SET status='resolved', resolved_at=?, resolution=? "
                   "WHERE venue_code=? AND ticker=?", (stamp, "owner_confirmed_current" if confirm else "owner_chose_node",
                                                      venue_code, ticker))
    return {"batch_id": result["batch_id"], "ticker": ticker, "node": target, "confirmed_current": confirm}


def write_review_report(db: sqlite3.Connection, out_dir: Path) -> Path:
    """Readable list of open disagreements, with each source's vote, for a person to review."""
    review_desk_schema(db)
    items = [dict(zip(("venue_code", "ticker", "name", "reason", "current_node", "votes_json", "advisory_json",
                       "first_seen_at", "times_seen"), row)) for row in db.execute(
        "SELECT venue_code, ticker, name, reason, current_node, votes_json, advisory_json, first_seen_at, times_seen "
        "FROM classification_review_item WHERE status='open' ORDER BY reason, ticker").fetchall()]
    out_dir.mkdir(parents=True, exist_ok=True)
    write_json(out_dir / "classification_review_latest.json", {"desk": REVIEW_DESK, "generated_at": now(),
                                                               "open": len(items), "items": items})
    lines = [f"# Classification review desk ({REVIEW_DESK})", "",
             f"Open disagreements: {len(items)}. Generated {now()}.",
             "Nothing here changes a classification by itself; sources that later agree close an item automatically.", ""]
    for reason, count in Counter(item["reason"] for item in items).most_common():
        lines.append(f"- {reason}: {count}")
    lines += ["", "| Ticker | Name | Current | Votes | Local model (advisory) | Reason |", "|---|---|---|---|---|---|"]
    for item in items:
        votes = "; ".join(f"{v['source']}={v['node']}" for v in json.loads(item["votes_json"])) or "-"
        advisory = "; ".join(v["node"] for v in json.loads(item["advisory_json"])) or "-"
        name = str(item["name"] or "").replace("|", "/")[:40]
        lines.append(f"| {item['ticker']} | {name} | {item['current_node'] or '-'} | {votes} | {advisory} | {item['reason']} |")
    path = out_dir / "classification_review_latest.md"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


if __name__ == "__main__":
    raise SystemExit(main())
