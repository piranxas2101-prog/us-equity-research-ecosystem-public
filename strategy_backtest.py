from __future__ import annotations

import argparse
import csv
import json
from datetime import datetime, timezone
from pathlib import Path
from statistics import median
from typing import Any

import market_intel_db
import strategy_scorecards


DEFAULT_DB_PATH = market_intel_db.DEFAULT_DB_PATH
DEFAULT_HORIZONS = [1, 3, 5, 10, 20]
DEFAULT_VERDICTS = ["candidate", "watch"]
SWING_PRIORITY_HORIZONS = [20, 10, 5, 3, 1]


def as_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def to_float(value: Any) -> float | None:
    return market_intel_db.to_float(value)


def load_trade_rows(
    db_path: Path,
    horizons: list[int],
    verdicts: list[str],
) -> list[dict[str, Any]]:
    placeholders_h = ",".join("?" for _ in horizons)
    placeholders_v = ",".join("?" for _ in verdicts)
    query = f"""
        SELECT
            ss.agent_name,
            ss.playbook_name,
            ss.signal_verdict,
            ss.signal_score,
            ss.confidence,
            ss.source_run_timestamp,
            ss.ticker,
            cs.company,
            cs.baseline_price,
            cs.giannis_score,
            cs.giannis_tier,
            cs.risk_level,
            po.horizon_days,
            po.price,
            po.return_pct
        FROM strategy_signals ss
        INNER JOIN candidate_setups cs
            ON cs.source_run_timestamp = ss.source_run_timestamp
           AND cs.ticker = ss.ticker
        INNER JOIN price_outcomes po
            ON po.setup_id = cs.id
        WHERE ss.signal_verdict IN ({placeholders_v})
          AND po.horizon_days IN ({placeholders_h})
        ORDER BY ss.agent_name, ss.playbook_name, ss.signal_verdict, po.horizon_days, ss.signal_score DESC, ss.id DESC
    """
    with market_intel_db.connect(db_path) as conn:
        market_intel_db.init_db(conn)
        rows = conn.execute(query, (*verdicts, *horizons)).fetchall()
    cleaned: list[dict[str, Any]] = []
    for row in rows:
        cleaned.append(
            {
                "agent_name": row["agent_name"],
                "playbook_name": row["playbook_name"],
                "signal_verdict": row["signal_verdict"],
                "signal_score": to_float(row["signal_score"]),
                "confidence": row["confidence"],
                "source_run_timestamp": row["source_run_timestamp"],
                "ticker": row["ticker"],
                "company": row["company"],
                "baseline_price": to_float(row["baseline_price"]),
                "giannis_score": to_float(row["giannis_score"]),
                "giannis_tier": row["giannis_tier"],
                "risk_level": row["risk_level"],
                "horizon_days": int(row["horizon_days"]),
                "price": to_float(row["price"]),
                "return_pct": to_float(row["return_pct"]),
            }
        )
    return cleaned


def load_signal_coverage(
    db_path: Path,
    verdicts: list[str],
) -> list[dict[str, Any]]:
    placeholders_v = ",".join("?" for _ in verdicts)
    query = f"""
        SELECT
            ss.agent_name,
            ss.playbook_name,
            ss.signal_verdict,
            COUNT(*) AS signal_count
        FROM strategy_signals ss
        INNER JOIN candidate_setups cs
            ON cs.source_run_timestamp = ss.source_run_timestamp
           AND cs.ticker = ss.ticker
        WHERE ss.signal_verdict IN ({placeholders_v})
        GROUP BY ss.agent_name, ss.playbook_name, ss.signal_verdict
        ORDER BY signal_count DESC, ss.agent_name, ss.playbook_name, ss.signal_verdict
    """
    with market_intel_db.connect(db_path) as conn:
        market_intel_db.init_db(conn)
        rows = conn.execute(query, tuple(verdicts)).fetchall()
    return [
        {
            "agent_name": row["agent_name"],
            "playbook_name": row["playbook_name"],
            "signal_verdict": row["signal_verdict"],
            "signal_count": int(row["signal_count"]),
        }
        for row in rows
    ]


def build_scorecards(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str, str, int], list[dict[str, Any]]] = {}
    for row in rows:
        key = (
            str(row.get("agent_name") or ""),
            str(row.get("playbook_name") or ""),
            str(row.get("signal_verdict") or ""),
            int(row.get("horizon_days") or 0),
        )
        grouped.setdefault(key, []).append(row)

    scorecards: list[dict[str, Any]] = []
    for key, bucket in grouped.items():
        returns = [to_float(item.get("return_pct")) for item in bucket]
        clean_returns = [value for value in returns if value is not None]
        if not clean_returns:
            continue
        gains = [value for value in clean_returns if value > 0]
        losses = [value for value in clean_returns if value < 0]
        avg_return = sum(clean_returns) / len(clean_returns)
        med_return = median(clean_returns)
        win_rate = (len(gains) / len(clean_returns)) * 100.0
        nonnegative_rate = (sum(1 for value in clean_returns if value >= 0) / len(clean_returns)) * 100.0
        avg_gain = sum(gains) / len(gains) if gains else None
        avg_loss = sum(losses) / len(losses) if losses else None
        avg_signal_score = sum(
            value for value in (to_float(item.get("signal_score")) for item in bucket) if value is not None
        ) / max(1, sum(1 for item in bucket if to_float(item.get("signal_score")) is not None))
        avg_giannis_score = sum(
            value for value in (to_float(item.get("giannis_score")) for item in bucket) if value is not None
        ) / max(1, sum(1 for item in bucket if to_float(item.get("giannis_score")) is not None))
        core_or_selective = sum(
            1 for item in bucket if str(item.get("giannis_tier") or "") in {"Core Watchlist", "Selective Watchlist"}
        )
        scorecards.append(
            {
                "agent_name": key[0],
                "playbook_name": key[1],
                "signal_verdict": key[2],
                "horizon_days": key[3],
                "sample_count": len(clean_returns),
                "avg_return_pct": round(avg_return, 4),
                "median_return_pct": round(med_return, 4),
                "win_rate_pct": round(win_rate, 2),
                "nonnegative_rate_pct": round(nonnegative_rate, 2),
                "avg_gain_pct": None if avg_gain is None else round(avg_gain, 4),
                "avg_loss_pct": None if avg_loss is None else round(avg_loss, 4),
                "best_return_pct": round(max(clean_returns), 4),
                "worst_return_pct": round(min(clean_returns), 4),
                "avg_signal_score": round(avg_signal_score, 4),
                "avg_giannis_score": round(avg_giannis_score, 4),
                "core_or_selective_rate_pct": round((core_or_selective / len(bucket)) * 100.0, 2),
            }
        )
    scorecards.sort(
        key=lambda item: (
            item["horizon_days"],
            item["signal_verdict"] == "candidate",
            item["avg_return_pct"],
            item["win_rate_pct"],
        ),
        reverse=True,
    )
    return scorecards


def build_playbook_summary(scorecards: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    for row in scorecards:
        key = (
            str(row.get("agent_name") or ""),
            str(row.get("playbook_name") or ""),
            str(row.get("signal_verdict") or ""),
        )
        grouped.setdefault(key, []).append(row)

    summary_rows: list[dict[str, Any]] = []
    for key, bucket in grouped.items():
        by_horizon = {int(item["horizon_days"]): item for item in bucket}
        primary = None
        for horizon in SWING_PRIORITY_HORIZONS:
            row = by_horizon.get(horizon)
            if row and int(row.get("sample_count") or 0) > 0:
                primary = row
                break
        if primary is None:
            primary = bucket[0]
        summary_rows.append(
            {
                "agent_name": key[0],
                "playbook_name": key[1],
                "signal_verdict": key[2],
                "primary_horizon_days": primary["horizon_days"],
                "sample_count": primary["sample_count"],
                "avg_return_pct": primary["avg_return_pct"],
                "median_return_pct": primary["median_return_pct"],
                "win_rate_pct": primary["win_rate_pct"],
                "avg_signal_score": primary["avg_signal_score"],
                "avg_giannis_score": primary["avg_giannis_score"],
                "core_or_selective_rate_pct": primary["core_or_selective_rate_pct"],
            }
        )
    summary_rows.sort(
        key=lambda item: (
            item["signal_verdict"] == "candidate",
            item["avg_return_pct"],
            item["win_rate_pct"],
            item["sample_count"],
        ),
        reverse=True,
    )
    return summary_rows


def build_payload(
    db_path: Path,
    trade_rows: list[dict[str, Any]],
    signal_coverage: list[dict[str, Any]],
    scorecards: list[dict[str, Any]],
    playbook_summary: list[dict[str, Any]],
    horizons: list[int],
    verdicts: list[str],
) -> dict[str, Any]:
    return {
        "analysis_type": "signal_outcome_scorecard",
        "execution_backtest": False,
        "generated_at": datetime.now(timezone.utc).astimezone().isoformat(),
        "db_path": str(db_path),
        "notes": [
            "This is a signal outcome scorecard based on recorded historical strategy signals and future returns; it is not an execution backtest.",
            "It does not yet model slippage, commissions, intraday drawdown, or max favorable excursion.",
            "Candidate and watch verdicts are tracked separately so we do not confuse hard entries with softer surveillance signals.",
            "Sokratis and the trader-review process may use these results as evaluation context; they never alter a frozen fundamental thesis.",
        ],
        "horizons": horizons,
        "verdicts": verdicts,
        "signal_coverage": signal_coverage,
        "tracked_signal_count": sum(int(item.get("signal_count") or 0) for item in signal_coverage),
        "trade_row_count": len(trade_rows),
        "scorecards": scorecards,
        "playbook_summary": playbook_summary,
    }


def fmt(value: Any) -> str:
    if value is None or value == "":
        return "N/A"
    if isinstance(value, float):
        return f"{value:.2f}"
    return str(value)


def render_markdown(payload: dict[str, Any]) -> str:
    scorecards = payload.get("scorecards", [])
    summary_rows = payload.get("playbook_summary", [])
    signal_coverage = payload.get("signal_coverage", [])
    candidate_rows = [row for row in summary_rows if row.get("signal_verdict") == "candidate"]
    watch_rows = [row for row in summary_rows if row.get("signal_verdict") == "watch"]

    lines = [
        "# Strategy Backtest Report",
        "",
        f"- Generated at: {payload.get('generated_at')}",
        f"- Database: {payload.get('db_path')}",
        f"- Tracked signals: {payload.get('tracked_signal_count')}",
        f"- Simulated rows: {payload.get('trade_row_count')}",
        f"- Horizons: {', '.join(str(item) for item in payload.get('horizons', []))}",
        f"- Verdicts: {', '.join(str(item) for item in payload.get('verdicts', []))}",
        "",
        "## Method Notes",
        "",
    ]
    for note in payload.get("notes", []):
        lines.append(f"- {note}")

    lines.extend(["", "## Signal Coverage", ""])
    if signal_coverage:
        for row in signal_coverage[:12]:
            lines.append(
                f"- {row['agent_name']} / {row['playbook_name']} / {row['signal_verdict']} | "
                f"tracked signals={row['signal_count']}"
            )
    else:
        lines.append("- No tracked strategy signals available yet.")

    lines.extend(["", "## Candidate Scorecard", ""])
    if candidate_rows:
        for row in candidate_rows[:12]:
            lines.append(
                f"- {row['agent_name']} / {row['playbook_name']} | horizon={row['primary_horizon_days']}d | "
                f"samples={row['sample_count']} | avg={fmt(row['avg_return_pct'])}% | "
                f"median={fmt(row['median_return_pct'])}% | win={fmt(row['win_rate_pct'])}%"
            )
    else:
        lines.append("- No candidate backtest rows available yet.")

    lines.extend(["", "## Watch Scorecard", ""])
    if watch_rows:
        for row in watch_rows[:12]:
            lines.append(
                f"- {row['agent_name']} / {row['playbook_name']} | horizon={row['primary_horizon_days']}d | "
                f"samples={row['sample_count']} | avg={fmt(row['avg_return_pct'])}% | "
                f"median={fmt(row['median_return_pct'])}% | win={fmt(row['win_rate_pct'])}%"
            )
    else:
        lines.append("- No watch backtest rows available yet.")

    lines.extend(["", "## Detailed Horizons", ""])
    if scorecards:
        for row in scorecards[:30]:
            lines.append(
                f"- {row['agent_name']} / {row['playbook_name']} / {row['signal_verdict']} | "
                f"{row['horizon_days']}d | samples={row['sample_count']} | avg={fmt(row['avg_return_pct'])}% | "
                f"median={fmt(row['median_return_pct'])}% | win={fmt(row['win_rate_pct'])}% | "
                f"best={fmt(row['best_return_pct'])}% | worst={fmt(row['worst_return_pct'])}%"
            )
    else:
        lines.append("- No detailed backtest rows available yet.")
    return "\n".join(lines)


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    headers = list(rows[0].keys())
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=headers, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def command_build(args: argparse.Namespace) -> None:
    strategy_scorecards.command_build(args)
    return
    db_path = Path(args.db)
    horizons = sorted(set(args.horizons))
    verdicts = [item.lower() for item in args.verdicts]
    trade_rows = load_trade_rows(db_path, horizons, verdicts)
    signal_coverage = load_signal_coverage(db_path, verdicts)
    scorecards = build_scorecards(trade_rows)
    playbook_summary = build_playbook_summary(scorecards)
    payload = build_payload(db_path, trade_rows, signal_coverage, scorecards, playbook_summary, horizons, verdicts)

    out_prefix = Path(args.out_prefix)
    out_json = Path(f"{out_prefix}_latest.json")
    out_md = Path(f"{out_prefix}_latest.md")
    out_csv = Path(f"{out_prefix}_scorecards_latest.csv")
    out_trades = Path(f"{out_prefix}_trades_latest.csv")
    out_json.write_text(as_json(payload), encoding="utf-8")
    out_md.write_text(render_markdown(payload), encoding="utf-8")
    write_csv(out_csv, scorecards)
    write_csv(out_trades, trade_rows)
    print(f"[OUTPUT] JSON: {out_json}")
    print(f"[OUTPUT] MD: {out_md}")
    print(f"[OUTPUT] Scorecards CSV: {out_csv}")
    print(f"[OUTPUT] Trades CSV: {out_trades}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Simple strategy backtest / outcome scorecard engine.")
    parser.add_argument("--db", default=str(DEFAULT_DB_PATH))
    subparsers = parser.add_subparsers(dest="command", required=True)

    build = subparsers.add_parser("build", help="Build strategy scorecards from recorded signals and outcomes.")
    build.add_argument("--out-prefix", default="output/strategy_backtest")
    build.add_argument("--horizons", nargs="+", type=int, default=DEFAULT_HORIZONS)
    build.add_argument("--verdicts", nargs="+", default=DEFAULT_VERDICTS)
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    if args.command == "build":
        command_build(args)
        return 0
    parser.error("unknown command")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
