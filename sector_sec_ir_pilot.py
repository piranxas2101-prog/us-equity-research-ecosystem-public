"""Bounded SEC/IR evidence pilot for one sector subcategory.

The production pilot targets technology.software, caches SEC Submissions and
Companyfacts by CIK, performs no retries, stops its SEC circuit on 429, and
updates only source-backed Sector Context Packet fields.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import gzip
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import time
from typing import Any
import urllib.error
import urllib.request

from research_registry.core import DEFAULT_DB, PROJECT, connect, digest
from sec_xbrl_pilot import extract_companyfacts, load_sec_user_agent, select_fact
from sector_ecosystem_integration import schema as ecosystem_schema
from sector_research_agent import analyze_input


ROOT = PROJECT / "data" / "sector_sec_ir_pilot"
CACHE_DB = ROOT / "sec_ir_cache.sqlite3"
TARGET_NODE = "technology.software"
ELIGIBLE_STATUS = "sector_context_local_evidence_review_required"
SEC_INTERVAL_SECONDS = 0.25
MAX_REQUESTS = 500
TERMINAL = {"finished_with_review_required", "finished", "failed", "stopped_with_pending", "stopped_rate_limited"}

SOFTWARE_CONCEPTS = {
    "research_and_development": ("ResearchAndDevelopmentExpense",),
    "stock_based_compensation_context": (
        "ShareBasedCompensation", "EmployeeServiceShareBasedCompensationNoncash",
    ),
    "remaining_performance_obligations": ("RevenueRemainingPerformanceObligation",),
}


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def save(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def schema(db: sqlite3.Connection) -> None:
    ecosystem_schema(db)
    db.executescript("""
      CREATE TABLE IF NOT EXISTS sector_source_collection_run(
        run_id TEXT PRIMARY KEY,activation_id TEXT NOT NULL,target_node TEXT NOT NULL,
        status TEXT NOT NULL,total_count INTEGER NOT NULL,processed_count INTEGER NOT NULL,
        packet_updated_count INTEGER NOT NULL,source_available_count INTEGER NOT NULL,
        source_unavailable_count INTEGER NOT NULL,failed_count INTEGER NOT NULL,
        source_requests INTEGER NOT NULL,llm_calls INTEGER NOT NULL,created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,finished_at TEXT,summary_json TEXT NOT NULL);
      CREATE TABLE IF NOT EXISTS sector_source_evidence(
        evidence_id TEXT PRIMARY KEY,run_id TEXT NOT NULL,activation_id TEXT NOT NULL,
        queue_id TEXT NOT NULL,ticker TEXT NOT NULL,cik TEXT,source_status TEXT NOT NULL,
        evidence_json TEXT NOT NULL,created_at TEXT NOT NULL,UNIQUE(run_id,queue_id));
    """)


def _sec_catalog(path: Path) -> dict[tuple[str, str], dict[str, Any]]:
    source = sqlite3.connect(f"file:{path.resolve().as_posix()}?mode=ro", uri=True)
    try:
        output = {}
        for ticker, venue, payload in source.execute("SELECT ticker,venue,payload FROM results"):
            row = json.loads(payload)
            output[(ticker, row.get("venue_code") or venue)] = row
        return output
    finally:
        source.close()


def prepare(activation_id: str | None = None, db_path: Path = DEFAULT_DB,
            root: Path = ROOT, target_node: str = TARGET_NODE) -> Path:
    db = connect(db_path)
    try:
        schema(db)
        if not activation_id:
            row = db.execute("SELECT activation_id FROM sector_activation_run ORDER BY created_at DESC LIMIT 1").fetchone()
            if not row: raise ValueError("No sector activation exists")
            activation_id = row[0]
        activation = db.execute(
            "SELECT babis_pipeline_job,snapshot_id FROM sector_activation_run WHERE activation_id=?",
            (activation_id,),
        ).fetchone()
        if not activation: raise ValueError(f"Unknown activation: {activation_id}")
        pipeline = json.loads((Path(activation[0]) / "state.json").read_text(encoding="utf-8"))
        sec_results = Path(pipeline["sec_job"]) / "results.sqlite3"
        catalog = _sec_catalog(sec_results)
        records = []
        for row in db.execute(
            "SELECT queue_id,ticker,venue_code,snapshot_id,node_id FROM sector_analysis_queue "
            "WHERE activation_id=? AND status=? AND (node_id=? OR node_id LIKE ?) ORDER BY ticker,venue_code",
            (activation_id, ELIGIBLE_STATUS, target_node, target_node + ".%"),
        ):
            result = catalog.get((row["ticker"], row["venue_code"])) or {}
            details = ((result.get("source_ref") or {}).get("details") or {})
            cik = str(details.get("cik") or "").lstrip("0") or None
            records.append({**dict(row), "cik": cik})
        if not records: raise ValueError(f"No eligible packets under {target_node}")
        run_id = "sec-ir-pilot-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        folder = Path(root) / run_id; folder.mkdir(parents=True, exist_ok=False)
        payload = {
            "run_id": run_id, "activation_id": activation_id, "snapshot_id": activation[1],
            "target_node": target_node, "total_symbols": len(records), "records": records,
            "created_at": now(), "policy": {
                "primary_source": "sec_edgar", "endpoints": ["submissions", "companyfacts"],
                "cache_by_cik": True, "minimum_interval_seconds": SEC_INTERVAL_SECONDS,
                "maximum_new_requests": MAX_REQUESTS, "retries": 0, "stop_on_429": True,
                "llm_calls": 0, "technical_analysis": False, "financial_verdicts": False,
            },
        }
        save(folder / "input.json", payload); save(folder / "state.json", {"status": "prepared", **payload})
        return folder
    finally:
        db.close()


class SecCache:
    def __init__(self, path: Path = CACHE_DB, interval: float = SEC_INTERVAL_SECONDS,
                 max_requests: int = MAX_REQUESTS):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.execute("""CREATE TABLE IF NOT EXISTS response_cache(
          endpoint TEXT NOT NULL,cik TEXT NOT NULL,status TEXT NOT NULL,observed_at TEXT NOT NULL,
          payload_gzip BLOB,payload_sha256 TEXT,PRIMARY KEY(endpoint,cik))""")
        self.db.execute("""CREATE TABLE IF NOT EXISTS request_attempt(
          endpoint TEXT NOT NULL,cik TEXT NOT NULL,attempted_at TEXT NOT NULL,status TEXT NOT NULL,
          PRIMARY KEY(endpoint,cik))""")
        self.db.commit()
        self.user_agent = load_sec_user_agent(str(PROJECT / ".env"))
        if not self.user_agent: raise ValueError("SEC_USER_AGENT is not configured")
        self.interval = interval; self.max_requests = max_requests; self.requests = 0
        self.last_call = 0.0; self.rate_limited = False

    def close(self) -> None:
        self.db.close()

    def get(self, endpoint: str, cik: str) -> dict[str, Any]:
        cached = self.db.execute(
            "SELECT status,observed_at,payload_gzip,payload_sha256 FROM response_cache WHERE endpoint=? AND cik=?",
            (endpoint, cik),
        ).fetchone()
        if cached:
            payload = json.loads(gzip.decompress(cached[2]).decode("utf-8")) if cached[2] else None
            return {"status": cached[0], "observed_at": cached[1], "payload": payload,
                    "payload_sha256": cached[3], "cache_hit": True}
        if self.rate_limited: return {"status": "circuit_open_rate_limited", "cache_hit": False}
        if self.requests >= self.max_requests: return {"status": "request_budget_exhausted", "cache_hit": False}
        time.sleep(max(0.0, self.interval - (time.monotonic() - self.last_call)))
        url = (
            f"https://data.sec.gov/submissions/CIK{int(cik):010d}.json" if endpoint == "submissions"
            else f"https://data.sec.gov/api/xbrl/companyfacts/CIK{int(cik):010d}.json"
        )
        request = urllib.request.Request(url, headers={"User-Agent": self.user_agent, "Accept": "application/json"})
        observed = now(); payload = None; status = "unexpected_error"
        try:
            with urllib.request.urlopen(request, timeout=25) as response:
                raw = response.read(); payload = json.loads(raw.decode("utf-8")); status = "ok"
        except urllib.error.HTTPError as exc:
            status = {401: "auth_failed", 403: "forbidden", 404: "not_found", 429: "rate_limited"}.get(exc.code, "http_error")
            if exc.code == 429: self.rate_limited = True
        except (OSError, urllib.error.URLError, TimeoutError):
            status = "timeout_or_network"
        except (json.JSONDecodeError, UnicodeDecodeError):
            status = "invalid_json"
        self.last_call = time.monotonic(); self.requests += 1
        encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8") if payload is not None else b""
        payload_hash = hashlib.sha256(encoded).hexdigest() if encoded else None
        compressed = gzip.compress(encoded, compresslevel=6) if encoded else None
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO request_attempt VALUES(?,?,?,?)", (endpoint, cik, observed, status))
            self.db.execute("INSERT OR REPLACE INTO response_cache VALUES(?,?,?,?,?,?)", (
                endpoint, cik, status, observed, compressed, payload_hash,
            ))
        return {"status": status, "observed_at": observed, "payload": payload,
                "payload_sha256": payload_hash, "cache_hit": False}


def _latest_filings(payload: dict[str, Any] | None, limit: int = 8) -> list[dict[str, Any]]:
    recent = ((((payload or {}).get("filings") or {}).get("recent")) or {})
    forms = recent.get("form") or []; accepted = {"10-K", "10-Q", "20-F", "40-F", "6-K"}
    output = []
    cik = str((payload or {}).get("cik") or "").lstrip("0")
    for index, form in enumerate(forms):
        if form not in accepted: continue
        accession = (recent.get("accessionNumber") or [])[index]
        document = (recent.get("primaryDocument") or [])[index]
        output.append({
            "form": form, "filing_date": (recent.get("filingDate") or [])[index],
            "report_date": (recent.get("reportDate") or [])[index], "accession": accession,
            "url": f"https://www.sec.gov/Archives/edgar/data/{cik}/{str(accession).replace('-', '')}/{document}",
        })
        if len(output) >= limit: break
    return output


def _fact_summary(companyfacts: dict[str, Any] | None, as_of: str) -> dict[str, Any]:
    if not companyfacts: return {"fields": {}, "coverage": {"resolved": 0, "requested": 0, "pct": 0.0}}
    base = extract_companyfacts(companyfacts, as_of)
    fields = dict(base.get("fields") or {})
    for field, concepts in SOFTWARE_CONCEPTS.items():
        selected = select_fact(companyfacts, concepts, as_of)
        if selected: fields[field] = selected
    return {"fields": fields, "coverage": {
        "resolved": len(fields), "requested": len(base.get("fields") or {}) + len(SOFTWARE_CONCEPTS),
        "pct": round(len(fields) / (len(base.get("fields") or {}) + len(SOFTWARE_CONCEPTS)) * 100, 2)
        if (len(base.get("fields") or {}) + len(SOFTWARE_CONCEPTS)) else 0.0,
    }}


def _merge_packet(packet: dict[str, Any], facts: dict[str, Any], filings: list[dict[str, Any]],
                  cik: str, observed_at: str, run_id: str) -> dict[str, Any]:
    existing_sources = list(packet.get("source_references") or [])
    companyfacts_id = "src_sec_companyfacts_" + digest([cik, facts])[:20]
    submissions_id = "src_sec_submissions_" + digest([cik, filings])[:20]
    sources = existing_sources + [{
        "source_id": companyfacts_id, "provider": "sec_companyfacts", "as_of": observed_at,
        "url": f"https://data.sec.gov/api/xbrl/companyfacts/CIK{int(cik):010d}.json",
        "evidence_label": "fact_source_reported",
    }, {
        "source_id": submissions_id, "provider": "sec_submissions", "as_of": observed_at,
        "url": f"https://data.sec.gov/submissions/CIK{int(cik):010d}.json",
        "evidence_label": "fact_source_reported",
    }]
    existing_ids = [row["source_id"] for row in existing_sources if row.get("source_id")]
    claims: dict[str, list[str]] = {}
    for field in ("business_model", "peer_group"):
        if packet.get(field): claims[field] = existing_ids
    revenue = []
    if facts.get("revenue"):
        revenue = [{"metric": "reported_revenue", **facts["revenue"]}]
        claims["revenue_drivers"] = [companyfacts_id]
    costs = []
    if facts.get("research_and_development"):
        costs = [{"metric": "research_and_development", **facts["research_and_development"]}]
        claims["cost_drivers"] = [companyfacts_id]
    sector_kpis = {}
    for field in ("remaining_performance_obligations", "stock_based_compensation_context"):
        if facts.get(field): sector_kpis[field] = facts[field]
    if sector_kpis: claims["sector_kpis"] = [companyfacts_id]
    evidence = {
        "business_model": packet.get("business_model"), "value_chain_position": packet.get("value_chain_position"),
        "revenue_drivers": revenue, "cost_drivers": costs, "peer_group": packet.get("peer_group") or [],
        "competitive_position": packet.get("competitive_position"), "sector_kpis": sector_kpis,
        "industry_risks": packet.get("industry_risks") or [],
        "company_specific_risks": packet.get("company_specific_risks") or [],
        "catalysts": packet.get("catalysts") or [], "source_references": sources,
        "claim_sources": claims, "evidence_gap_ids": packet.get("evidence_gap_ids") or [],
    }
    return analyze_input({"identity": {
        "run_id": run_id, "snapshot_id": packet["snapshot_id"], "symbol": packet["symbol"],
        "venue_code": packet["venue_code"], "classification_id": packet["classification_id"],
        "leaf_node": packet["leaf_node"],
    }, "evidence": evidence})


def _write_report(folder: Path, state: dict[str, Any], outcomes: Counter[str], coverage: Counter[str]) -> None:
    lines = [
        "# SEC/IR pilot — technology.software", "", f"Run: `{state['run_id']}`",
        f"Status: `{state['status']}`", "", "## Summary", "",
        f"- Symbols: **{state['processed_symbols']} / {state['total_symbols']}**",
        f"- Packets updated: **{state['packets_updated']}**",
        f"- SEC source available: **{state['source_available']}**",
        f"- Source unavailable / missing CIK: **{state['source_unavailable']}**",
        f"- Failures: **{state['failed']}**", f"- New SEC requests: **{state['source_requests']}**",
        "- Retries: **0**", "- LLM calls: **0**", "- Technical analysis: **0**", "",
        "## Outcome", "", "| State | Symbols |", "|---|---:|",
    ]
    lines += [f"| `{key}` | {value} |" for key, value in sorted(outcomes.items())]
    lines += ["", "## Extracted SEC fields", "", "| Field | Symbols |", "|---|---:|"]
    lines += [f"| `{key}` | {value} |" for key, value in coverage.most_common()]
    lines += [
        "", "## Interpretation", "",
        "The pilot adds issuer-reported numeric context and canonical filing links. It does not infer ARR, retention, customer acquisition, competitive position, risks or catalysts when those disclosures are not available in standard SEC XBRL facts.",
        "Packets remain review-required until the software evidence contract is satisfied. No incomplete packet was handed to Giannis.",
    ]
    (folder / "REPORT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def run(folder: Path, db_path: Path = DEFAULT_DB) -> int:
    import msvcrt
    folder = Path(folder); lock = (folder.parent / "sec_ir_pilot.lock").open("a+b"); lock.seek(0)
    try: msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
    except OSError: lock.close(); return 2
    inputs = json.loads((folder / "input.json").read_text(encoding="utf-8"))
    state = {**inputs, "records": None, "status": "running", "pid": os.getpid(), "started_at": now(),
             "processed_symbols": 0, "packets_updated": 0, "source_available": 0,
             "source_unavailable": 0, "failed": 0, "source_requests": 0, "llm_calls": 0,
             "financial_handoffs": 0}
    save(folder / "state.json", state)
    db = connect(db_path); source = None; outcomes: Counter[str] = Counter(); coverage: Counter[str] = Counter()
    try:
        schema(db); source = SecCache()
        with db:
            db.execute("INSERT OR REPLACE INTO sector_source_collection_run VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (
                inputs["run_id"], inputs["activation_id"], inputs["target_node"], state["status"],
                len(inputs["records"]), 0, 0, 0, 0, 0, 0, 0, inputs["created_at"], now(), None,
                json.dumps(state, ensure_ascii=False, sort_keys=True),
            ))
        for index, record in enumerate(inputs["records"], start=1):
            if (folder / "STOP").exists(): state["status"] = "stopped_with_pending"; break
            if source.rate_limited: state["status"] = "stopped_rate_limited"; break
            try:
                cik = record.get("cik")
                if not cik:
                    state["source_unavailable"] += 1; outcomes["missing_cik"] += 1
                    evidence = {"cik": None, "status": "missing_cik", "filings": [], "facts": {}}
                else:
                    submissions = source.get("submissions", cik); companyfacts = source.get("companyfacts", cik)
                    source_status = "ok" if submissions["status"] == companyfacts["status"] == "ok" else (
                        "partial" if "ok" in {submissions["status"], companyfacts["status"]} else
                        submissions["status"] + ":" + companyfacts["status"]
                    )
                    filings = _latest_filings(submissions.get("payload"))
                    fact_summary = _fact_summary(companyfacts.get("payload"), inputs["created_at"])
                    evidence = {"cik": cik, "status": source_status, "filings": filings,
                                "facts": fact_summary, "submissions_observed_at": submissions.get("observed_at"),
                                "companyfacts_observed_at": companyfacts.get("observed_at"),
                                "submissions_sha256": submissions.get("payload_sha256"),
                                "companyfacts_sha256": companyfacts.get("payload_sha256")}
                    if source_status in {"ok", "partial"}:
                        packet_row = db.execute(
                            "SELECT packet_json FROM sector_context_packet_store WHERE queue_id=?", (record["queue_id"],)
                        ).fetchone()
                        if packet_row:
                            packet = json.loads(packet_row[0]); merged = _merge_packet(
                                packet, fact_summary.get("fields") or {}, filings, cik,
                                companyfacts.get("observed_at") or submissions.get("observed_at") or now(), inputs["run_id"],
                            )
                            with db:
                                db.execute("INSERT OR REPLACE INTO sector_context_packet_store VALUES(?,?,?,?,?,?,?,?,?)", (
                                    merged["packet_id"], inputs["activation_id"], record["queue_id"], merged["agent_id"],
                                    inputs["run_id"], record["ticker"], json.dumps(merged, ensure_ascii=False, sort_keys=True),
                                    merged["status"], merged["created_at"],
                                ))
                                db.execute("UPDATE sector_analysis_queue SET status='sector_context_sec_ir_review_required' WHERE queue_id=?", (record["queue_id"],))
                            state["packets_updated"] += 1
                    state["source_available"] += int(source_status in {"ok", "partial"})
                    state["source_unavailable"] += int(source_status not in {"ok", "partial"})
                    outcomes[source_status] += 1; coverage.update((fact_summary.get("fields") or {}).keys())
                evidence_id = "sector_source_evidence_" + digest([inputs["run_id"], record["queue_id"], evidence])[:24]
                with db:
                    db.execute("INSERT OR REPLACE INTO sector_source_evidence VALUES(?,?,?,?,?,?,?,?,?)", (
                        evidence_id, inputs["run_id"], inputs["activation_id"], record["queue_id"], record["ticker"],
                        record.get("cik"), evidence["status"], json.dumps(evidence, ensure_ascii=False, sort_keys=True), now(),
                    ))
            except Exception as exc:
                state["failed"] += 1; outcomes["failed"] += 1
                with (folder / "errors.log").open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps({"ticker": record["ticker"], "error_type": type(exc).__name__, "error": str(exc)}, ensure_ascii=False) + "\n")
            state["processed_symbols"] = index; state["source_requests"] = source.requests
            if index % 20 == 0: state["updated_at"] = now(); save(folder / "state.json", state)
        else:
            state["status"] = "finished_with_review_required"
    except Exception as exc:
        state.update(status="failed", error_type=type(exc).__name__, error_message=str(exc)[:1000])
    finally:
        state["finished_at"] = now(); state["updated_at"] = state["finished_at"]
        if source: state["source_requests"] = source.requests; source.close()
        save(folder / "state.json", state); _write_report(folder, state, outcomes, coverage)
        with db:
            db.execute("UPDATE sector_source_collection_run SET status=?,processed_count=?,packet_updated_count=?,"
                       "source_available_count=?,source_unavailable_count=?,failed_count=?,source_requests=?,"
                       "updated_at=?,finished_at=?,summary_json=? WHERE run_id=?", (
                           state["status"], state["processed_symbols"], state["packets_updated"], state["source_available"],
                           state["source_unavailable"], state["failed"], state["source_requests"], state["updated_at"],
                           state["finished_at"], json.dumps(state, ensure_ascii=False, sort_keys=True), inputs["run_id"],
                       ))
        db.close(); lock.close()
    return 1 if state["status"] == "failed" else 0


def start(activation_id: str | None = None, db_path: Path = DEFAULT_DB,
          target_node: str = TARGET_NODE) -> dict[str, Any]:
    folder = prepare(activation_id, db_path, target_node=target_node)
    log = (folder / "worker.log").open("a", encoding="utf-8")
    try:
        process = subprocess.Popen(
            [sys.executable, "-B", str(Path(__file__).resolve()), "run", "--job", str(folder), "--db", str(db_path)],
            cwd=PROJECT, stdin=subprocess.DEVNULL, stdout=log, stderr=log,
            creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW,
        )
    finally: log.close()
    save(ROOT / "latest.json", {"run": str(folder), "run_id": folder.name, "pid": process.pid})
    notifier = PROJECT / "notify_sector_sec_ir_pilot.ps1"
    if notifier.exists():
        subprocess.Popen(["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(notifier),
                          "-StatePath", str(folder / "state.json")], cwd=PROJECT, stdin=subprocess.DEVNULL,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW)
    return {"status": "dispatched", "pid": process.pid, "run": str(folder), "run_id": folder.name}


def status(job: Path | None = None) -> dict[str, Any]:
    if job is None:
        latest = json.loads((ROOT / "latest.json").read_text(encoding="utf-8")); job = Path(latest["run"])
    return json.loads((Path(job) / "state.json").read_text(encoding="utf-8"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("start", "run", "status")); parser.add_argument("--activation-id")
    parser.add_argument("--target-node", default=TARGET_NODE); parser.add_argument("--job", type=Path)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB); args = parser.parse_args(argv)
    if args.action == "start": result = start(args.activation_id, args.db, args.target_node)
    elif args.action == "run":
        if not args.job: parser.error("--job is required for run")
        return run(args.job, args.db)
    else: result = status(args.job)
    print(json.dumps(result, ensure_ascii=False, indent=2)); return 0


if __name__ == "__main__": raise SystemExit(main())
