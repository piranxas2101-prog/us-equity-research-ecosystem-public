"""Bridge from the daily-bar strategies to the traders' signal format.

A trader playbook whose name is a ``daily_strategies`` strategy is scored from the stored daily prices of the symbol
(``price_history_store``, updated for the shortlist before the traders run). The trader then builds its usual signal
from the long and short scores with its own finalize step: same verdict thresholds, same instrument and risk rules,
same catalyst lens and the same fundamental gate afterwards. Without a current price history (missing, or its last
session older than five days) both scores are 0 with the reason stated: no signal is made up.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import daily_strategies as ds
import price_history_store as phs

MAX_STALE_DAYS = 5


@dataclass
class StrategyScore:
    long_score: float
    short_score: float
    long_rationale: list[str]
    short_rationale: list[str]
    meta: dict[str, Any] = field(default_factory=dict)


def is_daily_strategy(playbook: dict[str, Any]) -> bool:
    return str(playbook.get("name") or "") in ds.STRATEGIES


def score(playbook: dict[str, Any], row: dict[str, Any], *, directory: Path = phs.DEFAULT_DIR,
          today: date | None = None) -> StrategyScore:
    name = str(playbook["name"])
    strategy = ds.STRATEGIES[name]
    ticker = str(row.get("ticker") or "").upper().strip()
    today = today or date.today()
    history = phs.load(ticker, directory) if ticker else None
    current = history is not None and history.index.max().date() >= today - timedelta(days=MAX_STALE_DAYS)
    result = ds.score_latest(name, history[["Open", "High", "Low", "Close", "Volume"]].dropna()) if current else None
    meta = {"strategy_engine": "daily_strategies", "strategy_horizon_days": strategy.horizon_days,
            "strategy_session": None if result is None else result["session"], "strategy_source": strategy.library_doc}
    if result is None:
        reason = ("No current daily price history is stored for this symbol; the strategy cannot be judged."
                  if not current else f"Fewer than {strategy.min_history} sessions of history; the strategy cannot be judged.")
        return StrategyScore(0.0, 0.0, [reason], [reason], meta)
    base = f"{strategy.description} ({strategy.library_doc}); scored on the session {result['session']}."
    long_score, short_score = float(result["long_score"]), float(result["short_score"])
    return StrategyScore(long_score, short_score, [base, f"Long rule score {long_score:.2f}."],
                         [base, f"Short rule score {short_score:.2f}."], meta)


def playbooks_for(trader: str) -> list[dict[str, Any]]:
    """Playbook entries for the daily strategies a trader runs (appended to the trader's playbook file at load)."""
    return [{"name": s.name, "style": "daily_bar_strategy", "library_doc": s.library_doc, "description": s.description,
             "engine": "daily_strategies", "horizon_days": s.horizon_days, "sides": list(s.sides),
             "approved_sources": [], "rules": {}}
            for s in ds.STRATEGIES.values() if s.trader == trader]
