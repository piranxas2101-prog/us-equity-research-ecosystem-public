"""Macro environment: the long-term backdrop, computed every night and explained in the daily report. Shadow only.

Two halves, each with its own evidence:

* Economy (FRED, point-in-time, via ``macro_regime``): yield curve, unemployment trend, industrial production,
  credit spread and inflation give a regime (expansion, transition, contraction risk) and sector tail/headwinds.
* Market (the price store): SPY against its 200- and 50-day averages, its 3- and 12-month return, its distance from
  the 52-week high, its realized volatility, and breadth (share of stored companies above their own 200-day
  average, counted only over symbols priced on SPY's last session).

The two give a backdrop: supportive, mixed or hostile, or unknown when the evidence is not enough. Nothing reads it
to change a score, a gate, a limit or an order (shadow, owner's decision 2026-10-13); it is recorded every night so
the outcome review can later show how each strategy and holding period did in each backdrop. Unknown stays unknown:
a missing series or too few priced symbols is reported as such, never filled in.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
from typing import Any

import pandas as pd

import macro_regime
import price_history_store as phs

ROOT = Path(__file__).resolve().parent
MIN_BREADTH_SYMBOLS = 2000   # fewer priced companies (e.g. only the shortlist): breadth is not representative
VOL_BUCKETS = ((15.0, "calm"), (25.0, "normal"), (35.0, "elevated"))  # annualized %, above the last: stressed


MARKET_STATES = ("up_calm", "up_volatile", "down_calm", "down_volatile")
VOLATILE_FROM = 25.0  # annualized 20-day realized volatility, % (the "elevated" bucket and above)


def market_state_history(spy: pd.DataFrame | None) -> pd.Series:
    """The market state of every session, rebuilt from SPY alone so any past date has one (point in time).

    Trend: close above or below its 200-day average. Volatility: 20-day realized volatility below or from 25%
    (annualized). Sessions with fewer than 200 prior sessions have no state (NaN, not a guess).
    """
    if spy is None or "Close" not in spy:
        return pd.Series(dtype=object)
    close = spy["Close"].astype(float).dropna()
    sma200 = close.rolling(200).mean()
    vol = close.pct_change().rolling(20).std() * (252 ** 0.5) * 100
    trend = (close > sma200).map({True: "up", False: "down"})
    calm = (vol < VOLATILE_FROM).map({True: "calm", False: "volatile"})
    states = trend + "_" + calm
    return states.where(sma200.notna() & vol.notna())


MARKET_STATE_GR = {"up_calm": "άνοδος, ήρεμη", "up_volatile": "άνοδος, ταραγμένη", "down_calm": "πτώση, ήρεμη",
                   "down_volatile": "πτώση, ταραγμένη"}


def _sma(close: pd.Series, window: int) -> float | None:
    return float(close.iloc[-window:].mean()) if len(close) >= window else None


def market_state(spy: pd.DataFrame | None) -> dict[str, Any]:
    """Trend, momentum and volatility of SPY on its last stored session."""
    if spy is None or "Close" not in spy:
        return {"status": "unknown", "reason": "no SPY history in the price store"}
    close = spy["Close"].astype(float).dropna()
    if len(close) < 252:
        return {"status": "unknown", "reason": f"only {len(close)} SPY sessions stored"}
    last = float(close.iloc[-1])
    sma200, sma50 = _sma(close, 200), _sma(close, 50)
    vol = float(close.pct_change().iloc[-20:].std() * (252 ** 0.5) * 100)
    bucket = next((name for limit, name in VOL_BUCKETS if vol < limit), "stressed")
    high = float(close.iloc[-252:].max())
    return {"status": "ok", "session": str(close.index[-1].date()), "spy_close": round(last, 2),
            "above_sma200": last > sma200, "close_to_sma200_pct": round((last / sma200 - 1) * 100, 2),
            "sma50_above_sma200": sma50 > sma200, "return_63d_pct": round((last / float(close.iloc[-64]) - 1) * 100, 2),
            "return_252d_pct": round((last / float(close.iloc[-253]) - 1) * 100, 2) if len(close) > 252 else None,
            "drawdown_from_52w_high_pct": round((last / high - 1) * 100, 2),
            "realized_vol_20d_pct": round(vol, 1), "volatility": bucket,
            "trend": "up" if last > sma200 else "down"}


def breadth(directory: Path, session: str | None, symbols: list[str]) -> dict[str, Any]:
    """Share of companies above their own 200- and 50-day averages on ``session``."""
    if session is None:
        return {"status": "unknown", "reason": "no market session"}
    counted = above200 = above50 = 0
    for symbol in symbols:
        history = phs.load(symbol, directory)
        if history is None or "Close" not in history:
            continue
        close = history["Close"].astype(float).dropna()
        if len(close) < 200 or str(close.index[-1].date()) != session:
            continue
        counted += 1
        last = float(close.iloc[-1])
        above200 += last > float(close.iloc[-200:].mean())
        above50 += last > float(close.iloc[-50:].mean())
    if counted < MIN_BREADTH_SYMBOLS:
        return {"status": "unknown", "counted": counted,
                "reason": f"only {counted} companies priced on {session} (need {MIN_BREADTH_SYMBOLS})"}
    pct200 = round(above200 / counted * 100, 1)
    return {"status": "ok", "counted": counted, "above_sma200_pct": pct200,
            "above_sma50_pct": round(above50 / counted * 100, 1),
            "participation": "broad" if pct200 >= 60 else "narrow" if pct200 < 40 else "mixed"}


def backdrop(economy: dict[str, Any], market: dict[str, Any], width: dict[str, Any]) -> dict[str, Any]:
    """Supportive, mixed, hostile or unknown, with the reasons."""
    regime = (economy or {}).get("regime")
    known_economy = regime in {"expansion", "transition", "contraction_risk"}
    known_market = market.get("status") == "ok"
    if not known_economy and not known_market:
        return {"label": "unknown", "reasons": ["Neither the economy nor the market state could be read."]}
    reasons, points = [], 0
    if known_economy:
        points += {"expansion": 1, "transition": 0, "contraction_risk": -1}[regime]
        reasons.append(f"economy: {regime}")
    if known_market:
        points += 1 if market["trend"] == "up" else -1
        reasons.append(f"S&P 500 {'above' if market['trend'] == 'up' else 'below'} its 200-day average")
        if market["volatility"] in {"elevated", "stressed"}:
            points -= 1
            reasons.append(f"volatility {market['volatility']} ({market['realized_vol_20d_pct']}%)")
    if width.get("status") == "ok":
        points += {"broad": 1, "mixed": 0, "narrow": -1}[width["participation"]]
        reasons.append(f"participation {width['participation']} ({width['above_sma200_pct']}% above the 200-day)")
    label = "supportive" if points >= 2 else "hostile" if points <= -2 else "mixed"
    complete = known_economy and known_market and width.get("status") == "ok"
    return {"label": label, "points": points, "complete": complete, "reasons": reasons}


HOLDING_WEIGHT = {"day": "none", "week": "minor", "month": "moderate", "quarter": "major"}


def holding_class_view(back: dict[str, Any]) -> dict[str, Any]:
    """What the backdrop would mean for each holding class if it became a criterion (shadow: nothing applies it).

    A day or a week is decided by price action; the backdrop leans month and quarter positions: longs in a
    supportive backdrop, shorts in a hostile one, neither in a mixed or unknown one."""
    label = back.get("label")
    lean = {"supportive": "long", "hostile": "short"}.get(label)
    return {name: {"weight": weight, "leans": lean if weight in {"moderate", "major"} else None}
            for name, weight in HOLDING_WEIGHT.items()}


def build(macro_context: dict[str, Any], directory: Path, symbols: list[str]) -> dict[str, Any]:
    economy = (macro_context or {}).get("regime_assessment") or (
        macro_regime.classify(macro_context) if (macro_context or {}).get("series") else None)
    series = {sid: {"date": v.get("date"), "value": v.get("value"), "fresh": v.get("fresh")}
              for sid, v in ((macro_context or {}).get("series") or {}).items() if isinstance(v, dict) and "date" in v}
    spy = phs.load("SPY", directory)
    market = market_state(spy)
    states = market_state_history(spy).dropna()
    if market.get("status") == "ok" and len(states):
        market["state"] = str(states.iloc[-1])
    width = breadth(directory, market.get("session"), symbols)
    back = backdrop(economy or {}, market, width)
    return {"schema": "ecosystem.macro_environment.v1", "generated_at": datetime.now(timezone.utc).isoformat(),
            "use": "shadow_only", "economy": economy or {"regime": "unknown", "reason": (macro_context or {}).get(
                "reason") or "no FRED series"}, "fred_series": series, "market": market, "breadth": width,
            "backdrop": back, "holding_class_view": holding_class_view(back)}


ECON_GR = {"expansion": "επέκταση", "transition": "μετάβαση", "contraction_risk": "κίνδυνος συρρίκνωσης",
           "insufficient_data": "ανεπαρκή στοιχεία", "unknown": "άγνωστο"}
BACKDROP_GR = {"supportive": "ευνοϊκό", "mixed": "μικτό", "hostile": "δυσμενές", "unknown": "άγνωστο"}
VOL_GR = {"calm": "ήρεμη", "normal": "κανονική", "elevated": "αυξημένη", "stressed": "πίεση"}
SIGNAL_GR = {
    "yield_curve": ("Καμπύλη επιτοκίων 10–2 ετών", {"positive": "θετική", "inverted": "ανεστραμμένη"},
                    "ανεστραμμένη καμπύλη έχει προηγηθεί των περισσότερων υφέσεων"),
    "unemployment_trend": ("Ανεργία", {"stable_or_improving": "σταθερή/βελτιώνεται", "deteriorating": "χειροτερεύει"},
                           "άνοδος 0,3 μονάδων μέσα στο έτος είναι πρώιμο σήμα επιβράδυνσης"),
    "industrial_production": ("Βιομηχανική παραγωγή", {"expanding": "ανεβαίνει", "contracting": "πέφτει"},
                              "δείχνει αν η πραγματική οικονομία μεγαλώνει"),
    "credit_stress": ("Πιστωτικό spread (BAA − 10ετές)", {"normal": "κανονικό", "high": "υψηλό"},
                      "όταν οι δανειστές ζητούν πολύ μεγαλύτερο επιτόκιο από εταιρείες, ο κίνδυνος ανεβαίνει"),
    "inflation_direction": ("Πληθωρισμός", {"rising": "ανεβαίνει", "stable_or_falling": "σταθερός/πέφτει"},
                            "ανερχόμενος πληθωρισμός κρατά τα επιτόκια ψηλά και πιέζει τις αποτιμήσεις"),
}


def report_lines(env: dict[str, Any], lesson: dict[str, Any] | None = None) -> list[str]:
    """The daily report section: what the backdrop is and why, in plain words (plus Sokratis's backdrop lesson)."""
    if not env:
        return []
    economy, market, width, back = env.get("economy") or {}, env.get("market") or {}, env.get("breadth") or {}, \
        env.get("backdrop") or {}
    lines = ["## Περιβάλλον οικονομίας και αγοράς (shadow)", "",
             f"- **Συνολικά: {BACKDROP_GR.get(back.get('label'), back.get('label'))}**"
             + ("" if back.get("complete", True) else " (με ελλιπή στοιχεία)") + ". "
             "Καταγράφεται κάθε βράδυ· δεν αλλάζει ακόμη καμία απόφαση.",
             f"- Οικονομία (FRED): **{ECON_GR.get(economy.get('regime'), economy.get('regime'))}**"
             + (f", βεβαιότητα {economy.get('confidence')}" if economy.get("confidence") else "") + "."]
    for key, value in (economy.get("signals") or {}).items():
        name, values, why = SIGNAL_GR.get(key, (key, {}, ""))
        lines.append(f"  - {name}: {values.get(value, value)} — {why}.")
    impacts = economy.get("sector_impacts") or {}
    tail = [s for s, v in impacts.items() if v.get("stance") == "tailwind"]
    head = [s for s, v in impacts.items() if v.get("stance") == "headwind"]
    if tail or head:
        lines.append(f"  - Ούριος άνεμος: {', '.join(tail) or 'κανένας τομέας'} · αντίθετος: {', '.join(head) or 'κανένας τομέας'}.")
    if market.get("status") == "ok":
        lines.append(f"- Αγορά (S&P 500, {market['session']}): {'πάνω' if market['above_sma200'] else 'κάτω'} από τον μέσο "
                     f"200 ημερών ({market['close_to_sma200_pct']:+.1f}%), 3μηνο {market['return_63d_pct']:+.1f}%, "
                     f"12μηνο {market['return_252d_pct']:+.1f}%, απόσταση από το υψηλό 52 εβδομάδων "
                     f"{market['drawdown_from_52w_high_pct']:+.1f}%, μεταβλητότητα {VOL_GR[market['volatility']]} "
                     f"({market['realized_vol_20d_pct']}% ετησιοποιημένη)."
                     + (f" Κατάσταση αγοράς: **{MARKET_STATE_GR[market['state']]}**." if market.get("state") else ""))
    else:
        lines.append(f"- Αγορά: άγνωστη ({market.get('reason')}).")
    if width.get("status") == "ok":
        lines.append(f"- Συμμετοχή: {width['above_sma200_pct']}% των {width['counted']} εταιρειών πάνω από τον μέσο 200 ημερών "
                     f"({width['above_sma50_pct']}% πάνω από τον μέσο 50). Όταν ο δείκτης ανεβαίνει με λίγες μετοχές, "
                     "η άνοδος είναι πιο εύθραυστη.")
    else:
        lines.append(f"- Συμμετοχή: άγνωστη ({width.get('reason')}).")
    lines.append("- Για ποιες θέσεις μετράει: ελάχιστα για ημέρα, λίγο για εβδομάδα, αρκετά για μήνα, πολύ για τρίμηνο.")
    view = env.get("holding_class_view") or {}
    leans = {name: v.get("leans") for name, v in view.items() if v.get("leans")}
    if view:
        lean_gr = {"long": "υπέρ των long", "short": "υπέρ των short"}
        lines.append("- Αν γινόταν κριτήριο (δεν εφαρμόζεται ακόμη): " + (
            "; ".join(f"{ {'month': 'μήνας', 'quarter': 'τρίμηνο'}.get(k, k)} {lean_gr[v]}" for k, v in leans.items())
            or "ουδέτερο για όλες τις διάρκειες") + ".")
    for trader, view in ((lesson or {}).get("traders") or {}).items():
        good = [s["strategy_side"] for s in view.get("strategies") or [] if s.get("verdict") == "works_in_state"]
        weak = [s["strategy_side"] for s in view.get("strategies") or [] if s.get("verdict") == "weak_in_state"]
        lines.append(f"- Sokratis προς {trader}: σε αυτή την κατάσταση ιστορικά δούλεψαν {', '.join(good) or 'καμία'}· "
                     f"αδύναμες {', '.join(weak) or 'καμία'}.")
    lines.append("")
    return lines


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--config", type=Path, help="default: <root>/scanner_config.json")
    parser.add_argument("--routes", type=Path, help="default: <root>/output/all_listed_universe_routes_latest.json")
    parser.add_argument("--update-store", action="store_true",
                        help="first bring every company's stored prices up to date (new sessions only), for breadth")
    args = parser.parse_args(argv)
    root = args.root
    from research_workflow_orchestrator import load_env
    import us_equity_monitor

    load_env(root / ".env")  # FRED_API_KEY, never printed
    config = json.loads((args.config or root / "scanner_config.json").read_text(encoding="utf-8-sig"))
    try:
        macro_context = us_equity_monitor.fetch_fred_macro_context(config.get("data_sources") or {})
    except Exception as exc:  # noqa: BLE001 - no FRED is an unknown economy, not a failed step
        macro_context = {"status": "failed", "reason": f"{type(exc).__name__}"}
    routes = args.routes or root / "output" / "all_listed_universe_routes_latest.json"
    symbols = [s for s in phs.company_symbols(routes) if s not in phs.BENCHMARKS] if routes.is_file() else []
    if args.update_store and symbols:
        phs.update(symbols + list(phs.BENCHMARKS), root / "data" / "price_history", progress=lambda _m: None)
    env = build(macro_context, root / "data" / "price_history", symbols)
    out = root / "output"
    (out / "macro_environment_latest.json").write_text(json.dumps(env, ensure_ascii=False, indent=2), encoding="utf-8")
    history = out / "macro_environment"
    history.mkdir(parents=True, exist_ok=True)
    (history / f"macro_environment_{datetime.now():%Y%m%d}.json").write_text(
        json.dumps(env, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"backdrop": env["backdrop"]["label"], "economy": env["economy"].get("regime"),
                      "market": env["market"].get("trend"), "breadth": env["breadth"].get("status"),
                      "fred_key": bool(os.getenv("FRED_API_KEY"))}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
