"""What waits for the owner (owner 2026-11-17: "Jarvis gives the approvals I have to give for the ecosystem").

Every open decision of the ecosystem in one list, read-only:

* Jarvis's own proposals (lessons with a verdict, ready checks, the debate's veto, run resumes) - the Jarvis page;
* classification reviews where the sources disagree - Control Center -> Έλεγχος κατηγοριοποίησης;
* trained models still `candidate` - their promotion is the owner's (manual promotion), done with Claude;
* due checks that have no lab yet - built with the owner and Claude.

A source that cannot be read is reported as unknown, never as "nothing waiting".
"""
from __future__ import annotations

from contextlib import closing
import json
from pathlib import Path
import sqlite3
from typing import Any

REASON_GR = {"no_two_sources_agree": "όπου διαφωνούν οι πηγές",
             "probable_spac_needs_second_signal": "πιθανά SPAC που θέλουν δεύτερη ένδειξη",
             "would_contradict_prior_approval": "που θα αντέβαιναν προηγούμενη έγκρισή σου"}
ITEMS_SHOWN = 5


def _query(path: Path, sql: str) -> list[tuple] | None:
    if not path.exists():
        return None
    try:
        with closing(sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)) as conn:
            return conn.execute(sql).fetchall()
    except sqlite3.Error:
        return None


def _proposals(root: Path) -> dict[str, Any]:
    import jarvis
    import jarvis_state

    path = root / "data" / "jarvis" / "proposals.sqlite3"
    group = {"kind": "proposals", "where": "σελίδα Jarvis (Ναι / Όχι / Αργότερα)"}
    if not path.exists():
        return {**group, "count": 0, "items": []}
    with closing(jarvis_state.connect(path)) as conn:
        latest = conn.execute("SELECT MAX(updated_session) FROM proposals").fetchone()[0] or ""
        rows = jarvis_state.open_proposals(conn, session_date=latest, sessions_since=jarvis.sessions_since(root))
    return {**group, "count": len(rows), "items": [str(r["title"]) for r in rows]}


def _classification(root: Path) -> dict[str, Any]:
    rows = _query(root / "data" / "research_registry.db",
                  "SELECT reason, COUNT(*) FROM classification_review_item WHERE status='open' GROUP BY reason")
    group = {"kind": "classification", "where": "Κέντρο Ελέγχου → Έλεγχος κατηγοριοποίησης"}
    if rows is None:
        return {**group, "count": None, "items": []}
    return {**group, "count": sum(n for _, n in rows),
            "items": [f"{n} {REASON_GR.get(reason, reason)}" for reason, n in sorted(rows, key=lambda r: -r[1])]}


def _models(root: Path) -> dict[str, Any]:
    rows = _query(root / "data" / "market_intel.db",
                  "SELECT agent_id, COUNT(*) FROM agent_model_registry WHERE state='candidate' GROUP BY agent_id")
    group = {"kind": "models", "where": "προαγωγή με το Claude (candidate → shadow → champion)"}
    if rows is None:
        return {**group, "count": None, "items": []}
    return {**group, "count": sum(n for _, n in rows), "items": [f"{n} του {agent}" for agent, n in rows]}


def _labs(root: Path) -> dict[str, Any]:
    group = {"kind": "labs", "where": "να τα χτίσουμε μαζί (εσύ και το Claude)"}
    try:
        report = json.loads((root / "output" / "jarvis" / "report_latest.json").read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return {**group, "count": None, "items": []}
    results = ((report.get("sections") or {}).get("checks") or {}).get("results") or []
    items = [str(r.get("title") or r.get("id")) for r in results if r.get("state") == "needs_lab"]
    return {**group, "count": len(items), "items": items}


def pending(root: Path) -> dict[str, Any]:
    groups = []
    for build in (_proposals, _classification, _models, _labs):
        try:
            groups.append(build(root))
        except Exception as exc:  # noqa: BLE001 - one unreadable source stays unknown, the rest still show
            groups.append({"kind": build.__name__.strip("_"), "count": None, "items": [], "where": "",
                           "error": f"{type(exc).__name__}: {str(exc)[:200]}"})
    known = [g["count"] for g in groups if g["count"] is not None]
    return {"groups": groups, "total": sum(known), "unknown": len(groups) - len(known)}


def _phrase(group: dict[str, Any]) -> str | None:
    n, kind, items = group["count"], group["kind"], group["items"]
    if n is None:
        names = {"proposals": "οι προτάσεις του Jarvis", "classification": "ο έλεγχος κατηγοριοποίησης",
                 "models": "τα εκπαιδευμένα μοντέλα", "labs": "τα εργαστήρια"}
        return f"{names.get(kind, kind)}: άγνωστο (δεν διαβάστηκε)"
    if n == 0:
        return None
    if kind == "proposals":
        head = f"{n} πρόταση του Jarvis" if n == 1 else f"{n} προτάσεις του Jarvis"
        shown = "· ".join(items[:ITEMS_SHOWN]) + (" κ.ά." if len(items) > ITEMS_SHOWN else "")
        return f"{head} ({shown}) στη {group['where']}"
    if kind == "classification":
        return f"{n} σύμβολα στον έλεγχο κατηγοριοποίησης ({', '.join(items)}) στο {group['where']}"
    if kind == "models":
        head = "1 εκπαιδευμένο μοντέλο" if n == 1 else f"{n} εκπαιδευμένα μοντέλα"
        return f"{head} σε αναμονή προαγωγής ({', '.join(items)}), {group['where']}"
    head = "1 εργαστήριο" if n == 1 else f"{n} εργαστήρια"
    return f"{head} για ελέγχους που έφτασαν στην ώρα τους ({'; '.join(items)}), {group['where']}"


def story(result: dict[str, Any]) -> str:
    parts = [p for p in (_phrase(g) for g in result["groups"]) if p]
    if not parts:
        return "Δεν περιμένει τίποτα από σένα αυτή τη στιγμή."
    waiting = sum(1 for g in result["groups"] if g["count"])
    head = "Περιμένουν εσένα " + ("1 είδος απόφασης" if waiting == 1 else f"{waiting} είδη αποφάσεων")
    return head + ": " + "; ".join(parts) + "."
