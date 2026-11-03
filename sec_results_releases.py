"""Results release dates from the SEC (F5, owner 2026-11-03): every 8-K with item 2.02 of every company.

The results calendar (``results_calendar.py``) first estimated the next results from periodic report filing dates.
A results release comes before the report: about a day for a 10-Q, up to weeks for a 10-K (PTC: early November,
10-K on 2025-11-21), and the local 8-K index starts in May 2026, too late for last year's. The SEC submissions
file of a company (``data.sec.gov/submissions/CIK##########.json``) lists every filing with its 8-K items, so the
release behind each periodic report can be dated exactly: the latest 8-K carrying item 2.02 ("Results of Operations
and Financial Condition") filed in the ``RELEASE_BEFORE_DAYS`` before the report.

Stored in ``data/sec_results_releases.sqlite3`` (company: when asked, status; release: cik, accession, filed, form,
items). A company is asked again after ``REFRESH_DAYS`` (the nightly results journal reads new releases in between);
a night asks at most ``--budget`` companies, all through the shared SEC rate limiter. Foreign filers release
results on 6-K without items: they keep the periodic-report estimate. Unknown stays unknown.

    sec_results_releases.py [--budget N]      # nightly before the results calendar
"""
from __future__ import annotations

import argparse
from contextlib import closing
from datetime import date, datetime, timedelta, timezone
import json
import os
from pathlib import Path
import sqlite3
import time
from typing import Any, Callable
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parent
DB = ROOT / "data" / "sec_results_releases.sqlite3"
JOURNAL = ROOT / "data" / "earnings_journal.db"
REPORT = ROOT / "output" / "sec_results_releases_latest.json"
URL = "https://data.sec.gov/submissions/{name}"
RESULTS_ITEM = "2.02"
RELEASE_BEFORE_DAYS = 45
REFRESH_DAYS = 30
HISTORY_DAYS = 420  # last year's releases are what the estimate needs
DEFAULT_BUDGET = 800


def schema(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS company (cik INTEGER PRIMARY KEY, asked_at TEXT NOT NULL, status TEXT NOT NULL,
                                            releases INTEGER NOT NULL DEFAULT 0, detail TEXT);
        CREATE TABLE IF NOT EXISTS release (accession TEXT PRIMARY KEY, cik INTEGER NOT NULL, filed TEXT NOT NULL,
                                            form TEXT NOT NULL, items TEXT);
        CREATE INDEX IF NOT EXISTS release_cik ON release(cik, filed);
    """)


def releases_from(submissions: dict[str, Any]) -> list[dict[str, str]]:
    """The 8-K / 8-K/A filings carrying item 2.02 in one submissions document (``recent`` or an older page)."""
    block = (submissions.get("filings") or {}).get("recent") if "filings" in submissions else submissions
    block = block or {}
    out = []
    for k, form in enumerate(block.get("form") or []):
        items = str((block.get("items") or [""] * (k + 1))[k] or "")
        if str(form) in ("8-K", "8-K/A") and RESULTS_ITEM in [i.strip() for i in items.split(",")]:
            out.append({"accession": str(block["accessionNumber"][k]), "filed": str(block["filingDate"][k]),
                        "form": str(form), "items": items})
    return out


def oldest_filing(submissions: dict[str, Any]) -> str | None:
    block = (submissions.get("filings") or {}).get("recent") if "filings" in submissions else submissions
    dates = [str(d) for d in (block or {}).get("filingDate") or []]
    return min(dates) if dates else None


def ask(cik: int, fetch: Callable[[str], dict[str, Any]], today: date) -> list[dict[str, str]]:
    """Every results release of the company back to ``HISTORY_DAYS`` (one older page at most when needed)."""
    first = fetch(URL.format(name=f"CIK{cik:010d}.json"))
    found = releases_from(first)
    oldest = oldest_filing(first)
    pages = (first.get("filings") or {}).get("files") or []
    if oldest and oldest > (today - timedelta(days=HISTORY_DAYS)).isoformat() and pages:
        found += releases_from(fetch(URL.format(name=pages[0]["name"])))
    return found


def due(conn: sqlite3.Connection, ciks: list[int], today: date) -> list[int]:
    asked = dict(conn.execute("SELECT cik, asked_at FROM company"))
    limit = (today - timedelta(days=REFRESH_DAYS)).isoformat()
    never = [c for c in ciks if c not in asked]
    stale = sorted((c for c in ciks if c in asked and str(asked[c])[:10] < limit), key=lambda c: asked[c])
    return never + stale


def run(conn: sqlite3.Connection, ciks: list[int], *, fetch: Callable[[str], dict[str, Any]], today: date,
        budget: int, limiter: Any = None) -> dict[str, Any]:
    schema(conn)
    counts = {"companies": len(ciks), "asked": 0, "releases_stored": 0, "not_found": 0, "failed": 0, "deferred": 0}
    started = time.perf_counter()
    for cik in due(conn, ciks, today):
        if counts["asked"] + counts["failed"] + counts["not_found"] >= budget:
            counts["deferred"] += 1
            continue
        if limiter is not None and not limiter.permit("sec", max_interval_wait_seconds=2.0).permitted:
            counts["deferred"] += 1
            continue
        now = datetime.now(timezone.utc).isoformat()
        try:
            found = ask(cik, fetch, today)
        except urllib.error.HTTPError as exc:
            if limiter is not None:
                limiter.record_result("sec", "rate_limited" if exc.code == 429 else "failed")
            status = "not_found" if exc.code == 404 else "failed"
            counts[status] += 1
            if status == "not_found":
                conn.execute("INSERT OR REPLACE INTO company VALUES(?,?,?,?,?)", (cik, now, status, 0, "HTTP 404"))
                conn.commit()
            continue
        except (urllib.error.URLError, OSError, ValueError, KeyError, IndexError, TypeError) as exc:
            counts["failed"] += 1
            if limiter is not None:
                limiter.record_result("sec", "failed")
            print(f"[SEC-RELEASES] CIK {cik}: {type(exc).__name__}: {str(exc)[:120]}")
            continue
        if limiter is not None:
            limiter.record_result("sec", "ok")
        conn.executemany("INSERT OR REPLACE INTO release VALUES(?,?,?,?,?)",
                         [(r["accession"], cik, r["filed"], r["form"], r["items"]) for r in found])
        conn.execute("INSERT OR REPLACE INTO company VALUES(?,?,?,?,?)", (cik, now, "ok", len(found), None))
        conn.commit()
        counts["asked"] += 1
        counts["releases_stored"] += len(found)
    asked = conn.execute("SELECT COUNT(*) FROM company WHERE cik IN (%s)" % ",".join("?" * len(ciks)),
                         ciks).fetchone()[0] if ciks else 0
    return {"schema": "ecosystem.sec_results_releases.v1", "generated_at": datetime.now(timezone.utc).isoformat(),
            "budget": budget, "counts": counts, "seconds": round(time.perf_counter() - started, 1),
            "coverage_pct": round(100.0 * asked / len(ciks), 1) if ciks else None}


def load_releases(db: Path | None = None) -> dict[str, list[date]]:
    """CIK (string) -> results release dates, oldest first; {} without a store."""
    db = db or DB
    if not db.exists():
        return {}
    out: dict[str, set[date]] = {}
    with closing(sqlite3.connect(db)) as conn:
        for cik, filed in conn.execute("SELECT cik, filed FROM release"):
            try:
                out.setdefault(str(int(cik)), set()).add(date.fromisoformat(str(filed)[:10]))
            except (TypeError, ValueError):
                continue
    return {k: sorted(v) for k, v in out.items()}


def journal_ciks(db: Path | None = None) -> list[int]:
    db = db or JOURNAL
    if not db.exists():
        return []
    with closing(sqlite3.connect(db)) as conn:
        return sorted({int(c) for (c,) in conn.execute("SELECT DISTINCT cik FROM earnings_event") if str(c).isdigit()})


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--budget", type=int, default=DEFAULT_BUDGET)
    args = parser.parse_args(argv)
    from provider_rate_limiter import SharedProviderRateLimiter
    from research_workflow_orchestrator import load_env

    load_env(ROOT / ".env")  # SEC_USER_AGENT, never printed
    agent = os.getenv("SEC_USER_AGENT", "").strip()
    if not agent:
        print(json.dumps({"skipped": "no SEC user agent"}))
        return 0

    def fetch(url: str) -> dict[str, Any]:
        request = urllib.request.Request(url, headers={"User-Agent": agent, "Accept-Encoding": "identity"})
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read())

    DB.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(DB)) as conn:
        report = run(conn, journal_ciks(), fetch=fetch, today=date.today(), budget=args.budget,
                     limiter=SharedProviderRateLimiter())
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({"counts": report["counts"], "coverage_pct": report["coverage_pct"], "seconds": report["seconds"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
