"""Bounded live trial of the parallel Babis stage-1 fetch (pipeline.stage1_workers), approved by the owner 2026-10-06.

Scans the same symbols twice with the nightly stage-1 settings, first sequentially, then with N workers, and compares
timing, provider errors and results. It writes only its own report (output/babis_parallel_trial_<stamp>.json/.md):
no checkpoint, no shortlist, no membership state, no info-cache writes (info_cache=None, so every info call is live,
the heaviest case for yfinance). Research only; nothing talks to a broker.

Run from the project root:  .venv-next\\Scripts\\python.exe tools\\babis_parallel_trial.py --symbols 60 --workers 3
"""
from __future__ import annotations

import argparse
from datetime import datetime
import json
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import us_equity_monitor as monitor  # noqa: E402


def pick_symbols(count: int) -> list[str]:
    """Shortlist names first (they pass every filter), then other company routes, alphabetically spread."""
    shortlist = json.loads(Path("output/babis_shortlist_latest.json").read_text(encoding="utf-8-sig")).get("shortlist") or []
    tickers = [str(row["ticker"]) for row in shortlist if row.get("ticker")]
    step = max(1, len(tickers) // count)
    return tickers[::step][:count]


def scan(symbols: list[str], workers: int, config: dict) -> tuple[dict, float]:
    pipeline_cfg = config.get("pipeline", {})
    scan_cfg = config.get("scan", {})
    data_cfg, profile = monitor.build_stage1_data_sources_cfg(config)
    started = time.perf_counter()
    payload = monitor.scan_market_core(
        symbols=symbols,
        lookback_days=int(scan_cfg.get("lookback_days", 420)),
        min_history_days=int(scan_cfg.get("min_history_days", 220)),
        max_price=monitor.to_float(scan_cfg.get("max_price")),
        min_composite_score=monitor.to_float(pipeline_cfg.get("stage1_min_composite_score")),
        min_fundamental_coverage=monitor.to_float(pipeline_cfg.get("stage1_min_fundamental_coverage")),
        ma_rules=config.get("moving_average_rules", config.get("technical_rules", [])),
        fundamental_rules=config.get("fundamental_rules", []),
        ma_weight=0.0, fundamental_weight=1.0,
        data_sources_cfg=data_cfg, snapshot_profile=profile,
        workers=workers,
    )
    return payload, time.perf_counter() - started


def summary(payload: dict, seconds: float, symbols: int) -> dict:
    timing = payload.get("runtime_summary") or {}
    return {
        "seconds": round(seconds, 1), "seconds_per_symbol": round(seconds / max(1, symbols), 2),
        "results": len(payload.get("results") or []), "errors": len(payload.get("errors") or []),
        "error_samples": [str(e)[:200] for e in (payload.get("errors") or [])[:5]],
        "provider_timeouts": sum(len((row.get("source_base") or {}).get("provider_timeouts") or [])
                                 for row in payload.get("results") or []),
        "market_data_seconds": timing.get("market_data_seconds"),
        "fundamentals_and_news_seconds": timing.get("fundamentals_and_news_seconds"),
        "sec_seconds": timing.get("sec_seconds"),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--symbols", type=int, default=60)
    parser.add_argument("--workers", type=int, default=3)
    args = parser.parse_args()
    monitor.load_dotenv(Path(".env"))
    config = monitor.load_json(Path("scanner_config.json"))
    symbols = pick_symbols(args.symbols)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out = Path("output") / f"babis_parallel_trial_{stamp}"
    report: dict = {"started_at": datetime.now().isoformat(), "symbols": symbols, "workers": args.workers}
    seq, seq_s = scan(symbols, 1, config)
    report["sequential"] = summary(seq, seq_s, len(symbols))
    par, par_s = scan(symbols, args.workers, config)
    report["parallel"] = summary(par, par_s, len(symbols))
    key = lambda p: [(r.get("ticker"), r.get("composite_score")) for r in p.get("results") or []]  # noqa: E731
    report["same_results"] = key(seq) == key(par)
    report["differences"] = sorted(set(map(str, key(seq))) ^ set(map(str, key(par))))[:20]
    report["speedup"] = round(seq_s / par_s, 2) if par_s else None
    report["finished_at"] = datetime.now().isoformat()
    Path(f"{out}.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    s, p = report["sequential"], report["parallel"]
    Path(f"{out}.md").write_text("\n".join([
        f"# Δοκιμή παράλληλης λήψης Babis · {len(symbols)} σύμβολα · {args.workers} ταυτόχρονες",
        "", f"- Σειριακά: {s['seconds']}″ ({s['seconds_per_symbol']}″/σύμβολο), σφάλματα {s['errors']}, όρια χρόνου {s['provider_timeouts']}",
        f"- Παράλληλα: {p['seconds']}″ ({p['seconds_per_symbol']}″/σύμβολο), σφάλματα {p['errors']}, όρια χρόνου {p['provider_timeouts']}",
        f"- Επιτάχυνση: ×{report['speedup']} · ίδια αποτελέσματα: {'ναι' if report['same_results'] else 'όχι'}",
        f"- Διαφορές: {report['differences'] or '—'}", ""]), encoding="utf-8")
    print(f"[TRIAL] {out}.md")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
