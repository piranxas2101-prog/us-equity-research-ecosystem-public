"""Shared, reason-coded evidence-gap registry for every ecosystem agent.

The registry records why evidence was unavailable and prevents downstream
agents from treating provider failures as non-disclosure or retrying a provider
independently while a shared cooldown or budget gate is active.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone, timedelta
import json
from pathlib import Path
import sqlite3
from typing import Any

from .core import DEFAULT_DB, PROJECT, connect, digest


DEFAULT_RATE_DB = PROJECT / "data" / "babis_enrichment" / "provider_rate_state.sqlite3"
CONTRACT_PATH = PROJECT / "unknown_data_contract.json"


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def schema(db: sqlite3.Connection) -> None:
    db.executescript(
        """
        CREATE TABLE IF NOT EXISTS ecosystem_evidence_gap(
          gap_id TEXT PRIMARY KEY,
          subject_id TEXT NOT NULL,
          ticker TEXT,
          field TEXT NOT NULL,
          provider TEXT NOT NULL,
          reason_code TEXT NOT NULL,
          observed_status TEXT NOT NULL,
          observed_at TEXT,
          owner_agent TEXT NOT NULL,
          consumer_agents_json TEXT NOT NULL,
          decision_impact TEXT NOT NULL,
          resolution_state TEXT NOT NULL,
          next_eligible_at TEXT,
          source_run_id TEXT NOT NULL,
          evidence_hash TEXT,
          payload_json TEXT NOT NULL,
          created_at TEXT NOT NULL,
          resolved_at TEXT
        );
        CREATE INDEX IF NOT EXISTS ecosystem_evidence_gap_open
          ON ecosystem_evidence_gap(resolution_state,reason_code,provider);
        CREATE INDEX IF NOT EXISTS ecosystem_evidence_gap_ticker
          ON ecosystem_evidence_gap(ticker,field);
        CREATE INDEX IF NOT EXISTS ecosystem_evidence_gap_subject
          ON ecosystem_evidence_gap(subject_id);
        CREATE VIEW IF NOT EXISTS open_ecosystem_evidence_gap AS
          SELECT * FROM ecosystem_evidence_gap
          WHERE resolution_state IN ('open','cooldown','retry_eligible');
        """
    )


def _contract() -> dict[str, Any]:
    return json.loads(CONTRACT_PATH.read_text(encoding="utf-8-sig"))


def _reason(status: str) -> str:
    value = status.lower()
    if "daily_budget" in value or value == "budget_exhausted":
        return "provider_daily_budget_exhausted"
    if "rate_limited" in value or "shared_cooldown" in value or value == "429":
        return "provider_rate_limited"
    if value in {"missing_configuration", "auth_failed", "forbidden_or_plan"}:
        return "contract_mismatch"
    if value in {"empty_or_ambiguous_response", "not_found", "no_data"}:
        return "insufficient_source_detail"
    if value in {"timeout_or_network", "http_error", "transport_error", "temporarily_unavailable"}:
        return "temporarily_unavailable"
    return "provider_no_coverage"


def _field(category: str) -> str:
    if category == "instrument_type_review":
        return "instrument_type"
    if category == "identity_resolution":
        return "security_identity"
    return "sector_id"


def _next_eligible(provider: str, reason_code: str, rate_db: Path) -> str | None:
    if reason_code == "provider_daily_budget_exhausted":
        current = datetime.now(timezone.utc)
        return datetime.combine(current.date() + timedelta(days=1), datetime.min.time(), tzinfo=timezone.utc).isoformat()
    if reason_code != "provider_rate_limited" or not rate_db.exists():
        return None
    db = sqlite3.connect(rate_db)
    try:
        row = db.execute("SELECT blocked_until FROM provider_rate_state WHERE provider=?", (provider,)).fetchone()
    finally:
        db.close()
    if not row or not row[0]:
        return None
    return datetime.fromtimestamp(float(row[0]), timezone.utc).isoformat()


def build_gaps(rows: list[dict[str, Any]], source_run_id: str, rate_db: Path = DEFAULT_RATE_DB) -> list[dict[str, Any]]:
    contract = _contract()
    agents = sorted(contract["agents"])
    reasons = contract["reason_codes"]
    gaps: list[dict[str, Any]] = []
    stamp = now()
    for row in rows:
        problem_id = row["problem_id"]
        category = row.get("category") or "unclassified_review"
        field = _field(category)
        created = False
        for source in row.get("source_refs") or []:
            status = str(source.get("status") or "unknown")
            if status == "ok":
                continue
            provider = str(source.get("provider") or "unknown")
            reason_code = _reason(status)
            action = reasons[reason_code]["default_action"]
            state = "cooldown" if reason_code in {
                "provider_rate_limited", "provider_daily_budget_exhausted"
            } else "open"
            body = {
                "problem_id": problem_id,
                "assessment": row.get("assessment"),
                "category": category,
                "source_ref": source,
                "default_action": action,
            }
            gaps.append({
                "gap_id": "gap_" + digest([source_run_id, problem_id, field, provider, reason_code])[:24],
                "subject_id": problem_id,
                "ticker": row.get("ticker"),
                "field": field,
                "provider": provider,
                "reason_code": reason_code,
                "observed_status": status,
                "observed_at": source.get("observed_at"),
                "owner_agent": "babis",
                "consumer_agents_json": json.dumps(agents, ensure_ascii=False),
                "decision_impact": "classification_and_downstream_sector_context",
                "resolution_state": state,
                "next_eligible_at": _next_eligible(provider, reason_code, rate_db),
                "source_run_id": source_run_id,
                "evidence_hash": source.get("hash"),
                "payload_json": json.dumps(body, ensure_ascii=False, sort_keys=True),
                "created_at": stamp,
                "resolved_at": None,
            })
            created = True
        assessment = row.get("assessment")
        synthetic_reason = None
        if assessment == "supplemental_source_conflict":
            synthetic_reason = "conflicting_sources"
        elif assessment == "insufficient_supplemental_evidence" and not created:
            synthetic_reason = "insufficient_source_detail"
        if synthetic_reason:
            provider = "classification_evidence_pipeline"
            body = {
                "problem_id": problem_id,
                "assessment": assessment,
                "category": category,
                "default_action": reasons[synthetic_reason]["default_action"],
            }
            gaps.append({
                "gap_id": "gap_" + digest([source_run_id, problem_id, field, provider, synthetic_reason])[:24],
                "subject_id": problem_id,
                "ticker": row.get("ticker"),
                "field": field,
                "provider": provider,
                "reason_code": synthetic_reason,
                "observed_status": assessment,
                "observed_at": stamp,
                "owner_agent": "babis",
                "consumer_agents_json": json.dumps(agents, ensure_ascii=False),
                "decision_impact": "classification_and_downstream_sector_context",
                "resolution_state": "open",
                "next_eligible_at": None,
                "source_run_id": source_run_id,
                "evidence_hash": digest(row),
                "payload_json": json.dumps(body, ensure_ascii=False, sort_keys=True),
                "created_at": stamp,
                "resolved_at": None,
            })
    return gaps


def import_enrichment(db: sqlite3.Connection, folder: Path, rate_db: Path = DEFAULT_RATE_DB) -> dict[str, Any]:
    folder = Path(folder)
    state = json.loads((folder / "state.json").read_text(encoding="utf-8"))
    rows = json.loads((folder / "assessments.json").read_text(encoding="utf-8"))
    if state.get("status") != "finished_with_review_required":
        raise ValueError("Only a completed enrichment run can create production evidence gaps")
    if state.get("processed_symbols") != len(rows):
        raise ValueError("Enrichment rows do not reconcile")
    gaps = build_gaps(rows, state["job_id"], rate_db)
    schema(db)
    before = db.total_changes
    with db:
        db.executemany(
            "INSERT OR IGNORE INTO ecosystem_evidence_gap VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            [tuple(gap[key] for key in (
                "gap_id", "subject_id", "ticker", "field", "provider", "reason_code",
                "observed_status", "observed_at", "owner_agent", "consumer_agents_json",
                "decision_impact", "resolution_state", "next_eligible_at", "source_run_id",
                "evidence_hash", "payload_json", "created_at", "resolved_at",
            )) for gap in gaps],
        )
    inserted = db.total_changes - before
    return {
        "status": "evidence_gaps_imported",
        "source_run_id": state["job_id"],
        "generated": len(gaps),
        "inserted": inserted,
        "by_reason": dict(Counter(gap["reason_code"] for gap in gaps)),
        "consumer_agents": sorted(_contract()["agents"]),
    }


def status(db: sqlite3.Connection) -> dict[str, Any]:
    schema(db)
    return {
        "total": db.execute("SELECT COUNT(*) FROM ecosystem_evidence_gap").fetchone()[0],
        "open": db.execute("SELECT COUNT(*) FROM open_ecosystem_evidence_gap").fetchone()[0],
        "by_reason": dict(db.execute(
            "SELECT reason_code,COUNT(*) FROM ecosystem_evidence_gap GROUP BY reason_code ORDER BY reason_code"
        )),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("install")
    p = sub.add_parser("import-enrichment")
    p.add_argument("--job", type=Path, required=True)
    p.add_argument("--rate-db", type=Path, default=DEFAULT_RATE_DB)
    sub.add_parser("status")
    args = parser.parse_args(argv)
    db = connect(args.db)
    try:
        if args.command == "install":
            schema(db)
            db.commit()
            result = {"status": "installed"}
        elif args.command == "import-enrichment":
            result = import_enrichment(db, args.job, args.rate_db)
        else:
            result = status(db)
    finally:
        db.close()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
