"""Generate a readable, local analyst report from one immutable run."""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import symbol_research_dashboard as dashboard


ROOT = Path(__file__).resolve().parent


def text(value: Any) -> str:
    if value in (None, "", [], {}):
        return "Unknown"
    return json.dumps(value, ensure_ascii=False, sort_keys=True) if isinstance(value, (dict, list)) else str(value)


def build(manifest: Path, output: Path, ticker: str | None = None) -> dict[str, Any]:
    meta, artifacts, verified = dashboard.load_run(manifest)
    reader = dashboard.LocalPacketReader(ROOT / "data" / "market_intel.db", str(meta["run_id"]))
    try:
        records = dashboard.build_symbol_records(artifacts, reader)
    finally:
        reader.close()
    if ticker:
        records = [row for row in records if row["ticker"] == ticker.upper()]
    lines = ["# Equity Research Report", "", f"- Run: `{meta['run_id']}`",
             f"- Generated: {datetime.now(timezone.utc).isoformat()}",
             f"- Verified artifacts: {len(verified)}", "- Scope: research only; no execution authority", ""]
    macro = dashboard._safe_macro_context(artifacts["babis"].get("macro_context")) or {}
    lines += ["## Macroeconomic context", "", f"- Regime: {text((macro.get('regime_assessment') or {}).get('regime'))}",
              f"- Confidence: {text((macro.get('regime_assessment') or {}).get('confidence'))}", ""]
    for row in records:
        p, g, b = row["petros"], row["giannis"], row["babis"]
        lines += [f"## {row['ticker']} · {row['company']}", "",
                  f"- Sector: {text(row.get('sector'))} / {text(row.get('industry'))}",
                  f"- Babis rank: {text(b.get('rank'))}; evidence coverage: {text(b.get('fundamental_coverage'))}",
                  f"- Giannis tier: {text(g.get('giannis_tier'))}; score: {text(g.get('score_final'))}; risk: {text(g.get('risk_level'))}",
                  f"- Petros verdict: {text(p.get('petros_verdict'))}; score: {text(p.get('petros_score'))}",
                  f"- Priced-in status: {text(p.get('priced_in_status'))}",
                  f"- DCF range: {text(p.get('valuation_range'))}", "",
                  "### Thesis controls", "",
                  f"- Rationale: {text(p.get('rationale'))}",
                  f"- Prove criteria: {text(p.get('prove_criteria'))}",
                  f"- Kill criteria: {text(p.get('kill_criteria'))}",
                  f"- Decision blockers: {text(p.get('decision_blockers'))}", ""]
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("\n".join(lines), encoding="utf-8")
    return {"run_id": meta["run_id"], "symbols": len(records), "output": str(output.resolve())}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--ticker")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    manifest = args.manifest or dashboard.latest_manifest(ROOT)
    suffix = f"_{args.ticker.upper()}" if args.ticker else ""
    output = args.output or ROOT / "output" / "reports" / f"analyst_report_{manifest.parent.name}{suffix}.md"
    print(json.dumps(build(manifest, output, args.ticker), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
