"""Are Giannis's tiers inverted, or is it only beta? A read-only check (owner approved 2026-11-16).

``outcome_evaluator.py`` measures each tier against SPY and finds the order upside down: Speculative Monitor beats
Core Watchlist. SPY removes the market's move but not each company's *sensitivity* to it. In a rising market small,
volatile companies (beta above 1) beat SPY with no information at all. This check takes the same outcomes and

* estimates each company's Dimson beta (same day + one day late) on the 250 sessions before the decision (at least
  120 known; otherwise unknown),
* measures the return minus SPY (as the evaluator does) and the return minus beta x SPY (beta-adjusted),
* groups by tier and horizon, and also reads each tier per decision date (the same companies repeat every night, so
  the dates, not the rows, are the real sample).

Prices come from the local store (data/price_history). A company or day missing there stays out and is counted.
Writes output/strategy_lab/giannis_tier_beta_latest.{json,md}; changes no score, tier or order.
"""
from __future__ import annotations

import argparse
from bisect import bisect_right
from collections import defaultdict
from contextlib import closing
from datetime import date, datetime, timezone
import json
import math
from pathlib import Path
import sqlite3
import statistics
import sys
from typing import Any

ROOT = Path(__file__).resolve().parent
DB_PATH = ROOT / "data" / "market_intel.db"
PRICES = ROOT / "data" / "price_history"
OUT = ROOT / "output" / "strategy_lab"
SCHEMA = "ecosystem.giannis_tier_beta_check.v1"
TIERS = ("Core Watchlist", "Selective Watchlist", "Speculative Monitor", "Avoid For Now")
BETA_WINDOW, BETA_MIN = 250, 120
BETA_CLIP = (-1.0, 4.0)  # a beta outside this comes from a bad print or a halted stock, not the company


def load_outcomes(db_path: Path) -> list[dict[str, Any]]:
    """Giannis's tier per decision and the outcome's target session (read-only connection)."""
    conn = sqlite3.connect(f"file:{db_path.resolve().as_posix()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    with closing(conn):
        rows = conn.execute(
            "SELECT e.subject_id,e.observed_at,e.decision_json,o.horizon_days,o.evidence_json "
            "FROM agent_learning_outcomes o JOIN agent_learning_episodes e ON e.episode_id=o.episode_id "
            "WHERE o.objective_id='market_return_pct' AND e.agent_id='giannis'").fetchall()
    out = []
    for row in rows:
        tier = json.loads(row["decision_json"] or "{}").get("giannis_tier")
        target = json.loads(row["evidence_json"] or "{}").get("target_session_date")
        if tier in TIERS and target:
            out.append({"ticker": str(row["subject_id"]).upper(), "observed_at": row["observed_at"], "tier": tier,
                        "horizon": int(row["horizon_days"]), "target": date.fromisoformat(target)})
    return out


def read_closes(symbol: str, directory: Path) -> tuple[list[date], list[float]] | None:
    import price_history_store

    frame = price_history_store.load(symbol, directory)
    if frame is None:
        return None
    column = "Adj Close" if "Adj Close" in frame.columns else "Close"
    series = frame[column].dropna()
    series = series[series > 0]
    return [d.date() for d in series.index], [float(v) for v in series.values]


def baseline(observed_at: str, days: list[date]) -> int | None:
    """Index of the last completed session at the observation time (as the outcome tracker sets its base)."""
    from outcome_tracker import completed_through

    try:
        moment = datetime.fromisoformat(str(observed_at).replace("Z", "+00:00"))
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    k = bisect_right(days, completed_through(moment)) - 1
    return k if k >= 0 else None


def _two_slopes(x1: list[float], x2: list[float], ys: list[float]) -> tuple[float, float] | None:
    """OLS slopes of ys on x1 and x2 together (with an intercept), by the 2x2 normal equations."""
    m1, m2, my = statistics.fmean(x1), statistics.fmean(x2), statistics.fmean(ys)
    a = sum((u - m1) ** 2 for u in x1)
    b = sum((u - m1) * (v - m2) for u, v in zip(x1, x2))
    c = sum((v - m2) ** 2 for v in x2)
    p = sum((u - m1) * (y - my) for u, y in zip(x1, ys))
    q = sum((v - m2) * (y - my) for v, y in zip(x2, ys))
    det = a * c - b * b
    if det <= 0:
        return None
    return (c * p - b * q) / det, (a * q - b * p) / det


def beta(stock: dict[date, float], spy_days: list[date], spy: list[float], end: int) -> float | None:
    """Dimson beta of daily returns on SPY's over the window before ``end``: the same-day and the day-before slopes,
    fitted together, added. A thinly traded stock reacts to the market a day late, and the same-day slope alone reads it
    as a low beta (Dimson 1979). Sessions where the stock's price is missing stay out."""
    xs, lag, ys = [], [], []
    for k in range(max(2, end - BETA_WINDOW + 1), end + 1):
        a, b = stock.get(spy_days[k - 1]), stock.get(spy_days[k])
        if a and b:
            xs.append(spy[k] / spy[k - 1] - 1.0)
            lag.append(spy[k - 1] / spy[k - 2] - 1.0)
            ys.append(b / a - 1.0)
    if len(xs) < BETA_MIN:
        return None
    slopes = _two_slopes(xs, lag, ys)
    if slopes is None:
        return None
    return min(max(slopes[0] + slopes[1], BETA_CLIP[0]), BETA_CLIP[1])


def measure(outcomes: list[dict[str, Any]], prices: Path) -> tuple[list[dict[str, Any]], dict[str, int]]:
    spy_series = read_closes("SPY", prices)
    if spy_series is None:
        raise SystemExit("SPY is not in the price store: nothing can be measured.")
    spy_days, spy = spy_series
    spy_index = {d: k for k, d in enumerate(spy_days)}
    by_ticker: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in outcomes:
        by_ticker[row["ticker"]].append(row)
    rows, missing = [], defaultdict(int)
    for ticker, items in by_ticker.items():
        series = read_closes(ticker, prices)
        if series is None:
            missing["no_prices"] += len(items)
            continue
        stock = dict(zip(*series))
        betas: dict[int, float | None] = {}
        for item in items:
            base = baseline(item["observed_at"], spy_days)
            target = spy_index.get(item["target"])
            if base is None or target is None or target <= base:
                missing["no_session"] += 1
                continue
            a, b = stock.get(spy_days[base]), stock.get(spy_days[target])
            if not a or not b:
                missing["no_price_on_day"] += 1
                continue
            if base not in betas:
                betas[base] = beta(stock, spy_days, spy, base)
            ret, market = b / a - 1.0, spy[target] / spy[base] - 1.0
            if abs(ret) > 2.0:  # a reverse split or bad print: not a return
                missing["implausible"] += 1
                continue
            value = betas[base]
            rows.append({**item, "date": spy_days[base].isoformat(), "beta": value,
                         "excess_spy_pct": (ret - market) * 100,
                         "excess_beta_pct": None if value is None else (ret - value * market) * 100})
    return rows, dict(missing)


def _summary(values: list[float]) -> dict[str, Any]:
    if not values:
        return {"n": 0, "mean_pct": None}
    return {"n": len(values), "mean_pct": round(statistics.fmean(values), 3)}


def _by_date(rows: list[dict[str, Any]], field: str) -> dict[str, Any]:
    """Mean of the per-date means, with a t across dates (the honest sample size)."""
    per: dict[str, list[float]] = defaultdict(list)
    for row in rows:
        if row[field] is not None:
            per[row["date"]].append(row[field])
    means = [statistics.fmean(v) for v in per.values()]
    if len(means) < 2:
        return {"dates": len(means), "mean_pct": None, "t": None}
    sd = statistics.stdev(means)
    mean = statistics.fmean(means)
    return {"dates": len(means), "mean_pct": round(mean, 3),
            "t": None if sd == 0 else round(mean / (sd / math.sqrt(len(means))), 2)}


def analyse(rows: list[dict[str, Any]], missing: dict[str, int]) -> dict[str, Any]:
    horizons = sorted({r["horizon"] for r in rows})
    tiers = []
    for horizon in horizons:
        for tier in TIERS:
            cell = [r for r in rows if r["horizon"] == horizon and r["tier"] == tier]
            known = [r for r in cell if r["beta"] is not None]
            tiers.append({"horizon": horizon, "tier": tier, "rows": len(cell),
                          "beta_known": len(known),
                          "median_beta": round(statistics.median(r["beta"] for r in known), 2) if known else None,
                          "vs_spy": _summary([r["excess_spy_pct"] for r in known]),
                          "vs_beta": _summary([r["excess_beta_pct"] for r in known]),
                          "vs_beta_by_date": _by_date(known, "excess_beta_pct")})
    return {"schema": SCHEMA, "generated_at": datetime.now(timezone.utc).isoformat(),
            "rows": len(rows), "left_out": missing, "tiers": tiers, "verdicts": verdicts(tiers)}


def verdicts(tiers: list[dict[str, Any]]) -> dict[str, str]:
    """Per horizon: does Core still trail Speculative once beta is taken out?"""
    out = {}
    for horizon in sorted({t["horizon"] for t in tiers}):
        cells = {t["tier"]: t for t in tiers if t["horizon"] == horizon}
        core, spec = cells.get("Core Watchlist"), cells.get("Speculative Monitor")
        means = None if not core or not spec else (spec["vs_spy"]["mean_pct"], core["vs_spy"]["mean_pct"],
                                                    spec["vs_beta"]["mean_pct"], core["vs_beta"]["mean_pct"])
        if (means is None or None in means or core["vs_beta_by_date"]["dates"] < 10
                or spec["vs_beta_by_date"]["dates"] < 10):
            out[str(horizon)] = "insufficient"  # a missing mean is not read as zero
            continue
        before, after = means[0] - means[1], means[2] - means[3]
        if before <= 0:
            out[str(horizon)] = "not inverted even against SPY"
        elif after <= 0:
            out[str(horizon)] = "inversion explained by beta"
        elif after < before / 2:
            out[str(horizon)] = "mostly beta, part remains"
        else:
            out[str(horizon)] = "inverted even after beta"
    return out


VERDICT_GR = {"insufficient": "ανεπαρκές δείγμα", "not inverted even against SPY": "καμία αναστροφή ούτε έναντι SPY",
              "inversion explained by beta": "η αναστροφή εξηγείται από το beta",
              "mostly beta, part remains": "κυρίως beta, ένα μέρος μένει",
              "inverted even after beta": "αναστροφή και μετά το beta"}


def render(payload: dict[str, Any]) -> str:
    def v(value: Any, suffix: str = "%") -> str:
        return "-" if value is None else f"{value:+.2f}{suffix}".replace(".", ",")

    lines = ["# Βαθμίδες Γιάννη: αναστροφή ή beta;", "",
             f"Παράχθηκε {payload['generated_at'][:16]} · {payload['rows']} αποτελέσματα με τιμές · εκτός: "
             + (", ".join(f"{k} {n}" for k, n in payload["left_out"].items()) or "κανένα"), "",
             "Το beta κάθε εταιρείας (Dimson: ίδια μέρα + μία μέρα αργότερα, για τις λίγο ρευστές) μετριέται στις 250 συνεδριάσεις πριν από την απόφαση (τουλάχιστον 120). "
             "«Έναντι SPY» είναι η μέτρηση του outcome_evaluator· «έναντι beta» αφαιρεί beta × SPY. "
             "Το t είναι πάνω στις ημερομηνίες, γιατί οι ίδιες εταιρείες επαναλαμβάνονται κάθε νύχτα· στους μεγάλους ορίζοντες "
             "οι διαδοχικές ημερομηνίες επικαλύπτονται, άρα το t εκεί είναι υπερεκτιμημένο.", ""]
    for horizon in sorted({t["horizon"] for t in payload["tiers"]}):
        lines += [f"## {horizon} συνεδριάσεις · {VERDICT_GR.get(payload['verdicts'][str(horizon)])}", "",
                  "| βαθμίδα | n (με beta) | διάμεσο beta | έναντι SPY | έναντι beta | ανά ημερομηνία (t, ημ.) |",
                  "|---|---|---|---|---|---|"]
        for t in (x for x in payload["tiers"] if x["horizon"] == horizon):
            d = t["vs_beta_by_date"]
            lines.append(f"| {t['tier']} | {t['beta_known']} / {t['rows']} | "
                         f"{'-' if t['median_beta'] is None else str(t['median_beta']).replace('.', ',')} | "
                         f"{v(t['vs_spy']['mean_pct'])} | {v(t['vs_beta']['mean_pct'])} | "
                         f"{v(d['mean_pct'])} (t {'-' if d['t'] is None else d['t']}, {d['dates']}) |")
        lines.append("")
    lines += ["Read-only: δεν αλλάζει σκορ, βαθμίδες ή εντολές.", ""]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--db", default=str(DB_PATH))
    parser.add_argument("--prices", default=str(PRICES))
    parser.add_argument("--out", default=str(OUT))
    args = parser.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    rows, missing = measure(load_outcomes(Path(args.db)), Path(args.prices))
    payload = analyse(rows, missing)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "giannis_tier_beta_latest.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                                                       encoding="utf-8")
    (out / "giannis_tier_beta_latest.md").write_text(render(payload), encoding="utf-8")
    print(f"[TIER-BETA] rows={payload['rows']} verdicts={payload['verdicts']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
