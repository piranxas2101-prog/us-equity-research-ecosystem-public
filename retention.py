"""Disk retention for the runtime state (owner 2026-11-18, ADR-002): a report first, deletion only on the owner's yes.

Runtime state had reached ~73 GB. Each rule below says what it may remove and why that is safe; ``report`` (the
default) only lists the candidates with their size. ``apply --rule <name> --yes`` deletes one rule's candidates and
prints what it removed; nothing else ever deletes. A rule never touches the audit record (``output/runs/<run_id>/``,
read by the results journal and the holding shadows), the databases or anything newer than its age limit.

* ``baseline_compare`` - ``output/baseline_compare_<timestamp>/``: the previous night's ``*_latest`` files, copied
  only for that run's compare step (and the newest one by the follow-up rerun). The newest ``KEEP_BASELINES`` stay.
* ``test_reports`` - ``output/ecosystem_test_reports/<run_id>/``: each run's agent-audit report, written once and read
  by no step afterwards; older than ``REPORT_DAYS`` days.
* ``logs`` - files in ``logs/`` older than ``LOG_DAYS`` days.

Reported only (no rule deletes them): ``output/runs`` (the audit record) and the SQLite stores in ``data/``; the
largest, ``data/market_intel.db``, needs its own plan (what it keeps and for how long) decided with the owner.

    python retention.py                      # report
    python retention.py apply --rule logs --yes
"""
from __future__ import annotations

import argparse
from datetime import datetime
import json
from pathlib import Path
import shutil
import time
from typing import Any

ROOT = Path(__file__).resolve().parent
KEEP_BASELINES = 3
REPORT_DAYS = 60
LOG_DAYS = 60
RULES = ("baseline_compare", "test_reports", "logs")


def _size(path: Path) -> int:
    if path.is_file():
        return path.stat().st_size
    return sum(p.stat().st_size for p in path.rglob("*") if p.is_file())


def _older(path: Path, days: int, now: float) -> bool:
    return path.stat().st_mtime < now - days * 86400


def candidates(root: Path, rule: str, *, now: float | None = None) -> list[Path]:
    now = now or time.time()
    out = root / "output"
    if rule == "baseline_compare":
        folders = sorted((p for p in out.glob("baseline_compare_*") if p.is_dir()), key=lambda p: p.name)
        return folders[:-KEEP_BASELINES] if len(folders) > KEEP_BASELINES else []
    if rule == "test_reports":
        base = out / "ecosystem_test_reports"
        return sorted(p for p in base.iterdir() if p.is_dir() and _older(p, REPORT_DAYS, now)) if base.is_dir() else []
    if rule == "logs":
        base = root / "logs"
        return sorted(p for p in base.rglob("*") if p.is_file() and _older(p, LOG_DAYS, now)) if base.is_dir() else []
    raise ValueError(f"unknown rule {rule!r}")


def report(root: Path, *, now: float | None = None) -> dict[str, Any]:
    rules = {}
    for rule in RULES:
        found = candidates(root, rule, now=now)
        rules[rule] = {"items": len(found), "gb": round(sum(_size(p) for p in found) / 2**30, 2),
                       "oldest": found[0].name if found else None, "newest": found[-1].name if found else None}
    kept = {}
    for rel in ("output/runs", "data/market_intel.db", "data/research_registry.db", "data/paper_learning.db"):
        path = root / rel
        kept[rel] = round(_size(path) / 2**30, 2) if path.exists() else None
    return {"rules": rules, "kept_reported_only": kept,
            "would_free_gb": round(sum(r["gb"] for r in rules.values()), 2)}


def apply(root: Path, rule: str, *, now: float | None = None) -> dict[str, Any]:
    found = candidates(root, rule, now=now)
    freed = 0
    for path in found:
        freed += _size(path)
        if path.is_dir():
            shutil.rmtree(path)
        else:
            path.unlink()
    return {"rule": rule, "removed": len(found), "freed_gb": round(freed / 2**30, 2),
            "at": datetime.now().astimezone().isoformat(timespec="seconds")}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Runtime-state retention: report, or apply one rule on the owner's yes.")
    parser.add_argument("command", nargs="?", default="report", choices=("report", "apply"))
    parser.add_argument("--rule", choices=RULES)
    parser.add_argument("--yes", action="store_true", help="the owner approved deleting this rule's candidates")
    parser.add_argument("--root", type=Path, default=ROOT)
    args = parser.parse_args(argv)
    if args.command == "apply":
        if not args.rule or not args.yes:
            parser.error("apply needs --rule and --yes (the owner's approval)")
        result = apply(args.root, args.rule)
        log = args.root / "data" / "retention_log.jsonl"
        log.parent.mkdir(parents=True, exist_ok=True)
        with log.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(result, ensure_ascii=False) + "\n")
        print(json.dumps(result, ensure_ascii=False))
        return 0
    print(json.dumps(report(args.root), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
