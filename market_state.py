"""The state of the market: the ecosystem reads it, explains it and learns what works in it (owner 2026-11-04).

Owner's words: "the market's sentiment is the state of the market" - the ecosystem must learn to explain and
understand it by itself from the data it has, use it every day, and slowly become more human; mistakes are seen and
corrected through repetition (docs/MARKET_STATE_LESSON.md, the owner chose option A and a Greek paragraph).

**1. Read** - every night, from the price store only (``data/price_history``, every stored company), for every
session of the last five years, five sides of the market (each one unknown when its data are missing):

* trend (SPY against its 20/50/200-day averages and its 3-month return: four votes) - up / sideways / down;
* mood (SPY's 20-day realized volatility, and against its 60-day one) - calm / anxious / panic;
* participation (share of companies above their own 50-day average, at least ``MIN_COMPANIES`` priced) - broad /
  mixed / narrow, with new 52-week highs and lows for the explanation;
* risk appetite (small companies, IWM, against SPY over 20 sessions; QQQ for the explanation) - on / neutral / off;
* what the market rewards (share of companies up over the last 5 sessions) - longs / both / shorts.

**2. Understand** - the five-year signals of the strategy lab (daily strategies and the original playbooks) are
labelled with the state of the session they were taken in (no look-ahead: the state at that close). For every
strategy, side, holding horizon and value of each side of the market: the mean **directional return** (what the
position made, SPY added back to the lab's excess), averaged per date, a month-block bootstrap interval, the share of
years with the same sign, and a Benjamini-Hochberg control (q <= ``FDR_Q``) over all cells. A cell is "against"
when it loses with that evidence, "supported" when it gains with it. Rebuilt when the lab's signals change.

**3. Use** - ``fit``: a proposal is "against" the market when at least one side of today's market is significantly
against its strategy and side and none is significantly for it; "supported" in the mirror case; otherwise
"neutral" (or "unknown" with no reading). Koulis does not send an "against" proposal (it is recorded and measured
as left out, so the filter's cost or saving shows) and puts "supported" ones first. No risk limit, size or stop
changes. Every night the report also says whether yesterday's reading of what the market rewards came true.

**Continuity** (owner 2026-11-04: "it must be followed, to see whether it changes, so that we change too"): every
night the reading says what changed since yesterday, how long each side has held, and which strategies Koulis keeps
out from today or lets through again; ``market_state_review_latest.md`` reads every paper session so far with the
state of the night before and shows how the proposals the filter judges against / supported did.

    market_state.py                 # nightly: read, (re)build the evidence when the lab changed, explain
    market_state.py --rebuild       # rebuild the evidence now
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import date, datetime, timezone
import json
import math
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent
PRICES = ROOT / "data" / "price_history"
LAB = ROOT / "output" / "strategy_lab"
SIGNALS = ("strategy_lab_signals_latest.csv.gz", "strategy_lab_legacy_signals_latest.csv.gz")
OUT = ROOT / "output" / "market_state_latest.json"
EVIDENCE = ROOT / "output" / "market_state_evidence.json"
HISTORY = ROOT / "output" / "market_state_history.jsonl"
REVIEW = ROOT / "output" / "market_state_review_latest"
SCHEMA = "ecosystem.market_state.v1"
MIN_COMPANIES = 2000
DIMENSIONS = ("trend", "mood", "participation", "appetite", "rewards")
VALUES = {"trend": ("up", "sideways", "down"), "mood": ("calm", "anxious", "panic"),
          "participation": ("broad", "mixed", "narrow"), "appetite": ("on", "neutral", "off"),
          "rewards": ("longs", "both", "shorts")}
GREEK = {"up": "ανεβαίνει", "sideways": "κινείται πλάγια", "down": "πέφτει", "calm": "ήρεμη", "anxious": "ανήσυχη",
         "panic": "σε πανικό", "broad": "ευρεία", "mixed": "μικτή", "narrow": "στενή", "on": "θέλει ρίσκο",
         "neutral": "ουδέτερη", "off": "φοβάται", "longs": "τα long", "both": "και τα δύο", "shorts": "τα short"}
DIMENSION_GR = {"trend": "τάση", "mood": "διάθεση", "participation": "συμμετοχή", "appetite": "διάθεση για ρίσκο",
                "rewards": "τι ανταμείβει"}
# starting levels (owner's approval 2026-11-04 of the five sides; corrected through repetition)
CALM_BELOW, PANIC_FROM, PANIC_RISE_FROM, RISE_RATIO, PANIC_RATIO = 15.0, 30.0, 20.0, 1.25, 1.75
BROAD_FROM, NARROW_BELOW = 60.0, 40.0
APPETITE_POINTS = 1.5
REWARD_LONGS_FROM, REWARD_SHORTS_BELOW = 55.0, 45.0
HORIZONS = (1, 5, 20, 60)
FDR_Q = 0.10
BOOTSTRAP = 1000
BLOCK_MONTHS = 3
MIN_DATES = 20
MAX_STATE_AGE_DAYS = 5


# ---------------------------------------------------------------- 1. read

def load_closes(directory: Path | None = None) -> pd.DataFrame:
    """Close of every stored symbol by session (columns = symbols)."""
    series = {}
    for path in sorted((directory or PRICES).glob("*.csv.gz")):
        try:
            frame = pd.read_csv(path, usecols=["Date", "Close"], index_col="Date", parse_dates=True)
        except (OSError, ValueError, EOFError):
            continue
        close = frame["Close"].astype(float)
        series[path.name[:-len(".csv.gz")]] = close[~close.index.duplicated(keep="last")]
    return pd.DataFrame(series).sort_index()


def _sign(x: pd.Series) -> pd.Series:
    return np.sign(x).where(x.notna())


def read_history(closes: pd.DataFrame) -> pd.DataFrame:
    """One row per session: the five sides of the market and the figures behind them (NaN / None = unknown)."""
    spy = closes.get("SPY")
    if spy is None:
        return pd.DataFrame()
    spy = spy.dropna()
    idx = spy.index
    out = pd.DataFrame(index=idx)
    out["spy_close"] = spy
    for w in (20, 50, 200):
        out[f"spy_vs_sma{w}_pct"] = (spy / spy.rolling(w).mean() - 1) * 100
    out["spy_return_63d_pct"] = spy.pct_change(63) * 100
    votes = sum(_sign(out[c]) for c in ("spy_vs_sma20_pct", "spy_vs_sma50_pct", "spy_vs_sma200_pct",
                                         "spy_return_63d_pct"))
    out["trend"] = np.where(votes.isna(), None, np.where(votes >= 2, "up", np.where(votes <= -2, "down", "sideways")))
    daily = spy.pct_change()
    vol20 = daily.rolling(20).std() * math.sqrt(252) * 100
    vol60 = daily.rolling(60).std() * math.sqrt(252) * 100
    out["vol20_pct"], out["vol60_pct"] = vol20, vol60
    ratio = vol20 / vol60
    out["mood"] = np.where(vol20.isna() | vol60.isna(), None,
                           np.where((vol20 >= PANIC_FROM) | ((vol20 >= PANIC_RISE_FROM) & (ratio >= PANIC_RATIO)),
                                    "panic", np.where((vol20 < CALM_BELOW) & (ratio < RISE_RATIO), "calm", "anxious")))
    stocks = closes.drop(columns=[c for c in ("SPY", "QQQ", "IWM") if c in closes.columns]).reindex(idx)
    priced = stocks.notna()
    sma50 = stocks.rolling(50, min_periods=50).mean()
    above = (stocks > sma50) & sma50.notna()
    counted = sma50.notna().sum(axis=1)
    out["companies_counted"] = counted
    share = above.sum(axis=1) / counted.replace(0, np.nan) * 100
    enough = counted >= MIN_COMPANIES
    out["above_sma50_pct"] = share.where(enough)
    out["participation"] = np.where(~enough, None, np.where(share >= BROAD_FROM, "broad",
                                                             np.where(share < NARROW_BELOW, "narrow", "mixed")))
    high = stocks.rolling(252, min_periods=200).max()
    low = stocks.rolling(252, min_periods=200).min()
    out["new_highs"] = ((stocks >= high) & priced).sum(axis=1).where(enough)
    out["new_lows"] = ((stocks <= low) & priced).sum(axis=1).where(enough)
    up5 = stocks.pct_change(5, fill_method=None) > 0
    known5 = stocks.pct_change(5, fill_method=None).notna()
    adv5 = up5.sum(axis=1) / known5.sum(axis=1).replace(0, np.nan) * 100
    adv5 = adv5.where(known5.sum(axis=1) >= MIN_COMPANIES)
    out["up_5d_pct"] = adv5
    out["rewards"] = np.where(adv5.isna(), None, np.where(adv5 >= REWARD_LONGS_FROM, "longs",
                                                           np.where(adv5 <= REWARD_SHORTS_BELOW, "shorts", "both")))
    up1 = stocks.pct_change(1, fill_method=None)
    out["up_1d_pct"] = ((up1 > 0).sum(axis=1) / up1.notna().sum(axis=1).replace(0, np.nan) * 100).where(
        up1.notna().sum(axis=1) >= MIN_COMPANIES)
    spy20 = spy.pct_change(20) * 100
    for name in ("IWM", "QQQ"):
        other = closes.get(name)
        out[f"{name.lower()}_vs_spy_20d_pts"] = (other.reindex(idx).pct_change(20, fill_method=None) * 100 - spy20
                                                 if other is not None else np.nan)
    rel = out["iwm_vs_spy_20d_pts"]
    out["appetite"] = np.where(rel.isna(), None, np.where(rel > APPETITE_POINTS, "on",
                                                           np.where(rel < -APPETITE_POINTS, "off", "neutral")))
    return out


def _num(value: Any, digits: int = 1) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(number) else round(number, digits)


def snapshot(history: pd.DataFrame, day: Any = None) -> dict[str, Any]:
    """The reading of one session (the last one by default)."""
    if history.empty:
        return {"session": None, **{d: None for d in DIMENSIONS}}
    row = history.loc[day] if day is not None else history.iloc[-1]
    session = (day or history.index[-1])
    return {"session": pd.Timestamp(session).date().isoformat(),
            **{d: (row[d] if isinstance(row[d], str) else None) for d in DIMENSIONS},
            "figures": {k: _num(row.get(k), 2) for k in (
                "spy_close", "spy_vs_sma20_pct", "spy_vs_sma50_pct", "spy_vs_sma200_pct", "spy_return_63d_pct",
                "vol20_pct", "vol60_pct", "above_sma50_pct", "companies_counted", "new_highs", "new_lows",
                "up_5d_pct", "up_1d_pct", "iwm_vs_spy_20d_pts", "qqq_vs_spy_20d_pts")}}


# ---------------------------------------------------------------- 2. understand

def read_signals(lab: Path | None = None) -> pd.DataFrame:
    frames = []
    for name in SIGNALS:
        path = (lab or LAB) / name
        if path.exists():
            frames.append(pd.read_csv(path, usecols=lambda c: c in {"strategy", "date", "side", "x1", "x5", "x20",
                                                                      "x60"}, parse_dates=["date"]))
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def _bh(pvalues: list[float], q: float = FDR_Q) -> list[bool]:
    order = sorted(range(len(pvalues)), key=lambda k: pvalues[k])
    passed = [False] * len(pvalues)
    cut = -1
    for rank, k in enumerate(order, start=1):
        if pvalues[k] <= q * rank / len(pvalues):
            cut = rank
    for rank, k in enumerate(order, start=1):
        passed[k] = rank <= cut
    return passed


def _bootstrap(per_date: pd.Series, rng: np.random.Generator) -> tuple[float, float, float]:
    """(low, high, two-sided p) of the mean of per-date means, resampling blocks of ``BLOCK_MONTHS`` months."""
    months = per_date.groupby(per_date.index.to_period("M")).agg(["sum", "count"])
    sums, counts = months["sum"].to_numpy(), months["count"].to_numpy()
    m = len(sums)
    starts = rng.integers(0, m, size=(BOOTSTRAP, math.ceil(m / BLOCK_MONTHS)))
    picked = (starts[:, :, None] + np.arange(BLOCK_MONTHS)).reshape(BOOTSTRAP, -1)[:, :m] % m
    means = sums[picked].sum(axis=1) / counts[picked].sum(axis=1)
    low, high = np.percentile(means, [2.5, 97.5])
    p = 2 * min((means <= 0).mean(), (means >= 0).mean())
    return float(low), float(high), float(min(1.0, max(p, 1.0 / BOOTSTRAP)))


def build_evidence(signals: pd.DataFrame, history: pd.DataFrame, seed: int = 7) -> dict[str, Any]:
    """Per strategy, side, horizon and value of each side of the market: mean directional return and its judgement."""
    if signals.empty or history.empty:
        return {"cells": []}
    spy = history["spy_close"]
    frame = signals.copy()
    frame["date"] = pd.to_datetime(frame["date"])
    frame = frame[frame["date"].isin(history.index)]
    sign = np.where(frame["side"] == "short", -1.0, 1.0)
    rng = np.random.default_rng(seed)
    cells: list[dict[str, Any]] = []
    for h in HORIZONS:
        col = f"x{h}"
        if col not in frame:
            continue
        spy_forward = (spy.shift(-h) / spy - 1) * 100
        frame[f"abs{h}"] = frame[col] + sign * spy_forward.reindex(frame["date"]).to_numpy()
    for dim in DIMENSIONS:
        frame[dim] = history[dim].reindex(frame["date"]).to_numpy()
    for (strategy, side), group in frame.groupby(["strategy", "side"]):
        for h in HORIZONS:
            if f"abs{h}" not in group:
                continue
            part = group[["date", f"abs{h}", *DIMENSIONS]].dropna(subset=[f"abs{h}"])
            for dim in DIMENSIONS:
                for value in VALUES[dim]:
                    chosen = part[part[dim] == value]
                    per_date = chosen.groupby("date")[f"abs{h}"].mean()
                    if len(per_date) < MIN_DATES:
                        continue
                    low, high, p = _bootstrap(per_date, rng)
                    years = per_date.groupby(per_date.index.year).mean()
                    mean = float(per_date.mean())
                    same = int((np.sign(years) == np.sign(mean)).sum())
                    cells.append({"strategy": strategy, "side": side, "horizon": h, "dimension": dim, "value": value,
                                  "signals": int(len(chosen)), "dates": int(len(per_date)),
                                  "mean_return_pct": round(mean, 3), "ci95_pct": [round(low, 3), round(high, 3)],
                                  "p": round(p, 4), "years_same_sign": f"{same}/{len(years)}",
                                  "_years_ok": same > len(years) / 2})
    passed = _bh([c["p"] for c in cells])
    for cell, ok in zip(cells, passed):
        significant = ok and cell.pop("_years_ok")
        cell["verdict"] = ("supported" if cell["mean_return_pct"] > 0 else "against") if significant else "unclear"
    return {"cells": cells}


# ---------------------------------------------------------------- 3. use

def _nearest_horizon(sessions: Any) -> int:
    try:
        value = float(sessions)
    except (TypeError, ValueError):
        return 1  # same-day
    return min(HORIZONS, key=lambda h: abs(h - max(value, 1)))


def fit(state: dict[str, Any] | None, evidence: dict[str, Any] | None, playbook: str, side: str,
        holding_sessions: Any = None, today: date | None = None) -> dict[str, Any]:
    """How a proposal fits today's market: supported / against / neutral / unknown, with the reasons."""
    if not state or not evidence or not state.get("session"):
        return {"fit": "unknown", "reasons": ["no market reading"]}
    if today is not None and (today - date.fromisoformat(str(state["session"]))).days > MAX_STATE_AGE_DAYS:
        return {"fit": "unknown", "reasons": [f"market reading of {state['session']} is too old"]}
    horizon = _nearest_horizon(holding_sessions)
    table = {(c["dimension"], c["value"]): c for c in evidence.get("cells") or []
             if c["strategy"] == playbook and c["side"] == str(side).lower() and c["horizon"] == horizon}
    votes = []
    for dim in DIMENSIONS:
        value = state.get(dim)
        cell = table.get((dim, value)) if value else None
        if cell and cell["verdict"] != "unclear":
            votes.append((cell["verdict"], f"{DIMENSION_GR[dim]} {GREEK.get(value, value)}: "
                                           f"{cell['mean_return_pct']:+.2f}% σε {horizon} συνεδρίες ({cell['dates']} μέρες)"))
    against = [r for v, r in votes if v == "against"]
    support = [r for v, r in votes if v == "supported"]
    verdict = "against" if against and not support else "supported" if support and not against else "neutral"
    return {"fit": verdict, "horizon": horizon, "reasons": against + support}


def load(path: Path | None = None) -> dict[str, Any] | None:
    try:
        payload = json.loads((path or OUT).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


# ---------------------------------------------------------------- explanation

def paragraph(state: dict[str, Any], *, yesterday: dict[str, Any] | None = None,
              agents: dict[str, int] | None = None) -> str:
    """The reading in plain Greek; unknown parts are said to be unknown."""
    f = state.get("figures") or {}

    def pct(key: str, signed: bool = True) -> str:
        value = f.get(key)
        return "άγνωστο" if value is None else (f"{value:+.1f}%" if signed else f"{value:.0f}%")

    def count(key: str) -> str:
        value = f.get(key)
        return "άγνωστα" if value is None else str(int(value))

    parts = []
    trend = state.get("trend")
    parts.append(f"Η αγορά (SPY) {GREEK[trend]}" if trend else "Η τάση της αγοράς είναι άγνωστη")
    parts[-1] += f": {pct('spy_vs_sma200_pct')} από τον μέσο όρο 200 ημερών, {pct('spy_return_63d_pct')} σε 3 μήνες."
    mood = state.get("mood")
    parts.append((f"Η διάθεση είναι {GREEK[mood]}" if mood else "Η διάθεση είναι άγνωστη")
                 + f" (μεταβλητότητα 20 ημερών {pct('vol20_pct', False)}, 60 ημερών {pct('vol60_pct', False)}).")
    participation = state.get("participation")
    if participation:
        parts.append(f"Η συμμετοχή είναι {GREEK[participation]}: {pct('above_sma50_pct', False)} των μετοχών πάνω "
                     f"από τον μέσο όρο 50 ημερών, {count('new_highs')} νέα υψηλά και {count('new_lows')} νέα χαμηλά "
                     "έτους.")
    else:
        parts.append("Η συμμετοχή είναι άγνωστη (λίγες εταιρείες με τιμή).")
    appetite = state.get("appetite")
    parts.append((f"Η διάθεση για ρίσκο είναι {GREEK[appetite]}" if appetite else "Η διάθεση για ρίσκο είναι άγνωστη")
                 + f": μικρές εταιρείες {pct('iwm_vs_spy_20d_pts')} και τεχνολογία {pct('qqq_vs_spy_20d_pts')} "
                   f"έναντι του SPY σε 20 μέρες.")
    rewards = state.get("rewards")
    if rewards:
        parts.append(f"Την τελευταία εβδομάδα ανέβηκαν {pct('up_5d_pct', False)} των μετοχών: η αγορά ανταμείβει "
                     f"{GREEK[rewards]}.")
    if agents:
        longs, shorts = agents.get("long", 0), agents.get("short", 0)  # counted proposals: none counted is 0
        lean = "long" if longs > 1.5 * shorts else "short" if shorts > 1.5 * longs else None
        line = f"Οι agents προτείνουν {longs} long και {shorts} short"
        if lean and rewards in ("longs", "shorts") and lean != rewards[:-1]:
            line += " - αντίθετα από αυτό που ανταμείβει η αγορά"
        parts.append(line + ".")
    if yesterday and yesterday.get("rewards") and f.get("up_1d_pct") is not None:
        actual = ("longs" if f["up_1d_pct"] >= REWARD_LONGS_FROM else "shorts" if f["up_1d_pct"] <= REWARD_SHORTS_BELOW
                  else "both")
        right = actual == yesterday["rewards"] or "both" in (actual, yesterday["rewards"])
        parts.append(f"Χθες διάβασα ότι η αγορά ανταμείβει {GREEK[yesterday['rewards']]}· σήμερα ανέβηκαν "
                     f"{f['up_1d_pct']:.0f}% των μετοχών - {'σωστή ανάγνωση' if right else 'λάθος ανάγνωση, για εξήγηση'}.")
    return " ".join(parts)


def strategy_view(state: dict[str, Any], evidence: dict[str, Any], horizon: int = 20) -> dict[str, list[str]]:
    """Strategies the market supports / stands against today, at one horizon (for the report)."""
    out: dict[str, list[str]] = {"supported": [], "against": []}
    keys = {(c["strategy"], c["side"]) for c in evidence.get("cells") or []}
    for strategy, side in sorted(keys):
        verdict = fit(state, evidence, strategy, side, horizon)["fit"]
        if verdict in out:
            out[verdict].append(f"{strategy} {side}")
    return out


def report_lines(payload: dict[str, Any] | None) -> list[str]:
    if not payload:
        return []
    lines = ["## Η κατάσταση της αγοράς", "", payload.get("paragraph") or "Άγνωστη.", ""]
    view = payload.get("strategies_today") or {}
    if view.get("supported") or view.get("against"):
        lines += [f"- Η αγορά στηρίζει (20 συνεδρίες): {', '.join(view.get('supported') or []) or 'καμία στρατηγική'}.",
                  f"- Η αγορά είναι κατά: {', '.join(view.get('against') or []) or 'καμίας στρατηγικής'} "
                  "(ο Koulis δεν τις στέλνει όταν έρθουν).", ""]
    lines += ["- Όλες οι συνεδρίες μας με την κατάσταση της αγοράς: output/market_state_review_latest.md", ""]
    return lines


def agents_balance(paths: list[Path]) -> dict[str, int]:
    out: dict[str, int] = defaultdict(int)
    for path in paths:
        try:
            rows = json.loads(path.read_text(encoding="utf-8")).get("results") or []
        except (OSError, ValueError):
            continue
        for row in rows:
            if isinstance(row, dict) and str(row.get("signal_verdict") or "").lower() == "candidate":
                out[str(row.get("trade_direction") or "").lower()] += 1
    return dict(out)


# ---------------------------------------------------------------- continuity

def changes(history: pd.DataFrame) -> dict[str, Any]:
    """Per side of the market: today's value, yesterday's, and for how many sessions it has held (owner 2026-11-04:
    the market's state is followed without a break, so a change is seen the day it comes)."""
    out: dict[str, Any] = {}
    if len(history) < 2:
        return out
    for dim in DIMENSIONS:
        values = [v if isinstance(v, str) else None for v in history[dim].tolist()]
        today, before = values[-1], values[-2]
        held = 0
        for value in reversed(values):
            if value != today:
                break
            held += 1
        out[dim] = {"today": today, "yesterday": before, "changed": today != before, "sessions": held}
    return out


def fit_changes(today: dict[str, list[str]], yesterday: dict[str, list[str]]) -> dict[str, list[str]]:
    """What Koulis does differently from yesterday: strategies newly against (kept out) or no longer against."""
    now, before = set(today.get("against") or []), set(yesterday.get("against") or [])
    return {"now_against": sorted(now - before), "no_longer_against": sorted(before - now),
            "now_supported": sorted(set(today.get("supported") or []) - set(yesterday.get("supported") or []))}


def change_sentence(moves: dict[str, Any], koulis: dict[str, list[str]]) -> str:
    changed = [f"{DIMENSION_GR[d]}: από {GREEK.get(m['yesterday'], 'άγνωστη')} σε {GREEK.get(m['today'], 'άγνωστη')}"
               for d, m in moves.items() if m["changed"]]
    steady = [f"{DIMENSION_GR[d]} {GREEK.get(m['today'], 'άγνωστη')} εδώ και {m['sessions']} συνεδρίες"
              for d, m in moves.items() if not m["changed"] and m["today"]]
    text = ("Αλλαγές από χθες: " + "· ".join(changed) + "." if changed else "Καμία αλλαγή από χθες.")
    if steady:
        text += " Σταθερά: " + "· ".join(steady) + "."
    if koulis.get("now_against"):
        text += " Από σήμερα ο Koulis κρατά έξω: " + ", ".join(koulis["now_against"]) + "."
    if koulis.get("no_longer_against"):
        text += " Ξαναπερνούν: " + ", ".join(koulis["no_longer_against"]) + "."
    return text


# ---------------------------------------------------------------- review of our sessions

CLASS_SESSIONS = {"day": 1, "week": 5, "month": 20, "quarter": 60}


def review(history: pd.DataFrame, evidence: dict[str, Any], records: list[dict[str, Any]],
           counterfactuals: dict[str, float]) -> dict[str, Any]:
    """Every paper session so far read with the market's state of the night before (owner 2026-11-04: "it should
    already have an idea of what is happening, since we have the data"): what the reading said, what longs and
    shorts made that session, and how the proposals the filter would have judged against / supported did.
    Directional results: a trade's own result, otherwise the session's open-to-close move in the trade's direction."""
    sessions = sorted({r["session_date"] for r in records})
    by_fit: dict[str, list[float]] = defaultdict(list)
    rows = []
    for day in sessions:
        before = history.index[history.index < pd.Timestamp(day)]
        if not len(before):
            continue
        state = snapshot(history, before[-1])
        side_results: dict[str, list[float]] = defaultdict(list)
        for r in (x for x in records if x["session_date"] == day):
            sign = -1.0 if r.get("direction") == "short" else 1.0
            result = r.get("result_pct") if r.get("kind") == "trade" else None
            if result is None and r.get("record_id") in counterfactuals:
                result = sign * counterfactuals[r["record_id"]]
            if result is None:
                continue
            side_results[str(r.get("direction"))].append(result)
            verdict = fit(state, evidence, str(r.get("playbook")), str(r.get("direction")),
                          CLASS_SESSIONS.get(str(r.get("holding_class")), 1))["fit"]
            by_fit[verdict].append(result)

        def mean(values: list[float]) -> float | None:
            return round(sum(values) / len(values), 2) if values else None

        rows.append({"session": day, "reading_of": state["session"], **{d: state.get(d) for d in DIMENSIONS},
                     "longs": len(side_results["long"]), "longs_mean_pct": mean(side_results["long"]),
                     "shorts": len(side_results["short"]), "shorts_mean_pct": mean(side_results["short"])})
    summary = {k: {"n": len(v), "mean_pct": round(sum(v) / len(v), 2) if v else None,
                   "positive_share": round(sum(1 for x in v if x > 0) / len(v), 3) if v else None}
               for k, v in sorted(by_fit.items())}
    return {"sessions": rows, "by_fit": summary}


def review_markdown(result: dict[str, Any], timeline: list[dict[str, Any]]) -> str:
    def v(x: Any) -> str:
        return "άγνωστο" if x is None else str(x)

    lines = ["# Η κατάσταση της αγοράς στις συνεδρίες μας", "",
             "Κάθε συνεδρία με την ανάγνωση της αγοράς της προηγούμενης νύχτας, και τι έβγαλαν τα long και τα short "
             "(αποτέλεσμα θέσης, ή η κίνηση ανοίγματος-κλεισίματος για τις προτάσεις που δεν έγιναν θέσεις).", "",
             "| συνεδρία | τάση | διάθεση | συμμετοχή | ρίσκο | ανταμείβει | long (μέσο %) | short (μέσο %) |",
             "|---|---|---|---|---|---|---|---|"]
    for r in result["sessions"]:
        lines.append(f"| {r['session']} | {GREEK.get(r['trend'], 'άγνωστο')} | {GREEK.get(r['mood'], 'άγνωστο')} | "
                     f"{GREEK.get(r['participation'], 'άγνωστο')} | {GREEK.get(r['appetite'], 'άγνωστο')} | "
                     f"{GREEK.get(r['rewards'], 'άγνωστο')} | {r['longs']} ({v(r['longs_mean_pct'])}) | "
                     f"{r['shorts']} ({v(r['shorts_mean_pct'])}) |")
    names = {"against": "κατά (ο Koulis θα τις κρατούσε έξω)", "supported": "υπέρ (θα έμπαιναν πρώτες)",
             "neutral": "ουδέτερες", "unknown": "άγνωστο"}
    lines += ["", "## Αν το φίλτρο υπήρχε από την αρχή", "", "| ταίριασμα με την αγορά | προτάσεις | μέσο % | θετικές |",
              "|---|---|---|---|"]
    for key, s in result["by_fit"].items():
        lines.append(f"| {names.get(key, key)} | {s['n']} | {v(s['mean_pct'])} | {v(s['positive_share'])} |")
    lines += ["", "## Οι αλλαγές της αγοράς (τελευταίες 30 συνεδρίες)", ""]
    previous = None
    for row in timeline[-30:]:
        label = " · ".join(GREEK.get(row.get(d), "άγνωστο") for d in DIMENSIONS)
        if label != previous:
            lines.append(f"- {row['session']}: {label}")
        previous = label
    return "\n".join(lines) + "\n"


def timeline(history: pd.DataFrame) -> list[dict[str, Any]]:
    return [{"session": pd.Timestamp(day).date().isoformat(),
             **{d: (history.at[day, d] if isinstance(history.at[day, d], str) else None) for d in DIMENSIONS}}
            for day in history.index]


def ledger_data(db: Path | None = None) -> tuple[list[dict[str, Any]], dict[str, float]]:
    import sqlite3
    from contextlib import closing

    db = db or ROOT / "data" / "paper_learning.db"
    if not db.exists():
        return [], {}
    with closing(sqlite3.connect(db)) as conn:
        records = [json.loads(r[0]) for r in conn.execute("SELECT record_json FROM paper_trade_ledger")]
        try:
            moves = dict(conn.execute("SELECT record_id, open_close_pct FROM paper_counterfactuals "
                                      "WHERE open_close_pct IS NOT NULL"))
        except sqlite3.OperationalError:
            moves = {}
    return records, moves


def _signals_stamp(lab: Path | None = None) -> str:
    stamps = []
    for name in SIGNALS:
        path = (lab or LAB) / name
        if path.exists():
            stat = path.stat()
            stamps.append(f"{name}:{stat.st_size}:{int(stat.st_mtime)}")
    return "|".join(stamps)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--rebuild", action="store_true", help="rebuild the evidence even when the lab did not change")
    args = parser.parse_args(argv)
    history = read_history(load_closes())
    state = snapshot(history)
    stamp = _signals_stamp()
    evidence = load(EVIDENCE)
    if args.rebuild or not evidence or evidence.get("signals_stamp") != stamp:
        evidence = {"schema": "ecosystem.market_state_evidence.v1", "generated_at": datetime.now(timezone.utc).isoformat(),
                    "signals_stamp": stamp, "fdr_q": FDR_Q, **build_evidence(read_signals(), history)}
        EVIDENCE.parent.mkdir(parents=True, exist_ok=True)
        EVIDENCE.write_text(json.dumps(evidence, indent=1), encoding="utf-8")
    yesterday = snapshot(history, history.index[-2]) if len(history) > 1 else None
    agents = agents_balance([ROOT / "output" / f"{a}_strategy_latest.json" for a in ("yankos", "periklis")])
    view = strategy_view(state, evidence)
    moves = changes(history)
    koulis = fit_changes(view, strategy_view(yesterday, evidence) if yesterday else {})
    payload = {"schema": SCHEMA, "generated_at": datetime.now(timezone.utc).isoformat(), **state,
               "paragraph": paragraph(state, yesterday=yesterday, agents=agents) + " " + change_sentence(moves, koulis),
               "changes": moves, "koulis_changes": koulis,
               "agents_candidates": agents, "strategies_today": view,
               "evidence": {"file": str(EVIDENCE.relative_to(ROOT)) if EVIDENCE.is_relative_to(ROOT) else str(EVIDENCE),
                            "cells": len(evidence.get("cells") or []),
                            "judged": sum(1 for c in evidence.get("cells") or [] if c["verdict"] != "unclear")}}
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    with HISTORY.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({k: payload.get(k) for k in ("session", *DIMENSIONS, "figures")}, ensure_ascii=False) + "\n")
    # every paper session so far read with the market's state (owner 2026-11-04), refreshed every night
    records, moves_by_record = ledger_data()
    result = review(history, evidence, records, moves_by_record)
    line = timeline(history)
    Path(f"{REVIEW}.json").write_text(json.dumps({"generated_at": payload["generated_at"], **result,
                                                   "timeline": line[-260:]}, ensure_ascii=False, indent=1),
                                      encoding="utf-8")
    Path(f"{REVIEW}.md").write_text(review_markdown(result, line), encoding="utf-8")
    print("[MARKET-STATE] " + json.dumps({k: payload.get(k) for k in ("session", *DIMENSIONS)})
          + f" evidence cells {payload['evidence']['cells']}, judged {payload['evidence']['judged']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
