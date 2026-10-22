from __future__ import annotations

import argparse
import json
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import agent_methodology
import agent_personality
from zoneinfo import ZoneInfo

from alpaca_paper_trader import (
    current_session_label,
    entry_confirmation,
    load_dotenv,
    load_json,
    normalize_agent_slug,
    now_athens,
    submit_paper_order,
    to_float,
)
from thymios_order_intent import intents_for_agent, load_approved_intents


DEFAULT_TRADER_REPORTS = {
    "yankos": Path("output/yankos_paper_trades_latest.json"),
    "periklis": Path("output/periklis_paper_trades_latest.json"),
}
ATHENS_TZ = ZoneInfo("Europe/Athens")
EASTERN_TZ = ZoneInfo("America/New_York")


def candidate_key(agent_name: str, opening: dict[str, Any], session_date: str | None = None) -> str:
    ticker = str(opening.get("ticker") or "").upper().strip()
    playbook = str(opening.get("playbook_name") or "").lower().strip()
    direction = str(opening.get("trade_direction") or "long").lower().strip()
    strategy_run = str(opening.get("strategy_run_timestamp") or "").strip()
    day_key = strategy_run[:10] if strategy_run else (session_date or now_athens().date().isoformat())
    return f"{day_key}|{agent_name}|{ticker}|{playbook}|{direction}"


def load_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"submitted_keys": [], "runs": []}
    try:
        payload = load_json(path)
    except Exception:
        return {"submitted_keys": [], "runs": []}
    if not isinstance(payload, dict):
        return {"submitted_keys": [], "runs": []}
    payload.setdefault("submitted_keys", [])
    payload.setdefault("runs", [])
    return payload


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def parse_hhmm(value: str) -> tuple[int, int]:
    hour, minute = str(value).split(":", 1)
    return int(hour), int(minute)


def load_trading_window(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {
            "timezone": "Europe/Athens",
            "canonical_market_timezone": "America/New_York",
            "entry_allowed_from_eastern": "09:30",
            "entry_allowed_until_eastern": "15:00",
            "entry_allowed_from_athens": "16:30",
            "entry_allowed_until_athens": "22:00",
        }
    return load_json(path)


def entry_window_open(
    config: dict[str, Any],
    current_time: datetime | None = None,
) -> tuple[bool, str]:
    now = current_time or datetime.now(ATHENS_TZ)
    if now.tzinfo is None:
        now = now.replace(tzinfo=ATHENS_TZ)
    if config.get("entry_allowed_from_eastern") and config.get("entry_allowed_until_eastern"):
        eastern_now = now.astimezone(EASTERN_TZ)
        start_raw = str(config["entry_allowed_from_eastern"])
        end_raw = str(config["entry_allowed_until_eastern"])
        start_h, start_m = parse_hhmm(start_raw)
        end_h, end_m = parse_hhmm(end_raw)
        start = eastern_now.replace(hour=start_h, minute=start_m, second=0, microsecond=0)
        end = eastern_now.replace(hour=end_h, minute=end_m, second=0, microsecond=0)
        allowed = eastern_now.weekday() < 5 and start <= eastern_now <= end
        start_athens = start.astimezone(ATHENS_TZ).strftime("%H:%M")
        end_athens = end.astimezone(ATHENS_TZ).strftime("%H:%M")
        reason = (
            f"Entry window is open ({start_raw}-{end_raw} America/New_York; "
            f"{start_athens}-{end_athens} Athens today)."
            if allowed
            else f"Entry window is closed ({start_raw}-{end_raw} America/New_York; "
            f"now {eastern_now.strftime('%a %H:%M %Z')})."
        )
        return allowed, reason

    now = now.astimezone(ATHENS_TZ)
    start_raw = str(config.get("entry_allowed_from_athens") or "16:30")
    end_raw = str(config.get("entry_allowed_until_athens") or "22:00")
    start_h, start_m = parse_hhmm(start_raw)
    end_h, end_m = parse_hhmm(end_raw)
    start = now.replace(hour=start_h, minute=start_m, second=0, microsecond=0)
    end = now.replace(hour=end_h, minute=end_m, second=0, microsecond=0)
    allowed = start <= now <= end
    reason = (
        f"Entry window is open ({start_raw}-{end_raw} Athens)."
        if allowed
        else f"Entry window is closed ({start_raw}-{end_raw} Athens, now {now.strftime('%H:%M')})."
    )
    return allowed, reason


def entry_window_finished(
    config: dict[str, Any],
    current_time: datetime | None = None,
) -> bool:
    now = current_time or datetime.now(ATHENS_TZ)
    if now.tzinfo is None:
        now = now.replace(tzinfo=ATHENS_TZ)
    if now.weekday() >= 5:
        return True
    if config.get("entry_allowed_until_eastern"):
        eastern_now = now.astimezone(EASTERN_TZ)
        end_h, end_m = parse_hhmm(str(config["entry_allowed_until_eastern"]))
        end = eastern_now.replace(hour=end_h, minute=end_m, second=0, microsecond=0)
        return eastern_now > end
    athens_now = now.astimezone(ATHENS_TZ)
    end_h, end_m = parse_hhmm(str(config.get("entry_allowed_until_athens") or "22:00"))
    end = athens_now.replace(hour=end_h, minute=end_m, second=0, microsecond=0)
    return athens_now > end


def submitted_key_set(state: dict[str, Any]) -> set[str]:
    return {str(item) for item in state.get("submitted_keys", []) if str(item).strip()}


def has_broker_order(opening: dict[str, Any]) -> bool:
    order = opening.get("order_response")
    if isinstance(order, dict) and str(order.get("id") or "").strip():
        return True
    status = str(opening.get("order_status") or "").lower().strip()
    return status not in {"", "preview", "setup_only", "waiting_confirmation"}


def row_from_opening(opening: dict[str, Any]) -> dict[str, Any]:
    row = dict(opening)
    row["signal_score"] = to_float(opening.get("signal_score")) or 0.0
    row["signal_verdict"] = opening.get("signal_verdict") or "candidate"
    row["trade_direction"] = str(opening.get("trade_direction") or "long").lower().strip() or "long"
    return row


def render_markdown(payload: dict[str, Any]) -> str:
    preview_mode = payload.get("execution_mode") == "preview_only"
    submitted_label = "Confirmed previews" if preview_mode else "Submitted orders"
    submitted_section = "## Confirmed Preview" if preview_mode else "## Submitted"
    lines = [
        "# Miltos Entry Stalker Report",
        "",
        f"- Run timestamp: {payload.get('run_timestamp')}",
        f"- Session: {payload.get('session')}",
        f"- Execution mode: {payload.get('execution_mode')}",
        f"- Timeframe: {payload.get('entry_timeframe')}",
        f"- Entry window open: {payload.get('entry_window_open')}",
        f"- Entry window reason: {payload.get('entry_window_reason')}",
        f"- Checked waiting setups: {payload.get('checked_count')}",
        f"- {submitted_label}: {payload.get('submitted_count')}",
        f"- Still waiting: {payload.get('still_waiting_count')}",
        f"- Skipped: {payload.get('skipped_count')}",
        f"- Failed: {payload.get('failed_count')}",
        "",
        submitted_section,
        "",
    ]
    submitted = payload.get("submitted") or []
    if submitted:
        for item in submitted:
            lines.append(
                f"- {item.get('agent_name')} | {item.get('ticker')} | direction={item.get('trade_direction')} | "
                f"status={item.get('order_status')}"
            )
            lines.append(f"  trigger: {item.get('entry_trigger_reason')}")
            if item.get("order_request"):
                lines.append(f"  request: {json.dumps(item.get('order_request'), ensure_ascii=False)}")
    else:
        lines.append("- None.")

    lines.extend(["", "## Still Waiting", ""])
    waiting = payload.get("still_waiting") or []
    if waiting:
        for item in waiting:
            lines.append(
                f"- {item.get('agent_name')} | {item.get('ticker')} | direction={item.get('trade_direction')} | "
                f"{item.get('entry_trigger_reason')}"
            )
    else:
        lines.append("- None.")

    lines.extend(["", "## Skipped", ""])
    skipped = payload.get("skipped") or []
    if skipped:
        for item in skipped:
            lines.append(f"- {item.get('agent_name')} | {item.get('ticker')} | reason={item.get('reason')}")
    else:
        lines.append("- None.")

    lines.extend(["", "## Failed", ""])
    failed = payload.get("failed") or []
    if failed:
        for item in failed:
            lines.append(f"- {item.get('agent_name')} | {item.get('ticker')} | error={item.get('error')}")
    else:
        lines.append("- None.")
    lines.append("")
    return "\n".join(lines)


def process_report(
    agent_name: str,
    report_path: Path,
    state: dict[str, Any],
    args: argparse.Namespace,
    entry_allowed: bool,
    entry_window_reason: str,
) -> tuple[dict[str, Any] | None, dict[str, list[dict[str, Any]]]]:
    if not report_path.exists():
        return None, {"submitted": [], "still_waiting": [], "skipped": [{"agent_name": agent_name, "ticker": "", "reason": f"missing_report={report_path}"}], "failed": []}

    payload = load_json(report_path)
    report_agent = str(payload.get("agent_name") or agent_name)
    trader_slug = normalize_agent_slug(report_agent)
    session_date = str(payload.get("run_timestamp") or now_athens().isoformat())[:10]
    submitted_keys = submitted_key_set(state)
    changed = False
    result = {"submitted": [], "still_waiting": [], "skipped": [], "failed": []}

    for opening in payload.get("openings", []):
        if not isinstance(opening, dict):
            continue
        ticker = str(opening.get("ticker") or "").upper().strip()
        if not ticker:
            continue
        key = candidate_key(report_agent, opening, session_date)
        if has_broker_order(opening):
            result["skipped"].append({"agent_name": report_agent, "ticker": ticker, "reason": "already_has_broker_order"})
            continue
        if key in submitted_keys:
            result["skipped"].append({"agent_name": report_agent, "ticker": ticker, "reason": "duplicate_blocked_by_miltos_state"})
            continue
        if not entry_allowed:
            result["skipped"].append({"agent_name": report_agent, "ticker": ticker, "reason": entry_window_reason})
            continue
        if str(opening.get("order_status") or "").lower().strip() != "waiting_confirmation":
            result["skipped"].append({"agent_name": report_agent, "ticker": ticker, "reason": f"order_status={opening.get('order_status')}"})
            continue
        approved_intent_id = str(opening.get("approved_intent_id") or "").strip()
        approved_ids = getattr(args, "approved_intent_ids", {}).get(trader_slug, set())
        if args.submit and approved_intent_id not in approved_ids:
            result["skipped"].append(
                {"agent_name": report_agent, "ticker": ticker, "reason": "intent_not_approved_by_koulis_thymios"}
            )
            continue

        try:
            confirmation = entry_confirmation(
                ticker,
                str(opening.get("trade_direction") or "long"),
                args.entry_timeframe,
            )
        except Exception as exc:
            reason = f"Entry confirmation check failed; will retry next cycle: {exc}"
            confirmation = {
                "entry_status": "waiting_confirmation",
                "entry_trigger_confirmed": False,
                "entry_trigger_reason": reason,
                "entry_timeframe": args.entry_timeframe,
            }
            if args.submit:
                opening.update(confirmation)
                opening["order_status"] = "waiting_confirmation"
                changed = True
            result["still_waiting"].append(
                {
                    "agent_name": report_agent,
                    "ticker": ticker,
                    "trade_direction": opening.get("trade_direction"),
                    "entry_trigger_reason": reason,
                }
            )
            continue
        reviewed_opening = dict(opening)
        reviewed_opening.update(confirmation)
        if not bool(confirmation.get("entry_trigger_confirmed")):
            if args.submit:
                opening.update(confirmation)
                opening["order_status"] = "waiting_confirmation"
                changed = True
            result["still_waiting"].append(
                {
                    "agent_name": report_agent,
                    "ticker": ticker,
                    "trade_direction": reviewed_opening.get("trade_direction"),
                    "entry_trigger_reason": confirmation.get("entry_trigger_reason"),
                }
            )
            continue

        if not args.submit:
            result["submitted"].append(
                {
                    "agent_name": report_agent,
                    "ticker": ticker,
                    "trade_direction": reviewed_opening.get("trade_direction"),
                    "order_status": "preview",
                    "entry_trigger_reason": confirmation.get("entry_trigger_reason"),
                }
            )
            continue

        try:
            opening.update(confirmation)
            order_result = submit_paper_order(
                trader_slug,
                row_from_opening(opening),
                float(opening.get("approved_notional_usd") or args.notional_per_trade),
                approved_intent_id=approved_intent_id,
            )
            opening["order_status"] = str(order_result["response"].get("status") or "submitted")
            opening["client_order_id"] = order_result["response"].get("client_order_id")
            opening["order_response"] = order_result["response"]
            opening["initial_order_response"] = order_result.get("initial_response")
            opening["refreshed_order_response"] = order_result.get("refreshed_response")
            opening["order_refresh_attempts"] = order_result.get("refresh_attempts")
            opening["order_request"] = order_result["request"]
            opening["asset_meta"] = order_result.get("asset", {})
            opening["sizing_mode"] = order_result.get("sizing_mode")
            opening["estimated_price"] = order_result.get("estimated_price")
            opening["miltos_activated_at"] = now_athens().isoformat()
            opening["miltos_candidate_key"] = key
            state.setdefault("submitted_keys", []).append(key)
            result["submitted"].append(
                {
                    "agent_name": report_agent,
                    "ticker": ticker,
                    "trade_direction": opening.get("trade_direction"),
                    "order_status": opening.get("order_status"),
                    "entry_trigger_reason": confirmation.get("entry_trigger_reason"),
                    "order_request": opening.get("order_request"),
                }
            )
            changed = True
        except Exception as exc:
            opening["order_status"] = "submit_failed"
            opening["submission_error"] = str(exc)
            result["failed"].append({"agent_name": report_agent, "ticker": ticker, "error": str(exc)})
            changed = True

    if changed:
        payload["run_timestamp"] = now_athens().isoformat()
        payload["miltos_last_checked_at"] = now_athens().isoformat()
        payload["submitted_count"] = sum(1 for item in payload.get("openings", []) if isinstance(item, dict) and item.get("order_response"))
        payload["failed_count"] = sum(1 for item in payload.get("openings", []) if isinstance(item, dict) and item.get("order_status") == "submit_failed")
        payload["confirmation_blocked_count"] = sum(1 for item in payload.get("openings", []) if isinstance(item, dict) and item.get("order_status") == "waiting_confirmation")
        write_json(report_path, payload)
        md_path = Path(str(report_path).replace("_latest.json", "_latest.md"))
        if md_path.exists():
            from alpaca_paper_trader import render_markdown_report

            md_path.write_text(render_markdown_report(payload), encoding="utf-8")
    return payload, result


def run_once(args: argparse.Namespace, state: dict[str, Any]) -> dict[str, Any]:
    reports = {
        "yankos": Path(args.yankos_report),
        "periklis": Path(args.periklis_report),
    }
    trading_window = load_trading_window(Path(args.trading_window_config))
    entry_allowed, entry_window_reason = entry_window_open(trading_window)
    entry_finished = entry_window_finished(trading_window)
    aggregate = {"submitted": [], "still_waiting": [], "skipped": [], "failed": []}
    checked_count = 0
    for agent_name, report_path in reports.items():
        _, result = process_report(agent_name, report_path, state, args, entry_allowed, entry_window_reason)
        for key in aggregate:
            aggregate[key].extend(result.get(key, []))
        checked_count += len(result.get("submitted", [])) + len(result.get("still_waiting", [])) + len(result.get("failed", []))

    payload = {
        "run_id": getattr(args, "ecosystem_run_id", None),
        "run_timestamp": now_athens().isoformat(),
        "agent_name": "miltos",
        "methodology": agent_methodology.methodology_identity("miltos"),
        "personality": agent_personality.personality_identity("miltos"),
        "session": current_session_label(),
        "execution_mode": "paper_submit" if args.submit else "preview_only",
        "entry_timeframe": args.entry_timeframe,
        "trading_window": trading_window,
        "entry_window_open": entry_allowed,
        "entry_window_finished": entry_finished,
        "entry_window_reason": entry_window_reason,
        "checked_count": checked_count,
        "submitted_count": len(aggregate["submitted"]),
        "still_waiting_count": len(aggregate["still_waiting"]),
        "skipped_count": len(aggregate["skipped"]),
        "failed_count": len(aggregate["failed"]),
        **aggregate,
    }
    state.setdefault("runs", []).append(
        {
            "run_timestamp": payload["run_timestamp"],
            "submitted_count": payload["submitted_count"],
            "still_waiting_count": payload["still_waiting_count"],
            "failed_count": payload["failed_count"],
        }
    )
    return payload


def write_report(out_prefix: Path, payload: dict[str, Any]) -> None:
    out_json = Path(f"{out_prefix}_latest.json")
    out_md = Path(f"{out_prefix}_latest.md")
    write_json(out_json, payload)
    out_md.write_text(render_markdown(payload), encoding="utf-8")
    print(f"[OUTPUT] JSON: {out_json}")
    print(f"[OUTPUT] MD: {out_md}")


def build_noop_report(run_id: str, reason: str, *, trading_window_config: str = "trading_window_config.json") -> dict[str, Any]:
    """Emit a current-run Miltos artifact when no entry-monitor process is required."""

    if not str(run_id).strip():
        raise ValueError("A no-op Miltos report requires an explicit run_id.")
    now = now_athens()
    trading_window = load_trading_window(Path(trading_window_config))
    entry_allowed, entry_reason = entry_window_open(trading_window, current_time=now)
    return {
        "schema": "ecosystem.miltos_entry_stalker.v1",
        "run_id": str(run_id).strip(),
        "run_timestamp": now.isoformat(),
        "agent_name": "miltos",
        "methodology": agent_methodology.methodology_identity("miltos"),
        "personality": agent_personality.personality_identity("miltos"),
        "status": "completed_noop",
        "completion_reason": str(reason).strip() or "no_entry_monitor_required",
        "session": current_session_label(),
        "execution_mode": "paper_noop",
        "entry_timeframe": None,
        "trading_window": trading_window,
        "entry_window_open": entry_allowed,
        "entry_window_finished": entry_window_finished(trading_window, current_time=now),
        "entry_window_reason": entry_reason,
        "checked_count": 0,
        "submitted_count": 0,
        "still_waiting_count": 0,
        "skipped_count": 0,
        "failed_count": 0,
        "submitted": [],
        "still_waiting": [],
        "skipped": [],
        "failed": [],
    }


def run(args: argparse.Namespace) -> int:
    load_dotenv(Path(".env"))
    if args.no_op_reason:
        payload = build_noop_report(
            args.no_op_run_id,
            args.no_op_reason,
            trading_window_config=args.trading_window_config,
        )
        write_report(Path(args.out_prefix), payload)
        print(f"[MILTOS] status=completed_noop reason={payload['completion_reason']}")
        return 0
    if args.submit:
        if not args.approved_intents:
            raise SystemExit("Miltos paper submission requires --approved-intents.")
        approved_payload = load_approved_intents(Path(args.approved_intents))
        args.ecosystem_run_id = approved_payload.get("run_id")
        args.approved_intent_ids = {
            agent: {item["intent_id"] for item in intents_for_agent(approved_payload, agent).values()}
            for agent in ("yankos", "periklis")
        }
    else:
        args.approved_intent_ids = {}
        args.ecosystem_run_id = None
    state_path = Path(args.state_file)
    state = load_state(state_path)
    last_payload: dict[str, Any] | None = None

    cycles = 1 if args.once else max(1, args.max_cycles)
    for index in range(cycles):
        last_payload = run_once(args, state)
        write_report(Path(args.out_prefix), last_payload)
        write_json(state_path, state)
        print(
            f"[MILTOS] cycle={index + 1}/{cycles} submitted={last_payload['submitted_count']} "
            f"waiting={last_payload['still_waiting_count']} failed={last_payload['failed_count']}"
        )
        if last_payload.get("entry_window_finished"):
            print("[MILTOS] Entry window finished; stopping the entry stalker.")
            break
        if args.once or index == cycles - 1:
            break
        time.sleep(max(60, int(args.poll_seconds)))

    return 1 if last_payload and last_payload.get("failed_count", 0) else 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Miltos entry stalker for waiting trader setups.")
    parser.add_argument("--yankos-report", default="output/yankos_paper_trades_latest.json")
    parser.add_argument("--periklis-report", default="output/periklis_paper_trades_latest.json")
    parser.add_argument("--out-prefix", default="output/miltos_entry_stalker")
    parser.add_argument("--state-file", default="output/miltos_entry_stalker_state.json")
    parser.add_argument("--trading-window-config", default="trading_window_config.json")
    parser.add_argument("--entry-timeframe", default="15Min")
    parser.add_argument("--notional-per-trade", type=float, default=250.0)
    parser.add_argument("--poll-seconds", type=int, default=300)
    parser.add_argument("--max-cycles", type=int, default=84)
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--submit", action="store_true", help="Submit paper orders when waiting setups become confirmed.")
    parser.add_argument("--approved-intents", help="Koulis-approved order-intent JSON; required with --submit.")
    parser.add_argument("--no-op-run-id", default="", help="Write a no-op report for this ecosystem run.")
    parser.add_argument("--no-op-reason", default="", help="Why no entry-monitor process is required.")
    return parser


def main() -> int:
    return run(build_parser().parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
