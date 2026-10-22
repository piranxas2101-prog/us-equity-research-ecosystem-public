"""Feature registry: one place where every data source offers its tags to the learning loops.

Owner's principle 2026-10-22: every datum must do a job, and the ecosystem should find and propose by itself. A
provider here turns one stored data source into tags for a ticker; ``tags_for`` merges them, and the paper-trade
ledger stores them in every record's categories. The category findings (``paper_learning.category_findings``) and
the results journal test every tag they see, so a provider added here is tested every night without further code.

Each provider: ``(name, prefix, source, loader)``; ``loader(output_dir) -> function(ticker, session) -> tags``.
A value that is not known is "unknown", never a guess. Providers only read stored files: no network.
"""
from __future__ import annotations

from contextlib import closing
from datetime import date, timedelta
import json
from pathlib import Path
import sqlite3
from typing import Any, Callable

Tags = Callable[[str, date], dict[str, str]]


def _text(value: Any) -> str:
    return "unknown" if value in (None, "") else str(value)


def periodic_results(out: Path) -> Tags:
    """The trend of the company's last reported periods (``periodic_results.py``)."""
    try:
        companies = json.loads((out / "periodic_results_latest.json").read_text(encoding="utf-8")).get("companies") or {}
    except (OSError, ValueError):
        companies = {}

    def tags(ticker: str, session: date) -> dict[str, str]:
        t = (companies.get(ticker) or {}).get("tags") or {}
        runway = t.get("cash_runway_months")
        last = ((companies.get(ticker) or {}).get("periods") or [{}])[-1]
        growth = last.get("revenue_yoy_pct")
        return {"results_revenue_trend": _text(t.get("revenue_trend")),
                "results_gross_margin": _text(t.get("gross_margin")),
                "results_growth_bucket": "unknown" if growth is None else "<-10%" if growth < -10 else
                "-10..0%" if growth < 0 else "0..10%" if growth < 10 else "10..25%" if growth < 25 else ">=25%",
                "results_operating_margin": _text(t.get("operating_margin")),
                "results_dilution": _text(t.get("dilution")),
                "results_losses": _text(t.get("losses")),
                "results_runway": "unknown" if runway is None else "<12m" if runway < 12 else
                "12-24m" if runway < 24 else ">=24m"}
    return tags


def recent_results(out: Path) -> Tags:
    """Whether the company reported in the week before the session, and what its press release said
    (``earnings_journal.py`` and ``press_release_reader.py``)."""
    path = out.parent / "data" / "earnings_journal.db"
    events: dict[str, list[tuple[str, str, dict[str, Any]]]] = {}
    if path.exists():
        try:
            with closing(sqlite3.connect(f"file:{path}?mode=ro", uri=True)) as conn:
                for ticker, filed, form, tags_json in conn.execute(
                        "SELECT ticker, filed, form, tags_json FROM earnings_event"):
                    events.setdefault(ticker, []).append((filed, form, json.loads(tags_json or "{}")))
        except sqlite3.Error:
            events = {}

    def tags(ticker: str, session: date) -> dict[str, str]:
        week = [(f, form, t) for f, form, t in events.get(ticker, [])
                if session - timedelta(days=7) <= date.fromisoformat(f) <= session]
        press = next((t for f, form, t in sorted(week, reverse=True)
                      if form in ("8-K 2.02", "6-K press release")), {})
        return {"results_this_week": "unknown" if not events else "yes" if week else "no",
                "pr_revenue_growth": _text(press.get("pr_revenue_growth")),
                "pr_profit": _text(press.get("pr_profit"))}
    return tags


PROVIDERS: tuple[tuple[str, str, Callable[[Path], Tags]], ...] = (
    ("periodic_results", "output/periodic_results_latest.json", periodic_results),
    ("recent_results", "data/earnings_journal.db", recent_results),
)


def load(out: Path) -> Tags:
    loaded = [loader(out) for _, _, loader in PROVIDERS]

    def tags_for(ticker: str, session: date) -> dict[str, str]:
        merged: dict[str, str] = {}
        for provider in loaded:
            try:
                merged.update(provider(ticker, session))
            except Exception:  # noqa: BLE001 - a broken source adds no tags; the record stays as it was
                continue
        return merged
    return tags_for
