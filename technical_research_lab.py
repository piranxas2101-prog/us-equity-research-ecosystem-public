from __future__ import annotations

import argparse
import json
import math
import random
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from statistics import mean, median, pstdev
from typing import Any, Iterable


SCHEMA = "ecosystem.technical_research_lab.v1"


def number(value: Any) -> float | None:
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (TypeError, ValueError):
        return None


def timestamp(value: Any) -> datetime:
    text = str(value or "").replace("Z", "+00:00")
    result = datetime.fromisoformat(text)
    if result.tzinfo is None:
        raise ValueError(f"Timestamp must contain a timezone: {value!r}")
    return result


def validate_point_in_time_bars(bars: list[dict[str, Any]]) -> list[str]:
    errors: list[str] = []
    previous_time: datetime | None = None
    for index, bar in enumerate(bars):
        try:
            bar_time = timestamp(bar.get("timestamp"))
            available_at = timestamp(bar.get("available_at"))
        except (TypeError, ValueError) as exc:
            errors.append(f"bar[{index}]: {exc}")
            continue
        if available_at < bar_time:
            errors.append(f"bar[{index}]: available_at precedes timestamp")
        if previous_time is not None and bar_time <= previous_time:
            errors.append(f"bar[{index}]: bars are not strictly chronological")
        previous_time = bar_time
        for field in ("open", "high", "low", "close", "volume"):
            value = number(bar.get(field))
            if value is None or value < 0:
                errors.append(f"bar[{index}]: invalid {field}")
        high, low = number(bar.get("high")), number(bar.get("low"))
        open_, close = number(bar.get("open")), number(bar.get("close"))
        if None not in (high, low, open_, close) and (high < max(open_, close) or low > min(open_, close)):
            errors.append(f"bar[{index}]: inconsistent OHLC range")
    return errors


def _ema(values: list[float], period: int) -> list[float | None]:
    if period < 1:
        raise ValueError("period must be positive")
    alpha = 2.0 / (period + 1.0)
    result: list[float | None] = []
    current: float | None = None
    for index, value in enumerate(values):
        current = value if current is None else alpha * value + (1.0 - alpha) * current
        result.append(current if index + 1 >= period else None)
    return result


def _rsi(values: list[float], period: int = 14) -> list[float | None]:
    result: list[float | None] = [None] * len(values)
    gains: list[float] = []
    losses: list[float] = []
    for index in range(1, len(values)):
        change = values[index] - values[index - 1]
        gains.append(max(change, 0.0))
        losses.append(max(-change, 0.0))
        if index >= period:
            avg_gain = mean(gains[index - period:index])
            avg_loss = mean(losses[index - period:index])
            result[index] = 100.0 if avg_loss == 0 else 100.0 - 100.0 / (1.0 + avg_gain / avg_loss)
    return result


def compute_indicators(bars: list[dict[str, Any]]) -> list[dict[str, float | None]]:
    closes = [float(bar["close"]) for bar in bars]
    ema12, ema26 = _ema(closes, 12), _ema(closes, 26)
    rsi14 = _rsi(closes, 14)
    rows: list[dict[str, float | None]] = []
    cumulative_pv = 0.0
    cumulative_volume = 0.0
    true_ranges: list[float] = []
    session = None
    for index, bar in enumerate(bars):
        current_session = str(bar.get("session") or str(bar["timestamp"])[:10])
        if current_session != session:
            cumulative_pv = cumulative_volume = 0.0
            session = current_session
        typical = (float(bar["high"]) + float(bar["low"]) + float(bar["close"])) / 3.0
        volume = float(bar["volume"])
        cumulative_pv += typical * volume
        cumulative_volume += volume
        prior_close = closes[index - 1] if index else closes[index]
        true_ranges.append(max(float(bar["high"]) - float(bar["low"]), abs(float(bar["high"]) - prior_close), abs(float(bar["low"]) - prior_close)))
        atr14 = mean(true_ranges[-14:]) if len(true_ranges) >= 14 else None
        macd = None if ema12[index] is None or ema26[index] is None else ema12[index] - ema26[index]
        rows.append({
            "ema_12": ema12[index], "ema_26": ema26[index], "macd": macd,
            "rsi_14": rsi14[index], "atr_14": atr14,
            "vwap": cumulative_pv / cumulative_volume if cumulative_volume > 0 else None,
        })
    return rows


def audit_indicator_causality(bars: list[dict[str, Any]], tolerance: float = 1e-10) -> dict[str, Any]:
    errors = validate_point_in_time_bars(bars)
    if errors:
        return {"status": "blocked", "errors": errors, "mismatches": []}
    full = compute_indicators(bars)
    mismatches: list[dict[str, Any]] = []
    for end in range(1, len(bars) + 1):
        incremental = compute_indicators(bars[:end])[-1]
        for field, actual in incremental.items():
            expected = full[end - 1][field]
            if expected is None or actual is None:
                equal = expected is actual
            else:
                equal = abs(float(expected) - float(actual)) <= tolerance
            if not equal:
                mismatches.append({"bar_index": end - 1, "indicator": field, "full": expected, "incremental": actual})
    return {"status": "pass" if not mismatches else "fail", "errors": [], "mismatches": mismatches}


def classify_regime(bars: list[dict[str, Any]], index: int) -> list[str]:
    closes = [float(row["close"]) for row in bars[: index + 1]]
    labels: set[str] = set()
    if len(closes) >= 21:
        ret20 = closes[-1] / closes[-21] - 1.0
        labels.add("trend_up" if ret20 > 0.04 else "trend_down" if ret20 < -0.04 else "range")
    else:
        labels.add("insufficient_trend_history")
    returns = [closes[i] / closes[i - 1] - 1.0 for i in range(1, len(closes))]
    if len(returns) >= 20:
        recent = returns[-20:]
        variance = mean([(value - mean(recent)) ** 2 for value in recent])
        labels.add("high_volatility" if math.sqrt(variance) * math.sqrt(252) > 0.55 else "normal_volatility")
    volume = float(bars[index]["volume"])
    history = [float(row["volume"]) for row in bars[max(0, index - 20):index]]
    if history:
        labels.add("low_liquidity" if volume < median(history) * 0.35 else "normal_liquidity")
    return sorted(labels)


@dataclass(frozen=True)
class ExecutionCosts:
    commission_bps: float = 1.0
    slippage_bps: float = 5.0
    max_volume_participation: float = 0.02


def simulate_trade(
    bars: list[dict[str, Any]], signal_index: int, side: str, quantity: int,
    stop_price: float | None = None, target_price: float | None = None,
    max_holding_bars: int = 20, costs: ExecutionCosts | None = None,
) -> dict[str, Any]:
    if costs is None:
        costs = ExecutionCosts()
    if side not in {"long", "short"}:
        raise ValueError("side must be long or short")
    if signal_index + 1 >= len(bars) or quantity <= 0:
        return {"status": "unfilled", "reason": "no_next_bar_or_invalid_quantity"}
    entry_bar = bars[signal_index + 1]
    max_fill = int(float(entry_bar["volume"]) * costs.max_volume_participation)
    filled = min(quantity, max_fill)
    if filled <= 0:
        return {"status": "unfilled", "reason": "volume_participation_limit"}
    direction = 1.0 if side == "long" else -1.0
    raw_entry = float(entry_bar["open"])
    entry = raw_entry * (1.0 + direction * costs.slippage_bps / 10000.0)
    exit_price = float(bars[min(len(bars) - 1, signal_index + max_holding_bars)]["close"])
    exit_reason = "time_exit"
    exit_index = min(len(bars) - 1, signal_index + max_holding_bars)
    mfe = mae = 0.0
    for index in range(signal_index + 1, exit_index + 1):
        bar = bars[index]
        high, low = float(bar["high"]), float(bar["low"])
        favorable = (high / entry - 1.0) if side == "long" else (entry / low - 1.0)
        adverse = (low / entry - 1.0) if side == "long" else (entry / high - 1.0)
        mfe, mae = max(mfe, favorable), min(mae, adverse)
        stop_hit = stop_price is not None and (low <= stop_price if side == "long" else high >= stop_price)
        target_hit = target_price is not None and (high >= target_price if side == "long" else low <= target_price)
        if stop_hit:  # Conservative when stop and target occur in the same bar.
            exit_price, exit_reason, exit_index = float(stop_price), "stop", index
            break
        if target_hit:
            exit_price, exit_reason, exit_index = float(target_price), "target", index
            break
    exit_price *= 1.0 - direction * costs.slippage_bps / 10000.0
    gross = direction * (exit_price - entry) * filled
    commissions = (entry + exit_price) * filled * costs.commission_bps / 10000.0
    net = gross - commissions
    return {
        "status": "filled", "side": side, "requested_quantity": quantity, "filled_quantity": filled,
        "entry_index": signal_index + 1, "exit_index": exit_index, "entry_price": round(entry, 6),
        "exit_price": round(exit_price, 6), "exit_reason": exit_reason, "gross_pnl": round(gross, 4),
        "costs": round(commissions, 4), "net_pnl": round(net, 4),
        "return_pct": round(net / (entry * filled) * 100.0, 4),
        "mfe_pct": round(mfe * 100.0, 4), "mae_pct": round(mae * 100.0, 4),
        "regimes": classify_regime(bars, signal_index),
    }


def walk_forward_splits(size: int, train: int, validation: int, test: int, step: int | None = None) -> list[dict[str, list[int]]]:
    if min(size, train, validation, test) <= 0:
        raise ValueError("all window sizes must be positive")
    stride = step or test
    result: list[dict[str, list[int]]] = []
    start = 0
    while start + train + validation + test <= size:
        train_end, validation_end = start + train, start + train + validation
        result.append({"train": [start, train_end], "validation": [train_end, validation_end], "test": [validation_end, validation_end + test]})
        start += stride
    return result


def summarize_trades(trades: Iterable[dict[str, Any]]) -> dict[str, Any]:
    filled = [trade for trade in trades if trade.get("status") == "filled"]
    returns = [float(trade["return_pct"]) for trade in filled]
    wins = [value for value in returns if value > 0]
    losses = [value for value in returns if value < 0]
    gross_gain, gross_loss = sum(wins), abs(sum(losses))
    equity = peak = 1.0
    max_drawdown = 0.0
    for value in returns:
        equity *= 1.0 + value / 100.0
        peak = max(peak, equity)
        max_drawdown = min(max_drawdown, (equity / peak - 1.0) * 100.0)
    regime_groups: dict[str, list[float]] = {}
    for trade in filled:
        for regime in trade.get("regimes", []):
            regime_groups.setdefault(str(regime), []).append(float(trade["return_pct"]))
    downside = [min(value, 0.0) for value in returns]
    volatility = pstdev(returns) if len(returns) > 1 else 0.0
    downside_deviation = math.sqrt(mean([value * value for value in downside])) if downside else 0.0
    ci_low = ci_high = None
    if returns:
        rng = random.Random(0)
        bootstrap_means = sorted(mean(rng.choice(returns) for _ in returns) for _ in range(1000))
        ci_low = round(bootstrap_means[int(len(bootstrap_means) * 0.025)], 4)
        ci_high = round(bootstrap_means[int(len(bootstrap_means) * 0.975)], 4)
    return {
        "trade_count": len(filled),
        "win_rate_pct": round(len(wins) / len(returns) * 100.0, 2) if returns else None,
        "net_expectancy_pct": round(mean(returns), 4) if returns else None,
        "median_return_pct": round(median(returns), 4) if returns else None,
        "profit_factor": round(gross_gain / gross_loss, 4) if gross_loss else None,
        "max_drawdown_pct": round(max_drawdown, 4) if returns else None,
        "trade_level_sharpe": round(mean(returns) / volatility, 4) if volatility else None,
        "trade_level_sortino": round(mean(returns) / downside_deviation, 4) if downside_deviation else None,
        "expectancy_bootstrap_95pct": [ci_low, ci_high],
        "average_mfe_pct": round(mean(float(trade["mfe_pct"]) for trade in filled), 4) if filled else None,
        "average_mae_pct": round(mean(float(trade["mae_pct"]) for trade in filled), 4) if filled else None,
        "total_costs": round(sum(float(trade["costs"]) for trade in filled), 4),
        "regime_scorecards": {
            regime: {"count": len(values), "expectancy_pct": round(mean(values), 4),
                     "win_rate_pct": round(sum(value > 0 for value in values) / len(values) * 100.0, 2)}
            for regime, values in sorted(regime_groups.items())
        },
    }


def evaluate_promotion(metrics: dict[str, Any]) -> dict[str, Any]:
    expectancy = number(metrics.get("net_expectancy_pct")) or 0.0
    profit_factor = number(metrics.get("profit_factor")) or 0.0
    max_drawdown = number(metrics.get("max_drawdown_pct"))
    gates = {
        "bias_audit_passed": metrics.get("bias_audit_status") == "pass",
        "out_of_sample_trades_at_least_50": int(metrics.get("out_of_sample_trades") or 0) >= 50,
        "positive_net_expectancy": expectancy > 0,
        "profit_factor_at_least_1_2": profit_factor >= 1.2,
        "max_drawdown_at_most_20pct": max_drawdown is not None and abs(max_drawdown) <= 20.0,
        "paper_trades_at_least_20": int(metrics.get("paper_trades") or 0) >= 20,
    }
    return {"status": "paper_candidate" if all(gates.values()) else "shadow", "gates": gates, "missing_gates": [key for key, passed in gates.items() if not passed]}


def build_report(payload: dict[str, Any]) -> dict[str, Any]:
    bars = payload.get("bars") if isinstance(payload.get("bars"), list) else []
    audit = audit_indicator_causality(bars)
    trades = [simulate_trade(bars, **item) for item in payload.get("trades", [])] if audit["status"] == "pass" else []
    scorecard = summarize_trades(trades)
    supplied_metrics = dict(payload.get("promotion_metrics", {}))
    supplied_metrics.setdefault("bias_audit_status", audit["status"])
    supplied_metrics.setdefault("out_of_sample_trades", scorecard["trade_count"])
    supplied_metrics.setdefault("net_expectancy_pct", scorecard["net_expectancy_pct"])
    supplied_metrics.setdefault("profit_factor", scorecard["profit_factor"])
    supplied_metrics.setdefault("max_drawdown_pct", scorecard["max_drawdown_pct"])
    return {
        "schema": SCHEMA, "bias_audit": audit,
        "walk_forward_splits": walk_forward_splits(len(bars), **payload["walk_forward"]) if payload.get("walk_forward") else [],
        "trades": trades, "scorecard": scorecard, "promotion": evaluate_promotion(supplied_metrics),
        "execution_backtest": True,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Point-in-time technical research, bias audit and execution simulation.")
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    report = build_report(json.loads(Path(args.input).read_text(encoding="utf-8-sig")))
    target = Path(args.output); target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    print(f"[TECHNICAL-LAB] {report['bias_audit']['status']} -> {target}")


if __name__ == "__main__":
    main()
