"""Age of the last periodic SEC report (10-K/10-Q/20-F/40-F) per company, from the local companyfacts store and the SEC daily filing calendar.

Giannis used to call its primary data stale when a company had filed *nothing* in 30 days. A company that
filed its 10-Q six weeks ago has perfectly current fundamentals, so what matters for fundamental freshness is
the age of the last periodic report, not of the last filing of any kind (an 8-K says nothing about the ratios).

The date is read from the SEC documents already stored by ``companyfacts_cache`` (no network). A sidecar index
keeps one entry per CIK, keyed by the sha256 of the stored document, so a nightly refresh only re-reads
documents that changed. A company without a stored document, or without a periodic form in it, simply has no
entry: unknown stays unknown, and Giannis then scores it as before.
"""
from __future__ import annotations

from datetime import date
import gzip
import json
from pathlib import Path
import re
import sqlite3
from typing import Any

ROOT = Path(__file__).resolve().parent
DEFAULT_DIR = ROOT / "cache" / "sec_companyfacts"
DEFAULT_REGISTRY = ROOT / "data" / "research_registry.db"
DEFAULT_TICKERS = ROOT / "cache" / "sec_company_tickers_exchange.json"
INDEX_NAME = "periodic_filed.json"
SCHEMA = "ecosystem.periodic_filing_index.v1"

# Amendments are left out on purpose: a 10-Q/A does not bring new fundamentals.
QUARTERLY_FORMS = frozenset({"10-K", "10-KT", "10-Q", "10-QT"})
ANNUAL_ONLY_FORMS = frozenset({"20-F", "40-F"})
PERIODIC_FORMS = QUARTERLY_FORMS | ANNUAL_ONLY_FORMS
# A quarterly filer files again at most ~100 days later (quarter + the 40-45 day deadline); 135 leaves margin.
# A 20-F/40-F filer reports once a year.
MAX_AGE_DAYS = {"quarterly": 135, "annual": 400}

_ENTRY = re.compile(rb'"form":\s*"([0-9A-Za-z/\-]+)",\s*"filed":\s*"(\d{4}-\d{2}-\d{2})"')


def latest_periodic_from_bytes(body: bytes) -> tuple[str, str] | None:
    """(filed_date, form) of the newest periodic report in a companyfacts document, or None."""
    best: tuple[str, str] | None = None
    for form_raw, filed_raw in _ENTRY.findall(body):
        form = form_raw.decode("ascii")
        if form in PERIODIC_FORMS:
            filed = filed_raw.decode("ascii")
            if best is None or filed > best[0]:
                best = (filed, form)
    return best


def freshness_limit(form: str | None) -> int:
    return MAX_AGE_DAYS["annual" if form in ANNUAL_ONLY_FORMS else "quarterly"]


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def refresh_index(directory: Path = DEFAULT_DIR) -> dict[str, dict[str, Any]]:
    """Bring the sidecar index up to date; only documents whose sha256 changed are read again."""
    index_path = directory / INDEX_NAME
    current = _read_json(index_path)
    entries: dict[str, dict[str, Any]] = dict(current.get("entries", {})) if isinstance(current, dict) and current.get("schema") == SCHEMA else {}
    changed = False
    seen: set[str] = set()
    for meta_path in sorted(directory.glob("CIK*.meta.json")):
        meta = _read_json(meta_path)
        if not isinstance(meta, dict) or "cik" not in meta:
            continue
        cik = str(int(meta["cik"]))
        seen.add(cik)
        sha = meta.get("sha256")
        if entries.get(cik, {}).get("sha256") == sha:
            continue
        data_path = meta_path.with_name(meta_path.name.replace(".meta.json", ".json.gz"))
        try:
            found = latest_periodic_from_bytes(gzip.decompress(data_path.read_bytes()))
        except (OSError, EOFError):
            continue
        entries[cik] = {"sha256": sha, "filed": found[0] if found else None, "form": found[1] if found else None}
        changed = True
    for cik in set(entries) - seen:
        del entries[cik]
        changed = True
    if changed:
        temporary = index_path.with_name(index_path.name + ".tmp")
        temporary.write_text(json.dumps({"schema": SCHEMA, "entries": entries}), encoding="utf-8")
        temporary.replace(index_path)
    return entries


def ticker_to_cik(tickers_file: Path = DEFAULT_TICKERS) -> dict[str, str]:
    """Ticker -> CIK from the SEC ticker map the monitor already caches locally (no network)."""
    raw = _read_json(tickers_file)
    if not isinstance(raw, dict) or not isinstance(raw.get("data"), list):
        return {}
    fields = raw.get("fields") or []
    try:
        cik_at, ticker_at = fields.index("cik"), fields.index("ticker")
    except ValueError:
        return {}
    mapping: dict[str, str] = {}
    for row in raw["data"]:
        try:
            mapping.setdefault(str(row[ticker_at]).upper(), str(int(row[cik_at])))
        except (IndexError, TypeError, ValueError):
            continue
    return mapping


def calendar_periodic(registry_db: Path = DEFAULT_REGISTRY) -> dict[str, tuple[str, str]]:
    """CIK -> (filed_date, form) of the newest periodic report in the SEC daily filing calendar; read-only.

    companyfacts can lag a filing by weeks (PPG, PYPL and ED filed their Q2 10-Q in July/August, yet the stored
    companyfacts of 30 September did not contain it), whereas the daily form index lists every filing the day it
    is accepted. The calendar is only evidence of a *newer* report; it never makes a date older.
    """
    try:
        db = sqlite3.connect(f"file:{registry_db.as_posix()}?mode=ro", uri=True)
    except sqlite3.Error:
        return {}
    try:
        marks = ",".join("?" for _ in PERIODIC_FORMS)
        rows = db.execute(f"SELECT cik, filed_date, form FROM sec_filing_event WHERE form IN ({marks}) "
                          "ORDER BY filed_date", sorted(PERIODIC_FORMS)).fetchall()
    except sqlite3.Error:
        return {}
    finally:
        db.close()
    newest: dict[str, tuple[str, str]] = {}
    for cik, filed, form in rows:  # ascending by date, so the last one per CIK wins
        try:
            newest[str(int(cik))] = (str(filed), str(form))
        except (TypeError, ValueError):
            continue
    return newest


def calendar_coverage_days(registry_db: Path = DEFAULT_REGISTRY, *, today: date) -> int:
    """How many days back the SEC filing calendar has been read (0 when unknown)."""
    try:
        db = sqlite3.connect(f"file:{registry_db.as_posix()}?mode=ro", uri=True)
    except sqlite3.Error:
        return 0
    try:
        first = db.execute("SELECT MIN(day) FROM sec_filing_index_day").fetchone()[0]
    except sqlite3.Error:
        return 0
    finally:
        db.close()
    try:
        return max(0, (today - date.fromisoformat(first)).days) if first else 0
    except ValueError:
        return 0


def annotate_rows(rows: list[dict[str, Any]], *, today: date | None = None, directory: Path = DEFAULT_DIR,
                  tickers_file: Path = DEFAULT_TICKERS, registry_db: Path = DEFAULT_REGISTRY) -> int:
    """Add periodic_filing_date / form / age_days to rows with a known periodic report; returns the count.

    The date is the newer of the stored companyfacts and the SEC filing calendar.
    """
    today = today or date.today()
    entries = refresh_index(directory)
    calendar = calendar_periodic(registry_db)
    coverage = calendar_coverage_days(registry_db, today=today)
    mapping = ticker_to_cik(tickers_file)
    annotated = 0
    for row in rows:
        cik = mapping.get(str(row.get("ticker") or "").upper())
        if not cik:
            continue
        entry = entries.get(cik) or {}
        best = (entry["filed"], entry["form"]) if entry.get("filed") else None
        seen = calendar.get(cik)
        if seen and (best is None or seen[0] > best[0]):
            best = seen
        if best is None:
            continue
        row["periodic_filing_date"], row["periodic_filing_form"] = best
        age = max(0, (today - date.fromisoformat(best[0])).days)
        if age > freshness_limit(best[1]) and coverage < freshness_limit(best[1]):
            # Old evidence is only conclusive when the calendar reaches back as far as the limit: a 20-F is filed
            # once a year, so a calendar of 140 days cannot show that no newer one exists. Unknown stays unknown.
            row["periodic_filing_note"] = "calendar_window_shorter_than_limit"
            continue
        row["periodic_filing_age_days"] = age
        annotated += 1
    return annotated
