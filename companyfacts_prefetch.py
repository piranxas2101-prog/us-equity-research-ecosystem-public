"""Nightly: keep SEC companyfacts stored for every company Babis scans, not only the shortlist.

Owner's decision 2026-10-12 (SEC first for Babis's rules): Babis may only score from the SEC when every company it
scans is judged the same way, so the companyfacts store must cover the whole company universe, not just the ~1,060
companies the control-plane batch downloads. This step runs before the Babis scan and downloads, within a nightly
budget, the companies whose stored copy is missing, older than the store's max age, or older than a financial filing
on the SEC calendar. Companies the SEC answers 404 for are remembered (``known_absent``) and not asked again for 14
days. All requests pass the shared SEC rate limiter. The report says how much of the universe is covered, which
decides when ``pipeline.babis_sec_first`` can be switched on.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import time
from typing import Any, Callable

from companyfacts_cache import CompanyFactsCache

ROOT = Path(__file__).resolve().parent
DEFAULT_ROUTES = ROOT / "output" / "all_listed_universe_routes_latest.json"
DEFAULT_TICKERS = ROOT / "cache" / "sec_company_tickers.json"
DEFAULT_REPORT = ROOT / "output" / "companyfacts_prefetch_latest.json"


def company_ciks(routes_path: Path, tickers_path: Path) -> dict[int, str]:
    """CIK -> one ticker for every company route Babis scans (deferred instruments and non-companies left out)."""
    from fundamental_control_plane_batch import _ticker_to_cik

    routes = json.loads(Path(routes_path).read_text(encoding="utf-8-sig")).get("routes") or []
    mapping = _ticker_to_cik(Path(tickers_path))
    ciks: dict[int, str] = {}
    for route in routes:
        if not route.get("company_fundamental_eligible") or route.get("defer_market_scan"):
            continue
        ticker = str(route.get("ticker") or "").upper()
        cik = mapping.get(ticker)
        if cik and cik not in ciks:
            ciks[cik] = ticker
    return ciks


def prefetch(cache: CompanyFactsCache, ciks: dict[int, str], *, budget: int, fetch: Callable[[int], dict[str, Any]],
             limiter: Any = None) -> dict[str, Any]:
    counts = {"companies": len(ciks), "current": 0, "known_absent": 0, "downloaded": 0, "absent_now": 0,
              "failed": 0, "deferred": 0}
    started = time.perf_counter()
    for cik in sorted(ciks):
        if cache.is_current(cik):
            counts["current"] += 1
            continue
        if cache.known_absent(cik):
            counts["known_absent"] += 1
            continue
        if counts["downloaded"] + counts["absent_now"] + counts["failed"] >= budget:
            counts["deferred"] += 1
            continue
        if limiter is not None and not limiter.permit("sec", max_interval_wait_seconds=2.0).permitted:
            counts["deferred"] += 1
            continue
        try:
            raw = fetch(cik)
        except RuntimeError as exc:
            if limiter is not None:
                limiter.record_result("sec", "rate_limited" if "429" in str(exc) else "failed")
            if "HTTP 404" in str(exc):
                cache.note_absent(cik, 404)
                counts["absent_now"] += 1
            else:
                counts["failed"] += 1
            continue
        except OSError:
            counts["failed"] += 1
            continue
        if limiter is not None:
            limiter.record_result("sec", "ok")
        cache.store(cik, raw)
        counts["downloaded"] += 1
    covered = counts["current"] + counts["downloaded"]
    askable = counts["companies"] - counts["known_absent"] - counts["absent_now"]
    return {
        "schema": "ecosystem.companyfacts_prefetch.v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "budget": budget, "counts": counts, "seconds": round(time.perf_counter() - started, 1),
        "coverage_pct": round(100.0 * covered / askable, 1) if askable else None,
        "note": "coverage = companies with a current stored copy among those the SEC publishes companyfacts for",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--config", type=Path, default=ROOT / "scanner_config.json")
    parser.add_argument("--routes", type=Path, default=DEFAULT_ROUTES)
    parser.add_argument("--tickers", type=Path, default=DEFAULT_TICKERS)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--budget", type=int, help="downloads this run (default: config prefetch_budget, else 800)")
    args = parser.parse_args(argv)
    from provider_rate_limiter import SharedProviderRateLimiter
    from research_workflow_orchestrator import load_env
    from sec_xbrl_pilot import fetch_companyfacts

    load_env(ROOT / ".env")  # SEC_USER_AGENT, never printed
    agent = os.getenv("SEC_USER_AGENT", "").strip()
    config = json.loads(args.config.read_text(encoding="utf-8-sig"))
    cfg = ((config.get("data_sources") or {}).get("sec") or {}).get("companyfacts_cache") or {}
    budget = args.budget if args.budget is not None else int(cfg.get("prefetch_budget", 800))
    if not agent or str(cfg.get("mode", "off")) == "off" or not args.routes.is_file():
        print(json.dumps({"skipped": "no SEC user agent, store off, or no routes file"}))
        return 0
    cache = CompanyFactsCache.from_config(config, filing_db=ROOT / "data" / "research_registry.db")
    if cache.mode == "populate":
        cache.mode = "reuse"  # the prefetch must write; populate keeps young copies untouched for the gate
    report = prefetch(cache, company_ciks(args.routes, args.tickers), budget=budget,
                      fetch=lambda cik: fetch_companyfacts(cik, agent), limiter=SharedProviderRateLimiter())
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"counts": report["counts"], "coverage_pct": report["coverage_pct"], "seconds": report["seconds"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
