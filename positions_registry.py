"""Multi-day paper positions (ADR-001 step 3, owner's decision 2026-10-23): which holding class a position is opened
for, and the registry of the positions kept across sessions.

* Holding class per strategy: ``paper_session.holding_classes`` in scanner_config.json maps ``agent:playbook:direction``
  (or ``playbook``) to week / month / quarter. Anything not listed is a day position, flat by 15:55 as before; with
  the map empty nothing changes. Only the owner fills the map (a risk decision).
* Registry ``data/paper_positions.json``: every filled position of a multi-day class, with what its monitor needs -
  entry, session it opened, class length, ATR(14) and Giannis's score at entry (for the widening stop and the thesis
  check). A position in the account that is not in the registry is a leftover: the preflight closes it at the next
  session (owner 2026-10-23, after four Miltos entries of 10/22 were left open with no monitor).
* Unknown stays unknown: a position whose ATR(14) is unknown cannot get its widening stop, so it is not registered
  and stays a day position (``register`` returns False and the monitor flattens it by 15:55 as before).
* Closed positions leave the registry and are appended to ``data/paper_positions_closed.jsonl`` with the reason.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
import json
import os
from pathlib import Path
import time
from typing import Any, Callable, Iterator

ROOT = Path(__file__).resolve().parent
REGISTRY = ROOT / "data" / "paper_positions.json"
CLOSED = ROOT / "data" / "paper_positions_closed.jsonl"
CONFIG = ROOT / "scanner_config.json"
SESSIONS = {"day": 1, "week": 5, "month": 20, "quarter": 60}


def policy(config: Path = CONFIG) -> dict[str, str]:
    try:
        mapping = (json.loads(config.read_text(encoding="utf-8-sig")).get("paper_session") or {}).get("holding_classes")
    except (OSError, ValueError):
        mapping = None
    return {str(k): str(v) for k, v in (mapping or {}).items() if str(v) in SESSIONS}


def class_for(agent: str, playbook: str, direction: str, mapping: dict[str, str]) -> str:
    return mapping.get(f"{agent}:{playbook}:{direction}") or mapping.get(str(playbook)) or "day"


def load(path: Path = REGISTRY) -> dict[str, dict[str, Any]]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def save(entries: dict[str, dict[str, Any]], path: Path = REGISTRY) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(entries, indent=2, sort_keys=True), encoding="utf-8")
    temporary.replace(path)


@contextmanager
def locked(path: Path = REGISTRY, timeout: float = 30.0) -> Iterator[None]:
    """The two day monitors and the multi-day monitor write the same registry: one writer at a time. A lock older
    than ``timeout`` is a crashed writer's and is taken over."""
    lock = path.with_suffix(".lock")
    lock.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + timeout
    while True:
        try:
            os.close(os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY))
            break
        except FileExistsError:
            try:
                stale = time.time() - lock.stat().st_mtime > timeout
            except OSError:
                stale = False
            if stale or time.monotonic() > deadline:
                lock.unlink(missing_ok=True)
                continue
            time.sleep(0.2)
    try:
        yield
    finally:
        lock.unlink(missing_ok=True)


def update(change: Callable[[dict[str, dict[str, Any]]], Any], path: Path = REGISTRY) -> Any:
    """Load, change and save the registry under the lock; returns what ``change`` returns."""
    with locked(path):
        entries = load(path)
        result = change(entries)
        save(entries, path)
    return result


def register(entries: dict[str, dict[str, Any]], opening: dict[str, Any], *, agent: str, holding_class: str,
             today: date, row: dict[str, Any] | None = None) -> bool:
    """Add a filled multi-day opening once (keyed by symbol); False when it is a day position, already known, or its
    ATR(14) is unknown. ``row`` is the trader's strategy row for the ticker (ATR, Giannis's score, risk at entry)."""
    symbol = str(opening.get("ticker") or "").upper()
    if holding_class not in SESSIONS or holding_class == "day" or not symbol or symbol in entries:
        return False
    row = row or {}
    atr = _float(opening.get("atr_14_pct")) or _float(row.get("atr_14_pct"))
    if atr is None or atr <= 0:
        return False
    order = opening.get("refreshed_order_response") or opening.get("order_response") or {}
    entries[symbol] = {
        "symbol": symbol, "agent": agent, "playbook": opening.get("playbook_name"),
        "direction": str(opening.get("trade_direction") or "long").lower(), "holding_class": holding_class,
        "sessions": SESSIONS[holding_class], "opened_session": str(today),
        "entry_price": _float(order.get("filled_avg_price")), "qty": order.get("filled_qty") or order.get("qty"),
        "atr_14_pct": atr, "giannis_score": _float(row.get("giannis_score")), "risk_level": row.get("risk_level"),
        "registered_at": datetime.now(timezone.utc).isoformat(), "weak_nights": 0, "last_thesis_check": None,
    }
    return True


def sessions_held(opened: str, today: date) -> int:
    """Sessions since the opening session, counting today (the opening session is 1). Weekdays only: an exchange
    holiday counts as a session, so the time stop can come one session early, never late."""
    start = date.fromisoformat(opened)
    days = (today - start).days
    return 1 + sum(1 for offset in range(1, days + 1) if (start + timedelta(days=offset)).weekday() < 5)


def close(entries: dict[str, dict[str, Any]], symbol: str, reason: str, *, detail: dict[str, Any] | None = None,
          path: Path = CLOSED) -> None:
    entry = entries.pop(symbol, None)
    if entry is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    record = {**entry, "closed_at": datetime.now(timezone.utc).isoformat(), "close_reason": reason, **(detail or {})}
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, sort_keys=True) + "\n")


def _float(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number else None
