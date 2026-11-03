"""Results calendar (F5, owner's decisions 2026-11-03): when every company's next periodic report is expected.

Built every night from the results journal (``data/earnings_journal.db``: the filing dates of every 10-Q/10-K and
foreign equivalent, five years back). The estimate is the same report a year earlier + 52 weeks (``results_lab.py``:
within 7 days of the real filing in 89% of 60,788 cases, 95% within 14); without a year of history, the last report
+ 13 weeks, labelled as the weaker method. A company without reports has no date: unknown stays unknown. Written to
``output/results_calendar_latest.json`` and read by:

* ``alpaca_multi_day_monitor.py`` - the three legacy shorts that lose through results (``STEP_ASIDE``: -1.25 to
  -1.30% over the results window, 95% intervals below zero) are closed ``STEP_ASIDE_SESSIONS`` sessions before the
  expected date, or while a report is overdue; every other held position sits through results as before
  (support_bounce long +0.96%, momentum short +1.09% through results);
* ``alpaca_short_gate_data.py`` / Koulis - the expected results date resolves the "catalyst timing" gate of a short
  only for the strategies whose shorts gain through results (``RESULTS_CATALYST``) and only when it falls within
  ``CATALYST_WITHIN_DAYS``.

    results_calendar.py            # nightly step after the results journal
"""
from __future__ import annotations

import argparse
from contextlib import closing
from datetime import date, datetime, timedelta, timezone
import json
from pathlib import Path
import sqlite3
from typing import Any

import numpy as np

ROOT = Path(__file__).resolve().parent
JOURNAL = ROOT / "data" / "earnings_journal.db"
OUT = ROOT / "output" / "results_calendar_latest.json"
SCHEMA = "ecosystem.results_calendar.v1"
STEP_ASIDE = frozenset({("controlled_breakout_swing", "short"), ("early_accumulation_swing", "short"),
                        ("pre_run_breakout_confirmation", "short")})
RESULTS_CATALYST = frozenset({("momentum_12_1", "short"), ("gap_fade", "short"),
                              ("mean_reversion_overextension", "short")})
STEP_ASIDE_SESSIONS = 3      # the results window starts 2 sessions before the filing day; the estimate is +-5-7 days
OVERDUE_DAYS = 14            # a report this late is still pending; later than that the estimate is not trusted
CATALYST_WITHIN_DAYS = 92    # a quarter
MAX_CALENDAR_AGE_DAYS = 4    # a calendar older than this (a long weekend) decides nothing


def estimate_next(reports: list[tuple[date, Any]], after: date) -> tuple[date | None, str]:
    """The expected next report after ``after``: the nearest "report a year earlier + 52 weeks" within 17 weeks;
    without a year of history, the last report + 13 weeks. (date, method)."""
    past = [d for d, *_ in reports if d <= after]
    if not past:
        return None, "unknown"
    ahead = [g for d in past if after < (g := d + timedelta(weeks=52)) <= after + timedelta(weeks=17)]
    if ahead:
        return min(ahead), "last_year_plus_52_weeks"  # the nearest one: the next report, not the one after it
    return past[-1] + timedelta(weeks=13), "last_report_plus_13_weeks"


def load_reports(db: Path | None = None) -> dict[str, list[date]]:
    """Ticker -> filing dates of its periodic reports, oldest first."""
    db = db or JOURNAL
    if not db.exists():
        return {}
    out: dict[str, set[date]] = {}
    with closing(sqlite3.connect(db)) as conn:
        for ticker, filed in conn.execute("SELECT ticker, filed FROM earnings_event"):
            try:
                out.setdefault(str(ticker).upper(), set()).add(date.fromisoformat(str(filed)[:10]))
            except ValueError:
                continue
    return {t: sorted(v) for t, v in out.items()}


def build(reports: dict[str, list[date]], today: date) -> dict[str, Any]:
    symbols = {}
    for ticker, days in reports.items():
        last = max(d for d in days if d <= today) if any(d <= today for d in days) else None
        if last is None:
            continue
        # from a month after the last report, so that report's own twin a year earlier is not taken for the next
        expected, method = estimate_next([(d,) for d in days], last + timedelta(days=30))
        if expected is None:
            continue
        symbols[ticker] = {"expected_date": expected.isoformat(), "method": method, "last_report": last.isoformat()}
    return {"schema": SCHEMA, "as_of": today.isoformat(), "generated_at": datetime.now(timezone.utc).isoformat(),
            "source": "SEC periodic report filing dates (data/earnings_journal.db)", "symbols": symbols}


def load(path: Path | None = None) -> dict[str, Any] | None:
    try:
        payload = json.loads((path or OUT).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


def expected_date(calendar: dict[str, Any] | None, ticker: str, today: date) -> date | None:
    """The expected next report of ``ticker`` - None when unknown, the calendar is stale, or the report is more
    than ``OVERDUE_DAYS`` late (the estimate is not trusted then)."""
    if not calendar:
        return None
    try:
        if (today - date.fromisoformat(str(calendar.get("as_of")))).days > MAX_CALENDAR_AGE_DAYS:
            return None
        found = date.fromisoformat(str(((calendar.get("symbols") or {}).get(str(ticker).upper()) or {})["expected_date"]))
    except (KeyError, TypeError, ValueError):
        return None
    return None if (today - found).days > OVERDUE_DAYS else found


def step_aside(calendar: dict[str, Any] | None, ticker: str, playbook: str | None, direction: str,
               today: date) -> date | None:
    """The expected results date when a held position of this strategy should be out before it, else None."""
    if (str(playbook or ""), str(direction).lower()) not in STEP_ASIDE:
        return None
    found = expected_date(calendar, ticker, today)
    return found if found is not None and close_to_results(found, today) else None


def close_to_results(expected: date, today: date) -> bool:
    """Within ``STEP_ASIDE_SESSIONS`` sessions of the expected report, or past it (overdue: still pending)."""
    return (int(np.busday_count(today, expected)) if expected > today else 0) <= STEP_ASIDE_SESSIONS


def entry_results_ahead(short_gate_data: dict[str, Any] | None, ticker: str, playbook: str | None, direction: str,
                        today: str) -> str | None:
    """Koulis: the expected results date when a new position of a ``STEP_ASIDE`` strategy would only be closed again
    before them (read from today's short gate data, which carries the calendar's date per symbol)."""
    if (str(playbook or ""), str(direction).lower()) not in STEP_ASIDE or not short_gate_data             or short_gate_data.get("as_of") != today:
        return None
    try:
        found = date.fromisoformat(str(((short_gate_data.get("symbols") or {}).get(str(ticker).upper()) or {})
                                       ["expected_results_date"]))
    except (KeyError, TypeError, ValueError):
        return None
    return str(found) if close_to_results(found, date.fromisoformat(today)) else None


def results_catalyst(calendar: dict[str, Any] | None, ticker: str, playbook: str | None, direction: str,
                     today: date) -> date | None:
    """The expected results date that dates a short's catalyst, for the strategies that gain through results."""
    if (str(playbook or ""), str(direction).lower()) not in RESULTS_CATALYST:
        return None
    found = expected_date(calendar, ticker, today)
    return found if found is not None and today <= found <= today + timedelta(days=CATALYST_WITHIN_DAYS) else None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, default=OUT)
    args = parser.parse_args(argv)
    payload = build(load_reports(), date.today())
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, indent=1), encoding="utf-8")
    weak = sum(1 for s in payload["symbols"].values() if s["method"] != "last_year_plus_52_weeks")
    print(f"[RESULTS-CALENDAR] {len(payload['symbols'])} companies ({weak} on last report + 13 weeks) -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
