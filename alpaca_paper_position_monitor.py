from __future__ import annotations

import argparse
import json
import time
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from alpaca_paper_exit_manager import (
    alpaca_request,
    best_current_row,
    close_position_qty,
    exit_decision_reason,
    exit_justification,
    fetch_order_by_id,
    load_dotenv,
    load_json,
    now_athens,
    to_float,
)
from alpaca_paper_trader import render_markdown_report as render_opening_markdown_report
import holding_horizon
import positions_registry
import trading_clock


ATHENS_TZ = ZoneInfo("Europe/Athens")
EASTERN_TZ = ZoneInfo("America/New_York")
CANCELABLE_ORDER_STATUSES = {"new", "accepted", "pending_new", "partially_filled", "done_for_day"}
TERMINAL_ORDER_STATUSES = {"filled", "canceled", "expired", "rejected", "suspended", "stopped", "done_for_day"}


def now_eastern() -> datetime:
    return datetime.now(EASTERN_TZ)


def current_session_label() -> str:
    eastern_now = now_eastern()
    minutes = eastern_now.hour * 60 + eastern_now.minute
    if 4 * 60 <= minutes < 9 * 60 + 30:
        return "pre_market"
    if 9 * 60 + 30 <= minutes < 16 * 60:
        return "regular"
    if 16 * 60 <= minutes < 20 * 60:
        return "after_hours"
    return "closed"


def parse_cutoff_minutes(raw: str) -> int:
    text = str(raw or "").strip()
    parts = text.split(":")
    if len(parts) != 2:
        raise SystemExit(f"Invalid eod cutoff format: {raw!r}. Expected HH:MM.")
    hour = int(parts[0])
    minute = int(parts[1])
    if hour < 0 or hour > 23 or minute < 0 or minute > 59:
        raise SystemExit(f"Invalid eod cutoff value: {raw!r}.")
    return hour * 60 + minute


def eod_cutoff_reached(cutoff_minutes: int) -> bool:
    eastern_now = now_eastern()
    now_minutes = eastern_now.hour * 60 + eastern_now.minute
    # an early close (13:00, e.g. the day after Thanksgiving) moves the flat to 5 minutes before it (trading_clock)
    left = trading_clock.minutes_to_close(eastern_now)
    return now_minutes >= cutoff_minutes or (left is not None and left <= 5)


def fetch_position(symbol: str) -> dict[str, Any] | None:
    try:
        payload = alpaca_request("GET", f"/v2/positions/{symbol}")
    except RuntimeError as exc:
        if "HTTP 404" in str(exc):
            return None
        raise
    return payload if isinstance(payload, dict) else None


def cancel_order(order_id: str) -> dict[str, Any]:
    return alpaca_request("DELETE", f"/v2/orders/{order_id}")


def compact_order_payload(order: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": order.get("id"),
        "client_order_id": order.get("client_order_id"),
        "status": order.get("status"),
        "symbol": order.get("symbol"),
        "side": order.get("side"),
        "notional": order.get("notional"),
        "qty": order.get("qty"),
        "filled_qty": order.get("filled_qty"),
        "submitted_at": order.get("submitted_at"),
        "filled_at": order.get("filled_at"),
        "canceled_at": order.get("canceled_at"),
        "updated_at": order.get("updated_at"),
        "filled_avg_price": order.get("filled_avg_price"),
    }


def refreshed_action_response(order_id: str, wait_seconds: float, attempts: int = 5) -> dict[str, Any] | None:
    if not order_id:
        return None
    latest: dict[str, Any] | None = None
    for attempt in range(max(1, attempts)):
        if wait_seconds > 0:
            time.sleep(wait_seconds)
        try:
            payload = fetch_order_by_id(order_id)
        except Exception:
            break
        if not isinstance(payload, dict):
            break
        latest = compact_order_payload(payload)
        status = str(latest.get("status") or "").lower().strip()
        if status in TERMINAL_ORDER_STATUSES:
            break
    return latest


def is_terminal_status(status: Any) -> bool:
    return str(status or "").lower().strip() in TERMINAL_ORDER_STATUSES


def write_open_report(path: Path, payload: dict[str, Any]) -> None:
    payload["broker_reconciled_at"] = now_athens().isoformat()
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path = Path(str(path).replace("_latest.json", "_latest.md"))
    if md_path.exists():
        md_path.write_text(render_opening_markdown_report(payload), encoding="utf-8")


def refreshed_order(item: dict[str, Any]) -> dict[str, Any]:
    order = item.get("order_response") or {}
    order_id = str(order.get("id") or "").strip()
    if not order_id:
        return order if isinstance(order, dict) else {}
    try:
        payload = fetch_order_by_id(order_id)
        return payload if isinstance(payload, dict) else {}
    except Exception:
        return order if isinstance(order, dict) else {}


def live_qty_from_opening(opening: dict[str, Any], order: dict[str, Any]) -> str:
    qty = order.get("filled_qty") or order.get("qty") or opening.get("order_response", {}).get("qty")
    if qty is None:
        return ""
    return str(qty).strip()


def unrealized_plpc(position: dict[str, Any] | None) -> float | None:
    if not isinstance(position, dict):
        return None
    return to_float(position.get("unrealized_plpc"))


def today_flat_reason(agent_name: str, symbol: str) -> str:
    return f"{agent_name} closes {symbol} because today's paper positions must be flat before the session ends."


def render_markdown_report(payload: dict[str, Any]) -> str:
    lines = [
        f"# {payload.get('agent_name')} Position Monitor",
        "",
        f"- Run timestamp: {payload.get('run_timestamp')}",
        f"- Session: {payload.get('session')}",
        f"- Poll seconds: {payload.get('poll_seconds')}",
        f"- EOD cutoff: {payload.get('eod_cutoff')}",
        f"- Today-only policy: {payload.get('today_only_policy')}",
        f"- Monitored openings: {payload.get('monitored_openings')}",
        f"- Holds: {payload.get('holds_count')}",
        f"- Actions: {payload.get('actions_count')}",
        f"- Remaining active: {payload.get('remaining_active_count')}",
        "",
        "## Actions",
        "",
    ]

    actions = payload.get("actions") or []
    if actions:
        for item in actions:
            lines.append(
                f"- {item.get('ticker')} | type={item.get('action_type')} | direction={item.get('trade_direction')} | "
                f"status={item.get('action_status')}"
            )
            lines.append(f"  reason: {item.get('decision_reason')}")
            if item.get("position_plpc") is not None:
                lines.append(f"  position_plpc: {item.get('position_plpc')}")
            if item.get("request_payload"):
                lines.append(f"  request: {json.dumps(item.get('request_payload'), ensure_ascii=False)}")
            if item.get("response_payload"):
                lines.append(f"  response: {json.dumps(item.get('response_payload'), ensure_ascii=False)}")
            if item.get("refreshed_response_payload"):
                lines.append(f"  refreshed_response: {json.dumps(item.get('refreshed_response_payload'), ensure_ascii=False)}")
            if item.get("action_error"):
                lines.append(f"  error: {item.get('action_error')}")
        lines.append("")
    else:
        lines.extend(["- No actions this cycle.", ""])

    holds = payload.get("holds") or []
    lines.extend(["## Holds", ""])
    if holds:
        for item in holds:
            lines.append(
                f"- {item.get('ticker')} | direction={item.get('trade_direction')} | "
                f"status={item.get('live_order_status')} | reason={item.get('decision_reason')}"
            )
    else:
        lines.append("- No holds this cycle.")
    lines.append("")
    return "\n".join(lines)


def monitor_once(args: argparse.Namespace) -> dict[str, Any]:
    open_report_path = Path(args.open_report)
    open_report = load_json(open_report_path)
    strategy_payload = load_json(Path(args.strategy))
    agent_name = str(open_report.get("agent_name") or args.agent_name or "agent")
    cutoff_minutes = parse_cutoff_minutes(args.eod_cutoff)
    force_flat_now = bool(args.today_only_policy) and eod_cutoff_reached(cutoff_minutes)

    actions: list[dict[str, Any]] = []
    holds: list[dict[str, Any]] = []
    remaining_active_count = 0
    entry_order_refreshes = 0
    open_report_changed = False

    for opening in open_report.get("openings", []):
        if not isinstance(opening, dict):
            continue
        order = refreshed_order(opening)
        order_id = str(order.get("id") or opening.get("order_response", {}).get("id") or "").strip()
        symbol = str(opening.get("ticker") or "").upper().strip()
        if not symbol or not order_id:
            continue

        if order:
            compact_order = compact_order_payload(order)
            previous_status = str(opening.get("order_status") or "").strip()
            latest_status = str(compact_order.get("status") or previous_status).strip()
            opening["order_status"] = latest_status
            opening["order_response"] = compact_order
            opening["last_broker_reconciled_at"] = now_athens().isoformat()
            entry_order_refreshes += 1
            if latest_status != previous_status or opening.get("refreshed_order_response") != compact_order:
                opening["refreshed_order_response"] = compact_order
                open_report_changed = True

        order_status = str(order.get("status") or opening.get("order_status") or "").lower().strip()
        trade_direction = str(opening.get("trade_direction") or "long").lower().strip() or "long"
        qty = live_qty_from_opening(opening, order)
        position = fetch_position(symbol)
        position_plpc = unrealized_plpc(position)

        if force_flat_now and order_status in CANCELABLE_ORDER_STATUSES and position is None:
            action = {
                "ticker": symbol,
                "trade_direction": trade_direction,
                "action_type": "cancel_order",
                "decision_reason": today_flat_reason(agent_name, symbol),
                "action_status": "preview",
                "order_status": order_status,
            }
            if args.submit:
                try:
                    cancel_order(order_id)
                    action["action_status"] = "canceled"
                    action["request_payload"] = {"order_id": order_id}
                    action["response_payload"] = {"order_id": order_id}
                    refreshed = refreshed_action_response(
                        order_id,
                        args.post_action_refresh_seconds,
                        attempts=args.post_action_refresh_attempts,
                    )
                    if refreshed:
                        action["refreshed_response_payload"] = refreshed
                        action["action_status"] = str(refreshed.get("status") or action["action_status"])
                    action["broker_terminal"] = is_terminal_status(action.get("action_status"))
                    if not action["broker_terminal"]:
                        remaining_active_count += 1
                except Exception as exc:
                    action["action_status"] = "cancel_failed"
                    action["action_error"] = str(exc)
                    action["broker_terminal"] = False
                    remaining_active_count += 1
            actions.append(action)
            continue

        if position is None or not qty:
            if order_status in CANCELABLE_ORDER_STATUSES:
                remaining_active_count += 1
                holds.append(
                    {
                        "ticker": symbol,
                        "trade_direction": trade_direction,
                        "live_order_status": order_status,
                        "decision_reason": f"{agent_name} is still waiting for {symbol} to fill.",
                    }
                )
            continue

        current_row = best_current_row(strategy_payload, symbol)
        plan = opening.get("paper_trade_plan") if isinstance(opening.get("paper_trade_plan"), dict) else {}
        if plan.get("holding_period_rule") == holding_horizon.RULE:
            # owner 2026-10-23: the agent decided how long it holds (holding_horizon.py); from its fill on the
            # position belongs to alpaca_multi_day_monitor.py (widening stop, thesis, market shock, horizon end) -
            # no same-day stop, take-profit or flat here.
            def hand_over(entries, symbol=symbol, opening=opening, row=current_row):
                return symbol in entries or positions_registry.register(entries, opening, agent=agent_name, row=row)

            if positions_registry.update(hand_over):
                horizon = plan.get("holding_horizon") or {}
                holds.append({"ticker": symbol, "trade_direction": trade_direction, "live_order_status": order_status,
                              "holding_horizon_sessions": horizon.get("sessions"),
                              "decision_reason": f"{agent_name} holds {symbol} for up to {horizon.get('sessions')} "
                                                 "session(s): handed to the multi-day monitor."})
                continue
            opening["holding_horizon_fallback"] = "same_day_atr_or_fill_time_unknown"
        should_close, decision_reason = exit_decision_reason(
            agent_name=agent_name,
            opening=opening,
            current_row=current_row,
            exit_score_floor=args.exit_score_floor,
            close_on_watch=args.close_on_watch,
        )

        trade_plan = opening.get("paper_trade_plan") if isinstance(opening.get("paper_trade_plan"), dict) else {}
        stop_loss_pct = to_float(trade_plan.get("stop_loss_pct")) or args.stop_loss_pct
        take_profit_pct = to_float(trade_plan.get("take_profit_pct")) or args.take_profit_pct

        if position_plpc is not None and position_plpc <= -abs(stop_loss_pct):
            should_close = True
            decision_reason = (
                f"{agent_name} stop-loss hit on {symbol} "
                f"({position_plpc:.4f} <= {-abs(stop_loss_pct):.4f}; source=agent_trade_plan)."
            )
        elif position_plpc is not None and position_plpc >= abs(take_profit_pct):
            should_close = True
            decision_reason = (
                f"{agent_name} take-profit hit on {symbol} "
                f"({position_plpc:.4f} >= {abs(take_profit_pct):.4f}; source=agent_trade_plan)."
            )
        elif force_flat_now:
            should_close = True
            decision_reason = today_flat_reason(agent_name, symbol)

        if not should_close:
            remaining_active_count += 1
            holds.append(
                {
                    "ticker": symbol,
                    "trade_direction": trade_direction,
                    "live_order_status": order_status,
                    "decision_reason": decision_reason,
                }
            )
            continue

        action = {
            "ticker": symbol,
            "trade_direction": trade_direction,
            "action_type": "close_position",
            "decision_reason": decision_reason,
            "position_plpc": position_plpc,
            "action_status": "preview",
            "request_payload": {"symbol": symbol, "qty": qty},
            "exit_justification": exit_justification(agent_name, opening, current_row, decision_reason),
        }
        if args.submit:
            try:
                response = close_position_qty(symbol, qty)
                action["action_status"] = str(response.get("status") or "submitted")
                action["response_payload"] = compact_order_payload(response)
                refreshed = refreshed_action_response(
                    str(response.get("id") or ""),
                    args.post_action_refresh_seconds,
                    attempts=args.post_action_refresh_attempts,
                )
                if refreshed:
                    action["refreshed_response_payload"] = refreshed
                    action["action_status"] = str(refreshed.get("status") or action["action_status"])
                action["broker_terminal"] = is_terminal_status(action.get("action_status"))
                if not action["broker_terminal"]:
                    remaining_active_count += 1
            except Exception as exc:
                action["action_status"] = "close_failed"
                action["action_error"] = str(exc)
                action["broker_terminal"] = False
                remaining_active_count += 1
        else:
            remaining_active_count += 1
        actions.append(action)

    payload = {
        "run_timestamp": now_athens().isoformat(),
        "agent_name": agent_name,
        "session": current_session_label(),
        "poll_seconds": args.poll_seconds,
        "eod_cutoff": args.eod_cutoff,
        "today_only_policy": bool(args.today_only_policy),
        "monitored_openings": len(open_report.get("openings", [])),
        "holds_count": len(holds),
        "actions_count": len(actions),
        "remaining_active_count": remaining_active_count,
        "entry_order_refreshes": entry_order_refreshes,
        "open_report_reconciled": open_report_changed,
        "actions": actions,
        "holds": holds,
        "all_done": remaining_active_count == 0,
        "force_flat_now": force_flat_now,
    }
    if open_report_changed:
        write_open_report(open_report_path, open_report)
    return payload


ACTION_JOURNAL_DIR = Path("output") / "paper_session"


def journal_actions(payload: dict[str, Any], directory: Path = ACTION_JOURNAL_DIR) -> None:
    """Append this cycle's actions to the day's journal (actions_<YYYYMMDD>.jsonl). The *_latest files keep only the
    last cycle, so a position closed mid-session by a stop or take-profit was missing from the day's report
    (ASND, 2026-10-15). Logging only: nothing here decides or sends anything."""
    actions = payload.get("actions") or []
    if not actions:
        return
    directory.mkdir(parents=True, exist_ok=True)
    stamp = str(payload.get("run_timestamp") or datetime.now().astimezone().isoformat())
    path = directory / f"actions_{stamp[:10].replace('-', '')}.jsonl"
    with path.open("a", encoding="utf-8") as handle:
        for action in actions:
            handle.write(json.dumps({"agent": payload.get("agent_name"), "cycle_at": stamp, **action},
                                    ensure_ascii=False) + "\n")


def write_payload(out_prefix: Path, payload: dict[str, Any]) -> None:
    out_json = Path(f"{out_prefix}_latest.json")
    out_md = Path(f"{out_prefix}_latest.md")
    out_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    out_md.write_text(render_markdown_report(payload), encoding="utf-8")
    journal_actions(payload)


def run(args: argparse.Namespace) -> int:
    load_dotenv(Path(".env"))
    out_prefix = Path(args.out_prefix)
    while True:
        payload = monitor_once(args)
        write_payload(out_prefix, payload)
        print(
            f"[MONITOR] agent={payload['agent_name']} session={payload['session']} "
            f"actions={payload['actions_count']} holds={payload['holds_count']} remaining={payload['remaining_active_count']}"
        )
        if args.once:
            return 0
        if payload.get("all_done") and (payload.get("force_flat_now") or payload.get("session") == "closed"):
            return 0
        time.sleep(max(15, int(args.poll_seconds)))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Monitor agent-owned Alpaca paper positions and flatten them today.")
    parser.add_argument("--open-report", required=True, help="Path to the agent paper opening report JSON.")
    parser.add_argument("--strategy", required=True, help="Path to the agent current strategy JSON.")
    parser.add_argument("--out-prefix", required=True, help="Output prefix for monitor reports.")
    parser.add_argument("--agent-name", help="Optional agent-name override.")
    parser.add_argument("--poll-seconds", type=int, default=120, help="Seconds between monitoring cycles.")
    parser.add_argument("--stop-loss-pct", type=float, default=0.03, help="Close if unrealized P/L falls below this negative threshold.")
    parser.add_argument("--take-profit-pct", type=float, default=0.05, help="Close if unrealized P/L reaches this threshold.")
    parser.add_argument("--exit-score-floor", type=float, default=4.75, help="Close if current signal score falls below this level.")
    parser.add_argument("--close-on-watch", action="store_true", help="Also close if the trader downgrades a name to watch.")
    parser.add_argument("--today-only-policy", action="store_true", help="Force flat all monitored exposures before the EOD cutoff.")
    parser.add_argument("--eod-cutoff", default="15:55", help="US/Eastern cutoff used by the today-only flattening policy.")
    parser.add_argument("--post-action-refresh-seconds", type=float, default=2.0, help="Seconds to wait before refreshing a submitted close/cancel order for the report.")
    parser.add_argument("--post-action-refresh-attempts", type=int, default=10, help="How many times to refresh a submitted close/cancel order for the report.")
    parser.add_argument("--once", action="store_true", help="Run one monitoring cycle only.")
    parser.add_argument("--submit", action="store_true", help="Actually send cancel/close requests to Alpaca paper.")
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return run(args)


if __name__ == "__main__":
    raise SystemExit(main())
