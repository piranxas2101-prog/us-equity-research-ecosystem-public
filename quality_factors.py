"""Quality factors from the accounting literature, computed from SEC companyfacts, in shadow for Giannis.

Owner's decision 2026-10-12 (a knowledge library for the agents). Three well-known scores, all from the company's
two latest fiscal years (10-K / 20-F), each with every input and the rule that built it:

- Piotroski F-score (Piotroski 2000): nine pass/fail signals of profitability, leverage/liquidity and efficiency.
  0-9; reported with how many of the nine signals could be computed.
- Altman Z''-score (Altman 1995, non-manufacturer form, book equity, no market price needed):
  6.56·WC/TA + 3.26·RE/TA + 6.72·EBIT/TA + 1.05·BE/TL. Zones: safe > 2.6, grey 1.1-2.6, distress < 1.1.
  Not applicable to banks and insurers (their balance sheets break the ratios).
- Beneish M-score (Beneish 1999, eight variables): -4.84 + 0.920·DSRI + 0.528·GMI + 0.404·AQI + 0.892·SGI +
  0.115·DEPI - 0.172·SGAI + 4.679·TATA - 0.327·LVGI. Above -1.78 = earnings-manipulation warning.

Unknown stays unknown: a score whose inputs are missing is None with the missing inputs listed; nothing is assumed.
The scores change no decision: they are reported next to Giannis's score and judged against outcomes first.
"""
from __future__ import annotations

from datetime import date
import gzip
import hashlib
import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
FORMS = frozenset({"10-K", "10-K/A", "20-F", "20-F/A", "40-F", "40-F/A"})
MAX_AGE_DAYS = 548  # the latest fiscal year must have ended within ~18 months
CONCEPTS = {
    "revenue": ("Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax",
                "RevenueFromContractWithCustomerIncludingAssessedTax", "SalesRevenueNet", "Revenue"),
    "net_income": ("NetIncomeLoss", "ProfitLoss", "NetIncomeLossAvailableToCommonStockholdersBasic", "ProfitLossAttributableToOwnersOfParent"),
    "cfo": ("NetCashProvidedByUsedInOperatingActivities", "CashFlowsFromUsedInOperatingActivities"),
    "gross_profit": ("GrossProfit",),
    "cost_of_revenue": ("CostOfRevenue", "CostOfGoodsAndServicesSold", "CostOfSales"),
    "operating_income": ("OperatingIncomeLoss", "ProfitLossFromOperatingActivities"),
    "sga": ("SellingGeneralAndAdministrativeExpense",),
    "depreciation": ("DepreciationDepletionAndAmortization", "DepreciationAndAmortization", "Depreciation"),
    "assets": ("Assets",),
    "current_assets": ("AssetsCurrent", "CurrentAssets"),
    "current_liabilities": ("LiabilitiesCurrent", "CurrentLiabilities"),
    "liabilities": ("Liabilities",),
    "long_term_debt": ("LongTermDebtNoncurrent", "LongTermDebt", "NoncurrentFinancialLiabilities"),
    "receivables": ("AccountsReceivableNetCurrent", "TradeAndOtherCurrentReceivables"),
    "ppe": ("PropertyPlantAndEquipmentNet", "PropertyPlantAndEquipment"),
    "retained_earnings": ("RetainedEarningsAccumulatedDeficit", "RetainedEarnings"),
    "equity": ("StockholdersEquity", "Equity",
               "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest"),
    "shares": ("WeightedAverageNumberOfSharesOutstandingBasic",),
}
FLOWS = ("revenue", "net_income", "cfo", "gross_profit", "cost_of_revenue", "operating_income", "sga",
         "depreciation", "shares")


def _day(value: Any) -> date | None:
    try:
        return date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return None


def _rows(raw: dict[str, Any], concept: str) -> list[dict[str, Any]]:
    out = []
    for taxonomy in ("us-gaap", "ifrs-full"):
        units = (((raw.get("facts") or {}).get(taxonomy) or {}).get(concept) or {}).get("units") or {}
        for unit, rows in units.items():
            if unit not in ("USD", "shares") and len(unit) != 3:
                continue
            for row in rows or []:
                if row.get("form") in FORMS and isinstance(row.get("val"), (int, float)) and _day(row.get("end")):
                    out.append({**row, "unit": unit})
    return out


def annual_values(raw: dict[str, Any], key: str) -> dict[date, float]:
    """Fiscal-year value per year end for one item, from the concept with the most recent year (latest filing wins)."""
    best: dict[date, float] = {}
    for concept in CONCEPTS[key]:
        found: dict[date, tuple[str, float]] = {}
        for row in _rows(raw, concept):
            end = _day(row["end"])
            if key in FLOWS:
                start = _day(row.get("start"))
                if start is None or not 330 <= (end - start).days <= 400:
                    continue
            elif row.get("start"):
                continue
            filed = str(row.get("filed") or "")
            if end not in found or filed > found[end][0]:
                found[end] = (filed, float(row["val"]))
        values = {end: val for end, (_, val) in found.items()}
        if values and (not best or max(values) > max(best)):
            best = values
    return best


def _financial(sector: str | None) -> bool:
    """Banks, insurers and other financials: the Altman ratios do not apply to their balance sheets."""
    return "financ" in str(sector or "").lower()


def _pair(series: dict[date, float], year_end: date) -> tuple[float | None, float | None]:
    """Value at the fiscal year end and at the year before (within a week of one year earlier)."""
    current = next((v for d, v in series.items() if abs((d - year_end).days) <= 7), None)
    prior = next((v for d, v in series.items() if abs((year_end - d).days - 365) <= 10), None)
    return current, prior


def _div(a: float | None, b: float | None) -> float | None:
    return None if a is None or b in (None, 0) else a / b


def compute(raw: dict[str, Any], *, today: date | None = None, sector: str | None = None) -> dict[str, Any]:
    today = today or date.today()
    series = {key: annual_values(raw, key) for key in CONCEPTS}
    anchors = series["assets"] or series["revenue"]
    if not anchors:
        return {"fiscal_year_end": None, "piotroski": None, "altman_z2": None, "beneish": None,
                "reason": "no annual balance sheet in companyfacts"}
    year_end = max(anchors)
    if (today - year_end).days > MAX_AGE_DAYS:
        return {"fiscal_year_end": year_end.isoformat(), "piotroski": None, "altman_z2": None, "beneish": None,
                "reason": "latest fiscal year is older than 18 months"}
    v = {key: _pair(s, year_end) for key, s in series.items()}
    cur = {key: pair[0] for key, pair in v.items()}
    pri = {key: pair[1] for key, pair in v.items()}
    for book in (cur, pri):  # gross profit from revenue and cost of revenue when not reported directly
        if book["gross_profit"] is None and book["revenue"] is not None and book["cost_of_revenue"] is not None:
            book["gross_profit"] = book["revenue"] - book["cost_of_revenue"]
    return {"fiscal_year_end": year_end.isoformat(),
            "piotroski": _piotroski(cur, pri),
            "altman_z2": None if _financial(sector) else _altman(cur),
            "altman_applicable": not _financial(sector),
            "beneish": _beneish(cur, pri)}


def _piotroski(cur: dict[str, float | None], pri: dict[str, float | None]) -> dict[str, Any]:
    roa, roa_prior = _div(cur["net_income"], pri["assets"]), None
    signals = {
        "roa_positive": None if roa is None else roa > 0,
        "cfo_positive": None if cur["cfo"] is None else cur["cfo"] > 0,
        "roa_improved": None,
        "cash_over_income": (None if cur["cfo"] is None or cur["net_income"] is None
                             else cur["cfo"] > cur["net_income"]),
        "leverage_fell": None,
        "current_ratio_rose": None,
        "no_new_shares": None if cur["shares"] is None or pri["shares"] is None else cur["shares"] <= pri["shares"] * 1.005,
        "gross_margin_rose": None,
        "asset_turnover_rose": None,
    }
    # prior-year ratios need the balance sheet two years back; use the prior year-end assets as their base
    roa_prior = _div(pri["net_income"], pri["assets"])
    if roa is not None and roa_prior is not None:
        signals["roa_improved"] = roa > roa_prior
    lev, lev_prior = _div(cur["long_term_debt"], cur["assets"]), _div(pri["long_term_debt"], pri["assets"])
    if lev is not None and lev_prior is not None:
        signals["leverage_fell"] = lev < lev_prior
    cr, cr_prior = _div(cur["current_assets"], cur["current_liabilities"]), _div(pri["current_assets"], pri["current_liabilities"])
    if cr is not None and cr_prior is not None:
        signals["current_ratio_rose"] = cr > cr_prior
    gm, gm_prior = _div(cur["gross_profit"], cur["revenue"]), _div(pri["gross_profit"], pri["revenue"])
    if gm is not None and gm_prior is not None:
        signals["gross_margin_rose"] = gm > gm_prior
    at, at_prior = _div(cur["revenue"], pri["assets"]), _div(pri["revenue"], pri["assets"])
    if at is not None and at_prior is not None:
        signals["asset_turnover_rose"] = at > at_prior
    known = [s for s in signals.values() if s is not None]
    return {"score": sum(1 for s in known if s) if len(known) == 9 else None,
            "passed": sum(1 for s in known if s), "known": len(known), "signals": signals}


def _altman(cur: dict[str, float | None]) -> dict[str, Any] | None:
    ta, tl = cur["assets"], cur["liabilities"]
    if tl is None and ta is not None and cur["equity"] is not None:
        tl = ta - cur["equity"]
    parts = {"wc_ta": _div(None if cur["current_assets"] is None or cur["current_liabilities"] is None
                           else cur["current_assets"] - cur["current_liabilities"], ta),
             "re_ta": _div(cur["retained_earnings"], ta), "ebit_ta": _div(cur["operating_income"], ta),
             "be_tl": _div(cur["equity"], tl)}
    missing = [k for k, val in parts.items() if val is None]
    if missing:
        return {"score": None, "zone": None, "missing": missing}
    score = 6.56 * parts["wc_ta"] + 3.26 * parts["re_ta"] + 6.72 * parts["ebit_ta"] + 1.05 * parts["be_tl"]
    zone = "safe" if score > 2.6 else "grey" if score >= 1.1 else "distress"
    return {"score": round(score, 3), "zone": zone, "parts": {k: round(val, 4) for k, val in parts.items()}}


def _beneish(cur: dict[str, float | None], pri: dict[str, float | None]) -> dict[str, Any]:
    def ratio(a, b):
        return _div(a, b)

    dsri = _div(ratio(cur["receivables"], cur["revenue"]), ratio(pri["receivables"], pri["revenue"]))
    gm_cur, gm_pri = ratio(cur["gross_profit"], cur["revenue"]), ratio(pri["gross_profit"], pri["revenue"])
    gmi = _div(gm_pri, gm_cur)

    def aq(book):
        if None in (book["current_assets"], book["ppe"], book["assets"]) or not book["assets"]:
            return None
        return 1 - (book["current_assets"] + book["ppe"]) / book["assets"]

    aqi = _div(aq(cur), aq(pri))
    sgi = _div(cur["revenue"], pri["revenue"])

    def dep_rate(book):
        if None in (book["depreciation"], book["ppe"]) or (book["depreciation"] + book["ppe"]) == 0:
            return None
        return book["depreciation"] / (book["depreciation"] + book["ppe"])

    depi = _div(dep_rate(pri), dep_rate(cur))
    sgai = _div(ratio(cur["sga"], cur["revenue"]), ratio(pri["sga"], pri["revenue"]))

    def lev(book):
        if None in (book["current_liabilities"], book["long_term_debt"], book["assets"]):
            return None
        return _div(book["current_liabilities"] + book["long_term_debt"], book["assets"])

    lvgi = _div(lev(cur), lev(pri))
    tata = (None if None in (cur["net_income"], cur["cfo"], cur["assets"])
            else _div(cur["net_income"] - cur["cfo"], cur["assets"]))
    parts = {"dsri": dsri, "gmi": gmi, "aqi": aqi, "sgi": sgi, "depi": depi, "sgai": sgai, "lvgi": lvgi, "tata": tata}
    missing = [k for k, val in parts.items() if val is None]
    if missing:
        return {"score": None, "flag": None, "missing": missing}
    score = (-4.84 + 0.920 * dsri + 0.528 * gmi + 0.404 * aqi + 0.892 * sgi + 0.115 * depi - 0.172 * sgai
             + 4.679 * tata - 0.327 * lvgi)
    return {"score": round(score, 3), "flag": score > -1.78, "parts": {k: round(val, 4) for k, val in parts.items()}}


class QualityFactorSource:
    """Factors per ticker from the stored companyfacts, computed once per document (cache/quality_factors.json)."""

    def __init__(self, directory: Path | None = None, tickers_file: Path | None = None,
                 cache_path: Path | None = None, today: date | None = None) -> None:
        import periodic_filing

        self.directory = Path(directory or periodic_filing.DEFAULT_DIR)
        self.mapping = periodic_filing.ticker_to_cik(Path(tickers_file or periodic_filing.DEFAULT_TICKERS))
        self.cache_path = Path(cache_path) if cache_path else self.directory.parent / "quality_factors.json"
        self.today = today or date.today()
        try:
            self.cache = json.loads(self.cache_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self.cache = {}
        self.changed = False

    def factors(self, ticker: str, sector: str | None = None) -> dict[str, Any] | None:
        cik = self.mapping.get(str(ticker).upper())
        if not cik:
            return None
        stem = f"CIK{int(cik):010d}"
        try:
            sha = json.loads((self.directory / f"{stem}.meta.json").read_text(encoding="utf-8"))["sha256"]
        except (OSError, ValueError, KeyError):
            return None
        key = f"{cik}:{sector or ''}:{self.today.isoformat()[:7]}"
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
        result = compute(raw, today=self.today, sector=sector)
        self.cache[key], self.changed = {"sha256": sha, "factors": result}, True
        return result

    def save(self) -> None:
        if self.changed:
            self.cache_path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.cache_path.with_name(self.cache_path.name + ".tmp")
            temporary.write_text(json.dumps(self.cache), encoding="utf-8")
            temporary.replace(self.cache_path)
            self.changed = False
