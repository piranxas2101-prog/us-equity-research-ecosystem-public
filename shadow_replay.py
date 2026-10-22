"""Replay a trader variant on archived runs and compare it with the current rules.

A shadow variant is a JSON file that changes some of a trader's weights,
verdict thresholds or playbook rules. This tool re-runs the trader twice on
every archived run under ``output/runs/<run>/artifacts`` - once with the current
rules (the champion) and once with the variant - and scores both against the
price outcomes already stored in ``data/market_intel.db``.

It never changes a live decision: it reads the database read-only, writes no
signals or experiments, and only produces a report under ``output/``.
Promotion stays manual: if a variant looks better, a person edits the
playbooks or weights in a reviewed commit.

Both sides are replayed with the *current* code, so the comparison is fair
even though the code has evolved since those runs were made.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import sqlite3
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import periklis_strategy_agent as periklis
import strategy_scorecards as scorecards
import yankos_strategy_agent as yankos
from common import load_json

ROOT = Path(__file__).resolve().parent
RUNS_DIR = ROOT / "output" / "runs"
DB_PATH = ROOT / "data" / "market_intel.db"
OUTPUT_DIR = ROOT / "output"
HORIZONS = [1, 3, 5, 10, 20]
VERDICTS = {"candidate", "watch"}
VARIANT_SCHEMA = "ecosystem.shadow_variant.v1"
REPORT_SCHEMA = "ecosystem.shadow_replay_report.v1"
REQUIRED_ARTIFACTS = ("babis_shortlist_latest.json", "giannis_rule_based_latest.json",
                      "fundamental_research_packet_latest.json")
TRADERS = {
    "yankos": {"module": yankos, "playbooks": ROOT / "yankos_playbooks.json"},
    "periklis": {"module": periklis, "playbooks": ROOT / "periklis_playbooks.json"},
}
OVERRIDE_SECTIONS = {"short_case_weights", "verdict_thresholds", "playbook_rules"}


# ---------------------------------------------------------------- variants


def load_variant(path: Path) -> dict[str, Any]:
    variant = load_json(path)
    if variant.get("schema") != VARIANT_SCHEMA:
        raise ValueError(f"{path.name}: schema must be {VARIANT_SCHEMA}")
    if variant.get("trader") not in TRADERS:
        raise ValueError(f"{path.name}: trader must be one of {sorted(TRADERS)} (shadow replay supports these today)")
    if not str(variant.get("variant_id") or "").replace("_", "").replace("-", "").isalnum():
        raise ValueError(f"{path.name}: variant_id must be letters, digits, '-' or '_'")
    unknown = sorted(set(variant.get("overrides") or {}) - OVERRIDE_SECTIONS)
    if unknown:
        raise ValueError(f"{path.name}: unknown override sections {unknown}")
    if not variant.get("overrides"):
        raise ValueError(f"{path.name}: a variant must override something")
    variant["file_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    return variant


def apply_playbook_rules(playbook_payload: dict[str, Any], rule_overrides: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Return a copy of the playbooks with some rule values replaced."""
    payload = copy.deepcopy(playbook_payload)
    by_name = {str(item.get("name")): item for item in payload.get("playbooks") or []}
    for name, rules in (rule_overrides or {}).items():
        if name not in by_name:
            raise ValueError(f"Unknown playbook in playbook_rules: {name}")
        current = by_name[name].setdefault("rules", {})
        unknown = sorted(set(rules) - set(current))
        if unknown:
            raise ValueError(f"Unknown rule keys for {name}: {unknown}")
        current.update(copy.deepcopy(rules))
    return payload


# ---------------------------------------------------------------- replay


def archived_runs(runs_dir: Path) -> tuple[list[Path], list[dict[str, str]]]:
    usable, skipped = [], []
    for folder in sorted(path for path in runs_dir.iterdir() if path.is_dir()):
        artifacts = folder / "artifacts"
        missing = [name for name in REQUIRED_ARTIFACTS if not (artifacts / name).exists()]
        if missing:
            skipped.append({"run": folder.name, "reason": "missing " + ", ".join(missing)})
        else:
            usable.append(artifacts)
    return usable, skipped


def decisions(rows: list[dict[str, Any]], run_ts: str) -> list[dict[str, Any]]:
    """Directional candidate/watch signals from one replay, one per (ticker, playbook)."""
    return [
        {"source_run_timestamp": run_ts, "ticker": str(row["ticker"]).upper(), "playbook_name": row["playbook_name"],
         "trade_direction": row["trade_direction"], "signal_verdict": row["signal_verdict"],
         "signal_score": row["signal_score"]}
        for row in rows
        if row.get("signal_verdict") in VERDICTS and row.get("trade_direction") in {"long", "short"}
    ]


def replay(artifacts: Path, trader: str, variant: dict[str, Any]) -> dict[str, Any]:
    spec = TRADERS[trader]
    module = spec["module"]
    base_playbooks = load_json(spec["playbooks"])
    inputs = {key: load_json(artifacts / name) for key, name in
              zip(("babis", "giannis", "fundamental_packet"), REQUIRED_ARTIFACTS)}
    champion = module.evaluate_run(**inputs, playbook_payload=base_playbooks)
    overrides = variant["overrides"]
    variant_playbooks = apply_playbook_rules(base_playbooks, overrides.get("playbook_rules") or {})
    with module.scoring_overrides(short_case_weights=overrides.get("short_case_weights"),
                                  verdict_thresholds=overrides.get("verdict_thresholds")):
        challenger = module.evaluate_run(**inputs, playbook_payload=variant_playbooks)
    run_ts = champion["run_timestamp"]
    return {"run": artifacts.parent.name, "run_timestamp": run_ts,
            "champion": decisions(champion["rows"], run_ts), "variant": decisions(challenger["rows"], run_ts)}


# ---------------------------------------------------------------- outcomes (read-only)


def _read_only(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{db_path.resolve().as_posix()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def load_outcomes(db_path: Path, run_timestamps: set[str]) -> tuple[dict[tuple[str, str, int], float], dict[tuple[str, int], float]]:
    """Raw symbol returns per (run, ticker, horizon) and shortlist baselines per (run, horizon)."""
    if not run_timestamps:
        return {}, {}
    runs = sorted(run_timestamps)
    marks = ",".join("?" for _ in runs)
    hs = ",".join("?" for _ in HORIZONS)
    conn = _read_only(db_path)
    try:
        returns = {
            (row["source_run_timestamp"], str(row["ticker"]).upper(), int(row["horizon_days"])): float(row["return_pct"])
            for row in conn.execute(
                f"SELECT cs.source_run_timestamp, cs.ticker, po.horizon_days, po.return_pct FROM candidate_setups cs "
                f"JOIN price_outcomes po ON po.setup_id = cs.id WHERE cs.source_run_timestamp IN ({marks}) "
                f"AND po.horizon_days IN ({hs}) AND po.return_pct IS NOT NULL", (*runs, *HORIZONS))
        }
        baselines = {
            (row["source_run_timestamp"], int(row["horizon_days"])): float(row["mean_return"])
            for row in conn.execute(
                f"SELECT cs.source_run_timestamp, po.horizon_days, AVG(po.return_pct) AS mean_return FROM candidate_setups cs "
                f"JOIN price_outcomes po ON po.setup_id = cs.id WHERE cs.source_run_timestamp IN ({marks}) "
                f"AND po.horizon_days IN ({hs}) AND po.return_pct IS NOT NULL GROUP BY 1, 2", (*runs, *HORIZONS))
        }
    finally:
        conn.close()
    return returns, baselines


def outcome_rows(signals: list[dict[str, Any]], returns: dict[tuple[str, str, int], float],
                 baselines: dict[tuple[str, int], float]) -> list[dict[str, Any]]:
    rows = []
    for signal in signals:
        for horizon in HORIZONS:
            raw = returns.get((signal["source_run_timestamp"], signal["ticker"], horizon))
            if raw is None:
                continue
            rows.append({**signal, "agent_name": "replay", "horizon_days": horizon, "raw_symbol_return_pct": raw,
                         "directional_return_pct": scorecards.directional_return(raw, signal["trade_direction"])})
    scorecards.attach_baselines(rows, baselines)
    return rows


# ---------------------------------------------------------------- comparison


def _metrics(rows: list[dict[str, Any]]) -> dict[tuple[str, int], dict[str, Any]]:
    cards = scorecards.build_grouped_scorecards(scorecards.unique_decisions(rows), ["trade_direction", "horizon_days"])
    return {(card["trade_direction"], card["horizon_days"]): card for card in cards}


_KEEP = ("sample_count", "win_rate_pct", "wilson_win_rate_lower_pct", "expectancy_pct", "net_expectancy_pct",
         "avg_excess_return_pct", "beat_baseline_rate_pct", "edge_status")


def compare(champion_rows: list[dict[str, Any]], variant_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Per direction x horizon: both sides, plus the decisions only one side made."""
    champion_keys = {scorecards.decision_key(row) for row in champion_rows}
    variant_keys = {scorecards.decision_key(row) for row in variant_rows}
    groups = {
        "champion": _metrics(champion_rows),
        "variant": _metrics(variant_rows),
        "added_by_variant": _metrics([row for row in variant_rows if scorecards.decision_key(row) not in champion_keys]),
        "dropped_by_variant": _metrics([row for row in champion_rows if scorecards.decision_key(row) not in variant_keys]),
    }
    keys = sorted(set().union(*(set(group) for group in groups.values())))
    result = []
    for direction, horizon in keys:
        entry: dict[str, Any] = {"trade_direction": direction, "horizon_days": horizon}
        for name, group in groups.items():
            card = group.get((direction, horizon))
            entry[name] = {key: card.get(key) for key in _KEEP} if card else None
        result.append(entry)
    return result


def _git_commit() -> str | None:
    try:
        return subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True,
                              text=True, timeout=10, check=True).stdout.strip() or None
    except (OSError, subprocess.SubprocessError):
        return None


def build_report(variant: dict[str, Any], *, runs_dir: Path = RUNS_DIR, db_path: Path = DB_PATH) -> dict[str, Any]:
    usable, skipped = archived_runs(runs_dir)
    replays = []
    for artifacts in usable:
        try:
            replays.append(replay(artifacts, variant["trader"], variant))
        except (ValueError, KeyError, SystemExit) as exc:
            skipped.append({"run": artifacts.parent.name, "reason": f"replay failed: {exc}"[:300]})
    returns, baselines = load_outcomes(db_path, {item["run_timestamp"] for item in replays})
    champion = [signal for item in replays for signal in item["champion"]]
    challenger = [signal for item in replays for signal in item["variant"]]
    return {
        "schema": REPORT_SCHEMA,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "variant_id": variant["variant_id"], "trader": variant["trader"],
        "description": variant.get("description"), "hypothesis": variant.get("hypothesis"),
        "overrides": variant["overrides"], "variant_file_sha256": variant["file_sha256"],
        "code_commit": _git_commit(),
        "runs_replayed": [item["run"] for item in replays], "runs_skipped": skipped,
        "signal_counts": {"champion": len(champion), "variant": len(challenger)},
        "comparison": compare(outcome_rows(champion, returns, baselines), outcome_rows(challenger, returns, baselines)),
        "live_decisions_changed": False,
        "notes": [
            "Champion and variant are both replayed with the current code on the archived inputs of each run.",
            "Each (run, ticker, direction, horizon) decision is counted once; returns are before borrow fees.",
            "added_by_variant / dropped_by_variant show only the decisions one side made and the other did not.",
            "Promotion is manual: edit the trader's playbooks or weights in a reviewed commit.",
        ],
    }


def _fmt(card: dict[str, Any] | None) -> str:
    if not card:
        return "-"
    return (f"n={card['sample_count']} win={card['win_rate_pct']}% net={card['net_expectancy_pct']}% "
            f"vs shortlist={card['avg_excess_return_pct']}% [{card['edge_status']}]")


def render_markdown(report: dict[str, Any]) -> str:
    lines = [f"# Shadow replay: {report['variant_id']}", "",
             f"- Trader: {report['trader']}", f"- Hypothesis: {report.get('hypothesis') or '-'}",
             f"- Overrides: `{json.dumps(report['overrides'], ensure_ascii=False)}`",
             f"- Runs replayed: {len(report['runs_replayed'])}; skipped: {len(report['runs_skipped'])}",
             f"- Signals: champion {report['signal_counts']['champion']}, variant {report['signal_counts']['variant']}",
             f"- Code: {report.get('code_commit') or 'unknown'}; live decisions changed: no", "",
             "| Direction | Horizon | Champion | Variant | Added by variant | Dropped by variant |",
             "|---|---:|---|---|---|---|"]
    for row in report["comparison"]:
        lines.append(f"| {row['trade_direction']} | {row['horizon_days']}d | {_fmt(row['champion'])} | {_fmt(row['variant'])} "
                     f"| {_fmt(row['added_by_variant'])} | {_fmt(row['dropped_by_variant'])} |")
    if report["runs_skipped"]:
        lines += ["", "## Skipped runs", ""] + [f"- {item['run']}: {item['reason']}" for item in report["runs_skipped"]]
    lines += ["", "## Notes", ""] + [f"- {note}" for note in report["notes"]]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Replay a trader shadow variant on archived runs (read-only).")
    parser.add_argument("--variant", required=True, help="Path to a shadow variant JSON file.")
    parser.add_argument("--runs-dir", default=str(RUNS_DIR))
    parser.add_argument("--db", default=str(DB_PATH))
    args = parser.parse_args(argv)
    variant = load_variant(Path(args.variant))
    report = build_report(variant, runs_dir=Path(args.runs_dir), db_path=Path(args.db))
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    prefix = OUTPUT_DIR / f"shadow_replay_{variant['variant_id']}"
    Path(f"{prefix}_latest.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    Path(f"{prefix}_latest.md").write_text(render_markdown(report), encoding="utf-8")
    print(f"[OUTPUT] {prefix}_latest.md")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
