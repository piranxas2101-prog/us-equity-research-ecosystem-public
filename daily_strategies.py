"""Daily-bar trading strategies as explicit rules, shared by the backtest lab and the nightly traders.

Owner's decision 2026-10-12: every strategy in the library becomes available to the traders, so they have variety.
Each strategy here is a set of rules on daily OHLCV bars (no intraday data, no fundamentals: the traders' fundamental
gate stays where it is). The same function scores one symbol's whole history (for the backtest) or its last bar (for
the nightly run), so what is tested is exactly what runs.

A strategy returns, per session, a long score and a short score from 0 to 10 and the reasons that built them.
The traders' usual thresholds turn scores into verdicts (candidate >= 7.25 long / 7.35 short, watch >= 5.5 / 5.7).
A rule whose inputs are missing (too little history) scores 0 for that session: no signal, never a guess.

Sources of the rule sets: the strategy library (strategy_library/*.md) and the literature they come from
(Jegadeesh & Titman 1993 momentum; George & Hwang 2004 52-week high; Connors & Alvarez 2009 RSI(2);
Bollinger 2001 band squeeze; Bulkowski 2005 failed breakouts and gaps).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

import numpy as np
import pandas as pd

MIN_HISTORY = 60


@dataclass(frozen=True)
class Strategy:
    name: str
    trader: str            # which trader runs it: "yankos" (short-horizon) or "periklis" (swing)
    horizon_days: int      # the holding horizon the backtest judges it on
    sides: tuple[str, ...]
    description: str
    library_doc: str
    min_history: int
    score: Callable[[pd.DataFrame], pd.DataFrame]


def features(history: pd.DataFrame) -> pd.DataFrame:
    """Indicators every strategy reads, computed once per history (columns: Open, High, Low, Close, Volume)."""
    h = history.sort_index()
    close, high, low, open_, volume = (h["Close"].astype(float), h["High"].astype(float), h["Low"].astype(float),
                                       h["Open"].astype(float), h["Volume"].astype(float))
    f = pd.DataFrame(index=h.index)
    f["close"], f["open"], f["high"], f["low"], f["volume"] = close, open_, high, low, volume
    f["prev_close"] = close.shift(1)
    f["sma5"] = close.rolling(5).mean()
    f["sma20"] = close.rolling(20).mean()
    f["sma50"] = close.rolling(50).mean()
    f["sma200"] = close.rolling(200).mean()
    f["vol_ratio20"] = volume / volume.rolling(20).mean().shift(1)
    delta = close.diff()
    for n in (2, 14):
        gain = delta.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
        loss = (-delta.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
        f[f"rsi{n}"] = 100 - 100 / (1 + gain / loss.replace(0, np.nan))
    true_range = pd.concat([high - low, (high - f["prev_close"]).abs(), (low - f["prev_close"]).abs()], axis=1).max(axis=1)
    f["atr14_pct"] = true_range.rolling(14).mean() / close * 100
    std20 = close.rolling(20).std()
    f["bb_upper"], f["bb_lower"] = f["sma20"] + 2 * std20, f["sma20"] - 2 * std20
    f["bb_upper25"], f["bb_lower25"] = f["sma20"] + 2.5 * std20, f["sma20"] - 2.5 * std20
    f["bb_width"] = (4 * std20) / f["sma20"] * 100
    f["bb_width_pct_rank"] = f["bb_width"].rolling(120).rank(pct=True)
    f["prior_high20"] = high.rolling(20).max().shift(1)
    f["prior_low20"] = low.rolling(20).min().shift(1)
    f["high252"] = high.rolling(252).max()
    f["ret_12_1"] = close.shift(21) / close.shift(252) - 1
    f["gap_pct"] = (open_ / f["prev_close"] - 1) * 100
    span = (high - low).replace(0, np.nan)
    f["close_in_range"] = (close - low) / span  # 0 = closed at the low, 1 = at the high
    return f


def _frame(f: pd.DataFrame) -> pd.DataFrame:
    return pd.DataFrame({"long": 0.0, "short": 0.0}, index=f.index)


def _clip(series: pd.Series) -> pd.Series:
    return series.clip(lower=0.0, upper=10.0).fillna(0.0)


def momentum_12_1(f: pd.DataFrame) -> pd.DataFrame:
    """12-month momentum skipping the last month, with the 200-day trend agreeing."""
    out = _frame(f)
    mom = f["ret_12_1"]
    up = (mom > 0.20) & (f["close"] > f["sma200"]) & (f["sma50"] > f["sma200"])
    down = (mom < -0.20) & (f["close"] < f["sma200"]) & (f["sma50"] < f["sma200"])
    out["long"] = np.where(up, 7.5 + (mom.clip(upper=1.0) - 0.20) * 3, np.where(mom > 0.10, 5.6, 0.0))
    out["short"] = np.where(down, 7.6 + (-mom.clip(lower=-0.8) - 0.20) * 3, np.where(mom < -0.10, 5.8, 0.0))
    return out.apply(_clip)


def high_52w(f: pd.DataFrame) -> pd.DataFrame:
    """Close within 2% of the 52-week high on at least average volume: anchoring makes such names drift higher."""
    out = _frame(f)
    near = f["close"] / f["high252"]
    trigger = (near >= 0.98) & (f["vol_ratio20"] >= 1.0) & (f["close"] > f["sma50"])
    out["long"] = np.where(trigger, 7.5 + (f["vol_ratio20"].clip(upper=3) - 1) * 0.8, np.where(near >= 0.95, 5.6, 0.0))
    return out.apply(_clip)


def rsi2_mean_reversion(f: pd.DataFrame) -> pd.DataFrame:
    """Short-term pullback inside a long-term trend: RSI(2) extreme against the 200-day direction."""
    out = _frame(f)
    long_ok = (f["close"] > f["sma200"]) & (f["rsi2"] < 10)
    short_ok = (f["close"] < f["sma200"]) & (f["rsi2"] > 90)
    out["long"] = np.where(long_ok, 7.5 + (10 - f["rsi2"]) * 0.2, np.where((f["close"] > f["sma200"]) & (f["rsi2"] < 25), 5.6, 0.0))
    out["short"] = np.where(short_ok, 7.6 + (f["rsi2"] - 90) * 0.2, np.where((f["close"] < f["sma200"]) & (f["rsi2"] > 75), 5.8, 0.0))
    return out.apply(_clip)


def failed_breakdown_reclaim(f: pd.DataFrame) -> pd.DataFrame:
    """Trades below the 20-day low, then closes back above it near the day's high: trapped sellers."""
    out = _frame(f)
    reclaim = (f["low"] < f["prior_low20"]) & (f["close"] > f["prior_low20"]) & (f["close_in_range"] >= 0.6)
    out["long"] = np.where(reclaim, 7.5 + f["vol_ratio20"].clip(upper=3) * 0.5 - 0.5, 0.0)
    return out.apply(_clip)


def failed_breakout_reversal(f: pd.DataFrame) -> pd.DataFrame:
    """Trades above the 20-day high, then closes back below it near the day's low: trapped buyers."""
    out = _frame(f)
    failed = (f["high"] > f["prior_high20"]) & (f["close"] < f["prior_high20"]) & (f["close_in_range"] <= 0.4)
    out["short"] = np.where(failed, 7.6 + f["vol_ratio20"].clip(upper=3) * 0.5 - 0.5, 0.0)
    return out.apply(_clip)


def volatility_compression_breakout(f: pd.DataFrame) -> pd.DataFrame:
    """Bollinger squeeze (band width in its lowest fifth of 120 days) resolved by a close beyond the 20-day range."""
    out = _frame(f)
    squeeze = f["bb_width_pct_rank"].shift(1) <= 0.2
    up = squeeze & (f["close"] > f["prior_high20"]) & (f["vol_ratio20"] >= 1.3)
    down = squeeze & (f["close"] < f["prior_low20"]) & (f["vol_ratio20"] >= 1.3)
    out["long"] = np.where(up, 7.6 + (f["vol_ratio20"].clip(upper=3) - 1.3), np.where(squeeze, 5.5, 0.0))
    out["short"] = np.where(down, 7.7 + (f["vol_ratio20"].clip(upper=3) - 1.3), 0.0)
    return out.apply(_clip)


def mean_reversion_overextension(f: pd.DataFrame) -> pd.DataFrame:
    """Fade a stretch beyond the 2.5-sigma band with an extreme RSI(14)."""
    out = _frame(f)
    over = (f["close"] > f["bb_upper25"]) & (f["rsi14"] >= 75)
    under = (f["close"] < f["bb_lower25"]) & (f["rsi14"] <= 25)
    out["short"] = np.where(over, 7.6 + (f["rsi14"] - 75) * 0.1, np.where(f["rsi14"] >= 70, 5.8, 0.0))
    out["long"] = np.where(under, 7.5 + (25 - f["rsi14"]) * 0.1, np.where(f["rsi14"] <= 30, 5.6, 0.0))
    return out.apply(_clip)


def gap_continuation(f: pd.DataFrame) -> pd.DataFrame:
    """A large gap that held into the close on heavy volume (daily form of gap-and-go)."""
    out = _frame(f)
    up = (f["gap_pct"] >= 4) & (f["close"] >= f["open"]) & (f["vol_ratio20"] >= 2)
    down = (f["gap_pct"] <= -4) & (f["close"] <= f["open"]) & (f["vol_ratio20"] >= 2)
    out["long"] = np.where(up, 7.5 + f["gap_pct"].clip(upper=15) * 0.1, 0.0)
    out["short"] = np.where(down, 7.6 + (-f["gap_pct"]).clip(upper=15) * 0.1, 0.0)
    return out.apply(_clip)


def gap_fade(f: pd.DataFrame) -> pd.DataFrame:
    """A large gap that failed during the session: bet on the fill continuing."""
    out = _frame(f)
    faded_up = (f["gap_pct"] >= 4) & (f["close"] < f["open"]) & (f["close_in_range"] <= 0.35)
    faded_down = (f["gap_pct"] <= -4) & (f["close"] > f["open"]) & (f["close_in_range"] >= 0.65)
    out["short"] = np.where(faded_up, 7.6 + f["gap_pct"].clip(upper=15) * 0.1, 0.0)
    out["long"] = np.where(faded_down, 7.5 + (-f["gap_pct"]).clip(upper=15) * 0.1, 0.0)
    return out.apply(_clip)


STRATEGIES: dict[str, Strategy] = {s.name: s for s in (
    Strategy("momentum_12_1", "periklis", 20, ("long", "short"), "12-1 month momentum with the 200-day trend",
             "literature: Jegadeesh & Titman (1993)", 260, momentum_12_1),
    Strategy("high_52w_breakout", "periklis", 20, ("long",), "close near the 52-week high on volume",
             "literature: George & Hwang (2004)", 260, high_52w),
    Strategy("rsi2_mean_reversion", "yankos", 5, ("long", "short"), "RSI(2) extreme against the 200-day trend",
             "literature: Connors & Alvarez (2009)", 210, rsi2_mean_reversion),
    Strategy("failed_breakdown_reclaim", "yankos", 5, ("long",), "reclaim of a broken 20-day low",
             "strategy_library/failed_breakdown_reclaim.md", MIN_HISTORY, failed_breakdown_reclaim),
    Strategy("failed_breakout_reversal", "yankos", 5, ("short",), "failure of a 20-day high breakout",
             "strategy_library/breakout_retest_failed_breakout.md", MIN_HISTORY, failed_breakout_reversal),
    Strategy("volatility_compression_breakout", "periklis", 10, ("long", "short"),
             "Bollinger squeeze resolved beyond the 20-day range",
             "strategy_library/sokratis_volatility_compression_breakout.md", 150, volatility_compression_breakout),
    Strategy("mean_reversion_overextension", "yankos", 5, ("long", "short"), "fade of a 2.5-sigma stretch",
             "strategy_library/mean_reversion_fade_overextended.md", MIN_HISTORY, mean_reversion_overextension),
    Strategy("gap_continuation", "yankos", 3, ("long", "short"), "large gap held into the close on volume",
             "strategy_library/gap_go_gap_fade_decision_tree.md", MIN_HISTORY, gap_continuation),
    Strategy("gap_fade", "yankos", 3, ("long", "short"), "large gap that failed during the session",
             "strategy_library/gap_go_gap_fade_decision_tree.md", MIN_HISTORY, gap_fade),
)}

# Library strategies that need intraday bars (VWAP, opening range, 15-minute candles). They cannot be judged on
# daily bars; they stay with the entry-confirmation step of the paper path until 15-minute history is stored.
INTRADAY_ONLY = ("vwap_reclaim_rejection", "pullback_to_vwap_continuation", "opening_range_breakout_breakdown",
                 "sokratis_volatility_adjusted_vwap_reclaim")


def score_history(name: str, history: pd.DataFrame) -> pd.DataFrame:
    """Long and short scores for every session of one symbol's history."""
    strategy = STRATEGIES[name]
    f = features(history)
    scores = strategy.score(f)
    scores.loc[f.index[: max(0, strategy.min_history - 1)], ["long", "short"]] = 0.0
    if "short" not in strategy.sides:
        scores["short"] = 0.0
    if "long" not in strategy.sides:
        scores["long"] = 0.0
    return scores


def score_latest(name: str, history: pd.DataFrame) -> dict[str, object] | None:
    """The scores of the last session, or None when the history is too short to judge."""
    strategy = STRATEGIES[name]
    if history is None or len(history) < strategy.min_history:
        return None
    scores = score_history(name, history.iloc[-(strategy.min_history + 300):])
    last = scores.iloc[-1]
    return {"strategy": name, "session": str(scores.index[-1].date()), "long_score": round(float(last["long"]), 2),
            "short_score": round(float(last["short"]), 2)}
