"""Jarvis health: what the nightly run and the afternoon session left behind, read from their own files.

Read-only. A file or field that is not there is reported as ``unknown`` with the reason, never as ok. Failures are
diagnosed by fixed patterns in the launchers' logs (the same ones read by hand so far).
"""
from __future__ import annotations

import csv
from dataclasses import dataclass, field
from datetime import date, datetime
import json
from pathlib import Path
import re
from typing import Any
from zoneinfo import ZoneInfo

POINTER = Path("output") / "full_research_control_plane_background_latest.json"
PERFORMANCE = Path("output") / "run_performance_history.csv"
FALLBACK = Path("output") / "fallback_research_latest.json"
SESSION_REPORT = Path("output") / "paper_session" / "session_report_latest.json"
OK_RUN = {"completed", "completed_review_required"}
FAILED_RUN = {"failed", "interrupted", "stopped_by_owner"}
NEW_YORK = ZoneInfo("America/New_York")

# first match wins
PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("start_failure", re.compile(r"0xC0000142")),
    ("rate_limit", re.compile(r"\b429\b|rate limit|Too Many Requests", re.IGNORECASE)),
    ("traceback", re.compile(r"Traceback \(most recent call last\)")),
    ("not_started", re.compile(r"No run started|Full run did not start")),
    ("interrupted", re.compile(r"INTERRUPTED")),
)


@dataclass
class StepStatus:
    name: str
    state: str  # ok | failed | missing | running | unknown
    detail: str
    diagnosis: str | None = None
    evidence: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {"name": self.name, "state": self.state, "detail": self.detail, "diagnosis": self.diagnosis,
                "evidence": list(self.evidence)}


def read_json(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def diagnose(log_text: str) -> tuple[str | None, str | None]:
    """(diagnosis, the relevant line) from a launcher log; (None, None) when no known pattern is there."""
    lines = log_text.splitlines()
    for name, pattern in PATTERNS:
        for index, line in enumerate(lines):
            if not pattern.search(line):
                continue
            if name == "traceback":
                after = [x.strip() for x in lines[index + 1:] if x.strip()]
                return name, after[-1] if after else line.strip()
            return name, line.strip()
    return None, None


def _newest(root: Path, pattern: str) -> Path | None:
    files = sorted((root / "logs").glob(pattern))
    return files[-1] if files else None


def _read(path: Path | None) -> str:
    if path is None:
        return ""
    try:
        return path.read_text(encoding="utf-8-sig", errors="replace")
    except OSError:
        return ""


def _last_step(root: Path, run_id: str) -> str | None:
    try:
        with (root / PERFORMANCE).open(encoding="utf-8-sig", newline="") as handle:
            rows = [r for r in csv.DictReader(handle) if r.get("run_id") == run_id]
    except OSError:
        return None
    return (rows[-1].get("last_step") or None) if rows else None


def _started_day(pointer: dict[str, Any]) -> date | None:
    try:
        return datetime.fromisoformat(str(pointer["started_at"])).astimezone(NEW_YORK).date()
    except (KeyError, ValueError, TypeError):
        return None


def nightly_health(root: Path, expected_day: date | None = None) -> list[StepStatus]:
    """With ``expected_day`` (New York date the nightly should have started), a pointer still naming an older run
    means no run started that night: reported as failed with the log's diagnosis, never as last night's ok."""
    pointer_path = root / POINTER
    pointer = read_json(pointer_path)
    if pointer is None:
        reason = "λείπει το αρχείο κατάστασης του run" if not pointer_path.exists() else "το αρχείο κατάστασης δεν διαβάζεται"
        return [StepStatus("run", "unknown", reason, evidence=[str(POINTER)])]
    run_id, status = str(pointer.get("run_id") or "άγνωστο"), str(pointer.get("status") or "")
    log = _newest(root, "nightly_research_*.log")
    evidence = [str(POINTER)] + ([str(log.relative_to(root))] if log else [])
    if expected_day is not None:
        started = _started_day(pointer)
        if started is None:
            return [StepStatus("run", "unknown", f"το run {run_id} δεν γράφει πότε ξεκίνησε", evidence=evidence)]
        if started < expected_day:
            diagnosis, line = diagnose(_read(log))
            detail = (f"δεν ξεκίνησε run για {expected_day.isoformat()}: το τελευταίο ({run_id}) ξεκίνησε "
                      f"{started.isoformat()}")
            if line:
                detail += f" — {line}"
            return [StepStatus("run", "failed", detail, diagnosis or "not_started", evidence)]
    steps: list[StepStatus] = []
    if status in OK_RUN:
        steps.append(StepStatus("run", "ok", f"το run {run_id} ολοκληρώθηκε ({status})", evidence=evidence))
    elif status in FAILED_RUN:
        diagnosis, line = diagnose(_read(log))
        step = _last_step(root, run_id)
        detail = f"το run {run_id} είναι {status}, στο βήμα {step or 'άγνωστο'}"
        if line:
            detail += f": {line}"
        steps.append(StepStatus("run", "failed", detail, diagnosis, evidence + [str(PERFORMANCE)]))
        fallback = read_json(root / FALLBACK)
        if fallback is None:
            steps.append(StepStatus("fallback", "unknown", "δεν βρέθηκε το αποτέλεσμα της εφεδρικής έρευνας",
                                    evidence=[str(FALLBACK)]))
        else:
            fstate = str(fallback.get("status") or "")
            steps.append(StepStatus("fallback", "ok" if fstate in OK_RUN else "failed",
                                    f"εφεδρική έρευνα: {fstate or 'άγνωστο'}", evidence=[str(FALLBACK)]))
        return steps
    elif not status:
        steps.append(StepStatus("run", "unknown", f"το run {run_id} δεν γράφει κατάσταση", evidence=evidence))
    else:
        steps.append(StepStatus("run", "running", f"το run {run_id} είναι ακόμα {status}", evidence=evidence))

    errors = pointer.get("technical_errors")
    if errors is None:
        steps.append(StepStatus("technical_errors", "unknown", "δεν αναφέρονται τεχνικά σφάλματα", evidence=[str(POINTER)]))
    else:
        steps.append(StepStatus("technical_errors", "ok" if int(errors) == 0 else "failed",
                                f"τεχνικά σφάλματα: {errors}", evidence=[str(POINTER)]))
    audit = pointer.get("agent_audit_status")
    if audit is None:
        steps.append(StepStatus("agent_audit", "unknown", "δεν αναφέρεται ο έλεγχος των agents", evidence=[str(POINTER)]))
    else:
        steps.append(StepStatus("agent_audit", "ok" if audit == "pass" else "failed", f"έλεγχος agents: {audit}",
                                evidence=[str(POINTER)]))
    return steps


def session_health(root: Path, session_date: str) -> list[StepStatus]:
    steps: list[StepStatus] = []
    log = _newest(root, f"afternoon_session_{session_date.replace('-', '')}_*.log")
    if log is None:
        steps.append(StepStatus("session", "unknown", f"δεν βρέθηκε log απογευματινής συνεδρίας για {session_date}"))
    else:
        text = _read(log)
        rel = str(log.relative_to(root))
        stopped = re.search(r"Afternoon session stopped by an error: (.*)", text)
        no_orders = re.search(r"No paper orders today: (.*)", text)
        if stopped:
            diagnosis, line = diagnose(text)
            steps.append(StepStatus("session", "failed", f"η συνεδρία σταμάτησε από σφάλμα: {stopped.group(1).strip()}",
                                    diagnosis, [rel]))
        elif no_orders:
            steps.append(StepStatus("session", "ok", f"χωρίς εντολές σήμερα: {no_orders.group(1).strip()}",
                                    evidence=[rel]))
        else:
            steps.append(StepStatus("session", "ok", "η συνεδρία έτρεξε", evidence=[rel]))

    report = read_json(root / SESSION_REPORT)
    if report is None or str(report.get("session_date")) != session_date:
        steps.append(StepStatus("session_report", "missing", f"δεν υπάρχει αναφορά συνεδρίας για {session_date}",
                                evidence=[str(SESSION_REPORT)]))
    else:
        day = report.get("day_result") or {}
        average = day.get("average_result_pct")
        steps.append(StepStatus("session_report", "ok",
                                f"θέσεις {day.get('positions', 'άγνωστο')}, μέσο αποτέλεσμα "
                                f"{'άγνωστο' if average is None else f'{average}%'}", evidence=[str(SESSION_REPORT)]))
    return steps
