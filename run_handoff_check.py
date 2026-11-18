"""Whether the research files the session reads all come from one run (owner 2026-11-18, ADR-002).

The pipeline hands its results from step to step through ``output/*_latest.json``. A fallback run, a resumed run or
a step run by hand can overwrite some of them, and a reader would then mix two runs without knowing it. Every
principal file carries its ``run_id``; this check reads them and reports one run, or which file belongs to which run
when they differ. A missing file or one without a run id is unknown, never assumed to match.

Read-only: it changes no file and stops nothing. Jarvis runs it on every pass and reports a mix as a problem. (Moving
every reader to ``output/runs/<run_id>/`` is the fuller fix; it touches the traders and Koulis, which the release
freeze of period P1 keeps unchanged, so it waits until after the period.)

    python run_handoff_check.py
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
PRINCIPAL = ("babis_shortlist_latest.json", "giannis_rule_based_latest.json", "petros_review_latest.json",
             "company_deep_research_latest.json", "fundamental_research_packet_latest.json",
             "yankos_strategy_latest.json", "periklis_strategy_latest.json")


def _run_id(payload: Any) -> str | None:
    if not isinstance(payload, dict):
        return None
    value = payload.get("run_id") or (payload.get("run_context") or {}).get("run_id")
    return str(value) if value else None


def check(root: Path = ROOT) -> dict[str, Any]:
    files: dict[str, str | None] = {}
    unknown: list[str] = []
    for name in PRINCIPAL:
        try:
            payload = json.loads((root / "output" / name).read_text(encoding="utf-8-sig"))
        except (OSError, ValueError):
            files[name] = None
            unknown.append(name)
            continue
        files[name] = _run_id(payload)
        if files[name] is None:
            unknown.append(name)
    runs: dict[str, list[str]] = {}
    for name, rid in files.items():
        if rid is not None:
            runs.setdefault(rid, []).append(name)
    state = "mixed" if len(runs) > 1 else "unknown" if not runs or unknown else "one_run"
    return {"state": state, "run_id": next(iter(runs)) if len(runs) == 1 else None,
            "runs": {rid: sorted(names) for rid, names in sorted(runs.items())}, "unknown": unknown}


def main() -> int:
    print(json.dumps(check(ROOT), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
