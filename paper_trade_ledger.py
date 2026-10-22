"""Paper trade ledger: one immutable record per paper trade, and per decision that did not become one.

Owner's decision 2026-10-15 (learning from real paper trades, phase 1). After each afternoon session this collects,
for the session's New York date:

* ``trade``: an approved intent that was filled - entry and exit price and time, exit reason (stop, take-profit,
  end of day), result, plus the context it was decided in (trader, strategy, direction, holding class, run, signal
  score, standing inside the strategy, pre-open check, market state).
* ``approved_not_entered``: approved by Koulis, never filled (Miltos saw no confirmation, or no order went out).
* ``preopen_demoted``: a candidate the pre-open check took down to watch.
* ``koulis_left_out``: eligible candidates Koulis did not approve for portfolio limits (the best 10 by standing).

The last three keep what *would* have happened, filled in later by ``paper_learning_evaluator.py`` from the price
store, so the control team (pre-open check, Koulis, Miltos, monitors) can be judged too. Prices come from the
ecosystem's own files (the trader's opening report, the monitors' action journal); only a price missing there is
read from Alpaca (read-only: closed orders of the session, owner's approval 2026-10-15). A value that cannot be read
stays unknown. A complete record is never rewritten (``INSERT OR IGNORE``); an incomplete one may be completed.
"""
from __future__ import annotations

import argparse
from datetime import date, datetime
import hashlib
import json
import os
from pathlib import Path
import sqlite3
from typing import Any, Callable
from zoneinfo import ZoneInfo

import feature_registry

ROOT = Path(__file__).resolve().parent
DB_PATH = ROOT / "data" / "paper_learning.db"
NEW_YORK = ZoneInfo("America/New_York")
AGENTS = ("yankos", "periklis")
LEFT_OUT_KEPT = 10
HOLDING_CLASS = {"same_day_flat": "day"}
SCHEMA = "ecosystem.paper_trade_record.v1"

Orders = Callable[[date], list[dict[str, Any]]]


def _load(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _float(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number and number > 0 else None


def _moment(stamp: Any) -> datetime | None:
    """ISO 8601 with up to nanoseconds (Alpaca) or an offset (our files); None when unreadable."""
    import re

    text = re.sub(r"(\.\d{6})\d+", r"\1", str(stamp or "").strip()).replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(text) if text else None
    except ValueError:
        return None


def _ny_date(stamp: Any) -> date | None:
    moment = _moment(stamp)
    if moment is None:
        return None
    return moment.date() if moment.tzinfo is None else moment.astimezone(NEW_YORK).date()


def exit_reason(text: Any) -> str:
    lowered = str(text or "").lower()
    if "flat before the session ends" in lowered or "end of day" in lowered or "session ends" in lowered:
        return "end_of_day"
    if "take-profit" in lowered or "take profit" in lowered or "take_profit" in lowered:
        return "take_profit"
    if "stop" in lowered:
        return "stop_loss"
    return "other" if lowered else "unknown"


def _fill(response: Any) -> tuple[float | None, str | None]:
    response = response if isinstance(response, dict) else {}
    return _float(response.get("filled_avg_price")), (str(response.get("filled_at")) if response.get("filled_at") else None)


def record_id(session: date, kind: str, agent: str, ticker: str, playbook: str, direction: str) -> str:
    raw = "|".join([session.isoformat(), kind, agent, ticker, playbook, direction])
    return "ptr_" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def _bucket(value: float | None, edges: tuple[float, ...], names: tuple[str, ...]) -> str:
    if value is None:
        return "unknown"
    for edge, name in zip(edges, names):
        if value < edge:
            return name
    return names[-1]


def categories(row: dict[str, Any], company: dict[str, Any], petros: dict[str, Any], preopen: dict[str, Any] | None,
               market_state: Any, session: date) -> dict[str, str]:
    """The tags every record carries, so learning can tell *where* something works. A missing value is the
    explicit label "unknown", never a guessed one."""
    direction = str(row.get("trade_direction") or row.get("direction") or "").lower()
    gap = (preopen or {}).get("gap_pct")
    signed = None if gap is None or direction not in {"long", "short"} else (gap if direction == "long" else -gap)
    factors = company.get("quality_factors") or {}
    f_score = (factors.get("piotroski") or {}).get("score")
    rank = row.get("strategy_rank_pct")
    score = _float(row.get("signal_score"))

    def text(value: Any) -> str:
        return str(value) if value not in (None, "") else "unknown"

    return {
        "sector": text(row.get("sector") or company.get("sector")),
        "market_cap": _bucket(_float(company.get("market_cap")), (3e8, 2e9, 1e10, 2e11),
                              ("micro", "small", "mid", "large", "mega")),
        "giannis_tier": text(company.get("giannis_tier")),
        "petros_verdict": text(petros.get("petros_verdict")),
        "piotroski": "unknown" if f_score is None else "strong" if f_score >= 7 else "middle" if f_score >= 4 else "weak",
        "catalyst": text(row.get("catalyst_category_id") or company.get("catalyst_category_id")),
        "volatility": _bucket(_float(row.get("atr_14_pct")), (2.0, 4.0, 6.0), ("calm", "normal", "high", "very_high")),
        "score": "unknown" if score is None else str(int(score)),
        "standing": ("unknown" if rank is None else "top_10pct" if rank >= 0.9 else "top_25pct" if rank >= 0.75
                     else "rest"),
        "premarket_gap": ("unknown" if signed is None else "against_large" if signed < -2 else "against"
                          if signed < -0.5 else "with" if signed > 0.5 else "flat"),
        "risk_tier": text(row.get("risk_tier")),
        "timing": text(row.get("technical_timing")),
        "market_state": text(market_state),
        "weekday": session.strftime("%a"),
    }


def _base(session: date, kind: str, agent: str, row: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    ticker = str(row.get("ticker") or "").upper()
    playbook = str(row.get("playbook_name") or row.get("playbook") or "")
    direction = str(row.get("trade_direction") or row.get("direction") or "").lower()
    preopen = context["preopen"].get((agent, ticker, playbook, direction))
    signal = context["signals"].get((agent, ticker, playbook, direction)) or {}
    merged = {**signal, **{k: v for k, v in row.items() if v is not None}}
    # each position's own limits, as the monitors apply them (the trade plan first, the session default otherwise)
    plan = merged.get("paper_trade_plan") or merged.get("trade_plan") or {}
    plan = plan if isinstance(plan, dict) else {}
    return {"schema": SCHEMA, "record_id": record_id(session, kind, agent, ticker, playbook, direction),
            "session_date": session.isoformat(), "kind": kind, "agent": agent, "ticker": ticker,
            "playbook": playbook, "direction": direction, "holding_class": context["holding_class"],
            "run_id": context["run_id"], "market_state": context["market_state"],
            "signal_score": _float(merged.get("signal_score")),
            "strategy_rank_pct": row.get("strategy_rank_pct"), "preopen": preopen,
            "stop_loss_pct": _float(plan.get("stop_loss_pct")) or context.get("default_stop"),
            "take_profit_pct": _float(plan.get("take_profit_pct")) or context.get("default_take"),
            "limits_source": "trade_plan" if _float(plan.get("stop_loss_pct")) else "session_default",
            "categories": {**categories(merged, context["companies"].get(ticker, {}), context["petros"].get(ticker, {}),
                                        preopen, context["market_state"], session),
                           **(context["features"](ticker, session) if context.get("features") else {}),
                           # the 09:32 recheck at the open (owner 2026-10-22): restored / stays_watch / none
                           "open_recheck": str((merged.get("open_recheck") or {}).get("decision") or "none"),
                           # taken down at the previous session's pre-open check and a candidate again (the
                           # owner's watch priority, followed in shadow): after_filing / after_gap / no / unknown
                           "returned_after_demotion": returned_after_demotion(
                               context.get("previous_demoted"), (agent, ticker, playbook, direction),
                               str(merged.get("signal_verdict") or "").lower())}}


def previous_demotions(out: Path, session: date) -> dict[tuple[str, str, str, str], str] | None:
    """The take-downs of the last pre-open check before ``session`` (history file), with their kind; None when
    there is no earlier check to compare with."""
    folder = out / "preopen" / "history"
    files = sorted(p for p in folder.glob("preopen_check_*.json") if p.stem[-8:] < session.strftime("%Y%m%d")) \
        if folder.is_dir() else []
    if not files:
        return None
    report = _load(files[-1])
    out_map: dict[tuple[str, str, str, str], str] = {}
    for r in report.get("rows") or []:
        if r.get("decision") == "demoted_to_watch":
            reasons = " ".join(r.get("demote_reasons") or [])
            out_map[(r.get("agent"), str(r.get("ticker") or "").upper(), r.get("playbook"), r.get("direction"))] = \
                "after_filing" if "SEC event" in reasons else "after_gap"
    return out_map


def returned_after_demotion(previous: dict[tuple[str, str, str, str], str] | None,
                            key: tuple[str, str, str, str], verdict: str) -> str:
    if previous is None:
        return "unknown"
    return previous.get(key, "no") if verdict == "candidate" else "no"


def build_records(out: Path, session: date, *, orders: Orders | None = None) -> list[dict[str, Any]]:
    """All records of one session from the output files (``orders``: Alpaca closed orders, used only for gaps)."""
    koulis = _load(out / "koulis_order_intents_latest.json")
    preopen_report = _load(out / "preopen" / "preopen_check_latest.json")
    traded = _ny_date(koulis.get("generated_at")) == session
    checked = _ny_date(preopen_report.get("checked_at")) == session
    if not traded and not checked:
        return []  # no session that day: nothing to record, nothing invented
    if not traded:
        koulis = {"run_id": preopen_report.get("nightly_run_id")}  # checked but no orders: shadow records only
    run = _load(out / "trader_paper_trading_run_latest.json")
    risk = run.get("risk_settings") or {}
    rule = str(risk.get("holding_period_rule") or "same_day_flat")
    stop, take = _float(risk.get("stop_loss_pct")), _float(risk.get("take_profit_pct"))
    preopen = {}
    if checked:
        for r in preopen_report.get("rows") or []:
            preopen[(r.get("agent"), str(r.get("ticker") or "").upper(), r.get("playbook"), r.get("direction"))] = {
                "decision": r.get("decision"), "gap_pct": r.get("gap_pct"), "reasons": r.get("demote_reasons") or []}
    macro = _load(out / "macro_environment_latest.json")
    signals: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    for agent in AGENTS:  # the checked trader rows: every candidate, with its own fields for the tags
        payload = _load(out / "preopen" / f"{agent}_strategy_preopen_latest.json") if checked else {}
        for row in payload.get("results") or []:
            if isinstance(row, dict) and str(row.get("signal_verdict") or "").lower() in {"candidate", "watch"}:
                signals[(agent, str(row.get("ticker") or "").upper(), str(row.get("playbook_name") or ""),
                         str(row.get("trade_direction") or "").lower())] = row
    companies = {str(r.get("ticker") or "").upper(): r for r in
                 _load(out / "giannis_rule_based_latest.json").get("results") or [] if isinstance(r, dict)}
    petros = {str(r.get("ticker") or "").upper(): r for r in
              _load(out / "petros_review_latest.json").get("results") or [] if isinstance(r, dict)}
    context = {"holding_class": HOLDING_CLASS.get(rule, rule), "run_id": koulis.get("run_id"),
               "market_state": (macro.get("market") or {}).get("state"), "preopen": preopen,
               "signals": signals, "companies": companies, "petros": petros,
               "default_stop": stop or 0.03, "default_take": take or 0.06,
               "features": feature_registry.load(out),  # every registered data source adds its tags
               "previous_demoted": previous_demotions(out, session)}

    openings: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    for agent in AGENTS:
        for o in _load(out / f"{agent}_paper_trades_latest.json").get("openings") or []:
            openings[(agent, str(o.get("ticker") or "").upper(), str(o.get("playbook_name") or ""),
                      str(o.get("trade_direction") or "").lower())] = o
    closes: dict[tuple[str, str], dict[str, Any]] = {}
    journal = out / "paper_session" / f"actions_{session:%Y%m%d}.jsonl"
    if journal.is_file():
        for line in journal.read_text(encoding="utf-8").splitlines():
            try:
                action = json.loads(line)
            except ValueError:
                continue
            if action.get("action_type") == "close_position":
                closes[(str(action.get("agent") or ""), str(action.get("ticker") or "").upper())] = action
    for agent in AGENTS:  # the last cycle, in case the journal missed it
        monitor = _load(out / f"{agent}_position_monitor_latest.json")
        if _ny_date(monitor.get("run_timestamp")) == session:
            for action in monitor.get("actions") or []:
                if action.get("action_type") == "close_position":
                    closes.setdefault((agent, str(action.get("ticker") or "").upper()), action)

    broker: list[dict[str, Any]] | None = None
    records: list[dict[str, Any]] = []
    for intent in koulis.get("approved_intents") or []:
        agent = str(intent.get("agent") or "")
        key = (agent, str(intent.get("ticker") or "").upper(), str(intent.get("playbook_name") or ""),
               str(intent.get("trade_direction") or "").lower())
        opening = openings.get(key) or {}
        status = str(opening.get("order_status") or "").lower()
        if status != "filled":
            record = _base(session, "approved_not_entered", agent, intent, context)
            record.update({"order_status": status or None, "notional_usd": intent.get("notional_usd")})
            records.append(record)
            continue
        record = _base(session, "trade", agent, intent, context)
        entry_price, entry_at = _fill(opening.get("refreshed_order_response"))
        if entry_price is None:
            entry_price, entry_at = _fill(opening.get("order_response"))
        close = closes.get((agent, key[1])) or {}
        exit_price, exit_at = _fill(close.get("refreshed_response_payload"))
        if (entry_price is None or exit_price is None) and orders is not None:
            if broker is None:
                try:
                    broker = orders(session)
                except Exception:  # noqa: BLE001 - the broker read is optional; gaps stay unknown
                    broker = []
            side_in, side_out = ("buy", "sell") if key[3] == "long" else ("sell", "buy")
            for order in broker:
                if str(order.get("symbol") or "").upper() != key[1] or str(order.get("status")) != "filled":
                    continue
                if entry_price is None and order.get("side") == side_in:
                    entry_price, entry_at = _fill(order)
                elif exit_price is None and order.get("side") == side_out:
                    exit_price, exit_at = _fill(order)
        sign = 1.0 if key[3] == "long" else -1.0
        plpc = close.get("position_plpc")
        result = (round(sign * (exit_price / entry_price - 1) * 100, 3) if entry_price and exit_price
                  else None if plpc is None else round(float(plpc) * 100, 3))
        reason, inferred = (exit_reason(close.get("decision_reason")), False) if close else ("unknown", False)
        if reason == "unknown" and result is not None:  # exit read from the broker only: infer it from the limits
            if stop is not None and result <= -0.9 * stop * 100:
                reason, inferred = "stop_loss", True
            elif take is not None and result >= 0.9 * take * 100:
                reason, inferred = "take_profit", True
        record.update({"notional_usd": intent.get("notional_usd"), "entry_price": entry_price, "entry_at": entry_at,
                       "exit_price": exit_price, "exit_at": exit_at, "exit_reason": reason,
                       "exit_reason_inferred": inferred, "result_pct": result, "complete": result is not None})
        records.append(record)

    # Shadow candidates: every candidate the traders kept after the pre-open check, traded or not. What it would
    # have done (open to close) is filled later; ~400 a day instead of ~6 trades, so learning has data in days.
    shadows = []
    for (agent, _ticker, _playbook, _direction), row in signals.items():
        if str(row.get("signal_verdict") or "").lower() == "candidate" and row.get("execution_eligible") is True:
            shadows.append(_base(session, "shadow_candidate", agent, row, context))
    groups: dict[tuple[str, str, str], list[float]] = {}
    for record in shadows:  # standing inside the strategy, as Koulis computes it (mid-rank percentile)
        if record["signal_score"] is not None:
            groups.setdefault((record["agent"], record["playbook"], record["direction"]), []).append(record["signal_score"])
    for record in shadows:
        scores = groups.get((record["agent"], record["playbook"], record["direction"]))
        if record["signal_score"] is None or not scores:
            continue
        below = sum(1 for s in scores if s < record["signal_score"])
        equal = sum(1 for s in scores if s == record["signal_score"])
        rank = round((below + 0.5 * equal) / len(scores), 4)
        record["strategy_rank_pct"] = rank
        record["categories"]["standing"] = "top_10pct" if rank >= 0.9 else "top_25pct" if rank >= 0.75 else "rest"
    records += shadows
    for (agent, ticker, playbook, direction), view in preopen.items():
        recheck = (signals.get((agent, ticker, playbook, direction)) or {}).get("open_recheck") or {}
        if view.get("decision") == "demoted_to_watch" and not (
                recheck.get("decision") == "restored"
                and str((signals.get((agent, ticker, playbook, direction)) or {}).get("signal_verdict")) == "candidate"):
            # an idea the recheck at the open gave back to the traders is a candidate (or a trade), not a take-down
            records.append(_base(session, "preopen_demoted", agent, {"ticker": ticker, "playbook_name": playbook,
                                                                      "trade_direction": direction}, context))
    left = [b for b in koulis.get("blocked_intents") or []
            if b.get("block_reason") in {"portfolio_order_limit", "portfolio_strategy_limit"}]
    # best standing first; a missing standing or score sorts last (it is not read as a value)
    left.sort(key=lambda b: (b.get("strategy_rank_pct") is None, -(b.get("strategy_rank_pct") or 0.0),
                             b.get("signal_score") is None, -(b.get("signal_score") or 0.0)))
    for item in left[:LEFT_OUT_KEPT]:
        record = _base(session, "koulis_left_out", str(item.get("agent") or ""), item, context)
        record["block_reason"] = item.get("block_reason")
        records.append(record)
    return records


def connect(path: Path = DB_PATH) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE IF NOT EXISTS paper_trade_ledger (record_id TEXT PRIMARY KEY, session_date TEXT NOT NULL,"
                 " kind TEXT NOT NULL, complete INTEGER NOT NULL, record_json TEXT NOT NULL, recorded_at TEXT NOT NULL)")
    conn.execute("CREATE TABLE IF NOT EXISTS paper_counterfactuals (record_id TEXT PRIMARY KEY, open_close_pct REAL,"
                 " spy_open_close_pct REAL, computed_at TEXT NOT NULL)")
    return conn


def store(conn: sqlite3.Connection, records: list[dict[str, Any]]) -> dict[str, int]:
    """Insert new records; complete an incomplete one; never rewrite a complete one."""
    counts = {"inserted": 0, "completed": 0, "kept": 0}
    stamp = datetime.now().astimezone().isoformat()
    for record in records:
        complete = 1 if record.get("kind") != "trade" or record.get("complete") else 0
        row = conn.execute("SELECT complete FROM paper_trade_ledger WHERE record_id=?", (record["record_id"],)).fetchone()
        payload = json.dumps(record, ensure_ascii=False, sort_keys=True)
        if row is None:
            conn.execute("INSERT INTO paper_trade_ledger VALUES (?,?,?,?,?,?)", (
                record["record_id"], record["session_date"], record["kind"], complete, payload, stamp))
            counts["inserted"] += 1
        elif row[0] == 0 and complete:
            conn.execute("UPDATE paper_trade_ledger SET complete=1, record_json=?, recorded_at=? WHERE record_id=?",
                         (payload, stamp, record["record_id"]))
            counts["completed"] += 1
        else:
            counts["kept"] += 1
    conn.commit()
    return counts


def load(conn: sqlite3.Connection, since: date | None = None, until: date | None = None) -> list[dict[str, Any]]:
    rows = conn.execute("SELECT record_json FROM paper_trade_ledger ORDER BY session_date, record_id").fetchall()
    records = [json.loads(r[0]) for r in rows]
    return [r for r in records if (since is None or r["session_date"] >= since.isoformat())
            and (until is None or r["session_date"] <= until.isoformat())]


def alpaca_closed_orders(key_id: str, secret: str) -> Orders:
    """Read-only: the paper account's closed orders of one session (no order is ever sent)."""
    from urllib import parse, request

    def fetch(session: date) -> list[dict[str, Any]]:
        query = parse.urlencode({"status": "closed", "after": f"{session.isoformat()}T00:00:00Z",
                                 "until": f"{session.isoformat()}T23:59:59Z", "limit": 500, "direction": "asc"})
        req = request.Request(f"https://paper-api.alpaca.markets/v2/orders?{query}",
                              headers={"APCA-API-KEY-ID": key_id, "APCA-API-SECRET-KEY": secret,
                                       "Accept": "application/json"})
        with request.urlopen(req, timeout=20) as response:
            payload = json.loads(response.read() or b"[]")
        return payload if isinstance(payload, list) else []
    return fetch


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--session", help="New York date YYYY-MM-DD (default: today in New York)")
    args = parser.parse_args(argv)
    session = date.fromisoformat(args.session) if args.session else datetime.now(NEW_YORK).date()
    from research_workflow_orchestrator import load_env

    load_env(args.root / ".env")  # Alpaca keys, never printed
    key_id, secret = os.getenv("ALPACA_API_KEY_ID", "").strip(), os.getenv("ALPACA_API_SECRET_KEY", "").strip()
    orders = alpaca_closed_orders(key_id, secret) if key_id and secret else None
    records = build_records(args.root / "output", session, orders=orders)
    with connect(args.root / "data" / "paper_learning.db") as conn:
        counts = store(conn, records)
    print(json.dumps({"session": session.isoformat(), "records": len(records),
                      "by_kind": {k: sum(r["kind"] == k for r in records) for k in sorted({r["kind"] for r in records})},
                      **counts}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
