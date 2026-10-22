"""Bounded multi-source evidence pass for open Babis classification problems.

Uses FMP company profiles and Polygon reference metadata only for open problem
items. It has no retries, no LLM calls, no trading endpoints and no automatic
classification writes.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import time
from urllib.parse import quote, urlencode

from .babis_catalog_v2 import SPECIAL_TYPES, normalize_cik, relation, save, save_text
from . import source_http
from .core import DEFAULT_DB, PROJECT, classify, digest, taxonomy


ROOT = PROJECT / "data" / "babis_enrichment"
RATE_POLICY = Path(__file__).with_name("provider_rate_policy.json")
SHARED_RATE_DB = ROOT / "provider_rate_state.sqlite3"


def now():
    return datetime.now(timezone.utc).isoformat()


def prepare(run_id="babis-sec-v6-20260916T120530578065Z", root=ROOT):
    db = sqlite3.connect(DEFAULT_DB)
    db.row_factory = sqlite3.Row
    try:
        run = db.execute("SELECT * FROM classification_problem_run WHERE run_id=?", (run_id,)).fetchone()
        if not run:
            raise ValueError("Unknown problem-registry run")
        source_job = Path(run["source_job"])
        proposal_rows = json.loads((source_job / "proposals.json").read_text(encoding="utf-8"))
        proposals = {(row.get("venue_code"), row["ticker"]): row for row in proposal_rows}
        records = []
        for item in db.execute(
            "SELECT * FROM classification_problem_item WHERE run_id=? AND status='open' ORDER BY ticker,venue_code",
            (run_id,),
        ):
            item = dict(item)
            source = proposals.get((item["venue_code"], item["ticker"]), {})
            details = ((source.get("source_ref") or {}).get("details") or {})
            current = db.execute(
                "SELECT instrument_type,node_id,status FROM effective_listed_symbol "
                "WHERE snapshot_id=? AND venue_code=? AND ticker=?",
                (item["snapshot_id"], item["venue_code"], item["ticker"]),
            ).fetchone()
            records.append({
                "problem_id": item["problem_id"], "group_id": item["group_id"],
                "category": item["category"], "ticker": item["ticker"], "name": item["name"],
                "venue_code": item["venue_code"], "snapshot_id": item["snapshot_id"],
                "old_node": item["old_node"], "proposed_node": item["proposed_node"],
                "old_instrument_type": item["old_instrument_type"],
                "proposed_instrument_type": item["proposed_instrument_type"],
                "current_node": current["node_id"] if current else item["old_node"],
                "current_instrument_type": current["instrument_type"] if current else item["old_instrument_type"],
                "sec": {key: details.get(key) for key in ("cik", "sic", "sicDescription", "entityType")},
            })
    finally:
        db.close()
    job_id = "babis-enrichment-v1-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    folder = root / job_id
    folder.mkdir(parents=True)
    tax = taxonomy()[0]
    inputs = {
        "job_id": job_id, "problem_run_id": run_id, "source_job": str(source_job),
        "snapshot_id": run["snapshot_id"], "taxonomy_version": tax["version"],
        "taxonomy_sha256": digest(tax), "worker_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "records": records, "symbols": len(records),
        "providers": ["fmp_company_profile", "polygon_ticker_reference"],
        "policy": {"open_problems_only": True, "max_one_request_per_provider_symbol": True,
                   "retries": 0, "llm_calls": 0, "trading_endpoints": False,
                   "automatic_classification_writes": False},
    }
    save(folder / "input.json", inputs)
    save(folder / "state.json", {"status": "prepared", "job_id": job_id, "symbols": len(records), "created_at": now()})
    return folder


class Sources:
    def __init__(self, folder, max_requests=4200, interval=0.25):
        self.helper = source_http
        try:
            _, self.env = self.helper.load_env(str(PROJECT / ".env"))
        except SystemExit:
            self.env = {}
        self.db = sqlite3.connect(folder / "provider_evidence.sqlite3")
        self.db.execute("CREATE TABLE IF NOT EXISTS evidence(key TEXT PRIMARY KEY,payload TEXT NOT NULL)")
        self.db.execute("CREATE TABLE IF NOT EXISTS attempts(key TEXT PRIMARY KEY,provider TEXT NOT NULL,at TEXT NOT NULL)")
        self.db.execute("CREATE TABLE IF NOT EXISTS circuit(provider TEXT PRIMARY KEY,status TEXT NOT NULL)")
        self.db.commit()
        self.rate_policy = json.loads(RATE_POLICY.read_text(encoding="utf-8"))["providers"]
        ROOT.mkdir(parents=True, exist_ok=True)
        self.rate_db = sqlite3.connect(SHARED_RATE_DB)
        self.rate_db.execute("""CREATE TABLE IF NOT EXISTS provider_rate_state(
          provider TEXT PRIMARY KEY,utc_day TEXT NOT NULL,day_requests INTEGER NOT NULL,
          minute_window_start REAL NOT NULL,minute_requests INTEGER NOT NULL,
          last_request_at REAL NOT NULL,blocked_until REAL NOT NULL,last_status TEXT NOT NULL)""")
        self.rate_db.commit()
        self.max_requests = max_requests
        self.interval = interval
        self.last_call = 0.0

    def _shared_permit(self, provider):
        policy = self.rate_policy[provider]
        current = time.time()
        utc_day = datetime.now(timezone.utc).date().isoformat()
        row = self.rate_db.execute("SELECT * FROM provider_rate_state WHERE provider=?", (provider,)).fetchone()
        if row:
            _, saved_day, day_requests, window_start, minute_requests, last_request, blocked_until, last_status = row
            if saved_day != utc_day:
                day_requests, minute_requests, window_start = 0, 0, current
            if blocked_until > current:
                return False, "shared_cooldown:" + last_status
            if day_requests >= policy["maximum_requests_per_utc_day"]:
                return False, "shared_daily_budget_exhausted"
            if current - window_start >= 60:
                minute_requests, window_start = 0, current
            if minute_requests >= policy["maximum_requests_per_minute"]:
                time.sleep(max(0.0, 60 - (current - window_start)))
                current, window_start, minute_requests = time.time(), time.time(), 0
            time.sleep(max(0.0, policy["minimum_interval_seconds"] - (current - last_request)))
            current = time.time()
        else:
            day_requests, minute_requests, window_start, blocked_until, last_status = 0, 0, current, 0.0, "new"
        self.rate_db.execute(
            "INSERT OR REPLACE INTO provider_rate_state VALUES(?,?,?,?,?,?,?,?)",
            (provider, utc_day, day_requests + 1, window_start, minute_requests + 1, current, blocked_until, last_status),
        )
        self.rate_db.commit()
        return True, "permitted"

    def _shared_status(self, provider, status):
        policy = self.rate_policy[provider]
        blocked_until = time.time() + policy["cooldown_seconds_after_429"] if status == "rate_limited" else 0.0
        self.rate_db.execute(
            "UPDATE provider_rate_state SET last_status=?,blocked_until=MAX(blocked_until,?) WHERE provider=?",
            (status or "unknown", blocked_until, provider),
        )
        self.rate_db.commit()

    def _get(self, provider, ticker):
        key = provider + ":" + ticker.upper()
        cached = self.db.execute("SELECT payload FROM evidence WHERE key=?", (key,)).fetchone()
        if cached:
            return json.loads(cached[0])
        result = {"provider": provider, "observed_at": now(), "data": {}}
        circuit = self.db.execute("SELECT status FROM circuit WHERE provider=?", (provider,)).fetchone()
        count = self.db.execute("SELECT COUNT(*) FROM attempts").fetchone()[0]
        env_name = "FMP_API_KEY" if provider == "fmp" else "POLYGON_API_KEY"
        if circuit:
            result["status"] = "provider_disabled:" + circuit[0]
        elif not self.env.get(env_name):
            result["status"] = "missing_configuration"
        elif count >= self.max_requests:
            result["status"] = "budget_exhausted"
        else:
            permitted, gate_status = self._shared_permit(provider)
            if not permitted:
                result["status"] = gate_status
            else:
                time.sleep(max(0.0, self.interval - (time.monotonic() - self.last_call)))
                self.db.execute("INSERT INTO attempts VALUES(?,?,?)", (key, provider, now()))
                self.db.commit()
                if provider == "fmp":
                    source_url = "https://financialmodelingprep.com/stable/profile?symbol=" + quote(ticker)
                    response = self.helper.fetch_json("fmp", source_url, headers={"apikey": self.env[env_name]}, timeout=20)
                else:
                    source_url = "https://api.polygon.io/v3/reference/tickers/" + quote(ticker)
                    request_url = source_url + "?" + urlencode({"apiKey": self.env[env_name]})
                    response = self.helper.fetch_json("polygon_ticker_details", request_url, timeout=20)
                self.last_call = time.monotonic()
                result["status"] = response.get("status")
                self._shared_status(provider, result["status"])
                payload = response.get("data")
                if result["status"] == "ok":
                    if provider == "fmp":
                        matches = [row for row in payload if isinstance(row, dict) and str(row.get("symbol", "")).upper() == ticker.upper()] if isinstance(payload, list) else []
                        raw = matches[0] if len(matches) == 1 else {}
                        fields = ("symbol", "companyName", "cik", "sector", "industry", "description", "isEtf", "isFund", "isAdr", "website")
                    else:
                        raw = payload.get("results", {}) if isinstance(payload, dict) else {}
                        fields = ("ticker", "name", "market", "locale", "primary_exchange", "type", "active", "currency_name", "cik", "sic_code", "sic_description", "description", "homepage_url", "total_employees")
                    result["data"] = {field: raw.get(field) for field in fields if raw.get(field) is not None}
                    if not result["data"]:
                        result["status"] = "empty_or_ambiguous_response"
                if result["status"] in {"auth_failed", "forbidden_or_plan", "rate_limited", "timeout_or_network"}:
                    self.db.execute("INSERT OR REPLACE INTO circuit VALUES(?,?)", (provider, result["status"]))
        result["evidence_sha256"] = digest(result)
        self.db.execute("INSERT INTO evidence VALUES(?,?)", (key, json.dumps(result, ensure_ascii=False)))
        self.db.commit()
        return result

    def fmp(self, ticker):
        return self._get("fmp", ticker)

    def polygon(self, ticker):
        return self._get("polygon", ticker)


def _comparable(left, right):
    return bool(left and right) and (left == right or left.startswith(right + ".") or right.startswith(left + "."))


def analyze(record, fmp, polygon):
    f = fmp.get("data") or {}
    p = polygon.get("data") or {}
    expected_cik = normalize_cik((record.get("sec") or {}).get("cik"))
    observed_ciks = {normalize_cik(value) for value in (f.get("cik"), p.get("cik")) if normalize_cik(value)}
    identity_conflict = bool(expected_cik and observed_ciks and any(value != expected_cik for value in observed_ciks))
    fmp_candidate = None
    if fmp.get("status") == "ok":
        classified = classify({"sector": f.get("sector"), "industry": f.get("industry")})
        fmp_candidate = classified.get("node_id")
    type_signals = []
    if f.get("isEtf"):
        type_signals.append("etf")
    if f.get("isFund"):
        type_signals.append("fund_candidate")
    if f.get("isAdr"):
        type_signals.append("adr_candidate")
    polygon_type = str(p.get("type") or "").upper()
    if polygon_type in SPECIAL_TYPES:
        type_signals.append(SPECIAL_TYPES[polygon_type])
    elif polygon_type in {"CS", "OS"}:
        type_signals.append("equity_candidate")
    type_signals = sorted(set(type_signals))
    if identity_conflict:
        assessment = "identity_conflict"
    elif record["category"] == "instrument_type_review" and len(type_signals) == 1:
        assessment = "corroborated_instrument_type"
    elif record["category"] == "instrument_type_review" and len(type_signals) > 1:
        assessment = "instrument_type_conflict"
    elif fmp_candidate and _comparable(fmp_candidate, record.get("proposed_node")):
        assessment = "corroborated_taxonomy_candidate"
    elif fmp_candidate and _comparable(fmp_candidate, record.get("current_node")):
        assessment = "supports_current_taxonomy"
    elif fmp_candidate:
        assessment = "supplemental_source_conflict"
    else:
        assessment = "insufficient_supplemental_evidence"
    return {
        "problem_id": record["problem_id"], "group_id": record["group_id"], "ticker": record["ticker"],
        "category": record["category"], "assessment": assessment, "current_node": record.get("current_node"),
        "sec_proposed_node": record.get("proposed_node"), "fmp_candidate_node": fmp_candidate,
        "taxonomy_relation": relation(record.get("current_node"), fmp_candidate),
        "instrument_type_signals": type_signals, "expected_sec_cik": expected_cik,
        "observed_ciks": sorted(observed_ciks), "requires_human_approval": True,
        "source_refs": [
            {"provider": source.get("provider"), "status": source.get("status"), "observed_at": source.get("observed_at"), "hash": source.get("evidence_sha256"), "data": source.get("data")}
            for source in (fmp, polygon)
        ],
    }


def report(folder, inputs, db, state):
    rows = [json.loads(row[0]) for row in db.execute("SELECT payload FROM results ORDER BY ticker")]
    assessments = Counter(row["assessment"] for row in rows)
    state.update(processed_symbols=len(rows), total_symbols=inputs["symbols"], assessment_counts=dict(assessments))
    save(folder / "state.json", state)
    save(folder / "assessments.json", rows)
    lines = ["# Μπάμπης: bounded API evidence enrichment", "", f"Κατάσταση: {state['status']}.",
             f"Open problems: {len(rows)} / {inputs['symbols']}.",
             f"API requests: {state.get('source_requests',0)}. Retries: 0. LLM calls: 0. Production classification writes: 0.", "",
             "| Assessment | Πλήθος |", "| --- | ---: |"]
    lines += [f"| {key} | {value} |" for key, value in sorted(assessments.items())]
    lines += ["", "FMP και Polygon χρησιμοποιούνται ως συμπληρωματικά τεκμήρια. Η SEC παραμένει η κύρια πηγή εταιρικής ταυτότητας και disclosure. Καμία νέα πρόταση δεν εφαρμόζεται χωρίς approval batch."]
    save_text(folder / "report.md", "\n".join(lines))


def run(folder, max_requests=4200):
    import msvcrt
    lock = (folder.parent / "babis_enrichment.lock").open("a+b")
    lock.seek(0)
    try:
        msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
    except OSError:
        lock.close()
        return 2
    inputs = json.loads((folder / "input.json").read_text(encoding="utf-8"))
    db = sqlite3.connect(folder / "results.sqlite3")
    db.execute("CREATE TABLE IF NOT EXISTS results(problem_id TEXT PRIMARY KEY,ticker TEXT,payload TEXT)")
    source = None
    state = {"status": "running", "pid": os.getpid(), "job_id": folder.name, "started_at": now(),
             "max_requests": max_requests, "retries": 0, "llm_calls": 0, "registry_writes": 0}
    try:
        if hashlib.sha256(Path(__file__).read_bytes()).hexdigest() != inputs["worker_sha256"]:
            raise ValueError("Worker changed since preparation")
        if digest(taxonomy()[0]) != inputs["taxonomy_sha256"]:
            raise ValueError("Taxonomy changed since preparation")
        source = Sources(folder, max_requests=max_requests)
        report(folder, inputs, db, state)
        for index, record in enumerate(inputs["records"]):
            if (folder / "STOP").exists():
                state["status"] = "stopped_with_pending"
                break
            if db.execute("SELECT 1 FROM results WHERE problem_id=?", (record["problem_id"],)).fetchone():
                continue
            result = analyze(record, source.fmp(record["ticker"]), source.polygon(record["ticker"]))
            with db:
                db.execute("INSERT INTO results VALUES(?,?,?)", (record["problem_id"], record["ticker"], json.dumps(result, ensure_ascii=False)))
            if index % 50 == 0:
                state.update(updated_at=now(), processed_symbols=db.execute("SELECT COUNT(*) FROM results").fetchone()[0],
                             source_requests=source.db.execute("SELECT COUNT(*) FROM attempts").fetchone()[0])
                save(folder / "state.json", state)
        else:
            state["status"] = "finished_with_review_required"
    except Exception as exc:
        state.update(status="failed", error_type=type(exc).__name__, error_message=str(exc)[:500])
    finally:
        state["finished_at"] = now()
        if source:
            state["source_requests"] = source.db.execute("SELECT COUNT(*) FROM attempts").fetchone()[0]
            state["provider_circuits"] = dict(source.db.execute("SELECT provider,status FROM circuit"))
            source.db.close()
            source.rate_db.close()
        report(folder, inputs, db, state)
        db.close()
        lock.close()
    return 1 if state["status"] == "failed" else 0


def sync_rate_state(folder):
    folder = Path(folder)
    evidence_db = sqlite3.connect(folder / "provider_evidence.sqlite3")
    try:
        counts = dict(evidence_db.execute("SELECT provider,COUNT(*) FROM attempts GROUP BY provider"))
        circuits = dict(evidence_db.execute("SELECT provider,status FROM circuit"))
    finally:
        evidence_db.close()
    policy = json.loads(RATE_POLICY.read_text(encoding="utf-8"))["providers"]
    ROOT.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(SHARED_RATE_DB)
    db.execute("""CREATE TABLE IF NOT EXISTS provider_rate_state(
      provider TEXT PRIMARY KEY,utc_day TEXT NOT NULL,day_requests INTEGER NOT NULL,
      minute_window_start REAL NOT NULL,minute_requests INTEGER NOT NULL,
      last_request_at REAL NOT NULL,blocked_until REAL NOT NULL,last_status TEXT NOT NULL)""")
    current = time.time(); utc_day = datetime.now(timezone.utc).date().isoformat()
    with db:
        for provider in policy:
            status = circuits.get(provider, "ok")
            blocked = current + policy[provider]["cooldown_seconds_after_429"] if status == "rate_limited" else 0.0
            db.execute("INSERT OR REPLACE INTO provider_rate_state VALUES(?,?,?,?,?,?,?,?)",
                       (provider, utc_day, counts.get(provider, 0), current, 0, current, blocked, status))
    db.close()
    return {"status":"synced","providers":{provider:{"requests":counts.get(provider,0),"status":circuits.get(provider,"ok")} for provider in policy}}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("start", "run", "status", "sync-rate-state"))
    parser.add_argument("--job", type=Path)
    parser.add_argument("--run-id", default="babis-sec-v6-20260916T120530578065Z")
    parser.add_argument("--max-requests", type=int, default=4200)
    args = parser.parse_args(argv)
    if not 0 <= args.max_requests <= 4200:
        parser.error("max-requests must be 0..4200")
    if args.action == "start":
        folder = args.job or prepare(args.run_id)
        log = (folder / "worker.log").open("a", encoding="utf-8")
        try:
            process = subprocess.Popen(
                [sys.executable, "-B", "-m", "research_registry.babis_problem_enrichment", "run", "--job", str(folder), "--max-requests", str(args.max_requests)],
                cwd=PROJECT, stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW,
            )
        finally:
            log.close()
        save(ROOT / "latest.json", {"job": str(folder), "pid": process.pid})
        print(json.dumps({"status": "dispatched", "pid": process.pid, "job": str(folder)}, ensure_ascii=False))
        return 0
    folder = args.job or Path(json.loads((ROOT / "latest.json").read_text(encoding="utf-8"))["job"])
    if args.action == "sync-rate-state":
        print(json.dumps(sync_rate_state(folder), ensure_ascii=False, indent=2))
        return 0
    if args.action == "status":
        print((folder / "state.json").read_text(encoding="utf-8"))
        return 0
    return run(folder, args.max_requests)


if __name__ == "__main__":
    raise SystemExit(main())
