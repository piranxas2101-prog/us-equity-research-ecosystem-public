"""Shadow multi-day holds: what each ledger record would have done held for a week, a month or a quarter.

Owner's decision 2026-10-16 (ADR-001, evidence for step 3). The exit lab found the edge in the holding period,
not in the exit rule, while every paper position is closed the same day. This keeps the live evidence for longer
holds without any order: for every record of the paper-learning ledger (trades, the decisions that did not trade
and the shadow candidates), each holding class is replayed on the daily bars of the price store:

* entry at the open of the record's session (orders go in at 09:35 New York), in the record's direction;
* ``fixed``: out at the close of the class's last session (day 1, week 5, month 20, quarter 60 sessions,
  the entry session included) - the exit lab's best rule;
* ``atr_bracket``: the record's own stop / take-profit (its trade plan, or the launcher's 3% / 6%), checked on
  each session's range; a session opening beyond the stop exits at the open; stop and target touched in the same
  session count as the stop; otherwise out at the class's last close - today's rule, held longer.
* ``thesis`` (week, month, quarter): held while the reason it was opened holds, judged on its own clock against the
  traders' nightly boards of the archived runs, with a stop that widens with time (``thesis_exit.py``).

A class whose last session is not stored yet stays pending (unknown) and is filled on a later run. SPY over the
same window gives the excess. Research only: nothing reads this to decide; it is shown in the learning reports.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

import pandas as pd

CLASSES = {"day": 1, "week": 5, "month": 20, "quarter": 60}
RULES = ("fixed", "atr_bracket")
THESIS_CLASSES = ("week", "month", "quarter")
DEFAULT_STOP, DEFAULT_TAKE = 0.03, 0.06

Frames = Callable[[str], "pd.DataFrame | None"]


def store_frames(directory: Path) -> Frames:
    import price_history_store as phs

    cache: dict[str, Any] = {}

    def frames(ticker: str) -> pd.DataFrame | None:
        if ticker not in cache:
            frame = phs.load(ticker, directory)
            cache[ticker] = None if frame is None else frame[["Open", "High", "Low", "Close"]].dropna()
        return cache[ticker]
    return frames


def hold(frame: pd.DataFrame, session: date, sessions: int, direction: str, rule: str,
         stop: float, take: float) -> dict[str, Any] | None:
    """One hold entered at the open of ``session``; None while its last session is not stored (or no entry bar)."""
    dates = frame.index.date
    start = int((dates < session).sum())
    if start >= len(frame) or dates[start] != session:
        return None
    end = start + sessions - 1
    if end >= len(frame):
        return None
    o, h, low, c = (frame[k].to_numpy(float) for k in ("Open", "High", "Low", "Close"))
    entry, long = o[start], direction == "long"
    if not entry > 0:
        return None
    exit_index, exit_price = end, c[end]
    if rule == "atr_bracket":
        stop_level = entry * (1 - stop) if long else entry * (1 + stop)
        target = entry * (1 + take) if long else entry * (1 - take)
        for j in range(start, end + 1):
            if j > start and ((o[j] <= stop_level) if long else (o[j] >= stop_level)):
                exit_index, exit_price = j, o[j]
                break
            if (low[j] <= stop_level) if long else (h[j] >= stop_level):
                exit_index, exit_price = j, stop_level
                break
            if (h[j] >= target) if long else (low[j] <= target):
                exit_index, exit_price = j, target
                break
    result = (1.0 if long else -1.0) * (exit_price / entry - 1) * 100
    return {"entry_price": round(float(entry), 4), "exit_date": str(dates[exit_index]),
            "exit_price": round(float(exit_price), 4), "sessions_held": exit_index - start + 1,
            "result_pct": round(float(result), 3)}


def _spy(frame: pd.DataFrame | None, entry_date: str, exit_date: str) -> float | None:
    if frame is None:
        return None
    dates = frame.index.date
    first = frame[dates == date.fromisoformat(entry_date)]
    last = frame[dates == date.fromisoformat(exit_date)]
    if first.empty or last.empty:
        return None
    return round(float((last["Close"].iloc[0] / first["Open"].iloc[0] - 1) * 100), 3)


def ensure_table(conn: Any) -> None:
    conn.execute("CREATE TABLE IF NOT EXISTS paper_holding_shadows (record_id TEXT NOT NULL, holding_class TEXT NOT NULL,"
                 " rule TEXT NOT NULL, entry_date TEXT NOT NULL, exit_date TEXT NOT NULL, sessions_held INTEGER NOT NULL,"
                 " result_pct REAL NOT NULL, spy_pct REAL NOT NULL, excess_pct REAL NOT NULL, computed_at TEXT NOT NULL,"
                 " PRIMARY KEY (record_id, holding_class, rule))")


def _cells() -> list[tuple[str, str]]:
    return [(c, r) for c in CLASSES for r in RULES] + [(c, r) for c in THESIS_CLASSES for r in ("thesis", "thesis_shock")]


def _thesis(frame: pd.DataFrame, session: date, cls: str, record: dict[str, Any],
            boards: list[tuple[date, Any]], rule: str = "thesis", shocks: dict[date, str] | None = None
            ) -> dict[str, Any] | None:
    import thesis_exit as te

    dates = frame.index.date
    start = int((dates < session).sum())
    if start >= len(frame) or dates[start] != session:
        return None
    nights = te.nightly_sessions(boards, dates)
    rows = ((nights.get(start) or {}).get(record.get("agent")) or {}).get(record["ticker"]) or []
    own = next((r for r in rows if r.get("playbook_name") == record.get("playbook")), {})
    opening = {"agent": record.get("agent"), "ticker": record["ticker"], "playbook": record.get("playbook"),
               "direction": record["direction"], "giannis_score": own.get("giannis_score"),
               "atr_14_pct": own.get("atr_14_pct")}
    out = te.replay(frame, start, cls, rule, opening, nights, shocks)
    if out is None:
        return None
    return {"exit_date": str(dates[out["exit_index"]]), "sessions_held": out["sessions_held"],
            "result_pct": out["result_pct"]}


def fill(conn: Any, records: list[dict[str, Any]], frames: Frames, runs_dir: Path | None = None) -> int:
    """Every (record, class, rule) whose window is complete in the price store, once. ``runs_dir``: the archived
    runs whose nightly trader boards judge the thesis rule (loaded only when a thesis cell is due; none: no
    thesis exit, only its widening stop)."""
    boards: list[tuple[date, Any]] | None = None
    shocks: dict[date, str] | None = None
    ensure_table(conn)
    known = {(r[0], r[1], r[2]) for r in
             conn.execute("SELECT record_id, holding_class, rule FROM paper_holding_shadows").fetchall()}
    spy = frames("SPY")
    stamp, added = datetime.now(timezone.utc).isoformat(), 0
    for record in records:
        if record.get("direction") not in ("long", "short"):
            continue
        missing = [(c, r) for c, r in _cells() if (record["record_id"], c, r) not in known]
        if not missing:
            continue
        frame = frames(record["ticker"])
        if frame is None:
            continue
        session = date.fromisoformat(record["session_date"])
        stop = record.get("stop_loss_pct") or DEFAULT_STOP
        take = record.get("take_profit_pct") or DEFAULT_TAKE
        for cls, rule in missing:
            if rule in ("thesis", "thesis_shock"):
                if boards is None:
                    import thesis_exit as te

                    first = min(date.fromisoformat(r["session_date"]) for r in records)
                    boards = te.load_boards(runs_dir, since=first - timedelta(days=7)) if runs_dir else []
                if shocks is None:
                    import thesis_exit as te

                    shocks = te.market_shocks(frames("SPY"))
                out = _thesis(frame, session, cls, record, boards, rule, shocks)
            else:
                out = hold(frame, session, CLASSES[cls], record["direction"], rule, stop, take)
            if out is None:
                continue  # not stored yet: unknown, filled on a later run
            bench = _spy(spy, record["session_date"], out["exit_date"])
            if bench is None:
                continue
            excess = out["result_pct"] - bench if record["direction"] == "long" else out["result_pct"] + bench
            conn.execute("INSERT INTO paper_holding_shadows VALUES (?,?,?,?,?,?,?,?,?,?)",
                         (record["record_id"], cls, rule, record["session_date"], out["exit_date"], out["sessions_held"],
                          out["result_pct"], bench, round(excess, 3), stamp))
            added += 1
    conn.commit()
    return added


def attach(conn: Any, records: list[dict[str, Any]]) -> None:
    """``record['holds'][class][rule]`` = {result_pct, excess_pct, sessions_held}; absent while pending."""
    ensure_table(conn)
    table: dict[str, dict[str, dict[str, Any]]] = {}
    for rid, cls, rule, result, excess, held in conn.execute(
            "SELECT record_id, holding_class, rule, result_pct, excess_pct, sessions_held FROM paper_holding_shadows"):
        table.setdefault(rid, {}).setdefault(cls, {})[rule] = {"result_pct": result, "excess_pct": excess,
                                                               "sessions_held": held}
    for record in records:
        record["holds"] = table.get(record["record_id"], {})


def category_findings(records: list[dict[str, Any]], *, min_records: int, min_days: int, fdr_q: float = 0.10,
                      min_weeks: int = 2) -> dict[str, Any]:
    """Where a longer hold beats closing the same day (owner 2026-10-17: the holding class by fundamentals, macro
    and micro environment). For every category tag and value (Giannis tier, Piotroski, results trend, market state,
    sector, volatility ...) and every longer class (fixed hold): how often the longer hold beat the same-day hold
    on the same records, against the rest. Two-proportion test, Benjamini-Hochberg q <= ``fdr_q`` over all tests,
    the same direction in at least ``min_weeks`` weeks; "unknown" is never a category to act on."""
    import math

    def longer_won(record: dict[str, Any], cls: str) -> bool | None:
        holds = record.get("holds") or {}
        day, longer = (holds.get("day") or {}).get("fixed"), (holds.get(cls) or {}).get("fixed")
        if not day or not longer:
            return None
        return longer["excess_pct"] > day["excess_pct"]

    tests = []
    tags = sorted({t for r in records for t in (r.get("categories") or {})})
    for cls in ("week", "month", "quarter"):
        judged = [(r, w) for r in records if (w := longer_won(r, cls)) is not None]
        for tag in tags:
            for value in sorted({str(r["categories"].get(tag)) for r, _ in judged if r.get("categories")} - {"unknown", "None"}):
                inside = [(r, w) for r, w in judged if str((r.get("categories") or {}).get(tag)) == value]
                rest = [(r, w) for r, w in judged if str((r.get("categories") or {}).get(tag)) != value]
                if len(inside) < min_records or len(rest) < min_records or \
                        len({r["session_date"] for r, _ in inside}) < min_days:
                    continue
                w1, w2 = sum(w for _, w in inside), sum(w for _, w in rest)
                p1, p2 = w1 / len(inside), w2 / len(rest)
                pooled = (w1 + w2) / (len(inside) + len(rest))
                se = math.sqrt(pooled * (1 - pooled) * (1 / len(inside) + 1 / len(rest))) or 1e-9
                weeks: dict[str, list[list[bool]]] = {}
                for r, w in judged:
                    week = str(date.fromisoformat(r["session_date"]).isocalendar()[:2])
                    weeks.setdefault(week, [[], []])[0 if str((r.get("categories") or {}).get(tag)) == value else 1].append(w)
                same = sum(1 for a, b in weeks.values() if len(a) >= 5 and len(b) >= 5
                           and (sum(a) / len(a) - sum(b) / len(b)) * (p1 - p2) > 0)
                tests.append({"class": cls, "tag": tag, "value": value, "records": len(inside),
                              "longer_won_pct": round(p1 * 100, 1), "rest_longer_won_pct": round(p2 * 100, 1),
                              "effect_pct_points": round((p1 - p2) * 100, 1), "weeks_same_direction": same,
                              "p_value": math.erfc(abs(p1 - p2) / se / math.sqrt(2))})
    tests.sort(key=lambda t: t["p_value"])
    running = 1.0
    for i in range(len(tests) - 1, -1, -1):
        running = min(running, tests[i]["p_value"] * len(tests) / (i + 1))
        tests[i]["q_value"] = round(running, 4)
        tests[i]["p_value"] = round(tests[i]["p_value"], 5)
    found = [t for t in tests if t["q_value"] <= fdr_q and t["weeks_same_direction"] >= min_weeks]
    return {"tests": len(tests), "findings": found, "fdr_q": fdr_q}


def summarize(records: list[dict[str, Any]], metrics: Callable[[list[dict[str, Any]]], dict[str, Any]]
              ) -> dict[str, Any]:
    """Per class and rule, overall and per strategy (agent, playbook, direction), with the learning metrics."""
    def rows(cls: str, rule: str, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        out = []
        for r in items:
            cell = (r.get("holds") or {}).get(cls, {}).get(rule)
            out.append({"session_date": r["session_date"],
                        "outcome_pct": None if cell is None else cell["result_pct"],
                        "excess_pct": None if cell is None else cell["excess_pct"]})
        return out

    strategies: dict[str, list[dict[str, Any]]] = {}
    for r in records:
        strategies.setdefault(f"{r.get('agent')} · {r.get('playbook')} · {r.get('direction')}", []).append(r)
    cells = sorted(_cells(), key=lambda cell: list(CLASSES).index(cell[0]))
    overall = {f"{cls} · {rule}": metrics(rows(cls, rule, records)) for cls, rule in cells}
    per_strategy = {name: {f"{cls} · {rule}": metrics(rows(cls, rule, items)) for cls, rule in cells
                           if rule in ("fixed", "thesis", "thesis_shock")}
                    for name, items in sorted(strategies.items())}
    return {"classes": CLASSES, "rules": list(RULES) + ["thesis"], "overall": overall, "strategies": per_strategy,
            "pending_note": "a class is filled once its last session is in the price store"}
