"""Canonical, bounded production workflow for the Babis classification agent.

The pipeline runs each stage once, in order, without polling or automatic
retries.  It imports evidence and creates a review package, but it never
applies classification changes.  Applying a reviewed batch remains a separate
owner-approved command.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import time
from typing import Any

from . import babis_problem_enrichment, babis_sec_catalog, classification_problems, evidence_gaps
from .core import DEFAULT_DB, PROJECT, connect


ROOT = PROJECT / "data" / "babis_pipeline"
LATEST = ROOT / "latest.json"
RATE_DB = babis_problem_enrichment.SHARED_RATE_DB
RATE_POLICY = babis_problem_enrichment.RATE_POLICY


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def save(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def notify_machine(message: str) -> None:
    """Best-effort one-shot local notification; never changes run outcome."""
    try:
        subprocess.run(
            ["msg.exe", "*", message], check=False, timeout=15,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
    except (OSError, subprocess.SubprocessError):
        pass


def _configured_names() -> set[str]:
    names = {name for name, value in os.environ.items() if value}
    env_path = PROJECT / ".env"
    if env_path.exists():
        for raw in env_path.read_text(encoding="utf-8-sig").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            name, value = line.split("=", 1)
            if name.strip() and value.strip().strip("\"'"):
                names.add(name.strip())
    return names


def provider_readiness(rate_db: Path = RATE_DB, policy_path: Path = RATE_POLICY, at: float | None = None) -> dict[str, Any]:
    current = time.time() if at is None else at
    utc_day = datetime.fromtimestamp(current, timezone.utc).date().isoformat()
    policy = json.loads(Path(policy_path).read_text(encoding="utf-8"))["providers"]
    rows: dict[str, tuple[Any, ...]] = {}
    if Path(rate_db).exists():
        db = sqlite3.connect(rate_db)
        try:
            try:
                rows = {row[0]: row for row in db.execute("SELECT * FROM provider_rate_state")}
            except sqlite3.OperationalError:
                rows = {}
        finally:
            db.close()
    providers = {}
    for provider, limits in policy.items():
        row = rows.get(provider)
        day_requests = int(row[2]) if row and row[1] == utc_day else 0
        blocked_until = float(row[6]) if row else 0.0
        reason = None
        if blocked_until > current:
            reason = "shared_cooldown"
        elif day_requests >= int(limits["maximum_requests_per_utc_day"]):
            reason = "shared_daily_budget_exhausted"
        providers[provider] = {
            "ready": reason is None,
            "reason": reason,
            "day_requests": day_requests,
            "daily_limit": int(limits["maximum_requests_per_utc_day"]),
            "next_eligible_at": (
                datetime.fromtimestamp(blocked_until, timezone.utc).isoformat()
                if blocked_until > current else None
            ),
        }
    return providers


def readiness(rate_db: Path = RATE_DB, policy_path: Path = RATE_POLICY, at: float | None = None) -> dict[str, Any]:
    configured = _configured_names()
    providers = provider_readiness(rate_db, policy_path, at)
    required = {
        "SEC_USER_AGENT": "sec",
        "FMP_API_KEY": "fmp",
        "POLYGON_API_KEY": "polygon",
    }
    configuration = {name: name in configured for name in required}
    blockers = [f"missing_configuration:{name}" for name, present in configuration.items() if not present]
    blockers.extend(
        f"provider_unavailable:{provider}:{details['reason']}"
        for provider, details in providers.items() if not details["ready"]
    )
    return {
        "status": "ready" if not blockers else "not_ready",
        "ready": not blockers,
        "configuration": configuration,
        "providers": providers,
        "blockers": blockers,
        "automatic_retries": 0,
        "llm_calls": 0,
        "automatic_classification_writes": False,
    }


def _review_summary(db: sqlite3.Connection, run_id: str, enrichment_job_id: str) -> dict[str, Any]:
    assessments = dict(db.execute(
        "SELECT assessment,COUNT(*) FROM classification_problem_enrichment "
        "WHERE job_id=? GROUP BY assessment ORDER BY assessment", (enrichment_job_id,)
    ))
    problems = dict(db.execute(
        "SELECT category,COUNT(*) FROM classification_problem_item "
        "WHERE run_id=? GROUP BY category ORDER BY category", (run_id,)
    ))
    gaps = dict(db.execute(
        "SELECT reason_code,COUNT(*) FROM ecosystem_evidence_gap "
        "WHERE source_run_id=? GROUP BY reason_code ORDER BY reason_code", (enrichment_job_id,)
    ))
    return {"problem_categories": problems, "assessments": assessments, "evidence_gaps": gaps}


def _write_report(folder: Path, state: dict[str, Any], summary: dict[str, Any]) -> None:
    lines = [
        "# Μπάμπης: canonical classification run",
        "",
        f"Κατάσταση: `{state['status']}`.",
        f"SEC job: `{state.get('sec_job_id')}`.",
        f"Enrichment job: `{state.get('enrichment_job_id')}`.",
        "",
        "Δεν εφαρμόστηκαν αυτόματα αλλαγές στην production ταξινόμηση. Το πακέτο είναι έτοιμο για ανθρώπινο έλεγχο.",
    ]
    for heading, values in (("Κατηγορίες προβλημάτων", summary.get("problem_categories", {})),
                            ("Assessments", summary.get("assessments", {})),
                            ("Evidence gaps", summary.get("evidence_gaps", {}))):
        lines += ["", f"## {heading}", "", "| Τύπος | Πλήθος |", "| --- | ---: |"]
        lines += [f"| {key} | {value} |" for key, value in sorted(values.items())]
    (folder / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    save(folder / "review_summary.json", summary)


def run(folder: Path, sec_max_requests: int = 6000, enrichment_max_requests: int = 4200,
        allow_source_gaps: bool = False) -> int:
    folder = Path(folder)
    state = json.loads((folder / "state.json").read_text(encoding="utf-8"))
    state.update(status="running", pid=os.getpid(), started_at=now(), stage="readiness")
    save(folder / "state.json", state)
    try:
        gate = readiness()
        save(folder / "readiness.json", gate)
        if not gate["ready"] and not allow_source_gaps:
            state.update(status="waiting_for_sources", stage="readiness", blockers=gate["blockers"], finished_at=now())
            save(folder / "state.json", state)
            notify_machine("Ο Μπάμπης δεν ξεκίνησε: τα API sources δεν είναι ακόμη διαθέσιμα. Δείτε το readiness report.")
            return 3
        if not gate["ready"]:
            state.update(source_gap_override=True, source_gap_blockers=gate["blockers"])
            save(folder / "state.json", state)

        state["stage"] = "sec_catalog"
        save(folder / "state.json", state)
        sec_folder = babis_sec_catalog.prepare()
        if babis_sec_catalog.run(sec_folder, sec_max_requests) != 0:
            raise RuntimeError("SEC catalogue stage failed")
        sec_state = json.loads((sec_folder / "state.json").read_text(encoding="utf-8"))
        if sec_state.get("status") != "finished_with_review_required":
            raise RuntimeError(f"SEC catalogue stage ended as {sec_state.get('status')}")
        run_id = sec_state["job_id"]
        state.update(sec_job_id=run_id, sec_job=str(sec_folder), stage="problem_registry")
        save(folder / "state.json", state)

        db = connect(DEFAULT_DB)
        try:
            problem_result = classification_problems.import_job(db, sec_folder)
        finally:
            db.close()

        state["stage"] = "supplemental_enrichment"
        save(folder / "state.json", state)
        enrichment_folder = babis_problem_enrichment.prepare(run_id)
        if babis_problem_enrichment.run(enrichment_folder, enrichment_max_requests) != 0:
            raise RuntimeError("Supplemental enrichment stage failed")
        enrichment_state = json.loads((enrichment_folder / "state.json").read_text(encoding="utf-8"))
        if enrichment_state.get("status") != "finished_with_review_required":
            raise RuntimeError(f"Enrichment stage ended as {enrichment_state.get('status')}")
        enrichment_job_id = enrichment_state["job_id"]
        state.update(enrichment_job_id=enrichment_job_id, enrichment_job=str(enrichment_folder), stage="evidence_import")
        save(folder / "state.json", state)

        db = connect(DEFAULT_DB)
        try:
            enrichment_result = classification_problems.import_enrichment(db, enrichment_folder)
            gap_result = evidence_gaps.import_enrichment(db, enrichment_folder)
            summary = _review_summary(db, run_id, enrichment_job_id)
        finally:
            db.close()

        state.update(
            status="finished_with_review_required", stage="complete", finished_at=now(),
            automatic_retries=0, llm_calls=0, automatic_classification_writes=0,
            problem_registry=problem_result, enrichment_import=enrichment_result,
            evidence_gap_import=gap_result,
        )
        save(folder / "state.json", state)
        _write_report(folder, state, summary)
        try:
            from sector_ecosystem_integration import activate as activate_sector_ecosystem
            sector_result = activate_sector_ecosystem(folder)
            state["sector_ecosystem_activation"] = sector_result
        except Exception as exc:
            state["sector_ecosystem_activation"] = {
                "status": "failed", "error_type": type(exc).__name__,
                "error_message": str(exc)[:500],
            }
        save(folder / "state.json", state)
        notify_machine("Ο Μπάμπης ολοκλήρωσε το νέο classification run. Το report είναι έτοιμο για έλεγχο.")
        return 0
    except Exception as exc:
        state.update(status="failed", error_type=type(exc).__name__, error_message=str(exc)[:500], finished_at=now())
        save(folder / "state.json", state)
        notify_machine("Το νέο run του Μπάμπη απέτυχε. Δείτε το state.json για το καταγεγραμμένο πρόβλημα.")
        return 1


def prepare(root: Path = ROOT) -> Path:
    job_id = "babis-pipeline-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    folder = Path(root) / job_id
    folder.mkdir(parents=True)
    save(folder / "state.json", {"status": "prepared", "job_id": job_id, "created_at": now()})
    save(LATEST, {"job": str(folder)})
    return folder


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("readiness", "start", "run", "status"))
    parser.add_argument("--job", type=Path)
    parser.add_argument("--sec-max-requests", type=int, default=6000)
    parser.add_argument("--enrichment-max-requests", type=int, default=4200)
    parser.add_argument("--allow-source-gaps", action="store_true",
                        help="Preserve unavailable providers as explicit evidence gaps")
    args = parser.parse_args(argv)
    if not 0 <= args.sec_max_requests <= 6000:
        parser.error("sec-max-requests must be 0..6000")
    if not 0 <= args.enrichment_max_requests <= 4200:
        parser.error("enrichment-max-requests must be 0..4200")
    if args.action == "readiness":
        print(json.dumps(readiness(), ensure_ascii=False, indent=2))
        return 0
    folder = args.job
    if args.action == "start":
        gate = readiness()
        if not gate["ready"] and not args.allow_source_gaps:
            print(json.dumps(gate, ensure_ascii=False, indent=2))
            return 3
        folder = folder or prepare()
        log = (folder / "worker.log").open("a", encoding="utf-8")
        try:
            process = subprocess.Popen(
                [sys.executable, "-B", "-m", "research_registry.babis_pipeline", "run", "--job", str(folder),
                 "--sec-max-requests", str(args.sec_max_requests),
                 "--enrichment-max-requests", str(args.enrichment_max_requests)]
                + (["--allow-source-gaps"] if args.allow_source_gaps else []),
                cwd=PROJECT, stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW,
            )
        finally:
            log.close()
        latest = json.loads(LATEST.read_text(encoding="utf-8")); latest["pid"] = process.pid; save(LATEST, latest)
        print(json.dumps({"status": "dispatched", "pid": process.pid, "job": str(folder)}, ensure_ascii=False))
        return 0
    if folder is None:
        folder = Path(json.loads(LATEST.read_text(encoding="utf-8"))["job"])
    if args.action == "status":
        print((folder / "state.json").read_text(encoding="utf-8"))
        return 0
    return run(folder, args.sec_max_requests, args.enrichment_max_requests, args.allow_source_gaps)


if __name__ == "__main__":
    raise SystemExit(main())
