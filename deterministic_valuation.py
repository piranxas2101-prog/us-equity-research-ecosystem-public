"""Auditable valuation calculations; never invents missing inputs."""
from __future__ import annotations

import math
import random
import statistics
from typing import Any


def number(value: Any) -> float | None:
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (TypeError, ValueError):
        return None


def _pick(row: dict[str, Any], *names: str) -> float | None:
    for name in names:
        value = number(row.get(name))
        if value is not None:
            return value
    return None


def dcf_value(fcf: float, growth: float, discount: float, terminal_growth: float,
              years: int = 5) -> float:
    if fcf <= 0 or discount <= terminal_growth or years <= 0:
        raise ValueError("Invalid DCF assumptions")
    total = 0.0
    projected = fcf
    for year in range(1, years + 1):
        projected *= 1.0 + growth
        total += projected / ((1.0 + discount) ** year)
    terminal = projected * (1.0 + terminal_growth) / (discount - terminal_growth)
    return total + terminal / ((1.0 + discount) ** years)


def calculate_wacc(row: dict[str, Any]) -> dict[str, Any]:
    risk_free = _pick(row, "risk_free_rate", "dgs10_rate")
    beta = _pick(row, "beta")
    market_cap = _pick(row, "market_cap")
    debt = _pick(row, "debt", "total_debt")
    interest = _pick(row, "interest_expense_ttm", "interest_expense")
    tax_rate = _pick(row, "effective_tax_rate")
    missing = [name for name, value in (("risk_free_rate", risk_free), ("beta", beta),
                                        ("market_cap", market_cap), ("debt", debt)) if value is None]
    if market_cap is not None and market_cap <= 0:
        missing.append("positive_market_cap")
    if missing:
        return {"status": "not_assessed", "missing": missing}
    # ERP is an explicit versioned policy assumption, never presented as observed data.
    erp = 0.05
    rf = float(risk_free) / 100.0 if float(risk_free) > 1 else float(risk_free)
    cost_equity = rf + float(beta) * erp
    cost_debt = abs(float(interest)) / float(debt) if debt and interest is not None else rf + 0.02
    tax = min(max(float(tax_rate) if tax_rate is not None else 0.21, 0.0), 0.5)
    capital = float(market_cap) + max(float(debt), 0.0)
    wacc = (float(market_cap) / capital) * cost_equity + (max(float(debt), 0.0) / capital) * cost_debt * (1 - tax)
    return {"status": "assessed", "wacc": round(wacc, 6), "cost_of_equity": round(cost_equity, 6),
            "pre_tax_cost_of_debt": round(cost_debt, 6), "tax_rate": tax,
            "policy_assumptions": {"equity_risk_premium": erp,
                                   "fallback_debt_spread": 0.02, "fallback_tax_rate": 0.21}}


def reverse_dcf_growth(*, price: float, shares: float, fcf: float, cash: float,
                       debt: float, discount: float, terminal_growth: float) -> dict[str, Any]:
    target = price * shares - cash + debt
    low, high = -0.5, min(0.5, discount - 0.001)
    low_value = dcf_value(fcf, low, discount, terminal_growth)
    high_value = dcf_value(fcf, high, discount, terminal_growth)
    if not low_value <= target <= high_value:
        return {"status": "outside_solvable_range", "growth_bounds": [low, high]}
    for _ in range(80):
        mid = (low + high) / 2
        if dcf_value(fcf, mid, discount, terminal_growth) < target:
            low = mid
        else:
            high = mid
    return {"status": "assessed", "implied_fcf_growth": round((low + high) / 2, 6),
            "discount_rate": discount, "terminal_growth": terminal_growth}


def peer_multiples(row: dict[str, Any], peers: list[dict[str, Any]] | None) -> dict[str, Any]:
    peers = peers or []
    revenue = _pick(row, "revenue_ttm")
    earnings = _pick(row, "net_income_ttm")
    shares = _pick(row, "shares_outstanding", "sharesOutstanding")
    cash, debt = _pick(row, "cash", "total_cash") or 0.0, _pick(row, "debt", "total_debt") or 0.0
    ev_sales = [value for peer in peers if (value := _pick(peer, "enterprise_to_sales")) is not None and value > 0]
    pe = [value for peer in peers if (value := _pick(peer, "trailing_pe")) is not None and value > 0]
    if not shares or len(pe) + len(ev_sales) < 3:
        return {"status": "not_assessed", "reason": "insufficient_peer_observations",
                "peer_count": len(pe) + len(ev_sales)}
    values = {}
    if revenue and ev_sales:
        values["ev_sales_fair_value"] = (revenue * statistics.median(ev_sales) + cash - debt) / shares
    if earnings and earnings > 0 and pe:
        values["pe_fair_value"] = earnings * statistics.median(pe) / shares
    if not values:
        return {"status": "not_assessed", "reason": "company_metric_unavailable"}
    return {"status": "assessed", "method": "median_peer_multiples", "values": values,
            "peer_observation_count": len(pe) + len(ev_sales)}


def monte_carlo(*, fcf: float, shares: float, cash: float, debt: float,
                simulations: int = 1000, seed: int = 42) -> dict[str, Any]:
    rng = random.Random(seed)
    values = []
    for _ in range(simulations):
        growth = min(max(rng.gauss(0.05, 0.05), -0.20), 0.25)
        discount = min(max(rng.gauss(0.11, 0.02), 0.065), 0.20)
        terminal = min(max(rng.gauss(0.025, 0.0075), 0.0), discount - 0.01)
        values.append((dcf_value(fcf, growth, discount, terminal) + cash - debt) / shares)
    values.sort()
    def percentile(p: float) -> float:
        return values[min(len(values) - 1, int((len(values) - 1) * p))]
    return {"status": "assessed", "simulations": simulations, "seed": seed,
            "p10": round(percentile(.1), 4), "median": round(percentile(.5), 4),
            "p90": round(percentile(.9), 4)}


def assess(row: dict[str, Any], peers: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    price = _pick(row, "share_price", "current_price", "close")
    shares = _pick(row, "shares_outstanding", "sharesOutstanding")
    fcf = _pick(row, "free_cash_flow_ttm", "free_cashflow", "free_cash_flow", "cash_burn_proxy_fcf")
    cash = _pick(row, "cash", "total_cash") or 0.0
    debt = _pick(row, "debt", "total_debt") or 0.0
    missing = [name for name, value in (("price", price), ("shares_outstanding", shares),
                                        ("positive_free_cash_flow", fcf)) if value is None or value <= 0]
    if missing:
        return {"schema": "ecosystem.deterministic_valuation.v1", "status": "not_assessed",
                "reason": "missing_required_inputs", "missing": missing,
                "method": "five_year_fcf_dcf", "execution_authority": False}

    scenarios = {
        "bear": {"growth": -0.02, "discount": 0.14, "terminal_growth": 0.015},
        "base": {"growth": 0.05, "discount": 0.11, "terminal_growth": 0.025},
        "bull": {"growth": 0.10, "discount": 0.09, "terminal_growth": 0.03},
    }
    values: dict[str, Any] = {}
    for name, assumptions in scenarios.items():
        enterprise = dcf_value(float(fcf), **assumptions)
        equity = enterprise + float(cash) - float(debt)
        fair = equity / float(shares)
        values[name] = {**assumptions, "enterprise_value": round(enterprise, 2),
                        "equity_value": round(equity, 2), "fair_value_per_share": round(fair, 4),
                        "upside_pct": round((fair / float(price) - 1.0) * 100.0, 2)}
    ordered = [values[key]["fair_value_per_share"] for key in ("bear", "base", "bull")]
    wacc = calculate_wacc(row)
    reverse_discount = float(wacc["wacc"]) if wacc.get("status") == "assessed" else 0.11
    return {
        "schema": "ecosystem.deterministic_valuation.v1", "status": "assessed",
        "method": "five_year_fcf_dcf", "current_price": price,
        "input_snapshot": {"free_cash_flow_ttm": fcf, "shares_outstanding": shares,
                           "cash": cash, "debt": debt},
        "scenarios": values, "valuation_range": {"low": min(ordered), "base": ordered[1], "high": max(ordered)},
        "wacc": wacc,
        "reverse_dcf": reverse_dcf_growth(price=float(price), shares=float(shares), fcf=float(fcf),
                                           cash=float(cash), debt=float(debt), discount=reverse_discount,
                                           terminal_growth=min(0.025, reverse_discount - 0.01)),
        "peer_multiples": peer_multiples(row, peers),
        "monte_carlo": monte_carlo(fcf=float(fcf), shares=float(shares), cash=float(cash), debt=float(debt)),
        "limitations": ["Scenario assumptions are policy defaults, not forecasts.",
                        "Negative or missing FCF fails closed instead of forcing a value."],
        "execution_authority": False,
    }
