"""Jarvis's memory of past days (owner 2026-11-17; the idea of OpenJarvis's monitor agent: earlier days compressed and
recalled when asked).

The ecosystem already keeps its history: one session report per day (output/paper_session/session_report_*.json)
and one row per nightly run (output/run_performance_history.csv). This module reads them as a list of compact day
records and answers questions about a period - this or last week, this or last month, the last N sessions, everything
- in sentences, with the files' own numbers, without the model. A period with no data is said plainly.
"""
from __future__ import annotations

import csv
from datetime import date, datetime, timedelta
import json
from pathlib import Path
import re
from typing import Any

SESSION_GLOB = "session_report_2*.json"
SESSION_DIR = Path("output") / "paper_session"
RUNS_FILE = Path("output") / "run_performance_history.csv"
SESSIONS_SOURCE = "output/paper_session/session_report_*.json"
MIN_STRATEGY_POSITIONS = 3
MONTHS_ACC = ("τον Ιανουάριο", "τον Φεβρουάριο", "τον Μάρτιο", "τον Απρίλιο", "τον Μάιο", "τον Ιούνιο", "τον Ιούλιο",
              "τον Αύγουστο", "τον Σεπτέμβριο", "τον Οκτώβριο", "τον Νοέμβριο", "τον Δεκέμβριο")
WEEKDAY_NOM = ("η Δευτέρα", "η Τρίτη", "η Τετάρτη", "η Πέμπτη", "η Παρασκευή", "το Σάββατο", "η Κυριακή")
FAILED = {"failed", "interrupted", "stopped_by_owner"}


def _dm(day: date) -> str:
    return f"{day.day}/{day.month}"


def _signed(value: float) -> str:
    return f"{round(value, 2):+g}%"


def sessions(root: Path) -> list[dict[str, Any]]:
    """One compact record per session report, oldest first; a report that does not read is skipped."""
    out = []
    for path in sorted((root / SESSION_DIR).glob(SESSION_GLOB)):
        try:
            report = json.loads(path.read_text(encoding="utf-8-sig"))
            day = date.fromisoformat(str(report["session_date"]))
        except (OSError, ValueError, KeyError):
            continue
        result = report.get("day_result") or {}
        out.append({"session_date": day.isoformat(), "day": day, "positions": result.get("positions"),
                    "with_result": result.get("with_result"), "average": result.get("average_result_pct"),
                    "winners": result.get("winners"), "losers": result.get("losers"),
                    "spy": report.get("spy_open_to_close_pct"), "strategies": report.get("per_strategy") or []})
    return out


def runs(root: Path) -> list[dict[str, Any]]:
    try:
        with (root / RUNS_FILE).open(encoding="utf-8-sig", newline="") as handle:
            rows = list(csv.DictReader(handle))
    except OSError:
        return []
    out = []
    for row in rows:
        try:
            started = datetime.fromisoformat(str(row.get("started_at"))).date()
        except ValueError:
            continue
        out.append({"run_id": row.get("run_id"), "day": started, "status": row.get("status") or "",
                    "last_step": row.get("last_step") or ""})
    return out


def period(folded: str, latest: date) -> tuple[date, date, str, int | None] | None:
    """The period a question names, relative to the latest session; None when it names none. ``folded`` is lower
    case without accents (``jarvis_facts.fold``); any text is folded again here."""
    from jarvis_facts import fold

    folded = fold(folded)
    previous = bool(re.search(r"προηγουμεν|περασμεν", folded))
    match = re.search(r"τελευται\w*\s+(\d+)", folded)
    if match:
        n = int(match.group(1))
        return date.min, latest, ("στην τελευταία συνεδρία" if n == 1 else f"στις τελευταίες {n} συνεδρίες"), n
    if "εβδομαδ" in folded or "week" in folded:
        monday = latest - timedelta(days=latest.weekday())
        if previous:
            start, end = monday - timedelta(days=7), monday - timedelta(days=1)
            return start, end, f"την εβδομάδα {_dm(start)} έως {_dm(end)}", None
        return monday, latest, f"την εβδομάδα από τις {_dm(monday)}", None
    if "μηνα" in folded or "month" in folded:
        first = latest.replace(day=1)
        if previous:
            end = first - timedelta(days=1)
            return end.replace(day=1), end, MONTHS_ACC[end.month - 1], None
        return first, latest, MONTHS_ACC[first.month - 1], None
    if re.search(r"απο την αρχη|συνολικα|ολες τις συνεδρι", folded):
        return date.min, latest, "από την αρχή", None
    return None


def _in(records: list[dict[str, Any]], span: tuple[date, date, str, int | None]) -> tuple[list[dict[str, Any]], str]:
    start, end, label, last_n = span
    if last_n is not None:
        return [r for r in records if r["day"] <= end][-last_n:], label
    return [r for r in records if start <= r["day"] <= end], label


def _group(row: dict[str, Any]) -> str:
    return f"τα {row['direction']} του {str(row['agent']).capitalize()} στο {row['playbook']}"


def period_story(root: Path, folded: str) -> str | None:
    records = sessions(root)
    if not records:
        return None
    span = period(folded, records[-1]["day"])
    if span is None:
        return None
    chosen, label = _in(records, span)
    if not chosen:
        return f"{label[0].upper() + label[1:]}: δεν βρήκα συνεδρίες στο ιστορικό."
    known = [r for r in chosen if isinstance(r.get("average"), (int, float)) and r.get("with_result")]
    counted = [r for r in chosen if all(isinstance(r.get(k), int) for k in ("positions", "winners", "losers"))]
    uncounted = len(chosen) - len(counted)  # a day whose report has no counts is said unknown, never added as zero
    positions = sum(r["positions"] for r in counted)
    winners = sum(r["winners"] for r in counted)
    losers = sum(r["losers"] for r in counted)
    weight = sum(r["with_result"] for r in known)
    average = sum(r["average"] * r["with_result"] for r in known) / weight if weight else None
    sessions_part = "" if span[3] is not None else (
        "1 συνεδρία με " if len(chosen) == 1 else f"{len(chosen)} συνεδρίες με ")
    sentence = (f"{label[0].upper() + label[1:]} είχαμε {sessions_part}{positions} θέσεις: κέρδισαν οι "
                f"{winners} και έχασαν οι {losers}, με μέσο αποτέλεσμα "
                f"{_signed(average) if average is not None else 'άγνωστο'} ανά θέση")
    compared = [r for r in known if isinstance(r.get("spy"), (int, float))]
    if compared:
        beat = sum(1 for r in compared if r["average"] > r["spy"])
        if len(compared) == 1:
            sentence += (", και πήγαμε " + ("καλύτερα" if beat else "χειρότερα") +
                         " από την αγορά (SPY) εκείνη τη μέρα")
        else:
            sentence += f", και σε {beat} από τις {len(compared)} μέρες πήγαμε καλύτερα από την αγορά (SPY)"
    if uncounted:
        sentence += (f" (σε {uncounted} μέρα το πλήθος των θέσεων είναι άγνωστο)" if uncounted == 1 else
                     f" (σε {uncounted} μέρες το πλήθος των θέσεων είναι άγνωστο)")
    sentences = [sentence + "."]
    if len(known) > 1:
        best = max(known, key=lambda r: r["average"])
        worst = min(known, key=lambda r: r["average"])
        sentences.append(f"Η καλύτερη μέρα ήταν {WEEKDAY_NOM[best['day'].weekday()]} {_dm(best['day'])} "
                         f"({_signed(best['average'])}) και η χειρότερη {WEEKDAY_NOM[worst['day'].weekday()]} "
                         f"{_dm(worst['day'])} ({_signed(worst['average'])}).")
    totals: dict[tuple, list[float]] = {}
    for record in chosen:
        for row in record["strategies"]:
            if row.get("closed") and isinstance(row.get("average_pct"), (int, float)):
                key = (row.get("agent"), row.get("playbook"), row.get("direction"))
                cell = totals.setdefault(key, [0, 0.0])
                cell[0] += row["closed"]
                cell[1] += row["closed"] * row["average_pct"]
    groups = [{"agent": k[0], "playbook": k[1], "direction": k[2], "closed": n, "average": s / n}
              for k, (n, s) in totals.items() if n >= MIN_STRATEGY_POSITIONS]
    if len(groups) > 1:
        best = max(groups, key=lambda g: g["average"])
        worst = min(groups, key=lambda g: g["average"])
        sentences.append(f"Η καλύτερη στρατηγική ήταν {_group(best)}: {best['closed']} θέσεις με μέσο "
                         f"{_signed(best['average'])}· η χειρότερη {_group(worst)}: {worst['closed']} θέσεις με μέσο "
                         f"{_signed(worst['average'])}.")
    return " ".join(sentences)


def nightly_story(root: Path, folded: str) -> str | None:
    history = runs(root)
    if not history:
        return None
    span = period(folded, history[-1]["day"])
    if span is None:
        return None
    chosen, label = _in(history, span)
    if not chosen:
        return f"{label[0].upper() + label[1:]}: δεν βρήκα νυχτερινά runs στο ιστορικό."
    failed = [r for r in chosen if r["status"] in FAILED]
    done = sum(1 for r in chosen if r["status"].startswith("completed"))
    text = (f"{label[0].upper() + label[1:]} έτρεξαν {len(chosen)} νυχτερινά runs: {done} ολοκληρώθηκαν, "
            f"{len(failed)} απέτυχε" + ("" if len(failed) == 1 else "αν"))
    if failed:
        text += " (" + "· ".join(f"{_dm(r['day'])}" + (f", στο βήμα {r['last_step']}" if r["last_step"] else "")
                                for r in failed) + ")"
    return text + "."
