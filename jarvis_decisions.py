"""The owner's approvals go through Jarvis (owner 2026-11-18): proposals about lessons, and carrying out a "yes".

A proposal may carry an ``action`` in its evidence. The only kind is ``lesson_status``: move one or more lessons
along the learning store's allowed transitions (``agent_learning_control_plane.LESSON_TRANSITIONS``). Jarvis carries
it out on its next pass **only** after the owner answered "yes" on the Control Center page, records the result once
(table ``applied`` in Jarvis's proposal store) and lists it in the daily report. Lessons are REVIEW in
``jarvis_tiers`` - never AUTO - so nothing here moves by itself.

What changes code - making a lesson live, a period's extension, the next period's pre-registration - carries no
action: the "yes" is recorded and done with Claude, with tests.

Proposals from the measurement period (``period_experiments.py``):

* an experiment once judged: ``passed`` (make it live, with Claude), ``failed`` (action: the lessons -> rejected),
  ``insufficient_sample`` (one extension, with Claude);
* once the period is judged: the lessons waiting for a period, oldest first (at most ``NEXT_PERIOD_PROPOSALS``),
  each "for the next period" - the yes is the owner's choice; the pre-registration is written with Claude.
"""
from __future__ import annotations

from contextlib import closing
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
from typing import Any

import jarvis_health
import jarvis_tiers
from jarvis_state import Proposal

ACTION_KIND = "lesson_status"
NEXT_PERIOD_PROPOSALS = 10
PERIOD_OUTPUT = Path("output") / "paper_learning" / "period_experiments_latest.json"
PERIOD_CONFIG = "period_experiments.json"
VERDICT_TITLE = {"passed": "Πέρασε", "failed": "Δεν πέρασε", "insufficient_sample": "Λίγο δείγμα"}


def lesson_action(lesson_ids: list[str], to: str) -> dict[str, Any]:
    return {"kind": ACTION_KIND, "lesson_ids": sorted(str(i) for i in lesson_ids), "to": to}


def period_lesson_ids(root: Path) -> set[str]:
    """The lessons the current measurement period judges (they are not judged again by the older follow)."""
    try:
        config = json.loads((root / PERIOD_CONFIG).read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return set()
    return {str(i) for e in config.get("experiments") or [] for i in e.get("lesson_ids") or []}


def period_proposals(root: Path) -> list[Proposal]:
    result = jarvis_health.read_json(root / PERIOD_OUTPUT) or {}
    if result.get("baseline"):
        return []
    period = (result.get("period") or {}).get("id") or "?"
    out = []
    for exp in result.get("experiments") or []:
        verdict = exp.get("verdict")
        if not exp.get("final") or verdict not in VERDICT_TITLE:
            continue
        evidence = {"verdict": verdict, "run_id": period, "criterion_id": exp.get("id"),
                    "sessions": exp.get("sessions"), "mean_daily_difference_pct": exp.get("mean_daily_difference_pct"),
                    "p_holm": exp.get("p_holm"), "lesson_ids": exp.get("lesson_ids"),
                    "file": str(PERIOD_OUTPUT).replace("\\", "/")}
        if verdict == "failed":
            evidence["action"] = lesson_action(exp.get("lesson_ids") or [], "rejected")
            change = "Αν πεις ναι: ο Jarvis το απορρίπτει στο επόμενο πέρασμα (μένει στο ιστορικό)"
        elif verdict == "passed":
            change = "Να γίνει ενεργό (με tests, μαζί με το Claude)"
        else:
            change = "Μία παράταση για μία ακόμα περίοδο (η προεγγραφή με το Claude)"
        out.append(Proposal(source="period_experiment", source_key=f"{period}:{exp.get('id')}",
                            title=f"Περίοδος {period}, {exp.get('id')} {exp.get('title')}: {VERDICT_TITLE[verdict]}",
                            evidence=evidence, change=change, rules_review="section 14"))
    if result.get("verdict") == "judged":
        import agent_learning_control_plane

        waiting = agent_learning_control_plane.waiting_lesson_items(
            root / "data" / "market_intel.db", ("paper_trading", "jarvis_learning", "earnings_journal"))
        for item in waiting[:NEXT_PERIOD_PROPOSALS]:
            out.append(Proposal(
                source="next_period", source_key=f"{period}:{item['lesson_id']}",
                title=f"Για την επόμενη περίοδο: {item.get('proposed_change') or item['criterion_id']}",
                evidence={"verdict": "waiting", "run_id": period, "criterion_id": item["criterion_id"],
                          "waiting_since": item.get("waiting_since"), "task_id": item.get("task_id")},
                change="Αν πεις ναι: μπαίνει στην επόμενη περίοδο μέτρησης (έως 5· η προεγγραφή με το Claude)",
                rules_review="section 14"))
    return out


def _ensure(conn: sqlite3.Connection) -> None:
    conn.execute("CREATE TABLE IF NOT EXISTS applied (proposal_id TEXT PRIMARY KEY, at TEXT NOT NULL,"
                 " result TEXT NOT NULL, detail_json TEXT NOT NULL)")
    conn.commit()


def apply_approved(root: Path, *, proposals_db: Path | None = None) -> list[dict[str, Any]]:
    """Carry out every approved proposal with a lesson action that was not carried out yet; one row per proposal.
    A transition the learning store refuses is recorded as refused (never forced) and shown in the report."""
    db = proposals_db or root / "data" / "jarvis" / "proposals.sqlite3"
    if not db.exists():
        return []
    if jarvis_tiers.tier_for("lesson") != "REVIEW":  # lessons are the owner's decision, never AUTO
        raise RuntimeError("lesson changes must be REVIEW")
    import agent_learning_control_plane as learning
    import market_intel_db

    done: list[dict[str, Any]] = []
    with closing(sqlite3.connect(db)) as conn:
        _ensure(conn)
        rows = conn.execute(
            "SELECT p.proposal_id, p.title, p.evidence_json, p.decided_session FROM proposals p"
            " LEFT JOIN applied a USING(proposal_id) WHERE p.state='approved' AND a.proposal_id IS NULL"
            " ORDER BY p.proposal_id").fetchall()
        for pid, title, evidence_json, decided in rows:
            action = (json.loads(evidence_json or "{}") or {}).get("action")
            if not isinstance(action, dict) or action.get("kind") != ACTION_KIND:
                continue
            reason = f"owner via Jarvis ({decided}): {title}"
            results = []
            with closing(market_intel_db.connect(root / "data" / "market_intel.db")) as intel:
                learning.init_schema(intel)
                for lesson_id in action.get("lesson_ids") or []:
                    try:
                        learning.set_lesson_status(intel, lesson_id, str(action.get("to")), reason=reason)
                        results.append({"lesson_id": lesson_id, "to": action.get("to"), "result": "applied"})
                    except (KeyError, ValueError) as exc:
                        results.append({"lesson_id": lesson_id, "to": action.get("to"), "result": "refused",
                                        "why": str(exc)})
            outcome = "applied" if results and all(r["result"] == "applied" for r in results) else "refused"
            conn.execute("INSERT INTO applied VALUES (?,?,?,?)", (pid, datetime.now(timezone.utc).isoformat(),
                                                                 outcome, json.dumps(results, ensure_ascii=False)))
            conn.commit()
            done.append({"proposal_id": pid, "title": title, "result": outcome, "lessons": results})
    return done
