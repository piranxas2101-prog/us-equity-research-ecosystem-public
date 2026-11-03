"""Results lab (F5): can the next results date be known, and should a held position sit through it? Research only.

Owner 2026-11-03 (rules review, F5): GOOGL gapped 10.5% through its stop on its results of 2026-10-29. The pre-open
check judges new entries only; a held position meets its company's results unprepared, because no part of the
ecosystem knows when they come. Two questions, on data already stored:

1. **Calendar** - is "the same quarter's report a year earlier + 52 weeks" a usable estimate of the next periodic
   report? Measured on every report of the results journal (``data/earnings_journal.db``, five years of 10-Q/10-K
   and foreign equivalents with their filing dates): the error in days of the estimate, and of the simpler "last
   report + 13 weeks".
2. **Sitting through results** - for the five-year signals of the strategy lab (daily strategies and the original
   playbooks, a random sample of at most ``MAX_PER_GROUP`` per strategy and side) held for ``HOLDS`` sessions, the
   positions whose hold contains a report: the return of the results window (close ``BEFORE`` sessions before the
   filing day -> close of the session after it, large companies announce a day or more before filing) in the
   trade's direction minus SPY over the same sessions. That is exactly what stepping aside - out before, back in
   after - gives up or saves. Judged per strategy and side: mean, median, 95% month-block bootstrap interval,
   share beyond -5% and -10% (the GOOGL kind), against the same window of the same stock's every report
   (``baseline``: does the strategy's signal tell anything about the results reaction?).

Unknown stays unknown: a report without prices around it is left out. Nothing reads this to decide.

    results_lab.py      # writes output/strategy_lab/results_lab_latest.json and .md
"""
from __future__ import annotations

import argparse
from bisect import bisect_left
from collections import defaultdict
from contextlib import closing
from datetime import date, datetime, timedelta, timezone
import gzip
import json
from pathlib import Path
import random
import sqlite3
from typing import Any

ROOT = Path(__file__).resolve().parent
PRICES = ROOT / "data" / "price_history"
JOURNAL = ROOT / "data" / "earnings_journal.db"
LAB = ROOT / "output" / "strategy_lab"
SIGNALS = ("strategy_lab_signals_latest.csv.gz", "strategy_lab_legacy_signals_latest.csv.gz")
SCHEMA = "ecosystem.results_lab.v1"
HOLDS = (10, 20, 60)
BEFORE = 2
MAX_PER_GROUP = 4000
BOOTSTRAP = 2000
SEED = 11
CLIP_PCT = 50.0  # a results window beyond +-50% is clipped (reverse splits, bad prints)


def load_closes(path: Path) -> tuple[list[date], list[float]]:
    days, closes = [], []
    try:
        with gzip.open(path, "rt", encoding="utf-8") as fh:
            next(fh, None)
            for line in fh:
                parts = line.rstrip().split(",")
                try:
                    day, close = date.fromisoformat(parts[0]), float(parts[4])
                except (IndexError, ValueError):
                    continue
                if close > 0:
                    days.append(day)
                    closes.append(close)
    except OSError:
        pass
    return days, closes


def load_reports(db: Path | None = None) -> dict[str, list[tuple[date, str | None]]]:
    """Ticker -> (filing date, period end) of every periodic report, oldest first."""
    db = db or JOURNAL
    if not db.exists():
        return {}
    out: dict[str, list[tuple[date, str | None]]] = defaultdict(list)
    with closing(sqlite3.connect(db)) as conn:
        for ticker, filed, period_end in conn.execute("SELECT ticker, filed, period_end FROM earnings_event"):
            try:
                out[str(ticker).upper()].append((date.fromisoformat(str(filed)[:10]), period_end))
            except ValueError:
                continue
    return {t: sorted(set(v), key=lambda e: (e[0], e[1] or "")) for t, v in out.items()}


def estimate_next(reports: list[tuple[date, str | None]], after: date) -> tuple[date | None, str]:
    """The expected next report after ``after``: the report filed 49-55 weeks before the next one would fall, + 52
    weeks; without a year of history, the last report + 13 weeks. (date, method)."""
    past = [d for d, _ in reports if d <= after]
    if not past:
        return None, "unknown"
    ahead = [g for d in past if after < (g := d + timedelta(weeks=52)) <= after + timedelta(weeks=17)]
    if ahead:
        return min(ahead), "last_year_plus_52_weeks"  # the nearest one: the next report, not the one after it
    return past[-1] + timedelta(weeks=13), "last_report_plus_13_weeks"


def calendar_accuracy(reports: dict[str, list[tuple[date, str | None]]]) -> dict[str, Any]:
    errors: dict[str, list[int]] = defaultdict(list)
    for days in reports.values():
        for k in range(1, len(days)):
            actual = days[k][0]
            guess, method = estimate_next(days[:k], actual - timedelta(days=30))
            if guess is not None and method != "unknown" and abs((actual - guess).days) <= 60:
                errors[method].append((actual - guess).days)
    out = {}
    for method, values in errors.items():
        values.sort()
        n = len(values)
        out[method] = {"n": n, "median_days": values[n // 2],
                       "within_3_days": round(sum(1 for v in values if abs(v) <= 3) / n, 3),
                       "within_7_days": round(sum(1 for v in values if abs(v) <= 7) / n, 3),
                       "within_14_days": round(sum(1 for v in values if abs(v) <= 14) / n, 3),
                       "p10_p90_days": [values[n // 10], values[9 * n // 10]]}
    return out


def read_signals(lab: Path | None = None) -> dict[tuple[str, str], list[tuple[str, date]]]:
    groups: dict[tuple[str, str], list[tuple[str, date]]] = defaultdict(list)
    for name in SIGNALS:
        path = (lab or LAB) / name
        if not path.exists():
            continue
        with gzip.open(path, "rt", encoding="utf-8") as fh:
            header = next(fh).rstrip().split(",")
            col = {k: i for i, k in enumerate(header)}
            for line in fh:
                parts = line.rstrip().split(",")
                try:
                    groups[(parts[col["strategy"]], parts[col["side"]])].append(
                        (parts[col["symbol"]].upper(), date.fromisoformat(parts[col["date"]])))
                except (KeyError, IndexError, ValueError):
                    continue
    rng = random.Random(SEED)
    return {k: rng.sample(v, min(len(v), MAX_PER_GROUP)) for k, v in groups.items()}


def window_return(days: list[date], closes: list[float], filed: date) -> tuple[int, int, float] | None:
    """(start index, end index, return) of the results window around a report filed on ``filed``."""
    d = bisect_left(days, filed)
    start, end = d - BEFORE, d + 1
    if start < 0 or end >= len(closes) or d >= len(days):
        return None
    return start, end, closes[end] / closes[start] - 1.0


def _stats(values: list[tuple[str, float]], seed: int = SEED) -> dict[str, Any]:
    """values: (month, signed excess return %)."""
    if not values:
        return {"n": 0}
    xs = sorted(v for _, v in values)
    n = len(xs)
    by_month: dict[str, list[float]] = defaultdict(list)
    for month, v in values:
        by_month[month].append(v)
    months = sorted(by_month)
    rng = random.Random(seed)
    boots = []
    for _ in range(BOOTSTRAP):
        pick = [v for _ in months for v in by_month[months[rng.randrange(len(months))]]]
        boots.append(sum(pick) / len(pick))
    boots.sort()
    return {"n": n, "mean_pct": round(sum(xs) / n, 2), "median_pct": round(xs[n // 2], 2),
            "ci95_pct": [round(boots[int(0.025 * BOOTSTRAP)], 2), round(boots[int(0.975 * BOOTSTRAP)], 2)],
            "share_below_minus5": round(sum(1 for v in xs if v < -5) / n, 3),
            "share_below_minus10": round(sum(1 for v in xs if v < -10) / n, 3),
            "mean_abs_pct": round(sum(abs(v) for v in xs) / n, 2)}


def analyse() -> dict[str, Any]:
    reports = load_reports()
    spy_days, spy_closes = load_closes(PRICES / "SPY.csv.gz")
    spy = dict(zip(spy_days, spy_closes))
    cache: dict[str, tuple[list[date], list[float]]] = {}

    def prices(ticker: str) -> tuple[list[date], list[float]]:
        if ticker not in cache:
            cache[ticker] = load_closes(PRICES / f"{ticker}.csv.gz")
        return cache[ticker]

    def excess(days: list[date], closes: list[float], filed: date) -> tuple[int, int, float] | None:
        found = window_return(days, closes, filed)
        if found is None:
            return None
        start, end, ret = found
        a, b = spy.get(days[start]), spy.get(days[end])
        if not a or not b:
            return None
        return start, end, max(-CLIP_PCT, min(CLIP_PCT, (ret - (b / a - 1.0)) * 100.0))  # bad prints

    baseline: dict[str, list[tuple[str, float]]] = {"long": [], "short": []}
    for ticker, events in reports.items():
        days, closes = prices(ticker)
        if not days:
            continue
        for filed, _ in events:
            found = excess(days, closes, filed)
            if found:
                baseline["long"].append((filed.isoformat()[:7], found[2]))
                baseline["short"].append((filed.isoformat()[:7], -found[2]))
    groups = read_signals()
    results: dict[str, Any] = {}
    for (strategy, side), signals in sorted(groups.items()):
        sign = 1.0 if side == "long" else -1.0
        per_hold: dict[int, list[tuple[str, float]]] = {h: [] for h in HOLDS}
        holds_with_report = {h: 0 for h in HOLDS}
        for ticker, day in signals:
            days, closes = prices(ticker)
            if not days:
                continue
            entry = bisect_left(days, day)
            if entry >= len(days) or days[entry] != day:
                continue
            for filed, _ in reports.get(ticker, []):
                if filed <= day:
                    continue
                found = excess(days, closes, filed)
                if found is None or found[0] < entry:
                    if filed > days[min(len(days) - 1, entry + max(HOLDS))]:
                        break
                    continue
                for h in HOLDS:
                    if found[1] <= entry + h:
                        per_hold[h].append((filed.isoformat()[:7], sign * found[2]))
                        holds_with_report[h] += 1
                break  # the first report after entry
        results[f"{strategy}|{side}"] = {
            "signals": len(signals),
            "holds": {str(h): {"share_with_report": round(holds_with_report[h] / len(signals), 3) if signals else None,
                               **_stats(per_hold[h])} for h in HOLDS}}
    return {"calendar": calendar_accuracy(reports), "before_sessions": BEFORE,
            "baseline_every_report": {side: _stats(v) for side, v in baseline.items()},
            "strategies": results, "companies_with_reports": len(reports)}


def render(payload: dict[str, Any]) -> str:
    lines = [f"# Results lab ({payload['generated_at'][:10]})", "",
             f"Reports of {payload['analysis']['companies_with_reports']} companies (results journal). Results window: "
             f"close {payload['analysis']['before_sessions']} sessions before the filing day -> close of the session "
             "after it, in the trade's direction, minus SPY. Research only.", "", "## Calendar estimate", "",
             "| method | n | median error (days) | within 3 | within 7 | within 14 | 10%-90% |", "|---|---|---|---|---|---|---|"]
    for method, c in payload["analysis"]["calendar"].items():
        lines.append(f"| {method} | {c['n']} | {c['median_days']} | {c['within_3_days']} | {c['within_7_days']} | "
                     f"{c['within_14_days']} | {c['p10_p90_days']} |")
    base = payload["analysis"]["baseline_every_report"]["long"]
    lines += ["", f"Every report, long side (baseline): {base}", "",
              "## Positions sitting through a report, 60-session hold", "",
              "| strategy | side | with a report | n | mean % | median % | 95% | below -5% | below -10% | mean abs % |",
              "|---|---|---|---|---|---|---|---|---|---|"]
    for key, r in payload["analysis"]["strategies"].items():
        strategy, side = key.split("|")
        s = r["holds"]["60"]
        if s.get("n"):
            lines.append(f"| {strategy} | {side} | {s['share_with_report']} | {s['n']} | {s['mean_pct']} | "
                         f"{s['median_pct']} | {s['ci95_pct']} | {s['share_below_minus5']} | {s['share_below_minus10']} | "
                         f"{s['mean_abs_pct']} |")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    argparse.ArgumentParser(description=__doc__.splitlines()[0]).parse_args(argv)
    payload = {"schema": SCHEMA, "generated_at": datetime.now(timezone.utc).isoformat(), "analysis": analyse()}
    LAB.mkdir(parents=True, exist_ok=True)
    (LAB / "results_lab_latest.json").write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    (LAB / "results_lab_latest.md").write_text(render(payload), encoding="utf-8")
    print(render(payload))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
