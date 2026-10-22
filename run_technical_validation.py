from __future__ import annotations

import argparse
import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib import parse as urlparse

import alpaca_paper_trader
import technical_research_lab as lab


STATE_SCHEMA = "ecosystem.technical_validation_run.v1"


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    temporary.replace(path)


def selected_setups(paths: list[Path]) -> list[dict[str, Any]]:
    best: dict[tuple[str, str], dict[str, Any]] = {}
    for path in paths:
        payload = read_json(path)
        agent = str(payload.get("agent_name") or path.stem.split("_")[0])
        for row in payload.get("results", []):
            if row.get("signal_verdict") not in {"candidate", "watch"}:
                continue
            ticker = str(row.get("ticker") or "").upper().strip()
            if not ticker:
                continue
            key = (agent, ticker)
            score = float(row.get("signal_score") or 0.0)
            if key not in best or score > float(best[key].get("signal_score") or 0.0):
                best[key] = {
                    "agent_name": agent, "ticker": ticker, "signal_score": score,
                    "signal_verdict": row.get("signal_verdict"),
                    "trade_direction": row.get("trade_direction") if row.get("trade_direction") in {"long", "short"} else "long",
                    "playbook_name": row.get("playbook_name") or row.get("primary_playbook") or "unknown",
                }
    return sorted(best.values(), key=lambda row: (row["ticker"], row["agent_name"]))


def fetch_bars(symbol: str, days: int) -> list[dict[str, Any]]:
    end = datetime.now(timezone.utc) - timedelta(minutes=20)
    start = end - timedelta(days=days)
    params = {
        "timeframe": "15Min", "start": start.isoformat().replace("+00:00", "Z"),
        "end": end.isoformat().replace("+00:00", "Z"), "limit": "10000",
        "adjustment": "raw", "feed": "iex", "sort": "asc",
    }
    payload = alpaca_paper_trader.alpaca_request(
        "GET", f"/v2/stocks/{symbol}/bars?{urlparse.urlencode(params)}",
        base_url=alpaca_paper_trader.MARKET_DATA_BASE_URL,
    )
    raw = payload.get("bars", []) if isinstance(payload, dict) else []
    result: list[dict[str, Any]] = []
    for row in raw if isinstance(raw, list) else []:
        stamp = str(row.get("t") or "")
        if not stamp:
            continue
        parsed = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
        result.append({
            "timestamp": parsed.isoformat(),
            "available_at": (parsed + timedelta(minutes=15)).isoformat(),
            "session": parsed.date().isoformat(),
            "open": row.get("o"), "high": row.get("h"), "low": row.get("l"),
            "close": row.get("c"), "volume": row.get("v"),
        })
    return result


def fetch_with_rate_handling(symbol: str, days: int) -> list[dict[str, Any]]:
    for attempt in range(3):
        try:
            return fetch_bars(symbol, days)
        except RuntimeError as exc:
            if "HTTP 429" not in str(exc) or attempt == 2:
                raise
            time.sleep(65)
    return []


def smoke_trade(bars: list[dict[str, Any]], side: str) -> dict[str, Any]:
    signal_index = len(bars) - 21
    price = float(bars[signal_index]["close"])
    if side == "short":
        stop, target = price * 1.02, price * 0.96
    else:
        stop, target = price * 0.98, price * 1.04
    return lab.simulate_trade(
        bars, signal_index, side, 100, stop_price=stop, target_price=target,
        max_holding_bars=20, costs=lab.ExecutionCosts(commission_bps=1.0, slippage_bps=5.0, max_volume_participation=0.02),
    )


def markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Technical Validation Run", "",
        f"- Status: `{report['status']}`",
        f"- Started: {report['started_at']}", f"- Finished: {report['finished_at']}",
        f"- Selected trader setups: {report['setup_count']}",
        f"- Unique symbols: {report['unique_symbol_count']}",
        f"- Symbols with usable 15Min history: {report['symbols_with_history']}",
        f"- Bias audit passed: {report['bias_pass_count']}",
        f"- Bias audit blocked/failed: {report['bias_problem_count']}",
        f"- Infrastructure smoke trades: {report['scorecard']['trade_count']}", "",
        "## Interpretation", "",
        "This run validates point-in-time data, causal indicators, walk-forward window construction and execution plumbing.",
        "Smoke trades exercise fills and costs; they do not claim that the traders' playbooks are profitable.",
        "Playbook performance remains in shadow until historical event rules and forward paper samples pass the promotion gates.", "",
        "## Gaps", "",
    ]
    if report["failures"]:
        for row in report["failures"][:30]:
            lines.append(f"- {row['ticker']}: {row['reason']}")
    else:
        lines.append("- No provider or validation failures.")
    return "\n".join(lines) + "\n"


def run(args: argparse.Namespace) -> int:
    started = datetime.now(timezone.utc).isoformat()
    state_path, checkpoint_path = Path(args.state), Path(args.checkpoint)
    state = {"schema": STATE_SCHEMA, "status": "running", "started_at": started, "research_only": True, "paper_submission": False}
    write_json(state_path, state)
    alpaca_paper_trader.load_dotenv(Path(args.env_file))
    setups = selected_setups([Path(args.yankos), Path(args.periklis)])
    by_ticker: dict[str, list[dict[str, Any]]] = {}
    for setup in setups:
        by_ticker.setdefault(setup["ticker"], []).append(setup)
    completed: dict[str, Any] = {}
    if checkpoint_path.exists():
        prior = read_json(checkpoint_path)
        completed = prior.get("symbols", {}) if isinstance(prior.get("symbols"), dict) else {}
    failures: list[dict[str, str]] = []
    trades: list[dict[str, Any]] = []
    bias_pass_count = 0
    symbols_with_history = 0
    for index, (ticker, ticker_setups) in enumerate(sorted(by_ticker.items()), 1):
        cached = completed.get(ticker)
        if cached:
            result = cached
        else:
            try:
                bars = fetch_with_rate_handling(ticker, args.history_days)
                if len(bars) < 100:
                    result = {"status": "blocked", "reason": f"insufficient_15min_history:{len(bars)}", "bar_count": len(bars)}
                else:
                    audit = lab.audit_indicator_causality(bars)
                    split_count = len(lab.walk_forward_splits(len(bars), 60, 20, 20, 20))
                    smoke = [smoke_trade(bars, str(setup["trade_direction"])) for setup in ticker_setups]
                    result = {"status": audit["status"], "bar_count": len(bars), "split_count": split_count,
                              "mismatch_count": len(audit.get("mismatches", [])), "smoke_trades": smoke}
            except Exception as exc:
                result = {"status": "failed", "reason": str(exc)[:300]}
            completed[ticker] = result
            write_json(checkpoint_path, {"schema": STATE_SCHEMA, "updated_at": datetime.now(timezone.utc).isoformat(), "symbols": completed})
        if int(result.get("bar_count") or 0) >= 100:
            symbols_with_history += 1
        if result.get("status") == "pass":
            bias_pass_count += 1
            trades.extend(result.get("smoke_trades", []))
        else:
            failures.append({"ticker": ticker, "reason": str(result.get("reason") or result.get("status"))})
        state.update({"processed_symbols": index, "total_symbols": len(by_ticker)})
        write_json(state_path, state)
        time.sleep(max(0.0, args.request_delay_seconds))
    report = {
        "schema": STATE_SCHEMA, "status": "completed" if not failures else "completed_review_required",
        "started_at": started, "finished_at": datetime.now(timezone.utc).isoformat(),
        "research_only": True, "paper_submission": False, "setup_count": len(setups),
        "unique_symbol_count": len(by_ticker), "symbols_with_history": symbols_with_history,
        "bias_pass_count": bias_pass_count, "bias_problem_count": len(by_ticker) - bias_pass_count,
        "scorecard": lab.summarize_trades(trades), "failures": failures,
        "interpretation": "infrastructure_validation_not_strategy_performance",
    }
    write_json(Path(args.output_json), report)
    Path(args.output_md).write_text(markdown(report), encoding="utf-8")
    write_json(state_path, report)
    return 0 if report["status"] == "completed" else 2


def main() -> int:
    parser = argparse.ArgumentParser(description="Run technical validation for current Yankos and Periklis candidate/watch symbols.")
    parser.add_argument("--yankos", default="output/yankos_strategy_latest.json")
    parser.add_argument("--periklis", default="output/periklis_strategy_latest.json")
    parser.add_argument("--env-file", default=".env")
    parser.add_argument("--history-days", type=int, default=60)
    parser.add_argument("--request-delay-seconds", type=float, default=0.4)
    parser.add_argument("--state", default="output/technical_validation_state_latest.json")
    parser.add_argument("--checkpoint", default="output/technical_validation_checkpoint_latest.json")
    parser.add_argument("--output-json", default="output/technical_validation_latest.json")
    parser.add_argument("--output-md", default="output/technical_validation_latest.md")
    return run(parser.parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
