"""Jarvis's proposal store (design: docs/superpowers/specs/2026-11-15-jarvis-design.md).

A proposal is something only the owner can decide: a lesson whose out-of-sample follow reached a verdict, or a due
check that came out ready. Jarvis records it once per (source, source_key); the owner answers yes / no / later on the
Control Center page. Nothing here applies a decision: a "yes" is recorded here; a "yes" on a proposal with a lesson
action is carried out by Jarvis's next pass (``jarvis_decisions``, owner 2026-11-18), any other with Claude.

* no      - declined; Jarvis does not raise it again unless its evidence changes materially (fingerprint below).
* later   - asked again after ``LATER_SESSIONS`` sessions.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
from typing import Any, Callable

ROOT = Path(__file__).resolve().parent
PROPOSALS_DB = ROOT / "data" / "jarvis" / "proposals.sqlite3"
LATER_SESSIONS = 5
DECISIONS = {"yes": "approved", "no": "declined", "later": "later"}


@dataclass(frozen=True)
class Proposal:
    source: str  # "jarvis_learning" | "paper_learning" | "check"
    source_key: str  # lesson_id or check id
    title: str  # Greek, one line
    evidence: dict[str, Any] = field(default_factory=dict)  # numbers and file paths
    change: str = ""  # what would change if the owner says yes
    rules_review: str | None = None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect(path: Path = PROPOSALS_DB) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE IF NOT EXISTS proposals (proposal_id TEXT PRIMARY KEY, source TEXT NOT NULL,"
                 " source_key TEXT NOT NULL, title TEXT NOT NULL, change TEXT NOT NULL, rules_review TEXT,"
                 " evidence_json TEXT NOT NULL, fingerprint TEXT NOT NULL, state TEXT NOT NULL,"
                 " first_seen TEXT NOT NULL, updated_session TEXT NOT NULL, decided_session TEXT,"
                 " updated_at TEXT NOT NULL)")
    conn.execute("CREATE TABLE IF NOT EXISTS decisions (proposal_id TEXT NOT NULL, decision TEXT NOT NULL,"
                 " note TEXT NOT NULL, session_date TEXT NOT NULL, at TEXT NOT NULL)")
    conn.commit()
    return conn


def proposal_id(source: str, source_key: str) -> str:
    return "prop_" + hashlib.sha256(f"{source}|{source_key}".encode("utf-8")).hexdigest()[:16]


def _numbers(value: Any) -> list[Any]:
    if isinstance(value, bool) or value is None:
        return []
    if isinstance(value, (int, float)):
        return [round(float(value), 1)]
    if isinstance(value, dict):
        return [n for key in sorted(value) for n in _numbers(value[key])]
    if isinstance(value, (list, tuple)):
        return [n for item in value for n in _numbers(item)]
    return []


MATERIAL_KEYS = ("verdict", "criterion_id", "run_id", "diagnosis")
LOSS_RATE_BAND = 10.0  # percentage points


def evidence_fingerprint(evidence: dict[str, Any]) -> str:
    """What makes evidence materially new: a different verdict, criterion, run or diagnosis, or a loss rate that moved
    into another 10-point band. Counters that grow every session (sessions_after, n, losses) are not new evidence,
    so a "no" is not undone by the next pass (review 2026-11-16)."""
    material = {key: evidence.get(key) for key in MATERIAL_KEYS}
    rate = evidence.get("loss_rate_pct")
    material["loss_rate_band"] = (int(float(rate) // LOSS_RATE_BAND)
                                  if isinstance(rate, (int, float)) and not isinstance(rate, bool) else None)
    key = json.dumps(material, sort_keys=True, default=str)
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def upsert_proposal(conn: sqlite3.Connection, p: Proposal, *, session_date: str) -> tuple[str, str]:
    pid = proposal_id(p.source, p.source_key)
    fingerprint = evidence_fingerprint(p.evidence)
    payload = json.dumps(p.evidence, ensure_ascii=False, sort_keys=True)
    row = conn.execute("SELECT state, fingerprint FROM proposals WHERE proposal_id=?", (pid,)).fetchone()
    if row is None:
        conn.execute("INSERT INTO proposals VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                     (pid, p.source, p.source_key, p.title, p.change, p.rules_review, payload, fingerprint, "open",
                      session_date, session_date, None, _now()))
        conn.commit()
        return pid, "created"
    state, old = row
    if old == fingerprint:
        return pid, "suppressed" if state == "declined" else "unchanged"
    # new evidence: a declined or withdrawn proposal is open again; a postponed one keeps waiting its sessions with
    # the new evidence; an approved one keeps its state
    new_state = "open" if state in {"declined", "withdrawn"} else state
    conn.execute("UPDATE proposals SET title=?, change=?, rules_review=?, evidence_json=?, fingerprint=?, state=?,"
                 " updated_session=?, updated_at=? WHERE proposal_id=?",
                 (p.title, p.change, p.rules_review, payload, fingerprint, new_state, session_date, _now(), pid))
    conn.commit()
    return pid, "updated"


def decide(conn: sqlite3.Connection, proposal_id: str, decision: str, *, note: str, session_date: str) -> dict[str, Any]:
    if decision not in DECISIONS:
        raise ValueError("decision must be 'yes', 'no' or 'later'")
    if conn.execute("SELECT 1 FROM proposals WHERE proposal_id=?", (proposal_id,)).fetchone() is None:
        raise ValueError(f"unknown proposal: {proposal_id}")
    state = DECISIONS[decision]
    conn.execute("UPDATE proposals SET state=?, decided_session=?, updated_at=? WHERE proposal_id=?",
                 (state, session_date, _now(), proposal_id))
    conn.execute("INSERT INTO decisions VALUES (?,?,?,?,?)", (proposal_id, decision, note, session_date, _now()))
    conn.commit()
    return {"proposal_id": proposal_id, "decision": decision, "state": state}


def withdraw_stale(conn: sqlite3.Connection, *, source: str, seen: set[str] | None, ready: set[str],
                   session_date: str) -> list[str]:
    """Open or postponed proposals of ``source`` whose verdict no longer stands: the source reported on them this
    pass (``seen``; None = every key of the source) and they are not ready any more. They leave the page as
    ``withdrawn`` and come back as new evidence if their verdict returns."""
    rows = conn.execute("SELECT proposal_id, source_key FROM proposals WHERE source=? AND state IN ('open', 'later')",
                        (source,)).fetchall()
    out = [pid for pid, key in rows if (seen is None or key in seen) and key not in ready]
    for pid in out:
        conn.execute("UPDATE proposals SET state='withdrawn', updated_session=?, updated_at=? WHERE proposal_id=?",
                     (session_date, _now(), pid))
    conn.commit()
    return out


def _row(row: sqlite3.Row | tuple, columns: list[str]) -> dict[str, Any]:
    out = dict(zip(columns, row))
    out["evidence"] = json.loads(out.pop("evidence_json") or "{}")
    return out


def open_proposals(conn: sqlite3.Connection, *, session_date: str,
                   sessions_since: Callable[[str], int]) -> list[dict[str, Any]]:
    """Open proposals, and postponed ones whose "later" is ``LATER_SESSIONS`` or more sessions old."""
    cursor = conn.execute("SELECT * FROM proposals WHERE state IN ('open', 'later') ORDER BY first_seen, proposal_id")
    columns = [c[0] for c in cursor.description]
    out = []
    for row in cursor.fetchall():
        item = _row(row, columns)
        if item["state"] == "later" and sessions_since(item["decided_session"] or session_date) < LATER_SESSIONS:
            continue
        out.append(item)
    return out


def all_proposals(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    cursor = conn.execute("SELECT * FROM proposals ORDER BY first_seen, proposal_id")
    columns = [c[0] for c in cursor.description]
    return [_row(row, columns) for row in cursor.fetchall()]
