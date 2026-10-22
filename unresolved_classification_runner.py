"""Offline-first batch analysis for the unresolved-classification queue.

The worker reuses the completed Babis SEC/catalog evidence. It performs no
network or LLM calls, writes no official classification, and returns sourced
sector/subcategory candidates to Babis for review.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import csv
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
from typing import Any

from research_registry.core import DEFAULT_DB, PROJECT, connect, digest
from sector_agents.unresolved_classification import analyze


ROOT = PROJECT / "data" / "unresolved_classification"
AGENT_ID = "sector_unresolved_classification"
READY_STATUS = "unresolved_analysis_complete"
REVIEW_STATUS = "unresolved_analysis_review_required"
TERMINAL_STATES = {"finished_with_review_required", "failed", "stopped_with_pending"}


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def save(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def schema(db: sqlite3.Connection) -> None:
    db.executescript("""
      CREATE TABLE IF NOT EXISTS unresolved_classification_run(
        run_id TEXT PRIMARY KEY,activation_id TEXT NOT NULL,status TEXT NOT NULL,
        total_count INTEGER NOT NULL,processed_count INTEGER NOT NULL,
        ready_for_babis_review_count INTEGER NOT NULL,review_required_count INTEGER NOT NULL,
        failed_count INTEGER NOT NULL,source_reuse_count INTEGER NOT NULL,
        network_calls INTEGER NOT NULL,llm_calls INTEGER NOT NULL,
        created_at TEXT NOT NULL,updated_at TEXT NOT NULL,finished_at TEXT,
        summary_json TEXT NOT NULL);
      CREATE TABLE IF NOT EXISTS unresolved_classification_packet_store(
        packet_id TEXT PRIMARY KEY,run_id TEXT NOT NULL,activation_id TEXT NOT NULL,
        queue_id TEXT NOT NULL,ticker TEXT NOT NULL,packet_json TEXT NOT NULL,
        status TEXT NOT NULL,created_at TEXT NOT NULL,UNIQUE(run_id,queue_id));
      CREATE INDEX IF NOT EXISTS unresolved_packet_status
        ON unresolved_classification_packet_store(run_id,status,ticker);
    """)


def _latest_activation(db: sqlite3.Connection) -> str:
    row = db.execute("SELECT activation_id FROM sector_activation_run ORDER BY created_at DESC LIMIT 1").fetchone()
    if not row:
        raise ValueError("No sector activation exists")
    return row[0]


def prepare(activation_id: str | None = None, db_path: Path = DEFAULT_DB, root: Path = ROOT) -> Path:
    db = connect(db_path)
    try:
        schema(db)
        activation_id = activation_id or _latest_activation(db)
        activation = db.execute(
            "SELECT babis_pipeline_job,snapshot_id FROM sector_activation_run WHERE activation_id=?",
            (activation_id,),
        ).fetchone()
        if not activation:
            raise ValueError(f"Unknown activation: {activation_id}")
        total = db.execute(
            "SELECT COUNT(*) FROM sector_analysis_queue WHERE activation_id=? AND agent_id=? "
            "AND status IN ('ready_for_unresolved_evidence','unresolved_analysis_review_required')",
            (activation_id, AGENT_ID),
        ).fetchone()[0]
        if total == 0:
            raise ValueError("No unresolved-classification items are eligible for analysis")
        pipeline_state = json.loads((Path(activation[0]) / "state.json").read_text(encoding="utf-8"))
        catalog_job = Path(pipeline_state["sec_job"])
        results_db = catalog_job / "results.sqlite3"
        if not results_db.exists():
            raise ValueError("Completed Babis SEC results are missing")
        run_id = "unresolved-analysis-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        folder = Path(root) / run_id
        folder.mkdir(parents=True, exist_ok=False)
        payload = {
            "run_id": run_id,
            "activation_id": activation_id,
            "snapshot_id": activation[1],
            "babis_pipeline_job": str(activation[0]),
            "catalog_job": str(catalog_job),
            "catalog_results_db": str(results_db),
            "total_symbols": total,
            "created_at": now(),
            "policy": {
                "local_evidence_reuse_first": True,
                "network_calls": 0,
                "llm_calls": 0,
                "classification_writes": False,
                "review_authority": "babis",
                "unknowns_are_explicit": True,
            },
        }
        save(folder / "input.json", payload)
        save(folder / "state.json", {"status": "prepared", **payload})
        return folder
    finally:
        db.close()


def _catalog_results(path: Path) -> dict[tuple[str, str], dict[str, Any]]:
    source = sqlite3.connect(f"file:{path.resolve().as_posix()}?mode=ro", uri=True)
    try:
        output: dict[tuple[str, str], dict[str, Any]] = {}
        for ticker, _venue, payload in source.execute("SELECT ticker,venue,payload FROM results"):
            row = json.loads(payload)
            output[(ticker, row.get("venue_code") or "")] = row
        return output
    finally:
        source.close()


def _source_reference(source_id: str, provider: str, as_of: str, url: str, evidence_label: str) -> dict[str, Any]:
    return {
        "source_id": source_id,
        "provider": provider,
        "as_of": as_of,
        "url": url,
        "evidence_label": evidence_label,
    }


def _build_evidence(queue: sqlite3.Row, catalog: dict[str, Any] | None,
                    snapshot_as_of: str, gap_ids: list[str]) -> dict[str, Any]:
    listed = json.loads(queue["payload_json"])
    raw_profile = listed.get("raw_profile") or {}
    sources: list[dict[str, Any]] = []
    claim_sources: dict[str, list[str]] = defaultdict(list)
    geography: list[str] = []
    value_chain_clues: list[str] = []
    sector_candidates: list[dict[str, Any]] = []
    subcategory_candidates: list[dict[str, Any]] = []

    registry_source_id = "src_registry_" + digest([queue["snapshot_id"], queue["venue_code"], queue["ticker"]])[:20]
    profile_url = str(raw_profile.get("url") or "")
    if profile_url.startswith("/"):
        profile_url = "https://www.nasdaq.com" + profile_url
    sources.append(_source_reference(
        registry_source_id, "listed_symbol_registry", snapshot_as_of,
        profile_url or str(listed.get("source_directory") or "local_symbol_registry"),
        "fact_provider_standardized",
    ))
    if raw_profile.get("country"):
        geography = [str(raw_profile["country"])]
        claim_sources["geography"].append(registry_source_id)
    if raw_profile.get("industry"):
        value_chain_clues.append("Provider industry: " + str(raw_profile["industry"]))
        claim_sources["value_chain_clues"].append(registry_source_id)

    current_node = str(queue["node_id"] or "")
    if current_node:
        sector_candidates.append({
            "sector": current_node.split(".")[0],
            "source_ids": [registry_source_id],
            "confidence": "low",
            "rationale": "Existing provisional top-level classification retained as context only.",
        })

    if catalog:
        source_ref = catalog.get("source_ref") or {}
        details = source_ref.get("details") or {}
        if catalog.get("source_status") == "ok" and source_ref.get("hash"):
            sec_source_id = "src_sec_" + str(source_ref["hash"])[:20]
            sources.append(_source_reference(
                sec_source_id, "sec_submissions", str(source_ref.get("observed_at") or snapshot_as_of),
                str(source_ref.get("url") or "https://data.sec.gov/submissions/"),
                "fact_source_reported",
            ))
            if details.get("sicDescription"):
                value_chain_clues.append(f"SEC SIC {details.get('sic')}: {details['sicDescription']}")
                claim_sources["value_chain_clues"].append(sec_source_id)
            proposed = str(catalog.get("proposed_node") or "")
            parts = proposed.split(".") if proposed else []
            if parts:
                sector_candidates = [{
                    "sector": parts[0],
                    "source_ids": [sec_source_id],
                    "confidence": "medium" if catalog.get("reason") == "explicit_sic_crosswalk" else "low",
                    "rationale": f"Babis SEC proposal: {catalog.get('reason')}; relation={catalog.get('relation')}",
                }]
            if len(parts) >= 2:
                subcategory_candidates = [{
                    "subcategory": parts[1],
                    "source_ids": [sec_source_id],
                    "confidence": "medium" if catalog.get("reason") == "explicit_sic_crosswalk" else "low",
                    "rationale": f"Candidate node {proposed}; requires Babis review.",
                }]

    return {
        "business_model": None,
        "products_services": [],
        "revenue_model": None,
        "geography": geography,
        "value_chain_clues": list(dict.fromkeys(value_chain_clues)),
        "peer_clues": [],
        "sector_candidates": sector_candidates,
        "subcategory_candidates": subcategory_candidates,
        "source_references": sources,
        "claim_sources": dict(claim_sources),
        "unknown_fields": [],
        "evidence_gap_ids": gap_ids,
    }


def _write_report(folder: Path, state: dict[str, Any], packets: list[dict[str, Any]],
                  catalog_statuses: Counter[str], candidate_sectors: Counter[str]) -> None:
    rows = []
    for packet in packets:
        rows.append({
            "ticker": packet["symbol"],
            "status": packet["status"],
            "confidence": packet["confidence"],
            "sector_candidates": ";".join(x["sector"] for x in packet["sector_candidates"]),
            "subcategory_candidates": ";".join(x["subcategory"] for x in packet["subcategory_candidates"]),
            "unknown_fields": ";".join(packet["unknown_fields"]),
            "evidence_gap_ids": ";".join(packet["evidence_gap_ids"]),
        })
    with (folder / "symbol_results.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader(); writer.writerows(sorted(rows, key=lambda row: row["ticker"]))
    lines = [
        "# Unresolved-classification analysis report", "",
        f"Run: `{state['run_id']}`", f"Status: `{state['status']}`", "",
        "## Summary", "",
        f"- Queue symbols: **{state['total_symbols']}**",
        f"- Processed: **{state['processed_symbols']}**",
        f"- Ready for Babis classification review: **{state['ready_for_babis_review']}**",
        f"- Review required / insufficient subcategory evidence: **{state['review_required']}**",
        f"- Failed: **{state['failed']}**",
        f"- Reused local evidence: **{state['source_reuse_count']} symbols**",
        "- Network calls: **0**", "- LLM calls: **0**", "- Automatic classification writes: **0**", "",
        "## Babis catalog posture", "", "| Catalog state | Symbols |", "|---|---:|",
    ]
    lines += [f"| `{key}` | {value} |" for key, value in sorted(catalog_statuses.items())]
    lines += ["", "## Candidate sectors returned to Babis", "", "| Sector | Symbols |", "|---|---:|"]
    lines += [f"| `{key}` | {value} |" for key, value in sorted(candidate_sectors.items())]
    lines += [
        "", "## Governance", "",
        "Every packet preserves source ids, observed dates, confidence, unknown fields and evidence-gap ids.",
        "The special agent produced proposals only. Babis remains the sole authority for official taxonomy writes.",
        "Symbols without a supported subcategory remain review-required rather than being guessed.",
    ]
    (folder / "REPORT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def run(folder: Path, db_path: Path = DEFAULT_DB) -> int:
    import msvcrt
    folder = Path(folder)
    lock_path = folder.parent / "unresolved_worker.lock"
    lock = lock_path.open("a+b"); lock.seek(0)
    try:
        msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
    except OSError:
        lock.close(); return 2
    inputs = json.loads((folder / "input.json").read_text(encoding="utf-8"))
    state = {
        **inputs, "status": "running", "pid": os.getpid(), "started_at": now(),
        "processed_symbols": 0, "ready_for_babis_review": 0,
        "review_required": 0, "failed": 0, "source_reuse_count": 0,
        "network_calls": 0, "llm_calls": 0, "classification_writes": 0,
    }
    save(folder / "state.json", state)
    db = connect(db_path)
    packets: list[dict[str, Any]] = []
    catalog_statuses: Counter[str] = Counter()
    candidate_sectors: Counter[str] = Counter()
    try:
        schema(db)
        catalog_map = _catalog_results(Path(inputs["catalog_results_db"]))
        snapshot_row = db.execute(
            "SELECT retrieved_at FROM universe_snapshot WHERE snapshot_id=?", (inputs["snapshot_id"],)
        ).fetchone()
        snapshot_as_of = snapshot_row[0] if snapshot_row else inputs["created_at"]
        queue_rows = list(db.execute(
            "SELECT q.*,v.name,v.payload_json FROM sector_analysis_queue q "
            "JOIN effective_listed_symbol v ON v.snapshot_id=q.snapshot_id AND v.venue_code=q.venue_code AND v.ticker=q.ticker "
            "WHERE q.activation_id=? AND q.agent_id=? "
            "AND q.status IN ('ready_for_unresolved_evidence','unresolved_analysis_review_required') "
            "ORDER BY q.ticker,q.venue_code",
            (inputs["activation_id"], AGENT_ID),
        ))
        with db:
            db.execute("INSERT OR REPLACE INTO unresolved_classification_run VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (
                inputs["run_id"], inputs["activation_id"], state["status"], len(queue_rows), 0, 0, 0, 0, 0,
                0, 0, inputs["created_at"], now(), None, json.dumps(state, ensure_ascii=False, sort_keys=True),
            ))
        for index, queue in enumerate(queue_rows, start=1):
            if (folder / "STOP").exists():
                state["status"] = "stopped_with_pending"; break
            try:
                catalog = catalog_map.get((queue["ticker"], queue["venue_code"]))
                catalog_statuses[str((catalog or {}).get("status") or "missing")] += 1
                gap_ids = [row[0] for row in db.execute(
                    "SELECT gap_id FROM ecosystem_evidence_gap WHERE ticker=? "
                    "AND resolution_state IN ('open','cooldown','retry_eligible') ORDER BY gap_id",
                    (queue["ticker"],),
                )]
                evidence = _build_evidence(queue, catalog, snapshot_as_of, gap_ids)
                identity = {
                    "run_id": inputs["run_id"], "snapshot_id": queue["snapshot_id"],
                    "symbol": queue["ticker"], "venue_code": queue["venue_code"],
                    "classification_id": queue["queue_id"],
                }
                packet = analyze(identity, evidence)
                packets.append(packet)
                for candidate in packet["sector_candidates"]:
                    candidate_sectors[candidate["sector"]] += 1
                queue_status = READY_STATUS if packet["status"] == "ready_for_babis_review" else REVIEW_STATUS
                with db:
                    db.execute("INSERT OR REPLACE INTO unresolved_classification_packet_store VALUES(?,?,?,?,?,?,?,?)", (
                        packet["packet_id"], inputs["run_id"], inputs["activation_id"], queue["queue_id"],
                        queue["ticker"], json.dumps(packet, ensure_ascii=False, sort_keys=True), packet["status"], packet["created_at"],
                    ))
                    db.execute("UPDATE sector_analysis_queue SET status=? WHERE queue_id=?", (queue_status, queue["queue_id"]))
                state["source_reuse_count"] += 1
            except Exception as exc:
                state["failed"] += 1
                with (folder / "errors.log").open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps({"ticker": queue["ticker"], "error_type": type(exc).__name__, "error": str(exc)}, ensure_ascii=False) + "\n")
            state["processed_symbols"] = index
            state["ready_for_babis_review"] = sum(p["status"] == "ready_for_babis_review" for p in packets)
            state["review_required"] = sum(p["status"] == "review_required" for p in packets)
            if index % 50 == 0:
                state["updated_at"] = now(); save(folder / "state.json", state)
        else:
            state["status"] = "finished_with_review_required"
    except Exception as exc:
        state.update(status="failed", error_type=type(exc).__name__, error_message=str(exc)[:1000])
    finally:
        state["finished_at"] = now(); state["updated_at"] = state["finished_at"]
        state["ready_for_babis_review"] = sum(p["status"] == "ready_for_babis_review" for p in packets)
        state["review_required"] = sum(p["status"] == "review_required" for p in packets)
        save(folder / "state.json", state)
        if packets:
            _write_report(folder, state, packets, catalog_statuses, candidate_sectors)
        with db:
            db.execute("UPDATE unresolved_classification_run SET status=?,processed_count=?,"
                       "ready_for_babis_review_count=?,review_required_count=?,failed_count=?,"
                       "source_reuse_count=?,updated_at=?,finished_at=?,summary_json=? WHERE run_id=?", (
                           state["status"], state["processed_symbols"], state["ready_for_babis_review"],
                           state["review_required"], state["failed"], state["source_reuse_count"],
                           state["updated_at"], state["finished_at"], json.dumps(state, ensure_ascii=False, sort_keys=True),
                           inputs["run_id"],
                       ))
        db.close(); lock.close()
    return 1 if state["status"] == "failed" else 0


def start(activation_id: str | None = None, db_path: Path = DEFAULT_DB) -> dict[str, Any]:
    folder = prepare(activation_id, db_path)
    log = (folder / "worker.log").open("a", encoding="utf-8")
    try:
        process = subprocess.Popen(
            [sys.executable, "-B", str(Path(__file__).resolve()), "run", "--job", str(folder), "--db", str(db_path)],
            cwd=PROJECT, stdin=subprocess.DEVNULL, stdout=log, stderr=log,
            creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW,
        )
    finally:
        log.close()
    save(ROOT / "latest.json", {"run": str(folder), "run_id": folder.name, "pid": process.pid})
    notifier = PROJECT / "notify_unresolved_classification.ps1"
    if notifier.exists():
        subprocess.Popen(
            ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(notifier),
             "-StatePath", str(folder / "state.json")],
            cwd=PROJECT, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW,
        )
    return {"status": "dispatched", "pid": process.pid, "run": str(folder), "run_id": folder.name}


def status(job: Path | None = None) -> dict[str, Any]:
    if job is None:
        latest = json.loads((ROOT / "latest.json").read_text(encoding="utf-8"))
        job = Path(latest["run"])
    return json.loads((Path(job) / "state.json").read_text(encoding="utf-8"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("start", "run", "status"))
    parser.add_argument("--activation-id")
    parser.add_argument("--job", type=Path)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    args = parser.parse_args(argv)
    if args.action == "start":
        result = start(args.activation_id, args.db)
    elif args.action == "run":
        if not args.job:
            parser.error("--job is required for run")
        return run(args.job, args.db)
    else:
        result = status(args.job)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
