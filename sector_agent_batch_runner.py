"""Run the production sector-agent queue as a finite offline-first batch.

This bootstrap pass creates an auditable Sector Context Packet for every
classified corporate symbol. Missing research remains explicit and packets
without source evidence are review-required; no market claim is fabricated.
"""
from __future__ import annotations

import argparse
from collections import Counter
import csv
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
from typing import Any

from research_registry.core import DEFAULT_DB, PROJECT, connect
from sector_ecosystem_integration import schema as ecosystem_schema
from sector_research_agent import analyze_input


ROOT = PROJECT / "data" / "sector_agent_runs"
ELIGIBLE_STATUS = "ready_for_sector_evidence"
PACKET_STATUS = "sector_context_review_required"
TERMINAL_STATES = {"finished_with_review_required", "failed", "stopped_with_pending"}


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def save(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def schema(db: sqlite3.Connection) -> None:
    ecosystem_schema(db)
    db.executescript("""
      CREATE TABLE IF NOT EXISTS sector_agent_run(
        run_id TEXT PRIMARY KEY,activation_id TEXT NOT NULL,status TEXT NOT NULL,
        total_count INTEGER NOT NULL,processed_count INTEGER NOT NULL,
        complete_count INTEGER NOT NULL,review_required_count INTEGER NOT NULL,
        failed_count INTEGER NOT NULL,network_calls INTEGER NOT NULL,
        llm_calls INTEGER NOT NULL,created_at TEXT NOT NULL,updated_at TEXT NOT NULL,
        finished_at TEXT,summary_json TEXT NOT NULL);
      CREATE INDEX IF NOT EXISTS sector_agent_run_status
        ON sector_agent_run(status,created_at);
    """)


def _latest_activation(db: sqlite3.Connection) -> str:
    row = db.execute(
        "SELECT activation_id FROM sector_activation_run ORDER BY created_at DESC LIMIT 1"
    ).fetchone()
    if not row:
        raise ValueError("No sector activation exists")
    return row[0]


def prepare(activation_id: str | None = None, db_path: Path = DEFAULT_DB,
            root: Path = ROOT) -> Path:
    db = connect(db_path)
    try:
        schema(db)
        activation_id = activation_id or _latest_activation(db)
        row = db.execute(
            "SELECT snapshot_id FROM sector_activation_run WHERE activation_id=?",
            (activation_id,),
        ).fetchone()
        if not row:
            raise ValueError(f"Unknown activation: {activation_id}")
        total = db.execute(
            "SELECT COUNT(*) FROM sector_analysis_queue WHERE activation_id=? AND status=?",
            (activation_id, ELIGIBLE_STATUS),
        ).fetchone()[0]
        if not total:
            raise ValueError("No ready sector-evidence items are eligible for this run")
        run_id = "sector-agents-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        folder = Path(root) / run_id
        folder.mkdir(parents=True, exist_ok=False)
        payload = {
            "run_id": run_id,
            "activation_id": activation_id,
            "snapshot_id": row[0],
            "total_symbols": total,
            "created_at": now(),
            "policy": {
                "offline_first": True,
                "network_calls": 0,
                "llm_calls": 0,
                "classification_writes": False,
                "financial_verdicts": False,
                "unknowns_are_explicit": True,
            },
        }
        save(folder / "input.json", payload)
        save(folder / "state.json", {"status": "prepared", **payload})
        return folder
    finally:
        db.close()


def _gap_ids(db: sqlite3.Connection, ticker: str) -> list[str]:
    exists = db.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='ecosystem_evidence_gap'"
    ).fetchone()
    if not exists:
        return []
    return [row[0] for row in db.execute(
        "SELECT gap_id FROM ecosystem_evidence_gap WHERE ticker=? "
        "AND resolution_state IN ('open','cooldown','retry_eligible') ORDER BY gap_id",
        (ticker,),
    )]


def _write_report(folder: Path, state: dict[str, Any], rows: list[dict[str, Any]],
                  by_agent: Counter[str], by_subcategory: Counter[str]) -> None:
    with (folder / "symbol_results.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        fields = ["ticker", "venue_code", "agent_id", "sector", "subcategory", "status",
                  "confidence", "unknown_count", "evidence_gap_count"]
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(sorted(rows, key=lambda item: (item["ticker"], item["venue_code"])))
    lines = [
        "# Sector-agent production bootstrap", "",
        f"Run: `{state['run_id']}`", f"Status: `{state['status']}`", "",
        "## Summary", "",
        f"- Eligible classified symbols: **{state['total_symbols']}**",
        f"- Processed: **{state['processed_symbols']}**",
        f"- Evidence-complete packets: **{state['complete']}**",
        f"- Review-required packets: **{state['review_required']}**",
        f"- Failed: **{state['failed']}**",
        "- Network calls: **0**", "- LLM calls: **0**",
        "- Classification writes: **0**", "- Financial verdicts: **0**", "",
        "## Packets by sector agent", "", "| Agent | Packets |", "|---|---:|",
    ]
    lines += [f"| `{key}` | {value} |" for key, value in sorted(by_agent.items())]
    lines += ["", "## Packets by category / subcategory", "", "| Node | Packets |", "|---|---:|"]
    lines += [f"| `{key}` | {value} |" for key, value in sorted(by_subcategory.items())]
    lines += [
        "", "## Interpretation", "",
        "This is a production bootstrap and coverage inventory, not a claim that company research is complete.",
        "Every symbol now has a schema-valid Sector Context Packet. Where sourced business-model or sector-KPI evidence is absent, the packet is explicitly review-required and lists the unknown fields.",
        "Only evidence-complete packets may proceed to Giannis; this run does not enqueue unsupported packets for financial analysis.",
    ]
    (folder / "REPORT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def run(folder: Path, db_path: Path = DEFAULT_DB) -> int:
    import msvcrt
    folder = Path(folder)
    lock = (folder.parent / "sector_agent_worker.lock").open("a+b")
    lock.seek(0)
    try:
        msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
    except OSError:
        lock.close()
        return 2
    inputs = json.loads((folder / "input.json").read_text(encoding="utf-8"))
    state = {
        **inputs, "status": "running", "pid": os.getpid(), "started_at": now(),
        "processed_symbols": 0, "complete": 0, "review_required": 0, "failed": 0,
        "network_calls": 0, "llm_calls": 0, "classification_writes": 0,
        "financial_handoffs": 0,
    }
    save(folder / "state.json", state)
    db = connect(db_path)
    results: list[dict[str, Any]] = []
    by_agent: Counter[str] = Counter()
    by_subcategory: Counter[str] = Counter()
    try:
        schema(db)
        queue = list(db.execute(
            "SELECT * FROM sector_analysis_queue WHERE activation_id=? AND status=? "
            "ORDER BY ticker,venue_code",
            (inputs["activation_id"], ELIGIBLE_STATUS),
        ))
        with db:
            db.execute("INSERT OR REPLACE INTO sector_agent_run VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (
                inputs["run_id"], inputs["activation_id"], state["status"], len(queue), 0, 0, 0, 0,
                0, 0, inputs["created_at"], now(), None,
                json.dumps(state, ensure_ascii=False, sort_keys=True),
            ))
        for index, item in enumerate(queue, start=1):
            if (folder / "STOP").exists():
                state["status"] = "stopped_with_pending"
                break
            try:
                identity = {
                    "run_id": inputs["run_id"], "snapshot_id": item["snapshot_id"],
                    "symbol": item["ticker"], "venue_code": item["venue_code"],
                    "classification_id": item["queue_id"], "leaf_node": item["node_id"],
                }
                packet = analyze_input({
                    "identity": identity,
                    "evidence": {"evidence_gap_ids": _gap_ids(db, item["ticker"])},
                })
                stored_status = "complete" if packet["status"] == "complete" else "review_required"
                queue_status = "sector_context_complete" if stored_status == "complete" else PACKET_STATUS
                with db:
                    db.execute("INSERT OR REPLACE INTO sector_context_packet_store VALUES(?,?,?,?,?,?,?,?,?)", (
                        packet["packet_id"], inputs["activation_id"], item["queue_id"], packet["agent_id"],
                        inputs["run_id"], item["ticker"], json.dumps(packet, ensure_ascii=False, sort_keys=True),
                        stored_status, packet["created_at"],
                    ))
                    db.execute("UPDATE sector_analysis_queue SET status=? WHERE queue_id=?", (queue_status, item["queue_id"]))
                by_agent[packet["agent_id"]] += 1
                by_subcategory[f"{packet['sector']}.{packet['subcategory']}"] += 1
                results.append({
                    "ticker": item["ticker"], "venue_code": item["venue_code"],
                    "agent_id": packet["agent_id"], "sector": packet["sector"],
                    "subcategory": packet["subcategory"], "status": packet["status"],
                    "confidence": packet["confidence"], "unknown_count": len(packet["unknown_fields"]),
                    "evidence_gap_count": len(packet["evidence_gap_ids"]),
                })
            except Exception as exc:
                state["failed"] += 1
                with (folder / "errors.log").open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps({
                        "ticker": item["ticker"], "venue_code": item["venue_code"],
                        "error_type": type(exc).__name__, "error": str(exc),
                    }, ensure_ascii=False) + "\n")
            state["processed_symbols"] = index
            state["complete"] = sum(row["status"] == "complete" for row in results)
            state["review_required"] = sum(row["status"] == "review_required" for row in results)
            if index % 100 == 0:
                state["updated_at"] = now()
                save(folder / "state.json", state)
        else:
            state["status"] = "finished_with_review_required" if state["review_required"] else "finished"
    except Exception as exc:
        state.update(status="failed", error_type=type(exc).__name__, error_message=str(exc)[:1000])
    finally:
        state["finished_at"] = now()
        state["updated_at"] = state["finished_at"]
        save(folder / "state.json", state)
        if results:
            _write_report(folder, state, results, by_agent, by_subcategory)
        with db:
            db.execute(
                "UPDATE sector_agent_run SET status=?,processed_count=?,complete_count=?,"
                "review_required_count=?,failed_count=?,updated_at=?,finished_at=?,summary_json=? WHERE run_id=?",
                (state["status"], state["processed_symbols"], state["complete"], state["review_required"],
                 state["failed"], state["updated_at"], state["finished_at"],
                 json.dumps(state, ensure_ascii=False, sort_keys=True), inputs["run_id"]),
            )
        db.close()
        lock.close()
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
    notifier = PROJECT / "notify_sector_agent_run.ps1"
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
