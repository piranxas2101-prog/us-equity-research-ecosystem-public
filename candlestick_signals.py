from __future__ import annotations

from typing import Any

import pandas as pd
import talib


PATTERN_LOOKBACK_BARS = 3


def _nonempty_pattern_list(values: list[str]) -> list[str]:
    return [value for value in values if value]


def _inside_bar_signal(history: pd.DataFrame) -> str | None:
    if len(history) < 2:
        return None
    recent = history[["Open", "High", "Low", "Close"]].tail(2).copy()
    previous = recent.iloc[-2]
    latest = recent.iloc[-1]
    if latest["High"] < previous["High"] and latest["Low"] > previous["Low"]:
        return "inside_bar"
    return None


def analyze_candlestick_patterns(history: pd.DataFrame) -> dict[str, Any]:
    required = {"Open", "High", "Low", "Close"}
    if not required.issubset(history.columns):
        return {
            "candlestick_patterns": [],
            "candlestick_bias": "neutral",
            "candlestick_score": 0.0,
            "candlestick_last_signal": None,
            "candlestick_bullish_count": 0,
            "candlestick_bearish_count": 0,
        }

    frame = history[["Open", "High", "Low", "Close"]].copy().dropna()
    if len(frame) < 5:
        return {
            "candlestick_patterns": [],
            "candlestick_bias": "neutral",
            "candlestick_score": 0.0,
            "candlestick_last_signal": None,
            "candlestick_bullish_count": 0,
            "candlestick_bearish_count": 0,
        }

    open_ = frame["Open"].astype(float).to_numpy()
    high = frame["High"].astype(float).to_numpy()
    low = frame["Low"].astype(float).to_numpy()
    close = frame["Close"].astype(float).to_numpy()

    patterns: list[tuple[int, str, float]] = []

    def collect(values: Any, bullish_name: str, bearish_name: str | None = None) -> None:
        series = pd.Series(values).fillna(0)
        if series.empty:
            return
        tail = series.tail(PATTERN_LOOKBACK_BARS)
        for offset, raw in enumerate(tail.tolist(), start=1):
            if raw == 0:
                continue
            sign = 1 if raw > 0 else -1
            label = bullish_name if sign > 0 or not bearish_name else bearish_name
            weight = 1.0 if sign > 0 else -1.0
            patterns.append((offset, label, weight))

    def collect_neutral(values: Any, label: str, weight: float = 0.0) -> None:
        series = pd.Series(values).fillna(0)
        if series.empty:
            return
        tail = series.tail(PATTERN_LOOKBACK_BARS)
        for offset, raw in enumerate(tail.tolist(), start=1):
            if raw == 0:
                continue
            patterns.append((offset, label, weight))

    collect_neutral(talib.CDLDOJI(open_, high, low, close), "doji")
    collect(talib.CDLHAMMER(open_, high, low, close), "hammer")
    collect(talib.CDLSHOOTINGSTAR(open_, high, low, close), "shooting_star")
    collect(talib.CDLENGULFING(open_, high, low, close), "bullish_engulfing", "bearish_engulfing")
    collect(talib.CDLMORNINGSTAR(open_, high, low, close, penetration=0.3), "morning_star")
    collect(talib.CDLEVENINGSTAR(open_, high, low, close, penetration=0.3), "evening_star")

    inside_bar = _inside_bar_signal(frame)
    if inside_bar:
        patterns.append((PATTERN_LOOKBACK_BARS, inside_bar, 0.25))

    patterns.sort(key=lambda item: item[0], reverse=True)

    ordered_names: list[str] = []
    for _, label, _ in patterns:
        if label not in ordered_names:
            ordered_names.append(label)

    bullish_count = sum(1 for _, _, weight in patterns if weight > 0.3)
    bearish_count = sum(1 for _, _, weight in patterns if weight < -0.3)
    score = round(sum(weight for _, _, weight in patterns), 2)
    if score > 0.35:
        bias = "bullish"
    elif score < -0.35:
        bias = "bearish"
    else:
        bias = "neutral"

    return {
        "candlestick_patterns": _nonempty_pattern_list(ordered_names[:5]),
        "candlestick_bias": bias,
        "candlestick_score": score,
        "candlestick_last_signal": ordered_names[0] if ordered_names else None,
        "candlestick_bullish_count": bullish_count,
        "candlestick_bearish_count": bearish_count,
    }
