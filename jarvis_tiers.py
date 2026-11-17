"""Risk tiers for Jarvis's own changes (owner 2026-11-17; after OpenJarvis's risk_tier / pending_queue / regression).

A fixed table - never a model - gives each kind of change its tier:

* AUTO   - only Jarvis's own chat settings, inside fixed bounds. Jarvis changes them by itself, writes every change in
           the daily report, and a regression check undoes a change that made the timeouts worse.
* REVIEW - everything else: lessons, checks, debate verdicts, run resumes. These are the proposals the owner answers
           with yes / no / later. A kind this table does not know is REVIEW.

Anything that touches scores, limits, strategies, orders or positions is in ``NEVER_AUTO`` and can never be AUTO.
"""
from __future__ import annotations

from contextlib import closing
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import statistics
from typing import Any

TIER_TABLE = {"chat_setting": "AUTO"}
NEVER_AUTO = ("score", "limit", "threshold", "risk", "strategy", "playbook", "lesson", "order", "position", "veto")
BOUNDS = {"max_total_chars": (800, 2400)}
SETTINGS = Path("data") / "jarvis" / "settings.json"
CHAT_DB = Path("data") / "jarvis" / "chat.sqlite3"
WINDOW = 20             # model answers judged per step
TIMEOUTS_TO_LOWER = 3   # of WINDOW
FAST_SECONDS = 30.0     # median answer time that allows a raise (and no timeouts)
LOWER, RAISE = 0.8, 1.1


def tier_for(kind: str) -> str:
    if kind in NEVER_AUTO:
        return "REVIEW"
    return TIER_TABLE.get(kind, "REVIEW")


def clamp(setting: str, value: int) -> int:
    low, high = BOUNDS[setting]
    return max(low, min(high, int(value)))


def _load(root: Path) -> dict[str, Any]:
    try:
        value = json.loads((root / SETTINGS).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"history": []}
    return value if isinstance(value, dict) else {"history": []}


def _save(root: Path, state: dict[str, Any]) -> None:
    path = root / SETTINGS
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def effective(root: Path, setting: str, base: int) -> int:
    """The setting Jarvis uses now: its own tuned value inside the bounds, else the configured one."""
    value = _load(root).get(setting)
    return clamp(setting, value) if isinstance(value, int) else base


def _model_turns(root: Path) -> list[tuple[int, str, float | None]]:
    path = root / CHAT_DB
    if not path.exists():
        return []
    try:
        with closing(sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)) as conn:
            return [(r[0], r[1], r[2]) for r in conn.execute(
                "SELECT rowid, outcome, seconds FROM chat WHERE mode='model' AND outcome IN ('ok', 'timeout')"
                " ORDER BY rowid")]
    except sqlite3.Error:
        return []


def tune(root: Path, *, base: int) -> list[dict[str, Any]]:
    """One AUTO step for the chat's text budget (``max_total_chars``) from the last WINDOW model answers."""
    setting = "max_total_chars"
    state = _load(root)
    turns = _model_turns(root)
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    pending = state.get("pending")
    if pending:
        after = [t for t in turns if t[0] > pending["rowid"]][:WINDOW]
        if len(after) < WINDOW:
            return []
        rate = sum(1 for t in after if t[1] == "timeout") / WINDOW
        state.pop("pending")
        state["last_rowid"] = after[-1][0]
        if rate > pending["before_rate"]:  # the regression check: the change made timeouts worse
            change = {"at": now, "setting": setting, "old": pending["new"], "new": pending["old"], "action": "undone",
                      "tier": tier_for("chat_setting"),
                      "reason": f"μετά την αλλαγή έληξαν {rate:.0%} των απαντήσεων, πριν {pending['before_rate']:.0%}"}
            state[setting] = pending["old"]
            state.setdefault("history", []).append(change)
            _save(root, state)
            return [change]
        _save(root, state)
        return []
    window = [t for t in turns if t[0] > state.get("last_rowid", 0)][-WINDOW:]
    if len(window) < WINDOW:
        return []
    timeouts = sum(1 for t in window if t[1] == "timeout")
    seconds = [t[2] for t in window if isinstance(t[2], (int, float))]
    current = effective(root, setting, base)
    if timeouts >= TIMEOUTS_TO_LOWER:
        new, action = clamp(setting, int(current * LOWER)), "lowered"
        reason = f"{timeouts} από τις {WINDOW} απαντήσεις του μοντέλου έληξαν"
    elif timeouts == 0 and seconds and statistics.median(seconds) < FAST_SECONDS:
        new, action = clamp(setting, int(current * RAISE)), "raised"
        reason = f"καμία δεν έληξε, μέσος χρόνος {statistics.median(seconds):.0f} δευτ."
    else:
        return []
    if new == current:
        return []
    change = {"at": now, "setting": setting, "old": current, "new": new, "action": action,
              "tier": tier_for("chat_setting"), "reason": reason}
    state[setting] = new
    state["pending"] = {"old": current, "new": new, "rowid": window[-1][0], "before_rate": timeouts / WINDOW}
    state.setdefault("history", []).append(change)
    _save(root, state)
    return [change]
