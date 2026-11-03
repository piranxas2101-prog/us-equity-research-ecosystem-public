"""Fundamental rules lab: does passing each of Babis's fundamental rules predict a better return? Research only.

Owner 2026-11-03 (rules review, part 2 - F1-F5, docs/RULES_REVIEW.md): the technical rules were judged on five years
of evidence; the eight fundamental rules of ``scanner_config.json`` (roe, margins, growth, debt, current ratio,
forward P/E) are fixed levels without any. This lab replays them point in time on the stored SEC companyfacts
(``cache/sec_companyfacts``, every filing with its ``filed`` date) and the five-year daily prices
(``data/price_history``):

* on the first session of every month, for every company with both, the ratios are computed with only the facts
  filed by that day (``sec_fundamental_ratios.compute_ratios``; an income statement or balance sheet older than
  ``MAX_PERIOD_AGE_DAYS`` is unknown, as live);
* the forward return over ``HORIZONS`` sessions from that day's close, minus the median of all companies that day
  (the cross-section is the baseline: no SPY, no look at the market's direction), clipped to that day's
  1st-99th percentile so a reverse split or a bad print cannot dominate a mean;
* per rule: the excess of the companies that pass against those that fail, per date and then averaged over the
  dates, with a 95% interval from a block bootstrap over months and the share of years where passing was better;
  plus the excess by decile of the metric, so the level of the threshold can be judged, not only its direction.

Forward P/E has no history (analyst estimates): the trailing P/E stands in for it - split-adjusted price x shares /
trailing net income. Shares are the company's own count at that day; when today's count differs by more than
1.8x (a split since), today's count is used. A negative P/E (losses) is its own group, never "below 35".
F3 (filing recency): the excess by days since the last 10-Q/10-K was filed. Unknown stays unknown: a company without
a value is in neither group. Survivorship: the price store holds today's companies only, so the delisted ones are
missing - the results compare survivors with survivors.

    fundamental_lab.py            # writes output/strategy_lab/fundamental_rules_latest.json and .md
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor
from datetime import date, datetime, timezone
import gzip
import json
from pathlib import Path
import random
from typing import Any

ROOT = Path(__file__).resolve().parent
PRICES = ROOT / "data" / "price_history"
FACTS = ROOT / "cache" / "sec_companyfacts"
TICKERS = ROOT / "cache" / "sec_company_tickers_exchange.json"
CONFIG = ROOT / "scanner_config.json"
OUT = ROOT / "output" / "strategy_lab"
SCHEMA = "ecosystem.fundamental_rules_lab.v1"
HORIZONS = (20, 60, 120)
MAIN_HORIZON = 60
BOOTSTRAP = 2000
BLOCK_MONTHS = 3
SPLIT_RATIO = 1.8
CLIP = 0.01  # forward returns are clipped to the day's 1st-99th percentile (bad prints, reverse splits)
PE_BUCKETS = ((None, 0.0, "negative (losses)"), (0.0, 15.0, "0-15"), (15.0, 25.0, "15-25"), (25.0, 35.0, "25-35"),
              (35.0, 50.0, "35-50"), (50.0, 100.0, "50-100"), (100.0, None, ">100"))
RECENCY_BUCKETS = ((0, 30, "0-30"), (31, 60, "31-60"), (61, 100, "61-100"), (101, 200, "101-200"), (201, None, ">200"))
SHARES = ("EntityCommonStockSharesOutstanding",)
REPORT_FORMS = frozenset({"10-K", "10-Q", "20-F", "40-F", "10-KT", "10-QT"})


def load_prices(path: Path) -> tuple[list[date], list[float]]:
    days, closes = [], []
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        next(fh, None)
        for line in fh:
            parts = line.rstrip().split(",")
            try:
                day, close = date.fromisoformat(parts[0]), float(parts[4])
            except (IndexError, ValueError):
                continue
            if close > 0:
                days.append(day)
                closes.append(close)
    return days, closes


def month_starts(days: list[date]) -> list[date]:
    out, seen = [], set()
    for day in days:
        if (day.year, day.month) not in seen:
            seen.add((day.year, day.month))
            out.append(day)
    return out[1:]  # the first month of the store may start mid-month


def _shares(raw: dict[str, Any], as_of: date | None) -> float | None:
    points = []
    for concept in SHARES:
        for rows in (((raw.get("facts") or {}).get("dei") or {}).get(concept) or {}).get("units", {}).values():
            for fact in rows or []:
                filed = str(fact.get("filed") or "")[:10]
                if isinstance(fact.get("val"), (int, float)) and fact["val"] > 0 and filed and \
                        (as_of is None or filed <= as_of.isoformat()):
                    points.append((filed, str(fact.get("end") or ""), float(fact["val"])))
    return max(points)[2] if points else None


def _last_report(raw: dict[str, Any], as_of: date) -> date | None:
    """Date the last 10-Q/10-K (or foreign equivalent) was filed by ``as_of``, read from the facts' filings."""
    latest = None
    for taxonomy in ("us-gaap", "ifrs-full"):
        for name in ("Assets", "Revenues", "NetIncomeLoss", "ProfitLoss"):
            for rows in (((raw.get("facts") or {}).get(taxonomy) or {}).get(name) or {}).get("units", {}).values():
                for fact in rows or []:
                    filed = str(fact.get("filed") or "")[:10]
                    if fact.get("form") in REPORT_FORMS and filed and filed <= as_of.isoformat() and \
                            (latest is None or filed > latest):
                        latest = filed
    return date.fromisoformat(latest) if latest else None


def company_rows(task: tuple[str, str, str, list[str]]) -> list[dict[str, Any]]:
    """Every month-start observation of one company: ratios point in time and forward returns."""
    import sec_fundamental_ratios as sfr

    ticker, facts_path, price_path, starts = task
    try:
        raw = json.loads(gzip.decompress(Path(facts_path).read_bytes()))
        days, closes = load_prices(Path(price_path))
    except (OSError, EOFError, ValueError):
        return []
    index = {d: k for k, d in enumerate(days)}
    shares_now = _shares(raw, None)
    out = []
    for text in starts:
        day = date.fromisoformat(text)
        k = index.get(day)
        if k is None:
            continue
        ratios = sfr.compute_ratios(raw, day)
        values = dict(ratios["values"])
        periods = ratios["periods"]
        for metric, end in (("income", periods.get("income_statement_end")), ("balance", periods.get("balance_sheet_end"))):
            if end is None or (day - end).days > sfr.MAX_PERIOD_AGE_DAYS:
                keys = sfr.FLOW_METRICS if metric == "income" else sfr.BALANCE_METRICS
                values.update({key: None for key in keys})
        income = sfr.flow(sfr._facts(raw, sfr.CONCEPTS["net_income"], day))
        shares = _shares(raw, day)
        if shares and shares_now and not (1 / SPLIT_RATIO < shares_now / shares < SPLIT_RATIO):
            shares = shares_now
        pe = None
        if income and income["ttm"] and shares and income.get("end") and (day - income["end"]).days <= sfr.MAX_PERIOD_AGE_DAYS:
            pe = closes[k] * shares / income["ttm"]
        values["trailing_pe"] = pe
        report = _last_report(raw, day)
        forward = {h: (closes[k + h] / closes[k] - 1.0) if k + h < len(closes) else None for h in HORIZONS}
        out.append({"ticker": ticker, "date": text, "values": values,
                    "days_since_report": (day - report).days if report else None, "forward": forward})
    return out


def collect(limit: int | None = None, workers: int = 4) -> list[dict[str, Any]]:
    import periodic_filing

    mapping = periodic_filing.ticker_to_cik(TICKERS)
    tasks = []
    calendar: list[date] = []
    for price in sorted(PRICES.glob("*.csv.gz")):
        ticker = price.name[:-len(".csv.gz")]
        cik = mapping.get(ticker.upper())
        facts = FACTS / f"CIK{int(cik):010d}.json.gz" if cik else None
        if facts is None or not facts.exists():
            continue
        if not calendar:
            days, _ = load_prices(PRICES / "SPY.csv.gz") if (PRICES / "SPY.csv.gz").exists() else load_prices(price)
            calendar = month_starts(days)
        tasks.append((ticker, str(facts), str(price), [d.isoformat() for d in calendar]))
    if limit:
        tasks = tasks[:limit]
    rows: list[dict[str, Any]] = []
    with ProcessPoolExecutor(max_workers=workers) as pool:
        for part in pool.map(company_rows, tasks, chunksize=8):
            rows.extend(part)
    return rows


def add_excess(rows: list[dict[str, Any]]) -> None:
    """Forward return minus the same date's median of all companies (the cross-section baseline)."""
    by_date: dict[tuple[str, int], list[float]] = defaultdict(list)
    for row in rows:
        for h, value in row["forward"].items():
            if value is not None:
                by_date[(row["date"], h)].append(value)
    bounds = {}
    for key, values in by_date.items():
        values.sort()
        n = len(values)
        bounds[key] = (values[n // 2], values[int(n * CLIP)], values[min(n - 1, int(n * (1 - CLIP)))])
    for row in rows:
        row["excess"] = {}
        for h, v in row["forward"].items():
            if v is None:
                row["excess"][h] = None
                continue
            median, low, high = bounds[(row["date"], h)]
            row["excess"][h] = min(max(v, low), high) - median  # a reverse split or bad print never dominates


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def spread(rows: list[dict[str, Any]], group_a, group_b, horizon: int, seed: int = 7) -> dict[str, Any]:
    """Mean over dates of (mean excess of group A - of group B), a month-block bootstrap interval, years positive."""
    per_date: dict[str, tuple[list[float], list[float]]] = defaultdict(lambda: ([], []))
    n_a = n_b = 0
    for row in rows:
        value = row["excess"].get(horizon)
        if value is None:
            continue
        if group_a(row):
            per_date[row["date"]][0].append(value)
            n_a += 1
        elif group_b(row):
            per_date[row["date"]][1].append(value)
            n_b += 1
    dates = sorted(d for d, (a, b) in per_date.items() if len(a) >= 5 and len(b) >= 5)
    diffs = [_mean(per_date[d][0]) - _mean(per_date[d][1]) for d in dates]
    hits = [_mean([1.0 if x > 0 else 0.0 for x in per_date[d][0]]) - _mean([1.0 if x > 0 else 0.0 for x in per_date[d][1]])
            for d in dates]
    if not diffs:
        return {"dates": 0, "n_pass": n_a, "n_fail": n_b, "spread_pct": None}
    rng = random.Random(seed)
    boots = []
    for _ in range(BOOTSTRAP):
        sample: list[float] = []
        while len(sample) < len(diffs):
            start = rng.randrange(len(diffs))
            sample += diffs[start:start + BLOCK_MONTHS]
        boots.append(_mean(sample[:len(diffs)]))
    boots.sort()
    years: dict[str, list[float]] = defaultdict(list)
    for d, x in zip(dates, diffs):
        years[d[:4]].append(x)
    positive = sum(1 for v in years.values() if _mean(v) > 0)
    return {"dates": len(dates), "n_pass": n_a, "n_fail": n_b, "spread_pct": round(_mean(diffs) * 100, 2),
            "ci95_pct": [round(boots[int(0.025 * BOOTSTRAP)] * 100, 2), round(boots[int(0.975 * BOOTSTRAP)] * 100, 2)],
            "years_positive": f"{positive}/{len(years)}",
            "beat_median_diff_pts": round(_mean(hits) * 100, 1)}


def by_bucket(rows: list[dict[str, Any]], key, buckets, horizon: int) -> list[dict[str, Any]]:
    out = []
    for low, high, label in buckets:
        values = [r["excess"][horizon] for r in rows if r["excess"].get(horizon) is not None
                  and (v := key(r)) is not None and (low is None or v >= low) and (high is None or v < high)]
        out.append({"bucket": label, "n": len(values), "mean_excess_pct": round(_mean(values) * 100, 2) if values else None,
                    "share_above_median": round(sum(1 for v in values if v > 0) / len(values), 3) if values else None})
    return out


def deciles(rows: list[dict[str, Any]], metric: str, horizon: int) -> list[dict[str, Any]]:
    known = sorted((r["values"][metric], r["excess"][horizon]) for r in rows
                   if r["values"].get(metric) is not None and r["excess"].get(horizon) is not None)
    out = []
    for q in range(10):
        part = known[q * len(known) // 10:(q + 1) * len(known) // 10]
        if part:
            out.append({"decile": q + 1, "from": round(part[0][0], 4), "to": round(part[-1][0], 4), "n": len(part),
                        "mean_excess_pct": round(_mean([e for _, e in part]) * 100, 2)})
    return out


def analyse(rows: list[dict[str, Any]], rules: list[dict[str, Any]]) -> dict[str, Any]:
    import operator

    ops = {">=": operator.ge, "<=": operator.le}
    results = {}
    for rule in rules:
        metric = "trailing_pe" if rule["metric"] == "forward_pe" else rule["metric"]
        level, compare = rule["value"], ops[rule["operator"]]
        invalid = rule.get("invalid_at_or_below")

        def passed(r, metric=metric, level=level, compare=compare, invalid=invalid):
            v = r["values"].get(metric)
            return v is not None and not (invalid is not None and v <= invalid) and compare(v, level)

        def failed(r, metric=metric, passed=passed):
            return r["values"].get(metric) is not None and not passed(r)

        results[rule["metric"]] = {
            "measured_as": metric, "rule": f"{rule['metric']} {rule['operator']} {level}",
            "pass_vs_fail": {str(h): spread(rows, passed, failed, h) for h in HORIZONS},
            "unknown_share": round(sum(1 for r in rows if r["values"].get(metric) is None) / len(rows), 3),
            "deciles_60": deciles(rows, metric, MAIN_HORIZON)}
    weights = {r["metric"]: r["weight"] for r in rules}
    total = sum(weights.values())

    def score(r):
        known = [m for m in weights if r["values"].get("trailing_pe" if m == "forward_pe" else m) is not None]
        if len(known) < len(weights) - 2:
            return None
        got = 0.0
        for rule in rules:
            m = "trailing_pe" if rule["metric"] == "forward_pe" else rule["metric"]
            v = r["values"].get(m)
            if v is not None and not (rule.get("invalid_at_or_below") is not None and v <= rule["invalid_at_or_below"]) \
                    and ops[rule["operator"]](v, rule["value"]):
                got += rule["weight"]
        return got / total * 100

    for r in rows:
        r["score"] = score(r)
    score_buckets = ((0, 20, "0-20"), (20, 40, "20-40"), (40, 60, "40-60"), (60, 80, "60-80"), (80, 101, "80-100"))
    return {
        "rules": results,
        "score_buckets": {str(h): by_bucket(rows, lambda r: r["score"], score_buckets, h) for h in HORIZONS},
        "score_high_vs_low_60": spread(rows, lambda r: (r["score"] or -1) >= 70, lambda r: r["score"] is not None
                                       and r["score"] < 40, MAIN_HORIZON),
        "pe_buckets": {str(h): by_bucket(rows, lambda r: (v if (v := r["values"].get("trailing_pe")) is None or v > 0
                                                          else -1.0), PE_BUCKETS, h) for h in HORIZONS},
        "negative_pe_vs_0_35": spread(rows, lambda r: (r["values"].get("trailing_pe") or 1) < 0,
                                      lambda r: 0 < (r["values"].get("trailing_pe") or -1) <= 35, MAIN_HORIZON),
        "days_since_report": {str(h): by_bucket(rows, lambda r: r["days_since_report"], RECENCY_BUCKETS, h)
                              for h in HORIZONS},
    }


def render(payload: dict[str, Any]) -> str:
    a = payload["analysis"]
    lines = [f"# Fundamental rules lab ({payload['generated_at'][:10]})", "",
             f"{payload['companies']} companies, {payload['observations']} month-start observations, "
             f"{payload['first_date']} to {payload['last_date']}. Excess = forward return minus the same day's median "
             "of all companies. Research only.", "",
             f"## Pass against fail, {MAIN_HORIZON} sessions", "",
             "| rule | measured as | pass | fail | spread % | 95% | years better | beat-median diff (pts) | unknown |", "|---|---|---|---|---|---|---|---|---|"]
    for name, r in a["rules"].items():
        s = r["pass_vs_fail"][str(MAIN_HORIZON)]
        lines.append(f"| {r['rule']} | {r['measured_as']} | {s['n_pass']} | {s['n_fail']} | {s.get('spread_pct')} | "
                     f"{s.get('ci95_pct')} | {s.get('years_positive')} | {s.get('beat_median_diff_pts')} | {r['unknown_share']} |")
    lines += ["", "## Composite rule score (share of weights passed), mean excess %", "",
              "| score | " + " | ".join(f"{h} sessions" for h in HORIZONS) + " |", "|---|" + "---|" * len(HORIZONS)]
    for k, bucket in enumerate(a["score_buckets"][str(HORIZONS[0])]):
        lines.append(f"| {bucket['bucket']} | " + " | ".join(
            f"{a['score_buckets'][str(h)][k]['mean_excess_pct']} (n {a['score_buckets'][str(h)][k]['n']})" for h in HORIZONS) + " |")
    lines += ["", f"Score >= 70 against < 40, {MAIN_HORIZON} sessions: {a['score_high_vs_low_60']}", "",
              "## Trailing P/E (stands in for forward P/E), mean excess %", "",
              "| P/E | " + " | ".join(f"{h} sessions" for h in HORIZONS) + " |", "|---|" + "---|" * len(HORIZONS)]
    for k, bucket in enumerate(a["pe_buckets"][str(HORIZONS[0])]):
        lines.append(f"| {bucket['bucket']} | " + " | ".join(
            f"{a['pe_buckets'][str(h)][k]['mean_excess_pct']} (n {a['pe_buckets'][str(h)][k]['n']})" for h in HORIZONS) + " |")
    lines += ["", f"Negative P/E against P/E 0-35, {MAIN_HORIZON} sessions: {a['negative_pe_vs_0_35']}", "",
              "## Days since the last 10-Q/10-K (F3), mean excess %", "",
              "| days | " + " | ".join(f"{h} sessions" for h in HORIZONS) + " |", "|---|" + "---|" * len(HORIZONS)]
    for k, bucket in enumerate(a["days_since_report"][str(HORIZONS[0])]):
        lines.append(f"| {bucket['bucket']} | " + " | ".join(
            f"{a['days_since_report'][str(h)][k]['mean_excess_pct']} (n {a['days_since_report'][str(h)][k]['n']})"
            for h in HORIZONS) + " |")
    lines += ["", f"## Deciles of each metric, {MAIN_HORIZON} sessions (mean excess %)", ""]
    for name, r in a["rules"].items():
        lines.append(f"- {r['rule']}: " + "; ".join(f"{d['from']}..{d['to']}: {d['mean_excess_pct']}"
                                                    for d in r["deciles_60"]))
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--limit", type=int, help="only the first N companies (a quick look)")
    parser.add_argument("--workers", type=int, default=4)
    args = parser.parse_args(argv)
    rules = json.loads(CONFIG.read_text(encoding="utf-8"))["fundamental_rules"]
    rows = collect(args.limit, args.workers)
    add_excess(rows)
    payload = {"schema": SCHEMA, "generated_at": datetime.now(timezone.utc).isoformat(),
               "companies": len({r["ticker"] for r in rows}), "observations": len(rows),
               "first_date": min((r["date"] for r in rows), default=None),
               "last_date": max((r["date"] for r in rows), default=None),
               "horizons": HORIZONS, "analysis": analyse(rows, rules)}
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "fundamental_rules_latest.json").write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    (OUT / "fundamental_rules_latest.md").write_text(render(payload), encoding="utf-8")
    print(render(payload))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
