"""Valuation gaps: a rule whose value has no meaning yet fails, and the ecosystem keeps looking for the value.

Owner's decision 2026-10-22 (the LAES report): a negative forward P/E (losses expected) used to pass the
"forward P/E <= 35" rule and lifted the score of every loss-making company. Now a rule may declare
``invalid_at_or_below``: an observed value at or below it is not a valuation, so the rule counts as **failed**
(never passed) and a gap is opened for that company and field. The gap is a task, not a verdict:

* every night the stage-1 info cache asks Yahoo afresh for a company with an open gap instead of reusing a stored
  copy (``Stage1InfoCache.decide``), so a new analyst estimate is seen at once;
* when Yahoo gives no forward P/E but a positive forward EPS, the forward P/E is built from price / forward EPS;
* the night a meaningful value is found, the rule is judged on it as for any company, the gap is closed with the
  date and the value, and the analysis is complete.

State: ``data/valuation_gaps.json`` (ticker -> field -> opened, last_seen, nights, observed, status, resolved).
"""
from __future__ import annotations

from datetime import date
import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
STATE = ROOT / "data" / "valuation_gaps.json"
REASON = "value_without_meaning"  # e.g. a forward P/E of a company expected to lose money


def invalid(rule: dict[str, Any], observed: float | None) -> bool:
    """True when the rule says values at or below a level are not a valuation (``invalid_at_or_below``)."""
    limit = rule.get("invalid_at_or_below")
    return isinstance(limit, (int, float)) and isinstance(observed, (int, float)) and observed <= limit


class Gaps:
    def __init__(self, path: Path = STATE, today: date | None = None) -> None:
        self.path, self.today = Path(path), today or date.today()
        try:
            self.state: dict[str, dict[str, dict[str, Any]]] = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self.state = {}

    def open_tickers(self) -> frozenset[str]:
        return frozenset(t for t, fields in self.state.items()
                         if any(g.get("status") == "open" for g in fields.values()))

    def observe(self, ticker: str, details: list[dict[str, Any]]) -> None:
        """Open, keep or close the gaps of one company from tonight's rule details."""
        ticker = ticker.upper()
        for detail in details:
            field = str(detail.get("metric") or "")
            if detail.get("gap") == REASON:
                entry = self.state.setdefault(ticker, {}).get(field)
                if entry is None or entry.get("status") != "open":
                    entry = {"opened": str(self.today), "nights": 0, "status": "open"}
                entry.update({"last_seen": str(self.today), "nights": int(entry.get("nights", 0)) + 1,
                              "observed": detail.get("observed")})
                self.state[ticker][field] = entry
            elif detail.get("status") in ("passed", "failed"):
                entry = (self.state.get(ticker) or {}).get(field)
                if entry and entry.get("status") == "open":
                    entry.update({"status": "resolved", "resolved": str(self.today),
                                  "resolved_value": detail.get("observed"), "resolved_status": detail.get("status")})

    def save(self) -> dict[str, int]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.state, indent=1, sort_keys=True), encoding="utf-8")
        statuses = [g.get("status") for fields in self.state.values() for g in fields.values()]
        return {"open": statuses.count("open"), "resolved": statuses.count("resolved")}
