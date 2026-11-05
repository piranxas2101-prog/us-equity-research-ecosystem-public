"""Results calendar (F5, owner's decisions 2026-11-03): when every company's next periodic report is expected.

Built every night from the results journal (``data/earnings_journal.db``: the filing dates of every 10-Q/10-K and
foreign equivalent, five years back) and the SEC results releases (``sec_results_releases.py``: every 8-K with item
2.02, which dates the release behind each report exactly). The estimate is the same quarter's results a year
earlier + 52 weeks (``results_lab.py``: on filing dates, within 7 days in 89% of 60,788 cases, 95% within 14);
without a year of history, the last report + 13 weeks, labelled as the weaker method. A company without reports has no date: unknown stays unknown. Written to
``output/results_calendar_latest.json`` and read by:

* ``alpaca_multi_day_monitor.py`` - the three legacy shorts that lose through results (``STEP_ASIDE``: -1.25 to
  -1.30% over the results window, 95% intervals below zero) are closed ``STEP_ASIDE_SESSIONS`` sessions before the
  results window opens, or inside it while the report is pending; the window opens ``QUARTERLY_LEAD_DAYS`` before
  the expected date, ``ANNUAL_LEAD_DAYS`` when the estimate rests on an annual report (results come up to weeks
  before a 10-K is filed, and the local 8-K index starts in May 2026, too late to date last year's). Koulis does
  not open them inside that window (``entry_results_ahead``). Since 2026-11-06 the support_bounce long does the same
  (owner, after MNTN -45.59% through its results on 2026-11-05): on average it gains through results (+0.83 to
  +0.96%, ``results_lab.py``), and that average is given up on purpose, because no measure known beforehand separates
  the rare collapse (0.6% below -25%; MNTN's own past reactions were only 3-5%). Every other held position sits
  through results as before (momentum short +1.09% through results);
* share classes share their company's calendar (GOOG and GOOGL, by CIK);
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
                        ("pre_run_breakout_confirmation", "short"),
                        ("support_bounce_swing", "long")})  # owner 2026-11-06, see the module's docstring
RESULTS_CATALYST = frozenset({("momentum_12_1", "short"), ("gap_fade", "short"),
                              ("mean_reversion_overextension", "short")})
STEP_ASIDE_SESSIONS = 3      # the results window starts 2 sessions before the filing day; the estimate is +-5-7 days
OVERDUE_DAYS = 14            # a report this late is still pending; later than that the estimate is not trusted
CATALYST_WITHIN_DAYS = 92    # a quarter
MAX_CALENDAR_AGE_DAYS = 4    # a calendar older than this (a long weekend) decides nothing


ANNUAL_FORMS = frozenset({"10-K", "10-KT", "20-F", "40-F"})
ANNUAL_LEAD_DAYS = 28    # results announced up to four weeks before the annual report is filed (PTC: 16 days)
RELEASE_BEFORE_DAYS = 45  # a results release belongs to the periodic report filed up to this long after it
QUARTERLY_LEAD_DAYS = 5  # the estimate's error is -5 to +7 days (10%-90%); 10-Qs follow the release by ~1 day


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


def load_reports(db: Path | None = None) -> dict[str, Any]:
    """CIK -> (filing date, annual?) of its periodic reports, oldest first; plus ``_tickers``: CIK -> the journal's
    tickers, and ``_announced``: CIK -> its latest results release (8-K 2.02 or 6-K, read since October 2026), which
    is not a periodic report but tells that the pending quarter is already out."""
    db = db or JOURNAL
    if not db.exists():
        return {}
    out: dict[str, set[tuple[date, bool]]] = {}
    tickers: dict[str, set[str]] = {}
    announced: dict[str, date] = {}
    with closing(sqlite3.connect(db)) as conn:
        for ticker, cik, form, filed in conn.execute("SELECT ticker, cik, form, filed FROM earnings_event"):
            form = str(form or "")
            try:
                key, day = str(int(cik)), date.fromisoformat(str(filed)[:10])
            except (TypeError, ValueError):
                continue
            if form.startswith(("8-K", "6-K")):  # a results release: the quarter it belongs to is already out
                announced[key] = max(day, announced.get(key, day))
                continue
            out.setdefault(key, set()).add((day, form in ANNUAL_FORMS))
            tickers.setdefault(key, set()).add(str(ticker).upper())
    return {**{k: sorted(v) for k, v in out.items()}, "_tickers": {k: sorted(v) for k, v in tickers.items()},
            "_announced": announced}


def share_classes(tickers_file: Path | None = None) -> dict[str, list[str]]:
    """CIK -> every ticker of the company in the SEC ticker map (GOOG and GOOGL share one calendar)."""
    import periodic_filing

    out: dict[str, list[str]] = {}
    for ticker, cik in periodic_filing.ticker_to_cik(tickers_file or periodic_filing.DEFAULT_TICKERS).items():
        out.setdefault(str(int(cik)), []).append(ticker)
    return out


def dated(events: list[tuple[date, bool]], releases: list[date]) -> list[tuple[date, bool, bool]]:
    """(results date, annual?, exact?) per periodic report: the latest SEC results release (8-K item 2.02) filed in
    the ``RELEASE_BEFORE_DAYS`` before the report, else the report's own filing date (not exact)."""
    out = []
    for filed, annual in events:
        near = [r for r in releases if filed - timedelta(days=RELEASE_BEFORE_DAYS) <= r <= filed]
        out.append((max(near), annual, True) if near else (filed, annual, False))
    return sorted(out)


def build(reports: dict[str, Any], today: date, classes: dict[str, list[str]] | None = None,
          releases: dict[str, list[date]] | None = None) -> dict[str, Any]:
    symbols = {}
    names = reports.get("_tickers") or {}
    announced = reports.get("_announced") or {}
    exact_count = 0
    for cik, events in reports.items():
        if cik.startswith("_"):
            continue
        events = sorted(events)
        past = [e for e in events if e[0] <= today]
        if not past:
            continue
        last = past[-1][0]
        own = [r for r in (releases or {}).get(cik, []) if r <= today]
        known = [d for d in (announced.get(cik), own[-1] if own else None) if d is not None and d <= today]
        release = max(known) if known else None
        latest = max(last, release) if release is not None else last
        timeline = dated(events, own)
        # from a month after the last report or results release (a released quarter whose 10-Q is still to come is
        # not the next results), so that report's own twin a year earlier is not taken for the next
        expected, method = estimate_next(timeline, latest + timedelta(days=30))
        if expected is None:
            continue
        source = next((e for e in timeline if e[0] + timedelta(weeks=52) == expected), None)             if method == "last_year_plus_52_weeks" else None
        annual, exact = (source[1], source[2]) if source else (False, False)
        exact_count += bool(exact)
        # an exact release date a year earlier needs only the estimate's own error; a 10-K date needs four weeks
        lead = ANNUAL_LEAD_DAYS if annual and not exact else QUARTERLY_LEAD_DAYS
        item = {"expected_date": expected.isoformat(), "window_start": (expected - timedelta(days=lead)).isoformat(),
                "annual": annual, "from_release": exact, "method": method, "last_report": last.isoformat(),
                "last_release": release.isoformat() if release else None, "cik": cik}
        for ticker in set(names.get(cik, [])) | set((classes or {}).get(cik, [])):
            symbols[ticker] = item
    return {"schema": SCHEMA, "as_of": today.isoformat(), "generated_at": datetime.now(timezone.utc).isoformat(),
            "source": "SEC results releases (8-K item 2.02, sec_results_releases.py) and periodic report filing "
                      "dates (data/earnings_journal.db)", "companies_from_release": exact_count,
            "lead_days": {"annual": ANNUAL_LEAD_DAYS, "quarterly": QUARTERLY_LEAD_DAYS}, "symbols": symbols}


def load(path: Path | None = None) -> dict[str, Any] | None:
    try:
        payload = json.loads((path or OUT).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


def _entry(calendar: dict[str, Any] | None, ticker: str, today: date) -> dict[str, Any] | None:
    if not calendar:
        return None
    try:
        if (today - date.fromisoformat(str(calendar.get("as_of")))).days > MAX_CALENDAR_AGE_DAYS:
            return None
    except ValueError:
        return None
    item = (calendar.get("symbols") or {}).get(str(ticker).upper())
    return item if isinstance(item, dict) else None


def expected_date(calendar: dict[str, Any] | None, ticker: str, today: date) -> date | None:
    """The expected next report of ``ticker`` - None when unknown, the calendar is stale, or the report is more
    than ``OVERDUE_DAYS`` late (the estimate is not trusted then)."""
    try:
        found = date.fromisoformat(str((_entry(calendar, ticker, today) or {})["expected_date"]))
    except (KeyError, TypeError, ValueError):
        return None
    return None if (today - found).days > OVERDUE_DAYS else found


def window_start(calendar: dict[str, Any] | None, ticker: str, today: date) -> date | None:
    """The first day the results may come (the expected date less the lead of its kind of report), or None."""
    if expected_date(calendar, ticker, today) is None:
        return None
    try:
        return date.fromisoformat(str((_entry(calendar, ticker, today) or {})["window_start"]))
    except (KeyError, TypeError, ValueError):
        return None


def close_to_results(start: date, today: date) -> bool:
    """Within ``STEP_ASIDE_SESSIONS`` sessions of the results window, or inside it."""
    return (int(np.busday_count(today, start)) if start > today else 0) <= STEP_ASIDE_SESSIONS


def step_aside(calendar: dict[str, Any] | None, ticker: str, playbook: str | None, direction: str,
               today: date) -> date | None:
    """The expected results date when a held position of this strategy should be out before them, else None."""
    if (str(playbook or ""), str(direction).lower()) not in STEP_ASIDE:
        return None
    start = window_start(calendar, ticker, today)
    return expected_date(calendar, ticker, today) if start is not None and close_to_results(start, today) else None


def entry_results_ahead(short_gate_data: dict[str, Any] | None, ticker: str, playbook: str | None, direction: str,
                        today: str, calendar: dict[str, Any] | None = None) -> str | None:
    """Koulis: the expected results date when a new position of a ``STEP_ASIDE`` strategy would only be closed again
    before them (read from today's short gate data, which carries the calendar's dates per symbol; a long is not in
    that file, so it is read from the calendar itself)."""
    if (str(playbook or ""), str(direction).lower()) not in STEP_ASIDE:
        return None
    if str(direction).lower() == "long":
        day = date.fromisoformat(today)
        start = window_start(calendar, ticker, day)
        return (str(expected_date(calendar, ticker, day))
                if start is not None and close_to_results(start, day) else None)
    if not short_gate_data:
        return None
    if short_gate_data.get("as_of") != today:
        return None
    facts = (short_gate_data.get("symbols") or {}).get(str(ticker).upper()) or {}
    try:
        start = date.fromisoformat(str(facts["results_window_start"]))
    except (KeyError, TypeError, ValueError):
        return None
    return str(facts.get("expected_results_date")) if close_to_results(start, date.fromisoformat(today)) else None


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
    import sec_results_releases

    payload = build(load_reports(), date.today(), share_classes(), sec_results_releases.load_releases())
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, indent=1), encoding="utf-8")
    weak = sum(1 for s in payload["symbols"].values() if s["method"] != "last_year_plus_52_weeks")
    print(f"[RESULTS-CALENDAR] {len(payload['symbols'])} tickers ({weak} on last report + 13 weeks) -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
