"""The companies the results tools cover: every company of the listed universe, not only the shortlist.

Owner's decision 2026-10-22 (the LAES report): a company the stage-1 filter cuts (LAES) had no results history in
the journal. The results per period, the results journal and the press-release reader now read every listed
company routed to company fundamental research (``output/all_listed_universe_routes_latest.json``: company
fundamental eligible, scanned in the central run, not a warrant/unit linked to an issuer), plus the shortlist.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def _load(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return {}


def company_tickers(output: Path) -> list[str]:
    routes = _load(output / "all_listed_universe_routes_latest.json").get("routes") or []
    shortlist = _load(output / "babis_shortlist_latest.json").get("shortlist") or []
    tickers = {str(r.get("ticker") or "").upper() for r in routes
               if r.get("company_fundamental_eligible") and not r.get("defer_market_scan") and not r.get("issuer_ticker")}
    tickers |= {str(r.get("ticker") or "").upper() for r in shortlist}
    return sorted(tickers - {""})
