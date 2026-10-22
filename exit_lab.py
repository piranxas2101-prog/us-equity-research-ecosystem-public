"""Exit lab: which exit rule and which holding period suit each strategy (ADR-001, step 1). Research only.

Owner's decision 2026-10-16: test the exits before changing any, and include how long a trade stays open (the
holding classes day / week / month / quarter = 1 / 5 / 20 / 60 sessions). Every exit rule is replayed in every
class on the daily bars of the price store, for the five-year signals of the strategy lab
(``output/strategy_lab/strategy_lab_signals_latest.csv.gz``; a random sample of at most ``MAX_PER_GROUP`` per
strategy and side). Entry at the signal session's close, as in the lab.

Exit rules (each also ends at the class's last session - the time stop):

* ``fixed``     - hold to the end of the class (the lab's reference).
* ``atr_bracket`` - today's rule: stop 1.1 x ATR(14) clamped to 1.5-7.5%, take-profit 1.75 x stop clamped to 2.5-12%.
* ``mr_signal`` - mean-reversion exit: out when the close crosses back over the 5-day average; disaster stop 3 x ATR.
* ``trend_trailing`` - chandelier: out when the low touches 3 x ATR below the highest high since entry; no target.

A session that opens beyond a stop exits at its open (a gap is not filled at the stop). A session touching stop and
target counts as the stop. Judged per strategy, side and class: mean excess over SPY for the same days, share beating
SPY, mean days held, and the difference to ``fixed`` paired per signal with a 95% month-block bootstrap interval;
all comparisons together pass a Benjamini-Hochberg control (q <= 0.10) before a rule is called better or worse.

Random-entry baseline: every signal is paired with one random session of the same stock, replayed with the same
rule, class and side. The edge is the paired difference (signal minus random entry), so a stock that simply fell
for five years gives no edge to a short. Judged with the same bootstrap and a separate FDR control, plus per-year
stability (years with at least ``MIN_YEAR_PAIRS`` pairs, and how many of them have a positive edge).
The four original playbooks join when ``strategy_lab.py --legacy`` has saved their signals.

``--horizon-curve`` (owner 2026-10-23: the agents decide how long each position stays open, any length): the
``fixed`` hold on a fine grid of holding periods (``HORIZONS``, 1-60 sessions) with the same random-entry edge,
FDR and per-year stability, written to ``horizon_curve_latest.json`` - the evidence ``holding_horizon.py`` reads.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import time
from typing import Any

import numpy as np
import pandas as pd

import price_history_store as phs

ROOT = Path(__file__).resolve().parent
CLASSES = {"day": 1, "week": 5, "month": 20, "quarter": 60}
HORIZONS = {f"s{n}": n for n in (1, 2, 3, 5, 7, 10, 15, 20, 30, 40, 60)}
RULES = ("fixed", "atr_bracket", "mr_signal", "trend_trailing", "fixed_shock")
FAMILY = {"rsi2_mean_reversion": "mean_reversion", "mean_reversion_overextension": "mean_reversion",
          "gap_fade": "mean_reversion", "failed_breakdown_reclaim": "mean_reversion",
          "failed_breakout_reversal": "mean_reversion", "momentum_12_1": "trend", "high_52w_breakout": "trend",
          "volatility_compression_breakout": "trend", "gap_continuation": "trend",
          "support_bounce_swing": "mean_reversion", "controlled_breakout_swing": "trend",
          "early_accumulation_swing": "trend", "pre_run_breakout_confirmation": "trend"}
MAX_PER_GROUP = 4000
MIN_YEAR_PAIRS = 30
BOOT = 1000
FDR_Q = 0.10
rng = np.random.default_rng(11)


def _atr_pct(frame: pd.DataFrame) -> np.ndarray:
    high, low, close = frame["High"].to_numpy(float), frame["Low"].to_numpy(float), frame["Close"].to_numpy(float)
    prev = np.r_[np.nan, close[:-1]]
    tr = np.nanmax(np.vstack([high - low, np.abs(high - prev), np.abs(low - prev)]), axis=0)
    return pd.Series(tr).rolling(14).mean().to_numpy() / close * 100


def simulate(o: np.ndarray, h: np.ndarray, low: np.ndarray, c: np.ndarray, sma5: np.ndarray, atr: float,
             i: int, horizon: int, rule: str, long: bool, shocks: dict[int, str] | None = None
             ) -> tuple[float, int] | None:
    """(exit price, sessions held) of one trade entered at close i, or None without enough future sessions.
    ``fixed_shock``: the fixed hold, left at the next open after a market shock against the position (owner
    2026-10-22: every position is watched for an abrupt change of mood, whatever its holding class)."""
    end = i + horizon
    if end >= len(c) or not np.isfinite(atr) or atr <= 0:
        return None
    entry = c[i]
    if rule == "fixed":
        return c[end], horizon
    if rule == "fixed_shock":
        adverse = {"down", "volatile"} if long else {"up", "volatile"}
        for j in range(i + 1, end):
            if (shocks or {}).get(j) in adverse:
                return o[j + 1], j + 1 - i
        return c[end], horizon
    stop_pct = min(max(1.1 * atr, 1.5), 7.5) / 100 if rule == "atr_bracket" else 3 * atr / 100
    take_pct = min(max(1.75 * stop_pct * 100, 2.5), 12.0) / 100 if rule == "atr_bracket" else None
    extreme = entry
    for j in range(i + 1, end + 1):
        if rule == "trend_trailing":
            stop_level = extreme * (1 - stop_pct) if long else extreme * (1 + stop_pct)
        else:
            stop_level = entry * (1 - stop_pct) if long else entry * (1 + stop_pct)
        gapped = o[j] <= stop_level if long else o[j] >= stop_level
        touched = low[j] <= stop_level if long else h[j] >= stop_level
        if gapped:
            return o[j], j - i
        if touched:
            return stop_level, j - i
        if take_pct is not None:
            target = entry * (1 + take_pct) if long else entry * (1 - take_pct)
            if (h[j] >= target) if long else (low[j] <= target):
                return target, j - i
        if rule == "mr_signal" and np.isfinite(sma5[j]) and ((c[j] > sma5[j]) if long else (c[j] < sma5[j])):
            return c[j], j - i
        if rule == "trend_trailing":
            extreme = max(extreme, h[j]) if long else min(extreme, low[j])
    return c[end], horizon


def collect(signals: pd.DataFrame, directory: Path, seed: int = 5, classes: dict[str, int] | None = None,
            rules: tuple[str, ...] = RULES) -> pd.DataFrame:
    """Every signal and its random entry (same stock, one random session) replayed for every class and rule."""
    spy_frame = phs.load("SPY", directory)
    spy = spy_frame["Close"].astype(float)
    import thesis_exit as te

    market = te.market_shocks(spy_frame)  # date -> down / up / volatile
    draw = np.random.default_rng(seed)
    classes = classes or CLASSES
    rows = []
    for count, (symbol, group) in enumerate(signals.groupby("symbol"), 1):
        frame = phs.load(symbol, directory)
        if frame is None or len(frame) < 80:
            continue
        frame = frame[["Open", "High", "Low", "Close"]].dropna()
        index = {d: k for k, d in enumerate(frame.index)}
        o, h, low, c = (frame[k].to_numpy(float) for k in ("Open", "High", "Low", "Close"))
        sma5 = pd.Series(c).rolling(5).mean().to_numpy()
        atr = _atr_pct(frame)
        spy_aligned = spy.reindex(frame.index).to_numpy(float)
        shocks = {k: market[d.date()] for k, d in enumerate(frame.index) if d.date() in market}
        valid = np.flatnonzero(np.isfinite(atr) & (atr > 0))
        for signal in group.itertuples(index=False):
            i = index.get(signal.date)
            if i is None:
                continue
            long = signal.side == "long"
            entries = [("signal", i)] + ([("random", int(draw.choice(valid)))] if len(valid) else [])
            for kind, k in entries:
                for cls, horizon in classes.items():
                    for rule in rules:
                        out = simulate(o, h, low, c, sma5, atr[k], k, horizon, rule, long, shocks)
                        if out is None:
                            continue
                        price, held = out
                        result = (1.0 if long else -1.0) * (price / c[k] - 1) * 100
                        s0, s1 = spy_aligned[k], spy_aligned[k + held]
                        if not (np.isfinite(s0) and np.isfinite(s1)):
                            continue
                        bench = (s1 / s0 - 1) * 100
                        rows.append((signal.strategy, signal.side, cls, rule, signal.date, signal.symbol, result,
                                     result - (bench if long else -bench), held, kind))
        if count % 500 == 0:
            print(f"[EXIT-LAB] {count} symbols, {len(rows)} replays", flush=True)
    return pd.DataFrame(rows, columns=["strategy", "side", "class", "rule", "date", "symbol", "result", "excess", "held",
                                       "kind"])


def _bh(pvalues: list[float]) -> list[float]:
    order = sorted(range(len(pvalues)), key=lambda k: pvalues[k])
    q, running = [1.0] * len(pvalues), 1.0
    for rank in range(len(order) - 1, -1, -1):
        k = order[rank]
        running = min(running, pvalues[k] * len(pvalues) / (rank + 1))
        q[k] = running
    return q


def _block_test(diffs: np.ndarray, months: np.ndarray) -> dict[str, Any] | None:
    """Mean of paired differences, 95% month-block bootstrap interval and a two-sided p-value (>= 12 months)."""
    by_month = pd.DataFrame({"d": diffs, "m": months}).groupby("m")["d"].agg(["sum", "count"])
    sums, counts = by_month["sum"].to_numpy(), by_month["count"].to_numpy()
    if len(sums) < 12:
        return None
    idx = rng.integers(0, len(sums), size=(BOOT, len(sums)))
    boot = sums[idx].sum(1) / counts[idx].sum(1)
    low, high = np.percentile(boot, [2.5, 97.5])
    mean = float(sums.sum() / counts.sum())
    se = float(np.std(boot)) or 1e-9
    return {"mean": round(mean, 3), "ci": [round(float(low), 3), round(float(high), 3)],
            "p": float(math.erfc(abs(mean / se) / math.sqrt(2)))}


def _years(diffs: pd.Series) -> dict[str, Any]:
    """Mean edge per signal year (years with at least MIN_YEAR_PAIRS pairs) and how many are positive."""
    years = pd.to_datetime(diffs.index.get_level_values("date")).year
    stats = diffs.groupby(years).agg(["mean", "count"])
    stats = stats[stats["count"] >= MIN_YEAR_PAIRS]
    return {"by_year": {int(y): round(float(m), 3) for y, m in stats["mean"].items()},
            "years": int(len(stats)), "years_positive": int((stats["mean"] > 0).sum())}


def _bh_verdicts(entries: list[dict[str, Any]], p_key: str, mean_key: str, q_key: str, verdict_key: str,
                 words: tuple[str, str, str]) -> None:
    tested = [e for e in entries if p_key in e]
    for e, q in zip(tested, _bh([e[p_key] for e in tested])):
        e[q_key] = round(q, 4)
        e[verdict_key] = (words[0] if q <= FDR_Q and e[mean_key] > 0 else
                          words[1] if q <= FDR_Q and e[mean_key] < 0 else words[2])
        e[p_key] = round(e[p_key], 5)


def summarize(replays: pd.DataFrame) -> list[dict[str, Any]]:
    out = []
    replays = replays.assign(month=pd.to_datetime(replays["date"]).dt.to_period("M"))
    if "kind" not in replays:
        replays = replays.assign(kind="signal")
    randoms = {key: cell.drop_duplicates(["symbol", "date"]).set_index(["symbol", "date"])["excess"]
               for key, cell in replays[replays["kind"] == "random"].groupby(["strategy", "side", "class", "rule"])}
    replays = replays[replays["kind"] == "signal"]
    for (strategy, side, cls), group in replays.groupby(["strategy", "side", "class"]):
        group = group.drop_duplicates(["rule", "symbol", "date"])
        base = group[group["rule"] == "fixed"].set_index(["symbol", "date"])["excess"]
        for rule, cell in group.groupby("rule"):
            entry = {"strategy": strategy, "side": side, "family": FAMILY.get(strategy, "other"), "class": cls,
                     "rule": rule, "n": int(len(cell)), "mean_excess_pct": round(float(cell["excess"].mean()), 3),
                     "beat_spy_pct": round(float((cell["excess"] > 0).mean() * 100), 1),
                     "win_pct": round(float((cell["result"] > 0).mean() * 100), 1),
                     "mean_days_held": round(float(cell["held"].mean()), 1)}
            keyed = cell.set_index(["symbol", "date"])
            if rule != "fixed":
                paired = keyed["excess"].sub(base).dropna()
                test = _block_test(paired.to_numpy(), keyed.loc[paired.index, "month"].to_numpy())
                if test:
                    entry.update({"vs_fixed_pct": test["mean"], "vs_fixed_ci": test["ci"], "p_value": test["p"]})
            random = randoms.get((strategy, side, cls, rule))
            if random is not None:
                edge = keyed["excess"].sub(random).dropna()
                test = _block_test(edge.to_numpy(), keyed.loc[edge.index, "month"].to_numpy())
                if test:
                    entry.update({"edge_vs_random_pct": test["mean"], "edge_ci": test["ci"], "edge_p": test["p"],
                                  **_years(edge)})
            out.append(entry)
    _bh_verdicts(out, "p_value", "vs_fixed_pct", "q_value", "verdict",
                 ("better than fixed", "worse than fixed", "no clear difference"))
    _bh_verdicts(out, "edge_p", "edge_vs_random_pct", "edge_q", "edge_verdict",
                 ("edge", "worse than random", "no clear edge"))
    return out


def real_edges(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Per strategy and side, the class x rule with the largest significant edge over a random entry, kept only
    when the edge is positive in more than half of the years it can be measured."""
    best: dict[tuple[str, str], dict[str, Any]] = {}
    for e in results:
        if e.get("edge_verdict") != "edge" or e.get("years_positive", 0) * 2 <= e.get("years", 0):
            continue
        key = (e["strategy"], e["side"])
        if key not in best or e["edge_vs_random_pct"] > best[key]["edge_vs_random_pct"]:
            best[key] = e
    return sorted(best.values(), key=lambda e: -e["edge_vs_random_pct"])


def best_per_class(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Per strategy and side: for each class the rule with the highest mean excess (fixed unless another one is
    better with q <= FDR_Q), and the class whose best rule earns the most."""
    table: dict[tuple[str, str], dict[str, Any]] = {}
    for e in results:
        key = (e["strategy"], e["side"])
        row = table.setdefault(key, {"strategy": e["strategy"], "side": e["side"], "family": e["family"], "classes": {}})
        current = row["classes"].get(e["class"])
        eligible = e["rule"] == "fixed" or e.get("verdict") == "better than fixed"
        if eligible and (current is None or e["mean_excess_pct"] > current["mean_excess_pct"]):
            row["classes"][e["class"]] = {"rule": e["rule"], "mean_excess_pct": e["mean_excess_pct"],
                                          "beat_spy_pct": e["beat_spy_pct"], "mean_days_held": e["mean_days_held"]}
    for row in table.values():
        if row["classes"]:
            best = max(row["classes"].items(), key=lambda kv: kv[1]["mean_excess_pct"])
            row["best_class"], row["best_rule"] = best[0], best[1]["rule"]
    return sorted(table.values(), key=lambda r: (r["strategy"], r["side"]))


def render(report: dict[str, Any]) -> str:
    names = {"day": "ημέρα", "week": "εβδομάδα", "month": "μήνας", "quarter": "τρίμηνο"}
    lines = ["# Εργαστήριο εξόδων · κανόνας εξόδου × διάρκεια θέσης", "",
             f"Επαναλήψεις {report['replays']} από {report['signals']} σήματα (έως {MAX_PER_GROUP} ανά στρατηγική και "
             f"πλευρά, 5 χρόνια). Είσοδος στο κλείσιμο του σήματος. Κανόνες: fixed (κράτημα ως το τέλος), atr_bracket "
             "(σημερινός), mr_signal (έξοδος στον μέσο 5 ημερών), trend_trailing (chandelier 3×ATR). Ένας κανόνας "
             f"λέγεται καλύτερος από το fixed μόνο με q ≤ {FDR_Q} (διόρθωση για πολλούς ελέγχους).", "",
             "## Καλύτερος κανόνας ανά διάρκεια (μέση υπέρβαση έναντι SPY %)", "",
             "| στρατηγική | πλευρά | τύπος | ημέρα | εβδομάδα | μήνας | τρίμηνο | καλύτερο |", "|---|---|---|---|---|---|---|---|"]
    for row in report["best"]:
        cells = []
        for cls in CLASSES:
            c = row["classes"].get(cls)
            cells.append("—" if c is None else f"{c['mean_excess_pct']:+.2f} ({c['rule']})")
        best = f"{names.get(row.get('best_class'), '—')} · {row.get('best_rule', '—')}"
        lines.append(f"| {row['strategy']} | {row['side']} | {row['family']} | " + " | ".join(cells) + f" | {best} |")
    lines += ["", "## Πραγματικό πλεονέκτημα έναντι τυχαίας εισόδου (ίδια μετοχή, ίδιος κανόνας και διάρκεια)", "",
              f"Μόνο ό,τι περνά τον έλεγχο (q ≤ {FDR_Q}) και είναι θετικό σε περισσότερες από τις μισές χρονιές. "
              "Ένα ανά στρατηγική και πλευρά.", "",
              "| στρατηγική | πλευρά | διάρκεια | κανόνας | πλεονέκτημα % | 95% | υπέρβαση SPY % | θετικές χρονιές |",
              "|---|---|---|---|---|---|---|---|"]
    edges = report.get("edges") or []
    lines += [f"| {e['strategy']} | {e['side']} | {names[e['class']]} | {e['rule']} | {e['edge_vs_random_pct']:+.2f} | "
              f"{e['edge_ci'][0]:+.2f} έως {e['edge_ci'][1]:+.2f} | {e['mean_excess_pct']:+.2f} | "
              f"{e['years_positive']}/{e['years']} |" for e in edges] or ["| — | | | | | | | |"]
    without = sorted({(e["strategy"], e["side"]) for e in report["results"]} - {(e["strategy"], e["side"]) for e in edges})
    lines += ["", "Χωρίς σταθερό πλεονέκτημα σε καμία διάρκεια: "
              + (", ".join(f"{s} {d}" for s, d in without) or "καμία") + "."]
    lines += ["", "## Κανόνες που διαφέρουν πραγματικά από το fixed", ""]
    sig = [e for e in report["results"] if e.get("verdict") in {"better than fixed", "worse than fixed"}]
    lines += [f"- {e['strategy']} {e['side']} · {names[e['class']]} · {e['rule']}: {e['vs_fixed_pct']:+.2f}% ανά trade "
              f"(95%: {e['vs_fixed_ci'][0]:+.2f} έως {e['vs_fixed_ci'][1]:+.2f}, q = {e['q_value']}) · "
              f"{'καλύτερος' if e['vs_fixed_pct'] > 0 else 'χειρότερος'}" for e in sig] or ["- Κανένας."]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dir", type=Path, default=phs.DEFAULT_DIR)
    parser.add_argument("--signals", type=Path, nargs="+",
                        default=[ROOT / "output" / "strategy_lab" / "strategy_lab_signals_latest.csv.gz",
                                 ROOT / "output" / "strategy_lab" / "strategy_lab_legacy_signals_latest.csv.gz"],
                        help="signal files; a missing one is skipped")
    parser.add_argument("--out", type=Path, default=ROOT / "output" / "strategy_lab")
    parser.add_argument("--per-group", type=int, default=MAX_PER_GROUP)
    parser.add_argument("--horizon-curve", action="store_true", help="fixed hold on the HORIZONS grid only")
    args = parser.parse_args(argv)
    started = time.perf_counter()
    files = [f for f in args.signals if f.exists()]
    if not files:
        raise SystemExit("no signal file found")
    signals = pd.concat([pd.read_csv(f, usecols=["strategy", "symbol", "date", "side"], parse_dates=["date"])
                         for f in files], ignore_index=True)
    # a random sample of at most per_group signals for each strategy and side
    sampled = signals.sample(frac=1.0, random_state=7).groupby(["strategy", "side"]).head(args.per_group)
    if args.horizon_curve:
        replays = collect(sampled, args.dir, classes=HORIZONS, rules=("fixed",))
        results = summarize(replays) if len(replays) else []
        report = {"schema": "ecosystem.horizon_curve.v1", "generated_at": datetime.now(timezone.utc).isoformat(),
                  "signal_files": [f.name for f in files], "signals": int(len(sampled)), "replays": int(len(replays)),
                  "horizons": HORIZONS, "seconds": round(time.perf_counter() - started, 1), "results": results}
        args.out.mkdir(parents=True, exist_ok=True)
        (args.out / "horizon_curve_latest.json").write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
        print(json.dumps({"signals": report["signals"], "replays": report["replays"], "seconds": report["seconds"]}))
        return 0
    replays = collect(sampled, args.dir)
    results = summarize(replays) if len(replays) else []
    report = {"schema": "ecosystem.exit_lab.v2", "generated_at": datetime.now(timezone.utc).isoformat(),
              "signal_files": [f.name for f in files], "signals": int(len(sampled)), "replays": int(len(replays)), "classes": CLASSES, "rules": list(RULES),
              "seconds": round(time.perf_counter() - started, 1), "results": results, "best": best_per_class(results),
              "edges": real_edges(results)}
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "exit_lab_latest.json").write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    (args.out / "exit_lab_latest.md").write_text(render(report), encoding="utf-8")
    print(json.dumps({"signals": report["signals"], "replays": report["replays"], "seconds": report["seconds"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
