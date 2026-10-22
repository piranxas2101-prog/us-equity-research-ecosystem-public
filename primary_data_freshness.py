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


def primary_data_fresh(row: dict[str, Any]) -> bool:
    periodic = row.get("fundamentals_fresh")
    if periodic is True:
        return True
    if periodic is False:
        return False
    return bool(row.get("fresh_primary_data", False))
