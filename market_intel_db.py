from __future__ import annotations

import argparse
import json
import math
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from common import SQLITE_BUSY_TIMEOUT_SECONDS, enable_wal


DEFAULT_DB_PATH = Path("data/market_intel.db")
TIMESTAMPED_JSON_RE = re.compile(r".*_\d{8}_\d{6}\.json$")


def as_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def load_json(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"[SKIP] {path}: {exc}")
        return None
    if not isinstance(data, dict):
        print(f"[SKIP] {path}: root JSON is not an object")
        return None
    return data


def connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, timeout=SQLITE_BUSY_TIMEOUT_SECONDS)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    enable_wal(conn)
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_file TEXT NOT NULL UNIQUE,
            kind TEXT NOT NULL,
            run_timestamp TEXT,
            ingested_at TEXT NOT NULL,
            payload_json TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS scan_results (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id INTEGER NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
            ticker TEXT NOT NULL,
            company_name TEXT,
            sector TEXT,
            industry TEXT,
            close REAL,
            market_cap REAL,
            composite_score REAL,
            fundamental_score REAL,
            ma_score REAL,
            rsi_14 REAL,
            macd_histogram REAL,
            atr_14_pct REAL,
            volume_ratio_20 REAL,
            return_20d_pct REAL,
            return_60d_pct REAL,
            price_position_20d_pct REAL,
            price_position_60d_pct REAL,
            distance_from_20d_low_pct REAL,
            distance_from_60d_low_pct REAL,
            volatility_20d_pct REAL,
            market_data_provider TEXT,
            payload_json TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_scan_results_ticker
            ON scan_results(ticker);
        CREATE INDEX IF NOT EXISTS idx_scan_results_score
            ON scan_results(composite_score DESC);

        CREATE TABLE IF NOT EXISTS shortlists (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id INTEGER NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
            rank INTEGER,
            ticker TEXT NOT NULL,
            company TEXT,
            share_price REAL,
            market_cap REAL,
            composite_score REAL,
            rsi_14 REAL,
            macd_histogram REAL,
            atr_14_pct REAL,
            volume_ratio_20 REAL,
            return_20d_pct REAL,
            return_60d_pct REAL,
            price_position_20d_pct REAL,
            price_position_60d_pct REAL,
            distance_from_20d_low_pct REAL,
            distance_from_60d_low_pct REAL,
            volatility_20d_pct REAL,
            catalyst_bucket TEXT,
            main_catalyst TEXT,
            catalyst_date TEXT,
            risk_level TEXT,
            verdict TEXT,
            confidence TEXT,
            payload_json TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_shortlists_ticker
            ON shortlists(ticker);
        CREATE INDEX IF NOT EXISTS idx_shortlists_rank
            ON shortlists(rank);

        CREATE TABLE IF NOT EXISTS macro_observations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id INTEGER NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
            series_id TEXT NOT NULL,
            observation_date TEXT,
            value REAL,
            as_of TEXT,
            payload_json TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_macro_series
            ON macro_observations(series_id, observation_date);

        CREATE TABLE IF NOT EXISTS candidate_setups (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_file TEXT NOT NULL,
            source_run_timestamp TEXT NOT NULL,
            ticker TEXT NOT NULL,
            company TEXT,
            observed_at TEXT NOT NULL,
            baseline_price REAL,
            giannis_score REAL,
            giannis_tier TEXT,
            risk_level TEXT,
            catalyst_bucket TEXT,
            rank INTEGER,
            payload_json TEXT NOT NULL,
            UNIQUE(source_run_timestamp, ticker)
        );

        CREATE TABLE IF NOT EXISTS price_outcomes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            setup_id INTEGER NOT NULL REFERENCES candidate_setups(id) ON DELETE CASCADE,
            horizon_days INTEGER NOT NULL,
            observed_at TEXT NOT NULL,
            price REAL,
            return_pct REAL,
            payload_json TEXT NOT NULL,
            UNIQUE(setup_id, horizon_days)
        );

        CREATE TABLE IF NOT EXISTS petros_decisions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_run_timestamp TEXT NOT NULL,
            ticker TEXT NOT NULL,
            company TEXT,
            created_at TEXT NOT NULL,
            petros_score REAL,
            petros_verdict TEXT,
            confidence TEXT,
            rationale_json TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            UNIQUE(source_run_timestamp, ticker)
        );

        CREATE INDEX IF NOT EXISTS idx_petros_decisions_ticker
            ON petros_decisions(ticker);

        CREATE TABLE IF NOT EXISTS petros_feedback (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticker TEXT NOT NULL,
            created_at TEXT NOT NULL,
            feedback_verdict TEXT NOT NULL,
            notes TEXT,
            source_run_timestamp TEXT,
            payload_json TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_petros_feedback_ticker
            ON petros_feedback(ticker);

        CREATE TABLE IF NOT EXISTS strategy_signals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            agent_name TEXT NOT NULL,
            source_run_timestamp TEXT NOT NULL,
            ticker TEXT NOT NULL,
            playbook_name TEXT NOT NULL,
            created_at TEXT NOT NULL,
            signal_score REAL,
            signal_verdict TEXT,
            confidence TEXT,
            rationale_json TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            UNIQUE(source_run_timestamp, ticker, agent_name, playbook_name)
        );

        CREATE INDEX IF NOT EXISTS idx_strategy_signals_ticker
            ON strategy_signals(ticker);
        CREATE INDEX IF NOT EXISTS idx_strategy_signals_agent_run
            ON strategy_signals(agent_name, source_run_timestamp);
        """
    )
    ensure_columns(
        conn,
        "scan_results",
        {
            "rsi_14": "REAL",
            "macd_histogram": "REAL",
            "atr_14_pct": "REAL",
            "volume_ratio_20": "REAL",
            "return_20d_pct": "REAL",
            "return_60d_pct": "REAL",
            "price_position_20d_pct": "REAL",
            "price_position_60d_pct": "REAL",
            "distance_from_20d_low_pct": "REAL",
            "distance_from_60d_low_pct": "REAL",
            "volatility_20d_pct": "REAL",
        },
    )
    ensure_columns(
        conn,
        "shortlists",
        {
            "sector": "TEXT",
            "industry": "TEXT",
            "sector_id": "TEXT",
            "industry_id": "TEXT",
            "classification_status": "TEXT",
            "rsi_14": "REAL",
            "macd_histogram": "REAL",
            "atr_14_pct": "REAL",
            "volume_ratio_20": "REAL",
            "return_20d_pct": "REAL",
            "return_60d_pct": "REAL",
            "price_position_20d_pct": "REAL",
            "price_position_60d_pct": "REAL",
            "distance_from_20d_low_pct": "REAL",
            "distance_from_60d_low_pct": "REAL",
            "volatility_20d_pct": "REAL",
        },
    )
    ensure_columns(
        conn,
        "candidate_setups",
        {
            "run_id": "TEXT",
            "sector": "TEXT",
            "industry": "TEXT",
            "sector_id": "TEXT",
            "industry_id": "TEXT",
            "classification_status": "TEXT",
            "catalyst_category_id": "TEXT",
            "catalyst_direction": "TEXT",
        },
    )
    ensure_columns(
        conn,
        "strategy_signals",
        {
            "run_id": "TEXT",
            "trade_direction": "TEXT",
            "sector_id": "TEXT",
            "industry_id": "TEXT",
            "catalyst_category_id": "TEXT",
            "confidence_probability": "REAL",
        },
    )
    conn.commit()


def ensure_columns(conn: sqlite3.Connection, table: str, columns: dict[str, str]) -> None:
    existing = {str(row["name"]) for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    for name, column_type in columns.items():
        if name not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {column_type}")


def insert_run(conn: sqlite3.Connection, source_file: Path, kind: str, payload: dict[str, Any]) -> int | None:
    ingested_at = datetime.now(timezone.utc).isoformat()
    try:
        cursor = conn.execute(
            """
            INSERT INTO runs (source_file, kind, run_timestamp, ingested_at, payload_json)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                str(source_file),
                kind,
                payload.get("run_timestamp"),
                ingested_at,
                as_json(payload),
            ),
        )
    except sqlite3.IntegrityError:
        return None
    return int(cursor.lastrowid)


def to_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(numeric):
        return None
    return numeric


def ingest_scan(conn: sqlite3.Connection, path: Path, payload: dict[str, Any]) -> int:
    run_id = insert_run(conn, path, "scan", payload)
    if run_id is None:
        return 0

    rows = payload.get("results", [])
    if not isinstance(rows, list):
        rows = []

    for row in rows:
        if not isinstance(row, dict):
            continue
        ticker = str(row.get("symbol") or row.get("ticker") or "").upper().strip()
        if not ticker:
            continue
        conn.execute(
            """
            INSERT INTO scan_results (
                run_id, ticker, company_name, sector, industry, close, market_cap,
                composite_score, fundamental_score, ma_score, rsi_14, macd_histogram,
                atr_14_pct, volume_ratio_20, return_20d_pct, return_60d_pct,
                price_position_20d_pct, price_position_60d_pct,
                distance_from_20d_low_pct, distance_from_60d_low_pct,
                volatility_20d_pct,
                market_data_provider, payload_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                ticker,
                row.get("company_name"),
                row.get("sector"),
                row.get("industry"),
                to_float(row.get("close")),
                to_float(row.get("market_cap")),
                to_float(row.get("composite_score")),
                to_float(row.get("fundamental_score")),
                to_float(row.get("ma_score")),
                to_float(row.get("rsi_14")),
                to_float(row.get("macd_histogram")),
                to_float(row.get("atr_14_pct")),
                to_float(row.get("volume_ratio_20")),
                to_float(row.get("return_20d_pct")),
                to_float(row.get("return_60d_pct")),
                to_float(row.get("price_position_20d_pct")),
                to_float(row.get("price_position_60d_pct")),
                to_float(row.get("distance_from_20d_low_pct")),
                to_float(row.get("distance_from_60d_low_pct")),
                to_float(row.get("volatility_20d_pct")),
                row.get("market_data_provider"),
                as_json(row),
            ),
        )

    ingest_macro(conn, run_id, payload)
    return len(rows)


def ingest_shortlist(conn: sqlite3.Connection, path: Path, payload: dict[str, Any]) -> int:
    run_id = insert_run(conn, path, "shortlist", payload)
    if run_id is None:
        return 0

    rows = payload.get("shortlist", [])
    if not isinstance(rows, list):
        rows = []

    for row in rows:
        if not isinstance(row, dict):
            continue
        ticker = str(row.get("ticker") or row.get("symbol") or "").upper().strip()
        if not ticker:
            continue
        conn.execute(
            """
            INSERT INTO shortlists (
                run_id, rank, ticker, company, sector, industry, sector_id, industry_id,
                classification_status, share_price, market_cap, composite_score,
                rsi_14, macd_histogram, atr_14_pct, volume_ratio_20, return_20d_pct,
                return_60d_pct, price_position_20d_pct, price_position_60d_pct,
                distance_from_20d_low_pct, distance_from_60d_low_pct,
                volatility_20d_pct, catalyst_bucket, main_catalyst, catalyst_date,
                risk_level, verdict, confidence, payload_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                row.get("rank"),
                ticker,
                row.get("company") or row.get("company_name"),
                row.get("sector"),
                row.get("industry"),
                row.get("sector_id"),
                row.get("industry_id"),
                row.get("classification_status"),
                to_float(row.get("share_price") or row.get("close")),
                to_float(row.get("market_cap")),
                to_float(row.get("composite_score")),
                to_float(row.get("rsi_14")),
                to_float(row.get("macd_histogram")),
                to_float(row.get("atr_14_pct")),
                to_float(row.get("volume_ratio_20")),
                to_float(row.get("return_20d_pct")),
                to_float(row.get("return_60d_pct")),
                to_float(row.get("price_position_20d_pct")),
                to_float(row.get("price_position_60d_pct")),
                to_float(row.get("distance_from_20d_low_pct")),
                to_float(row.get("distance_from_60d_low_pct")),
                to_float(row.get("volatility_20d_pct")),
                row.get("catalyst_bucket"),
                row.get("main_catalyst"),
                row.get("catalyst_date"),
                row.get("risk_level"),
                row.get("verdict"),
                row.get("confidence"),
                as_json(row),
            ),
        )

    ingest_macro(conn, run_id, payload)
    return len(rows)


def ingest_macro(conn: sqlite3.Connection, run_id: int, payload: dict[str, Any]) -> None:
    macro = payload.get("macro_context")
    if not isinstance(macro, dict):
        return
    series = macro.get("series")
    if not isinstance(series, dict):
        return
    for series_id, observation in series.items():
        if not isinstance(observation, dict):
            continue
        conn.execute(
            """
            INSERT INTO macro_observations (
                run_id, series_id, observation_date, value, as_of, payload_json
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                str(series_id),
                observation.get("date"),
                to_float(observation.get("value")),
                macro.get("as_of"),
                as_json(observation),
            ),
        )


def classify_payload(path: Path, payload: dict[str, Any]) -> str | None:
    if isinstance(payload.get("results"), list):
        return "scan"
    if isinstance(payload.get("shortlist"), list):
        return "shortlist"
    name = path.name.lower()
    if name.startswith("scan_"):
        return "scan"
    if name.startswith("babis_shortlist_"):
        return "shortlist"
    return None


def iter_candidate_json(output_dir: Path) -> list[Path]:
    paths = []
    for path in output_dir.glob("*.json"):
        name = path.name.lower()
        if "delta" in name or "checkpoint" in name or "latest" in name:
            continue
        if TIMESTAMPED_JSON_RE.match(name):
            paths.append(path)
    return sorted(paths)


def ingest_outputs(db_path: Path, output_dir: Path) -> None:
    with connect(db_path) as conn:
        init_db(conn)
        total_files = 0
        total_rows = 0
        for path in iter_candidate_json(output_dir):
            payload = load_json(path)
            if payload is None:
                continue
            kind = classify_payload(path, payload)
            if kind == "scan":
                rows = ingest_scan(conn, path, payload)
            elif kind == "shortlist":
                rows = ingest_shortlist(conn, path, payload)
            else:
                continue
            if rows:
                total_files += 1
                total_rows += rows
                print(f"[INGEST] {path} -> {kind} rows={rows}")
            else:
                print(f"[SKIP] {path}: already ingested or empty")
        conn.commit()
    print(f"[DONE] files={total_files} rows={total_rows} db={db_path}")


def print_summary(db_path: Path) -> None:
    with connect(db_path) as conn:
        init_db(conn)
        run_count = conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0]
        scan_count = conn.execute("SELECT COUNT(*) FROM scan_results").fetchone()[0]
        shortlist_count = conn.execute("SELECT COUNT(*) FROM shortlists").fetchone()[0]
        macro_count = conn.execute("SELECT COUNT(*) FROM macro_observations").fetchone()[0]
        setup_count = conn.execute("SELECT COUNT(*) FROM candidate_setups").fetchone()[0]
        outcome_count = conn.execute("SELECT COUNT(*) FROM price_outcomes").fetchone()[0]
        petros_count = conn.execute("SELECT COUNT(*) FROM petros_decisions").fetchone()[0]
        petros_feedback_count = conn.execute("SELECT COUNT(*) FROM petros_feedback").fetchone()[0]
        strategy_signal_count = conn.execute("SELECT COUNT(*) FROM strategy_signals").fetchone()[0]
        print(f"DB: {db_path}")
        print(f"Runs: {run_count}")
        print(f"Scan rows: {scan_count}")
        print(f"Shortlist rows: {shortlist_count}")
        print(f"Macro observations: {macro_count}")
        print(f"Candidate setups: {setup_count}")
        print(f"Price outcomes: {outcome_count}")
        print(f"Petros decisions: {petros_count}")
        print(f"Petros feedback: {petros_feedback_count}")
        print(f"Strategy signals: {strategy_signal_count}")


def print_top(db_path: Path, limit: int) -> None:
    with connect(db_path) as conn:
        init_db(conn)
        rows = conn.execute(
            """
            SELECT ticker, company, share_price, market_cap, composite_score, catalyst_bucket, risk_level
            FROM shortlists
            WHERE composite_score IS NOT NULL
            ORDER BY composite_score DESC, rank ASC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        if not rows:
            rows = conn.execute(
                """
                SELECT ticker, company_name AS company, close AS share_price, market_cap,
                       composite_score, NULL AS catalyst_bucket, NULL AS risk_level
                FROM scan_results
                WHERE composite_score IS NOT NULL
                ORDER BY composite_score DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        for row in rows:
            price = "" if row["share_price"] is None else f"${row['share_price']:.2f}"
            score = "" if row["composite_score"] is None else f"{row['composite_score']:.2f}"
            print(
                f"{row['ticker']:8} score={score:>7} price={price:>8} "
                f"risk={row['risk_level'] or '-':<8} {row['company'] or ''}"
            )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Local SQLite store for market intelligence outputs.")
    parser.add_argument("--db", default=str(DEFAULT_DB_PATH), help="SQLite database path.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("init", help="Create the SQLite schema.")

    ingest_parser = subparsers.add_parser("ingest", help="Import output JSON files into SQLite.")
    ingest_parser.add_argument("--output-dir", default="output", help="Directory with scanner JSON outputs.")

    subparsers.add_parser("summary", help="Print database row counts.")
    registry_parser = subparsers.add_parser("registry", help="Research registry commands (separate database).")
    registry_parser.add_argument("registry_args", nargs=argparse.REMAINDER)

    top_parser = subparsers.add_parser("top", help="Show top stored candidates.")
    top_parser.add_argument("--limit", type=int, default=20)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    if args.command == "registry":
        from research_registry.core import main as registry_main
        return registry_main(args.registry_args)
    db_path = Path(args.db)

    if args.command == "init":
        with connect(db_path) as conn:
            init_db(conn)
        print(f"[OK] initialized {db_path}")
        return 0
    if args.command == "ingest":
        ingest_outputs(db_path, Path(args.output_dir))
        return 0
    if args.command == "summary":
        print_summary(db_path)
        return 0
    if args.command == "top":
        print_top(db_path, args.limit)
        return 0
    parser.error("unknown command")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
