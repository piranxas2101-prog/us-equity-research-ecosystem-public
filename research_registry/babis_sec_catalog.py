"""SEC-backed completion pass for Babis classifications.

The worker performs at most one request per distinct SEC CIK, has no retries,
uses no LLM, and never writes to the production registry.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import sys
import time

from .babis_catalog_v2 import CORPORATE, now, normalize_cik, relation, save, save_text, sic_candidate
from . import source_http
from .core import DEFAULT_DB, PROJECT, digest, taxonomy

ROOT = PROJECT / "data/babis_catalog"


def canonical_ticker(value):
    return re.sub(r"[^A-Z0-9]", "", str(value or "").upper())


def load_effective_records(db, snapshot):
    records = []
    for payload_json, instrument_type, node_id, status in db.execute(
            "SELECT payload_json,instrument_type,node_id,status FROM effective_listed_symbol WHERE snapshot_id=?", (snapshot,)):
        row = json.loads(payload_json)
        row["instrument_type"] = instrument_type
        row["node_id"] = node_id
        row["status"] = status
        records.append(row)
    return records


def prepare(root=ROOT):
    db = sqlite3.connect(DEFAULT_DB.as_uri() + "?mode=ro", uri=True)
    try:
        snapshot = db.execute("SELECT snapshot_id FROM universe_snapshot ORDER BY retrieved_at DESC,rowid DESC LIMIT 1").fetchone()[0]
        records = load_effective_records(db, snapshot)
    finally:
        db.close()
    raw = (PROJECT / "cache/sec_company_tickers_exchange.json").read_bytes()
    payload = json.loads(raw)
    identities = {}; canonical_identities = {}
    for values in payload["data"]:
        row = dict(zip(payload["fields"], values))
        ticker = str(row.get("ticker") or "").upper(); cik = normalize_cik(row.get("cik"))
        identities.setdefault(ticker, set()).add(cik)
        canonical_identities.setdefault(canonical_ticker(ticker), set()).add(cik)
    for row in records:
        found = set()
        for alias in row.get("directory_aliases", []):
            found.update(value for value in identities.get(alias.upper(), set()) if value)
        if not found:
            for alias in row.get("directory_aliases", []):
                found.update(value for value in canonical_identities.get(canonical_ticker(alias), set()) if value)
        row["candidate_cik"] = next(iter(found)) if len(found) == 1 else None
    records.sort(key=lambda row: digest([row["candidate_cik"] or "", row["ticker"], row["venue_code"], "babis-sec-v6"]))
    seed = None
    for latest_name in ("latest_sec_v5.json", "latest_sec_v4.json", "latest_sec_v3.json"):
        latest = ROOT / latest_name
        if latest.exists():
            previous = Path(json.loads(latest.read_text(encoding="utf-8"))["job"]) / "sec_evidence.sqlite3"
            if previous.exists():
                seed = str(previous)
                break
    job_id = "babis-sec-v6-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    folder = root / job_id
    folder.mkdir(parents=True)
    tax = taxonomy()[0]
    inputs = {"snapshot_id": snapshot, "taxonomy_version": tax["version"], "taxonomy_sha256": digest(tax),
              "worker_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
              "identity_sha256": hashlib.sha256(raw).hexdigest(), "records": records, "symbols": len(records),
              "evidence_seed": seed,
              "policy": {"source": "sec_submissions", "one_request_per_distinct_cik": True,
                         "reuse_same_day_cached_evidence": True, "instrument_type_before_sector": True,
                         "retries": 0, "registry_writes": False, "llm_calls": False}}
    save(folder / "input.json", inputs)
    save(folder / "state.json", {"status": "prepared", "job_id": job_id, "symbols": len(records), "created_at": now()})
    return folder


class SecSource:
    def __init__(self, folder, max_requests=6000, interval=0.22, evidence_seed=None):
        self.helper = source_http
        try: _, self.env = self.helper.load_env(str(PROJECT / ".env"))
        except SystemExit: self.env = {}
        self.user_agent = self.env.get("SEC_USER_AGENT", "").strip()
        self.db = sqlite3.connect(folder / "sec_evidence.sqlite3")
        self.db.execute("CREATE TABLE IF NOT EXISTS evidence(cik TEXT PRIMARY KEY,payload TEXT NOT NULL)")
        self.db.execute("CREATE TABLE IF NOT EXISTS attempts(cik TEXT PRIMARY KEY,at TEXT NOT NULL)")
        self.db.execute("CREATE TABLE IF NOT EXISTS circuit(provider TEXT PRIMARY KEY,status TEXT NOT NULL)")
        if evidence_seed and Path(evidence_seed).resolve() != (folder / "sec_evidence.sqlite3").resolve():
            seed = sqlite3.connect("file:" + Path(evidence_seed).resolve().as_posix() + "?mode=ro", uri=True)
            try:
                self.db.executemany("INSERT OR IGNORE INTO evidence VALUES(?,?)", seed.execute("SELECT cik,payload FROM evidence"))
            finally:
                seed.close()
        self.db.commit(); self.max_requests = max_requests; self.interval = interval; self.last_call = 0.0

    def submissions(self, cik):
        cached = self.db.execute("SELECT payload FROM evidence WHERE cik=?", (cik,)).fetchone()
        if cached: return json.loads(cached[0])
        result = {"provider": "sec", "observed_at": now(), "data": {}}
        circuit = self.db.execute("SELECT status FROM circuit WHERE provider='sec'").fetchone()
        count = self.db.execute("SELECT COUNT(*) FROM attempts").fetchone()[0]
        if circuit: result["status"] = "provider_disabled:" + circuit[0]
        elif not self.user_agent: result["status"] = "missing_configuration"
        elif not cik: result["status"] = "missing_cik"
        elif count >= self.max_requests: result["status"] = "budget_exhausted"
        else:
            time.sleep(max(0.0, self.interval - (time.monotonic() - self.last_call)))
            self.db.execute("INSERT INTO attempts VALUES(?,?)", (cik, now())); self.db.commit()
            url = f"https://data.sec.gov/submissions/CIK{int(cik):010d}.json"
            response = self.helper.fetch_json("sec", url, headers={"User-Agent": self.user_agent, "Accept": "application/json"}, timeout=20)
            self.last_call = time.monotonic(); result["status"] = response.get("status")
            data = response.get("data") if isinstance(response.get("data"), dict) else {}
            if result["status"] == "ok":
                result["data"] = {key: data.get(key) for key in ("cik", "name", "sic", "sicDescription", "entityType", "tickers", "exchanges")}
            if result["status"] in {"auth_failed", "forbidden_or_plan", "rate_limited"}:
                self.db.execute("INSERT OR REPLACE INTO circuit VALUES('sec',?)", (result["status"],))
        result["evidence_sha256"] = digest(result)
        self.db.execute("INSERT INTO evidence VALUES(?,?)", (cik or "missing:" + digest(result), json.dumps(result, ensure_ascii=False))); self.db.commit()
        return result


def propose(row, evidence, nodes=None):
    nodes = nodes or taxonomy()[1]
    details = evidence.get("data") or {}; old = row.get("node_id")
    source_ref = {"provider": "sec", "url": "https://data.sec.gov/submissions/", "observed_at": evidence.get("observed_at"),
                  "hash": evidence.get("evidence_sha256"), "details": details}
    base = {"ticker": row["ticker"], "venue": row["venue"], "venue_code": row["venue_code"], "name": row["name"],
            "old_instrument_type": row["instrument_type"], "old_node": old, "source_status": evidence.get("status"),
            "verified": False, "applied_to_registry": False, "source_ref": source_ref}
    if evidence.get("status") != "ok":
        return {**base, "status": "unresolved", "reason": "sec_submission_unavailable", "proposed_node": None,
                "proposed_instrument_type": None, "relation": "no_candidate"}
    observed = normalize_cik(details.get("cik"))
    if observed != normalize_cik(row.get("candidate_cik")):
        return {**base, "status": "identity_review", "reason": "sec_cik_conflict", "proposed_node": None,
                "proposed_instrument_type": None, "relation": "no_candidate"}
    sic = str(details.get("sic") or "").strip()
    entity_type = str(details.get("entityType") or "").strip().lower()
    proposed_type = None
    if sic == "6770":
        proposed_type = "spac"
    elif "investment company" in entity_type or (sic in {"6722", "6726"} and any(
            word in row["name"].lower() for word in ("fund", "trust", "income", "opportunities"))):
        proposed_type = "fund_candidate"
    elif row["instrument_type"] == "unknown_instrument" and entity_type in {"operating", "other"}:
        proposed_type = "equity_candidate"
    if proposed_type and proposed_type != row["instrument_type"]:
        return {**base, "status": "asset_type_review", "reason": "sec_instrument_type_gate",
                "proposed_node": None, "proposed_instrument_type": proposed_type, "relation": "no_candidate"}
    candidate, basis = sic_candidate(details.get("sic"))
    if candidate and candidate not in nodes: raise ValueError("SEC SIC crosswalk outside taxonomy")
    rel = relation(old, candidate)
    if rel == "existing_more_specific": status, proposed = "confirmed_existing_more_specific", old
    elif rel == "same_node": status, proposed = "confirmed_same_node", old
    elif rel in {"cross_sector_conflict", "different_branch_same_sector"}: status, proposed = "classification_conflict_review", candidate
    elif candidate: status, proposed = "classification_proposal_review", candidate
    else: status, proposed = "unresolved", None
    return {**base, "status": status, "reason": basis, "proposed_node": proposed,
            "proposed_instrument_type": row["instrument_type"], "relation": rel}


def report(folder, inputs, db, state):
    rows = [json.loads(row[0]) for row in db.execute("SELECT payload FROM results ORDER BY ticker,venue")]
    statuses = Counter(row["status"] for row in rows); sources = Counter(row.get("source_status") or "not_applicable" for row in rows)
    state.update(processed_symbols=len(rows), total_symbols=inputs["symbols"], status_counts=dict(statuses), source_status_counts=dict(sources))
    save(folder / "state.json", state); save(folder / "proposals.json", rows)
    from .classification_problems import write_artifacts
    problems = write_artifacts(folder, rows, inputs)
    state["problem_items"] = len(problems["items"]); state["problem_groups"] = len(problems["groups"])
    save(folder / "state.json", state)
    lines = ["# Μπάμπης SEC v6: production problem-registry run", "", f"Κατάσταση: {state['status']}.",
             f"Σύμβολα: {len(rows)} / {inputs['symbols']}.", f"SEC requests: {state.get('source_requests', 0)}. Retries: 0. LLM calls: 0. Production writes: 0.", "",
             "## Καταστάσεις", "", "| Κατάσταση | Πλήθος |", "| --- | ---: |"]
    lines += [f"| {key} | {value} |" for key, value in sorted(statuses.items())]
    lines += ["", "## Πηγή", "", "| Source status | Πλήθος |", "| --- | ---: |"]
    lines += [f"| {key} | {value} |" for key, value in sorted(sources.items())]
    lines += ["", "Το SEC SIC είναι επίσημο disclosure metadata, όχι GICS και όχι από μόνο του επαρκής απόδειξη για κάθε υποκλάδο. Range mappings παραμένουν review.",
              "Καμία πρόταση δεν εφαρμόστηκε στο registry.", "", f"Snapshot: `{inputs['snapshot_id']}`. Taxonomy: `{inputs['taxonomy_version']}`."]
    save_text(folder / "report.md", "\n".join(lines))


def run(folder, max_requests=6000):
    import msvcrt
    lock = (folder.parent / "worker_sec_v3.lock").open("a+b"); lock.seek(0)
    try: msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
    except OSError: lock.close(); return 2
    inputs = json.loads((folder / "input.json").read_text(encoding="utf-8")); db = sqlite3.connect(folder / "results.sqlite3")
    db.execute("CREATE TABLE IF NOT EXISTS results(ticker TEXT,venue TEXT,payload TEXT,PRIMARY KEY(ticker,venue))")
    state = {"status": "running", "pid": os.getpid(), "job_id": folder.name, "started_at": now(),
             "max_requests": max_requests, "retries": 0, "llm_calls": 0, "registry_writes": 0}; source = None
    try:
        if hashlib.sha256(Path(__file__).read_bytes()).hexdigest() != inputs["worker_sha256"]: raise ValueError("Worker changed since preparation")
        if digest(taxonomy()[0]) != inputs["taxonomy_sha256"]: raise ValueError("Taxonomy changed since preparation")
        source = SecSource(folder, max_requests, evidence_seed=inputs.get("evidence_seed")); nodes = taxonomy()[1]; report(folder, inputs, db, state)
        for index, row in enumerate(inputs["records"]):
            if (folder / "STOP").exists(): state["status"] = "stopped_with_pending"; break
            if db.execute("SELECT 1 FROM results WHERE ticker=? AND venue=?", (row["ticker"], row["venue"])).fetchone(): continue
            if row["instrument_type"] not in CORPORATE:
                result = {"ticker": row["ticker"], "venue": row["venue"], "venue_code": row["venue_code"], "name": row["name"],
                          "old_instrument_type": row["instrument_type"], "old_node": row.get("node_id"), "proposed_node": None,
                          "proposed_instrument_type": None, "status": "retained_instrument_category", "reason": "separate_asset_classification",
                          "relation": "no_candidate", "verified": False, "applied_to_registry": False}
            elif not row.get("candidate_cik"):
                result = {"ticker": row["ticker"], "venue": row["venue"], "venue_code": row["venue_code"], "name": row["name"],
                          "old_instrument_type": row["instrument_type"], "old_node": row.get("node_id"), "proposed_node": None,
                          "proposed_instrument_type": None, "status": "unresolved", "reason": "missing_unambiguous_sec_cik",
                          "relation": "no_candidate", "source_status": "not_requested", "verified": False, "applied_to_registry": False}
            else: result = propose(row, source.submissions(row["candidate_cik"]), nodes)
            with db: db.execute("INSERT INTO results VALUES(?,?,?)", (row["ticker"], row["venue"], json.dumps(result, ensure_ascii=False)))
            if index % 100 == 0:
                state.update(updated_at=now(), processed_symbols=db.execute("SELECT COUNT(*) FROM results").fetchone()[0],
                             source_requests=source.db.execute("SELECT COUNT(*) FROM attempts").fetchone()[0]); save(folder / "state.json", state)
        else: state["status"] = "finished_with_review_required"
    except Exception as exc: state.update(status="failed", error_type=type(exc).__name__, error_message=str(exc)[:500])
    finally:
        state["finished_at"] = now()
        if source:
            state["source_requests"] = source.db.execute("SELECT COUNT(*) FROM attempts").fetchone()[0]
            state["provider_circuits"] = dict(source.db.execute("SELECT provider,status FROM circuit")); source.db.close()
        report(folder, inputs, db, state); db.close(); lock.close()
    return 1 if state["status"] == "failed" else 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__); parser.add_argument("action", choices=["start", "run", "status"])
    parser.add_argument("--job", type=Path); parser.add_argument("--max-requests", type=int, default=6000); args = parser.parse_args(argv)
    if not 0 <= args.max_requests <= 6000: parser.error("max-requests must be 0..6000")
    if args.action == "start":
        folder = args.job or prepare(); log = (folder / "worker.log").open("a", encoding="utf-8")
        try:
            process = subprocess.Popen([sys.executable, "-B", "-m", "research_registry.babis_sec_catalog", "run", "--job", str(folder),
                                        "--max-requests", str(args.max_requests)], cwd=PROJECT, stdin=subprocess.DEVNULL,
                                       stdout=log, stderr=log, creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW)
        finally: log.close()
        save(ROOT / "latest_sec_v6.json", {"job": str(folder), "pid": process.pid}); print(json.dumps({"status": "dispatched", "pid": process.pid, "job": str(folder)})); return 0
    folder = args.job or Path(json.loads((ROOT / "latest_sec_v6.json").read_text(encoding="utf-8"))["job"])
    if args.action == "status": print((folder / "state.json").read_text(encoding="utf-8")); return 0
    return run(folder, args.max_requests)


if __name__ == "__main__": raise SystemExit(main())
