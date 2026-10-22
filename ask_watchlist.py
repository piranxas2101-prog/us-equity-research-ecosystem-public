from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any
from common import load_json


def fmt(value: Any) -> str:
    if value is None or value == "":
        return "N/A"
    return str(value)


def print_babis_row(row: dict[str, Any]) -> None:
    print(
        f"{fmt(row.get('rank')).rjust(3)} {fmt(row.get('ticker')).ljust(7)} "
        f"score={fmt(row.get('composite_score')).rjust(6)} "
        f"price={fmt(row.get('share_price')).rjust(8)} "
        f"risk={fmt(row.get('risk_level')).ljust(7)} "
        f"{fmt(row.get('company'))}"
    )
    print(f"    catalyst: {fmt(row.get('catalyst_bucket'))} | {fmt(row.get('main_catalyst'))}")


def print_giannis_row(row: dict[str, Any]) -> None:
    print(
        f"{fmt(row.get('ticker')).ljust(7)} "
        f"score={fmt(row.get('score_final')).rjust(5)}/10 "
        f"tier={fmt(row.get('giannis_tier')).ljust(20)} "
        f"risk={fmt(row.get('risk_level')).ljust(7)} "
        f"price={fmt(row.get('share_price')).rjust(8)} "
        f"{fmt(row.get('company'))}"
    )
    print(f"    catalyst: {fmt(row.get('catalyst_bucket'))} | {fmt(row.get('main_catalyst'))}")
    flags = row.get("dilution_flags") or []
    if isinstance(flags, list):
        flags_text = ", ".join(str(x) for x in flags) or "none"
    else:
        flags_text = str(flags)
    print(f"    flags: {flags_text}")


def command_summary(args: argparse.Namespace) -> None:
    babis = load_json(Path(args.babis))
    giannis = load_json(Path(args.giannis))
    results = giannis.get("results", [])
    tiers: dict[str, int] = {}
    for row in results if isinstance(results, list) else []:
        tier = str((row or {}).get("giannis_tier") or "UNKNOWN")
        tiers[tier] = tiers.get(tier, 0) + 1

    print("Babis")
    print(f"  run: {fmt(babis.get('run_timestamp'))}")
    print(f"  shortlist: {fmt(babis.get('shortlist_total'))}")
    print(f"  candidates: {fmt(babis.get('candidates_total'))}")
    print("")
    print("Giannis")
    print(f"  run: {fmt(giannis.get('run_timestamp'))}")
    print(f"  coverage: {fmt(giannis.get('count'))}")
    for tier, count in sorted(tiers.items()):
        print(f"  {tier}: {count}")


def command_top(args: argparse.Namespace) -> None:
    giannis = load_json(Path(args.giannis))
    rows = giannis.get("results", [])
    if not isinstance(rows, list):
        return
    filtered = rows
    if args.tier:
        filtered = [row for row in filtered if str(row.get("giannis_tier") or "").lower() == args.tier.lower()]
    for row in filtered[: args.limit]:
        print_giannis_row(row)


def command_avoid(args: argparse.Namespace) -> None:
    giannis = load_json(Path(args.giannis))
    rows = giannis.get("results", [])
    if not isinstance(rows, list):
        return
    sorted_rows = sorted(rows, key=lambda x: float(x.get("score_final") or 0.0))
    for row in sorted_rows[: args.limit]:
        print_giannis_row(row)


def command_ticker(args: argparse.Namespace) -> None:
    ticker = args.ticker.upper()
    babis = load_json(Path(args.babis))
    giannis = load_json(Path(args.giannis))
    babis_rows = babis.get("shortlist", [])
    giannis_rows = giannis.get("results", [])

    print(f"Ticker: {ticker}")
    found = False
    if isinstance(babis_rows, list):
        for row in babis_rows:
            if str((row or {}).get("ticker") or "").upper() == ticker:
                print("")
                print("Babis")
                print_babis_row(row)
                found = True
                break
    if isinstance(giannis_rows, list):
        for row in giannis_rows:
            if str((row or {}).get("ticker") or "").upper() == ticker:
                print("")
                print("Giannis")
                print_giannis_row(row)
                found = True
                break
    if not found:
        print("Not found in latest Babis/Giannis outputs.")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Ask the latest non-LLM watchlist reports.")
    parser.add_argument("--babis", default="output/babis_shortlist_latest.json")
    parser.add_argument("--giannis", default="output/giannis_rule_based_latest.json")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("summary", help="Show latest Babis/Giannis summary.").set_defaults(func=command_summary)

    top = subparsers.add_parser("top", help="Show top Giannis names.")
    top.add_argument("--limit", type=int, default=10)
    top.add_argument("--tier", help="Optional exact tier filter, e.g. Core Watchlist.")
    top.set_defaults(func=command_top)

    avoid = subparsers.add_parser("avoid", help="Show lowest-scoring / avoid names.")
    avoid.add_argument("--limit", type=int, default=10)
    avoid.set_defaults(func=command_avoid)

    ticker = subparsers.add_parser("ticker", help="Show Babis and Giannis view for one ticker.")
    ticker.add_argument("ticker")
    ticker.set_defaults(func=command_ticker)
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    args.func(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
