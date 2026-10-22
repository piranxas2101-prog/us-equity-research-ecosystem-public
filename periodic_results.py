"""Results per period: the last quarters (or half-years / years) of every listed company, as a trend, from the SEC.

Owner's decision 2026-10-22: the agents should read and analyse each company's results quarter by quarter, half
year or year, not only one trailing snapshot. Built deterministically from the companyfacts stored every night
(``cache/sec_companyfacts``; 10-Q, 10-K, 20-F, 40-F and the 6-K that carry XBRL), no LLM, in shadow: nothing reads
it to decide yet; Giannis and Sokratis can show it next to their own view and it is judged against outcomes first.

Per company:

* cadence: ``quarterly`` (10-Q filers), ``semiannual`` or ``annual`` (foreign filers whose interim reports carry no
  XBRL - their half-year 6-K is then unknown, not zero);
* the last ``PERIODS`` discrete periods of revenue, gross profit, operating income, net income and operating cash
  flow (a quarter reported only as year-to-date is the difference of two year-to-date figures; the fourth quarter
  is the year minus nine months), cash at each period end and diluted shares;
* per period: margins and growth against the same period a year earlier (and the previous period);
* tags: revenue accelerating / decelerating (year-on-year growth up or down by ``ACCEL_PP`` points against the
  previous period), gross and operating margin improving / worsening (``MARGIN_PP`` points year on year), dilution
  (diluted shares up ``DILUTION_PCT``% or more in a year), loss narrowing / widening, cash runway in months when
  operating cash flow is negative over the last year.

Unknown stays unknown: a period, ratio or tag whose inputs are missing is None and listed as missing.
"""
from __future__ import annotations

import argparse
from datetime import date, datetime, timezone
import gzip
import json
from pathlib import Path
from typing import Any

import periodic_filing

ROOT = Path(__file__).resolve().parent
FORMS = frozenset({"10-K", "10-K/A", "10-KT", "10-Q", "10-Q/A", "10-QT", "20-F", "20-F/A", "40-F", "40-F/A",
                   "6-K", "6-K/A"})
PERIODS = 8
ACCEL_PP = 5.0
MARGIN_PP = 2.0
DILUTION_PCT = 10.0
FLOWS = {
    "revenue": ("RevenueFromContractWithCustomerExcludingAssessedTax", "Revenues", "SalesRevenueNet", "Revenue",
                "RevenueFromContractsWithCustomers", "RevenueFromContractWithCustomerIncludingAssessedTax"),
    "gross_profit": ("GrossProfit",),
    "cost_of_revenue": ("CostOfRevenue", "CostOfGoodsAndServicesSold", "CostOfGoodsSold", "CostOfSales"),
    "operating_income": ("OperatingIncomeLoss", "ProfitLossFromOperatingActivities"),
    "net_income": ("NetIncomeLoss", "ProfitLoss", "ProfitLossAttributableToOwnersOfParent"),
    "operating_cash_flow": ("NetCashProvidedByUsedInOperatingActivities", "CashFlowsFromUsedInOperatingActivities"),
    "diluted_shares": ("WeightedAverageNumberOfDilutedSharesOutstanding",
                       "WeightedAverageNumberOfSharesOutstandingDiluted",
                       "AdjustedWeightedAverageShares"),
}
INSTANTS = {"cash": ("CashAndCashEquivalentsAtCarryingValue",
                     "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents", "CashAndCashEquivalents")}
LENGTH = {"quarter": (80, 100), "half": (170, 195), "nine": (260, 285), "year": (340, 380)}


def _day(value: Any) -> date | None:
    try:
        return date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return None


def _kind(start: date | None, end: date) -> str | None:
    if start is None:
        return None
    days = (end - start).days
    return next((k for k, (lo, hi) in LENGTH.items() if lo <= days <= hi), None)


def concept_facts(raw: dict[str, Any], concepts: tuple[str, ...], as_of: date, shares: bool = False
                  ) -> list[dict[str, Any]]:
    """Facts of the concept with the most recent period (companies switch concepts over time), one per period,
    the latest filing winning."""
    best: list[dict[str, Any]] = []
    best_end: date | None = None
    for concept in concepts:
        found: dict[tuple[Any, Any], dict[str, Any]] = {}
        for taxonomy in ("us-gaap", "ifrs-full"):
            units = (((raw.get("facts") or {}).get(taxonomy) or {}).get(concept) or {}).get("units", {})
            for unit, rows in units.items():
                if (unit != "shares") if shares else len(str(unit)) != 3:
                    continue
                for fact in rows or []:
                    end, filed = _day(fact.get("end")), _day(fact.get("filed"))
                    if fact.get("form") not in FORMS or end is None or filed is None or filed > as_of:
                        continue
                    if not isinstance(fact.get("val"), (int, float)):
                        continue
                    key = (fact.get("start"), fact.get("end"))
                    if key not in found or str(fact.get("filed")) > str(found[key]["filed"]):
                        found[key] = {"start": _day(fact.get("start")), "end": end, "val": float(fact["val"]),
                                      "unit": unit, "filed": str(fact.get("filed")), "form": fact.get("form")}
        if found:
            latest = max(f["end"] for f in found.values())
            if best_end is None or latest > best_end:
                best, best_end = list(found.values()), latest
    return best


def discrete(facts: list[dict[str, Any]], derive: bool = True) -> dict[str, dict[date, float]]:
    """Discrete quarter, half-year and year values by period end. Year-to-date figures sharing a fiscal-year start
    are differenced (6M - 3M, 9M - 6M, 12M - 9M); a directly reported period wins over a derived one. Averages
    (diluted shares) are never differenced: ``derive=False``."""
    out: dict[str, dict[date, float]] = {"quarter": {}, "half": {}, "year": {}}
    by_start: dict[date, dict[str, dict[str, Any]]] = {}
    for f in facts:
        kind = _kind(f["start"], f["end"])
        if kind is None:
            continue
        if kind in out:
            out[kind][f["end"]] = f["val"]
        by_start.setdefault(f["start"], {})[kind] = f
    for cumulative in by_start.values() if derive else ():
        q, h, n, y = (cumulative.get(k) for k in ("quarter", "half", "nine", "year"))
        if h and q and h["end"] not in out["quarter"]:
            out["quarter"].setdefault(h["end"], h["val"] - q["val"])
        if n and h:
            out["quarter"].setdefault(n["end"], n["val"] - h["val"])
        if y and n:
            out["quarter"].setdefault(y["end"], y["val"] - n["val"])
        if y and h:
            out["half"].setdefault(y["end"], y["val"] - h["val"])
    return out


def _near_key(series: dict[date, float], end: date, days: int = 10) -> float | None:
    return next((v for k, v in series.items() if abs((k - end).days) <= days), None)


def _year_before(day: date) -> date:
    try:
        return day.replace(year=day.year - 1)
    except ValueError:
        return day.replace(year=day.year - 1, day=28)


def _pct(a: float | None, b: float | None) -> float | None:
    return None if a is None or b is None or b == 0 else round((a - b) / abs(b) * 100, 2)


def _margin(part: float | None, revenue: float | None) -> float | None:
    return None if part is None or not revenue else round(part / revenue * 100, 2)


def analyse(raw: dict[str, Any], as_of: date) -> dict[str, Any]:
    series = {name: discrete(concept_facts(raw, concepts, as_of, shares=name == "diluted_shares"),
                             derive=name != "diluted_shares")
              for name, concepts in FLOWS.items()}
    # many filers tag cost of revenue instead of gross profit: gross profit = revenue - cost, period by period
    for kind, values in series["revenue"].items():
        for end, value in values.items():
            cost = series["cost_of_revenue"][kind].get(end)
            if end not in series["gross_profit"][kind] and cost is not None:
                series["gross_profit"][kind][end] = value - cost
    cash_facts = sorted((f for f in concept_facts(raw, INSTANTS["cash"], as_of) if f["start"] is None),
                        key=lambda f: f["end"])
    revenue = series["revenue"]
    cadence = ("quarterly" if len(revenue["quarter"]) >= 3 else "semiannual" if len(revenue["half"]) >= 2
               else "annual" if revenue["year"] else None)
    if cadence is None:
        return {"cadence": None, "periods": [], "tags": {}, "missing": ["revenue by period"]}
    kind = {"quarterly": "quarter", "semiannual": "half", "annual": "year"}[cadence]
    ends = sorted(revenue[kind])[-PERIODS:]
    periods = []
    for end in ends:
        row: dict[str, Any] = {"end": str(end)}
        for name in FLOWS:
            if name != "cost_of_revenue":
                row[name] = _near_key(series[name][kind], end, 0)
        cash = next((f["val"] for f in reversed(cash_facts) if abs((f["end"] - end).days) <= 10), None)
        row["cash"] = cash
        row["gross_margin_pct"] = _margin(row["gross_profit"], row["revenue"])
        row["operating_margin_pct"] = _margin(row["operating_income"], row["revenue"])
        row["net_margin_pct"] = _margin(row["net_income"], row["revenue"])
        prior = _year_before(end)
        row["revenue_yoy_pct"] = _pct(row["revenue"], _near_key(revenue[kind], prior))
        row["diluted_shares_yoy_pct"] = _pct(row["diluted_shares"], _near_key(series["diluted_shares"][kind], prior))
        prior_gm = _margin(_near_key(series["gross_profit"][kind], prior), _near_key(revenue[kind], prior))
        prior_om = _margin(_near_key(series["operating_income"][kind], prior), _near_key(revenue[kind], prior))
        row["gross_margin_yoy_pp"] = None if row["gross_margin_pct"] is None or prior_gm is None else \
            round(row["gross_margin_pct"] - prior_gm, 2)
        row["operating_margin_yoy_pp"] = None if row["operating_margin_pct"] is None or prior_om is None else \
            round(row["operating_margin_pct"] - prior_om, 2)
        row["net_income_prior_year"] = _near_key(series["net_income"][kind], prior)
        periods.append(row)
    for previous, current in zip(periods, periods[1:]):
        current["revenue_vs_previous_pct"] = _pct(current["revenue"], previous["revenue"])
    return {"cadence": cadence, "periods": periods, "tags": tags(periods, cadence),
            "currency": next((f["unit"] for f in concept_facts(raw, FLOWS["revenue"], as_of)), None),
            "missing": sorted({k for p in periods[-1:] for k, v in p.items() if v is None and k != "end"})}


def tags(periods: list[dict[str, Any]], cadence: str) -> dict[str, Any]:
    if not periods:
        return {}
    last = periods[-1]
    before = periods[-2] if len(periods) >= 2 else {}
    g1, g0 = last.get("revenue_yoy_pct"), before.get("revenue_yoy_pct")
    out: dict[str, Any] = {
        "revenue_trend": None if g1 is None or g0 is None else
        "accelerating" if g1 - g0 >= ACCEL_PP else "decelerating" if g0 - g1 >= ACCEL_PP else "steady",
        "gross_margin": _direction(last.get("gross_margin_yoy_pp")),
        "operating_margin": _direction(last.get("operating_margin_yoy_pp")),
        "dilution": None if last.get("diluted_shares_yoy_pct") is None else
        last["diluted_shares_yoy_pct"] >= DILUTION_PCT,
    }
    ni, ni0 = last.get("net_income"), last.get("net_income_prior_year")
    out["losses"] = (None if ni is None or ni0 is None or (ni >= 0 and ni0 >= 0) else
                     "narrowing" if ni > ni0 else "widening")
    per_year = {"quarterly": 4, "semiannual": 2, "annual": 1}[cadence]
    flows = [p.get("operating_cash_flow") for p in periods[-per_year:]]
    cash = last.get("cash")
    if len(flows) == per_year and all(f is not None for f in flows) and cash is not None:
        yearly = sum(flows)
        out["cash_runway_months"] = None if yearly >= 0 else round(cash / (-yearly / 12), 1)
        out["operating_cash_flow_last_year"] = yearly
    else:
        out["cash_runway_months"] = None
    return out


def _direction(pp: float | None) -> str | None:
    if pp is None:
        return None
    return "improving" if pp >= MARGIN_PP else "worsening" if pp <= -MARGIN_PP else "steady"


def build(tickers: list[str], as_of: date, directory: Path = periodic_filing.DEFAULT_DIR,
          tickers_file: Path = periodic_filing.DEFAULT_TICKERS, cache: Path | None = None) -> dict[str, Any]:
    """``cache``: per company, the result and the modification time of the companyfacts file it was built from; an
    unchanged file is not read again (every listed company is covered each night, owner 2026-10-22)."""
    mapping = periodic_filing.ticker_to_cik(tickers_file)
    stored: dict[str, Any] = {}
    if cache is not None:
        try:
            stored = json.loads(cache.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            stored = {}
    companies: dict[str, Any] = {}
    for ticker in tickers:
        cik = mapping.get(ticker.upper())
        path = directory / f"CIK{int(cik):010d}.json.gz" if cik else None
        if path is None or not path.exists():
            companies[ticker] = {"cadence": None, "periods": [], "tags": {}, "missing": ["companyfacts"]}
            continue
        mtime = path.stat().st_mtime
        previous = stored.get(str(cik))
        if previous and previous.get("mtime") == mtime:
            companies[ticker] = previous["result"]
            continue
        try:
            raw = json.loads(gzip.decompress(path.read_bytes()))
        except (OSError, EOFError, ValueError):
            companies[ticker] = {"cadence": None, "periods": [], "tags": {}, "missing": ["companyfacts unreadable"]}
            continue
        companies[ticker] = {"cik": cik, **analyse(raw, as_of)}
        stored[str(cik)] = {"mtime": mtime, "result": companies[ticker]}
    if cache is not None:
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(stored, default=str), encoding="utf-8")
    counts: dict[str, dict[str, int]] = {}
    for info in companies.values():
        for tag, value in (info.get("tags") or {}).items():
            if tag in ("cash_runway_months", "operating_cash_flow_last_year"):
                continue
            counts.setdefault(tag, {})[str(value)] = counts.setdefault(tag, {}).get(str(value), 0) + 1
    return {"schema": "ecosystem.periodic_results.v1", "generated_at": datetime.now(timezone.utc).isoformat(),
            "as_of": str(as_of), "companies": companies, "tag_counts": counts,
            "cadences": {c: sum(1 for i in companies.values() if i.get("cadence") == c)
                         for c in ("quarterly", "semiannual", "annual", None)},
            "decisions_changed": False}


def render(report: dict[str, Any], detail: list[str] | None = None) -> str:
    lines = ["# Αποτελέσματα ανά περίοδο (SEC, σκιώδες)", "",
             f"Εταιρείες {len(report['companies'])} · ανά τρίμηνο {report['cadences'].get('quarterly')} · ανά εξάμηνο "
             f"{report['cadences'].get('semiannual')} · μόνο ετήσια {report['cadences'].get('annual')} · χωρίς στοιχεία "
             f"{report['cadences'].get('None', report['cadences'].get(None))}. Τίποτα δεν αλλάζει απόφαση.", "",
             "## Ετικέτες", ""]
    for tag, values in sorted(report["tag_counts"].items()):
        lines.append(f"- {tag}: " + ", ".join(f"{k} {v}" for k, v in sorted(values.items())))
    for ticker in detail or []:
        info = report["companies"].get(ticker)
        if not info:
            continue
        lines += ["", f"## {ticker} ({info.get('cadence')}, {info.get('currency')})", "",
                  "| περίοδος | έσοδα | ανάπτυξη έτους % | μικτό περιθ. % | λειτ. περιθ. % | καθαρά | λειτ. ταμ. ροές | ταμείο | μετοχές (dil.) |",
                  "|---|---|---|---|---|---|---|---|---|"]
        for p in info["periods"]:
            def m(x: Any) -> str:
                return "άγνωστο" if x is None else f"{x / 1e6:,.1f}M" if isinstance(x, float) and abs(x) >= 1e5 else str(x)
            lines.append(f"| {p['end']} | {m(p['revenue'])} | {m(p['revenue_yoy_pct'])} | {m(p['gross_margin_pct'])} | "
                         f"{m(p['operating_margin_pct'])} | {m(p['net_income'])} | {m(p['operating_cash_flow'])} | "
                         f"{m(p['cash'])} | {m(p['diluted_shares'])} |")
        lines.append("")
        lines.append("Ετικέτες: " + ", ".join(f"{k} = {v}" for k, v in (info.get("tags") or {}).items()))
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--shortlist", type=Path, default=ROOT / "output" / "babis_shortlist_latest.json")
    parser.add_argument("--tickers", nargs="*", default=[], help="extra tickers, also shown in detail")
    parser.add_argument("--out", type=Path, default=ROOT / "output")
    parser.add_argument("--companyfacts", type=Path, default=periodic_filing.DEFAULT_DIR)
    parser.add_argument("--sec-tickers", type=Path, default=periodic_filing.DEFAULT_TICKERS)
    parser.add_argument("--cache", type=Path, default=ROOT / "data" / "periodic_results_cache.json")
    args = parser.parse_args(argv)
    import research_universe

    # every listed company, not only the shortlist (owner 2026-10-22)
    tickers = sorted(set(research_universe.company_tickers(args.shortlist.parent)) | {t.upper() for t in args.tickers})
    report = build(tickers, date.today(), args.companyfacts, args.sec_tickers, args.cache)
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "periodic_results_latest.json").write_text(json.dumps(report, indent=1, default=str), encoding="utf-8")
    (args.out / "periodic_results_latest.md").write_text(render(report, [t.upper() for t in args.tickers]),
                                                         encoding="utf-8")
    print(json.dumps({"companies": len(report["companies"]), "cadences": report["cadences"]}, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
