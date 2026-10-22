from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from alpaca_account_preflight import fetch_snapshot
from alpaca_paper_exit_manager import load_dotenv, now_athens
from alpaca_paper_trader import compact_order_payload, fetch_order_by_id
from common import load_json


def collect_owned_orders(report_path: Path, agent_name: str) -> list[dict[str, Any]]:
    if not report_path.exists():
        return []
    payload = load_json(report_path)
    rows: list[dict[str, Any]] = []
    for opening in payload.get("openings", []):
        if not isinstance(opening, dict):
            continue
        order = opening.get("order_response") or {}
        order_id = str(order.get("id") or "").strip()
        client_order_id = str(opening.get("client_order_id") or order.get("client_order_id") or "").strip()
        if not order_id and not client_order_id:
            continue
        symbol = str(opening.get("ticker") or order.get("symbol") or "").upper().strip()
        latest_order = compact_order_payload(order) if isinstance(order, dict) else {}
        refresh_error = None
        if order_id:
            try:
                latest_order = compact_order_payload(fetch_order_by_id(order_id))
            except Exception as exc:
                refresh_error = str(exc)
        rows.append(
            {
                "agent_name": agent_name,
                "report": str(report_path),
                "symbol": symbol,
                "trade_direction": opening.get("trade_direction"),
                "client_order_id": client_order_id,
                "order_id": order_id,
                "report_order_status": opening.get("order_status"),
                "latest_order_status": latest_order.get("status"),
                "latest_order": latest_order,
                "refresh_error": refresh_error,
            }
        )
    return rows


def render_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# Alpaca Trader-Owned Reconciliation",
        "",
        f"- Run timestamp: {payload.get('run_timestamp')}",
        f"- Session: {payload.get('session')}",
        f"- Trader-owned orders: {payload.get('owned_order_count')}",
        f"- Trader-owned live positions: {payload.get('owned_live_position_count')}",
        f"- Trader-owned open orders: {payload.get('owned_open_order_count')}",
        f"- External positions: {payload.get('external_position_count')}",
        f"- Open orders total: {payload.get('open_orders_total')}",
        "",
        "## Trader-Owned Orders",
        "",
    ]
    orders = payload.get("owned_orders") or []
    if orders:
        for item in orders:
            lines.append(
                f"- {item.get('agent_name')} | {item.get('symbol')} | direction={item.get('trade_direction')} | "
                f"report_status={item.get('report_order_status')} | latest_status={item.get('latest_order_status')} | "
                f"client={item.get('client_order_id')}"
            )
            if item.get("refresh_error"):
                lines.append(f"  refresh_error: {item.get('refresh_error')}")
    else:
        lines.append("- None.")

    lines.extend(["", "## Trader-Owned Live Positions", ""])
    positions = payload.get("owned_live_positions") or []
    if positions:
        for item in positions:
            lines.append(
                f"- {item.get('symbol')} | side={item.get('side')} | qty={item.get('qty')} | "
                f"plpc={item.get('unrealized_plpc')}"
            )
    else:
        lines.append("- None.")

    lines.extend(["", "## Trader-Owned Open Orders", ""])
    open_orders = payload.get("owned_open_orders") or []
    if open_orders:
        for item in open_orders:
            lines.append(
                f"- {item.get('symbol')} | side={item.get('side')} | status={item.get('status')} | "
                f"client={item.get('client_order_id')}"
            )
    else:
        lines.append("- None.")

    lines.extend(["", "## External Positions", ""])
    external = payload.get("external_positions") or []
    if external:
        for item in external:
            lines.append(
                f"- {item.get('symbol')} | side={item.get('side')} | qty={item.get('qty')} | "
                f"plpc={item.get('unrealized_plpc')}"
            )
    else:
        lines.append("- None.")
    lines.append("")
    return "\n".join(lines)


def run(args: argparse.Namespace) -> int:
    load_dotenv(Path(".env"))
    reports = {
        "yankos": Path(args.yankos_report),
        "periklis": Path(args.periklis_report),
    }
    owned_orders: list[dict[str, Any]] = []
    for agent_name, report_path in reports.items():
        owned_orders.extend(collect_owned_orders(report_path, agent_name))

    owned_symbols = {str(item.get("symbol") or "").upper() for item in owned_orders if item.get("symbol")}
    owned_clients = {str(item.get("client_order_id") or "") for item in owned_orders if item.get("client_order_id")}
    snapshot = fetch_snapshot()
    positions = snapshot.get("positions", [])
    open_orders = snapshot.get("open_orders", [])

    owned_live_positions = [
        item for item in positions if str(item.get("symbol") or "").upper() in owned_symbols
    ]
    external_positions = [
        item for item in positions if str(item.get("symbol") or "").upper() not in owned_symbols
    ]
    owned_open_orders = [
        item for item in open_orders if str(item.get("client_order_id") or "") in owned_clients
    ]

    payload = {
        "run_timestamp": now_athens().isoformat(),
        "session": args.session_label,
        "reports": {key: str(value) for key, value in reports.items()},
        "owned_order_count": len(owned_orders),
        "owned_live_position_count": len(owned_live_positions),
        "owned_open_order_count": len(owned_open_orders),
        "external_position_count": len(external_positions),
        "open_orders_total": len(open_orders),
        "owned_orders": owned_orders,
        "owned_live_positions": owned_live_positions,
        "owned_open_orders": owned_open_orders,
        "external_positions": external_positions,
        "snapshot": snapshot,
    }

    out_prefix = Path(args.out_prefix)
    out_json = Path(f"{out_prefix}_latest.json")
    out_md = Path(f"{out_prefix}_latest.md")
    out_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    out_md.write_text(render_markdown(payload), encoding="utf-8")
    print(f"[OUTPUT] JSON: {out_json}")
    print(f"[OUTPUT] MD: {out_md}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Reconcile trader-owned Alpaca orders against live paper account state.")
    parser.add_argument("--yankos-report", default="output/yankos_paper_trades_latest.json")
    parser.add_argument("--periklis-report", default="output/periklis_paper_trades_latest.json")
    parser.add_argument("--out-prefix", default="output/alpaca_order_reconciliation")
    parser.add_argument("--session-label", default="manual")
    return parser


def main() -> int:
    return run(build_parser().parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
