"""Positions held on their agent's horizon, watched every session on their own clock (ADR-001 step 3; owner
2026-10-23: the agents decide how long a position stays open, the monitors close it whenever its rules say so).

A position with an agent horizon (positions_registry.py, holding_horizon.py) is not flattened at the close; this
monitor applies, live, the rules the exit lab and thesis_exit.py measured on five years and on the archived runs,
with time counted in trading minutes from the fill (``trading_clock``: nights, weekends, holidays, early closes):

* widening disaster stop: 1.1 x ATR(14) x sqrt(sessions held so far), clamped 1.5-20% (``thesis_exit.scaled_stop``);
* thesis exit: once per new nightly run, the trader's board for the stock is judged with ``thesis_exit.thesis_break``
  with the rules that fit the horizon (up to 5 sessions technical, up to 20 both with two weak nights, beyond that
  fundamental). A stock missing from the board is unknown: held. A break decided at night exits at the first cycle;
* abrupt change of the market's mood (owner 2026-10-22: every position is watched whatever its horizon): in the
  last ``SHOCK_WINDOW`` minutes of the session, SPY beyond 2 x its ATR(14) against the position, or the market
  turning from calm to volatile (``thesis_exit.market_shocks`` on SPY's daily bars with today's bar so far) - the
  lab measured it on the close and left at the next open, so live exits come one overnight gap earlier;
* horizon end: when the agent's horizon has run out, or 5 minutes before the close of the session it ends in.

Paper account only; closes go through the same Alpaca call as the day monitor (``close_position_qty``) and only with
``--submit``. Every action is written to output/multi_day_monitor_latest.{json,md} and the day's action journal.
"""
from __future__ import annotations

import argparse
from datetime import date, datetime, timedelta
import json
import math
from pathlib import Path
import time
from typing import Any
from urllib import parse as urlparse

import pandas as pd

from alpaca_paper_exit_manager import alpaca_request, close_position_qty, load_dotenv, load_json, now_athens, to_float
from alpaca_paper_position_monitor import (
    compact_order_payload,
    current_session_label,
    fetch_positions,
    journal_actions,
    now_eastern,
    unrealized_plpc,
)
import positions_registry
import thesis_exit
import trading_clock

DATA_URL = "https://data.alpaca.markets"
SHOCK_WINDOW = 15  # minutes before the close
CLOSE_MARGIN = 5  # minutes before the close


def spy_daily(today: date) -> pd.DataFrame | None:
    """SPY daily bars (IEX feed, read-only market data) with today's bar so far; None when unavailable."""
    query = urlparse.urlencode({"timeframe": "1Day", "start": (today - timedelta(days=430)).isoformat(),
                                "limit": 1000, "feed": "iex", "adjustment": "all"})
    try:
        payload = alpaca_request("GET", f"{DATA_URL}/v2/stocks/SPY/bars?{query}")
    except RuntimeError:
        return None
    bars = payload.get("bars") if isinstance(payload, dict) else None
    if not bars:
        return None
    frame = pd.DataFrame({"High": [b.get("h") for b in bars], "Low": [b.get("l") for b in bars],
                          "Close": [b.get("c") for b in bars]},
                         index=pd.to_datetime([str(b.get("t"))[:10] for b in bars]))
    return frame.astype(float)


def shock_against(shock: str | None, direction: str) -> bool:
    return shock == "volatile" or (shock == "down" and direction == "long") or (shock == "up" and direction == "short")


def board_rows(agent: str, symbol: str, strategy_dir: Path) -> tuple[str | None, list[dict[str, Any]] | None]:
    """The run id of the trader's latest board and the stock's rows on it (None when the stock is not on it)."""
    payload = load_json(strategy_dir / f"{agent}_strategy_latest.json")
    rows = [{key: row.get(key) for key in thesis_exit.FIELDS} for row in payload.get("results") or []
            if isinstance(row, dict) and str(row.get("ticker") or "").upper() == symbol]
    return (str(payload.get("run_id") or "") or None), (rows or None)


def decide(entry: dict[str, Any], position: dict[str, Any], *, now: datetime, shock: str | None,
           strategy_dir: Path) -> tuple[str | None, str]:
    """(exit reason or None, explanation). Updates the entry's thesis state in place."""
    symbol, cls, direction = entry["symbol"], entry["holding_class"], entry["direction"]
    today = now.astimezone(trading_clock.EASTERN).date()
    filled = datetime.fromisoformat(str(entry["filled_at"]).replace("Z", "+00:00"))
    expires = datetime.fromisoformat(str(entry["expires_at"]))
    elapsed = trading_clock.trading_minutes_between(filled, now)
    held = max(1, math.ceil(elapsed / trading_clock.SESSION_MINUTES))
    left = trading_clock.minutes_to_close(now)
    plpc = unrealized_plpc(position)
    stop = thesis_exit.scaled_stop(to_float(entry.get("atr_14_pct")), held)
    if plpc is not None and stop is not None and plpc <= -stop:
        return "scaled_stop", f"{symbol} {plpc:.2%} beyond the {stop:.2%} stop of session {held}."
    run_id, rows = board_rows(entry["agent"], symbol, strategy_dir)
    if run_id and run_id != entry.get("last_thesis_run") and entry["opened_session"] != str(today):
        state = {"weak_nights": int(entry.get("weak_nights") or 0)}
        reason = thesis_exit.thesis_break(cls, entry, rows, state)
        entry.update(weak_nights=state["weak_nights"], last_thesis_run=run_id, last_thesis_check=str(today))
        if reason:
            return f"thesis_{reason}", f"{symbol}: the reason it was opened no longer holds ({reason}, run {run_id})."
    if left is not None and left <= SHOCK_WINDOW and shock_against(shock, direction):
        return "market_shock", f"SPY today: {shock} - an abrupt change of the market's mood against {symbol}."
    ends_today = expires.astimezone(trading_clock.EASTERN).date() <= today
    if now >= expires or (ends_today and left is not None and left <= CLOSE_MARGIN):
        return "horizon_end", (f"{symbol} reached the end of its agent's horizon ({entry['horizon_sessions']} "
                               f"session(s), {elapsed} trading minutes held).")
    return None, (f"{symbol} held: {elapsed} of {entry['horizon_minutes']} trading minutes, P/L {plpc}, "
                  f"stop {stop}.")


def monitor_once(args: argparse.Namespace, spy_cache: dict[str, Any]) -> dict[str, Any]:
    eastern = now_eastern()
    today = eastern.date()
    entries = positions_registry.load(Path(args.registry))
    shock = None
    left = trading_clock.minutes_to_close(eastern)
    if entries and left is not None and left <= SHOCK_WINDOW:
        if spy_cache.get("at") is None or time.monotonic() - spy_cache["at"] > 240:
            spy_cache.update(at=time.monotonic(), shocks=thesis_exit.market_shocks(spy_daily(today)))
        shock = spy_cache["shocks"].get(today)
    actions: list[dict[str, Any]] = []
    holds: list[dict[str, Any]] = []
    closes: dict[str, tuple[str, dict[str, Any]]] = {}
    states: dict[str, dict[str, Any]] = {}
    positions = fetch_positions() if entries else {}
    for symbol, entry in sorted(entries.items()):
        position = positions.get(symbol)
        if position is None:
            closes[symbol] = ("not_in_account", {})
            continue
        reason, explanation = decide(entry, position, now=eastern, shock=shock, strategy_dir=Path(args.strategy_dir))
        states[symbol] = {key: entry.get(key) for key in ("weak_nights", "last_thesis_run", "last_thesis_check")}
        if reason is None:
            holds.append({"ticker": symbol, "horizon_sessions": entry["horizon_sessions"],
                          "decision_reason": explanation})
            continue
        qty = str(position.get("qty") or "").strip().lstrip("-")  # a short position's qty is negative
        action = {"ticker": symbol, "trade_direction": entry["direction"], "agent": entry["agent"],
                  "playbook_name": entry.get("playbook"), "horizon_sessions": entry["horizon_sessions"],
                  "action_type": "close_position", "exit_rule": reason, "decision_reason": explanation,
                  "position_plpc": unrealized_plpc(position), "action_status": "preview",
                  "request_payload": {"symbol": symbol, "qty": qty}}
        if args.submit and qty:
            try:
                response = close_position_qty(symbol, qty)
                action["action_status"] = str(response.get("status") or "submitted")
                action["response_payload"] = compact_order_payload(response)
                closes[symbol] = (reason, {"exit_plpc": action["position_plpc"],
                                           "exit_order_id": response.get("id")})
            except Exception as exc:  # the position stays registered and is tried again next cycle
                action["action_status"] = "close_failed"
                action["action_error"] = str(exc)
        actions.append(action)

    def apply(current: dict[str, dict[str, Any]]) -> None:
        for symbol, state in states.items():
            if symbol in current:
                current[symbol].update(state)
        for symbol, (reason, detail) in closes.items():
            positions_registry.close(current, symbol, reason, detail=detail, path=Path(args.closed_log))

    if states or closes:
        positions_registry.update(apply, Path(args.registry))
    return {"run_timestamp": now_athens().isoformat(), "agent_name": "multi_day_monitor",
            "session": current_session_label(), "submit": bool(args.submit), "market_shock_today": shock,
            "positions": len(entries), "actions_count": len(actions), "holds_count": len(holds),
            "remaining_active_count": len(entries) - len(closes), "actions": actions, "holds": holds}


def render(payload: dict[str, Any]) -> str:
    lines = ["# Multi-day positions monitor", "", f"- Run: {payload['run_timestamp']}",
             f"- Session: {payload['session']} | submit: {payload['submit']}",
             f"- Market shock today: {payload['market_shock_today'] or 'none'}",
             f"- Positions: {payload['positions']} | actions: {payload['actions_count']}", "", "## Actions", ""]
    lines += [f"- {a['ticker']} {a['exit_rule']} (horizon {a['horizon_sessions']}): {a['action_status']} - "
              f"{a['decision_reason']}"
              for a in payload["actions"]] or ["- None."]
    lines += ["", "## Held", ""]
    lines += [f"- {h['decision_reason']}" for h in payload["holds"]] or ["- None."]
    return "\n".join(lines) + "\n"


def run(args: argparse.Namespace) -> int:
    load_dotenv(Path(".env"))
    spy_cache: dict[str, Any] = {}
    while True:
        try:
            payload = monitor_once(args, spy_cache)
        except Exception as exc:  # a broker or network error must not leave the positions unwatched
            print(f"[MULTI-DAY] cycle failed, retrying next cycle: {exc}", flush=True)
            time.sleep(max(15, int(args.poll_seconds)))
            continue
        out = Path(args.out_prefix)
        out.parent.mkdir(parents=True, exist_ok=True)
        Path(f"{out}_latest.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        Path(f"{out}_latest.md").write_text(render(payload), encoding="utf-8")
        journal_actions(payload)
        print(f"[MULTI-DAY] session={payload['session']} positions={payload['positions']} "
              f"actions={payload['actions_count']}", flush=True)
        closed = (trading_clock.minutes_to_close(now_eastern()) or 0) == 0
        # --until-close: positions handed over during the day by the day monitors are picked up as they come
        if args.once or closed or (payload["remaining_active_count"] == 0 and not args.until_close):
            return 0
        time.sleep(max(15, int(args.poll_seconds)))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--registry", default=str(positions_registry.REGISTRY))
    parser.add_argument("--closed-log", default=str(positions_registry.CLOSED))
    parser.add_argument("--strategy-dir", default="output")
    parser.add_argument("--out-prefix", default="output/multi_day_monitor")
    parser.add_argument("--poll-seconds", type=int, default=60)
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--until-close", action="store_true",
                        help="keep watching until the close even with no position yet (new ones are handed over)")
    parser.add_argument("--submit", action="store_true", help="Send the closes to the Alpaca paper account.")
    return parser


def main(argv: list[str] | None = None) -> int:
    return run(build_parser().parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())
