"""Run performance history: one row per research run, kept in a CSV and a Markdown table.

Written automatically at the end of every run (run_full_research_control_plane.ps1) and fillable for earlier
runs with ``backfill``. Built only from files the run already produced (archived artifacts, the pipeline's own
step timings, the run pointer and the control-plane database, read-only): nothing is measured twice, nothing is
guessed. A value a run did not record stays empty.

Files (git-ignored under output/):
- ``output/run_performance_history.csv``: all columns, one row per run_id (a run is updated, not duplicated);
  new columns are added at the end, so old rows stay valid. Opens in Excel (UTF-8 with BOM).
- ``output/run_performance_history.md``: the latest runs as a readable table, newest first.
"""
from __future__ import annotations

import argparse
import csv
import re
from collections import Counter
from datetime import datetime
import json
from pathlib import Path
import sqlite3
from typing import Any

ROOT = Path(__file__).resolve().parent
CSV_NAME = "run_performance_history.csv"
MD_NAME = "run_performance_history.md"
STEP_KEYS = (
    "babis_refresh", "giannis_triage", "information_ledger", "control_plane_batch", "petros_synthesis",
    "deep_research", "freeze_packet", "yankos", "periklis", "lock_predictions", "outcome_baseline",
    "outcome_refresh", "backtest", "sokratis", "petros_training", "meeting_report", "action_report",
    "trader_handoff", "coordination_audit")
GAP_REASONS = ("sector_evidence_missing", "sector_evidence_partial", "unsupported_instrument", "classification_unresolved", "no_data",
               "source_unavailable", "fresh_primary_evidence_missing")
GIANNIS_TIERS = {"Core Watchlist": "core", "Selective Watchlist": "selective", "Speculative Monitor": "speculative",
                 "Avoid For Now": "avoid", "Complex Security Risk": "complex_risk"}
MD_COLUMNS = (
    ("started_at", "Έναρξη"), ("status", "Κατάσταση"), ("wall_hours", "Ώρες"), ("babis_scan_min", "Babis′"),
    ("batch_min", "Οικονομικά′"), ("shortlist", "Shortlist"), ("giannis_core", "Core"), ("petros_candidate", "Petros cand."),
    ("yankos_candidates", "Yankos"), ("periklis_candidates", "Periklis"), ("gaps_open", "Κενά"),
    ("giannis_tier_changes", "Αλλαγές Giannis"), ("technical_errors", "Σφάλματα"))


def _load(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _rows(payload: dict[str, Any], key: str = "results") -> list[dict[str, Any]]:
    return [row for row in payload.get(key) or [] if isinstance(row, dict)]


def _minutes(seconds: Any) -> float | None:
    try:
        return round(float(seconds) / 60.0, 1)
    except (TypeError, ValueError):
        return None


def _by_ticker(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {str(row.get("ticker")): row for row in rows if row.get("ticker")}


def _candidates(rows: list[dict[str, Any]]) -> set[str]:
    return {str(row.get("ticker")) for row in rows if row.get("post_gate_verdict") == "candidate"}


def _find_timings(root: Path, run_id: str) -> dict[str, Any]:
    latest = _load(root / "output" / "pipeline_step_timings_latest.json")
    if latest.get("run_id") == run_id:
        return latest
    for path in sorted((root / "output").glob("pipeline_step_timings_2*.json"), reverse=True):
        payload = _load(path)
        if payload.get("run_id") == run_id:
            return payload
    return {}


def _open_gaps(root: Path, run_id: str) -> dict[str, int] | None:
    database = root / "data" / "market_intel.db"
    if not database.is_file():
        return None
    try:
        db = sqlite3.connect(f"file:{database.resolve().as_posix()}?mode=ro", uri=True, timeout=10)
    except sqlite3.Error:
        return None
    try:
        rows = db.execute("SELECT reason_code, COUNT(*) FROM ecosystem_control_plane_gap "
                          "WHERE run_id=? AND status='open' GROUP BY reason_code", (run_id,)).fetchall()
    except sqlite3.Error:
        return None
    finally:
        db.close()
    return {str(reason): int(count) for reason, count in rows}


def _status_from_report(root: Path, run_id: str) -> str:
    """The final status the daily report printed for a run whose pointer has since been overwritten."""
    try:
        head = (root / "output" / "daily_reports" / f"{run_id}.md").read_text(encoding="utf-8")[:600]
    except OSError:
        return ""
    match = re.search(r"Κατάσταση run:\s*([a-z_]+)", head)
    return match.group(1) if match else ""


def _previous_run(runs_dir: Path, run_dir: Path) -> Path | None:
    mine = _load(run_dir / "manifest.json").get("captured_at") or ""
    earlier = []
    for manifest in runs_dir.glob("full_research_*/manifest.json"):
        captured = _load(manifest).get("captured_at") or ""
        if manifest.parent != run_dir and captured and (not mine or captured < mine) and (manifest.parent / "artifacts").is_dir():
            earlier.append((captured, manifest.parent))
    return max(earlier)[1] if earlier else None


def collect(root: Path, run_id: str) -> dict[str, Any]:
    """The performance row of one run. Anything the run did not record stays missing."""
    run_dir = root / "output" / "runs" / run_id
    art = run_dir / "artifacts"
    row: dict[str, Any] = {"run_id": run_id, "recorded_at": datetime.now().astimezone().isoformat(timespec="seconds")}
    pointer = _load(root / "output" / "full_research_control_plane_background_latest.json")
    state = pointer if pointer.get("run_id") == run_id else {}
    timings = _find_timings(root, run_id)
    steps = [s for s in timings.get("steps") or [] if isinstance(s, dict)]

    started = state.get("started_at") or (steps[0].get("started_at") if steps else None)
    finished = state.get("finished_at") or (steps[-1].get("finished_at") if steps and steps[-1].get("finished_at") else None)
    status = state.get("status") or _status_from_report(root, run_id) or ("" if steps else "unknown")
    row.update(started_at=started, finished_at=finished, status=status,
               technical_errors=state.get("technical_errors"), agent_audit=state.get("agent_audit_status"),
               last_step=state.get("last_step"))
    try:
        row["wall_hours"] = round((datetime.fromisoformat(str(finished)) - datetime.fromisoformat(str(started))).total_seconds() / 3600, 2)
    except (TypeError, ValueError):
        row["wall_hours"] = None
    by_step = {s.get("key"): s for s in steps}
    for key in STEP_KEYS:
        row[f"step_{key}_min"] = _minutes((by_step.get(key) or {}).get("seconds"))
    row["babis_scan_min"] = row["step_babis_refresh_min"]
    row["batch_min"] = row["step_control_plane_batch_min"]

    babis = _load(art / "babis_shortlist_latest.json")
    runtime = babis.get("runtime_summary") or {}
    routing = babis.get("universe_routing") or {}
    quality = babis.get("data_quality_summary") or {}
    lanes = routing.get("instrument_observations_deferred_to_lane") or {}
    row.update(
        universe_total=babis.get("universe_total"), shortlist=babis.get("shortlist_total"),
        deferred_etf=lanes.get("etf_lane"), deferred_spac=lanes.get("spac_lane"),
        deferred_issuer_linked=routing.get("instrument_observations_deferred_to_issuer"),
        history_excluded=quality.get("history_exclusion_count"), coverage_excluded=quality.get("coverage_exclusion_count"),
        scan_market_min=_minutes(runtime.get("market_data_seconds")), scan_fundamentals_min=_minutes(runtime.get("fundamentals_and_news_seconds")),
        scan_sec_min=_minutes(runtime.get("sec_seconds")))

    giannis, petros = _rows(_load(art / "giannis_rule_based_latest.json")), _rows(_load(art / "petros_review_latest.json"))
    yankos, periklis = _rows(_load(art / "yankos_strategy_latest.json")), _rows(_load(art / "periklis_strategy_latest.json"))
    tiers = Counter(str(r.get("giannis_tier")) for r in giannis)
    for tier, short in GIANNIS_TIERS.items():
        row[f"giannis_{short}"] = tiers.get(tier, 0) if giannis else None  # ran with nobody in a tier = 0; did not run = unknown
    verdicts = Counter(str(r.get("petros_verdict")) for r in petros)
    for verdict in ("candidate", "manual_review", "watch", "reject"):
        row[f"petros_{verdict}"] = verdicts.get(verdict, 0) if petros else None
    row.update(yankos_signals=len(yankos) or None, yankos_candidates=len(_candidates(yankos)) if yankos else None,
               periklis_signals=len(periklis) or None, periklis_candidates=len(_candidates(periklis)) if periklis else None)

    gaps = _open_gaps(root, run_id)
    row["gaps_open"] = sum(gaps.values()) if gaps is not None else None
    for reason in GAP_REASONS:
        row[f"gap_{reason}"] = gaps.get(reason, 0) if gaps is not None else None

    batch = _load(art / "fundamental_control_plane_batch_latest.json")
    counts = batch.get("counts") or {}
    row.update(batch_completed=counts.get("completed"), batch_blocked=counts.get("blocked"), batch_failed=counts.get("failed"),
               companyfacts_from_store=counts.get("companyfacts_from_store"),
               sec_permanent_404=sum(1 for r in _rows(batch) if (r.get("financial") or {}).get("permanent")) if batch else None)

    gate = _load(root / "output" / "refresh_gate_latest.json")
    modes = _load(root / "data" / "refresh_modes.json")
    row.update(gate_companyfacts=(modes.get("companyfacts") or {}).get("mode") or (gate.get("companyfacts") or {}).get("decision"),
               gate_stage1_info=(modes.get("stage1_info") or {}).get("mode") or (gate.get("stage1_info") or {}).get("decision"))

    previous = _previous_run(root / "output" / "runs", run_dir) if run_dir.is_dir() else None
    row["compared_with"] = previous.name if previous else None
    if previous is not None:
        old_art = previous / "artifacts"
        old_g, new_g = _by_ticker(_rows(_load(old_art / "giannis_rule_based_latest.json"))), _by_ticker(giannis)
        old_p, new_p = _by_ticker(_rows(_load(old_art / "petros_review_latest.json"))), _by_ticker(petros)
        row["giannis_tier_changes"] = sum(1 for t in set(old_g) & set(new_g) if old_g[t].get("giannis_tier") != new_g[t].get("giannis_tier"))
        row["petros_verdict_changes"] = sum(1 for t in set(old_p) & set(new_p) if old_p[t].get("petros_verdict") != new_p[t].get("petros_verdict"))
        old_b = set(_by_ticker(_rows(_load(old_art / "babis_shortlist_latest.json"), "shortlist")))
        new_b = set(_by_ticker(_rows(babis, "shortlist")))
        row["shortlist_added"], row["shortlist_removed"] = len(new_b - old_b), len(old_b - new_b)
    return row


def _read(path: Path) -> list[dict[str, str]]:
    try:
        with path.open(encoding="utf-8-sig", newline="") as handle:
            return list(csv.DictReader(handle))
    except OSError:
        return []


def record(root: Path, run_id: str, *, row: dict[str, Any] | None = None) -> dict[str, Any]:
    """Add or update the row of ``run_id`` in the CSV and rewrite the Markdown table."""
    row = row or collect(root, run_id)
    out = root / "output"
    out.mkdir(parents=True, exist_ok=True)
    existing = _read(out / CSV_NAME)
    by_id = {r["run_id"]: r for r in existing if r.get("run_id")}
    by_id[run_id] = {k: ("" if v is None else v) for k, v in row.items()}
    columns: list[str] = []
    for item in [*existing, *by_id.values()]:
        columns.extend(k for k in item if k not in columns)
    ordered = sorted(by_id.values(), key=lambda r: (str(r.get("started_at") or ""), str(r.get("run_id"))))
    temporary = out / (CSV_NAME + ".tmp")
    with temporary.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, restval="")
        writer.writeheader()
        writer.writerows(ordered)
    temporary.replace(out / CSV_NAME)
    write_markdown(out, ordered)
    return row


def write_markdown(out: Path, ordered: list[dict[str, Any]], limit: int = 30) -> None:
    lines = ["# Επιδόσεις των runs (ένα ανά γραμμή, τα νεότερα πρώτα)", "",
             f"Πλήρη στοιχεία: `{CSV_NAME}` (ανοίγει στο Excel). Κενό κελί = το run δεν το κατέγραψε.", "",
             "| Run | " + " | ".join(label for _, label in MD_COLUMNS) + " |",
             "|---|" + "|".join("---" for _ in MD_COLUMNS) + "|"]
    for item in reversed(ordered[-limit:]):
        cells = []
        for key, _ in MD_COLUMNS:
            value = item.get(key, "")
            cells.append(str(value)[:16] if key == "started_at" else str(value))
        lines.append(f"| {item.get('run_id', '')} | " + " | ".join(cells) + " |")
    (out / MD_NAME).write_text("\n".join(lines) + "\n", encoding="utf-8")


def backfill(root: Path) -> list[str]:
    """Record every archived full-research run that is not in the history yet (existing rows are kept)."""
    known = {r.get("run_id") for r in _read(root / "output" / CSV_NAME)}
    added = []
    for manifest in sorted((root / "output" / "runs").glob("full_research_*/manifest.json")):
        run_id = manifest.parent.name
        if run_id not in known:
            record(root, run_id)
            added.append(run_id)
    return added


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", type=Path, default=ROOT)
    sub = parser.add_subparsers(dest="command", required=True)
    one = sub.add_parser("record", help="add or update the row of one run")
    one.add_argument("--run-id", required=True)
    sub.add_parser("backfill", help="add the archived runs that are not in the history yet")
    args = parser.parse_args(argv)
    if args.command == "record":
        if any(part in args.run_id for part in ("/", "\\", "..")) or not args.run_id:
            raise SystemExit(f"Invalid run id: {args.run_id}")
        row = record(args.root, args.run_id)
        print(json.dumps({"recorded": args.run_id, "csv": str(args.root / "output" / CSV_NAME),
                          "wall_hours": row.get("wall_hours"), "status": row.get("status")}, ensure_ascii=False))
    else:
        print(json.dumps({"backfilled": backfill(args.root)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
