"""Positions held beyond the session they opened in (ADR-001 step 3; owner 2026-10-23: the agents decide which
positions open and for how long, and the monitors close them whenever their rules say so).

* The horizon is the agent's: its frozen trade plan carries ``holding_period_rule = agent_horizon`` and a
  ``holding_horizon`` (holding_horizon.py) in trading minutes. A plan without one is a same-day position.
* Registry ``data/paper_positions.json``: every filled position with a valid agent horizon, with what its monitor
  needs - fill time, the moment its horizon ends (``trading_clock``: nights, weekends, holidays and early closes
  skipped), ATR(14) and Giannis's score at entry (for the widening stop and the thesis check). A position in the
  account that is not in the registry is a leftover: the preflight closes it at the next session (owner
  2026-10-23, after four Miltos entries of 10/22 were left open with no monitor).
* Unknown stays unknown: without an ATR(14) no stop can be set and without a fill time no horizon can be counted;
  such a position is not registered and stays same-day (``register`` returns False, flat 5 minutes before close).
* Closed positions leave the registry and are appended to ``data/paper_positions_closed.jsonl`` with the reason.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import time
from typing import Any, Callable, Iterator

import holding_horizon
import trading_clock

ROOT = Path(__file__).resolve().parent
REGISTRY = ROOT / "data" / "paper_positions.json"
CLOSED = ROOT / "data" / "paper_positions_closed.jsonl"


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


def register(entries: dict[str, dict[str, Any]], opening: dict[str, Any], *, agent: str,
             row: dict[str, Any] | None = None) -> bool:
    """Add a filled opening with a valid agent horizon once (keyed by symbol). False when the plan is same-day, the
    symbol is known, or the ATR(14) or the fill time is unknown. ``row``: the trader's strategy row at entry."""
    symbol = str(opening.get("ticker") or "").upper()
    plan = opening.get("paper_trade_plan") if isinstance(opening.get("paper_trade_plan"), dict) else {}
    horizon = plan.get("holding_horizon")
    if not symbol or symbol in entries or not holding_horizon.valid(horizon):
        return False
    row = row or {}
    atr = _float(opening.get("atr_14_pct")) or _float(row.get("atr_14_pct"))
    order = opening.get("refreshed_order_response") or opening.get("order_response") or {}
    filled_at = order.get("filled_at")
    if atr is None or atr <= 0 or not filled_at:
        return False
    filled = datetime.fromisoformat(str(filled_at).replace("Z", "+00:00"))
    expires = trading_clock.add_trading_minutes(filled, horizon["minutes"])
    entries[symbol] = {
        "symbol": symbol, "agent": agent, "playbook": opening.get("playbook_name"),
        "direction": str(opening.get("trade_direction") or "long").lower(),
        "horizon_sessions": horizon["sessions"], "horizon_minutes": horizon["minutes"],
        "holding_class": holding_horizon.rules_class(horizon["sessions"]), "horizon_basis": horizon.get("basis"),
        "filled_at": str(filled_at), "expires_at": expires.isoformat(),
        "opened_session": str(filled.astimezone(trading_clock.EASTERN).date()),
        "entry_price": _float(order.get("filled_avg_price")), "qty": order.get("filled_qty") or order.get("qty"),
        "atr_14_pct": atr, "giannis_score": _float(row.get("giannis_score")), "risk_level": row.get("risk_level"),
        "registered_at": datetime.now(timezone.utc).isoformat(), "weak_nights": 0, "last_thesis_check": None,
    }
    return True


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
