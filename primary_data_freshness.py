"""Is a trader row's primary data fresh enough to trade? The one rule shared by Koulis and the paper trader.

Owner's decision 2026-10-15. The old flag ``fresh_primary_data`` means "any SEC filing in the last 30 days": a
company that is quiet between two quarterly reports fails it although its fundamentals are current. On the first
paper session it blocked 117 candidates (85 companies), every one of them with a current 10-Q/10-K. Giannis
already judges fundamental freshness by the age of the last periodic report (``fundamentals_fresh``, limits in
``periodic_filing.freshness_limit``); that is the criterion here. Unknown stays unknown: when the periodic age was
not known (``fundamentals_fresh`` missing or None) the old 30-day flag decides, as before.
"""
from __future__ import annotations

from typing import Any


RISK_LEVELS = ("Low", "Medium", "High")
TRADABLE_RISK = ("Low", "Medium")
UNKNOWN_RISK = "Unknown"


def declared_fresh(row: dict[str, Any]) -> bool | None:
    """The 30-day flag as Giannis wrote it: True, False, or None when it is missing (code review 2026-11-20: the
    traders read a missing flag as fresh and wrote True, so Koulis's "unknown is not fresh" never saw the gap)."""
    value = row.get("fresh_primary_data")
    return value if isinstance(value, bool) else None


def risk_level(row: dict[str, Any]) -> str:
    """Low / Medium / High, or ``Unknown`` when missing or unreadable - never a default "Medium" (code review
    2026-11-20). An unknown risk level is scored like a high one and is never a candidate or an order."""
    value = str(row.get("risk_level") or "").strip().capitalize()
    return value if value in RISK_LEVELS else UNKNOWN_RISK


def primary_data_fresh(row: dict[str, Any]) -> bool:
    periodic = row.get("fundamentals_fresh")
    if periodic is True:
        return True
    if periodic is False:
        return False
    return bool(row.get("fresh_primary_data", False))
