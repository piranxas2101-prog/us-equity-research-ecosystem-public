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
from run_context import require_compatible_runs
from symbol_classification import classify_symbol


SCHEMA = "ecosystem.market_experiment.v1"


class ExperimentConflictError(RuntimeError):
    pass


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def load_payload(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict):
        raise ValueError(f"Expected JSON object: {path}")
    return payload


def init_schema(conn: sqlite3.Connection) -> None:
    market_intel_db.init_db(conn)
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS trader_experiments (
            experiment_id TEXT PRIMARY KEY,
            run_id TEXT NOT NULL,
            source_run_timestamp TEXT NOT NULL,
            recorded_at TEXT NOT NULL,
            agent_name TEXT NOT NULL,
            ticker TEXT NOT NULL,
            playbook_name TEXT NOT NULL,
            experiment_type TEXT NOT NULL,
            trade_direction TEXT NOT NULL,
            signal_verdict TEXT,
            signal_score REAL,
            confidence_label TEXT,
            confidence_probability REAL,
            horizon_days INTEGER NOT NULL,
            baseline_price REAL,
            sector_id TEXT,
            industry_id TEXT,
            catalyst_category_id TEXT,
            catalyst_trade_bias TEXT,
            risk_level TEXT,
            status TEXT NOT NULL,
            input_sha256 TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            UNIQUE(run_id, agent_name, ticker, playbook_name)
        );
        CREATE INDEX IF NOT EXISTS idx_trader_experiments_symbol
            ON trader_experiments(ticker, source_run_timestamp);
        CREATE INDEX IF NOT EXISTS idx_trader_experiments_sector
            ON trader_experiments(sector_id, industry_id);
        """
    )
    conn.commit()


def _result_rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    rows = payload.get("results", [])
    return [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []


def record_experiments(
    db_path: Path,
    giannis: dict[str, Any],
    strategy_payloads: list[dict[str, Any]],
    *,
    horizon_days: int = 20,
) -> dict[str, Any]:
    run_id = require_compatible_runs(giannis, *strategy_payloads)
    run_ts = str(giannis.get("run_timestamp") or datetime.now(timezone.utc).isoformat())
    symbol_rows = {str(row.get("ticker") or "").upper(): row for row in _result_rows(giannis)}
    inserted = existing = 0

    with closing(market_intel_db.connect(db_path)) as conn:
        init_schema(conn)
        for strategy in strategy_payloads:
            default_agent = str(strategy.get("agent_name") or strategy.get("agent") or "unknown")
            for signal in _result_rows(strategy):
                ticker = str(signal.get("ticker") or "").upper().strip()
                if not ticker:
                    continue
                source = symbol_rows.get(ticker, {})
                merged = {**source, **signal}
                classification = classify_symbol(merged)
                direction = str(signal.get("trade_direction") or "flat").lower()
                verdict = str(signal.get("signal_verdict") or "unknown").lower()
                agent = str(signal.get("agent_name") or default_agent)
                playbook = str(signal.get("playbook_name") or "unknown")
                experiment_type = "paper_candidate" if verdict == "candidate" else ("watch" if verdict == "watch" else "abstention")
                locked_payload = {
                    "schema": SCHEMA,
                    "run_id": run_id,
                    "source_run_timestamp": run_ts,
                    "agent_name": agent,
                    "ticker": ticker,
                    "playbook_name": playbook,
                    "experiment_type": experiment_type,
                    "trade_direction": direction,
                    "signal_verdict": verdict,
                    "signal_score": signal.get("signal_score"),
                    "confidence_label": signal.get("confidence"),
                    "confidence_probability": signal.get("confidence_probability"),
                    "horizon_days": int(horizon_days),
                    "baseline_price": source.get("share_price"),
                    "sector_id": classification["sector_id"],
                    "industry_id": classification["industry_id"],
                    "classification_status": classification["classification_status"],
                    "catalyst_category_id": source.get("catalyst_category_id"),
                    "catalyst_trade_bias": source.get("catalyst_trade_bias"),
                    "risk_level": signal.get("risk_level") or source.get("risk_level"),
                    "signal_payload": signal,
                }
                input_hash = sha256(locked_payload)
                experiment_id = f"exp_{input_hash[:24]}"
                previous = conn.execute(
                    """
                    SELECT experiment_id, input_sha256 FROM trader_experiments
                    WHERE run_id = ? AND agent_name = ? AND ticker = ?
                      AND playbook_name = ?
                    """,
                    (run_id, agent, ticker, playbook),
                ).fetchone()
                if previous:
                    if previous["input_sha256"] != input_hash:
                        raise ExperimentConflictError(
                            f"Locked experiment changed: {run_id}/{agent}/{ticker}/{playbook}/{direction}"
                        )
                    existing += 1
                    continue
                conn.execute(
                    """
                    INSERT INTO trader_experiments (
                        experiment_id, run_id, source_run_timestamp, recorded_at, agent_name,
                        ticker, playbook_name, experiment_type, trade_direction, signal_verdict,
                        signal_score, confidence_label, confidence_probability, horizon_days,
                        baseline_price, sector_id, industry_id, catalyst_category_id,
                        catalyst_trade_bias, risk_level, status, input_sha256, payload_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        experiment_id, run_id, run_ts, datetime.now(timezone.utc).isoformat(), agent,
                        ticker, playbook, experiment_type, direction, verdict, signal.get("signal_score"),
                        signal.get("confidence"), signal.get("confidence_probability"), horizon_days,
                        source.get("share_price"), classification["sector_id"], classification["industry_id"],
                        source.get("catalyst_category_id"), source.get("catalyst_trade_bias"),
                        signal.get("risk_level") or source.get("risk_level"), "locked", input_hash,
                        canonical_json(locked_payload),
                    ),
                )
                inserted += 1
        conn.commit()
    return {"schema": SCHEMA, "run_id": run_id, "inserted": inserted, "existing": existing}


def render_markdown(summary: dict[str, Any]) -> str:
    return "\n".join([
        "# Immutable Trader Experiment Registry",
        "",
        f"- Run ID: {summary['run_id']}",
        f"- New locked experiments: {summary['inserted']}",
        f"- Identical existing experiments: {summary['existing']}",
        "- Mutation policy: an existing experiment cannot be silently rewritten.",
    ])


def main() -> int:
    parser = argparse.ArgumentParser(description="Lock strategy predictions before outcomes are known.")
    parser.add_argument("--db", default=str(market_intel_db.DEFAULT_DB_PATH))
    parser.add_argument("--giannis", default="output/giannis_rule_based_latest.json")
    parser.add_argument("--strategy", action="append", required=True)
    parser.add_argument("--horizon-days", type=int, default=20)
    parser.add_argument("--out-prefix", default="output/experiment_registry")
    args = parser.parse_args()
    giannis = load_payload(Path(args.giannis))
    strategies = [load_payload(Path(path)) for path in args.strategy]
    summary = record_experiments(Path(args.db), giannis, strategies, horizon_days=args.horizon_days)
    prefix = Path(args.out_prefix)
    prefix.parent.mkdir(parents=True, exist_ok=True)
    Path(f"{prefix}_latest.json").write_text(canonical_json(summary), encoding="utf-8")
    Path(f"{prefix}_latest.md").write_text(render_markdown(summary), encoding="utf-8")
    print(f"[EXPERIMENTS] inserted={summary['inserted']} existing={summary['existing']} run_id={summary['run_id']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
