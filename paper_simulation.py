"""What would have happened had we opened this position: our own rules, replayed on the session's 15-minute bars.

Owner's decision 2026-10-16: the counterfactual of a candidate is not the stock's open-to-close move but the trade
we would have made. Replayed with the paper workflow's rules:

* Entry as Miltos confirms it (``alpaca_paper_trader.entry_confirmation``): a long enters when a 15-minute bar of
  the session closes above the previous bar's high with a green body (a short: below the previous low, red body),
  at that bar's close, only for bars starting before 15:00 New York. No confirmation: the position would not have
  been opened, and that is recorded as such (``entered`` false), not as a zero result.
* Exit as the monitors manage it: stop-loss and take-profit at the session's limits (3% / 6% by default) checked
  on each later bar's low and high - a bar touching both counts as the stop (no flattering) - otherwise flat at the
  last bar of the session.
* ``forced_result_pct``: the same trade entered at the close of the first bar regardless of confirmation, to judge
  Miltos's filter.

Bars are read from Alpaca market data (read-only, IEX feed, owner's approval 2026-10-15/16). A symbol without
bars stays unknown.
"""
from __future__ import annotations

from datetime import date, datetime, time as dtime, timezone
import json
from typing import Any, Callable
from zoneinfo import ZoneInfo

NEW_YORK = ZoneInfo("America/New_York")
ENTRY_UNTIL = dtime(15, 0)

Bars = Callable[[list[str], date], dict[str, list[dict[str, Any]]]]


def _moment(stamp: Any) -> datetime | None:
    import re

    text = re.sub(r"(\.\d{6})\d+", r"\1", str(stamp or "").strip()).replace("Z", "+00:00")
    try:
        moment = datetime.fromisoformat(text) if text else None
    except ValueError:
        return None
    return None if moment is None else (moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc))


def session_bars(bars: list[dict[str, Any]], session: date) -> list[dict[str, Any]]:
    """Regular-session bars of ``session`` (09:30-16:00 New York), in time order, with numeric fields."""
    out = []
    for bar in bars:
        moment = _moment(bar.get("t"))
        if moment is None:
            continue
        local = moment.astimezone(NEW_YORK)
        if local.date() != session or not (dtime(9, 30) <= local.time() < dtime(16, 0)):
            continue
        try:
            out.append({"t": local, "o": float(bar["o"]), "h": float(bar["h"]), "l": float(bar["l"]), "c": float(bar["c"])})
        except (KeyError, TypeError, ValueError):
            continue
    return sorted(out, key=lambda b: b["t"])


def confirmed(previous: dict[str, Any], latest: dict[str, Any], direction: str) -> bool:
    """Miltos's 15-minute trigger (same rule as alpaca_paper_trader.entry_confirmation)."""
    if direction == "short":
        return latest["c"] < previous["l"] and latest["c"] < latest["o"]
    return latest["c"] > previous["h"] and latest["c"] > latest["o"]


def _manage(bars: list[dict[str, Any]], start: int, entry: float, direction: str, stop: float, take: float
            ) -> tuple[float, str, datetime]:
    """Stop / take-profit / end of day from bar ``start + 1`` on; a bar touching both counts as the stop."""
    long = direction != "short"
    stop_price = entry * (1 - stop) if long else entry * (1 + stop)
    take_price = entry * (1 + take) if long else entry * (1 - take)
    for bar in bars[start + 1:]:
        hit_stop = bar["l"] <= stop_price if long else bar["h"] >= stop_price
        hit_take = bar["h"] >= take_price if long else bar["l"] <= take_price
        if hit_stop:
            return stop_price, "stop_loss", bar["t"]
        if hit_take:
            return take_price, "take_profit", bar["t"]
    return bars[-1]["c"], "end_of_day", bars[-1]["t"]


def _result(entry: float, exit_price: float, direction: str) -> float:
    return round((1.0 if direction != "short" else -1.0) * (exit_price / entry - 1) * 100, 3)


def simulate(bars: list[dict[str, Any]], session: date, direction: str, *, stop: float = 0.03,
             take: float = 0.06) -> dict[str, Any] | None:
    """The trade our rules would have made, or None when the session has fewer than two bars (unknown)."""
    day = session_bars(bars, session)
    if len(day) < 2:
        return None
    forced_exit, _, _ = _manage(day, 0, day[0]["c"], direction, stop, take)
    out: dict[str, Any] = {"entered": False, "forced_result_pct": _result(day[0]["c"], forced_exit, direction),
                           "bars": len(day)}
    for i in range(1, len(day)):
        if day[i]["t"].time() >= ENTRY_UNTIL:
            break
        if confirmed(day[i - 1], day[i], direction):
            entry = day[i]["c"]
            exit_price, reason, exit_at = _manage(day, i, entry, direction, stop, take)
            out.update({"entered": True, "entry_price": round(entry, 4), "entry_at": day[i]["t"].isoformat(),
                        "exit_price": round(exit_price, 4), "exit_at": exit_at.isoformat(), "exit_reason": reason,
                        "result_pct": _result(entry, exit_price, direction)})
            break
    return out


def spy_session(bars: list[dict[str, Any]], session: date) -> float | None:
    day = session_bars(bars, session)
    return None if len(day) < 2 else round((day[-1]["c"] / day[0]["o"] - 1) * 100, 3)


def alpaca_bars(key_id: str, secret: str, timeout: float = 30.0) -> Bars:
    """Read-only market data: the session's 15-minute bars for many symbols (IEX feed, paginated)."""
    from urllib import parse, request

    def fetch(symbols: list[str], session: date) -> dict[str, list[dict[str, Any]]]:
        out: dict[str, list[dict[str, Any]]] = {}
        for offset in range(0, len(symbols), 100):
            token = None
            while True:
                query = {"symbols": ",".join(symbols[offset:offset + 100]), "timeframe": "15Min", "feed": "iex",
                         "start": f"{session.isoformat()}T13:00:00Z", "end": f"{session.isoformat()}T21:00:00Z",
                         "limit": 10000, "adjustment": "raw"}
                if token:
                    query["page_token"] = token
                req = request.Request(f"https://data.alpaca.markets/v2/stocks/bars?{parse.urlencode(query)}",
                                      headers={"APCA-API-KEY-ID": key_id, "APCA-API-SECRET-KEY": secret,
                                               "Accept": "application/json"})
                with request.urlopen(req, timeout=timeout) as response:
                    payload = json.loads(response.read() or b"{}")
                for symbol, bars in (payload.get("bars") or {}).items():
                    out.setdefault(symbol, []).extend(bars or [])
                token = payload.get("next_page_token")
                if not token:
                    break
        return out
    return fetch
