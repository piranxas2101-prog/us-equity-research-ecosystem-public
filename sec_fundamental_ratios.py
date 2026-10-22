"""Shadow check: the Babis rule ratios computed from SEC XBRL, compared with the yfinance values that decide today.

The stage-1 rules (roe, margins, growth, debt_to_equity, current_ratio) are evaluated on yfinance ``info``. The
owner's principle is that SEC is the primary source and yfinance a lagging, secondary one. Before any switch, this
module computes the same ratios from the stored SEC companyfacts and reports, per rule, how often the pass/fail
verdict agrees. It only reads: no shortlist, score, tier or packet is changed.

Definitions (trailing twelve months where flows are involved):
- TTM = last fiscal year + current year-to-date - same year-to-date a year earlier. With no year-to-date, the fiscal year.
- roe = TTM net income / latest stockholders' equity (None when equity is not positive).
- profit_margin, gross_margin = TTM net income or gross profit / TTM revenue.
- revenue_growth, earnings_growth = latest year-to-date against the same period a year earlier (annual against
  the prior fiscal year with no year-to-date). yfinance reports the latest quarter, so this is close but not equal.
- current_ratio = current assets / current liabilities at the latest balance sheet.
- debt_to_equity = long-term debt / equity * 100 (yfinance unit). Lower bound: SEC has no single total-debt fact.
Missing inputs give None: unknown stays unknown. forward_pe is analyst data and has no SEC equivalent.
"""
from __future__ import annotations

import argparse
from datetime import date, datetime, timezone
import gzip
import json
import operator
from pathlib import Path
from typing import Any

import periodic_filing
import sec_xbrl_pilot

SCHEMA = "ecosystem.sec_fundamental_ratios_shadow.v1"
OPERATORS = {">=": operator.ge, "<=": operator.le, ">": operator.gt, "<": operator.lt}
SEC_METRICS = ("roe", "profit_margin", "gross_margin", "revenue_growth", "earnings_growth",
               "debt_to_equity", "current_ratio")
FORMS = frozenset({"10-K", "10-KT", "10-Q", "10-QT", "20-F", "40-F"})
CONCEPTS = {
    "revenue": sec_xbrl_pilot.FIELD_CONCEPTS["revenue"],
    "net_income": sec_xbrl_pilot.FIELD_CONCEPTS["net_income"],
    "gross_profit": ("GrossProfit", "GrossProfitLoss"),
    "equity": sec_xbrl_pilot.FIELD_CONCEPTS["stockholders_equity"],
    "debt": sec_xbrl_pilot.FIELD_CONCEPTS["debt"],
    "current_assets": ("AssetsCurrent",),
    "current_liabilities": ("LiabilitiesCurrent",),
}


def _day(value: Any) -> date | None:
    try:
        return date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return None


def _facts(raw: dict[str, Any], concepts: tuple[str, ...], as_of: date) -> list[dict[str, Any]]:
    """Facts of the concept with the most recent period, deduplicated by period (latest filing wins).

    Companies change concepts over time (NVIDIA reported ``Revenues`` until 2022 and
    ``RevenueFromContractWithCustomerExcludingAssessedTax`` after): taking the first concept with any facts mixed a
    2022 revenue with a 2026 net income. Concept order only breaks ties between equally recent concepts.
    """
    best: list[dict[str, Any]] = []
    best_end: date | None = None
    for concept in concepts:
        found = _concept_facts(raw, concept, as_of)
        if not found:
            continue
        latest = max(fact["end"] for fact in found)
        if best_end is None or latest > best_end:
            best, best_end = found, latest
    return best


def _concept_facts(raw: dict[str, Any], concept: str, as_of: date) -> list[dict[str, Any]]:
    """Facts of one concept, deduplicated by period (latest filing wins)."""
    found: dict[tuple[Any, Any, str], dict[str, Any]] = {}
    for taxonomy in ("us-gaap", "ifrs-full"):
        for unit, rows in (((raw.get("facts") or {}).get(taxonomy) or {}).get(concept) or {}).get("units", {}).items():
            if len(str(unit)) != 3:
                continue
            for fact in rows or []:
                end, filed = _day(fact.get("end")), _day(fact.get("filed"))
                if fact.get("form") not in FORMS or end is None or filed is None or filed > as_of or end > as_of:
                    continue
                if not isinstance(fact.get("val"), (int, float)):
                    continue
                key = (fact.get("start"), fact.get("end"), unit)
                if key not in found or str(fact.get("filed")) > str(found[key].get("filed")):
                    found[key] = {"start": _day(fact.get("start")), "end": end, "val": float(fact["val"]),
                                  "unit": unit, "filed": filed}
    return list(found.values())


def _near(a: date | None, b: date | None, days: int = 6) -> bool:
    return a is not None and b is not None and abs((a - b).days) <= days


def _years_back(day: date) -> date:
    try:
        return day.replace(year=day.year - 1)
    except ValueError:  # 29 February
        return day.replace(year=day.year - 1, day=28)


def flow(facts: list[dict[str, Any]]) -> dict[str, Any] | None:
    """TTM and the latest year-on-year growth of one income-statement item, or None when it cannot be built."""
    flows = [f for f in facts if f["start"] is not None]
    annual = sorted((f for f in flows if 340 <= (f["end"] - f["start"]).days <= 380), key=lambda f: f["end"])
    if not annual:
        return None
    latest = annual[-1]
    prior = next((f for f in reversed(annual[:-1]) if _near(f["end"], _years_back(latest["end"]))), None)
    ytd = sorted((f for f in flows if f["end"] > latest["end"] and _near(f["start"], latest["end"], 4)
                  and 25 <= (f["end"] - f["start"]).days <= 300), key=lambda f: f["end"])
    if not ytd:
        growth = None if prior is None or prior["val"] == 0 else (latest["val"] - prior["val"]) / abs(prior["val"])
        return {"ttm": latest["val"], "growth": growth, "end": latest["end"], "unit": latest["unit"]}
    current = ytd[-1]
    base = next((f for f in flows if _near(f["start"], _years_back(current["start"]))
                 and _near(f["end"], _years_back(current["end"]))), None)
    if base is None:
        return None
    growth = None if base["val"] == 0 else (current["val"] - base["val"]) / abs(base["val"])
    return {"ttm": latest["val"] + current["val"] - base["val"], "growth": growth, "end": current["end"],
            "unit": current["unit"]}


def instant(facts: list[dict[str, Any]]) -> dict[str, Any] | None:
    points = sorted((f for f in facts if f["start"] is None), key=lambda f: (f["end"], f["filed"]))
    return points[-1] if points else None


def _ratio(numerator: float | None, denominator: float | None) -> float | None:
    return None if numerator is None or not denominator else numerator / denominator


def compute_ratios(raw: dict[str, Any], as_of: date) -> dict[str, Any]:
    f = {name: _facts(raw, concepts, as_of) for name, concepts in CONCEPTS.items()}
    revenue, income, gross = flow(f["revenue"]), flow(f["net_income"]), flow(f["gross_profit"])
    equity, debt = instant(f["equity"]), instant(f["debt"])
    assets, liabilities = instant(f["current_assets"]), instant(f["current_liabilities"])

    def same_unit(*parts: dict[str, Any] | None) -> bool:
        return all(p is not None for p in parts) and len({p["unit"] for p in parts if p}) == 1

    positive_equity = equity["val"] if equity and equity["val"] > 0 else None
    def same_period(a: dict[str, Any] | None, b: dict[str, Any] | None) -> bool:
        return a is not None and b is not None and _near(a["end"], b["end"])  # never divide numbers of two periods

    values: dict[str, float | None] = {
        "roe": _ratio(income["ttm"], positive_equity) if same_unit(income, equity) and positive_equity else None,
        "profit_margin": (_ratio(income["ttm"], revenue["ttm"])
                          if same_unit(income, revenue) and same_period(income, revenue) else None),
        "gross_margin": (_ratio(gross["ttm"], revenue["ttm"])
                         if same_unit(gross, revenue) and same_period(gross, revenue) else None),
        "revenue_growth": revenue["growth"] if revenue else None,
        "earnings_growth": income["growth"] if income else None,
        "debt_to_equity": (_ratio(debt["val"], positive_equity) * 100.0
                           if same_unit(debt, equity) and positive_equity else None),
        "current_ratio": (_ratio(assets["val"], liabilities["val"])
                          if same_unit(assets, liabilities) and assets["end"] == liabilities["end"] else None),
    }
    return {"values": values,
            "periods": {"income_statement_end": (income or revenue or {}).get("end"),
                        "balance_sheet_end": (equity or {}).get("end")}}


def verdict(rule: dict[str, Any], value: float | None) -> str:
    compare = OPERATORS.get(str(rule.get("operator")))
    threshold = rule.get("threshold")
    if compare is None or value is None or not isinstance(threshold, (int, float)):
        return "missing_data"
    limit = rule.get("invalid_at_or_below")
    if isinstance(limit, (int, float)) and value <= limit:
        return "failed"  # a value without meaning (negative forward P/E) never passes; valuation_gaps.py
    return "passed" if compare(value, threshold) else "failed"


def sec_first_rules(details: list[dict[str, Any]], sec_values: dict[str, float | None]) -> tuple[list[dict[str, Any]], float, dict[str, int]]:
    """Re-evaluate the Babis rules with the SEC value where there is one and the yfinance value only as a fallback.

    Same rules, thresholds and weights as stage 1, and the same score: matched weight over the full configured
    weight, so a missing value never improves a score. Each rule says where its value came from.
    """
    out: list[dict[str, Any]] = []
    sources = {"sec_edgar": 0, "yfinance_secondary": 0, "missing": 0}
    total = matched = 0.0
    for rule in details:
        weight = float(rule.get("weight") or 1.0)
        total += max(weight, 0.0)
        metric = rule.get("metric")
        sec_value = sec_values.get(metric) if metric in SEC_METRICS else None
        if sec_value is not None:
            value, source = sec_value, "sec_edgar"
        elif isinstance(rule.get("observed"), (int, float)):
            value, source = float(rule["observed"]), "yfinance_secondary"
        else:
            value, source = None, "missing"
        status = verdict(rule, value)
        sources[source if status != "missing_data" else "missing"] += 1
        if status == "passed":
            matched += weight
        entry = {k: v for k, v in rule.items() if k != "gap"}
        limit = rule.get("invalid_at_or_below")
        if isinstance(limit, (int, float)) and value is not None and value <= limit:
            entry["gap"] = "value_without_meaning"
        out.append({**entry, "observed": value, "status": status, "value_source": source if status != "missing_data" else "missing"})
    return out, round(matched / total * 100.0, 2) if total > 0 else 0.0, sources


def annotate_rows(rows: list[dict[str, Any]], as_of: date | None = None,
                  directory: Path = periodic_filing.DEFAULT_DIR,
                  tickers_file: Path = periodic_filing.DEFAULT_TICKERS) -> int:
    """Attach ``sec_ratio_values`` (metric -> value or None) to every row that has a stored SEC document."""
    ratios = load_ratios(rows, as_of or date.today(), directory, tickers_file)
    for row in rows:
        found = ratios.get(str(row.get("ticker") or ""))
        if found:
            row["sec_ratio_values"] = found["values"]
    return len(ratios)


def compare_rows(rows: list[dict[str, Any]], ratios_by_ticker: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Per metric: how often the SEC pass/fail agrees with the yfinance status stored on the shortlist row."""
    tally: dict[str, dict[str, int]] = {m: {"agree": 0, "disagree": 0, "sec_missing": 0, "yfinance_missing": 0,
                                           "both_missing": 0} for m in SEC_METRICS}
    differing_rows = 0
    compared_rows = 0
    for row in rows:
        ratios = ratios_by_ticker.get(str(row.get("ticker")))
        if not ratios:
            continue
        compared_rows += 1
        row_differs = False
        for rule in row.get("fundamental_rule_details") or []:
            metric = rule.get("metric")
            if metric not in tally:
                continue
            yf_status = rule.get("status")
            sec_status = verdict(rule, ratios["values"].get(metric))
            yf_missing, sec_missing = yf_status == "missing_data", sec_status == "missing_data"
            if yf_missing and sec_missing:
                tally[metric]["both_missing"] += 1
            elif sec_missing:
                tally[metric]["sec_missing"] += 1
            elif yf_missing:
                tally[metric]["yfinance_missing"] += 1
            elif yf_status == sec_status:
                tally[metric]["agree"] += 1
            else:
                tally[metric]["disagree"] += 1
                row_differs = True
        differing_rows += row_differs
    summary = {}
    for metric, counts in tally.items():
        both = counts["agree"] + counts["disagree"]
        summary[metric] = {**counts, "agreement_pct": round(counts["agree"] / both * 100.0, 1) if both else None}
    return {"compared_rows": compared_rows, "rows_with_a_disagreeing_rule": differing_rows, "by_metric": summary}


def load_ratios(rows: list[dict[str, Any]], as_of: date,
                directory: Path = periodic_filing.DEFAULT_DIR,
                tickers_file: Path = periodic_filing.DEFAULT_TICKERS) -> dict[str, dict[str, Any]]:
    mapping = periodic_filing.ticker_to_cik(tickers_file)
    result: dict[str, dict[str, Any]] = {}
    for row in rows:
        ticker = str(row.get("ticker") or "")
        cik = mapping.get(ticker.upper())
        path = directory / f"CIK{int(cik):010d}.json.gz" if cik else None
        if path is None or not path.exists():
            continue
        try:
            raw = json.loads(gzip.decompress(path.read_bytes()))
        except (OSError, EOFError, ValueError):
            continue
        result[ticker] = compute_ratios(raw, as_of)
    return result


FLOW_METRICS = ("roe", "profit_margin", "gross_margin", "revenue_growth", "earnings_growth")
BALANCE_METRICS = ("roe", "debt_to_equity", "current_ratio")
MAX_PERIOD_AGE_DAYS = 456  # ~15 months: an older income statement or balance sheet is unknown, not current


class SecRatioSource:
    """Babis's rule ratios from the stored SEC companyfacts, computed once per stored document.

    Ratios are cached per CIK with the document's hash (``cache/sec_ratios.json``), so a night recomputes only the
    companies whose companyfacts changed. A ratio built on an income statement or balance sheet older than
    MAX_PERIOD_AGE_DAYS is left unknown, so Babis falls back to yfinance for it (labelled secondary).
    """

    def __init__(self, directory: Path = periodic_filing.DEFAULT_DIR,
                 tickers_file: Path = periodic_filing.DEFAULT_TICKERS,
                 cache_path: Path | None = None, today: date | None = None) -> None:
        self.directory, self.today = Path(directory), today or date.today()
        self.cache_path = Path(cache_path) if cache_path else self.directory.parent / "sec_ratios.json"
        self.mapping = periodic_filing.ticker_to_cik(tickers_file)
        try:
            self.cache = json.loads(self.cache_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self.cache = {}
        self.changed = False

    def _entry(self, cik: str) -> dict[str, Any] | None:
        stem = f"CIK{int(cik):010d}"
        try:
            sha = json.loads((self.directory / f"{stem}.meta.json").read_text(encoding="utf-8"))["sha256"]
        except (OSError, ValueError, KeyError):
            return None
        cached = self.cache.get(cik)
        if cached and cached.get("sha256") == sha:
            return cached
        try:
            raw = json.loads(gzip.decompress((self.directory / f"{stem}.json.gz").read_bytes()))
        except (OSError, EOFError, ValueError):
            return None
        computed = compute_ratios(raw, self.today)
        entry = {"sha256": sha, "values": computed["values"],
                 "periods": {k: (v.isoformat() if isinstance(v, date) else v) for k, v in computed["periods"].items()}}
        self.cache[cik], self.changed = entry, True
        return entry

    def values(self, ticker: str) -> dict[str, float | None] | None:
        cik = self.mapping.get(str(ticker).upper())
        entry = self._entry(cik) if cik else None
        if not entry:
            return None
        values = dict(entry["values"])
        for period_key, metrics in (("income_statement_end", FLOW_METRICS), ("balance_sheet_end", BALANCE_METRICS)):
            end = _day(entry["periods"].get(period_key))
            if end is None or (self.today - end).days > MAX_PERIOD_AGE_DAYS:
                for metric in metrics:
                    values[metric] = None
        return values

    def save(self) -> None:
        if self.changed:
            self.cache_path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.cache_path.with_name(self.cache_path.name + ".tmp")
            temporary.write_text(json.dumps(self.cache), encoding="utf-8")
            temporary.replace(self.cache_path)
            self.changed = False


def render_markdown(payload: dict[str, Any]) -> str:
    lines = ["# SEC vs yfinance: Babis rules (shadow, read-only)", "",
             f"- Companies compared: {payload['compared_rows']} · with at least one disagreeing rule: "
             f"{payload['rows_with_a_disagreeing_rule']}", "",
             "| rule | agree | disagree | SEC missing | yfinance missing | agreement |", "|---|---|---|---|---|---|"]
    for metric, c in payload["by_metric"].items():
        lines.append(f"| {metric} | {c['agree']} | {c['disagree']} | {c['sec_missing']} | "
                     f"{c['yfinance_missing']} | {c['agreement_pct']}% |")
    lines += ["", "forward_pe has no SEC equivalent (analyst estimates) and stays yfinance, labelled secondary."]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--shortlist", default="output/babis_shortlist_latest.json")
    parser.add_argument("--out-prefix", default="output/sec_fundamental_ratios_shadow")
    args = parser.parse_args(argv)
    data = json.loads(Path(args.shortlist).read_text(encoding="utf-8"))
    rows = data.get("shortlist") or []
    as_of = date.today()
    payload = {"schema": SCHEMA, "generated_at": datetime.now(timezone.utc).isoformat(), "as_of": as_of.isoformat(),
               "source_shortlist": args.shortlist, **compare_rows(rows, load_ratios(rows, as_of))}
    prefix = Path(args.out_prefix)
    prefix.parent.mkdir(parents=True, exist_ok=True)
    Path(f"{prefix}_latest.json").write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    Path(f"{prefix}_latest.md").write_text(render_markdown(payload), encoding="utf-8")
    print(f"[OUTPUT] SEC vs yfinance shadow: {prefix}_latest.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
