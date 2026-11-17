"""The positions a market shock closed: what holding them would have done (RULES_REVIEW section 7).

Owner 2026-11-06, after SPY -4.4% on 2026-11-05: "measure the longs the shock closed, five sessions later"; built
2026-11-17. For every paper trade closed with ``exit_reason == market_shock``: the move from the real exit price to
the close 1, 3, 5 and 10 sessions after the exit day, in the trade's direction, and the excess over SPY on the same
sessions. A horizon whose sessions are not stored yet, or a ticker without price history, stays unknown.

Verdict (the section 8 standard, on the 5-session move): with at least 30 known cases from at least 2 shock days,
"holding would have helped" when the mean excess is at least +0.5 and its 95% day-bootstrap interval is above zero,
"the exit helped" when at most -0.5 and below zero, otherwise "no clear difference". Read-only; nothing changes.
"""
from __future__ import annotations

import argparse
from contextlib import closing
from datetime import date, datetime
import json
from pathlib import Path
import random
import sqlite3
import statistics
from typing import Any, Callable
from zoneinfo import ZoneInfo

import pandas as pd

HORIZONS = (1, 3, 5, 10)
JUDGED = 5
MIN_CASES = 30
MIN_DAYS = 2
MARGIN_PCT = 0.5
NEW_YORK = ZoneInfo("America/New_York")
VERDICT_GR = {"holding would have helped": "το κράτημα θα βοηθούσε", "the exit helped": "η έξοδος βοήθησε",
              "no clear difference": "καμία καθαρή διαφορά", "insufficient_sample": "ανεπαρκές δείγμα"}
Frames = Callable[[str], "pd.DataFrame | None"]


def _exit_day(trade: dict[str, Any]) -> date | None:
    try:
        return datetime.fromisoformat(str(trade["exit_at"])).astimezone(NEW_YORK).date()
    except (KeyError, ValueError):
        return None


def _close_after(frame: pd.DataFrame | None, day: date, sessions: int) -> float | None:
    if frame is None or frame.empty:
        return None
    later = frame[frame.index.date > day]
    if len(later) < sessions:
        return None
    return float(later["Close"].iloc[sessions - 1])


def _close_on(frame: pd.DataFrame | None, day: date) -> float | None:
    if frame is None or frame.empty:
        return None
    upto = frame[frame.index.date <= day]
    return None if upto.empty else float(upto["Close"].iloc[-1])


def measure(trades: list[dict[str, Any]], frames: Frames) -> list[dict[str, Any]]:
    spy = frames("SPY")
    out = []
    for trade in trades:
        if trade.get("exit_reason") != "market_shock":
            continue
        day = _exit_day(trade)
        price = trade.get("exit_price")
        sign = -1.0 if trade.get("direction") == "short" else 1.0
        frame = frames(str(trade.get("ticker")))
        spy_start = None if day is None else _close_on(spy, day)
        horizons = {}
        for n in HORIZONS:
            close = None if day is None else _close_after(frame, day, n)
            spy_end = None if day is None else _close_after(spy, day, n)
            move = None if close is None or not isinstance(price, (int, float)) or not price else \
                round(sign * (close / price - 1) * 100, 3)
            spy_move = None if spy_end is None or not spy_start else round((spy_end / spy_start - 1) * 100, 3)
            excess = None if move is None or spy_move is None else round(move - sign * spy_move, 3)
            horizons[str(n)] = {"move_pct": move, "spy_pct": spy_move, "excess_pct": excess}
        out.append({"ticker": trade.get("ticker"), "agent": trade.get("agent"), "direction": trade.get("direction"),
                    "entry_session": trade.get("session_date"), "shock_day": None if day is None else day.isoformat(),
                    "exit_price": price, "horizons": horizons})
    return out


def _mean(values: list[float]) -> float | None:
    return round(statistics.fmean(values), 3) if values else None


def _ci(rows: list[dict[str, Any]], seed: int = 0, boot: int = 2000) -> list[float] | None:
    days = sorted({r["shock_day"] for r in rows})
    by_day = {d: [r["horizons"][str(JUDGED)]["excess_pct"] for r in rows if r["shock_day"] == d] for d in days}
    rng = random.Random(seed)
    means = []
    for _ in range(boot):
        sample = [v for d in (rng.choice(days) for _ in days) for v in by_day[d]]
        means.append(statistics.fmean(sample))
    means.sort()
    return [round(means[int(0.025 * boot)], 3), round(means[int(0.975 * boot) - 1], 3)]


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    by_day: dict[str, dict[str, Any]] = {}
    for day in sorted({r["shock_day"] for r in rows if r["shock_day"]}):
        cases = [r for r in rows if r["shock_day"] == day]
        cells = {}
        for n in HORIZONS:
            moves = [r["horizons"][str(n)]["move_pct"] for r in cases if r["horizons"][str(n)]["move_pct"] is not None]
            excess = [r["horizons"][str(n)]["excess_pct"] for r in cases if r["horizons"][str(n)]["excess_pct"] is not None]
            cells[str(n)] = {"positions": len(cases), "known": len(moves), "mean_move_pct": _mean(moves),
                             "mean_excess_pct": _mean(excess), "held_better": sum(1 for m in moves if m > 0)}
        by_day[day] = cells
    judged = [r for r in rows if r["horizons"][str(JUDGED)]["excess_pct"] is not None]
    days = {r["shock_day"] for r in judged}
    mean = _mean([r["horizons"][str(JUDGED)]["excess_pct"] for r in judged])
    ci = None
    if len(judged) < MIN_CASES or len(days) < MIN_DAYS:
        verdict = "insufficient_sample"
    else:
        ci = _ci(judged)
        verdict = ("holding would have helped" if mean >= MARGIN_PCT and ci[0] > 0 else
                   "the exit helped" if mean <= -MARGIN_PCT and ci[1] < 0 else "no clear difference")
    return {"schema": "ecosystem.shock_exits_lab.v1", "positions": len(rows), "judged": len(judged),
            "judged_days": len(days), "mean_excess_5_pct": mean, "ci_95": ci, "verdict": verdict,
            "by_shock_day": by_day, "rows": rows, "active_decisions_changed": False}


def _v(value: Any) -> str:
    return "άγνωστο" if value is None else f"{value:+g}"


def render(result: dict[str, Any]) -> str:
    lines = ["# Οι θέσεις που έκλεισε το σοκ της αγοράς: τι θα έκανε το κράτημα", "",
             "Από την πραγματική τιμή εξόδου ως το κλείσιμο 1, 3, 5 και 10 συνεδρίες μετά, στην κατεύθυνση της θέσης· "
             "«πέρα από SPY» = η ίδια κίνηση μείον του SPY στις ίδιες συνεδρίες. Τίποτα δεν αλλάζει (μόνο μέτρηση).", "",
             f"Συμπέρασμα (5 συνεδρίες): **{VERDICT_GR[result['verdict']]}** · μέσο πέρα από SPY "
             f"{_v(result['mean_excess_5_pct'])}% σε {result['judged']} θέσεις από {result['judged_days']} μέρες σοκ "
             f"(95%: {result['ci_95'] or 'άγνωστο'}; κρίση με ≥{MIN_CASES} θέσεις από ≥{MIN_DAYS} μέρες).", ""]
    for day, cells in result["by_shock_day"].items():
        d = date.fromisoformat(day)
        lines += [f"## Σοκ {d.day}/{d.month}", "", "| συνεδρίες μετά | γνωστές | μέση κίνηση % | πέρα από SPY % | θα κέρδιζαν |",
                  "|---|---|---|---|---|"]
        for n, c in cells.items():
            lines.append(f"| {n} | {c['known']} από {c['positions']} | {_v(c['mean_move_pct'])} | "
                         f"{_v(c['mean_excess_pct'])} | {c['held_better']} |")
        lines.append("")
    return "\n".join(lines)


def load_trades(root: Path) -> list[dict[str, Any]]:
    db = root / "data" / "paper_learning.db"
    if not db.exists():
        return []
    with closing(sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)) as conn:
        return [json.loads(r[0]) for r in conn.execute("SELECT record_json FROM paper_trade_ledger WHERE kind='trade'")]


def run(root: Path, *, trades: list[dict[str, Any]] | None = None, frames: Frames | None = None) -> dict[str, Any]:
    if frames is None:
        import holding_shadow as hs

        frames = hs.store_frames(root / "data" / "price_history")
    result = summarize(measure(load_trades(root) if trades is None else trades, frames))
    result["generated_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
    out = root / "output" / "paper_learning"
    out.mkdir(parents=True, exist_ok=True)
    (out / "shock_exits_latest.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    (out / "shock_exits_latest.md").write_text(render(result), encoding="utf-8")
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="What holding the positions a market shock closed would have done.")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parent)
    args = parser.parse_args(argv)
    result = run(args.root.resolve())
    print(f"SHOCK EXITS {result['verdict']} judged={result['judged']} of {result['positions']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
