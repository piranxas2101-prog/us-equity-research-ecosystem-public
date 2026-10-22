"""Multi-day paper positions, watched every session on their own clock (ADR-001 step 3, owner 2026-10-23).

A position opened for a week, a month or a quarter (positions_registry.py) is not flattened at 15:55; this monitor
applies, live, the rules the exit lab and thesis_exit.py measured on five years and on the archived runs:

* widening disaster stop: 1.1 x ATR(14) x sqrt(sessions held), clamped 1.5-20% (``thesis_exit.scaled_stop``);
* thesis exit: once per new nightly run, the trader's board for the stock is judged with ``thesis_exit.thesis_break``
  for the position's class (week technical, month both with two weak nights, quarter fundamental). A stock missing
  from the board is unknown: held. A break decided at night exits at the first cycle of the next session;
* abrupt change of the market's mood (owner 2026-10-22: every position is watched whatever its class): from 15:45
  New York, SPY beyond 2 x its ATR(14) against the position, or the market turning from calm to volatile
  (``thesis_exit.market_shocks`` on SPY's daily bars with today's bar so far) - the lab measured it on the close;
* time stop: the class's last session (5 / 20 / 60), from 15:50 New York.

Paper account only; closes go through the same Alpaca call as the day monitor (``close_position_qty``) and only with
``--submit``. Every action is written to output/multi_day_monitor_latest.{json,md} and the day's action journal.
"""
from __future__ import annotations

import argparse
from datetime import date, timedelta
import json
from pathlib import Path
import time
from typing import Any
from urllib import parse as urlparse

import pandas as pd

from alpaca_paper_exit_manager import alpaca_request, close_position_qty, load_dotenv, load_json, now_athens, to_float
from alpaca_paper_position_monitor import (
    compact_order_payload,
    current_session_label,
    fetch_position,
    journal_actions,
    now_eastern,
    unrealized_plpc,
)
import positions_registry
import thesis_exit

DATA_URL = "https://data.alpaca.markets"
SHOCK_FROM = 15 * 60 + 45
TIME_STOP_FROM = 15 * 60 + 50


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


def decide(entry: dict[str, Any], position: dict[str, Any], *, today: date, minutes: int,
           shock: str | None, strategy_dir: Path) -> tuple[str | None, str]:
    """(exit reason or None, explanation). Updates the entry's thesis state in place."""
    symbol, cls, direction = entry["symbol"], entry["holding_class"], entry["direction"]
    held = positions_registry.sessions_held(entry["opened_session"], today)
    plpc = unrealized_plpc(position)
    stop = thesis_exit.scaled_stop(to_float(entry.get("atr_14_pct")), held)
    if plpc is not None and stop is not None and plpc <= -stop:
        return "scaled_stop", f"{symbol} {plpc:.2%} beyond the {stop:.2%} stop of session {held} ({cls})."
    run_id, rows = board_rows(entry["agent"], symbol, strategy_dir)
    if run_id and run_id != entry.get("last_thesis_run") and entry["opened_session"] != str(today):
        state = {"weak_nights": int(entry.get("weak_nights") or 0)}
        reason = thesis_exit.thesis_break(cls, entry, rows, state)
        entry.update(weak_nights=state["weak_nights"], last_thesis_run=run_id, last_thesis_check=str(today))
        if reason:
            return f"thesis_{reason}", f"{symbol}: the reason it was opened no longer holds ({reason}, run {run_id})."
    if minutes >= SHOCK_FROM and shock_against(shock, direction):
        return "market_shock", f"SPY today: {shock} - an abrupt change of the market's mood against {symbol}."
    if held >= int(entry["sessions"]) and minutes >= TIME_STOP_FROM:
        return "time_stop", f"{symbol} reached the end of its {cls} ({held} sessions)."
    return None, f"{symbol} held: session {held} of {entry['sessions']}, P/L {plpc}, stop {stop}."


def monitor_once(args: argparse.Namespace, spy_cache: dict[str, Any]) -> dict[str, Any]:
    eastern = now_eastern()
    today, minutes = eastern.date(), eastern.hour * 60 + eastern.minute
    entries = positions_registry.load(Path(args.registry))
    shock = None
    if entries and minutes >= SHOCK_FROM:
        if spy_cache.get("at") is None or time.monotonic() - spy_cache["at"] > 240:
            spy_cache.update(at=time.monotonic(), shocks=thesis_exit.market_shocks(spy_daily(today)))
        shock = spy_cache["shocks"].get(today)
    actions: list[dict[str, Any]] = []
    holds: list[dict[str, Any]] = []
    closes: dict[str, tuple[str, dict[str, Any]]] = {}
    states: dict[str, dict[str, Any]] = {}
    for symbol, entry in sorted(entries.items()):
        position = fetch_position(symbol)
        if position is None:
            closes[symbol] = ("not_in_account", {})
            continue
        reason, explanation = decide(entry, position, today=today, minutes=minutes, shock=shock,
                                     strategy_dir=Path(args.strategy_dir))
        states[symbol] = {key: entry.get(key) for key in ("weak_nights", "last_thesis_run", "last_thesis_check")}
        if reason is None:
            holds.append({"ticker": symbol, "holding_class": entry["holding_class"], "decision_reason": explanation})
            continue
        qty = str(position.get("qty") or "").strip().lstrip("-")  # a short position's qty is negative
        action = {"ticker": symbol, "trade_direction": entry["direction"], "holding_class": entry["holding_class"],
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
    lines += [f"- {a['ticker']} {a['exit_rule']} ({a['holding_class']}): {a['action_status']} - {a['decision_reason']}"
              for a in payload["actions"]] or ["- None."]
    lines += ["", "## Held", ""]
    lines += [f"- {h['decision_reason']}" for h in payload["holds"]] or ["- None."]
    return "\n".join(lines) + "\n"


def run(args: argparse.Namespace) -> int:
    load_dotenv(Path(".env"))
    spy_cache: dict[str, Any] = {}
    while True:
        payload = monitor_once(args, spy_cache)
        out = Path(args.out_prefix)
        out.parent.mkdir(parents=True, exist_ok=True)
        Path(f"{out}_latest.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        Path(f"{out}_latest.md").write_text(render(payload), encoding="utf-8")
        journal_actions(payload)
        print(f"[MULTI-DAY] session={payload['session']} positions={payload['positions']} "
              f"actions={payload['actions_count']}")
        if args.once or payload["remaining_active_count"] == 0 or payload["session"] in ("after_hours", "closed"):
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
    parser.add_argument("--submit", action="store_true", help="Send the closes to the Alpaca paper account.")
    return parser


def main(argv: list[str] | None = None) -> int:
    return run(build_parser().parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())
