from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import market_intel_db
import research_control_plane
from run_context import require_compatible_runs
from symbol_classification import classify_symbol, classification_coverage


SCHEMA = "ecosystem.information_observation.v1"


class InformationConflictError(RuntimeError):
    pass


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def init_schema(conn: sqlite3.Connection) -> None:
    market_intel_db.init_db(conn)
    research_control_plane.init_schema(conn)
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS information_observations (
            observation_id TEXT PRIMARY KEY,
            run_id TEXT NOT NULL,
            source_run_timestamp TEXT NOT NULL,
            recorded_at TEXT NOT NULL,
            ticker TEXT NOT NULL,
            sector_id TEXT NOT NULL,
            industry_id TEXT NOT NULL,
            classification_status TEXT NOT NULL,
            classification_source TEXT NOT NULL,
            catalyst_category_id TEXT,
            catalyst_direction TEXT,
            catalyst_evidence_json TEXT NOT NULL,
            fresh_primary_data INTEGER NOT NULL,
            missing_fields_json TEXT NOT NULL,
            unknown_events_json TEXT NOT NULL DEFAULT '[]',
            payload_sha256 TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            UNIQUE(run_id, ticker)
        );
        CREATE INDEX IF NOT EXISTS idx_information_observations_sector
            ON information_observations(sector_id, industry_id, ticker);
        """
    )
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(information_observations)")}
    if "unknown_events_json" not in columns:
        conn.execute(
            "ALTER TABLE information_observations ADD COLUMN unknown_events_json TEXT NOT NULL DEFAULT '[]'"
        )
    conn.commit()


def load(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict):
        raise ValueError(f"Expected JSON object: {path}")
    return payload


def rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    value = payload.get("results")
    if not isinstance(value, list):
        value = payload.get("shortlist", [])
    return [row for row in value if isinstance(row, dict)] if isinstance(value, list) else []


def record_information(db_path: Path, babis: dict[str, Any], giannis: dict[str, Any]) -> dict[str, Any]:
    run_id = require_compatible_runs(babis, giannis)
    run_ts = str(giannis.get("run_timestamp") or babis.get("run_timestamp") or datetime.now(timezone.utc).isoformat())
    babis_by_ticker = {str(row.get("ticker") or row.get("symbol") or "").upper(): row for row in rows(babis)}
    merged_rows: list[dict[str, Any]] = []
    for row in rows(giannis):
        ticker = str(row.get("ticker") or "").upper().strip()
        if ticker:
            merged_rows.append({**babis_by_ticker.get(ticker, {}), **row, "ticker": ticker})

    inserted = existing = 0
    unknown_event_count = 0
    with closing(market_intel_db.connect(db_path)) as conn:
        init_schema(conn)
        for row in merged_rows:
            classification = classify_symbol(row)
            unknown_trace = list(row.get("upstream_unknown_events") or []) + list(
                row.get("unknown_events") or []
            )
            unknown_event_count += len(unknown_trace)
            immutable = {
                "schema": SCHEMA,
                "run_id": run_id,
                "source_run_timestamp": run_ts,
                "ticker": classification["ticker"],
                "classification": classification,
                "catalyst_category_id": row.get("catalyst_category_id"),
                "catalyst_direction": row.get("catalyst_direction"),
                "catalyst_evidence": row.get("catalyst_evidence", []),
                "fresh_primary_data": bool(row.get("fresh_primary_data")),
                "missing_fields": row.get("unknown_fields", []),
                "unknown_events": unknown_trace,
                "information_payload": row,
            }
            digest = hashlib.sha256(canonical_json(immutable).encode("utf-8")).hexdigest()
            previous = conn.execute(
                "SELECT payload_sha256 FROM information_observations WHERE run_id = ? AND ticker = ?",
                (run_id, classification["ticker"]),
            ).fetchone()
            if previous:
                if previous["payload_sha256"] != digest:
                    raise InformationConflictError(f"Recorded information changed: {run_id}/{classification['ticker']}")
                research_control_plane.record_observation(
                    conn,
                    subject_id=f"ticker:{classification['ticker']}",
                    capability="research.information_ledger",
                    provider="ecosystem_internal",
                    source_ref=f"{run_id}/{classification['ticker']}",
                    observed_at=run_ts,
                    as_of=run_ts,
                    run_id=run_id,
                    payload=immutable,
                    status="available",
                )
                existing += 1
                continue
            conn.execute(
                """
                INSERT INTO information_observations (
                    observation_id, run_id, source_run_timestamp, recorded_at, ticker,
                    sector_id, industry_id, classification_status, classification_source,
                    catalyst_category_id, catalyst_direction, catalyst_evidence_json,
                    fresh_primary_data, missing_fields_json, unknown_events_json,
                    payload_sha256, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    f"info_{digest[:24]}", run_id, run_ts, datetime.now(timezone.utc).isoformat(),
                    classification["ticker"], classification["sector_id"], classification["industry_id"],
                    classification["classification_status"], classification["classification_source"],
                    row.get("catalyst_category_id"), row.get("catalyst_direction"),
                    canonical_json(row.get("catalyst_evidence", [])), int(bool(row.get("fresh_primary_data"))),
                    canonical_json(row.get("unknown_fields", [])), canonical_json(unknown_trace),
                    digest, canonical_json(immutable),
                ),
            )
            research_control_plane.record_observation(
                conn,
                subject_id=f"ticker:{classification['ticker']}",
                capability="research.information_ledger",
                provider="ecosystem_internal",
                source_ref=f"{run_id}/{classification['ticker']}",
                observed_at=run_ts,
                as_of=run_ts,
                run_id=run_id,
                payload=immutable,
                status="available",
            )
            inserted += 1
        conn.commit()
    return {
        "schema": "ecosystem.information_ledger_summary.v1", "run_id": run_id,
        "inserted": inserted, "existing": existing,
        "unknown_event_count": unknown_event_count,
        "classification_coverage": classification_coverage(merged_rows),
        "evaluation_status": "not_evaluated",
        "note": "This ledger records and categorizes information; it does not score information quality or trade performance.",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Immutable information recording and symbol categorization.")
    parser.add_argument("--db", default=str(market_intel_db.DEFAULT_DB_PATH))
    parser.add_argument("--babis", default="output/babis_shortlist_latest.json")
    parser.add_argument("--giannis", default="output/giannis_rule_based_latest.json")
    parser.add_argument("--out", default="output/information_ledger_latest.json")
    args = parser.parse_args()
    summary = record_information(Path(args.db), load(Path(args.babis)), load(Path(args.giannis)))
    out = Path(args.out); out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(summary, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    print(f"[INFORMATION] recorded={summary['inserted']} existing={summary['existing']} evaluation=not_evaluated")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
