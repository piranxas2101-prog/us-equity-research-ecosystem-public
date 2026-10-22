"""Nightly gate: move the evidence stores from populate to reuse only on evidence.

Run before each nightly research run. It first reads the SEC filing calendar up to
date, then compares stored copies with fresh answers and records the decision in
data/refresh_modes.json (git-ignored), which the stores read over their configured
mode. It only moves between ``populate`` and ``reuse``; a store configured ``off``
is never touched, and scanner_config.json is never edited.

Criteria (owner-approved plan, docs/FILING_DRIVEN_REFRESH_PLAN.md):

- companyfacts: at least MIN_COMPARED companies compared and every extraction from
  the stored copy identical to the fresh one. Any difference sets ``populate``.
  Only copies at least 3 days old count (oldest first): a copy from hours ago is
  trivially equal to a fresh one, so reuse cannot switch on before real evidence.
- stage-1 info: at least MIN_COMPARED companies compared; every filing-based field
  within 2% for at least 95% of companies (they should not move without a filing),
  and the price-rescaled fields within a median 3% (analyst estimates move).
  Since 2026-10-12 (owner's decision) a company whose rule fields drifted has only its own copy refreshed; the
  whole store goes back to ``populate`` only when more than STORE_WIDE_DRIFT (20%) of the compared companies drift on
  a rule field, or a price field drifts in the median. Since 2026-10-10 (owner's decision) only the fields Babis's fundamental rules use are
  judged (``rule_fields``); the others are reported but cannot switch the store off (on 2026-10-09 two of 23
  companies' share counts moved more than 2% and stopped all reuse, although no rule reads the share count).

Too few comparable companies leaves the mode as it is. The gate never blocks a run.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
from typing import Any

from companyfacts_cache import REFRESH_MODES_FILE, CompanyFactsCache
from stage1_info_cache import PRICE_SCALED_FIELDS, Stage1InfoCache

ROOT = Path(__file__).resolve().parent
MIN_COMPARED = 20
FILING_FIELD_SHARE = 0.95  # before 2026-10-12: one drifting company in twenty switched the whole store off
STORE_WIDE_DRIFT = 0.20     # since 2026-10-12: drifted companies are refreshed one by one; the store flips beyond this
PRICE_FIELD_MEDIAN = 0.03


def decide_companyfacts(shadow: dict[str, Any]) -> tuple[str | None, str]:
    compared = len(shadow.get("identical") or []) + len(shadow.get("different") or [])
    if shadow.get("different"):
        return "populate", f"{len(shadow['different'])} of {compared} extractions differed"
    if compared < MIN_COMPARED:
        return None, f"only {compared} comparable companies (need {MIN_COMPARED})"
    return "reuse", f"all {compared} extractions identical"


# Babis fundamental rule metric -> the yfinance info field it is computed from (us_equity_monitor.fetch_snapshot)
RULE_METRIC_FIELDS = {
    "roe": "returnOnEquity", "profit_margin": "profitMargins", "gross_margin": "grossMargins",
    "revenue_growth": "revenueGrowth", "earnings_growth": "earningsGrowth", "debt_to_equity": "debtToEquity",
    "current_ratio": "currentRatio", "forward_pe": "forwardPE", "operating_margin": "operatingMargins",
    "roa": "returnOnAssets", "trailing_pe": "trailingPE", "price_to_sales": "priceToSalesTrailing12Months",
    "price_to_book": "priceToBook", "peg_ratio": "pegRatio", "quick_ratio": "quickRatio",
}


def rule_fields(config: dict[str, Any]) -> set[str] | None:
    """The info fields Babis's fundamental rules read; None (judge every field) when the rules are unknown."""
    metrics = {str(rule.get("metric")) for rule in config.get("fundamental_rules") or [] if isinstance(rule, dict)}
    fields = {RULE_METRIC_FIELDS[m] for m in metrics if m in RULE_METRIC_FIELDS}
    return fields or None


def decide_stage1_info(shadow: dict[str, Any], fields: set[str] | None = None) -> tuple[str | None, str]:
    compared = int(shadow.get("compared") or 0)
    if compared < MIN_COMPARED:
        return None, f"only {compared} comparable companies (need {MIN_COMPARED})"
    price_fields = set(PRICE_SCALED_FIELDS) | {"enterpriseValue"}
    failures = []
    for field, stats in (shadow.get("fields") or {}).items():
        if fields is not None and field not in fields:
            continue  # reported in the shadow, not used by any rule
        if field in price_fields:
            if stats["median"] > PRICE_FIELD_MEDIAN:
                failures.append(f"{field} median {stats['median']:.3f}")
        elif stats["within_tolerance"] < 1.0 - STORE_WIDE_DRIFT:
            failures.append(f"{field} within 2% for {stats['within_tolerance']:.0%}")
    if failures:
        return "populate", "; ".join(failures[:6])
    drifted = [symbol for symbol, keys in (shadow.get("drifted") or {}).items()
               if fields is None or any(key in fields for key in keys)]
    if drifted:
        return "reuse", (f"{compared - len(drifted)} of {compared} companies within tolerance; "
                         f"{len(drifted)} drifted and are refreshed one by one")
    return "reuse", f"{compared} companies within tolerance"


def evaluate(config: dict[str, Any], *, state_path: Path = REFRESH_MODES_FILE) -> dict[str, Any]:
    from research_registry import sec_filing_index
    from research_registry.core import DEFAULT_DB, connect
    from research_workflow_orchestrator import load_env
    import companyfacts_cache
    import stage1_info_cache
    from us_equity_monitor import load_sec_ticker_cik_map

    load_env(ROOT / ".env")  # SEC_USER_AGENT, never printed
    agent = os.getenv("SEC_USER_AGENT", "").strip()
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        state = {}
    result: dict[str, Any] = {"evaluated_at": datetime.now(timezone.utc).isoformat()}
    if not agent:
        result["error"] = "SEC_USER_AGENT is not configured"
        return result
    db = connect(DEFAULT_DB)
    try:
        sync = sec_filing_index.sync(db, sec_filing_index.default_fetch(agent), today=sec_filing_index.sec_today())
        result["filing_calendar"] = {"days_read": len(sync["read"]), "failed": sync["failed"]}
    finally:
        db.close()

    checks = (
        ("companyfacts", lambda: companyfacts_cache.shadow(
            CompanyFactsCache.from_config(config), agent, limit=50,
            as_of=datetime.now(timezone.utc).date().isoformat()), decide_companyfacts,
         ((config.get("data_sources") or {}).get("sec") or {}).get("companyfacts_cache")),
        ("stage1_info", lambda: _stage1_shadow(config, load_sec_ticker_cik_map, stage1_info_cache),
         lambda shadow: decide_stage1_info(shadow, rule_fields(config)),
         (config.get("pipeline") or {}).get("stage1_info_cache")),
    )
    for key, run_shadow, decide, cfg in checks:
        if str((cfg or {}).get("mode", "off")) == "off":
            result[key] = {"decision": "unchanged", "reason": "store is configured off"}
            continue
        try:
            shadow = run_shadow()
            mode, reason = decide(shadow)
        except Exception as exc:  # noqa: BLE001 - the gate reports, never blocks
            result[key] = {"decision": "unchanged", "reason": f"comparison failed: {type(exc).__name__}"}
            continue
        entry = {"decision": mode or "unchanged", "reason": reason, "summary": shadow.get("summary") or
                 {"compared": shadow.get("compared"), "skipped": shadow.get("skipped")}}
        if mode:
            state[key] = {"mode": mode, "decided_at": result["evaluated_at"], "reason": reason}
        if key == "stage1_info" and shadow.get("drifted"):
            entry["refreshed_companies"] = refresh_drifted(
                Stage1InfoCache.from_config(config, load_sec_ticker_cik_map(config.get("data_sources") or {})),
                shadow, rule_fields(config))
        result[key] = entry
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    result["modes"] = {key: (state.get(key) or {}).get("mode") for key in ("companyfacts", "stage1_info")}
    return result


def refresh_drifted(cache: Stage1InfoCache, shadow: dict[str, Any], fields: set[str] | None) -> list[str]:
    """Drop the stored copy of every company whose rule fields drifted, so tonight's run asks yfinance for it."""
    refreshed = []
    for symbol, keys in sorted((shadow.get("drifted") or {}).items()):
        if (fields is None or any(key in fields for key in keys)) and cache.invalidate(symbol):
            refreshed.append(symbol)
    return refreshed


def _stage1_shadow(config: dict[str, Any], load_cik_map: Any, module: Any) -> dict[str, Any]:
    cache = Stage1InfoCache.from_config(config, load_cik_map(config.get("data_sources") or {}))
    symbols = [p.stem for p in cache.directory.glob("*.json")]
    return module.shadow(cache, symbols, limit=50)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--config", type=Path, default=ROOT / "scanner_config.json")
    parser.add_argument("--output", type=Path, default=ROOT / "output" / "refresh_gate_latest.json")
    args = parser.parse_args(argv)
    try:
        result = evaluate(json.loads(args.config.read_text(encoding="utf-8-sig")))
    except Exception as exc:  # noqa: BLE001 - never blocks the nightly run
        result = {"error": f"{type(exc).__name__}: {str(exc)[:200]}"}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
