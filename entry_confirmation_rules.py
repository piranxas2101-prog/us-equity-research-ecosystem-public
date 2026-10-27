"""Miltos's 15-minute entry confirmation (owner 2026-10-24, after the AMSF short of 10/23).

AMSF was shorted at 12:32 New York on a "red candle below the previous low" seen on the IEX feed alone: 413 shares,
2 cents under an IEX low that the consolidated tape (SIP) did not have - on SIP the low was not broken at all - and
the order filled inside a green 15-minute candle with the hour's largest volume while RSI(14) on 15 minutes sat at
51. The confirmation now asks, on completed bars of the consolidated tape:

1. **trigger** - the latest completed 15-minute bar closes beyond the previous bar's low (short) / high (long), in a
   candle of the trade's colour (as before, now on SIP);
2. **participation** - its volume is at least ``VOLUME_MIN`` x the average of the ``VOLUME_BARS`` regular-session
   bars before it (``PARTICIPATION_BASIS = "last_bars"``). A break on thin volume is not a break. T3 (2026-10-24)
   asked whether the same 15-minute slot of the last ``PROFILE_SESSIONS`` sessions (relative volume by time of day)
   judges better, since volume is U-shaped through the day. A replay of 361 records of the 7 paper sessions
   (trades, approvals not entered, Koulis leftovers, 250 shadow candidates; exit at the 15:45 close) did not show
   it: last 20 bars >= 1.0x entered 87, mean +0.30%, 60% won; same slot >= 1.0x entered 114, +0.08%, 50% won; no
   volume check 192, +0.06%, 48% - every 95% day-bootstrap interval spans zero and the others. So the basis stays
   "last_bars" and both ratios are recorded on every confirmation (``volume_x_last_bars``, ``volume_x_same_slot``)
   until enough sessions decide; switching is the owner's call (``"same_slot"``);
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
PARTICIPATION_BASIS = "last_bars"  # or "same_slot" (owner's decision; see 2. above)
PROFILE_SESSIONS = 20
PROFILE_MIN_SESSIONS = 10
REGULAR_OPEN, REGULAR_CLOSE = "09:30", "16:00"
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


def regular_hours(bars: list[dict[str, Any]], *, parse, tz) -> list[dict[str, Any]]:
    """Only the bars of the regular session (09:30-16:00 New York): pre- and after-market bars are not candles of
    the session the trigger is judged in."""
    out = []
    for bar in bars:
        start = parse(bar.get("t"))
        if start is not None and REGULAR_OPEN <= start.astimezone(tz).strftime("%H:%M") < REGULAR_CLOSE:
            out.append(bar)
    return out


def slot_profile(bars: list[dict[str, Any]], *, before: str, parse, tz,
                 sessions: int = PROFILE_SESSIONS) -> dict[str, list[float]]:
    """Slot ("HH:MM" New York) -> the volumes of that 15-minute slot over the last ``sessions`` sessions before the
    date ``before`` (regular hours only)."""
    by_day: dict[str, dict[str, float]] = {}
    for bar in regular_hours(bars, parse=parse, tz=tz):
        local = parse(bar.get("t")).astimezone(tz)
        day = local.date().isoformat()
        volume = _f(bar.get("v"))
        if day < before and volume is not None:
            by_day.setdefault(day, {})[local.strftime("%H:%M")] = volume
    profile: dict[str, list[float]] = {}
    for day in sorted(by_day)[-sessions:]:
        for slot, volume in by_day[day].items():
            profile.setdefault(slot, []).append(volume)
    return profile


def evaluate(direction: str, bars: list[dict[str, Any]], minute_bars: list[dict[str, Any]] | None,
             *, session_bars: list[dict[str, Any]], parse, volume_profile: dict[str, list[float]] | None = None,
             tz=None, basis: str | None = None) -> dict[str, Any]:
    """The confirmation of one entry. ``bars``: completed 15-minute SIP bars (several sessions, oldest first);
    ``session_bars``: those of today's session; ``minute_bars``: today's real-time IEX minute bars."""
    short = str(direction).lower() == "short"
    basis = basis or PARTICIPATION_BASIS
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
    volume = _f(latest.get("v"))
    before = bars[max(0, index - VOLUME_BARS):index] if index is not None else []
    known = [v for v in (_f(b.get("v")) for b in before) if v is not None]
    last_average = sum(known) / len(known) if len(known) == VOLUME_BARS else None
    checks["volume_x_last_bars"] = (None if last_average in (None, 0) or volume is None
                                    else round(volume / last_average, 2))
    if volume_profile is not None:  # measured on every confirmation, whichever basis decides (T3)
        slot = parse(latest.get("t")).astimezone(tz).strftime("%H:%M")
        history = volume_profile.get(slot) or []
        slot_average = sum(history) / len(history) if len(history) >= PROFILE_MIN_SESSIONS else None
        checks["volume_x_same_slot"] = (None if slot_average in (None, 0) or volume is None
                                        else round(volume / slot_average, 2))
        checks["same_slot_sessions"] = len(history)
    use_slot = basis == "same_slot" and volume_profile is not None
    checks["volume_basis"] = "same 15-minute slot of previous sessions" if use_slot else f"last {VOLUME_BARS} bars"
    checks["volume_x_average"] = checks.get("volume_x_same_slot") if use_slot else checks["volume_x_last_bars"]
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


# T6 (owner 2026-10-27): the SIP bars above arrive 16-31 minutes late on the free plan (48 Miltos entries of 10/26:
# median 22 minutes after the trigger candle closed, the fill on average 0.45% worse than that close, worse in 43).
# The free plan's real-time feed is IEX. As a shadow only - recorded on every confirmation, never deciding - the
# trigger is also judged on 15-minute candles built from today's real-time IEX minute bars, so a few weeks of
# sessions can show how much earlier, at which price and how often falsely it would fire. Participation and RSI
# stay on SIP: IEX volume is a small slice of the tape (the AMSF lesson). A candle with fewer than
# ``IEX_MIN_MINUTES`` traded minutes is too thin to judge and stays unknown.
IEX_MIN_MINUTES = 5


def iex_candles(minute_bars: list[dict[str, Any]] | None, *, now: datetime, parse, tz,
                minutes: int = 15) -> list[dict[str, Any]]:
    """Completed ``minutes`` candles of the regular session built from real-time IEX minute bars, oldest first."""
    groups: dict[datetime, list[tuple[datetime, dict[str, Any]]]] = {}
    for bar in regular_hours(minute_bars or [], parse=parse, tz=tz):
        stamp = parse(bar.get("t"))
        start = stamp.replace(minute=stamp.minute - stamp.minute % minutes, second=0, microsecond=0)
        if start + timedelta(minutes=minutes) <= now:
            groups.setdefault(start, []).append((stamp, bar))
    out = []
    for start in sorted(groups):
        bars = [bar for _, bar in sorted(groups[start], key=lambda item: item[0])]
        o, c = _f(bars[0].get("o")), _f(bars[-1].get("c"))
        highs = [h for h in (_f(b.get("h")) for b in bars) if h is not None]
        lows = [low for low in (_f(b.get("l")) for b in bars) if low is not None]
        if o is None or c is None or not highs or not lows:
            continue
        out.append({"t": start, "o": o, "h": max(highs), "l": min(lows), "c": c, "minutes": len(bars)})
    return out


def iex_trigger(direction: str, minute_bars: list[dict[str, Any]] | None, *, now: datetime, parse,
                tz) -> dict[str, Any]:
    """The shadow trigger on real-time IEX candles: the same rule as ``trigger`` above (close beyond the previous
    candle's low/high, in a candle of the trade's colour). Recorded only."""
    short = str(direction).lower() == "short"
    candles = iex_candles(minute_bars, now=now, parse=parse, tz=tz)
    if len(candles) < 2:
        return {"iex_trigger": None, "iex_reason": "fewer than two completed real-time IEX candles"}
    previous, latest = candles[-2], candles[-1]
    edge = previous["l"] if short else previous["h"]
    out = {"iex_bar_t": latest["t"].isoformat(), "iex_close": latest["c"], "iex_edge": edge,
           "iex_minutes": latest["minutes"]}
    if min(previous["minutes"], latest["minutes"]) < IEX_MIN_MINUTES:
        return {**out, "iex_trigger": None, "iex_reason": f"fewer than {IEX_MIN_MINUTES} traded IEX minutes"}
    c, o = latest["c"], latest["o"]
    return {**out, "iex_trigger": (c < edge and c < o) if short else (c > edge and c > o)}
