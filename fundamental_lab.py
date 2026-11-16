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

Fundamental factors (owner 2026-11-15, docs/FUNDAMENTAL_FACTORS_PLAN.md, phase 1): the same observations also carry
the six factors of ``fundamental_factors.py`` and the two composites ranked per date; per factor, the rank IC (signed
in the literature's direction, t from the month blocks), best against worst quintile, each leg against the rest, large
against micro caps and within the companies today's rules already score >= 60, with the phase-2 verdict.

    fundamental_lab.py                    # output/strategy_lab/fundamental_rules_latest.json/.md + fundamental_factors_latest.md
    fundamental_lab.py --paper-positions  # the closed paper positions by the composites they opened with (read-only)
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
import sys
from typing import Any

import fundamental_factors
import insider_transactions

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

    ticker, facts_path, price_path, starts, *rest = task
    insider_trades, insider_coverage = rest if rest else (None, None)
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
        factors = fundamental_factors.compute(raw, day)
        # Form 4 open-market trades (insider_transactions.py): unknown when the index does not cover the day
        insider = insider_transactions.factors(insider_trades, day, insider_coverage)
        out.append({"ticker": ticker, "date": text, "values": values,
                    "days_since_report": (day - report).days if report else None, "forward": forward,
                    "factors": {**factors["values"], **insider}, "factor_partial": sorted(factors["partial"]),
                    "market_cap": closes[k] * shares if shares else None})
    return out


def collect(limit: int | None = None, workers: int = 4) -> list[dict[str, Any]]:
    import periodic_filing

    mapping = periodic_filing.ticker_to_cik(TICKERS)
    insider = insider_transactions.InsiderSource()
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
        tasks.append((ticker, str(facts), str(price), [d.isoformat() for d in calendar],
                      insider.trades(cik) if insider.coverage else None, insider.coverage))
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


# --- Fundamental factors (owner 2026-11-15, docs/FUNDAMENTAL_FACTORS_PLAN.md, phase 1) ---------------------------
FACTOR_SIGNS = {"cfo_to_assets": 1, "cash_op_profit": 1, "net_equity_finance": -1, "net_external_finance": -1,
                "d_roa": 1, "asset_growth": -1, "long_quality": 1, "avoid": -1,  # the literature's direction
                # insiders buying on the open market predicts, selling much less (Lakonishok-Lee 2001)
                "insider_buyers_90d": 1, "insider_net_buy_180d": 1}
LARGE_CAP = 3e8  # below ~$300 million = micro caps, where most anomalies live and trading costs are highest
SIGNIFICANT_T = 2.0  # phase 2 gate; promotion asks for t >= 3 (Harvey-Liu-Zhu)


def add_factor_ranks(rows: list[dict[str, Any]]) -> None:
    """Per date: the run-style composites and every factor's quintile (1 = lowest value, 5 = highest)."""
    by_date: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_date[row["date"]].append(row)
    for day_rows in by_date.values():
        combos = fundamental_factors.composites({"values": r.get("factors") or {}} for r in day_rows)
        for row, combo in zip(day_rows, combos):
            row.setdefault("factors", {})
            row["factors"] = {**row["factors"], "long_quality": (combo or {}).get("long_quality"),
                              "avoid": (combo or {}).get("avoid")}
        for name in FACTOR_SIGNS:
            ranks = fundamental_factors.percentiles([r["factors"].get(name) for r in day_rows])
            for row, rank in zip(day_rows, ranks):
                row.setdefault("quintile", {})[name] = None if rank is None else min(5, int(rank * 5) + 1)


def _block_interval(series: list[float], seed: int = 11) -> tuple[float, float, float, float]:
    """Mean, 95% month-block bootstrap interval and a t-like ratio (mean / bootstrap standard error)."""
    rng = random.Random(seed)
    boots = []
    for _ in range(BOOTSTRAP):
        sample: list[float] = []
        while len(sample) < len(series):
            start = rng.randrange(len(series))
            sample += series[start:start + BLOCK_MONTHS]
        boots.append(_mean(sample[:len(series)]))
    boots.sort()
    mean = _mean(series)
    centre = _mean(boots)
    se = (sum((b - centre) ** 2 for b in boots) / (len(boots) - 1)) ** 0.5
    return mean, boots[int(0.025 * BOOTSTRAP)], (boots[int(0.975 * BOOTSTRAP)]), (mean / se if se else 0.0)


def rank_ic(rows: list[dict[str, Any]], name: str, horizon: int) -> dict[str, Any]:
    """Mean over dates of the Spearman correlation between the factor and the excess return, signed so that the
    literature's direction is positive; with a month-block interval and t (overlapping horizons are handled by the
    blocks, not by pretending months are independent)."""
    by_date: dict[str, list[tuple[float, float]]] = defaultdict(list)
    for row in rows:
        value, excess = row["factors"].get(name), row["excess"].get(horizon)
        if value is not None and excess is not None:
            by_date[row["date"]].append((value, excess))
    ics = []
    for pairs in (by_date[d] for d in sorted(by_date)):
        if len(pairs) < 30:
            continue
        a = fundamental_factors.percentiles([p[0] for p in pairs])
        b = fundamental_factors.percentiles([p[1] for p in pairs])
        ma, mb = _mean(a), _mean(b)
        cov = sum((x - ma) * (y - mb) for x, y in zip(a, b))
        var = (sum((x - ma) ** 2 for x in a) * sum((y - mb) ** 2 for y in b)) ** 0.5
        if var:
            ics.append(FACTOR_SIGNS[name] * cov / var)
    if len(ics) < 6:
        return {"dates": len(ics), "ic": None}
    mean, low, high, t = _block_interval(ics)
    return {"dates": len(ics), "ic": round(mean, 4), "ci95": [round(low, 4), round(high, 4)], "t": round(t, 2)}


def factor_report(rows: list[dict[str, Any]], name: str, horizon: int) -> dict[str, Any]:
    """Best quintile against worst (in the literature's direction), each leg against the rest, by size."""
    good, bad = (5, 1) if FACTOR_SIGNS[name] > 0 else (1, 5)

    def q(r):
        return (r.get("quintile") or {}).get(name)

    known = [r for r in rows if q(r) is not None]
    large = [r for r in known if (r.get("market_cap") or 0) >= LARGE_CAP]
    small = [r for r in known if r.get("market_cap") is not None and r["market_cap"] < LARGE_CAP]
    scored = [r for r in known if (r.get("score") or -1) >= 60]  # the companies today's rules already like
    return {
        "coverage": round(len(known) / len(rows), 3) if rows else 0.0,
        "partial_share": round(sum(1 for r in known if name in (r.get("factor_partial") or [])) / len(known), 3) if known else None,
        "best_vs_worst": spread(rows, lambda r: q(r) == good, lambda r: q(r) == bad, horizon),
        "long_leg": spread(rows, lambda r: q(r) == good, lambda r: q(r) not in (None, good), horizon),
        "short_leg": spread(rows, lambda r: q(r) not in (None, bad), lambda r: q(r) == bad, horizon),
        "large_caps": spread(large, lambda r: q(r) == good, lambda r: q(r) == bad, horizon),
        "micro_caps": spread(small, lambda r: q(r) == good, lambda r: q(r) == bad, horizon),
        "within_rules_score_60": spread(scored, lambda r: q(r) == good, lambda r: q(r) == bad, horizon),
        "rank_ic": rank_ic(rows, name, horizon),
    }


def verdict(report: dict[str, Any]) -> str:
    """Phase-2 gate: right sign at 20 and 60 sessions, |t| >= 2 on the rank IC at 60, not carried by micro caps."""
    ic20, ic60 = report["20"]["rank_ic"].get("ic"), report["60"]["rank_ic"]
    large = report["60"]["large_caps"].get("spread_pct")
    if ic20 is None or ic60.get("ic") is None:
        return "insufficient"
    if ic20 > 0 and ic60["ic"] > 0 and ic60["t"] >= SIGNIFICANT_T and (large or 0) > 0:
        return "passes (to shadow)"
    if ic60["ic"] < 0 and ic60["t"] <= -SIGNIFICANT_T:
        return "reversed"
    return "no edge"


def analyse_factors(rows: list[dict[str, Any]]) -> dict[str, Any]:
    add_factor_ranks(rows)
    out = {}
    for name in FACTOR_SIGNS:
        report = {str(h): factor_report(rows, name, h) for h in HORIZONS}
        report["verdict"] = verdict(report)
        out[name] = report
    return out


def render_factors(payload: dict[str, Any]) -> str:
    f = payload["factors"]
    lines = [f"# Θεμελιώδεις παράγοντες: εργαστήριο ({payload['generated_at'][:10]})", "",
             f"{payload['companies']} εταιρείες, {payload['observations']} παρατηρήσεις αρχής μήνα, {payload['first_date']} έως "
             f"{payload['last_date']}. Μόνο ό,τι είχε κατατεθεί στην SEC ως εκείνη τη μέρα. Υπέρβαση = απόδοση μείον τη διάμεσο "
             "όλων των εταιρειών της ίδιας μέρας. Μόνο έρευνα: τίποτα δεν αλλάζει σκορ, όρια ή εντολές.", "",
             "Κατεύθυνση: κάθε παράγοντας μετριέται προς την κατεύθυνση της βιβλιογραφίας (π.χ. λιγότερη έκδοση μετοχών = "
             "καλύτερο). Θετικό = η βιβλιογραφία επιβεβαιώνεται στο δικό σου σύμπαν.", "",
             "Όριο για shadow: σωστό πρόσημο σε 20 και 60 συνεδριάσεις, t ≥ 2 στο rank IC των 60, θετικό και στις μεγάλες "
             "εταιρείες (≥ $300 εκ.). Για πρόταση προαγωγής: t ≥ 3.", "",
             f"## Σύνοψη, {MAIN_HORIZON} συνεδριάσεις", "",
             "| παράγοντας | κάλυψη | rank IC (t) | καλύτερο − χειρότερο 20% | long πλευρά | short πλευρά | μεγάλες | micro | μέσα σε σκορ ≥ 60 | απόφαση |",
             "|---|---|---|---|---|---|---|---|---|---|"]

    def cell(s):
        return "—" if s.get("spread_pct") is None else f"{s['spread_pct']:+.2f}% [{s['ci95_pct'][0]}, {s['ci95_pct'][1]}] {s['years_positive']}"

    for name, report in f.items():
        r = report[str(MAIN_HORIZON)]
        ic = r["rank_ic"]
        ic_text = "—" if ic.get("ic") is None else f"{ic['ic']:+.3f} ({ic['t']})"
        lines.append(f"| {name} | {r['coverage']:.0%} | {ic_text} | {cell(r['best_vs_worst'])} | {cell(r['long_leg'])} | "
                     f"{cell(r['short_leg'])} | {cell(r['large_caps'])} | {cell(r['micro_caps'])} | "
                     f"{cell(r['within_rules_score_60'])} | **{report['verdict']}** |")
    lines += ["", "## Rank IC ανά ορίζοντα (t)", "", "| παράγοντας | " + " | ".join(f"{h} συν." for h in HORIZONS) + " |",
              "|---|" + "---|" * len(HORIZONS)]
    for name, report in f.items():
        lines.append(f"| {name} | " + " | ".join(
            "—" if report[str(h)]["rank_ic"].get("ic") is None else f"{report[str(h)]['rank_ic']['ic']:+.3f} ({report[str(h)]['rank_ic']['t']})"
            for h in HORIZONS) + " |")
    lines += ["", "Σημειώσεις:",
              "- Survivorship: οι τιμές έχουν μόνο τις σημερινές εταιρείες· όσες διαγράφηκαν λείπουν. Αυτό ευνοεί τις κακές "
              "μετοχές, άρα η short πλευρά μάλλον υποτιμάται.",
              "- Το beta δεν αφαιρείται: η βάση είναι η διάμεσος της ίδιας μέρας, όχι το SPY.",
              "- `insider_*`: αγορές (P) και πωλήσεις (S) στο χρηματιστήριο από Form 4 (insider_transactions.py). "
              "Μηδέν αγοραστές μέσα στην περίοδο που καλύπτουν τα αρχεία της SEC είναι γνωστό μηδέν· έξω από αυτήν, άγνωστο.",
              "- `cash_op_profit` είναι προσέγγιση (λειτουργικά κέρδη μείον accruals), όχι η ακριβής κατασκευή Ball et al.",
              "- Τα στοιχεία χρηματοδότησης που μια εταιρεία δεν ανέφερε δεν γίνονται μηδέν· το «μερικό» μετρά πόσες "
              "τιμές χτίστηκαν χωρίς κάποιο από αυτά."]
    lines += ["", "Μερικές τιμές (χωρίς κάποιο προαιρετικό στοιχείο): " + ", ".join(
        f"{name} {report[str(MAIN_HORIZON)]['partial_share']:.0%}" for name, report in f.items()
        if report[str(MAIN_HORIZON)].get("partial_share") is not None)]
    return "\n".join(lines) + "\n"


# --- Phase 3 check on the system's own trades: would the composites have kept us out of the losers? ------------
POSITIONS = ROOT / "data" / "paper_positions_closed.jsonl"


def _company_factors(task: tuple[str, str, list[str]]) -> tuple[str, dict[str, Any]]:
    ticker, facts_path, days = task
    try:
        raw = json.loads(gzip.decompress(Path(facts_path).read_bytes()))
    except (OSError, EOFError, ValueError):
        return ticker, {}
    return ticker, {d: fundamental_factors.compute(raw, date.fromisoformat(d))["values"] for d in days}


def positions_check(path: Path = POSITIONS, workers: int = 4) -> dict[str, Any]:
    """Closed paper positions grouped by the composites they had on the day they opened (read-only).

    Each position's factors are computed with the facts filed by its opening session and ranked against every company
    with stored facts on that same day, as a run would rank them. Descriptive only: a few hundred trades over a few
    weeks are not a test, they show whether the filter would have cut more losers than winners."""
    import periodic_filing

    try:
        positions = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    except OSError:
        return {"positions": 0, "reason": f"{path.name} not found"}
    positions = [p for p in positions if p.get("symbol") and p.get("opened_session") and
                 isinstance(p.get("exit_plpc"), (int, float)) and p.get("direction") in ("long", "short")]
    days = sorted({p["opened_session"] for p in positions})
    mapping = periodic_filing.ticker_to_cik(TICKERS)
    tasks = []
    for ticker, cik in mapping.items():
        facts = FACTS / f"CIK{int(cik):010d}.json.gz"
        if facts.exists():
            tasks.append((ticker, str(facts), days))
    by_day: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
    with ProcessPoolExecutor(max_workers=workers) as pool:
        for ticker, per_day in pool.map(_company_factors, tasks, chunksize=32):
            for day, values in per_day.items():
                if any(v is not None for v in values.values()):
                    by_day[day][ticker] = values
    combos_by_day = {}
    for day, companies in by_day.items():
        tickers = list(companies)
        combos_by_day[day] = dict(zip(tickers, fundamental_factors.composites({"values": companies[t]} for t in tickers)))
    groups: dict[tuple[str, str, str], list[float]] = defaultdict(list)
    for p in positions:
        combo = combos_by_day.get(p["opened_session"], {}).get(str(p["symbol"]).upper()) or {}
        for name in ("long_quality", "avoid"):
            label = fundamental_factors.quintile_label(combo.get(name)) or "unknown"
            groups[(p["direction"], name, label)].append(float(p["exit_plpc"]) * 100.0)
    table = []
    for (direction, name, label), values in sorted(groups.items()):
        table.append({"direction": direction, "composite": name, "bucket": label, "n": len(values),
                      "mean_result_pct": round(_mean(values), 2),
                      "win_rate": round(sum(1 for v in values if v > 0) / len(values), 2)})
    return {"positions": len(positions), "first_session": days[0] if days else None,
            "last_session": days[-1] if days else None, "table": table}


def render_positions(check: dict[str, Any]) -> str:
    lines = ["# Θεμελιώδεις δείκτες και τα δικά μας paper trades", "",
             f"{check['positions']} κλεισμένες θέσεις, {check.get('first_session')} έως {check.get('last_session')}. "
             "Οι δείκτες όπως ήταν την ημέρα που άνοιξε η θέση, ταξινομημένοι απέναντι σε όλες τις εταιρείες εκείνης της "
             "μέρας. Μόνο περιγραφή: λίγες εβδομάδες δεν είναι δοκιμή. Τίποτα δεν αλλάζει σε traders ή εντολές.", "",
             "Τι ψάχνουμε: long σε «avoid top 20%» ή short σε «long_quality top 20%» να πήγαν χειρότερα από τα υπόλοιπα.", "",
             "| κατεύθυνση | δείκτης | ομάδα | θέσεις | μέσο αποτέλεσμα % | κερδοφόρες |", "|---|---|---|---|---|---|"]
    for row in check.get("table", []):
        lines.append(f"| {row['direction']} | {row['composite']} | {row['bucket']} | {row['n']} | "
                     f"{row['mean_result_pct']:+.2f} | {row['win_rate']:.0%} |")
    return "\n".join(lines) + "\n"


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
    parser.add_argument("--paper-positions", action="store_true",
                        help="only the check of the closed paper positions against the fundamental composites")
    args = parser.parse_args(argv)
    if args.paper_positions:
        check = positions_check(POSITIONS, args.workers)
        OUT.mkdir(parents=True, exist_ok=True)
        (OUT / "fundamental_factors_positions_latest.json").write_text(json.dumps(check, indent=2), encoding="utf-8")
        (OUT / "fundamental_factors_positions_latest.md").write_text(render_positions(check), encoding="utf-8")
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        print(render_positions(check))
        return 0
    rules = json.loads(CONFIG.read_text(encoding="utf-8"))["fundamental_rules"]
    rows = collect(args.limit, args.workers)
    add_excess(rows)
    payload = {"schema": SCHEMA, "generated_at": datetime.now(timezone.utc).isoformat(),
               "companies": len({r["ticker"] for r in rows}), "observations": len(rows),
               "first_date": min((r["date"] for r in rows), default=None),
               "last_date": max((r["date"] for r in rows), default=None),
               "horizons": HORIZONS, "analysis": analyse(rows, rules)}
    payload["factors"] = analyse_factors(rows)  # after analyse(): it uses the rules score of each row
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "fundamental_rules_latest.json").write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    (OUT / "fundamental_rules_latest.md").write_text(render(payload), encoding="utf-8")
    (OUT / "fundamental_factors_latest.md").write_text(render_factors(payload), encoding="utf-8")
    print(render(payload))
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # the Greek report on a cp1253 console
    print(render_factors(payload))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
