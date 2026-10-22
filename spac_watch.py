"""SPAC shells that filed a merger document (S-4, F-4, DEFM14A, PREM14A, 425).

A SPAC has no business until it merges; it becomes worth a look when it files the
documents of a merger. This reads those filings from the SEC filing calendar
(research_registry.sec_filing_index) for the SPAC lane. It only reports what was
filed: an empty answer means "none recorded", within the calendar's coverage,
not proof that nothing happened.
"""
from __future__ import annotations

from datetime import date, timedelta
import json
from pathlib import Path
import sqlite3
from typing import Any

ROOT = Path(__file__).resolve().parent
SEC_TICKERS = ROOT / "cache" / "sec_company_tickers_exchange.json"
REGISTRY_DB = ROOT / "data" / "research_registry.db"
WINDOW_DAYS = 30


def ticker_ciks(path: Path = SEC_TICKERS) -> dict[str, str]:
    from research_registry.instrument_links import sec_identities

    try:
        exact, _ = sec_identities(json.loads(path.read_text(encoding="utf-8-sig")))
    except (OSError, ValueError):
        return {}
    return {ticker: next(iter(ciks)) for ticker, ciks in exact.items() if len(ciks) == 1}


def merger_filings(tickers: list[str], *, until: date, days: int = WINDOW_DAYS, sec_tickers: Path = SEC_TICKERS,
                   registry_db: Path = REGISTRY_DB) -> dict[str, list[dict[str, Any]]]:
    """Ticker -> merger filings of its SEC issuer within (until - days, until]."""
    ciks = ticker_ciks(sec_tickers)
    wanted = {t.upper(): ciks[t.upper()] for t in tickers if ciks.get(t.upper())}
    if not wanted or not registry_db.is_file():
        return {}
    try:
        db = sqlite3.connect(f"file:{registry_db.resolve().as_posix()}?mode=ro", uri=True, timeout=10)
    except sqlite3.Error:
        return {}
    found: dict[str, list[dict[str, Any]]] = {}
    try:
        for ticker, cik in sorted(wanted.items()):
            rows = db.execute(
                "SELECT form, filed_date, accession, company_name FROM sec_filing_event "
                "WHERE cik=? AND category='merger' AND filed_date>? AND filed_date<=? ORDER BY filed_date DESC",
                (cik, (until - timedelta(days=days)).isoformat(), until.isoformat())).fetchall()
            if rows:
                found[ticker] = [dict(zip(("form", "filed_date", "accession", "company_name"), r)) for r in rows]
    except sqlite3.Error:
        return {}  # calendar tables missing: nothing to report
    finally:
        db.close()
    return found


def issuer_count(tickers: list[str], sec_tickers: Path = SEC_TICKERS) -> int:
    """Distinct issuers behind SPAC tickers: shares, units, warrants and rights of one SPAC are one issuer."""
    ciks = ticker_ciks(sec_tickers)
    return len({ciks.get(ticker.upper()) or ticker.upper() for ticker in tickers})


def merger_issuers(tickers: list[str], *, until: date, days: int = WINDOW_DAYS, sec_tickers: Path = SEC_TICKERS,
                   registry_db: Path = REGISTRY_DB) -> list[dict[str, Any]]:
    """Merger filings grouped by issuer, most recent filing first (one entry per SPAC, not per security)."""
    ciks = ticker_ciks(sec_tickers)
    found = merger_filings(tickers, until=until, days=days, sec_tickers=sec_tickers, registry_db=registry_db)
    issuers: dict[str, dict[str, Any]] = {}
    for ticker, filings in sorted(found.items()):
        cik = ciks.get(ticker.upper())
        item = issuers.setdefault(cik or ticker, {"cik": cik, "company_name": filings[0]["company_name"],
                                                  "tickers": [], "filings": filings})
        item["tickers"].append(ticker)
    return sorted(issuers.values(), key=lambda issuer: issuer["filings"][0]["filed_date"], reverse=True)


def summarize_filings(filings: list[dict[str, Any]], limit: int = 4) -> str:
    """'425 2026-09-17 (x4), S-4/A 2026-09-04': identical form/date pairs are counted, not repeated."""
    counts: dict[tuple[str, str], int] = {}
    for filing in filings:
        key = (str(filing["form"]), str(filing["filed_date"]))
        counts[key] = counts.get(key, 0) + 1
    parts = [f"{form} {day}" + (f" (x{n})" if n > 1 else "") for (form, day), n in counts.items()]
    return ", ".join(parts[:limit]) + (f" +{len(parts) - limit} ακόμα" if len(parts) > limit else "")
