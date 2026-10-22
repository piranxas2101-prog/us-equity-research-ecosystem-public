from __future__ import annotations

import argparse
import json
import sqlite3
from contextlib import closing
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable
from zoneinfo import ZoneInfo

import yfinance as yf

import market_intel_db
from common import load_json


DEFAULT_HORIZONS = [1, 3, 5, 10, 20]
MARKET_TIMEZONE = ZoneInfo("America/New_York")
SESSION_BENCHMARK = "SPY"
SESSION_FINAL_HOUR = 18  # New York hour after which today's daily bar is taken as final


def as_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def to_float(value: Any) -> float | None:
    return market_intel_db.to_float(value)


def parse_dt(value: str | None) -> datetime:
    if not value:
        return datetime.now(timezone.utc)
    cleaned = value.replace("Z", "+00:00")
    parsed = datetime.fromisoformat(cleaned)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def init_schema(conn: sqlite3.Connection) -> None:
    market_intel_db.init_db(conn)
    conn.executescript(
        """
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

        CREATE INDEX IF NOT EXISTS idx_candidate_setups_ticker
            ON candidate_setups(ticker);
        CREATE INDEX IF NOT EXISTS idx_candidate_setups_tier
            ON candidate_setups(giannis_tier);

        CREATE TABLE IF NOT EXISTS price_outcomes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            setup_id INTEGER NOT NULL REFERENCES candidate_setups(id) ON DELETE CASCADE,
            horizon_days INTEGER NOT NULL,
            target_session_date TEXT,
            price_session_date TEXT,
            price_source TEXT,
            price_adjustment TEXT,
            observed_at TEXT NOT NULL,
            price REAL,
            return_pct REAL,
            payload_json TEXT NOT NULL,
            UNIQUE(setup_id, horizon_days)
        );

        CREATE INDEX IF NOT EXISTS idx_price_outcomes_horizon
            ON price_outcomes(horizon_days);
        """
    )
    existing_columns = {
        str(row[1]) for row in conn.execute("PRAGMA table_info(price_outcomes)").fetchall()
    }
    migrations = {
        "target_session_date": "TEXT",
        "price_session_date": "TEXT",
        "price_source": "TEXT",
        "price_adjustment": "TEXT",
    }
    for column, column_type in migrations.items():
        if column not in existing_columns:
            conn.execute(f"ALTER TABLE price_outcomes ADD COLUMN {column} {column_type}")
    conn.commit()


def record_setups(db_path: Path, giannis_path: Path) -> None:
    payload = load_json(giannis_path)
    run_ts = str(payload.get("run_timestamp") or datetime.now(timezone.utc).isoformat())
    rows = payload.get("results", [])
    if not isinstance(rows, list):
        rows = []

    inserted = 0
    skipped = 0
    with closing(market_intel_db.connect(db_path)) as conn:
        init_schema(conn)
        for row in rows:
            if not isinstance(row, dict):
                continue
            ticker = str(row.get("ticker") or "").upper().strip()
            if not ticker:
                continue
            try:
                conn.execute(
                    """
                    INSERT INTO candidate_setups (
                        source_file, source_run_timestamp, ticker, company, observed_at,
                        baseline_price, giannis_score, giannis_tier, risk_level,
                        catalyst_bucket, rank, run_id, sector, industry, sector_id,
                        industry_id, classification_status, catalyst_category_id,
                        catalyst_direction, payload_json
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(giannis_path),
                        run_ts,
                        ticker,
                        row.get("company"),
                        datetime.now(timezone.utc).isoformat(),
                        to_float(row.get("share_price")),
                        to_float(row.get("score_final")),
                        row.get("giannis_tier"),
                        row.get("risk_level"),
                        row.get("catalyst_bucket"),
                        row.get("rank"),
                        payload.get("run_id"),
                        row.get("sector"),
                        row.get("industry"),
                        row.get("sector_id"),
                        row.get("industry_id"),
                        row.get("classification_status"),
                        row.get("catalyst_category_id"),
                        row.get("catalyst_direction"),
                        as_json(row),
                    ),
                )
                inserted += 1
            except sqlite3.IntegrityError:
                skipped += 1
        conn.commit()
    print(f"[OUTCOMES] recorded setups inserted={inserted} skipped={skipped} db={db_path}")


def completed_through(now: datetime) -> date:
    """Last New York date whose daily bar is final: today after SESSION_FINAL_HOUR, otherwise yesterday.

    The nightly run starts around 18:30-22:00 New York time, after the close. Counting only dates before today
    delayed every outcome by one run (a 1-day label for Friday's setup waited for Tuesday's run).
    """
    local = now.astimezone(MARKET_TIMEZONE)
    return local.date() if local.hour >= SESSION_FINAL_HOUR else local.date() - timedelta(days=1)


def completed_session_dates_after(
    baseline_dt: datetime,
    now: datetime,
    benchmark: str = SESSION_BENCHMARK,
) -> list[date] | None:
    """Return completed US trading dates after the setup's New York calendar date.

    Benchmark bars provide the actual observed session calendar, including holidays. Today's session counts
    once its bar is final (see ``completed_through``). ``None`` means the calendar could not be read, which is
    not the same as "no session yet".
    """

    baseline_market_date = baseline_dt.astimezone(MARKET_TIMEZONE).date()
    last_final = completed_through(now)
    if last_final <= baseline_market_date:
        return []
    try:
        history = yf.Ticker(benchmark).history(
            start=baseline_market_date.isoformat(),
            end=(last_final + timedelta(days=1)).isoformat(),
            interval="1d",
            auto_adjust=False,
            actions=False,
        )
    except Exception:
        return None
    if history.empty:
        return None
    sessions: list[date] = []
    for index_value in history.index:
        session_date = index_value.date()
        if baseline_market_date < session_date <= last_final and session_date not in sessions:
            sessions.append(session_date)
    return sorted(sessions)


def close_on_session(ticker: str, session_date: date) -> float | None:
    try:
        history = yf.Ticker(ticker).history(
            start=session_date.isoformat(),
            end=(session_date + timedelta(days=1)).isoformat(),
            interval="1d",
            auto_adjust=False,
            actions=False,
        )
    except Exception:
        return None
    if history.empty or "Close" not in history.columns:
        return None
    close = history["Close"].dropna()
    if close.empty:
        return None
    return to_float(close.iloc[0])


def refresh_outcomes(
    db_path: Path,
    horizons: list[int],
    *,
    now: datetime | None = None,
    sessions_after_fn: Callable[[datetime, datetime], list[date] | None] = completed_session_dates_after,
    close_on_session_fn: Callable[[str, date], float | None] = close_on_session,
) -> None:
    now = now or datetime.now(timezone.utc)
    inserted = 0
    not_due = 0
    missing_price = 0
    calendar_unavailable = 0
    # Every setup from one run shares a baseline timestamp, so the trading-session
    # calendar (a benchmark download) is fetched once per run instead of once per
    # setup; prices are fetched once per (ticker, session). Results are unchanged.
    sessions_cache: dict[datetime, list[date] | None] = {}
    price_cache: dict[tuple[str, date], float | None] = {}
    with closing(market_intel_db.connect(db_path)) as conn:
        init_schema(conn)
        existing = {(row[0], row[1]) for row in conn.execute("SELECT setup_id, horizon_days FROM price_outcomes")}
        setups = conn.execute(
            """
            SELECT id, ticker, source_run_timestamp, baseline_price
            FROM candidate_setups
            WHERE baseline_price IS NOT NULL
            ORDER BY id
            """
        ).fetchall()
        for setup in setups:
            missing = [h for h in horizons if (setup["id"], h) not in existing and h > 0]
            if not missing:
                continue
            baseline_dt = parse_dt(setup["source_run_timestamp"])
            if baseline_dt not in sessions_cache:
                sessions_cache[baseline_dt] = sessions_after_fn(baseline_dt, now)
            completed_sessions = sessions_cache[baseline_dt]
            if completed_sessions is None:
                calendar_unavailable += len(missing)
                continue
            for horizon in missing:
                if len(completed_sessions) < horizon:
                    not_due += 1
                    continue
                target_session_date = completed_sessions[horizon - 1]
                price_key = (str(setup["ticker"]), target_session_date)
                if price_key not in price_cache:
                    price_cache[price_key] = close_on_session_fn(*price_key)
                price = price_cache[price_key]
                if price is None or setup["baseline_price"] in (None, 0):
                    missing_price += 1
                    continue
                ret = ((price / float(setup["baseline_price"])) - 1.0) * 100.0
                outcome_payload = {
                    "ticker": setup["ticker"],
                    "baseline_price": setup["baseline_price"],
                    "price": price,
                    "horizon_days": horizon,
                    "target_session_date": target_session_date.isoformat(),
                    "price_session_date": target_session_date.isoformat(),
                    "price_source": "yfinance",
                    "price_adjustment": "raw_close",
                    "return_pct": ret,
                }
                conn.execute(
                    """
                    INSERT INTO price_outcomes (
                        setup_id, horizon_days, target_session_date, price_session_date,
                        price_source, price_adjustment, observed_at, price, return_pct, payload_json
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        setup["id"],
                        horizon,
                        target_session_date.isoformat(),
                        target_session_date.isoformat(),
                        "yfinance",
                        "raw_close",
                        now.isoformat(),
                        price,
                        ret,
                        as_json(outcome_payload),
                    ),
                )
                existing.add((setup["id"], horizon))
                inserted += 1
        conn.commit()
    print(
        f"[OUTCOMES] refreshed inserted={inserted} not_due={not_due} "
        f"missing_price={missing_price} calendar_unavailable={calendar_unavailable} db={db_path}"
    )


def print_summary(db_path: Path) -> None:
    with closing(market_intel_db.connect(db_path)) as conn:
        init_schema(conn)
        setup_count = conn.execute("SELECT COUNT(*) FROM candidate_setups").fetchone()[0]
        outcome_count = conn.execute("SELECT COUNT(*) FROM price_outcomes").fetchone()[0]
        print(f"DB: {db_path}")
        print(f"Candidate setups: {setup_count}")
        print(f"Price outcomes: {outcome_count}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Track watchlist outcomes for future ML training.")
    parser.add_argument("--db", default=str(market_intel_db.DEFAULT_DB_PATH))
    subparsers = parser.add_subparsers(dest="command", required=True)

    record = subparsers.add_parser("record", help="Record baseline setups from Giannis report.")
    record.add_argument("--giannis", default="output/giannis_rule_based_latest.json")

    refresh = subparsers.add_parser("refresh", help="Refresh due future returns.")
    refresh.add_argument("--horizons", nargs="+", type=int, default=DEFAULT_HORIZONS)

    subparsers.add_parser("summary", help="Print outcome tracking counts.")
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    db_path = Path(args.db)
    if args.command == "record":
        record_setups(db_path, Path(args.giannis))
        return 0
    if args.command == "refresh":
        refresh_outcomes(db_path, args.horizons)
        return 0
    if args.command == "summary":
        print_summary(db_path)
        return 0
    parser.error("unknown command")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
