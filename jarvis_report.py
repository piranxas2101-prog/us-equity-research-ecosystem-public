"""Jarvis's daily report: the pass record rendered as Greek markdown (for the owner) plus JSON (for the page)."""
from __future__ import annotations

from typing import Any

SECTIONS = ("Τι περιμένει από σένα", "Νυχτερινό run", "Συνεδρία", "Τι πήγε στραβά", "Έλεγχοι", "Η εικόνα του οικοσυστήματος", "Τι έμαθα",
            "Τι άλλαξα μόνος μου (AUTO)", "Προτάσεις που περιμένουν απόφαση")
ACTION_GR = {"lowered": "μείωσα", "raised": "αύξησα", "undone": "αναίρεσα"}
PROBLEM_STEP_STATES = {"failed", "missing"}
PROBLEM_CHECK_STATES = {"failed", "timeout"}
STATE_GR = {"ok": "εντάξει", "failed": "απέτυχε", "missing": "λείπει", "running": "τρέχει ακόμα",
            "unknown": "άγνωστο", "ready": "έτοιμο για κρίση", "waiting": "μαζεύει δείγμα",
            "needs_lab": "χρειάζεται εργαστήριο", "timeout": "ξεπέρασε τον χρόνο", "skipped_budget": "αναβλήθηκε (χρόνος)"}
DIAGNOSIS_GR = {"rate_limit": "όριο αιτήσεων παρόχου", "traceback": "σφάλμα Python", "start_failure":
                "τα Windows δεν ξεκίνησαν το πρόγραμμα (0xC0000142)", "not_started": "το run δεν ξεκίνησε",
                "interrupted": "το run διακόπηκε"}


def _steps_lines(section: dict[str, Any] | None, missing: str) -> list[str]:
    if not section:
        return [f"- άγνωστο: {missing}"]
    if section.get("error"):
        return [f"- άγνωστο: το κομμάτι απέτυχε ({section['error']})"]
    lines = []
    for step in section.get("steps") or []:
        line = f"- **{STATE_GR.get(step['state'], step['state'])}**: {step['detail']}"
        if step.get("diagnosis"):
            line += f" ({DIAGNOSIS_GR.get(step['diagnosis'], step['diagnosis'])})"
        lines.append(line)
    return lines or [f"- άγνωστο: {missing}"]


def _problems(sections: dict[str, Any]) -> list[str]:
    out = []
    for key in ("nightly", "session"):
        section = sections.get(key) or {}
        for step in section.get("steps") or []:
            if step["state"] in PROBLEM_STEP_STATES:
                text = step["detail"]
                if step.get("diagnosis"):
                    text += f" ({DIAGNOSIS_GR.get(step['diagnosis'], step['diagnosis'])})"
                out.append(text)
    for result in (sections.get("checks") or {}).get("results") or []:
        if result["state"] in PROBLEM_CHECK_STATES:
            out.append(f"έλεγχος {result['id']}: {result['detail']}")
    for key, section in sections.items():
        if isinstance(section, dict) and section.get("error"):
            out.append(f"το κομμάτι «{key}» του Jarvis απέτυχε: {section['error']}")
    return out


def _learning_lines(section: dict[str, Any] | None) -> list[str]:
    if not section:
        return ["- άγνωστο: δεν έτρεξε η μάθηση σε αυτό το πέρασμα"]
    if section.get("error"):
        return [f"- άγνωστο: η μάθηση απέτυχε ({section['error']})"]
    lines = []
    if section.get("status") == "not_enough_data":
        lines.append(f"- Λίγα δεδομένα ακόμα: {section.get('sessions')} συνεδρίες, χρειάζονται "
                     f"{section.get('minimum_sessions')}.")
    for rule in section.get("rules") or []:
        lines.append(f"- Νέος κανόνας σε shadow: {rule['criterion_id']} — εκπαίδευση {rule['train']['losses']}/"
                     f"{rule['train']['n']} χαμένες, έλεγχος {rule['test']['losses']}/{rule['test']['n']}")
    for item in section.get("follow") or []:
        lines.append(f"- Παρακολούθηση {item['criterion_id']}: {item['verdict']} "
                     f"({item.get('losses')}/{item.get('n')} χαμένες μετά το {item.get('shadow_since')})")
    if not lines:
        lines.append(f"- Τίποτα νέο ({section.get('sessions', 'άγνωστο')} συνεδρίες εξετάστηκαν).")
    return lines


def _ecosystem_lines(section: dict[str, Any] | None) -> list[str]:
    if not section:
        return ["- άγνωστο: δεν διαβάστηκε η εικόνα του οικοσυστήματος"]
    if section.get("error"):
        return [f"- άγνωστο: το κομμάτι απέτυχε ({section['error']})"]
    lines = []
    market = section.get("market")
    if market:
        lines.append(f"- Αγορά ({market.get('session') or 'άγνωστο'}): τάση {market.get('trend') or 'άγνωστο'}, "
                     f"διάθεση {market.get('mood') or 'άγνωστο'}. {market.get('paragraph') or ''}".rstrip())
    else:
        lines.append("- Αγορά: άγνωστο (δεν υπάρχει output/market_state_latest.json)")
    lessons = section.get("lessons")
    if lessons:
        counts = lessons.get("by_verdict") or {}
        lines.append(f"- Διδάγματα του paper learning σε shadow: {counts.get('collecting', 0)} μαζεύουν δείγμα, "
                     f"{counts.get('confirmed', 0)} επιβεβαιώθηκαν, {counts.get('not_confirmed', 0)} δεν επιβεβαιώθηκαν.")
        for item in lessons.get("closest") or []:
            lines.append(f"  - πιο κοντά σε ετυμηγορία: {item.get('proposed_change')} "
                         f"({item.get('sessions_after')}/{item.get('minimum_sessions')} συνεδρίες)")
    else:
        lines.append("- Διδάγματα σε shadow: άγνωστο (δεν υπάρχει η αξιολόγηση του paper learning)")
    return lines


def render(record: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    sections = record.get("sections") or {}
    problems = _problems(sections)
    proposals = (sections.get("proposals") or {}).get("open") or []
    out = [f"# Jarvis — αναφορά {record.get('day', 'άγνωστο')}", "",
           f"Πέρασμα: {record.get('kind', 'άγνωστο')}, {record.get('finished_at') or record.get('started_at') or ''}", ""]
    owner = sections.get("owner") or {}
    out += [f"## {SECTIONS[0]}", f"- {owner.get('story') or 'άγνωστο: δεν διαβάστηκε τι περιμένει από σένα'}", ""]
    out += [f"## {SECTIONS[1]}", *_steps_lines(sections.get("nightly"), "δεν διαβάστηκε η κατάσταση του nightly"), ""]
    session = sections.get("session")
    out += [f"## {SECTIONS[2]}"]
    if session and session.get("weekend"):
        out += ["- Σαββατοκύριακο: δεν υπάρχει συνεδρία."]
    else:
        out += _steps_lines(session, "δεν διαβάστηκε η συνεδρία")
    out += ["", f"## {SECTIONS[3]}", *([f"- {p}" for p in problems] or ["- Τίποτα."]), ""]
    checks = sections.get("checks")
    out += [f"## {SECTIONS[4]}"]
    if not checks:
        out += ["- άγνωστο: δεν έτρεξαν οι έλεγχοι"]
    elif checks.get("error"):
        out += [f"- άγνωστο: οι έλεγχοι απέτυχαν ({checks['error']})"]
    else:
        results = checks.get("results") or []
        out += [f"- **{r.get('title') or r['id']}**: {STATE_GR.get(r['state'], r['state'])}"
                + (f" — «{r['verdict']}»" if r.get("verdict") not in (None, "refreshed") else "")
                + (f" ({r['detail']})" if r.get("detail") else "") for r in results] or ["- Κανένας έλεγχος δεν ήταν σε προθεσμία."]
    out += ["", f"## {SECTIONS[5]}", *_ecosystem_lines(sections.get("ecosystem")), ""]
    out += [f"## {SECTIONS[6]}", *_learning_lines(sections.get("learning")), ""]
    tuning = sections.get("tuning") or {}
    changes = tuning.get("changes") or []
    out += [f"## {SECTIONS[7]}"]
    if tuning.get("error"):
        out += [f"- άγνωστο: η ρύθμιση απέτυχε ({tuning['error']})"]
    out += [f"- {ACTION_GR.get(c.get('action'), c.get('action'))} το {c.get('setting')}: {c.get('old')} → {c.get('new')} "
            f"({c.get('reason')})" for c in changes] or ["- Τίποτα (μόνο ρυθμίσεις της συζήτησης μπορούν να αλλάξουν μόνες τους)."]
    out += ["", f"## {SECTIONS[8]}"]
    if not proposals:
        out += ["- Καμία."]
    for p in proposals:
        evidence = ", ".join(f"{k}: {v}" for k, v in sorted((p.get("evidence") or {}).items()))
        out += [f"- **{p['title']}** — {p.get('change') or ''}", f"  - στοιχεία: {evidence or 'άγνωστο'}"]
    out.append("")
    data = {**record, "problems": len(problems), "problem_lines": problems, "open_proposals": len(proposals)}
    return "\n".join(out), data
