"""Backtest lab: every daily strategy over five years of stored prices, judged against SPY. Research only.

Runs in the research environment (.venv-research, with vectorbt and quantstats). For each strategy in
``daily_strategies`` and each stored symbol, a signal is a session whose long or short score reaches the traders'
candidate threshold. Each signal is followed for 1, 3, 5, 10, 20 and 60 sessions from that session's close, in the
signal's direction, minus what SPY did over the same sessions. Reported per strategy, side and horizon: signals,
distinct days, share that beat SPY, median and mean excess return; plus, at the strategy's own horizon, an
equal-weight daily portfolio (one position per signal, held for the horizon) with Sharpe, max drawdown and CAGR
from quantstats. A cost of 0.10% per side is charged on the portfolio.

Nothing here touches the nightly run, any agent or any order path. The report says which strategies held up out of
sample; the owner decides which ones the traders use as candidates.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import time

import numpy as np
import pandas as pd

import daily_strategies as ds
import macro_environment as me
import price_history_store as phs

ROOT = Path(__file__).resolve().parent
HORIZONS = (1, 3, 5, 10, 20, 60)
# The owner's holding classes (day, week, month, quarter) in sessions. "Day" here is close to next close; an
# intraday hold needs intraday bars, which the store does not keep.
HOLDING_CLASSES = {"day": 1, "week": 5, "month": 20, "quarter": 60}
FIT_MIN_SIGNALS = 100     # fewer signals at a horizon: no verdict for that class
FIT_MIN_EDGE = 1.0        # percentage points better than a random entry, or the class is not called a fit
LONG_THRESHOLD, SHORT_THRESHOLD = 7.25, 7.35
COST_PER_SIDE = 0.001
MIN_PRICE = 3.0          # penny stocks distort backtests (spreads, halts)
MIN_DOLLAR_VOLUME = 2e6  # 20-day average dollar volume


def _forward(close: pd.Series, horizon: int) -> pd.Series:
    return close.shift(-horizon) / close - 1


def collect(directory: Path, symbols: list[str], strategies: list[str], benchmark: pd.Series,
            books: dict[tuple[str, str], dict[str, pd.Series]] | None = None,
            baseline: dict[int, list] | None = None, states: pd.Series | None = None,
            state_baseline: dict[tuple[int, str], list] | None = None) -> pd.DataFrame:
    """One row per signal: strategy, symbol, date, side, score and directional excess return per horizon.

    ``books`` collects, per strategy and side, the summed daily excess return of the open positions and their count
    (each signal opens a position at the next session and holds it for the strategy's horizon). ``states`` gives
    each signal the market state of its session; ``state_baseline`` collects the random entries per state."""
    bench = {h: _forward(benchmark, h) for h in HORIZONS}
    bench_daily = benchmark.pct_change()
    rows = []
    for count, symbol in enumerate(symbols, 1):
        history = phs.load(symbol, directory)
        if history is None or len(history) < ds.MIN_HISTORY:
            continue
        history = history[["Open", "High", "Low", "Close", "Volume"]].dropna()
        close = history["Close"].astype(float)
        liquid = (close >= MIN_PRICE) & ((close * history["Volume"]).rolling(20).mean() >= MIN_DOLLAR_VOLUME)
        forwards = {h: _forward(close, h) for h in HORIZONS}
        excess_daily = close.pct_change() - bench_daily.reindex(close.index)
        if baseline is not None:
            for h in HORIZONS:
                excess = ((forwards[h] - bench[h].reindex(close.index)) * 100)[liquid].dropna()
                baseline.setdefault(h, []).append(excess.to_numpy())
                if state_baseline is not None and states is not None:
                    labelled = states.reindex(excess.index)
                    for state in me.MARKET_STATES:
                        part = excess[labelled == state]
                        if len(part):
                            state_baseline.setdefault((h, state), []).append(part.to_numpy())
        for name in strategies:
            scores = ds.score_history(name, history)
            horizon = ds.STRATEGIES[name].horizon_days
            for side, threshold, sign in (("long", LONG_THRESHOLD, 1.0), ("short", SHORT_THRESHOLD, -1.0)):
                mask = (scores[side] >= threshold) & liquid
                if books is not None and mask.any():
                    opened = mask.astype(float)
                    # positions open that day; a day without a price for the symbol or SPY is left out of both the
                    # sum and the count, so a missing return never counts as a zero return
                    priced = excess_daily.notna()
                    open_count = opened.shift(1).rolling(horizon, min_periods=1).sum().where(priced).dropna()
                    contribution = (open_count * sign * excess_daily[priced])
                    book = books.setdefault((name, side), {"sum": pd.Series(dtype=float), "open": pd.Series(dtype=float),
                                                           "new": pd.Series(dtype=float)})
                    book["sum"] = book["sum"].add(contribution, fill_value=0.0)
                    book["open"] = book["open"].add(open_count, fill_value=0.0)
                    book["new"] = book["new"].add(opened.shift(1).fillna(0.0), fill_value=0.0)
                hits = scores.index[mask]
                for day in hits:
                    row = {"strategy": name, "symbol": symbol, "date": day, "side": side,
                           "score": float(scores.at[day, side]),
                           "market_state": None if states is None else states.get(day)}
                    for h in HORIZONS:
                        r, b = forwards[h].get(day), bench[h].get(day)
                        row[f"x{h}"] = None if pd.isna(r) or b is None or pd.isna(b) else sign * (r - b) * 100
                    rows.append(row)
        if count % 500 == 0:
            print(f"[LAB] {count}/{len(symbols)} symbols, {len(rows)} signals")
    return pd.DataFrame(rows)


LEGACY_PLAYBOOKS = {"yankos": ("support_bounce_swing", "controlled_breakout_swing"),
                    "periklis": ("early_accumulation_swing", "pre_run_breakout_confirmation")}


def collect_legacy(directory: Path, symbols: list[str], benchmark: pd.Series, step: int = 5,
                   baseline: dict[int, list] | None = None) -> pd.DataFrame:
    """The four original playbooks, called through the traders' own code on rows rebuilt for past sessions.

    Every ``step`` sessions the Babis technical features are recomputed from the history up to that session and
    the playbook function scores the row. Fundamentals are neutral (medium risk, fresh data, no flags), so this
    measures the technical part only. A candidate verdict is a signal, in its own direction. ``baseline`` collects
    the excess return of every liquid sampled session (the random entry the playbooks are compared with).
    """
    import json as _json
    import periklis_strategy_agent as periklis
    import us_equity_monitor as monitor
    import yankos_strategy_agent as yankos

    agents = {"yankos": yankos, "periklis": periklis}
    playbooks = {}
    for agent in agents:
        payload = _json.loads((ROOT / f"{agent}_playbooks.json").read_text(encoding="utf-8"))
        playbooks[agent] = [p for p in payload["playbooks"] if p["name"] in LEGACY_PLAYBOOKS[agent]]
    bench = {h: _forward(benchmark, h) for h in HORIZONS}
    rows = []
    for count, symbol in enumerate(symbols, 1):
        history = phs.load(symbol, directory)
        if history is None or len(history) < 260:
            continue
        history = history[["Open", "High", "Low", "Close", "Volume"]].dropna()
        close = history["Close"].astype(float)
        liquid = (close >= MIN_PRICE) & ((close * history["Volume"]).rolling(20).mean() >= MIN_DOLLAR_VOLUME)
        forwards = {h: _forward(close, h) for h in HORIZONS}
        for position in range(250, len(history), step):
            day = history.index[position]
            if not liquid.iloc[position]:
                continue
            if baseline is not None:
                for h in HORIZONS:
                    r, b = forwards[h].get(day), bench[h].get(day)
                    if not (pd.isna(r) or b is None or pd.isna(b)):
                        baseline.setdefault(h, []).append(np.array([(r - b) * 100]))
            metrics = monitor.moving_average_metrics(history.iloc[max(0, position - 299): position + 1])
            row = {"ticker": symbol, "company": symbol, "instrument_type": "common", "risk_level": "Medium",
                   "fresh_primary_data": True, "dilution_flags": [], "share_price": metrics.get("close"), **metrics}
            for agent, module in agents.items():
                for playbook in playbooks[agent]:
                    signal = module.evaluate_playbook(playbook, row)
                    if signal.get("signal_verdict") != "candidate" or signal.get("trade_direction") not in ("long", "short"):
                        continue
                    sign = 1.0 if signal["trade_direction"] == "long" else -1.0
                    entry = {"strategy": playbook["name"], "symbol": symbol, "date": day, "side": signal["trade_direction"],
                             "score": float(signal.get("signal_score") or 0.0)}
                    for h in HORIZONS:
                        r, b = forwards[h].get(day), bench[h].get(day)
                        entry[f"x{h}"] = None if pd.isna(r) or b is None or pd.isna(b) else sign * (r - b) * 100
                    rows.append(entry)
        if count % 100 == 0:
            print(f"[LEGACY] {count}/{len(symbols)} symbols, {len(rows)} signals")
    return pd.DataFrame(rows)


def summarize(signals: pd.DataFrame) -> list[dict]:
    out = []
    for (name, side), group in signals.groupby(["strategy", "side"]):
        entry = {"strategy": name, "side": side, "signals": int(len(group)),
                 "distinct_days": int(group["date"].nunique()),
                 "first": str(group["date"].min().date()), "last": str(group["date"].max().date()), "by_horizon": {}}
        for h in HORIZONS:
            values = group[f"x{h}"].dropna()
            if len(values):
                entry["by_horizon"][h] = {"n": int(len(values)), "beat_spy_pct": round(float((values > 0).mean() * 100), 1),
                                          "median_excess_pct": round(float(values.median()), 3),
                                          "mean_excess_pct": round(float(values.mean()), 3)}
        out.append(entry)
    return out


def random_baseline(baseline_values: dict[int, list]) -> dict:
    """Share of random entries (every liquid session) that beat SPY, per horizon and side."""
    baseline = {}
    for h, parts in baseline_values.items():
        values = np.concatenate(parts) if parts else np.array([])
        if len(values):
            baseline[h] = {"long": {"beat_spy_pct": round(float((values > 0).mean() * 100), 1),
                                    "median_excess_pct": round(float(np.median(values)), 3), "n": int(len(values))},
                           "short": {"beat_spy_pct": round(float((values < 0).mean() * 100), 1),
                                     "median_excess_pct": round(float(-np.median(values)), 3), "n": int(len(values))}}
    return baseline


def attach_edges(results: list[dict], baseline: dict) -> None:
    """The edge: how much better than a random entry in the same stocks and direction, per horizon."""
    for entry in results:
        for h, cell in entry["by_horizon"].items():
            base = (baseline.get(h) or baseline.get(str(h)) or {}).get(entry["side"])
            if base:
                cell["edge_vs_random_pct_points"] = round(cell["beat_spy_pct"] - base["beat_spy_pct"], 1)
                cell["edge_median_excess_pct"] = round(cell["median_excess_pct"] - base["median_excess_pct"], 3)


def horizon_fit(entry: dict) -> dict:
    """Edge per holding class and the class where the strategy holds up best (or none)."""
    classes = {}
    for name, h in HOLDING_CLASSES.items():
        cell = entry["by_horizon"].get(h) or entry["by_horizon"].get(str(h)) or {}
        enough = cell.get("n", 0) >= FIT_MIN_SIGNALS and cell.get("edge_vs_random_pct_points") is not None
        classes[name] = {"edge_vs_random_pct_points": cell.get("edge_vs_random_pct_points") if enough else None,
                         "beat_spy_pct": cell.get("beat_spy_pct") if enough else None, "n": cell.get("n", 0)}
    fits = [(v["edge_vs_random_pct_points"], k) for k, v in classes.items()
            if v["edge_vs_random_pct_points"] is not None and v["edge_vs_random_pct_points"] >= FIT_MIN_EDGE]
    return {"classes": classes, "best": max(fits)[1] if fits else None}


def by_market_state(signals: pd.DataFrame, state_baseline: dict[tuple[int, str], list]) -> list[dict]:
    """Per strategy and side, at the strategy's own horizon: how it did in each market state, against a random
    entry in the same stocks, direction and state."""
    out = []
    if not len(signals) or "market_state" not in signals:
        return out
    for (name, side), group in signals.groupby(["strategy", "side"]):
        h = ds.STRATEGIES[name].horizon_days if name in ds.STRATEGIES else 5
        cells = {}
        for state in me.MARKET_STATES:
            values = group.loc[group["market_state"] == state, f"x{h}"].dropna()
            base_parts = state_baseline.get((h, state)) or []
            base = np.concatenate(base_parts) if base_parts else np.array([])
            if not len(values) or not len(base):
                cells[state] = {"n": int(len(values))}
                continue
            beat = float((values > 0).mean() * 100)
            base_beat = float(((base > 0) if side == "long" else (base < 0)).mean() * 100)
            cells[state] = {"n": int(len(values)), "beat_spy_pct": round(beat, 1),
                            "edge_vs_random_pct_points": round(beat - base_beat, 1),
                            "median_excess_pct": round(float(values.median()), 3)}
        out.append({"strategy": name, "side": side, "horizon_days": h, "by_state": cells})
    return out


def render_states(rows: list[dict]) -> list[str]:
    lines = ["## Σε ποια κατάσταση αγοράς δουλεύει", "",
             "Πλεονέκτημα έναντι τυχαίας εισόδου στην ίδια κατάσταση (μονάδες %), στον ορίζοντα της στρατηγικής. "
             "Κατάσταση: S&P πάνω/κάτω από τον μέσο 200 ημερών, ήρεμη/ταραγμένη (μεταβλητότητα 20 ημερών κάτω/από 25%). "
             f"Λιγότερα από {FIT_MIN_SIGNALS} σήματα: —.", "",
             "| στρατηγική | πλευρά | " + " | ".join(me.MARKET_STATE_GR[s] for s in me.MARKET_STATES) + " |",
             "|---|---|" + "---|" * len(me.MARKET_STATES)]
    for row in rows:
        cells = []
        for state in me.MARKET_STATES:
            cell = row["by_state"].get(state) or {}
            edge = cell.get("edge_vs_random_pct_points")
            cells.append("—" if edge is None or cell.get("n", 0) < FIT_MIN_SIGNALS else f"{edge:+.1f} ({cell['n']})")
        lines.append(f"| {row['strategy']} | {row['side']} | " + " | ".join(cells) + " |")
    return lines


def render_horizons(results: list[dict]) -> list[str]:
    lines = ["## Ποια διάρκεια ταιριάζει", "",
             "Πλεονέκτημα έναντι τυχαίας εισόδου (μονάδες %) ανά διάρκεια θέσης. Ταιριάζει: η διάρκεια με το μεγαλύτερο "
             f"πλεονέκτημα, αν είναι τουλάχιστον {FIT_MIN_EDGE:g} μονάδα με {FIT_MIN_SIGNALS}+ σήματα. "
             "Ημέρα = από κλείσιμο σε κλείσιμο (όχι ενδοημερήσια).", "",
             "| στρατηγική | πλευρά | ημέρα | εβδομάδα | μήνας | τρίμηνο | ταιριάζει |", "|---|---|---|---|---|---|---|"]
    names = {"day": "ημέρα", "week": "εβδομάδα", "month": "μήνας", "quarter": "τρίμηνο", None: "καμία"}
    for entry in results:
        fit = entry.get("horizon_fit") or horizon_fit(entry)
        cells = ["—" if v["edge_vs_random_pct_points"] is None else f"{v['edge_vs_random_pct_points']:+.1f}"
                 for v in fit["classes"].values()]
        lines.append(f"| {entry['strategy']} | {entry['side']} | " + " | ".join(cells) + f" | {names[fit['best']]} |")
    return lines


def portfolio_stats(book: dict[str, pd.Series] | None) -> dict | None:
    """Equal-weight portfolio of the open positions: each day's return is the average daily excess return of the
    positions open that day, minus 0.10% per side spread over the positions opened that day."""
    try:
        import quantstats as qs
    except ImportError:
        return None
    if not book or book["open"].sum() < 30:
        return None
    open_count = book["open"].where(book["open"] > 0)
    daily = (book["sum"] - book["new"] * 2 * COST_PER_SIDE) / open_count
    # a short can lose more than its stake in one session (a squeeze); the portfolio cannot lose more than 100%
    clipped = int((daily < -0.95).sum())
    daily = daily.dropna().clip(lower=-0.95)
    if len(daily) < 30:
        return None
    return {"days": int(len(daily)), "sharpe": round(float(qs.stats.sharpe(daily)), 3),
            "max_drawdown_pct": round(float(qs.stats.max_drawdown(daily) * 100), 2),
            "cagr_pct": round(float(qs.stats.cagr(daily) * 100), 2),
            "days_clipped_at_minus_95pct": clipped,
            "note": "excess over SPY, net of 0.10% per side, equal weight across open positions"}


def render(report: dict) -> str:
    lines = ["# Εργαστήριο στρατηγικών · backtest 5 ετών", "",
             f"Σύμβολα: {report['symbols']} · σήματα: {report['signals']} · έναντι SPY · κόστος 0,10% ανά πλευρά στο χαρτοφυλάκιο.",
             "Μόνο έρευνα. Προσοχή: μόνο σημερινές εισηγμένες εταιρείες (survivorship bias) και καμία θεμελιώδης πύλη.", "",
             "Πλεονέκτημα = πόσες μονάδες % συχνότερα νίκησε το SPY από μια τυχαία είσοδο στις ίδιες μετοχές και κατεύθυνση.", "",
             "| στρατηγική | πλευρά | σήματα | ημέρες | ορίζοντας | νίκησε το SPY % | πλεονέκτημα (μονάδες) | διάμεση υπέρβαση % | Sharpe | max DD % |",
             "|---|---|---|---|---|---|---|---|---|---|"]
    for entry in report["results"]:
        h = ds.STRATEGIES[entry["strategy"]].horizon_days
        cell = entry["by_horizon"].get(h) or entry["by_horizon"].get(str(h)) or {}
        pf = entry.get("portfolio") or {}
        lines.append(f"| {entry['strategy']} | {entry['side']} | {entry['signals']} | {entry['distinct_days']} | {h} | "
                     f"{cell.get('beat_spy_pct', '—')} | {cell.get('edge_vs_random_pct_points', '—')} | "
                     f"{cell.get('median_excess_pct', '—')} | "
                     f"{pf.get('sharpe', '—')} | {pf.get('max_drawdown_pct', '—')} |")
    lines += [""] + render_horizons(report["results"])
    if report.get("by_market_state"):
        lines += [""] + render_states(report["by_market_state"])
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dir", type=Path, default=phs.DEFAULT_DIR)
    parser.add_argument("--out", type=Path, default=ROOT / "output" / "strategy_lab")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--strategies", nargs="*", default=list(ds.STRATEGIES))
    parser.add_argument("--legacy", action="store_true",
                        help="backtest the four original playbooks on the shortlist (every 5th session)")
    parser.add_argument("--shortlist", type=Path, default=ROOT / "output" / "babis_shortlist_latest.json")
    args = parser.parse_args(argv)
    if args.legacy:
        return main_legacy(args)
    benchmark = phs.load("SPY", args.dir)
    if benchmark is None:
        raise SystemExit("SPY history is missing from the price store")
    symbols = sorted(p.name[:-7] for p in args.dir.glob("*.csv.gz") if p.name[:-7] not in phs.BENCHMARKS)
    if args.limit:
        symbols = symbols[: args.limit]
    started = time.perf_counter()
    books: dict[tuple[str, str], dict[str, pd.Series]] = {}
    baseline_values: dict[int, list] = {}
    state_baseline: dict[tuple[int, str], list] = {}
    states = me.market_state_history(benchmark)
    signals = collect(args.dir, symbols, args.strategies, benchmark["Close"].astype(float), books, baseline_values,
                      states=states, state_baseline=state_baseline)
    results = summarize(signals) if len(signals) else []
    baseline = random_baseline(baseline_values)
    attach_edges(results, baseline)
    for entry in results:
        entry["portfolio"] = portfolio_stats(books.get((entry["strategy"], entry["side"])))
        entry["horizon_fit"] = horizon_fit(entry)
    report = {"schema": "ecosystem.strategy_lab.v1", "generated_at": datetime.now(timezone.utc).isoformat(),
              "symbols": len(symbols), "signals": int(len(signals)), "seconds": round(time.perf_counter() - started, 1),
              "baseline_random_entry": baseline,
              "by_market_state": by_market_state(signals, state_baseline),
              "market_state_days": {state: int((states == state).sum()) for state in me.MARKET_STATES},
              "thresholds": {"long": LONG_THRESHOLD, "short": SHORT_THRESHOLD}, "filters": {
                  "min_price": MIN_PRICE, "min_dollar_volume_20d": MIN_DOLLAR_VOLUME}, "results": results}
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "strategy_lab_latest.json").write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    (args.out / "strategy_lab_latest.md").write_text(render(report), encoding="utf-8")
    if len(signals):
        signals.to_csv(args.out / "strategy_lab_signals_latest.csv.gz", index=False, compression="gzip")
    print(json.dumps({"symbols": len(symbols), "signals": int(len(signals)), "seconds": report["seconds"]}))
    return 0


def main_legacy(args: argparse.Namespace) -> int:
    benchmark = phs.load("SPY", args.dir)
    if benchmark is None:
        raise SystemExit("SPY history is missing from the price store")
    rows = json.loads(args.shortlist.read_text(encoding="utf-8-sig")).get("shortlist") or []
    symbols = sorted({str(r.get("ticker") or "").upper() for r in rows} - {""})
    if args.limit:
        symbols = symbols[: args.limit]
    started = time.perf_counter()
    baseline_values: dict[int, list] = {}
    signals = collect_legacy(args.dir, symbols, benchmark["Close"].astype(float), baseline=baseline_values)
    results = summarize(signals) if len(signals) else []
    baseline = random_baseline(baseline_values)
    attach_edges(results, baseline)
    for entry in results:
        entry["horizon_fit"] = horizon_fit(entry)
    report = {"schema": "ecosystem.strategy_lab_legacy.v1", "generated_at": datetime.now(timezone.utc).isoformat(),
              "symbols": len(symbols), "signals": int(len(signals)), "seconds": round(time.perf_counter() - started, 1),
              "note": "technical part only (neutral fundamentals), every 5th session, current shortlist",
              "baseline_random_entry": baseline, "results": results}
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "strategy_lab_legacy_latest.json").write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    if len(signals):  # the exit lab replays these too
        signals.to_csv(args.out / "strategy_lab_legacy_signals_latest.csv.gz", index=False, compression="gzip")
    (args.out / "strategy_lab_legacy_latest.md").write_text(
        "\n".join([f"# Εργαστήριο στρατηγικών · τα 4 αρχικά playbooks ({len(symbols)} σύμβολα, {len(signals)} σήματα)", "",
                   "Μόνο το τεχνικό κομμάτι (ουδέτερα θεμελιώδη), κάθε 5η συνεδρίαση, σημερινό shortlist.", ""]
                  + render_horizons(results)) + "\n", encoding="utf-8")
    print(json.dumps({"symbols": len(symbols), "signals": int(len(signals)), "seconds": report["seconds"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
