"""Jarvis due checks: the recurring re-judgements of docs/RULES_REVIEW.md, run when they come due.

The registry ``jarvis_checks.json`` lists each check: when it is due, which producer (if any) refreshes its output,
and where its verdict is read. Only producers in ``PRODUCERS`` can ever run (read-only labs); a registry naming
anything else is refused at load time. A check with neither producer nor output has no measurement yet
(``needs_lab``): the report says it is due, and the lab is built with Claude and the owner.
"""
from __future__ import annotations

from datetime import date
import json
from pathlib import Path
import subprocess
import time
from typing import Any, Callable

from jarvis_state import Proposal

PRODUCERS: dict[str, list[str]] = {
    "exit_lab_horizon": ["exit_lab.py", "--horizon-curve"],
    # owner 2026-11-16: the debate shadow's nightly measure (reads the ledger and its own opinions, writes a report)
    "debate_shadow_evaluate": ["debate_shadow.py", "evaluate"],
    # owner 2026-11-17: the positions a market shock closed, measured 1-10 sessions later (RULES_REVIEW section 7)
    "shock_exits_lab": ["shock_exits_lab.py"],
}
DEFAULT_WAITING = ("insufficient_sample", "collecting")
MIN_PRODUCER_SECONDS = 30
DUE_KINDS = {"every_pass", "weekly", "after_date", "sessions_since"}


def load_registry(path: Path) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    checks = list(payload.get("checks") or [])
    seen: set[str] = set()
    for check in checks:
        cid = str(check.get("id") or "")
        if not cid or cid in seen:
            raise ValueError(f"check id missing or repeated: {cid!r}")
        seen.add(cid)
        producer = check.get("producer")
        if producer is not None and producer not in PRODUCERS:
            raise ValueError(f"check {cid}: producer {producer!r} is not in the allow-list")
        if (check.get("due") or {}).get("kind") not in DUE_KINDS:
            raise ValueError(f"check {cid}: unknown due kind")
    return checks


def is_due(check: dict[str, Any], *, today: date, is_saturday_nightly: bool,
           sessions_since: Callable[[str], int]) -> bool:
    due = check.get("due") or {}
    kind = due.get("kind")
    if kind == "every_pass":
        return True
    if kind == "weekly":
        return is_saturday_nightly
    if kind == "after_date":
        return today.isoformat() >= str(due["date"])
    if kind == "sessions_since":
        return sessions_since(str(due["date"])) >= int(due["sessions"])
    return False


def _dig(value: Any, dotted: str) -> Any:
    for part in dotted.split("."):
        if not isinstance(value, dict):
            return None
        value = value.get(part)
    return value


def _result(check: dict[str, Any], state: str, verdict: Any = None, detail: str = "",
            evidence: list[str] | None = None) -> dict[str, Any]:
    return {"id": check["id"], "title": check.get("title") or check["id"], "rules_review": check.get("rules_review"),
            "state": state, "verdict": verdict, "detail": detail, "evidence": evidence or []}


def run_check(check: dict[str, Any], *, root: Path, python: str, deadline: float,
              runner: Callable[..., Any] = subprocess.run, clock: Callable[[], float] = time.time) -> dict[str, Any]:
    producer, output = check.get("producer"), check.get("output")
    if producer is None and output is None:
        return _result(check, "needs_lab", detail="δεν υπάρχει ακόμα εργαστήριο που να το μετρά")
    if producer is not None:
        remaining = deadline - clock()
        if remaining < MIN_PRODUCER_SECONDS:
            return _result(check, "skipped_budget", detail="δεν έφτανε ο χρόνος· περιμένει το επόμενο πέρασμα")
        args = [python, *PRODUCERS[producer]]
        try:
            done = runner(args, cwd=str(root), capture_output=True, text=True,
                          timeout=min(float(check.get("timeout_seconds") or 300), remaining))
        except subprocess.TimeoutExpired:
            return _result(check, "timeout", detail="το εργαστήριο ξεπέρασε τον χρόνο του και σταμάτησε")
        except OSError as exc:
            return _result(check, "failed", detail=f"δεν ξεκίνησε: {exc}")
        if done.returncode != 0:
            tail = (done.stderr or done.stdout or "").strip().splitlines()[-1:] or [""]
            return _result(check, "failed", detail=f"κωδικός εξόδου {done.returncode}: {tail[0][:200]}")
    path = root / str(output)
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return _result(check, "unknown", detail=f"δεν διαβάζεται το {output}", evidence=[str(output)])
    if check.get("report_only"):
        return _result(check, "ready", "refreshed", "ανανεώθηκε", [str(output)])
    verdict = _dig(payload, str(check.get("verdict_path") or "verdict"))
    if verdict is None:
        return _result(check, "unknown", detail="το αρχείο δεν γράφει ετυμηγορία", evidence=[str(output)])
    if verdict in (check.get("waiting_values") or DEFAULT_WAITING):
        return _result(check, "waiting", verdict, "μαζεύει ακόμα δείγμα", [str(output)])
    if check.get("ready_values") is not None and verdict not in check["ready_values"]:
        return _result(check, "waiting", verdict, "χωρίς εύρημα που να ζητά απόφαση", [str(output)])
    return _result(check, "ready", verdict, "έτοιμο για κρίση", [str(output)])


def check_proposal(result: dict[str, Any], check: dict[str, Any]) -> Proposal | None:
    if result.get("state") != "ready" or check.get("report_only"):
        return None
    return Proposal(source="check", source_key=check["id"],
                    title=f"{check.get('title') or check['id']}: ετυμηγορία «{result.get('verdict')}»",
                    evidence={"verdict": result.get("verdict"), "files": result.get("evidence") or []},
                    change=str(check.get("change") or "Απόφαση για το επόμενο βήμα του κανόνα"),
                    rules_review=check.get("rules_review"))
