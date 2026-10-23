"""Miltos's 15-minute entry confirmation (owner 2026-10-24, after the AMSF short of 10/23).

AMSF was shorted at 12:32 New York on a "red candle below the previous low" seen on the IEX feed alone: 413 shares,
2 cents under an IEX low that the consolidated tape (SIP) did not have - on SIP the low was not broken at all - and
the order filled inside a green 15-minute candle with the hour's largest volume while RSI(14) on 15 minutes sat at
51. The confirmation now asks, on completed bars of the consolidated tape:

1. **trigger** - the latest completed 15-minute bar closes beyond the previous bar's low (short) / high (long), in a
   candle of the trade's colour (as before, now on SIP);
2. **participation** - its volume is at least ``VOLUME_MIN`` x the average of the ``VOLUME_BARS`` completed bars
   before it (a break on thin volume is not a break);
3. **momentum** - RSI(14) of the 15-minute closes is below ``RSI_SHORT_BELOW`` for a short, above
   ``RSI_LONG_ABOVE`` for a long (the move has the momentum of its direction, not a neutral 50);
4. **still holding now** - the latest real-time price (IEX minute bars, the only real-time feed of the free plan)
   has not reclaimed the trigger close against the trade, and the current minutes are not a surge against it: the
   real-time bars since the trigger bar are not a candle of the opposite colour on more than ``SURGE_MIN`` x the
   average minute volume (the AMSF fill went into exactly such a candle).

Unknown stays unknown: a missing bar, average or RSI is not a pass - the entry waits. SIP on the free plan is
available 15 minutes late, so the confirmation sees bars that ended at least 16 minutes ago; the real-time check
(4) covers the gap. Starting values with this rationale; they change only with the owner's approval and the
measurements of the ledger (owner's note 2026-10-24: the ecosystem's rules are reviewed - technical analysis first,
then the fundamental side; docs/RULES_REVIEW.md).
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

VOLUME_BARS = 20
VOLUME_MIN = 1.0
RSI_PERIOD = 14
RSI_SHORT_BELOW = 50.0
RSI_LONG_ABOVE = 50.0
SURGE_MIN = 1.5


def _f(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number else None


def rsi(closes: list[float], period: int = RSI_PERIOD) -> float | None:
    """Wilder's RSI of the last close; None with fewer than period + 1 closes."""
    if len(closes) < period + 1:
        return None
    gains = losses = 0.0
    for previous, current in zip(closes[:period], closes[1:period + 1]):
        change = current - previous
        gains += max(change, 0.0)
        losses += max(-change, 0.0)
    gain, loss = gains / period, losses / period
    for previous, current in zip(closes[period:-1], closes[period + 1:]):
        change = current - previous
        gain = (gain * (period - 1) + max(change, 0.0)) / period
        loss = (loss * (period - 1) + max(-change, 0.0)) / period
    if loss == 0:
        return 100.0 if gain > 0 else 50.0
    return 100.0 - 100.0 / (1.0 + gain / loss)


def completed(bars: list[dict[str, Any]], *, data_end: datetime, minutes: int, parse) -> list[dict[str, Any]]:
    """Only bars that had ended by ``data_end`` (a bar still forming is not a candle yet)."""
    out = []
    for bar in bars:
        start = parse(bar.get("t"))
        if start is not None and start + timedelta(minutes=minutes) <= data_end:
            out.append(bar)
    return out


def evaluate(direction: str, bars: list[dict[str, Any]], minute_bars: list[dict[str, Any]] | None,
             *, session_bars: list[dict[str, Any]], parse) -> dict[str, Any]:
    """The confirmation of one entry. ``bars``: completed 15-minute SIP bars (several sessions, oldest first);
    ``session_bars``: those of today's session; ``minute_bars``: today's real-time IEX minute bars."""
    short = str(direction).lower() == "short"
    checks: dict[str, Any] = {}
    if len(session_bars) < 2:
        return {"confirmed": False, "reason": "fewer than two completed 15-minute candles today", "checks": checks}
    previous, latest = session_bars[-2], session_bars[-1]
    o, c = _f(latest.get("o")), _f(latest.get("c"))
    edge = _f(previous.get("l") if short else previous.get("h"))
    if None in (o, c, edge):
        return {"confirmed": False, "reason": "the trigger candle is incomplete", "checks": checks}
    checks["trigger"] = (c < edge and c < o) if short else (c > edge and c > o)

    index = next((k for k, bar in enumerate(bars) if bar.get("t") == latest.get("t")), None)
    before = bars[max(0, index - VOLUME_BARS):index] if index is not None else []
    volumes = [_f(b.get("v")) for b in before]
    known = [v for v in volumes if v is not None]
    volume = _f(latest.get("v"))
    average = sum(known) / len(known) if len(known) == VOLUME_BARS else None
    checks["volume_x_average"] = None if average in (None, 0) or volume is None else round(volume / average, 2)
    checks["participation"] = None if checks["volume_x_average"] is None else checks["volume_x_average"] >= VOLUME_MIN

    closes = [_f(b.get("c")) for b in bars[:index + 1]] if index is not None else []
    value = rsi([x for x in closes if x is not None]) if closes and None not in closes else None
    checks["rsi14_15m"] = None if value is None else round(value, 1)
    checks["momentum"] = None if value is None else (value < RSI_SHORT_BELOW if short else value > RSI_LONG_ABOVE)

    end = parse(latest.get("t"))
    later = [m for m in minute_bars or [] if (p := parse(m.get("t"))) is not None and end is not None
             and p >= end + timedelta(minutes=15)]
    if not later:
        checks["holding_now"] = None
    else:
        last = _f(later[-1].get("c"))
        first_open = _f(later[0].get("o"))
        moved_against = None if last is None else (last > c if short else last < c)
        minute_volumes = [_f(m.get("v")) for m in minute_bars or []]
        minute_known = [v for v in minute_volumes if v is not None]
        recent = [_f(m.get("v")) for m in later]
        recent_known = [v for v in recent if v is not None]
        surge = None
        if minute_known and recent_known and first_open is not None and last is not None:
            mean_all = sum(minute_known) / len(minute_known)
            mean_recent = sum(recent_known) / len(recent_known)
            against_colour = last > first_open if short else last < first_open
            surge = bool(against_colour and mean_all > 0 and mean_recent >= SURGE_MIN * mean_all)
        checks["price_now"] = last
        checks["surge_against"] = surge
        checks["holding_now"] = None if moved_against is None or surge is None else not (moved_against or surge)

    order = ("trigger", "participation", "momentum", "holding_now")
    failed = [k for k in order if checks.get(k) is False]
    unknown = [k for k in order if checks.get(k) is None]
    confirmed = not failed and not unknown
    side = "short" if short else "long"
    if confirmed:
        reason = (f"{side} confirmed on completed SIP 15m: close {c:.4f} beyond previous "
                  f"{'low' if short else 'high'} {edge:.4f}, volume {checks['volume_x_average']}x average, "
                  f"RSI(14) {checks['rsi14_15m']}, price now {checks.get('price_now')} still holding.")
    else:
        reason = (f"{side} waiting: " + ", ".join([f"{k} failed" for k in failed] + [f"{k} unknown" for k in unknown])
                  + f" (close {c:.4f}, previous {'low' if short else 'high'} {edge:.4f}, volume "
                  f"{checks.get('volume_x_average')}x, RSI(14) {checks.get('rsi14_15m')}).")
    return {"confirmed": confirmed, "reason": reason, "checks": checks, "previous": previous, "latest": latest}
