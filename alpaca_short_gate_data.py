"""The data that resolves the global SHORT contract before the session (owner's choice B, 2026-10-24).

Every short must have its implementation checks resolved (playbook_framework.unresolved_short_gates): whether the
stock can be shorted, what borrowing it costs, and whether a squeeze is a real risk. Until now none of them was
ever known, so every catalog short was blocked and the lab's daily strategies traded shorts only because they
were missing from the catalog. Read-only, once at the session's start (no order is sent):

* ``shortable`` and ``easy_to_borrow`` of every active US equity from the Alpaca paper account (GET /v2/assets);
* ``short_percent_of_float`` and the float: Giannis's latest results (OpenBB, top names only), then the stored
  yfinance company info (cache/stage1_info), then - for the day's short candidates still missing - yfinance asked
  once at the session's start (``LIVE_LOOKUPS_MAX`` at most). The source of each value is kept.

Koulis resolves each short's gates from this file (``resolve``) and Thymios checks the resolution. Unknown stays
unknown: a missing value leaves its gate unresolved and the short is blocked.

Starting values (owner's approval with choice B; to be measured on the ledger, docs/RULES_REVIEW.md):
* shortability: the broker says shortable;
* borrow carry: easy to borrow (Alpaca charges no borrow fee on easy-to-borrow stocks; hard to borrow stays
  unresolved);
* squeeze risk: short interest below ``SQUEEZE_SHORT_FLOAT_BELOW`` of the float **and** a float (shares
  outstanding when the float is unknown) of at least ``LOW_FLOAT_SHARES`` (a crowded short or a small float is
  where squeezes happen).
"""
from __future__ import annotations

import argparse
from datetime import datetime
import json
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "output" / "short_gate_data_latest.json"
SQUEEZE_SHORT_FLOAT_BELOW = 0.20
LOW_FLOAT_SHARES = 20_000_000
LIVE_LOOKUPS_MAX = 300
INFO_CACHE = ROOT / "cache" / "stage1_info"
NEW_YORK = ZoneInfo("America/New_York")


def _f(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number else None


def cached_info(symbol: str, directory: Path = INFO_CACHE) -> dict[str, Any]:
    try:
        return json.loads((directory / f"{symbol.replace('/', '_')}.json").read_text(encoding="utf-8")).get("info") or {}
    except (OSError, ValueError):
        return {}


def build(assets: list[dict[str, Any]], giannis_rows: list[dict[str, Any]], today: str,
          info: Any = cached_info) -> dict[str, Any]:
    """``info(symbol)``: yfinance company info for a symbol ({} when unknown)."""
    fundamentals = {str(r.get("ticker") or "").upper(): r for r in giannis_rows if isinstance(r, dict)}
    symbols: dict[str, dict[str, Any]] = {}
    for asset in assets:
        symbol = str(asset.get("symbol") or "").upper()
        if not symbol:
            continue
        row = fundamentals.get(symbol, {})
        short_float, source = _f(row.get("short_percent_of_float")), "giannis"
        float_shares = None
        details = info(symbol) if short_float is None or row.get("shares_outstanding") is None else {}
        if short_float is None:
            short_float, source = _f(details.get("shortPercentOfFloat")), "yfinance"
        float_shares = _f(details.get("floatShares"))
        symbols[symbol] = {"shortable": asset.get("shortable"), "easy_to_borrow": asset.get("easy_to_borrow"),
                           "short_percent_of_float": short_float,
                           "short_interest_source": source if short_float is not None else None,
                           "float_shares": float_shares,
                           "shares_outstanding": _f(row.get("shares_outstanding")) or _f(details.get("sharesOutstanding"))}
    return {"schema": "ecosystem.short_gate_data.v1", "as_of": today, "generated_at": datetime.now(NEW_YORK).isoformat(),
            "sources": {"assets": "alpaca /v2/assets (paper)", "fundamentals": "giannis_rule_based_latest.json"},
            "thresholds": {"squeeze_short_float_below": SQUEEZE_SHORT_FLOAT_BELOW, "low_float_shares": LOW_FLOAT_SHARES},
            "symbols": symbols}


def resolve(gates: list[str], data: dict[str, Any] | None, ticker: str, today: str) -> tuple[dict[str, str], list[str]]:
    """(resolved gate -> evidence, gates still unresolved). Data of another day resolves nothing."""
    if not data or data.get("as_of") != today:
        return {}, list(gates)
    facts = (data.get("symbols") or {}).get(str(ticker).upper()) or {}
    resolved: dict[str, str] = {}
    for gate in gates:
        if gate == "shortability" and facts.get("shortable") is True:
            resolved[gate] = "broker: shortable"
        elif gate == "borrow_carry" and facts.get("easy_to_borrow") is True:
            resolved[gate] = "broker: easy to borrow (no borrow fee)"
        elif gate == "squeeze_buyback_low_float_risk":
            short_float = facts.get("short_percent_of_float")
            shares = facts.get("float_shares") if facts.get("float_shares") is not None else facts.get("shares_outstanding")
            if (short_float is not None and shares is not None and short_float < SQUEEZE_SHORT_FLOAT_BELOW
                    and shares >= LOW_FLOAT_SHARES):
                resolved[gate] = (f"short interest {short_float:.1%} of float < {SQUEEZE_SHORT_FLOAT_BELOW:.0%}, "
                                  f"{shares:,.0f} shares >= {LOW_FLOAT_SHARES:,}")
    return resolved, [g for g in gates if g not in resolved]


def short_candidates(paths: list[str]) -> set[str]:
    out: set[str] = set()
    for path in paths:
        try:
            rows = json.loads(Path(path).read_text(encoding="utf-8")).get("results") or []
        except (OSError, ValueError):
            continue
        out |= {str(r.get("ticker") or "").upper() for r in rows if isinstance(r, dict)
                and r.get("trade_direction") == "short" and r.get("signal_verdict") == "candidate"}
    return out


def live_info(symbol: str) -> dict[str, Any]:
    """yfinance info of one symbol, only the short-interest fields; {} on any failure (unknown stays unknown)."""
    try:
        import yfinance as yf

        details = yf.Ticker(symbol).info or {}
    except Exception:  # noqa: BLE001 - a provider failure leaves the gate unresolved
        return {}
    return {k: details.get(k) for k in ("shortPercentOfFloat", "floatShares", "sharesOutstanding")}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--giannis", default=str(ROOT / "output" / "giannis_rule_based_latest.json"))
    parser.add_argument("--out", default=str(OUT))
    parser.add_argument("--strategies", nargs="*", default=[str(ROOT / "output" / "preopen" / f"{a}_strategy_preopen_latest.json")
                                                            for a in ("yankos", "periklis")])
    parser.add_argument("--if-stale", action="store_true", help="do nothing when today's file already exists")
    args = parser.parse_args(argv)
    today = datetime.now(NEW_YORK).date().isoformat()
    if args.if_stale:
        try:
            if json.loads(Path(args.out).read_text(encoding="utf-8")).get("as_of") == today:
                print(f"[SHORT-GATES] today's data already in {args.out}")
                return 0
        except (OSError, ValueError):
            pass
    from alpaca_paper_exit_manager import alpaca_request, load_dotenv

    load_dotenv(ROOT / ".env")
    assets = alpaca_request("GET", "/v2/assets?status=active&asset_class=us_equity")
    if not isinstance(assets, list):
        raise SystemExit("Alpaca assets answer is not a list")
    try:
        giannis = json.loads(Path(args.giannis).read_text(encoding="utf-8")).get("results") or []
    except (OSError, ValueError):
        giannis = []  # squeeze risk stays unknown: shorts stay blocked
    wanted = short_candidates(args.strategies)
    live_left = [LIVE_LOOKUPS_MAX]

    def info(symbol: str) -> dict[str, Any]:
        details = cached_info(symbol)
        if details.get("shortPercentOfFloat") is None and symbol in wanted and live_left[0] > 0:
            live_left[0] -= 1
            details = {**details, **live_info(symbol)}
        return details

    payload = build(assets, giannis, today, info)
    payload["live_lookups"] = LIVE_LOOKUPS_MAX - live_left[0]
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    known = sum(1 for s in payload["symbols"].values() if s["short_percent_of_float"] is not None)
    print(f"[SHORT-GATES] {len(payload['symbols'])} assets, {known} with short interest -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
