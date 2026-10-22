"""Shortlist membership: names enter and leave by rules over time, not by re-applying one cut-off every night.

Babis used to keep a company only if its stage-1 score reached one absolute threshold, re-tested every night, so a
name sitting near it flipped in and out (109 Giannis tier changes in five days, many of them such flips). Membership
works like an index: a company ENTERS at the entry threshold and, once a member, STAYS until it falls below a lower
exit threshold or stops being eligible (route, price, coverage, history). The gap between the two is the buffer that
stops daily flipping. Every addition and removal is recorded with its date and reason.

The state file only records membership. It never changes a score; a missing or unreadable file means everyone is a
new name (entry threshold for all), which is the behaviour before this module existed.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

SCHEMA = "ecosystem.shortlist_membership.v1"
KEEP_EVENTS = 1000


def load(path: Path) -> dict[str, Any]:
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        state = None
    if not isinstance(state, dict) or state.get("schema") != SCHEMA or not isinstance(state.get("members"), dict):
        return {"schema": SCHEMA, "updated_at": None, "members": {}, "events": []}
    state.setdefault("events", [])
    return state


def save(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(state, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    temporary.replace(path)


def active_tickers(state: dict[str, Any]) -> set[str]:
    return {ticker for ticker, member in state["members"].items() if member.get("status") == "active"}


def threshold_for(was_member: bool, entry: float | None, exit_: float | None) -> float | None:
    """The score a company must reach tonight: the entry threshold, or the lower exit threshold for a member."""
    if entry is None:
        return None
    if was_member and exit_ is not None:
        return min(entry, exit_)
    return entry


def update(state: dict[str, Any], shortlist: list[str], excluded: dict[str, str], run_ts: str) -> list[dict[str, Any]]:
    """Record tonight's shortlist. Returns the events (additions and removals) of this run.

    ``excluded`` maps a ticker to the reason it was scanned and dropped. A previous member that is neither in the
    shortlist nor excluded was not scanned tonight (provider error, deferred): it stays a member, unchanged.
    """
    members = state["members"]
    events: list[dict[str, Any]] = []
    tonight = set(shortlist)
    for ticker in sorted(tonight):
        member = members.get(ticker)
        if member is None or member.get("status") != "active":
            members[ticker] = {"status": "active", "since": run_ts, "last_seen": run_ts}
            events.append({"at": run_ts, "ticker": ticker, "event": "added"})
        else:
            member["last_seen"] = run_ts
    for ticker in sorted(active_tickers(state) - tonight):
        reason = excluded.get(ticker)
        if reason is None:
            members[ticker]["not_scanned_at"] = run_ts
            continue
        members[ticker].update({"status": "removed", "removed_at": run_ts, "reason": reason})
        events.append({"at": run_ts, "ticker": ticker, "event": "removed", "reason": reason})
    state["updated_at"] = run_ts
    state["events"] = (list(state.get("events") or []) + events)[-KEEP_EVENTS:]
    return events
