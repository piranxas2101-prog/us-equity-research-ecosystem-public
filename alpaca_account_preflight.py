from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any
from urllib import parse as urlparse

from alpaca_paper_exit_manager import alpaca_request, load_dotenv, now_athens
from alpaca_paper_trader import current_session_label


CANCELABLE_ORDER_STATUSES = {
    "new",
    "accepted",
    "pending_new",
    "partially_filled",
    "done_for_day",
}


def compact_position(position: dict[str, Any]) -> dict[str, Any]:
    return {
        "symbol": position.get("symbol"),
        "side": position.get("side"),
        "qty": position.get("qty"),
        "market_value": position.get("market_value"),
        "cost_basis": position.get("cost_basis"),
        "avg_entry_price": position.get("avg_entry_price"),
        "current_price": position.get("current_price"),
        "unrealized_pl": position.get("unrealized_pl"),
        "unrealized_plpc": position.get("unrealized_plpc"),
    }


def compact_order(order: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": order.get("id"),
        "client_order_id": order.get("client_order_id"),
        "symbol": order.get("symbol"),
        "side": order.get("side"),
        "type": order.get("type"),
        "status": order.get("status"),
        "qty": order.get("qty"),
        "filled_qty": order.get("filled_qty"),
        "submitted_at": order.get("submitted_at"),
        "updated_at": order.get("updated_at"),
    }


def fetch_snapshot() -> dict[str, Any]:
    account = alpaca_request("GET", "/v2/account")
    positions = alpaca_request("GET", "/v2/positions")
    open_orders = alpaca_request("GET", "/v2/orders?status=open&limit=100&direction=desc")
    if not isinstance(positions, list):
        positions = []
    if not isinstance(open_orders, list):
        open_orders = []
    return {
        "account": {
            "account_id": account.get("id"),
            "status": account.get("status"),
            "buying_power": account.get("buying_power"),
            "cash": account.get("cash"),
            "portfolio_value": account.get("portfolio_value"),
            "pattern_day_trader": account.get("pattern_day_trader"),
        },
        "positions": [compact_position(item) for item in positions if isinstance(item, dict)],
        "open_orders": [compact_order(item) for item in open_orders if isinstance(item, dict)],
    }


def cancel_order(order_id: str) -> dict[str, Any]:
    return alpaca_request("DELETE", f"/v2/orders/{urlparse.quote(order_id)}")


def close_position(symbol: str) -> dict[str, Any]:
    return alpaca_request("DELETE", f"/v2/positions/{urlparse.quote(symbol)}")


def flatten_snapshot(snapshot: dict[str, Any], dry_run: bool) -> list[dict[str, Any]]:
    actions: list[dict[str, Any]] = []
    for order in snapshot.get("open_orders", []):
        order_id = str(order.get("id") or "").strip()
        status = str(order.get("status") or "").lower().strip()
        if not order_id or status not in CANCELABLE_ORDER_STATUSES:
            continue
        action = {
            "type": "cancel_order",
            "symbol": order.get("symbol"),
            "order_id": order_id,
            "status_before": status,
            "submit_mode": "dry_run" if dry_run else "submitted",
        }
        if not dry_run:
            try:
                cancel_order(order_id)
                action["status_after"] = "cancel_requested"
            except Exception as exc:
                action["status_after"] = "cancel_failed"
                action["error"] = str(exc)
        actions.append(action)

    for position in snapshot.get("positions", []):
        symbol = str(position.get("symbol") or "").upper().strip()
        if not symbol:
            continue
        action = {
            "type": "close_position",
            "symbol": symbol,
            "side": position.get("side"),
            "qty": position.get("qty"),
            "unrealized_pl": position.get("unrealized_pl"),
            "unrealized_plpc": position.get("unrealized_plpc"),
            "submit_mode": "dry_run" if dry_run else "submitted",
        }
        if not dry_run:
            try:
                response = close_position(symbol)
                action["status_after"] = str(response.get("status") or "close_requested")
                action["response"] = compact_order(response) if isinstance(response, dict) else response
            except Exception as exc:
                action["status_after"] = "close_failed"
                action["error"] = str(exc)
        actions.append(action)
    return actions


def render_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# Alpaca Account Preflight",
        "",
        f"- Run timestamp: {payload.get('run_timestamp')}",
        f"- Session: {payload.get('session')}",
        f"- Mode: {payload.get('mode')}",
        f"- Positions before: {payload.get('positions_before_count')}",
        f"- Open orders before: {payload.get('open_orders_before_count')}",
        f"- Actions: {payload.get('actions_count')}",
        f"- Block trader run: {payload.get('block_trader_run')}",
        "",
        "## Account",
        "",
    ]
    account = payload.get("snapshot_before", {}).get("account", {})
    lines.extend(
        [
            f"- Status: {account.get('status')}",
            f"- Buying power: {account.get('buying_power')}",
            f"- Cash: {account.get('cash')}",
            f"- Portfolio value: {account.get('portfolio_value')}",
            "",
            "## Existing Positions",
            "",
        ]
    )
    positions = payload.get("snapshot_before", {}).get("positions", [])
    if positions:
        for item in positions:
            lines.append(
                f"- {item.get('symbol')} | side={item.get('side')} | qty={item.get('qty')} | "
                f"value={item.get('market_value')} | pl={item.get('unrealized_pl')} | plpc={item.get('unrealized_plpc')}"
            )
    else:
        lines.append("- None.")
    lines.extend(["", "## Open Orders", ""])
    orders = payload.get("snapshot_before", {}).get("open_orders", [])
    if orders:
        for item in orders:
            lines.append(
                f"- {item.get('symbol')} | side={item.get('side')} | status={item.get('status')} | "
                f"qty={item.get('qty')} | id={item.get('id')}"
            )
    else:
        lines.append("- None.")
    lines.extend(["", "## Actions", ""])
    actions = payload.get("actions", [])
    if actions:
        for item in actions:
            lines.append(
                f"- {item.get('type')} | symbol={item.get('symbol')} | mode={item.get('submit_mode')} | "
                f"status={item.get('status_after', 'preview')}"
            )
            if item.get("error"):
                lines.append(f"  error: {item.get('error')}")
    else:
        lines.append("- No actions.")
    lines.append("")
    return "\n".join(lines)


def run(args: argparse.Namespace) -> int:
    load_dotenv(Path(".env"))
    snapshot_before = fetch_snapshot()
    positions_before_count = len(snapshot_before.get("positions", []))
    open_orders_before_count = len(snapshot_before.get("open_orders", []))
    has_existing_exposure = positions_before_count > 0 or open_orders_before_count > 0

    mode = "report_only"
    actions: list[dict[str, Any]] = []
    if args.auto_flatten:
        mode = "auto_flatten_dry_run" if args.dry_run else "auto_flatten_submit"
        actions = flatten_snapshot(snapshot_before, dry_run=args.dry_run)
    elif args.block_on_existing:
        mode = "block_on_existing"

    action_failures = [item for item in actions if str(item.get("status_after") or "").endswith("_failed")]
    block_trader_run = bool(args.block_on_existing and has_existing_exposure and not args.auto_flatten)
    if args.auto_flatten and action_failures:
        block_trader_run = True

    payload = {
        "run_timestamp": now_athens().isoformat(),
        "session": current_session_label(),
        "mode": mode,
        "positions_before_count": positions_before_count,
        "open_orders_before_count": open_orders_before_count,
        "has_existing_exposure": has_existing_exposure,
        "actions_count": len(actions),
        "action_failures_count": len(action_failures),
        "block_trader_run": block_trader_run,
        "snapshot_before": snapshot_before,
        "actions": actions,
    }

    out_prefix = Path(args.out_prefix)
    out_json = Path(f"{out_prefix}_latest.json")
    out_md = Path(f"{out_prefix}_latest.md")
    out_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    out_md.write_text(render_markdown(payload), encoding="utf-8")
    print(f"[OUTPUT] JSON: {out_json}")
    print(f"[OUTPUT] MD: {out_md}")
    if block_trader_run:
        print("[PREFLIGHT] blocking trader run")
        return 2
    print("[PREFLIGHT] ok")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Preflight Alpaca paper account before autonomous trader runs.")
    parser.add_argument("--out-prefix", default="output/alpaca_preflight", help="Output prefix for preflight reports.")
    parser.add_argument("--report-only", action="store_true", help="Only report current positions/open orders without blocking or flattening.")
    parser.add_argument("--block-on-existing", action="store_true", help="Return non-zero if existing positions/open orders are present.")
    parser.add_argument("--auto-flatten", action="store_true", help="Cancel open orders and close all existing paper positions before trading.")
    parser.add_argument("--dry-run", action="store_true", help="Preview auto-flatten actions without submitting them.")
    return parser


def main() -> int:
    parser = build_parser()
    return run(parser.parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
