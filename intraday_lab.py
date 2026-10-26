"""Intraday lab (rules review T8, owner 2026-10-26): holding periods shorter than a session, and the hour of entry.

The horizon curve (exit_lab.py --horizon-curve) is built on daily bars: entry at the signal's close, exits after
whole sessions. Live, a signal of session D is entered on session D+1 during the day (Miltos), and most entries
come in the first two hours. This lab asks, on consolidated (SIP) 15-minute bars of the last year:

* **holding periods of minutes to a session** - 15, 30, 60, 120, 180, 300 minutes, to the close, and 390 minutes
  (the same time of the next session);
* **the hour of entry** - 09:30, 10:00, 10:30, 11:00, 12:00, 13:00, 14:00, 15:00 New York on session D+1.

Every signal is paired with a **random session of the same stock** (at least ``AWAY_DAYS`` from the signal), entered
at the same time and held as long, so the stock's own drift and the time-of-day pattern of all stocks cancel out;
the edge is the paired difference. Judged per strategy, side, entry time and holding period: mean edge, a 95%
month-block bootstrap interval and a Benjamini-Hochberg control over all tests (q <= ``FDR_Q``). Entry at the open
of the 15-minute bar starting at the entry time, exit at the close of the last bar of the holding period; no
confirmation, no stop (the raw value of the signal at that time). Read-only market data; the bars are cached in
``data/intraday_lab_cache``. Research only: nothing reads this report until the owner decides.
"""
from __future__ import annotations

import argparse
from datetime import date, datetime, timedelta, timezone
import json
import math
from pathlib import Path
import random
import time
from typing import Any

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent
CACHE = ROOT / "data" / "intraday_lab_cache"
ENTRY_TIMES = ("09:30", "10:00", "10:30", "11:00", "12:00", "13:00", "14:00", "15:00")
HOLDS = (15, 30, 60, 120, 180, 300, "close", 390)
PER_GROUP = 200
AWAY_DAYS = 5
LOOKBACK_DAYS = 365
BOOT = 1000
FDR_Q = 0.10
rng = np.random.default_rng(13)


def sessions_between(start: date, end: date) -> list[date]:
    import trading_clock

    out, day = [], start
    while day <= end:
        if trading_clock.session(day) is not None:
            out.append(day)
        day += timedelta(days=1)
    return out


def regular_bars(symbol: str, first: date, fetch) -> list[dict[str, Any]]:
    """Regular-session 15-minute SIP bars of ``first`` and the next session, cached."""
    path = CACHE / f"{symbol.replace('/', '_')}_{first.isoformat()}.json"
    if path.is_file():
        return json.loads(path.read_text(encoding="utf-8"))
    import alpaca_paper_trader as t
    import entry_confirmation_rules as rules

    start = datetime.combine(first, datetime.min.time()).replace(tzinfo=t.EASTERN_TZ).astimezone(timezone.utc)
    end = start + timedelta(days=5)
    bars = fetch(symbol, "15Min", lookback_hours=24 * 5, end_dt=end)
    bars = sorted(rules.regular_hours(bars, parse=t.parse_alpaca_timestamp, tz=t.EASTERN_TZ), key=lambda b: b["t"])
    days = sorted({t.parse_alpaca_timestamp(b["t"]).astimezone(t.EASTERN_TZ).date() for b in bars})
    keep = [d for d in days if d >= first][:2]
    out = [{"t": b["t"], "o": b["o"], "c": b["c"],
            "d": t.parse_alpaca_timestamp(b["t"]).astimezone(t.EASTERN_TZ).strftime("%Y-%m-%d %H:%M")}
           for b in bars if t.parse_alpaca_timestamp(b["t"]).astimezone(t.EASTERN_TZ).date() in keep]
    CACHE.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(out), encoding="utf-8")
    return out


def returns(bars: list[dict[str, Any]], day: date, long: bool) -> dict[tuple[str, Any], float]:
    """(entry time, hold) -> signed return in % for an entry on ``day``."""
    out: dict[tuple[str, Any], float] = {}
    stamp = [b["d"] for b in bars]
    first_day = [k for k, s in enumerate(stamp) if s.startswith(day.isoformat())]
    if not first_day:
        return out
    last_of_day = first_day[-1]
    for entry in ENTRY_TIMES:
        key = f"{day.isoformat()} {entry}"
        if key not in stamp:
            continue
        k = stamp.index(key)
        price = bars[k]["o"]
        if not price:
            continue
        for hold in HOLDS:
            if hold == "close":
                exit_index = last_of_day
            else:
                exit_index = k + int(hold) // 15 - 1
                if hold != 390 and exit_index > last_of_day:
                    continue
            if exit_index >= len(bars) or exit_index < k:
                continue
            move = (bars[exit_index]["c"] / price - 1) * 100
            out[(entry, hold)] = move if long else -move
    return out


def _block_test(diffs: np.ndarray, months: np.ndarray) -> dict[str, Any] | None:
    frame = pd.DataFrame({"d": diffs, "m": months}).groupby("m")["d"].agg(["sum", "count"])
    sums, counts = frame["sum"].to_numpy(), frame["count"].to_numpy()
    if len(sums) < 6:
        return None
    idx = rng.integers(0, len(sums), size=(BOOT, len(sums)))
    boot = sums[idx].sum(1) / counts[idx].sum(1)
    mean = float(sums.sum() / counts.sum())
    se = float(np.std(boot)) or 1e-9
    low, high = np.percentile(boot, [2.5, 97.5])
    return {"mean": round(mean, 3), "ci": [round(float(low), 3), round(float(high), 3)],
            "p": float(math.erfc(abs(mean / se) / math.sqrt(2)))}


def _bh(p: list[float]) -> list[float]:
    order = sorted(range(len(p)), key=lambda k: p[k])
    q, running = [1.0] * len(p), 1.0
    for rank in range(len(order) - 1, -1, -1):
        k = order[rank]
        running = min(running, p[k] * len(p) / (rank + 1))
        q[k] = running
    return q


def summarize(rows: pd.DataFrame) -> list[dict[str, Any]]:
    out = []
    for (strategy, side, entry, hold), cell in rows.groupby(["strategy", "side", "entry", "hold"]):
        test = _block_test(cell["edge"].to_numpy(), cell["month"].to_numpy())
        item = {"strategy": strategy, "side": side, "entry": entry, "hold": hold, "n": int(len(cell)),
                "mean_signal_pct": round(float(cell["signal"].mean()), 3),
                "mean_random_pct": round(float(cell["random"].mean()), 3)}
        if test:
            item.update({"edge_pct": test["mean"], "edge_ci": test["ci"], "p": test["p"]})
        out.append(item)
    tested = [e for e in out if "p" in e]
    for e, q in zip(tested, _bh([e["p"] for e in tested])):
        e["q"] = round(q, 4)
        e["verdict"] = ("edge" if q <= FDR_Q and e["edge_pct"] > 0 else
                        "worse than random" if q <= FDR_Q and e["edge_pct"] < 0 else "no clear edge")
        e["p"] = round(e["p"], 5)
    return out


def render(report: dict[str, Any]) -> str:
    lines = ["# Εργαστήριο μέσα στη μέρα (T8)", "",
             f"{report['signals']} σήματα του τελευταίου έτους (έως {PER_GROUP} ανά στρατηγική και κατεύθυνση), είσοδος "
             "την επόμενη συνεδρία σε 8 ώρες, κράτημα από 15 λεπτά ως μία συνεδρία, σε σύγκριση με τυχαία μέρα της ίδιας "
             f"μετοχής στην ίδια ώρα και διάρκεια. Έλεγχος FDR q ≤ {FDR_Q} σε {report['tests']} ελέγχους.", "",
             "## Πραγματικό πλεονέκτημα (περνά τον έλεγχο)", "",
             "| στρατηγική | κατεύθ. | είσοδος NY | κράτημα | n | πλεονέκτημα % | 95% |", "|---|---|---|---|---|---|---|"]
    edges = [e for e in report["results"] if e.get("verdict") == "edge"]
    lines += [f"| {e['strategy']} | {e['side']} | {e['entry']} | {e['hold']} | {e['n']} | {e['edge_pct']:+.2f} | "
              f"{e['edge_ci'][0]:+.2f} έως {e['edge_ci'][1]:+.2f} |" for e in sorted(edges, key=lambda e: -e["edge_pct"])] or \
        ["| — | | | | | | |"]
    worse = [e for e in report["results"] if e.get("verdict") == "worse than random"]
    lines += ["", "## Χειρότερα από τυχαία", "", "| στρατηγική | κατεύθ. | είσοδος NY | κράτημα | n | % |", "|---|---|---|---|---|---|"]
    lines += [f"| {e['strategy']} | {e['side']} | {e['entry']} | {e['hold']} | {e['n']} | {e['edge_pct']:+.2f} |"
              for e in sorted(worse, key=lambda e: e["edge_pct"])] or ["| — | | | | | |"]
    lines += ["", "## Ώρα εισόδου, όλες οι στρατηγικές μαζί (κράτημα ως το κλείσιμο)", "",
              "| είσοδος NY | n | μέσο σήμα % | μέσο τυχαίο % | διαφορά % |", "|---|---|---|---|---|"]
    lines += [f"| {r['entry']} | {r['n']} | {r['signal']:+.3f} | {r['random']:+.3f} | {r['edge']:+.3f} |"
              for r in report["by_entry_to_close"]]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--signals", type=Path, nargs="+",
                        default=[ROOT / "output" / "strategy_lab" / "strategy_lab_signals_latest.csv.gz",
                                 ROOT / "output" / "strategy_lab" / "strategy_lab_legacy_signals_latest.csv.gz"])
    parser.add_argument("--out", type=Path, default=ROOT / "output" / "strategy_lab")
    parser.add_argument("--per-group", type=int, default=PER_GROUP)
    args = parser.parse_args(argv)
    from alpaca_paper_trader import fetch_intraday_bars

    started = time.perf_counter()
    frames = [pd.read_csv(f, usecols=["strategy", "symbol", "date", "side"], parse_dates=["date"])
              for f in args.signals if f.exists()]
    signals = pd.concat(frames, ignore_index=True)
    last = signals["date"].max()
    signals = signals[signals["date"] >= last - pd.Timedelta(days=LOOKBACK_DAYS)]
    sample = signals.sample(frac=1.0, random_state=11).groupby(["strategy", "side"]).head(args.per_group)
    calendar = sessions_between((last - pd.Timedelta(days=LOOKBACK_DAYS + 10)).date(), date.today())
    following = {d: calendar[k + 1] for k, d in enumerate(calendar[:-1])}
    draw = random.Random(17)
    rows = []
    for n, signal in enumerate(sample.itertuples(index=False), 1):
        day = following.get(signal.date.date())
        if day is None:
            continue
        choices = [d for d in calendar[:-2] if abs((d - day).days) > AWAY_DAYS]
        other = draw.choice(choices)
        try:
            mine = returns(regular_bars(signal.symbol, day, fetch_intraday_bars), day, signal.side == "long")
            base = returns(regular_bars(signal.symbol, other, fetch_intraday_bars), other, signal.side == "long")
        except Exception as exc:  # noqa: BLE001 - a missing symbol or a data error skips the signal
            print(f"[INTRADAY] {signal.symbol} {day}: {str(exc)[:80]}", flush=True)
            continue
        for key, value in mine.items():
            if key in base:
                rows.append((signal.strategy, signal.side, key[0], str(key[1]), str(day)[:7], value, base[key],
                             value - base[key]))
        if n % 250 == 0:
            print(f"[INTRADAY] {n}/{len(sample)} signals, {len(rows)} pairs", flush=True)
    frame = pd.DataFrame(rows, columns=["strategy", "side", "entry", "hold", "month", "signal", "random", "edge"])
    results = summarize(frame) if len(frame) else []
    close = frame[frame["hold"] == "close"].groupby("entry")[["signal", "random", "edge"]].agg(["mean", "count"])
    by_entry = [{"entry": e, "n": int(close.loc[e, ("edge", "count")]), "signal": float(close.loc[e, ("signal", "mean")]),
                 "random": float(close.loc[e, ("random", "mean")]), "edge": float(close.loc[e, ("edge", "mean")])}
                for e in ENTRY_TIMES if e in close.index]
    report = {"schema": "ecosystem.intraday_lab.v1", "generated_at": datetime.now(timezone.utc).isoformat(),
              "signals": int(len(sample)), "pairs": int(len(frame)), "tests": sum(1 for e in results if "q" in e),
              "entry_times": list(ENTRY_TIMES), "holds": [str(h) for h in HOLDS],
              "seconds": round(time.perf_counter() - started, 1), "results": results, "by_entry_to_close": by_entry}
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "intraday_lab_latest.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    (args.out / "intraday_lab_latest.md").write_text(render(report), encoding="utf-8")
    print(json.dumps({"signals": report["signals"], "pairs": report["pairs"], "seconds": report["seconds"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
