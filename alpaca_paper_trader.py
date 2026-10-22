from __future__ import annotations

import argparse
import json
import os
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib import error as urlerror
from urllib import parse as urlparse
from urllib import request as urlrequest
from zoneinfo import ZoneInfo

from primary_data_freshness import primary_data_fresh
from thymios_order_intent import intents_for_agent, load_approved_intents
from common import load_json


PAPER_BASE_URL = "https://paper-api.alpaca.markets"
MARKET_DATA_BASE_URL = "https://data.alpaca.markets"
ALPACA_HTTP_TIMEOUT_SECONDS = float(os.getenv("ALPACA_HTTP_TIMEOUT_SECONDS", "12"))
ATHENS_TZ = ZoneInfo("Europe/Athens")
EASTERN_TZ = ZoneInfo("America/New_York")
TERMINAL_ORDER_STATUSES = {
    "filled",
    "canceled",
    "expired",
    "rejected",
    "suspended",
    "stopped",
    "done_for_day",
}


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


def normalize_agent_slug(value: str) -> str:
    cleaned = re.sub(r"[^a-z0-9]+", "_", str(value or "").strip().lower()).strip("_")
    return cleaned or "agent"


def now_athens() -> datetime:
    return datetime.now(ATHENS_TZ)


def current_session_label() -> str:
    eastern_now = datetime.now(EASTERN_TZ)
    minutes = eastern_now.hour * 60 + eastern_now.minute
    if 4 * 60 <= minutes < 9 * 60 + 30:
        return "pre_market"
    if 9 * 60 + 30 <= minutes < 16 * 60:
        return "regular"
    if 16 * 60 <= minutes < 20 * 60:
        return "after_hours"
    return "closed"


def entry_justification(agent_name: str, row: dict[str, Any]) -> str:
    ticker = str(row.get("ticker") or "").upper()
    playbook = str(row.get("playbook_name") or "unknown_playbook")
    score = to_float(row.get("signal_score")) or 0.0
    timing = str(row.get("technical_timing") or "unknown_timing")
    direction = str(row.get("trade_direction") or "long").lower().strip() or "long"
    rationale = row.get("rationale") or []
    if not isinstance(rationale, list):
        rationale = [str(rationale)]
    top_points = [str(item).strip().rstrip(".") for item in rationale[:4] if str(item).strip()]
    joined = "; ".join(top_points) if top_points else "No detailed rationale available"
    return (
        f"{agent_name} opens a {direction} position in {ticker} via {playbook} "
        f"(score {score:.2f}, timing={timing}) because {joined}."
    )


def opening_key(item: dict[str, Any]) -> str:
    ticker = str(item.get("ticker") or "").upper().strip()
    playbook = str(item.get("playbook_name") or "").strip().lower()
    direction = str(item.get("trade_direction") or "long").strip().lower()
    return f"{ticker}|{playbook}|{direction}"


def excluded_tickers_from_report(path: Path) -> set[str]:
    if not path.exists():
        return set()
    try:
        payload = load_json(path)
    except Exception:
        return set()
    tickers: set[str] = set()
    for item in payload.get("openings", []):
        if not isinstance(item, dict):
            continue
        ticker = str(item.get("ticker") or "").upper().strip()
        if ticker:
            tickers.add(ticker)
    return tickers


def merge_openings(existing: list[dict[str, Any]], fresh: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    for item in existing:
        if not isinstance(item, dict):
            continue
        merged[opening_key(item)] = item
    for item in fresh:
        if not isinstance(item, dict):
            continue
        merged[opening_key(item)] = item
    return list(merged.values())


def choose_trade_rows(
    payload: dict[str, Any],
    max_orders: int,
    min_score: float,
    excluded_tickers: set[str] | None = None,
    precheck_shortable: bool = True,
    approved_intents: dict[tuple[str, str, str], dict[str, Any]] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    best_by_ticker: dict[str, dict[str, Any]] = {}
    skipped: list[dict[str, Any]] = []
    excluded_tickers = excluded_tickers or set()
    asset_cache: dict[str, dict[str, Any]] = {}

    for raw in payload.get("results", []):
        if not isinstance(raw, dict):
            continue
        ticker = str(raw.get("ticker") or "").upper().strip()
        if not ticker:
            continue
        if ticker in excluded_tickers:
            skipped.append({"ticker": ticker, "reason": "already_traded_today", "signal_score": to_float(raw.get("signal_score")) or 0.0})
            continue
        score = to_float(raw.get("signal_score")) or 0.0
        verdict = str(raw.get("signal_verdict") or "").lower().strip()
        trade_direction = str(raw.get("trade_direction") or "long").lower().strip() or "long"
        playbook_name = str(raw.get("playbook_name") or "").lower().strip()
        risk_level = str(raw.get("risk_level") or "Medium")
        instrument_type = str(raw.get("instrument_type") or "common")
        fresh_primary_data = primary_data_fresh(raw)

        reason: str | None = None
        intent = None
        if approved_intents is not None:
            intent = approved_intents.get((ticker, trade_direction, playbook_name))
            if intent is None:
                reason = "not_approved_by_koulis_thymios"
        if reason is None and raw.get("execution_eligible") is not True:
            reason = "fundamental_thesis_not_execution_ready"
        elif reason is None and str(raw.get("fundamental_gate_status") or "") != "pass":
            reason = "fundamental_gate_not_passed"
        elif reason is None and not str(raw.get("fundamental_thesis_hash") or "").strip():
            reason = "missing_fundamental_thesis_identity"
        elif reason is None and verdict != "candidate":
            reason = f"verdict={verdict}"
        elif reason is None and instrument_type != "common":
            reason = f"instrument_type={instrument_type}"
        elif reason is None and risk_level == "High":
            reason = "high_risk"
        elif reason is None and not fresh_primary_data:
            reason = "stale_primary_data"
        elif reason is None and score < min_score:
            reason = f"score_below_{min_score:.2f}"
        elif reason is None and precheck_shortable and trade_direction == "short":
            try:
                asset = asset_cache.get(ticker)
                if asset is None:
                    asset = fetch_asset(ticker)
                    asset_cache[ticker] = asset
                if not bool(asset.get("shortable", False)):
                    reason = "not_shortable_on_alpaca"
                elif not bool(asset.get("tradable", False)):
                    reason = "not_tradable_on_alpaca"
            except Exception as exc:
                reason = f"shortable_check_failed={exc}"

        if reason is not None:
            skipped.append({"ticker": ticker, "reason": reason, "signal_score": score})
            continue

        existing = best_by_ticker.get(ticker)
        if existing is None or score > (to_float(existing.get("signal_score")) or 0.0):
            row = dict(raw)
            if intent is not None:
                row["approved_intent_id"] = intent["intent_id"]
                row["approved_notional_usd"] = float(intent["notional_usd"])
                row["approved_trade_plan"] = intent.get("trade_plan")
            if ticker in asset_cache:
                asset = asset_cache[ticker]
                row["pretrade_asset_meta"] = {
                    "tradable": asset.get("tradable"),
                    "shortable": asset.get("shortable"),
                    "easy_to_borrow": asset.get("easy_to_borrow"),
                    "exchange": asset.get("exchange"),
                }
            best_by_ticker[ticker] = row

    selected = sorted(
        best_by_ticker.values(),
        key=lambda item: to_float(item.get("signal_score")) or 0.0,
        reverse=True,
    )[: max(0, max_orders)]
    return selected, skipped


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
        "User-Agent": "alpaca-paper-trader/1.0",
    }


def alpaca_request(method: str, path: str, body: dict[str, Any] | None = None, base_url: str = PAPER_BASE_URL) -> Any:
    url = urlparse.urljoin(base_url, path)
    payload = None if body is None else json.dumps(body).encode("utf-8")
    req = urlrequest.Request(url, data=payload, method=method.upper(), headers=alpaca_headers())
    try:
        with urlrequest.urlopen(req, timeout=ALPACA_HTTP_TIMEOUT_SECONDS) as response:
            raw = response.read().decode("utf-8")
    except urlerror.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Alpaca HTTP {exc.code}: {detail}") from exc
    except urlerror.URLError as exc:
        raise RuntimeError(f"Alpaca connection error: {exc}") from exc
    if not raw:
        return {}
    return json.loads(raw)


def fetch_account_snapshot() -> dict[str, Any]:
    account = alpaca_request("GET", "/v2/account")
    positions = alpaca_request("GET", "/v2/positions")
    open_orders = alpaca_request("GET", "/v2/orders?status=open&direction=desc")
    return {
        "account_id": account.get("id"),
        "status": account.get("status"),
        "buying_power": account.get("buying_power"),
        "cash": account.get("cash"),
        "portfolio_value": account.get("portfolio_value"),
        "pattern_day_trader": account.get("pattern_day_trader"),
        "positions_count": len(positions) if isinstance(positions, list) else 0,
        "open_orders_count": len(open_orders) if isinstance(open_orders, list) else 0,
        "session": current_session_label(),
    }


def fetch_market_clock() -> dict[str, Any]:
    clock = alpaca_request("GET", "/v2/clock")
    if not isinstance(clock, dict):
        raise RuntimeError("Alpaca market clock returned an invalid payload.")
    return clock


def require_regular_market_open() -> dict[str, Any]:
    """Hard execution gate based on the broker's holiday/early-close-aware clock."""

    clock = fetch_market_clock()
    if not bool(clock.get("is_open")):
        raise RuntimeError(
            "Paper order blocked because Alpaca market clock is closed "
            f"(timestamp={clock.get('timestamp')}, next_open={clock.get('next_open')})."
        )
    return clock


def fetch_asset(symbol: str) -> dict[str, Any]:
    return alpaca_request("GET", f"/v2/assets/{symbol}")


def fetch_order_by_id(order_id: str) -> dict[str, Any]:
    return alpaca_request("GET", f"/v2/orders/{order_id}")


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
        "created_at": order.get("created_at"),
        "updated_at": order.get("updated_at"),
        "filled_at": order.get("filled_at"),
        "canceled_at": order.get("canceled_at"),
        "expired_at": order.get("expired_at"),
        "failed_at": order.get("failed_at"),
        "filled_avg_price": order.get("filled_avg_price"),
    }


def refresh_order_until_stable(order_id: str, attempts: int = 5, wait_seconds: float = 1.0) -> dict[str, Any] | None:
    if not order_id or attempts <= 0:
        return None
    latest: dict[str, Any] | None = None
    for attempt in range(attempts):
        if attempt > 0 and wait_seconds > 0:
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


def fetch_latest_trade_price(symbol: str) -> float | None:
    payload = alpaca_request("GET", f"/v2/stocks/{symbol}/trades/latest?feed=iex", base_url=MARKET_DATA_BASE_URL)
    trade = payload.get("trade", {}) if isinstance(payload, dict) else {}
    return to_float(trade.get("p"))


def parse_alpaca_timestamp(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None


def fetch_intraday_bars(symbol: str, timeframe: str, lookback_hours: int = 48) -> list[dict[str, Any]]:
    end_dt = datetime.now(timezone.utc) - timedelta(minutes=16)
    start_dt = end_dt - timedelta(hours=max(4, lookback_hours))
    params = {
        "timeframe": timeframe,
        "start": start_dt.isoformat().replace("+00:00", "Z"),
        "end": end_dt.isoformat().replace("+00:00", "Z"),
        "limit": "50",
        "adjustment": "raw",
        "feed": "iex",
    }
    query = urlparse.urlencode(params)
    payload = alpaca_request("GET", f"/v2/stocks/{symbol}/bars?{query}", base_url=MARKET_DATA_BASE_URL)
    bars = payload.get("bars", []) if isinstance(payload, dict) else []
    if not isinstance(bars, list):
        return []
    return [bar for bar in bars if isinstance(bar, dict)]


def entry_confirmation(
    symbol: str,
    trade_direction: str,
    timeframe: str,
    *,
    expected_session_date: str | None = None,
) -> dict[str, Any]:
    bars = fetch_intraday_bars(symbol, timeframe)
    bars = sorted(
        bars,
        key=lambda item: parse_alpaca_timestamp(item.get("t")) or datetime.min.replace(tzinfo=timezone.utc),
    )
    required_session = expected_session_date or datetime.now(timezone.utc).astimezone(EASTERN_TZ).date().isoformat()
    observed_sessions = sorted(
        {
            parsed.astimezone(EASTERN_TZ).date().isoformat()
            for item in bars
            if (parsed := parse_alpaca_timestamp(item.get("t"))) is not None
        }
    )
    session_bars = [
        item
        for item in bars
        if (parsed := parse_alpaca_timestamp(item.get("t"))) is not None
        and parsed.astimezone(EASTERN_TZ).date().isoformat() == required_session
    ]
    if len(session_bars) < 2:
        return {
            "entry_status": "waiting_confirmation",
            "entry_trigger_confirmed": False,
            "entry_trigger_reason": (
                f"{symbol} has fewer than two completed {timeframe} candles for the required "
                f"market session {required_session}; stale prior-session bars cannot confirm entry."
            ),
            "entry_timeframe": timeframe,
            "bars_checked": len(session_bars),
            "bars_available_all_sessions": len(bars),
            "required_session_date": required_session,
            "observed_session_dates": observed_sessions,
            "data_error_code": "stale_last_bar",
        }

    previous = session_bars[-2]
    latest = session_bars[-1]
    prev_high = to_float(previous.get("h"))
    prev_low = to_float(previous.get("l"))
    latest_open = to_float(latest.get("o"))
    latest_close = to_float(latest.get("c"))
    latest_time = latest.get("t")
    direction = str(trade_direction or "long").lower().strip()

    if None in {prev_high, prev_low, latest_open, latest_close}:
        return {
            "entry_status": "waiting_confirmation",
            "entry_trigger_confirmed": False,
            "entry_trigger_reason": f"{symbol} {timeframe} confirmation data is incomplete.",
            "entry_timeframe": timeframe,
            "bars_checked": len(bars),
            "latest_bar_time": latest_time,
        }

    if direction == "short":
        confirmed = bool(latest_close < prev_low and latest_close < latest_open)
        if confirmed:
            reason = (
                f"{symbol} short trigger confirmed on {timeframe}: latest close "
                f"{latest_close:.4f} broke below previous low {prev_low:.4f} with a red candle."
            )
            status = "entry_confirmed"
        else:
            reason = (
                f"{symbol} short setup is still waiting: latest {timeframe} close "
                f"{latest_close:.4f} did not break below previous low {prev_low:.4f} "
                f"with bearish candle confirmation."
            )
            status = "waiting_confirmation"
    else:
        confirmed = bool(latest_close > prev_high and latest_close > latest_open)
        if confirmed:
            reason = (
                f"{symbol} long trigger confirmed on {timeframe}: latest close "
                f"{latest_close:.4f} broke above previous high {prev_high:.4f} with a green candle."
            )
            status = "entry_confirmed"
        else:
            reason = (
                f"{symbol} long setup is still waiting: latest {timeframe} close "
                f"{latest_close:.4f} did not break above previous high {prev_high:.4f} "
                f"with bullish candle confirmation."
            )
            status = "waiting_confirmation"

    return {
        "entry_status": status,
        "entry_trigger_confirmed": confirmed,
        "entry_trigger_reason": reason,
        "entry_timeframe": timeframe,
        "bars_checked": len(session_bars),
        "required_session_date": required_session,
        "observed_session_dates": observed_sessions,
        "previous_bar": {
            "t": previous.get("t"),
            "o": previous.get("o"),
            "h": previous.get("h"),
            "l": previous.get("l"),
            "c": previous.get("c"),
            "v": previous.get("v"),
        },
        "latest_bar": {
            "t": latest.get("t"),
            "o": latest.get("o"),
            "h": latest.get("h"),
            "l": latest.get("l"),
            "c": latest.get("c"),
            "v": latest.get("v"),
        },
    }


def validate_directional_trade(symbol: str, trade_direction: str, asset: dict[str, Any]) -> None:
    if not bool(asset.get("tradable", False)):
        raise RuntimeError(f'Asset "{symbol}" is not tradable on Alpaca.')
    if trade_direction != "short":
        return
    if not bool(asset.get("shortable", False)):
        raise RuntimeError(f'Asset "{symbol}" is not shortable on Alpaca paper right now.')


def submit_paper_order(
    trader_slug: str,
    row: dict[str, Any],
    notional_usd: float,
    refresh_attempts: int = 5,
    refresh_wait_seconds: float = 1.0,
    approved_intent_id: str | None = None,
) -> dict[str, Any]:
    if not str(approved_intent_id or "").strip():
        raise RuntimeError("Thymios blocked broker submission because approved_intent_id is missing.")
    market_clock = require_regular_market_open()
    ticker = str(row.get("ticker") or "").upper()
    trade_direction = str(row.get("trade_direction") or "long").lower().strip() or "long"
    side = "sell" if trade_direction == "short" else "buy"
    intent_suffix = str(approved_intent_id or "preview").replace("intent_", "")[:8]
    client_order_id = (
        f"{trader_slug[:8]}-{now_athens().strftime('%m%d%H%M%S')}-{ticker[:6]}-{intent_suffix}"
    ).lower()[:48]
    asset = fetch_asset(ticker)
    validate_directional_trade(ticker, trade_direction, asset)
    is_fractionable = bool(asset.get("fractionable", False))
    order_payload = {
        "symbol": ticker,
        "side": side,
        "type": "market",
        "time_in_force": "day",
        "client_order_id": client_order_id,
    }
    sizing_mode = "notional"
    estimated_price = None
    if is_fractionable and trade_direction != "short":
        order_payload["notional"] = f"{notional_usd:.2f}"
    else:
        estimated_price = fetch_latest_trade_price(ticker)
        if estimated_price is not None and estimated_price > 0:
            qty = max(1, int(notional_usd // estimated_price))
        else:
            qty = 1
        order_payload["qty"] = str(qty)
        sizing_mode = "qty"
    response = alpaca_request("POST", "/v2/orders", order_payload)
    initial_response = compact_order_payload(response)
    refreshed_response = refresh_order_until_stable(
        str(initial_response.get("id") or ""),
        attempts=refresh_attempts,
        wait_seconds=refresh_wait_seconds,
    )
    latest_response = refreshed_response or initial_response
    return {
        "request": order_payload,
        "asset": {
            "fractionable": is_fractionable,
            "tradable": asset.get("tradable"),
            "exchange": asset.get("exchange"),
            "shortable": asset.get("shortable"),
            "easy_to_borrow": asset.get("easy_to_borrow"),
        },
        "trade_direction": trade_direction,
        "sizing_mode": sizing_mode,
        "estimated_price": estimated_price,
        "response": latest_response,
        "initial_response": initial_response,
        "refreshed_response": refreshed_response,
        "refresh_attempts": refresh_attempts,
        "market_clock": {
            "is_open": market_clock.get("is_open"),
            "timestamp": market_clock.get("timestamp"),
            "next_close": market_clock.get("next_close"),
        },
    }


def render_markdown_report(payload: dict[str, Any]) -> str:
    risk_settings = payload.get("risk_settings", {})
    lines = [
        f"# {payload.get('agent_name')} Paper Trading Report",
        "",
        f"- Run timestamp: {payload.get('run_timestamp')}",
        f"- Execution mode: {payload.get('execution_mode')}",
        f"- Session: {payload.get('account_snapshot', {}).get('session')}",
        f"- Selected candidates: {payload.get('selected_count')}",
        f"- Submitted orders: {payload.get('submitted_count')}",
        f"- Failed submissions: {payload.get('failed_count')}",
        f"- Waiting for confirmation: {payload.get('confirmation_blocked_count', 0)}",
        f"- Notional per trade: {payload.get('notional_per_trade_usd')}",
        "",
        "## User-Configurable Risk Settings",
        "",
        f"- Position size / notional: {risk_settings.get('notional_per_trade_usd')} ({risk_settings.get('notional_source')})",
        f"- Take-profit: {risk_settings.get('take_profit_pct')} ({risk_settings.get('take_profit_source')})",
        f"- Stop-loss: {risk_settings.get('stop_loss_pct')} ({risk_settings.get('stop_loss_source')})",
        f"- Holding period: {risk_settings.get('holding_period_rule')} ({risk_settings.get('holding_period_source')})",
        "",
        "These are configurable user settings or paper-testing defaults, not universal recommendations.",
        "",
        "## Account Snapshot",
        "",
        f"- Buying power: {payload.get('account_snapshot', {}).get('buying_power')}",
        f"- Cash: {payload.get('account_snapshot', {}).get('cash')}",
        f"- Portfolio value: {payload.get('account_snapshot', {}).get('portfolio_value')}",
        f"- Existing positions: {payload.get('account_snapshot', {}).get('positions_count')}",
        f"- Existing open orders: {payload.get('account_snapshot', {}).get('open_orders_count')}",
        "",
        "## Opening Decisions",
        "",
    ]

    openings = payload.get("openings") or []
    if openings:
        for item in openings:
            lines.append(
                f"- {item.get('ticker')} | score={item.get('signal_score')} | playbook={item.get('playbook_name')} | "
                f"verdict={item.get('signal_verdict')} | direction={item.get('trade_direction')} | "
                f"entry_status={item.get('entry_status')} | order_status={item.get('order_status')}"
            )
            lines.append(f"  justification: {item.get('entry_justification')}")
            if item.get("entry_trigger_reason"):
                lines.append(f"  entry_trigger: {item.get('entry_trigger_reason')}")
            rationale = item.get("rationale") or []
            if rationale:
                lines.append(f"  rationale: {'; '.join(str(x) for x in rationale[:6])}")
            if item.get("submission_error"):
                lines.append(f"  submission_error: {item.get('submission_error')}")
            elif item.get("order_response"):
                order = item["order_response"]
                lines.append(
                    f"  sizing: mode={item.get('sizing_mode')} estimated_price={item.get('estimated_price')} "
                    f"request={json.dumps(item.get('order_request', {}), ensure_ascii=False)}"
                )
                lines.append(
                    f"  alpaca_order: id={order.get('id')} status={order.get('status')} "
                    f"filled_qty={order.get('filled_qty')} filled_avg_price={order.get('filled_avg_price')} "
                    f"submitted_at={order.get('submitted_at')} updated_at={order.get('updated_at')}"
                )
                if item.get("initial_order_response"):
                    lines.append(
                        f"  initial_alpaca_order: "
                        f"{json.dumps(item.get('initial_order_response'), ensure_ascii=False)}"
                    )
        lines.append("")
    else:
        lines.extend(["- No paper-trade openings selected.", ""])

    skipped = payload.get("skipped") or []
    lines.extend(["## Skipped", ""])
    if skipped:
        for item in skipped[:20]:
            lines.append(f"- {item.get('ticker')} | reason={item.get('reason')} | score={item.get('signal_score')}")
    else:
        lines.append("- No skipped rows.")
    lines.append("")
    return "\n".join(lines)


def run(args: argparse.Namespace) -> int:
    strategy_payload = load_json(Path(args.strategy))
    run_ts = str(strategy_payload.get("run_timestamp") or now_athens().isoformat())
    agent_name = str(strategy_payload.get("agent_name") or args.agent_name or "agent")
    trader_slug = normalize_agent_slug(agent_name)
    approved_payload: dict[str, Any] | None = None
    approved_map: dict[tuple[str, str, str], dict[str, Any]] | None = None
    if args.submit:
        if not args.approved_intents:
            raise SystemExit(
                "Paper submission requires --approved-intents produced by Koulis and validated by Thymios."
            )
        approved_payload = load_approved_intents(Path(args.approved_intents))
        approved_map = intents_for_agent(approved_payload, trader_slug)
        expected_source_run = str((approved_payload.get("source_run_ids") or {}).get(trader_slug) or "").strip()
        actual_source_run = str(strategy_payload.get("run_id") or strategy_payload.get("run_timestamp") or "").strip()
        if expected_source_run and actual_source_run and expected_source_run != actual_source_run:
            raise SystemExit(
                f"Approved intent/source run mismatch for {trader_slug}: "
                f"approved={expected_source_run!r}, strategy={actual_source_run!r}."
            )
    existing_report_path = Path(f"{Path(args.out_prefix)}_latest.json")
    excluded_tickers = excluded_tickers_from_report(existing_report_path) if args.exclude_existing_report else set()
    selected, skipped = choose_trade_rows(
        strategy_payload,
        max_orders=args.max_orders,
        min_score=args.min_score,
        excluded_tickers=excluded_tickers,
        precheck_shortable=args.precheck_shortable,
        approved_intents=approved_map,
    )

    account_snapshot: dict[str, Any] = {}
    if args.submit:
        account_snapshot = fetch_account_snapshot()
    else:
        account_snapshot = {"session": current_session_label()}

    openings: list[dict[str, Any]] = []
    submitted_count = 0
    failed_count = 0
    for row in selected:
        opening = {
            "ticker": str(row.get("ticker") or "").upper(),
            "company": row.get("company"),
            "playbook_name": row.get("playbook_name"),
            "signal_score": to_float(row.get("signal_score")),
            "signal_verdict": row.get("signal_verdict"),
            "trade_direction": str(row.get("trade_direction") or "long"),
            "risk_level": row.get("risk_level"),
            "technical_timing": row.get("technical_timing"),
            "rationale": row.get("rationale", []),
            "entry_justification": entry_justification(agent_name, row),
            "entry_status": "setup_only",
            "entry_timeframe": args.entry_confirmation_timeframe,
            "order_status": "preview",
            "order_request": {
                "side": "sell" if str(row.get("trade_direction") or "long").lower().strip() == "short" else "buy",
                "type": "market",
                "time_in_force": "day",
                "notional_usd": round(args.notional_per_trade, 2),
            },
            "approved_intent_id": row.get("approved_intent_id"),
            "approved_notional_usd": row.get("approved_notional_usd"),
            "paper_trade_plan": row.get("approved_trade_plan") or row.get("paper_trade_plan"),
            "frozen_initial_rationale": (row.get("approved_trade_plan") or row.get("paper_trade_plan") or {}).get("initial_rationale"),
            "frozen_initial_rationale_sha256": (row.get("approved_trade_plan") or row.get("paper_trade_plan") or {}).get("initial_rationale_sha256"),
        }
        try:
            confirmation = entry_confirmation(
                str(row.get("ticker") or "").upper(),
                str(row.get("trade_direction") or "long"),
                args.entry_confirmation_timeframe,
            )
        except Exception as exc:
            confirmation = {
                "entry_status": "waiting_confirmation",
                "entry_trigger_confirmed": False,
                "entry_trigger_reason": f"Entry confirmation check failed: {exc}",
                "entry_timeframe": args.entry_confirmation_timeframe,
            }
        opening.update(confirmation)
        if args.require_entry_confirmation and not bool(confirmation.get("entry_trigger_confirmed")):
            opening["order_status"] = "waiting_confirmation"
            openings.append(opening)
            continue
        if args.submit:
            try:
                order_result = submit_paper_order(
                    trader_slug,
                    row,
                    float(row.get("approved_notional_usd") or args.notional_per_trade),
                    refresh_attempts=args.post_submit_refresh_attempts,
                    refresh_wait_seconds=args.post_submit_refresh_seconds,
                    approved_intent_id=str(row.get("approved_intent_id") or ""),
                )
                opening["order_status"] = str(order_result["response"].get("status") or "submitted")
                opening["client_order_id"] = order_result["response"].get("client_order_id")
                opening["order_response"] = order_result["response"]
                opening["initial_order_response"] = order_result.get("initial_response")
                opening["refreshed_order_response"] = order_result.get("refreshed_response")
                opening["order_refresh_attempts"] = order_result.get("refresh_attempts")
                opening["order_request"] = order_result["request"]
                opening["asset_meta"] = order_result.get("asset", {})
                opening["trade_direction"] = order_result.get("trade_direction", opening.get("trade_direction"))
                opening["sizing_mode"] = order_result.get("sizing_mode")
                opening["estimated_price"] = order_result.get("estimated_price")
                submitted_count += 1
            except Exception as exc:
                opening["order_status"] = "submit_failed"
                opening["submission_error"] = str(exc)
                failed_count += 1
        openings.append(opening)

    existing_payload = load_json(existing_report_path) if args.merge_existing and existing_report_path.exists() else {}
    merged_openings = merge_openings(existing_payload.get("openings", []), openings)
    payload = {
        "run_id": (
            approved_payload.get("run_id")
            if approved_payload
            else strategy_payload.get("run_id")
        ),
        "run_timestamp": now_athens().isoformat(),
        "strategy_run_timestamp": run_ts,
        "agent_name": agent_name,
        "execution_mode": "paper_submit" if args.submit else "preview_only",
        "strategy_file": str(Path(args.strategy)),
        "approved_intents_file": str(Path(args.approved_intents)) if args.approved_intents else None,
        "approved_intents_run_id": approved_payload.get("run_id") if approved_payload else None,
        "notional_per_trade_usd": round(args.notional_per_trade, 2),
        "risk_settings": {
            "notional_per_trade_usd": round(args.notional_per_trade, 2),
            "notional_source": args.notional_source,
            "take_profit_pct": args.take_profit_pct,
            "take_profit_source": args.take_profit_source,
            "stop_loss_pct": args.stop_loss_pct,
            "stop_loss_source": args.stop_loss_source,
            "holding_period_rule": args.holding_period_rule,
            "holding_period_source": args.holding_period_source,
        },
        "max_orders": args.max_orders,
        "min_score": args.min_score,
        "precheck_shortable": bool(args.precheck_shortable),
        "account_snapshot": account_snapshot,
        "selected_count": len(selected),
        "submitted_count": submitted_count,
        "failed_count": failed_count,
        "confirmation_blocked_count": sum(
            1 for item in openings if item.get("order_status") == "waiting_confirmation"
        ),
        "skipped_count": len(skipped),
        "openings_total": len(merged_openings),
        "new_openings_count": len(openings),
        "openings": merged_openings,
        "skipped": skipped,
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
    parser = argparse.ArgumentParser(description="Submit or preview agent paper trades to Alpaca paper trading.")
    parser.add_argument("--strategy", required=True, help="Path to strategy latest JSON output.")
    parser.add_argument("--agent-name", help="Optional override for agent name.")
    parser.add_argument("--out-prefix", required=True, help="Output prefix for the paper-trade report.")
    parser.add_argument("--max-orders", type=int, default=3, help="Maximum number of candidate openings to submit.")
    parser.add_argument("--min-score", type=float, default=7.0, help="Minimum signal score to allow an opening.")
    parser.add_argument("--notional-per-trade", type=float, default=250.0, help="USD notional for each paper order.")
    parser.add_argument("--entry-confirmation-timeframe", default="15Min", help="Intraday Alpaca bar timeframe used for entry confirmation.")
    parser.add_argument("--require-entry-confirmation", action=argparse.BooleanOptionalAction, default=True, help="Require an intraday trigger before submitting a paper order.")
    parser.add_argument("--precheck-shortable", action=argparse.BooleanOptionalAction, default=True, help="Filter short candidates before ranking when Alpaca marks the asset as not shortable.")
    parser.add_argument("--exclude-existing-report", action="store_true", help="Skip symbols already present in the current output report.")
    parser.add_argument("--merge-existing", action="store_true", help="Merge newly created openings into the existing latest output report.")
    parser.add_argument("--post-submit-refresh-attempts", type=int, default=5, help="How many times to refresh an Alpaca order after submit before writing the report.")
    parser.add_argument("--post-submit-refresh-seconds", type=float, default=1.0, help="Seconds between post-submit order refresh attempts.")
    parser.add_argument("--notional-source", choices=["default", "user_setting", "agent_suggestion"], default="default", help="Source label for notional-per-trade in reports.")
    parser.add_argument("--take-profit-pct", type=float, default=0.06, help="Take-profit assumption recorded in reports.")
    parser.add_argument("--take-profit-source", choices=["default", "user_setting", "agent_suggestion"], default="default", help="Source label for take-profit in reports.")
    parser.add_argument("--stop-loss-pct", type=float, default=0.03, help="Stop-loss assumption recorded in reports.")
    parser.add_argument("--stop-loss-source", choices=["default", "user_setting", "agent_suggestion"], default="default", help="Source label for stop-loss in reports.")
    parser.add_argument("--holding-period-rule", default="same_day_flat", help="Holding period / maximum time-in-trade rule recorded in reports.")
    parser.add_argument("--holding-period-source", choices=["default", "user_setting", "agent_suggestion"], default="default", help="Source label for holding-period rule in reports.")
    parser.add_argument("--submit", action="store_true", help="Actually submit paper orders to Alpaca.")
    parser.add_argument(
        "--approved-intents",
        help="Koulis-approved order-intent JSON. Required with --submit and validated by Thymios.",
    )
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    load_dotenv(Path(".env"))
    return run(args)


if __name__ == "__main__":
    raise SystemExit(main())
