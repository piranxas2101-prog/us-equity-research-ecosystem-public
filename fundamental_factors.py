"""Fundamental factors with post-publication evidence, from SEC companyfacts, point in time. Shadow only.

Owner 2026-11-15 (docs/FUNDAMENTAL_FACTORS_PLAN.md, research in docs/research/): the two families of accounting signals
that held up after publication are *does the company really earn cash* and *does it raise or return capital*. Neither
is part of Giannis's score. This module computes them from the stored companyfacts with only the facts filed by
``as_of`` (the same point-in-time helpers as ``sec_fundamental_ratios``), for the research lab (``fundamental_lab.py``)
and, in shadow, next to Giannis's score. It changes no score, tier, threshold or order.

Factors (trailing twelve months for flows; scaled by total assets one year before the period end):

- ``cfo_to_assets``: operating cash flow / lagged assets.
- ``cash_op_profit``: operating income - (net income - operating cash flow) / lagged assets. A wide-coverage
  approximation of Ball et al. (2016) cash-based operating profitability: operating profit with the total accruals
  taken out (the exact construction needs cost of revenue and SG&A, which ~60% / ~37% of filers tag).
- ``net_equity_finance``: (common stock issued - common stock repurchased) / lagged assets. Positive = raises equity.
- ``net_external_finance``: net equity finance - dividends + (debt issued - debt repaid), / lagged assets.
- ``d_roa``: last quarter's net income / assets minus the same quarter a year before (Q4 = fiscal year - nine months).
- ``asset_growth``: total assets / total assets one year before - 1.

Unknown stays unknown: a factor whose required inputs are missing is None. The financing items are reported only by
companies that had them, so a factor built with some of its optional items unreported lists them in ``partial``
(it is never filled with a zero silently). A flow counts only when it belongs to the same period as the operating cash
flow, so an item a company stopped reporting years ago never enters today's value.

Composites (in shadow, computed across the companies of one run or one lab date): each factor becomes a percentile,
signed by the literature, and a composite is the plain mean of the components that are known (Stambaugh-Yuan), shown
only with at least ``MIN_COMPONENTS`` known:

- ``long_quality``: profitability (cash_op_profit, else cfo_to_assets) up, d_roa up, net_equity_finance down.
- ``avoid``: net_equity_finance up, asset_growth up, profitability down. High = the profile that did worse.
"""
from __future__ import annotations

from datetime import date
import gzip
import hashlib
import json
from pathlib import Path
from typing import Any, Iterable

import sec_fundamental_ratios as sfr

SCHEMA = "ecosystem.fundamental_factors.v1"
FACTORS = ("cfo_to_assets", "cash_op_profit", "net_equity_finance", "net_external_finance", "d_roa", "asset_growth")
MAX_PERIOD_AGE_DAYS = sfr.MAX_PERIOD_AGE_DAYS
YEAR_TOLERANCE_DAYS = 10  # 52/53-week fiscal years move a year end by up to a week
MIN_COMPONENTS = 2
CONCEPTS = {
    "assets": ("Assets",),
    "cfo": ("NetCashProvidedByUsedInOperatingActivities", "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations"),
    "net_income": sfr.CONCEPTS["net_income"],
    "operating_income": ("OperatingIncomeLoss",),
    "issuance_total": ("ProceedsFromIssuanceOrSaleOfEquity",),
    "repurchase_total": ("PaymentsForRepurchaseOfEquity",),
    "repurchase_common": ("PaymentsForRepurchaseOfCommonStock",),
    "dividends": ("PaymentsOfDividendsCommonStock", "PaymentsOfDividends"),
    "debt_issued": ("ProceedsFromIssuanceOfLongTermDebt", "ProceedsFromIssuanceOfDebt"),
    "debt_repaid": ("RepaymentsOfLongTermDebt", "RepaymentsOfDebt"),
}
# Common-equity issuance is tagged as a total or as its parts; the parts are added only when there is no total, so
# nothing is counted twice. A part a company did not report is not a zero: it is simply not in the sum.
ISSUANCE_PARTS = ("ProceedsFromIssuanceOfCommonStock", "ProceedsFromIssuanceInitialPublicOffering",
                  "ProceedsFromStockOptionsExercised", "ProceedsFromStockPlans",
                  "ProceedsFromIssuanceOfSharesUnderIncentiveAndShareBasedCompensationPlansIncludingStockOptions")
# composite components: (factor, sign); profitability = cash_op_profit, else cfo_to_assets
LONG_QUALITY = (("profitability", 1), ("d_roa", 1), ("net_equity_finance", -1))
AVOID = (("net_equity_finance", 1), ("asset_growth", 1), ("profitability", -1))


def _near(a: date | None, b: date | None, days: int = YEAR_TOLERANCE_DAYS) -> bool:
    return a is not None and b is not None and abs((a - b).days) <= days


def _div(a: float | None, b: float | None) -> float | None:
    return None if a is None or b is None or b <= 0 else a / b


def _instant_at(facts: list[dict[str, Any]], end: date) -> float | None:
    """Balance-sheet value at ``end`` (within the fiscal-year tolerance), the closest one."""
    points = [f for f in facts if f["start"] is None and _near(f["end"], end)]
    return min(points, key=lambda f: abs((f["end"] - end).days))["val"] if points else None


def _current_flow(facts: list[dict[str, Any]], period_end: date) -> float | None:
    """TTM of one flow, only when it belongs to the period that ends at ``period_end``."""
    built = sfr.flow(facts)
    return built["ttm"] if built and sfr._near(built["end"], period_end) else None


def quarters(facts: list[dict[str, Any]]) -> dict[date, float]:
    """Single-quarter values by quarter end: the 3-month facts plus Q4 = fiscal year - nine months."""
    flows = [f for f in facts if f["start"] is not None]
    out = {f["end"]: f["val"] for f in flows if 80 <= (f["end"] - f["start"]).days <= 100}
    for year in (f for f in flows if 340 <= (f["end"] - f["start"]).days <= 380):
        if any(_near(end, year["end"], 6) for end in out):
            continue
        nine = next((f for f in flows if sfr._near(f["start"], year["start"], 4) and 250 <= (f["end"] - f["start"]).days <= 290
                     and f["end"] < year["end"] and f["unit"] == year["unit"]), None)
        if nine is not None:
            out[year["end"]] = year["val"] - nine["val"]
    return out


def compute(raw: dict[str, Any], as_of: date) -> dict[str, Any]:
    """The six factors for one company with only the facts filed by ``as_of``."""
    f = {name: sfr._facts(raw, concepts, as_of) for name, concepts in CONCEPTS.items()}
    values: dict[str, float | None] = {name: None for name in FACTORS}
    partial: dict[str, list[str]] = {}
    cfo = sfr.flow(f["cfo"])
    period_end = cfo["end"] if cfo else None
    if period_end is not None and (as_of - period_end).days > MAX_PERIOD_AGE_DAYS:
        period_end = None  # a cash-flow statement this old is not today's company
    lagged = _instant_at(f["assets"], sfr._years_back(period_end)) if period_end else None
    if period_end is not None and lagged is not None and lagged > 0:
        values["cfo_to_assets"] = _div(cfo["ttm"], lagged)
        operating = _current_flow(f["operating_income"], period_end)
        income = _current_flow(f["net_income"], period_end)
        if operating is not None and income is not None:
            values["cash_op_profit"] = _div(operating - (income - cfo["ttm"]), lagged)
        items = {name: _current_flow(f[name], period_end) for name in ("dividends", "debt_issued", "debt_repaid")}
        items["issuance"] = _current_flow(f["issuance_total"], period_end)
        if items["issuance"] is None:
            parts = [_current_flow(sfr._concept_facts(raw, name, as_of), period_end) for name in ISSUANCE_PARTS]
            items["issuance"] = sum(p for p in parts if p is not None) if any(p is not None for p in parts) else None
        items["repurchase"] = _current_flow(f["repurchase_total"], period_end)
        if items["repurchase"] is None:
            items["repurchase"] = _current_flow(f["repurchase_common"], period_end)
        if items["issuance"] is not None or items["repurchase"] is not None:
            equity = (items["issuance"] or 0.0) - (items["repurchase"] or 0.0)
            missing = [name for name in ("issuance", "repurchase") if items[name] is None]
            values["net_equity_finance"] = _div(equity, lagged)
            if missing:
                partial["net_equity_finance"] = missing
            external = equity - (items["dividends"] or 0.0) + (items["debt_issued"] or 0.0) - (items["debt_repaid"] or 0.0)
            values["net_external_finance"] = _div(external, lagged)
            missing_external = missing + [n for n in ("dividends", "debt_issued", "debt_repaid") if items[n] is None]
            if missing_external:
                partial["net_external_finance"] = missing_external
    assets_now = sfr.instant(f["assets"])
    if assets_now is not None and (as_of - assets_now["end"]).days <= MAX_PERIOD_AGE_DAYS:
        before = _instant_at(f["assets"], sfr._years_back(assets_now["end"]))
        if before is not None and before > 0:
            values["asset_growth"] = assets_now["val"] / before - 1.0
    by_quarter = quarters(f["net_income"])
    if by_quarter:
        last = max(by_quarter)
        prior = next((end for end in by_quarter if _near(end, sfr._years_back(last))), None)
        if prior is not None and (as_of - last).days <= MAX_PERIOD_AGE_DAYS:
            roa_now = _div(by_quarter[last], _instant_at(f["assets"], last))
            roa_before = _div(by_quarter[prior], _instant_at(f["assets"], prior))
            if roa_now is not None and roa_before is not None:
                values["d_roa"] = roa_now - roa_before
    return {"schema": SCHEMA, "as_of": as_of.isoformat(),
            "period_end": period_end.isoformat() if period_end else None,
            "values": {k: (round(v, 6) if v is not None else None) for k, v in values.items()},
            "partial": partial}


def _profitability(values: dict[str, Any]) -> float | None:
    return values.get("cash_op_profit") if values.get("cash_op_profit") is not None else values.get("cfo_to_assets")


def percentiles(items: list[float | None]) -> list[float | None]:
    """Percentile (0-1, ties share their mean rank) of each known value among the known values; None stays None."""
    known = sorted((v, k) for k, v in enumerate(items) if v is not None)
    out: list[float | None] = [None] * len(items)
    if len(known) < 2:
        return out
    k = 0
    while k < len(known):
        j = k
        while j + 1 < len(known) and known[j + 1][0] == known[k][0]:
            j += 1
        rank = (k + j) / 2 / (len(known) - 1)
        for _, index in known[k:j + 1]:
            out[index] = rank
        k = j + 1
    return out


def composites(factor_sets: Iterable[dict[str, Any] | None]) -> list[dict[str, Any] | None]:
    """long_quality and avoid (0-100) for each company of one cross-section; None when too few components known."""
    sets = list(factor_sets)
    values = [(s or {}).get("values") or {} for s in sets]
    columns = {"profitability": [_profitability(v) for v in values]}
    for name in ("d_roa", "net_equity_finance", "asset_growth"):
        columns[name] = [v.get(name) for v in values]
    ranks = {name: percentiles(column) for name, column in columns.items()}
    out: list[dict[str, Any] | None] = []
    for k, s in enumerate(sets):
        if s is None:
            out.append(None)
            continue
        result: dict[str, Any] = {}
        for label, parts in (("long_quality", LONG_QUALITY), ("avoid", AVOID)):
            known = [ranks[name][k] if sign > 0 else 1.0 - ranks[name][k] for name, sign in parts if ranks[name][k] is not None]
            result[label] = round(100.0 * sum(known) / len(known), 1) if len(known) >= MIN_COMPONENTS else None
            result[f"{label}_components"] = len(known)
        out.append(result)
    return out


def quintile_label(value: float | None) -> str | None:
    """Composite (0-100) as the bucket the outcome check judges: top 20%, middle 60%, bottom 20%."""
    if value is None:
        return None
    return "top 20%" if value >= 80 else "bottom 20%" if value < 20 else "middle 60%"


class FactorSource:
    """Factors per ticker from the stored companyfacts, computed once per document and day (cache file)."""

    def __init__(self, directory: Path | None = None, tickers_file: Path | None = None,
                 cache_path: Path | None = None, today: date | None = None) -> None:
        import periodic_filing

        self.directory = Path(directory or periodic_filing.DEFAULT_DIR)
        self.mapping = periodic_filing.ticker_to_cik(Path(tickers_file or periodic_filing.DEFAULT_TICKERS))
        self.cache_path = Path(cache_path) if cache_path else self.directory.parent / "fundamental_factors.json"
        self.today = today or date.today()
        try:
            self.cache = json.loads(self.cache_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self.cache = {}
        self.changed = False

    def factors(self, ticker: str) -> dict[str, Any] | None:
        cik = self.mapping.get(str(ticker).upper())
        if not cik:
            return None
        stem = f"CIK{int(cik):010d}"
        try:
            sha = json.loads((self.directory / f"{stem}.meta.json").read_text(encoding="utf-8"))["sha256"]
        except (OSError, ValueError, KeyError):
            return None
        key = f"{cik}:{self.today.isoformat()}"
        cached = self.cache.get(key)
        if cached and cached.get("sha256") == sha:
            return cached["factors"]
        try:
            body = gzip.decompress((self.directory / f"{stem}.json.gz").read_bytes())
            if hashlib.sha256(body).hexdigest() != sha:
                return None
            raw = json.loads(body)
        except (OSError, EOFError, ValueError):
            return None
        result = compute(raw, self.today)
        self.cache[key], self.changed = {"sha256": sha, "factors": result}, True
        return result

    def save(self) -> None:
        if self.changed:
            today = self.today.isoformat()
            self.cache = {k: v for k, v in self.cache.items() if k.endswith(today)}  # keep only today's entries
            self.cache_path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.cache_path.with_name(self.cache_path.name + ".tmp")
            temporary.write_text(json.dumps(self.cache), encoding="utf-8")
            temporary.replace(self.cache_path)
            self.changed = False


def annotate(rows: list[dict[str, Any]], source: FactorSource | None = None) -> int:
    """Shadow: add ``fundamental_factors`` (values + the run's composites) to each row; returns how many have any."""
    source = source or FactorSource()
    sets = [source.factors(str(row.get("ticker") or "")) for row in rows]
    for row, factors, combo in zip(rows, sets, composites(sets)):
        row["fundamental_factors"] = None if factors is None else {**factors, "composites": combo}
    source.save()
    return sum(1 for s in sets if s is not None)
