from __future__ import annotations

import argparse
import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib import error as urlerror
from urllib import parse as urlparse
from urllib import request as urlrequest
from zoneinfo import ZoneInfo
from common import load_json


PAPER_BASE_URL = "https://paper-api.alpaca.markets"
ATHENS_TZ = ZoneInfo("Europe/Athens")


def load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip("'").strip('"')
        if key:
            os.environ.setdefault(key, value)


def to_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    if numeric != numeric:
        return None
    return numeric


def now_athens() -> datetime:
    return datetime.now(ATHENS_TZ)


def alpaca_headers() -> dict[str, str]:
    api_key = os.getenv("ALPACA_API_KEY_ID", "").strip()
    api_secret = os.getenv("ALPACA_API_SECRET_KEY", "").strip()
    if not api_key or not api_secret:
        raise SystemExit("Missing Alpaca API credentials in environment.")
    return {
        "APCA-API-KEY-ID": api_key,
        "APCA-API-SECRET-KEY": api_secret,
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": "alpaca-paper-exit-manager/1.0",
    }


def alpaca_request(method: str, path: str, body: dict[str, Any] | None = None) -> Any:
    url = urlparse.urljoin(PAPER_BASE_URL, path)
    payload = None if body is None else json.dumps(body).encode("utf-8")
    req = urlrequest.Request(url, data=payload, method=method.upper(), headers=alpaca_headers())
    try:
        with urlrequest.urlopen(req, timeout=45) as response:
            raw = response.read().decode("utf-8")
    except urlerror.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Alpaca HTTP {exc.code}: {detail}") from exc
    except urlerror.URLError as exc:
        raise RuntimeError(f"Alpaca connection error: {exc}") from exc
    if not raw:
        return {}
    return json.loads(raw)


def fetch_order_by_id(order_id: str) -> dict[str, Any]:
    return alpaca_request("GET", f"/v2/orders/{order_id}")


def close_position_qty(symbol: str, qty: str) -> dict[str, Any]:
    encoded_symbol = urlparse.quote(symbol)
    path = f"/v2/positions/{encoded_symbol}?qty={urlparse.quote(str(qty))}"
    return alpaca_request("DELETE", path)


def best_current_row(strategy_payload: dict[str, Any], symbol: str) -> dict[str, Any] | None:
    candidates: list[dict[str, Any]] = []
    for row in strategy_payload.get("results", []):
        if not isinstance(row, dict):
            continue
        if str(row.get("ticker") or "").upper().strip() != symbol:
            continue
        candidates.append(row)
    if not candidates:
        return None
    return max(candidates, key=lambda item: to_float(item.get("signal_score")) or 0.0)


def is_open_like_status(status: str) -> bool:
    normalized = str(status or "").strip().lower()
    return normalized in {
        "new",
        "accepted",
        "pending_new",
        "partially_filled",
        "filled",
        "done_for_day",
    }


def active_openings(open_report: dict[str, Any]) -> list[dict[str, Any]]:
    active: list[dict[str, Any]] = []
    for item in open_report.get("openings", []):
        if not isinstance(item, dict):
            continue
        order = item.get("order_response") or {}
        order_id = str(order.get("id") or "").strip()
        if not order_id:
            continue
        try:
            fresh_order = fetch_order_by_id(order_id)
        except Exception:
            fresh_order = order
        status = str(fresh_order.get("status") or item.get("order_status") or "")
        qty = str(fresh_order.get("filled_qty") or fresh_order.get("qty") or order.get("qty") or "").strip()
        if not qty:
            continue
        if not is_open_like_status(status):
            continue
        active.append(
            {
                **item,
                "trade_direction": str(item.get("trade_direction") or "long"),
                "live_order_status": status,
                "live_qty": qty,
                "live_filled_avg_price": fresh_order.get("filled_avg_price"),
                "live_order_id": fresh_order.get("id") or order_id,
                "live_updated_at": fresh_order.get("updated_at") or fresh_order.get("submitted_at"),
            }
        )
    return active


def exit_decision_reason(
    agent_name: str,
    opening: dict[str, Any],
    current_row: dict[str, Any] | None,
    exit_score_floor: float,
    close_on_watch: bool,
) -> tuple[bool, str]:
    symbol = str(opening.get("ticker") or "").upper()
    if current_row is None:
        return True, f"{agent_name} no longer carries {symbol} on the current board."

    verdict = str(current_row.get("signal_verdict") or "").lower().strip()
    current_direction = str(current_row.get("trade_direction") or "flat").lower().strip()
    opening_direction = str(opening.get("trade_direction") or "long").lower().strip()
    score = to_float(current_row.get("signal_score")) or 0.0
    timing = str(current_row.get("technical_timing") or "unknown")
    risk_level = str(current_row.get("risk_level") or "Medium")

    if verdict == "reject":
        return True, f"{agent_name} now rejects {symbol}, so the original setup is considered invalid."
    if current_direction not in {opening_direction, "flat"}:
        return True, f"{agent_name} flipped direction on {symbol} from {opening_direction} to {current_direction}."
    if close_on_watch and verdict == "watch":
        return True, f"{agent_name} downgraded {symbol} from candidate to watch."
    if score < exit_score_floor:
        return True, f"{agent_name} score for {symbol} fell below the exit floor ({score:.2f} < {exit_score_floor:.2f})."
    if risk_level == "High" and verdict != "candidate":
        return True, f"{agent_name} now sees {symbol} as too risky without active candidate conviction."
    if opening_direction == "long" and timing == "already_extended" and verdict != "candidate":
        return True, f"{agent_name} sees {symbol} as already extended, so the original entry edge has likely matured."
    if opening_direction == "short" and current_direction == "long":
        return True, f"{agent_name} no longer sees {symbol} as a short and now leans long."

    return False, f"{agent_name} still supports holding {symbol} for now."


def exit_justification(
    agent_name: str,
    opening: dict[str, Any],
    current_row: dict[str, Any] | None,
    decision_reason: str,
) -> str:
    symbol = str(opening.get("ticker") or "").upper()
    open_playbook = str(opening.get("playbook_name") or "unknown_playbook")
    open_direction = str(opening.get("trade_direction") or "long")
    if current_row is None:
        return (
            f"{agent_name} closes the {open_direction} position in {symbol} because it is no longer present on the "
            f"current board after opening it via {open_playbook}."
        )
    current_playbook = str(current_row.get("playbook_name") or "current_playbook")
    current_score = to_float(current_row.get("signal_score")) or 0.0
    current_verdict = str(current_row.get("signal_verdict") or "unknown")
    current_direction = str(current_row.get("trade_direction") or "flat")
    rationale = current_row.get("rationale") or []
    if not isinstance(rationale, list):
        rationale = [str(rationale)]
    top_points = "; ".join(str(x).strip().rstrip(".") for x in rationale[:3] if str(x).strip())
    return (
        f"{agent_name} closes the {open_direction} position in {symbol} after re-evaluating the position from "
        f"{open_playbook} against the current {current_playbook} read "
        f"(verdict={current_verdict}, direction={current_direction}, score={current_score:.2f}). "
        f"{decision_reason} Current exit logic: {top_points or 'No extra rationale available'}."
    )


def render_markdown_report(payload: dict[str, Any]) -> str:
    lines = [
        f"# {payload.get('agent_name')} Paper Exit Report",
        "",
        f"- Run timestamp: {payload.get('run_timestamp')}",
        f"- Execution mode: {payload.get('execution_mode')}",
        f"- Openings reviewed: {payload.get('reviewed_openings')}",
        f"- Exit candidates: {payload.get('exit_candidates_count')}",
        f"- Submitted exits: {payload.get('submitted_exits')}",
        f"- Failed exits: {payload.get('failed_exits')}",
        "",
        "## Exit Decisions",
        "",
    ]

    exits = payload.get("exit_candidates") or []
    if exits:
        for item in exits:
            lines.append(
                f"- {item.get('ticker')} | direction={item.get('trade_direction')} | open_status={item.get('live_order_status')} | "
                f"current_verdict={item.get('current_verdict')} | close_status={item.get('close_status')}"
            )
            lines.append(f"  decision_reason: {item.get('decision_reason')}")
            lines.append(f"  justification: {item.get('exit_justification')}")
            if item.get("close_request"):
                lines.append(f"  close_request: {json.dumps(item.get('close_request'), ensure_ascii=False)}")
            if item.get("close_response"):
                response = item["close_response"]
                lines.append(
                    f"  alpaca_close_order: id={response.get('id')} status={response.get('status')} "
                    f"submitted_at={response.get('submitted_at')}"
                )
            if item.get("close_error"):
                lines.append(f"  close_error: {item.get('close_error')}")
        lines.append("")
    else:
        lines.extend(["- No exit candidates selected.", ""])

    holds = payload.get("holds") or []
    lines.extend(["## Holds", ""])
    if holds:
        for item in holds:
            lines.append(f"- {item.get('ticker')} | reason={item.get('decision_reason')}")
    else:
        lines.append("- No holds recorded.")
    lines.append("")
    return "\n".join(lines)


def run(args: argparse.Namespace) -> int:
    load_dotenv(Path(".env"))
    open_report = load_json(Path(args.open_report))
    strategy_payload = load_json(Path(args.strategy))
    agent_name = str(open_report.get("agent_name") or args.agent_name or "agent")

    reviewed = active_openings(open_report)
    exit_candidates: list[dict[str, Any]] = []
    holds: list[dict[str, Any]] = []
    submitted_exits = 0
    failed_exits = 0

    for opening in reviewed:
        symbol = str(opening.get("ticker") or "").upper()
        current_row = best_current_row(strategy_payload, symbol)
        should_close, decision_reason = exit_decision_reason(
            agent_name=agent_name,
            opening=opening,
            current_row=current_row,
            exit_score_floor=args.exit_score_floor,
            close_on_watch=args.close_on_watch,
        )
        current_verdict = None if current_row is None else current_row.get("signal_verdict")
        if not should_close:
            holds.append(
                {
                    "ticker": symbol,
                    "decision_reason": decision_reason,
                    "current_verdict": current_verdict,
                }
            )
            continue

        candidate = {
            "ticker": symbol,
            "open_playbook_name": opening.get("playbook_name"),
            "trade_direction": opening.get("trade_direction"),
            "live_order_status": opening.get("live_order_status"),
            "live_qty": opening.get("live_qty"),
            "current_verdict": current_verdict,
            "current_score": None if current_row is None else to_float(current_row.get("signal_score")),
            "decision_reason": decision_reason,
            "exit_justification": exit_justification(agent_name, opening, current_row, decision_reason),
            "close_status": "preview",
        }
        if args.submit:
            try:
                response = close_position_qty(symbol, str(opening.get("live_qty")))
                candidate["close_status"] = str(response.get("status") or "submitted")
                candidate["close_request"] = {"symbol": symbol, "qty": str(opening.get("live_qty"))}
                candidate["close_response"] = {
                    "id": response.get("id"),
                    "client_order_id": response.get("client_order_id"),
                    "status": response.get("status"),
                    "symbol": response.get("symbol"),
                    "qty": response.get("qty"),
                    "submitted_at": response.get("submitted_at"),
                    "filled_at": response.get("filled_at"),
                    "filled_avg_price": response.get("filled_avg_price"),
                }
                submitted_exits += 1
            except Exception as exc:
                candidate["close_status"] = "submit_failed"
                candidate["close_error"] = str(exc)
                failed_exits += 1
        exit_candidates.append(candidate)

    payload = {
        "run_timestamp": now_athens().isoformat(),
        "agent_name": agent_name,
        "execution_mode": "paper_close_submit" if args.submit else "paper_close_preview",
        "open_report": str(Path(args.open_report)),
        "strategy_file": str(Path(args.strategy)),
        "reviewed_openings": len(reviewed),
        "exit_candidates_count": len(exit_candidates),
        "submitted_exits": submitted_exits,
        "failed_exits": failed_exits,
        "exit_score_floor": args.exit_score_floor,
        "close_on_watch": args.close_on_watch,
        "exit_candidates": exit_candidates,
        "holds": holds,
    }

    out_prefix = Path(args.out_prefix)
    out_json = Path(f"{out_prefix}_latest.json")
    out_md = Path(f"{out_prefix}_latest.md")
    out_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    out_md.write_text(render_markdown_report(payload), encoding="utf-8")
    print(f"[OUTPUT] JSON: {out_json}")
    print(f"[OUTPUT] MD: {out_md}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Review and close agent-owned Alpaca paper positions.")
    parser.add_argument("--open-report", required=True, help="Path to the agent's paper opening report JSON.")
    parser.add_argument("--strategy", required=True, help="Path to the current strategy latest JSON output.")
    parser.add_argument("--out-prefix", required=True, help="Output prefix for exit reports.")
    parser.add_argument("--agent-name", help="Optional agent-name override.")
    parser.add_argument("--exit-score-floor", type=float, default=4.75, help="Close if current score falls below this level.")
    parser.add_argument("--close-on-watch", action="store_true", help="Also close when a former candidate is downgraded to watch.")
    parser.add_argument("--submit", action="store_true", help="Actually submit position-closing orders to Alpaca paper.")
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return run(args)


if __name__ == "__main__":
    raise SystemExit(main())
