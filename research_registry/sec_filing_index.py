"""Daily SEC filing calendar: which companies filed what, one small request per day.

The SEC publishes, for every business day, a form index of all filings accepted
that day (``edgar/daily-index/YYYY/QTRn/form.YYYYMMDD.idx``). Reading it once per day
tells the ecosystem which CIKs filed a financial report or a merger document, so
evidence that only changes with a filing can be reused on the other days.

Coverage is explicit. A day is ``ok`` (index read) or ``no_index`` (the SEC has no
index for a past day: weekend or market holiday). A day that could not be read is
not recorded at all, and ``filed_since`` answers ``None`` (unknown) for any window
that contains such a day, so a caller can never mistake a gap for "nothing filed".
"""
from __future__ import annotations

import argparse
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import time
from typing import Any, Callable
from urllib import error, request

from .core import DEFAULT_DB, PROJECT, connect

INDEX_URL = "https://www.sec.gov/Archives/edgar/daily-index/{year}/QTR{quarter}/form.{day}.idx"
FINANCIAL_FORMS = frozenset({"10-K", "10-K/A", "10-Q", "10-Q/A", "20-F", "20-F/A", "40-F", "40-F/A",
                             "6-K", "6-K/A", "8-K", "8-K/A", "10-KT", "10-QT"})
MERGER_FORMS = frozenset({"S-4", "S-4/A", "F-4", "F-4/A", "DEFM14A", "PREM14A", "DEFM14C", "PREM14C", "425"})
DEFAULT_LOOKBACK_DAYS = 45
# A weekday without an index is a market holiday only once this many business days
# have passed since it; before that it may simply not be published yet.
SETTLED_AFTER_DAYS = 2
_ROW = re.compile(r"^(?P<head>.*?)\s+(?P<cik>\d{1,10})\s+(?P<date>\d{8}|\d{4}-\d{2}-\d{2})\s+(?P<file>edgar/\S+)\s*$")

# fetch(url) -> (http_status, body bytes or None); network failures raise OSError.
Fetch = Callable[[str], tuple[int, bytes | None]]


def schema(db: sqlite3.Connection) -> None:
    db.executescript("""
    CREATE TABLE IF NOT EXISTS sec_filing_index_day(
        day TEXT PRIMARY KEY, status TEXT NOT NULL CHECK(status IN ('ok','no_index')),
        url TEXT NOT NULL, sha256 TEXT, relevant_filings INTEGER NOT NULL, fetched_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS sec_filing_event(
        accession TEXT NOT NULL, cik TEXT NOT NULL, form TEXT NOT NULL, filed_date TEXT NOT NULL,
        category TEXT NOT NULL CHECK(category IN ('financial','merger')), company_name TEXT NOT NULL,
        index_day TEXT NOT NULL REFERENCES sec_filing_index_day(day),
        PRIMARY KEY(accession, cik, form));
    CREATE INDEX IF NOT EXISTS sec_filing_event_cik ON sec_filing_event(cik, filed_date);
    """)


def index_url(day: date) -> str:
    return INDEX_URL.format(year=day.year, quarter=(day.month - 1) // 3 + 1, day=day.strftime("%Y%m%d"))


def parse_form_index(text: str) -> list[dict[str, str]]:
    """Relevant rows of one form.idx file.

    Rows are ``form  company  cik  date  file`` separated by runs of spaces. The header
    wraps over two lines and does not line up with the rows, so the rows are read
    from the right (file, date, CIK) and the form is split from the company at the
    first run of two or more spaces.
    """
    lines = text.splitlines()
    rule = next((i for i, line in enumerate(lines) if line.startswith("---")), None)
    if rule is None or not any("Form Type" in line for line in lines[max(0, rule - 3):rule]):
        raise ValueError("Not an SEC form index")
    rows = []
    for line in lines[rule + 1:]:
        match = _ROW.match(line)
        if not match:
            continue
        form, _, company = match["head"].strip().partition("  ")
        form = form.strip().upper()
        category = "financial" if form in FINANCIAL_FORMS else "merger" if form in MERGER_FORMS else None
        if category is None:
            continue
        filed = match["date"].replace("-", "")
        rows.append({"form": form, "category": category, "cik": str(int(match["cik"])),
                     "company_name": company.strip(),
                     "filed_date": f"{filed[:4]}-{filed[4:6]}-{filed[6:]}",
                     "accession": Path(match["file"]).stem})
    return rows


def business_days_after(day: date, today: date) -> int:
    return sum((day + timedelta(days=n)).weekday() < 5 for n in range(1, (today - day).days + 1))


def default_fetch(user_agent: str, timeout: int = 30) -> Fetch:
    def fetch(url: str) -> tuple[int, bytes | None]:
        req = request.Request(url, headers={"User-Agent": user_agent, "Accept-Encoding": "identity"})
        try:
            with request.urlopen(req, timeout=timeout) as response:
                return int(response.status), response.read()
        except error.HTTPError as exc:
            return int(exc.code), None
    return fetch


def sync(db: sqlite3.Connection, fetch: Fetch, *, today: date, lookback_days: int = DEFAULT_LOOKBACK_DAYS,
         pause_seconds: float = 0.2, sleeper: Callable[[float], None] = time.sleep) -> dict[str, Any]:
    """Read every missing day of the window [today - lookback, today]; never re-read a recorded day."""
    schema(db)
    known = {row[0] for row in db.execute("SELECT day FROM sec_filing_index_day")}
    days = [today - timedelta(days=offset) for offset in range(lookback_days, -1, -1)]
    result: dict[str, Any] = {"read": [], "no_index": [], "not_yet_published": [], "failed": [], "events_added": 0}
    for day in days:
        key = day.isoformat()
        if key in known:
            continue
        url = index_url(day)
        try:
            status, body = fetch(url)
        except OSError as exc:
            result["failed"].append({"day": key, "error": type(exc).__name__})
            continue
        finally:
            sleeper(pause_seconds)
        now = datetime.now(timezone.utc).isoformat()
        if status == 200 and body is not None:
            try:
                rows = parse_form_index(body.decode("latin-1"))
            except ValueError:
                result["failed"].append({"day": key, "error": "unparseable_index"})
                continue
            with db:
                db.execute("INSERT INTO sec_filing_index_day VALUES(?,?,?,?,?,?)",
                           (key, "ok", url, hashlib.sha256(body).hexdigest(), len(rows), now))
                before = db.total_changes
                db.executemany("INSERT OR IGNORE INTO sec_filing_event VALUES(?,?,?,?,?,?,?)",
                               [(r["accession"], r["cik"], r["form"], r["filed_date"], r["category"],
                                 r["company_name"], key) for r in rows])
                result["events_added"] += db.total_changes - before
            result["read"].append(key)
        elif status in (403, 404) and (day.weekday() >= 5 or business_days_after(day, today) >= SETTLED_AFTER_DAYS):
            # No index for a weekend, or for a weekday long past (a market holiday).
            with db:
                db.execute("INSERT INTO sec_filing_index_day VALUES(?,?,?,?,?,?)", (key, "no_index", url, None, 0, now))
            result["no_index"].append(key)
        elif status in (403, 404):
            # A recent weekday: the index may simply not be published yet. Not recorded,
            # so the day stays uncovered and is read again by the next sync.
            result["not_yet_published"].append(key)
        else:
            result["failed"].append({"day": key, "http_status": status})
    return result


def filed_since(db: sqlite3.Connection, cik: str | int, since: date, *, until: date,
                category: str | None = "financial") -> list[dict[str, Any]] | None:
    """Filings of ``cik`` with since < filed_date <= until, or None when a day in that window is not covered."""
    if until <= since:
        return []
    days = {(since + timedelta(days=n)).isoformat() for n in range(1, (until - since).days + 1)}
    covered = {row[0] for row in db.execute(
        "SELECT day FROM sec_filing_index_day WHERE day>? AND day<=?", (since.isoformat(), until.isoformat()))}
    if days - covered:
        return None
    sql = ("SELECT accession, form, filed_date, category FROM sec_filing_event "
           "WHERE cik=? AND filed_date>? AND filed_date<=?")
    params: list[Any] = [str(int(cik)), since.isoformat(), until.isoformat()]
    if category:
        sql += " AND category=?"
        params.append(category)
    return [dict(zip(("accession", "form", "filed_date", "category"), row))
            for row in db.execute(sql + " ORDER BY filed_date, accession", params)]


def sec_today() -> date:
    """Today's date where the SEC dates filings (US Eastern), not on this machine's clock."""
    from zoneinfo import ZoneInfo
    return datetime.now(ZoneInfo("America/New_York")).date()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    sub = parser.add_subparsers(dest="command", required=True)
    s = sub.add_parser("sync", help="read the missing days of the SEC daily form index")
    s.add_argument("--lookback-days", type=int, default=DEFAULT_LOOKBACK_DAYS)
    q = sub.add_parser("show", help="filings of one CIK since a date")
    q.add_argument("cik")
    q.add_argument("--since", required=True)
    args = parser.parse_args(argv)
    db = connect(args.db)
    try:
        if args.command == "sync":
            from research_workflow_orchestrator import load_env
            load_env(PROJECT / ".env")  # sets SEC_USER_AGENT without printing anything
            agent = os.getenv("SEC_USER_AGENT", "").strip()
            if not agent:
                raise SystemExit("SEC_USER_AGENT is not configured")
            result = sync(db, default_fetch(agent), today=sec_today(), lookback_days=args.lookback_days)
        else:
            schema(db)
            result = filed_since(db, args.cik, date.fromisoformat(args.since), until=sec_today(), category=None)
    finally:
        db.close()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
