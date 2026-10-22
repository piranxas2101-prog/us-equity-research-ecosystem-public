"""Thesis exit: a multi-day position is held while the reason it was opened still holds, judged on its own clock.

Owner's decision 2026-10-17 (ADR-001 step 3 evidence). Every night the research run re-scores each stock; a
position opened for a week, a month or a quarter is judged against the traders' nightly board of the archived runs
(``output/runs/<run>/artifacts/{yankos,periklis}_strategy_latest.json``), with what matters for its holding class:

* week (technical): the trader now has a candidate in the opposite direction, or a state playbook (the four
  original ones: support bounce, controlled breakout, early accumulation, pre-run breakout) now rejects the setup.
  The nine daily strategies are one-day events: their own "reject" the next night is not a broken thesis.
* month (technical and fundamental): the opposite candidate; the state playbook rejecting on ``NIGHTS_BELOW``
  nights in a row (one weak night is noise); risk now High; Giannis's score down by ``GIANNIS_DROP`` or more.
* quarter (fundamental): risk now High or Giannis's score down by ``GIANNIS_DROP``; technical moves only through
  the disaster stop.

Every class also has a disaster stop that widens with time: 1.1 x ATR(14) x sqrt(sessions), clamped to
``STOP_FLOOR``-``STOP_CAP`` (a 3% stop suits a day, not a quarter), and its time stop (5 / 20 / 60 sessions).
A thesis break decided at night exits at the next session's open; a session opening beyond the stop exits at the
open. A stock missing from a night's board is unknown, not a signal: the position is held. Starting values with a
documented rationale; they change only with the owner's approval. Since 2026-10-23 (ADR-001 step 3)
alpaca_multi_day_monitor.py applies these same rules to the paper positions held for a week, a month or a quarter.

``backtest`` replays every candidate of the archived runs with three rules - ``fixed`` (hold to the class's last
close), ``scaled_stop`` (the widening stop alone) and ``thesis`` (stop + thesis exit) - so the thesis effect is
seen on its own. The archive starts in September, so only the week class has complete windows for now.
"""
from __future__ import annotations

import argparse
from datetime import date, datetime, timezone
import json
import math
from pathlib import Path
import re
import time
from typing import Any

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent
RUNS_DIR = ROOT / "output" / "runs"
AGENTS = ("yankos", "periklis")
CLASSES = {"week": 5, "month": 20, "quarter": 60}
RULES = ("fixed", "scaled_stop", "thesis", "thesis_shock")
SHOCK_ATR = 2.0  # a market day beyond 2 x SPY's ATR(14) against the position is an abrupt change of sentiment
STATE_PLAYBOOKS = frozenset({"support_bounce_swing", "controlled_breakout_swing", "early_accumulation_swing",
                             "pre_run_breakout_confirmation"})
GIANNIS_DROP = 1.0
NIGHTS_BELOW = 2
STOP_ATR_MULT, STOP_FLOOR, STOP_CAP = 1.1, 0.015, 0.20
FIELDS = ("playbook_name", "signal_verdict", "trade_direction", "signal_score", "giannis_score", "risk_level",
          "atr_14_pct")
RUN_NAME = re.compile(r"^full_research_(\d{8})_\d{6}$")
MIN_BOARD_ROWS = 500  # the first September runs scored a handful of stocks only

Board = dict[str, dict[str, list[dict[str, Any]]]]  # agent -> ticker -> rows


def load_boards(runs_dir: Path = RUNS_DIR, since: date | None = None) -> list[tuple[date, Board]]:
    """(run date, board) per archived full run with complete trader boards, oldest first."""
    boards = []
    for folder in sorted(p for p in runs_dir.iterdir() if p.is_dir()) if runs_dir.is_dir() else []:
        match = RUN_NAME.match(folder.name)
        if not match:
            continue
        run_date = datetime.strptime(match.group(1), "%Y%m%d").date()
        if since and run_date < since:
            continue
        board: Board = {}
        for agent in AGENTS:
            path = folder / "artifacts" / f"{agent}_strategy_latest.json"
            try:
                rows = json.loads(path.read_text(encoding="utf-8")).get("results") or []
            except (OSError, ValueError):
                rows = []
            if len(rows) < MIN_BOARD_ROWS:
                board = {}
                break
            table: dict[str, list[dict[str, Any]]] = {}
            for row in rows:
                ticker = str(row.get("ticker") or "").upper()
                if ticker:
                    table.setdefault(ticker, []).append({k: row.get(k) for k in FIELDS})
            board[agent] = table
        if board:
            boards.append((run_date, board))
    return boards


def nightly_sessions(boards: list[tuple[date, Board]], session_dates: np.ndarray) -> dict[int, Board]:
    """Session index -> the last board decided before that session (a run on date D precedes the New York session
    on or after D; weekend reruns of the same data collapse onto Monday)."""
    out: dict[int, Board] = {}
    for run_date, board in boards:
        k = int((session_dates < run_date).sum())
        if k < len(session_dates):
            out[k] = board
    return out


def _f(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def thesis_break(cls: str, opening: dict[str, Any], rows: list[dict[str, Any]] | None,
                 state: dict[str, int]) -> str | None:
    """The reason the thesis no longer holds tonight, or None. ``state`` carries consecutive weak nights."""
    if rows is None:
        return None  # not on tonight's board: unknown, hold
    direction, playbook = opening["direction"], opening["playbook"]
    opposite = "short" if direction == "long" else "long"
    own = next((r for r in rows if r.get("playbook_name") == playbook), None)
    if cls != "quarter" and any(r.get("signal_verdict") == "candidate" and r.get("trade_direction") == opposite
                                for r in rows):
        return "direction_flip"
    setup_gone = playbook in STATE_PLAYBOOKS and own is not None and own.get("signal_verdict") == "reject"
    if cls == "week" and setup_gone:
        return "setup_gone"
    if cls == "month":
        state["weak_nights"] = state.get("weak_nights", 0) + 1 if setup_gone else 0
        if state["weak_nights"] >= NIGHTS_BELOW:
            return "setup_gone_nights"
    if cls in ("month", "quarter"):
        reference = own or rows[0]
        if str(reference.get("risk_level") or "") == "High":
            return "risk_high"
        entry, now = _f(opening.get("giannis_score")), _f(reference.get("giannis_score"))
        if entry is not None and now is not None and entry - now >= GIANNIS_DROP:
            return "fundamentals_weaker"
    return None


def scaled_stop(atr_pct: float | None, sessions: int) -> float | None:
    if atr_pct is None or atr_pct <= 0:
        return None
    return min(max(STOP_ATR_MULT * atr_pct / 100 * math.sqrt(sessions), STOP_FLOOR), STOP_CAP)


def market_shocks(spy: pd.DataFrame | None) -> dict[date, str]:
    """Sessions when the market's mood changed abruptly (owner 2026-10-22: every position is watched, whatever its
    holding class): SPY closing beyond ``SHOCK_ATR`` x its ATR(14) (ATR up to the previous session) - "down" or
    "up" - or the market turning from calm to volatile ("volatile", against every position)."""
    import macro_environment as me

    if spy is None or spy.empty:
        return {}
    frame = spy[["High", "Low", "Close"]].dropna() if {"High", "Low"} <= set(spy.columns) else None
    close = spy["Close"].astype(float).dropna()
    if frame is not None:
        prev = frame["Close"].shift(1)
        tr = pd.concat([frame["High"] - frame["Low"], (frame["High"] - prev).abs(), (frame["Low"] - prev).abs()],
                       axis=1).max(axis=1)
        atr = (tr.rolling(14).mean() / frame["Close"] * 100).shift(1)
    else:
        atr = (close.pct_change().abs().rolling(14).mean() * 100).shift(1)
    move = close.pct_change() * 100
    states = me.market_state_history(spy)
    out: dict[date, str] = {}
    for day in close.index:
        a, m = atr.get(day), move.get(day)
        if a is not None and m is not None and np.isfinite(a) and np.isfinite(m) and abs(m) >= SHOCK_ATR * a:
            out[day.date()] = "down" if m < 0 else "up"
    previous = states.shift(1)
    for day, state in states.items():
        before = previous.get(day)
        if isinstance(state, str) and isinstance(before, str) and state.endswith("volatile") and before.endswith("calm"):
            out.setdefault(day.date(), "volatile")
    return out


def replay(frame: pd.DataFrame, start: int, cls: str, rule: str, opening: dict[str, Any],
           nights: dict[int, Board], shocks: dict[date, str] | None = None) -> dict[str, Any] | None:
    """One hold entered at the open of session ``start``; None while its window is not stored. ``thesis_shock`` is
    the thesis exit plus the market-shock guard: a shock against the position after a session's close exits at the
    next open, whatever the holding class."""
    sessions = CLASSES[cls]
    end = start + sessions - 1
    if end >= len(frame):
        return None
    o, h, low, c = (frame[k].to_numpy(float) for k in ("Open", "High", "Low", "Close"))
    entry, long = o[start], opening["direction"] == "long"
    if not entry > 0:
        return None
    stop = scaled_stop(_f(opening.get("atr_14_pct")), sessions) if rule != "fixed" else None
    stop_level = None if stop is None else (entry * (1 - stop) if long else entry * (1 + stop))
    state: dict[str, int] = {}
    exit_index, exit_price, reason = end, c[end], "time_stop"
    dates = frame.index.date
    adverse = {"down", "volatile"} if long else {"up", "volatile"}
    for j in range(start, end + 1):
        if rule == "thesis_shock" and j > start and (shocks or {}).get(dates[j - 1]) in adverse:
            exit_index, exit_price, reason = j, o[j], "market_shock"
            break
        if rule in ("thesis", "thesis_shock") and j > start and j in nights:
            board = nights[j].get(opening["agent"]) or {}
            broken = thesis_break(cls, opening, board.get(opening["ticker"]), state)
            if broken:
                exit_index, exit_price, reason = j, o[j], broken
                break
        if stop_level is not None:
            if j > start and ((o[j] <= stop_level) if long else (o[j] >= stop_level)):
                exit_index, exit_price, reason = j, o[j], "stop_gap"
                break
            if (low[j] <= stop_level) if long else (h[j] >= stop_level):
                exit_index, exit_price, reason = j, stop_level, "stop"
                break
    result = (1.0 if long else -1.0) * (exit_price / entry - 1) * 100
    return {"exit_index": exit_index, "sessions_held": exit_index - start + 1, "result_pct": round(float(result), 3),
            "exit_reason": reason}


def openings(board: Board) -> list[dict[str, Any]]:
    """Every candidate of one night, as the trader scored it."""
    out = []
    for agent, table in board.items():
        for ticker, rows in table.items():
            for row in rows:
                if row.get("signal_verdict") == "candidate" and row.get("trade_direction") in ("long", "short"):
                    out.append({"agent": agent, "ticker": ticker, "playbook": row.get("playbook_name"),
                                "direction": row["trade_direction"], "giannis_score": row.get("giannis_score"),
                                "atr_14_pct": row.get("atr_14_pct")})
    return out


def backtest(runs_dir: Path, price_dir: Path) -> pd.DataFrame:
    import price_history_store as phs

    boards = load_boards(runs_dir)
    spy = phs.load("SPY", price_dir)
    if spy is None or not boards:
        return pd.DataFrame()
    shocks = market_shocks(spy)
    spy = spy[["Open", "Close"]].dropna()
    by_ticker: dict[str, list[tuple[date, dict[str, Any]]]] = {}
    for run_date, board in boards:
        for opening in openings(board):
            by_ticker.setdefault(opening["ticker"], []).append((run_date, opening))
    rows = []
    for ticker, items in by_ticker.items():
        frame = phs.load(ticker, price_dir)
        if frame is None:
            continue
        frame = frame[["Open", "High", "Low", "Close"]].dropna()
        dates = frame.index.date
        nights = nightly_sessions(boards, dates)
        spy_open = spy["Open"].reindex(frame.index).to_numpy(float)
        spy_close = spy["Close"].reindex(frame.index).to_numpy(float)
        seen: set[tuple[Any, ...]] = set()
        for run_date, opening in items:
            start = int((dates < run_date).sum())
            key = (start, opening["agent"], opening["playbook"], opening["direction"])
            if start >= len(frame) or key in seen:
                continue  # weekend reruns of the same data enter on the same Monday once
            seen.add(key)
            for cls in CLASSES:
                for rule in RULES:
                    out = replay(frame, start, cls, rule, opening, nights, shocks)
                    if out is None:
                        continue
                    s0, s1 = spy_open[start], spy_close[out["exit_index"]]
                    if not (np.isfinite(s0) and np.isfinite(s1)):
                        continue
                    bench = (s1 / s0 - 1) * 100
                    excess = out["result_pct"] - bench if opening["direction"] == "long" else out["result_pct"] + bench
                    rows.append({"agent": opening["agent"], "playbook": opening["playbook"],
                                 "direction": opening["direction"], "ticker": ticker, "session": str(dates[start]),
                                 "class": cls, "rule": rule, "result_pct": out["result_pct"],
                                 "excess_pct": round(excess, 3), "sessions_held": out["sessions_held"],
                                 "exit_reason": out["exit_reason"]})
    return pd.DataFrame(rows)


def _cluster_ci(diffs: pd.Series, clusters: pd.Series, boot: int = 1000, seed: int = 3) -> list[float] | None:
    """95% interval of the mean paired difference, resampling whole entry sessions (signals of one night move
    together)."""
    frame = pd.DataFrame({"d": diffs.to_numpy(), "c": clusters.to_numpy()}).groupby("c")["d"].agg(["sum", "count"])
    if len(frame) < 5:
        return None
    sums, counts = frame["sum"].to_numpy(), frame["count"].to_numpy()
    idx = np.random.default_rng(seed).integers(0, len(sums), size=(boot, len(sums)))
    values = sums[idx].sum(1) / counts[idx].sum(1)
    return [round(float(v), 3) for v in np.percentile(values, [2.5, 97.5])]


def summarize(replays: pd.DataFrame, by: tuple[str, ...] = ()) -> list[dict[str, Any]]:
    out = []
    if replays.empty:
        return out
    for key, group in replays.groupby(list(by) + ["class"]):
        key = key if isinstance(key, tuple) else (key,)
        base = group[group["rule"] == "fixed"].set_index(["agent", "playbook", "direction", "ticker", "session"])
        for rule, cell in group.groupby("rule"):
            entry = dict(zip(list(by) + ["class"], key))
            entry.update({"rule": rule, "n": int(len(cell)), "sessions": int(cell["session"].nunique()),
                          "mean_pct": round(float(cell["result_pct"].mean()), 3),
                          "mean_excess_pct": round(float(cell["excess_pct"].mean()), 3),
                          "beat_spy_pct": round(float((cell["excess_pct"] > 0).mean() * 100), 1),
                          "mean_sessions_held": round(float(cell["sessions_held"].mean()), 1),
                          "exit_reasons": cell["exit_reason"].value_counts().to_dict()})
            if rule != "fixed":
                keyed = cell.set_index(["agent", "playbook", "direction", "ticker", "session"])
                diff = keyed["excess_pct"].sub(base["excess_pct"]).dropna()
                if len(diff):
                    entry["vs_fixed_pct"] = round(float(diff.mean()), 3)
                    entry["vs_fixed_ci"] = _cluster_ci(diff, pd.Series(diff.index.get_level_values("session")))
                    # what the positions closed early did after the exit, per reason (fixed minus this rule, excess
                    # over SPY): negative means the exit avoided a loss; positive means it gave up a gain
                    reasons = keyed.loc[diff.index, "exit_reason"]
                    entry["after_exit_pct"] = {str(r): {"n": int((reasons == r).sum()),
                                                        "mean": round(float(-diff[reasons == r].mean()), 3)}
                                               for r in reasons.unique() if r != "time_stop"}
            out.append(entry)
    return out


def render(report: dict[str, Any]) -> str:
    names = {"week": "εβδομάδα", "month": "μήνας", "quarter": "τρίμηνο"}
    lines = ["# Έξοδος «όσο ισχύει ο λόγος» · backtest στα αρχειοθετημένα νυχτερινά runs", "",
             f"Runs {report['runs']} ({report['first_run']} – {report['last_run']}) · candidates {report['openings']} · "
             f"επαναλήψεις {report['replays']}. Είσοδος στο άνοιγμα της επόμενης συνεδρίας. Κανόνες: fixed (ως το "
             "τέλος), scaled_stop (stop 1.1×ATR×√συνεδρίες), thesis (stop + έξοδος όταν σπάσει ο λόγος, ανά διάρκεια).",
             "Μόνο όσες διάρκειες έχουν ολόκληρο το παράθυρό τους στην αποθήκη τιμών.", "",
             "## Συνολικά", "",
             "| διάρκεια | κανόνας | θέσεις | νύχτες εισόδου | μέσο % | υπέρβαση SPY % | νίκησαν SPY % | μέσες συνεδρίες | έναντι fixed (95%) | λόγοι εξόδου |",
             "|---|---|---|---|---|---|---|---|---|---|"]
    for e in report["overall"]:
        vs = "—" if "vs_fixed_pct" not in e else (f"{e['vs_fixed_pct']:+.2f}" + (
            f" ({e['vs_fixed_ci'][0]:+.2f} έως {e['vs_fixed_ci'][1]:+.2f})" if e.get("vs_fixed_ci") else ""))
        reasons = ", ".join(f"{k} {v}" for k, v in e["exit_reasons"].items())
        lines.append(f"| {names[e['class']]} | {e['rule']} | {e['n']} | {e['sessions']} | {e['mean_pct']:+.2f} | "
                     f"{e['mean_excess_pct']:+.2f} | {e['beat_spy_pct']} | {e['mean_sessions_held']} | {vs} | {reasons} |")
    lines += ["", "## Τι έκαναν οι θέσεις μετά την έξοδο (υπέρβαση SPY από την έξοδο ως το τέλος της διάρκειας)", "",
              "Αρνητικό: η έξοδος γλίτωσε ζημιά. Θετικό: έχασε κέρδος. Αυτό δείχνει αν ο λόγος εξόδου έχει πληροφορία ή "
              "απλώς μικραίνει το κράτημα.", ""]
    for e in report["overall"]:
        if e["rule"] == "thesis" and e.get("after_exit_pct"):
            lines.append(f"- {names[e['class']]}: " + "; ".join(
                f"{r} {v['n']} θέσεις, μετά {v['mean']:+.2f}%" for r, v in e["after_exit_pct"].items()))
    lines += ["", "## Ανά trader, στρατηγική και κατεύθυνση (thesis έναντι fixed, ≥ 30 θέσεις)", "",
              "| trader · στρατηγική · κατεύθυνση | διάρκεια | θέσεις | fixed υπέρβαση % | thesis υπέρβαση % | διαφορά (95%) |",
              "|---|---|---|---|---|---|"]
    fixed = {(e["agent"], e["playbook"], e["direction"], e["class"]): e for e in report["strategies"] if e["rule"] == "fixed"}
    for e in report["strategies"]:
        if e["rule"] != "thesis" or e["n"] < 30:
            continue
        f = fixed.get((e["agent"], e["playbook"], e["direction"], e["class"]))
        ci = e.get("vs_fixed_ci")
        lines.append(f"| {e['agent']} · {e['playbook']} · {e['direction']} | {names[e['class']]} | {e['n']} | "
                     f"{f['mean_excess_pct'] if f else '—'} | {e['mean_excess_pct']} | {e.get('vs_fixed_pct', '—')}"
                     + (f" ({ci[0]:+.2f} έως {ci[1]:+.2f})" if ci else "") + " |")
    lines += ["", "Λίγες νύχτες εισόδου σημαίνουν πλατιά διαστήματα: ένδειξη, όχι απόφαση. Τίποτα δεν αλλάζει χωρίς "
              "την έγκριση του ιδιοκτήτη."]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    import price_history_store as phs

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--runs", type=Path, default=RUNS_DIR)
    parser.add_argument("--dir", type=Path, default=phs.DEFAULT_DIR)
    parser.add_argument("--out", type=Path, default=ROOT / "output" / "strategy_lab")
    args = parser.parse_args(argv)
    started = time.perf_counter()
    boards = load_boards(args.runs)
    replays = backtest(args.runs, args.dir)
    report = {"schema": "ecosystem.thesis_exit_backtest.v1", "generated_at": datetime.now(timezone.utc).isoformat(),
              "runs": len(boards), "first_run": str(boards[0][0]) if boards else None,
              "last_run": str(boards[-1][0]) if boards else None,
              "openings": int(replays.drop_duplicates(["agent", "playbook", "direction", "ticker", "session"]).shape[0])
              if len(replays) else 0, "replays": int(len(replays)),
              "parameters": {"giannis_drop": GIANNIS_DROP, "nights_below": NIGHTS_BELOW, "stop_atr_mult": STOP_ATR_MULT,
                             "stop_floor": STOP_FLOOR, "stop_cap": STOP_CAP},
              "overall": summarize(replays), "strategies": summarize(replays, ("agent", "playbook", "direction")),
              "seconds": round(time.perf_counter() - started, 1)}
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "thesis_exit_latest.json").write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    (args.out / "thesis_exit_latest.md").write_text(render(report), encoding="utf-8")
    print(json.dumps({"runs": report["runs"], "openings": report["openings"], "replays": report["replays"],
                      "seconds": report["seconds"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
