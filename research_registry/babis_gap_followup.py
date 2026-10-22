"""Recheck due Babis evidence gaps without rerunning the full universe.

Each invocation is bounded to the provider's remaining UTC-day allowance.  It
runs once, has no polling/retry loop, and records every attempt.  Successful
evidence closes only the matching gap; classification changes still require a
separate review and approval workflow.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import subprocess
from typing import Any

from .babis_problem_enrichment import RATE_POLICY, SHARED_RATE_DB, Sources
from .core import DEFAULT_DB, PROJECT, connect, digest
from .evidence_gaps import _next_eligible, _reason, schema as gap_schema


ROOT = PROJECT / "data" / "babis_gap_followup"
STOP_STATUSES = {"rate_limited", "shared_daily_budget_exhausted", "shared_cooldown:rate_limited"}


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def followup_schema(db: sqlite3.Connection) -> None:
    gap_schema(db)
    db.executescript("""
      CREATE TABLE IF NOT EXISTS ecosystem_evidence_gap_attempt(
        attempt_id TEXT PRIMARY KEY,gap_id TEXT NOT NULL,provider TEXT NOT NULL,
        attempted_at TEXT NOT NULL,status TEXT NOT NULL,evidence_hash TEXT,
        payload_json TEXT NOT NULL);
      CREATE INDEX IF NOT EXISTS evidence_gap_attempt_gap
        ON ecosystem_evidence_gap_attempt(gap_id,attempted_at);
    """)


def remaining_budget(provider: str, rate_db: Path = SHARED_RATE_DB) -> int:
    policy = json.loads(RATE_POLICY.read_text(encoding="utf-8"))["providers"][provider]
    maximum = int(policy["maximum_requests_per_utc_day"])
    if not Path(rate_db).exists():
        return maximum
    db = sqlite3.connect(rate_db)
    try:
        try:
            row = db.execute("SELECT utc_day,day_requests,blocked_until FROM provider_rate_state WHERE provider=?", (provider,)).fetchone()
        except sqlite3.OperationalError:
            row = None
    finally:
        db.close()
    if not row:
        return maximum
    current = datetime.now(timezone.utc)
    if float(row[2] or 0) > current.timestamp():
        return 0
    used = int(row[1]) if row[0] == current.date().isoformat() else 0
    return max(0, maximum - used)


def due_gaps(db: sqlite3.Connection, provider: str, limit: int, at: str | None = None) -> list[sqlite3.Row]:
    followup_schema(db)
    stamp = at or now()
    return db.execute(
        "SELECT * FROM ecosystem_evidence_gap WHERE owner_agent='babis' AND provider=? "
        "AND resolution_state IN ('open','cooldown','retry_eligible') "
        "AND (next_eligible_at IS NULL OR next_eligible_at<=?) "
        "ORDER BY COALESCE(next_eligible_at,created_at),gap_id LIMIT ?",
        (provider, stamp, limit),
    ).fetchall()


def record_result(db: sqlite3.Connection, gap: sqlite3.Row, result: dict[str, Any]) -> None:
    stamp = now()
    status = str(result.get("status") or "unknown")
    evidence_hash = result.get("evidence_sha256") or digest(result)
    attempt_id = "gap_attempt_" + digest([gap["gap_id"], stamp, evidence_hash])[:24]
    reason = _reason(status)
    next_at = _next_eligible(gap["provider"], reason, SHARED_RATE_DB)
    state = "resolved" if status == "ok" else (
        "cooldown" if reason in {"provider_rate_limited", "provider_daily_budget_exhausted"} else "open"
    )
    with db:
        db.execute(
            "INSERT INTO ecosystem_evidence_gap_attempt VALUES(?,?,?,?,?,?,?)",
            (attempt_id, gap["gap_id"], gap["provider"], stamp, status, evidence_hash,
             json.dumps(result, ensure_ascii=False, sort_keys=True)),
        )
        db.execute(
            "UPDATE ecosystem_evidence_gap SET observed_status=?,reason_code=?,resolution_state=?,"
            "next_eligible_at=?,evidence_hash=?,resolved_at=? WHERE gap_id=?",
            (status, reason, state, next_at, evidence_hash, stamp if state == "resolved" else None, gap["gap_id"]),
        )


def notify(message: str) -> None:
    try:
        subprocess.run(["msg.exe", "*", message], check=False, timeout=15,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                       creationflags=subprocess.CREATE_NO_WINDOW)
    except (OSError, subprocess.SubprocessError):
        pass


def run(provider: str = "fmp", requested_limit: int | None = None) -> dict[str, Any]:
    if provider != "fmp":
        raise ValueError("Only the FMP gap follow-up is enabled")
    allowance = remaining_budget(provider)
    limit = min(allowance, requested_limit) if requested_limit is not None else allowance
    if limit <= 0:
        return {"status": "provider_not_eligible", "provider": provider, "examined": 0, "resolved": 0}
    db = connect(DEFAULT_DB)
    try:
        gaps = due_gaps(db, provider, limit)
        if not gaps:
            return {"status": "no_due_gaps", "provider": provider, "examined": 0, "resolved": 0}
        job_id = "babis-gap-followup-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        folder = ROOT / job_id
        folder.mkdir(parents=True)
        source = Sources(folder, max_requests=limit)
        examined = resolved = 0
        try:
            for gap in gaps:
                result = source.fmp(gap["ticker"])
                record_result(db, gap, result)
                examined += 1
                resolved += int(result.get("status") == "ok")
                if str(result.get("status")) in STOP_STATUSES or str(result.get("status", "")).startswith("provider_disabled:rate_limited"):
                    break
        finally:
            source.db.close(); source.rate_db.close()
        payload = {"status": "finished", "job_id": job_id, "provider": provider,
                   "eligible": len(gaps), "examined": examined, "resolved": resolved,
                   "remaining_due": max(0, len(gaps) - examined), "finished_at": now(),
                   "automatic_classification_writes": 0, "retries": 0}
        (folder / "report.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return payload
    finally:
        db.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("run", "status"))
    parser.add_argument("--provider", default="fmp", choices=("fmp",))
    parser.add_argument("--limit", type=int)
    args = parser.parse_args(argv)
    if args.action == "status":
        db = connect(DEFAULT_DB)
        try:
            rows = due_gaps(db, args.provider, 1_000_000)
        finally:
            db.close()
        result = {"provider": args.provider, "due": len(rows), "remaining_budget": remaining_budget(args.provider)}
    else:
        result = run(args.provider, args.limit)
        if result["status"] == "finished":
            notify(f"Ο Μπάμπης επανεξέτασε {result['examined']} {args.provider.upper()} evidence gaps και έκλεισε {result['resolved']}.")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
