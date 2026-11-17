"""Jarvis facts: questions about numbers answered straight from the ecosystem's files, without the model.

Owner 2026-11-16: the small local model took the market numbers right but rephrased one wrongly ("near the 200-day
average" for "+3.1% above it"). So a question about the market, the session, the lessons in shadow or the nightly run
is answered here, deterministically, with the files' own sentences and numbers; only other questions go to the model.
A missing file is answered as unknown, never sent to the model to guess.
"""
from __future__ import annotations

from datetime import date
import json
from pathlib import Path
import re
from typing import Any
import unicodedata

MARKET_FILE = "output/market_state_latest.json"
SESSION_FILE = "output/paper_session/session_report_latest.json"
EVALUATION_FILE = "output/paper_learning/evaluation_latest.json"
JARVIS_REPORT_FILE = "output/jarvis/report_latest.json"
POINTER_FILE = "output/full_research_control_plane_background_latest.json"
MARKET_SENTENCES = 3
LESSONS_SHOWN = 8
STRATEGIES_SHOWN = 6

AGENTS = {"miltos": ("miltos", "μιλτο"), "koulis": ("koulis", "κουλη"), "yankos": ("yankos", "γιανκο", "γιαγκο"),
          "periklis": ("periklis", "περικλ"), "sokratis": ("sokratis", "σωκρατ"), "thymios": ("thymios", "θυμιο"),
          "babis": ("babis", "μπαμπη"), "giannis": ("giannis", "γιαννη"), "petros": ("petros", "πετρο")}
RULE_WORDS = ("κανον", "rule")
LESSON_WORDS = ("μαθ", "διδαγ", "shadow", "lesson")  # not "κανον": a rule question goes to the model + search
SESSION_WORDS = ("συνεδρι", "θεσει", "θεση", "κερδ", "ζημι", "στρατηγικ")
MARKET_WORDS = ("αγορ", "spy", "κλιμα")
NIGHTLY_WORDS = ("nightly", "νυχτεριν", "run")
OWNER_WORDS = ("εγκρισ", "εγκρινω", "εκκρεμ", "αποφασεις", "περιμενουν", "τι περιμενει")  # not "αποφασ":
# "τι αποφάσισα" asks about a past decision (model + search), "τι αποφάσεις" about open ones (2026-11-17)
VERDICT_GR = {"collecting": "μαζεύει δείγμα", "confirmed": "επιβεβαιώθηκε", "not_confirmed": "δεν επιβεβαιώθηκε"}
VERDICT_PLURAL_GR = {"collecting": "μαζεύουν δείγμα", "confirmed": "επιβεβαιώθηκαν", "not_confirmed": "δεν επιβεβαιώθηκαν"}


def fold(text: str) -> str:
    decomposed = unicodedata.normalize("NFD", text.lower())
    return "".join(c for c in decomposed if unicodedata.category(c) != "Mn")


def _has(folded: str, words: tuple[str, ...]) -> bool:
    return any(re.search(rf"(?<!\w){re.escape(w)}", folded) for w in words)


def _read(root: Path, rel: str) -> dict[str, Any] | None:
    try:
        value = json.loads((root / rel).read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def _result(lines: list[str], sources: list[str]) -> dict[str, Any]:
    return {"answer": "\n".join(lines), "sources": sources, "unverified_numbers": [], "ollama": "not_needed",
            "ollama_started": False, "mode": "facts"}


def _unknown(rel: str) -> dict[str, Any]:
    return _result([f"άγνωστο: δεν υπάρχει ακόμα το {rel}, οπότε δεν έχω αριθμούς γι' αυτό."], [rel])


def _pct(value: Any) -> str:
    return "άγνωστο" if value is None else f"{value}%"


def market(root: Path) -> dict[str, Any]:
    state = _read(root, MARKET_FILE)
    if state is None:
        return _unknown(MARKET_FILE)
    paragraph = str(state.get("paragraph") or "")
    sentences = re.split(r"(?<=[.;])\s+", paragraph.strip())
    head = " ".join(s for s in sentences[:MARKET_SENTENCES] if s)
    return _result([f"Αγορά ({state.get('session') or 'άγνωστο'}): {head or 'άγνωστο'}"], [MARKET_FILE])


def session(root: Path) -> dict[str, Any]:
    report = _read(root, SESSION_FILE)
    if report is None:
        return _unknown(SESSION_FILE)
    return _result([session_story(report)], [SESSION_FILE])


WEEKDAY_GR = ("Τη Δευτέρα", "Την Τρίτη", "Την Τετάρτη", "Την Πέμπτη", "Την Παρασκευή", "Το Σάββατο", "Την Κυριακή")


def _signed(value: Any) -> str:
    return "άγνωστο" if not isinstance(value, (int, float)) or isinstance(value, bool) else f"{value:+g}%"


def _when(session_date: Any) -> str:
    try:
        day = date.fromisoformat(str(session_date))
    except ValueError:
        return "Στη συνεδρία"
    return f"{WEEKDAY_GR[day.weekday()]} {day.day}/{day.month}"


def _group(row: dict[str, Any]) -> str:
    return f"τα {row.get('direction')} του {str(row.get('agent') or '').capitalize()} στο {row.get('playbook')}"


def _outcome(row: dict[str, Any]) -> str:
    if row.get("closed") == 1:
        return f"1 θέση με {_signed(row.get('average_pct'))}, {'κερδισμένη' if row.get('winners') else 'χαμένη'}"
    return (f"{row.get('closed')} θέσεις με μέσο {_signed(row.get('average_pct'))} "
            f"({row.get('winners')} κερδισμένες)")


def session_story(report: dict[str, Any]) -> str:
    """The day told in sentences (owner 2026-11-16: "say it naturally, not as a list of the agents' results"), with
    the report's own numbers; the only derived word is better / worse than the market."""
    day = report.get("day_result") or {}
    when = _when(report.get("session_date"))
    positions = day.get("positions")
    if positions is None:
        return f"{when}: άγνωστο πόσες θέσεις κλείσαμε (η αναφορά δεν το γράφει)."
    if not positions:
        return f"{when} δεν κλείσαμε καμία θέση."
    sentences = [f"{when} κλείσαμε {positions} θέσεις: οι {day.get('winners', 'άγνωστο')} κέρδισαν και οι "
                 f"{day.get('losers', 'άγνωστο')} έχασαν, με μέσο αποτέλεσμα {_signed(day.get('average_result_pct'))}."]
    spy, average = report.get("spy_open_to_close_pct"), day.get("average_result_pct")
    if isinstance(spy, (int, float)) and isinstance(average, (int, float)):
        compared = ("πήγαμε καλύτερα από την αγορά" if average > spy else
                    "πήγαμε χειρότερα από την αγορά" if average < spy else "πήγαμε όσο και η αγορά")
        sentences.append(f"Η αγορά (SPY) έκανε {_signed(spy)} από το άνοιγμα στο κλείσιμο, οπότε {compared}.")
    rows = [r for r in report.get("per_strategy") or []
            if r.get("closed") and isinstance(r.get("average_pct"), (int, float))]
    if len(rows) == 1:
        sentences.append(f"Όλες ήταν {_group(rows[0])}: {_outcome(rows[0])}.")
    elif rows:
        best = max(rows, key=lambda r: r["average_pct"])
        worst = min(rows, key=lambda r: r["average_pct"])
        sentences.append(f"Το καλύτερο ήταν {_group(best)}: {_outcome(best)}.")
        sentences.append(f"Το χειρότερο ήταν {_group(worst)}: {_outcome(worst)}.")
        rest = [r for r in rows if r is not best and r is not worst]
        shown = rest[:STRATEGIES_SHOWN]
        if shown:
            parts = [f"{_group(r)} έκαναν {_outcome(r)}" for r in shown]
            joined = parts[0] if len(parts) == 1 else "· ".join(parts[:-1]) + ", και " + parts[-1]
            more = f" (και {len(rest) - len(shown)} ακόμα στρατηγικές)" if len(rest) > len(shown) else ""
            sentences.append(f"Από τα υπόλοιπα, {joined}{more}.")
    return " ".join(sentences)


def _about(item: dict[str, Any], agent: str) -> bool:
    criterion = f":{item.get('criterion_id') or ''}:"
    conditions = item.get("conditions") or {}
    return item.get("target_agent") == agent or f":{agent}:" in criterion or conditions.get("agent") == agent


def lessons(root: Path, agent: str | None) -> dict[str, Any]:
    evaluation = _read(root, EVALUATION_FILE)
    if evaluation is None:
        return _unknown(EVALUATION_FILE)
    items = [dict(i, source="paper learning") for i in evaluation.get("shadow_follow") or [] if isinstance(i, dict)]
    sources = [EVALUATION_FILE]
    jarvis = _read(root, JARVIS_REPORT_FILE)
    follow = (((jarvis or {}).get("sections") or {}).get("learning") or {}).get("follow") or []
    if follow:
        items += [dict(i, source="Jarvis") for i in follow if isinstance(i, dict)]
        sources.append(JARVIS_REPORT_FILE)
    if agent:
        items = [i for i in items if _about(i, agent)]
    counts: dict[str, int] = {}
    for item in items:
        counts[str(item.get("verdict"))] = counts.get(str(item.get("verdict")), 0) + 1
    who = f" για τον {agent}" if agent else ""
    lines = [f"{len(items)} διδάγματα σε shadow{who}: "
             + ", ".join(f"{n} {VERDICT_PLURAL_GR.get(v, v)}" for v, n in sorted(counts.items())) + "."
             if items else f"Κανένα δίδαγμα σε shadow{who}."]

    def progress(item: dict[str, Any]) -> float:
        need = item.get("minimum_sessions") or item.get("minimum_trades")
        done = item.get("sessions_after") if item.get("minimum_sessions") else item.get("n")
        return (done or 0) / need if need else 0.0

    for item in sorted(items, key=progress, reverse=True)[:LESSONS_SHOWN]:
        if item.get("minimum_sessions"):
            where = f"{item.get('sessions_after')}/{item.get('minimum_sessions')} συνεδρίες"
        else:
            where = f"{item.get('losses')}/{item.get('n')} χαμένες"
        lines.append(f"- {item.get('proposed_change')} — {VERDICT_GR.get(item.get('verdict'), item.get('verdict'))}, "
                     f"{where} από {item.get('shadow_since') or 'άγνωστο'} ({item['source']})")
    return _result(lines, sources)


def nightly(root: Path) -> dict[str, Any]:
    pointer = _read(root, POINTER_FILE)
    if pointer is None:
        return _unknown(POINTER_FILE)
    return _result([f"Νυχτερινό run {pointer.get('run_id') or 'άγνωστο'}: {pointer.get('status') or 'άγνωστο'}, "
                    f"ξεκίνησε {pointer.get('started_at') or 'άγνωστο'}, τελείωσε {pointer.get('finished_at') or 'άγνωστο'}, "
                    f"τεχνικά σφάλματα {pointer.get('technical_errors', 'άγνωστο')}, έλεγχος agents "
                    f"{pointer.get('agent_audit_status') or 'άγνωστο'}."], [POINTER_FILE])


def answer_facts(question: str, root: Path) -> dict[str, Any] | None:
    """A fact answer, or None when the question is not about numbers the files hold (then the model answers)."""
    folded = fold(question)
    agent = next((name for name, words in AGENTS.items() if _has(folded, words)), None)
    if _has(folded, OWNER_WORDS):  # what waits for the owner (owner 2026-11-17)
        import jarvis_owner

        return _result([jarvis_owner.story(jarvis_owner.pending(root))],
                       ["data/jarvis/proposals.sqlite3", "data/research_registry.db", "data/market_intel.db",
                        "output/jarvis/report_latest.json"])
    if _has(folded, RULE_WORDS):  # how a rule works or why it was decided: the model with the document search
        return None
    if _has(folded, LESSON_WORDS):
        return lessons(root, agent)
    import jarvis_memory  # past days (owner 2026-11-17): a question that names a period

    if _has(folded, NIGHTLY_WORDS):
        story = jarvis_memory.nightly_story(root, folded)
        if story is not None:
            return _result([story], [jarvis_memory.RUNS_FILE.as_posix()])
    else:
        story = jarvis_memory.period_story(root, folded)
        if story is not None:
            return _result([story], [jarvis_memory.SESSIONS_SOURCE])
    if _has(folded, SESSION_WORDS):
        return session(root)
    if _has(folded, MARKET_WORDS):
        return market(root)
    if _has(folded, NIGHTLY_WORDS):
        return nightly(root)
    return None
