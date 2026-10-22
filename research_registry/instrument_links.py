"""Link warrants, preferreds, units and rights to the company they were issued by.

These listings are information about a company, not separate companies: they belong
on the issuer's symbol card. A link is recorded only when it is established:

1. ``sec_cik`` - the SEC ticker file gives the instrument the same CIK as exactly one
   listed common-stock/ADR/SPAC listing (or several share classes of one issuer, in
   which case the ticker root picks the class, else the first ticker in sort order and
   the other classes are kept as alternatives);
2. ``ticker_and_issuer_name`` - no SEC identity for the instrument, but its ticker root
   (``ACGLN`` -> ``ACGL``, ``AAC.W`` -> ``AAC``, ``BML$G`` -> ``BML``) is a listed
   parent whose issuer name matches.

Everything else stays ``unresolved`` with the reason; nothing is guessed. The links are
derived data for one universe snapshot and are rebuilt from the same inputs.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sqlite3
from typing import Any

from .babis_catalog_v2 import normalize_cik
from .babis_sec_catalog import canonical_ticker
from .core import DEFAULT_DB, PROJECT, connect

METHOD_VERSION = "instrument-links-1"
SEC_TICKERS = PROJECT / "cache/sec_company_tickers_exchange.json"
LINKED_TYPES = frozenset({"warrant", "preferred", "unit", "right"})
PARENT_TYPES = frozenset({"equity_candidate", "adr_candidate", "spac"})
_SEPARATOR = re.compile(r"[.$\-\s/^+=]")
_INSTRUMENT_TAIL = re.compile(
    r"\b(?:units?|warrants?|rights?|redeemable|depositary|preferred|each|series|class|common|stock|shares?|"
    r"ordinary|representing|consisting|\d+(?:\.\d+)?%).*$", re.IGNORECASE)
_LEGAL_WORDS = frozenset({"inc", "incorporated", "corp", "corporation", "co", "company", "ltd", "limited",
                          "plc", "lp", "l", "p", "llc", "sa", "nv", "ag", "the"})


def schema(db: sqlite3.Connection) -> None:
    db.executescript("""
    CREATE TABLE IF NOT EXISTS instrument_parent_link(
        snapshot_id TEXT NOT NULL REFERENCES universe_snapshot,
        venue_code TEXT NOT NULL, ticker TEXT NOT NULL, instrument_type TEXT NOT NULL,
        status TEXT NOT NULL CHECK(status IN ('linked','unresolved')),
        parent_venue_code TEXT, parent_ticker TEXT, parent_cik TEXT,
        method TEXT, reason TEXT, evidence_json TEXT NOT NULL,
        method_version TEXT NOT NULL, recorded_at TEXT NOT NULL,
        PRIMARY KEY(snapshot_id,venue_code,ticker),
        CHECK((status='linked' AND parent_ticker IS NOT NULL AND method IS NOT NULL AND reason IS NULL)
              OR (status='unresolved' AND parent_ticker IS NULL AND method IS NULL AND reason IS NOT NULL)));
    CREATE INDEX IF NOT EXISTS instrument_parent_link_parent ON instrument_parent_link(snapshot_id,parent_ticker);
    """)


def ticker_roots(ticker: str) -> list[str]:
    """Candidate parent tickers: the part before a separator, else a dropped fifth-letter suffix."""
    text = str(ticker or "").upper()
    head = _SEPARATOR.split(text, 1)
    if len(head) > 1:
        return [head[0]] if head[0] else []
    return [text[:-1]] if len(text) >= 5 else []


def issuer_key(name: str) -> str:
    """Issuer part of a security name, without legal-form words, for comparing listings."""
    parts = re.split(r"\s+-\s+", str(name or ""), maxsplit=1)
    head = parts[0] if len(parts) > 1 else _INSTRUMENT_TAIL.sub("", parts[0])
    return " ".join(w for w in re.findall(r"[a-z0-9]+", head.lower()) if w not in _LEGAL_WORDS)


def sec_identities(payload: dict[str, Any]) -> tuple[dict[str, set[str]], dict[str, set[str]]]:
    exact: dict[str, set[str]] = defaultdict(set)
    canonical: dict[str, set[str]] = defaultdict(set)
    for values in payload.get("data") or []:
        row = dict(zip(payload["fields"], values))
        cik = normalize_cik(row.get("cik"))
        ticker = str(row.get("ticker") or "").upper()
        if cik and ticker:
            exact[ticker].add(cik)
            canonical[canonical_ticker(ticker)].add(cik)
    return exact, canonical


def listing_cik(listing: dict[str, Any], exact: dict[str, set[str]], canonical: dict[str, set[str]]) -> str | None:
    aliases = listing.get("directory_aliases") or [listing["ticker"]]
    for table, key in ((exact, str.upper), (canonical, canonical_ticker)):
        found = set().union(*(table.get(key(alias), set()) for alias in aliases))
        if found:
            return next(iter(found)) if len(found) == 1 else None
    return None


def link_instruments(listings: list[dict[str, Any]], exact: dict[str, set[str]],
                     canonical: dict[str, set[str]]) -> list[dict[str, Any]]:
    parents = [row for row in listings if row["instrument_type"] in PARENT_TYPES]
    cik_of = {(row["venue_code"], row["ticker"]): listing_cik(row, exact, canonical) for row in parents}
    by_cik: dict[str, list[dict[str, Any]]] = defaultdict(list)
    by_ticker: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in parents:
        cik = cik_of[(row["venue_code"], row["ticker"])]
        if cik:
            by_cik[cik].append(row)
        by_ticker[row["ticker"].upper()].append(row)
    links = []
    for row in sorted(listings, key=lambda r: (r["ticker"], r["venue_code"])):
        if row["instrument_type"] not in LINKED_TYPES:
            continue
        roots = ticker_roots(row["ticker"])
        cik = listing_cik(row, exact, canonical)
        evidence: dict[str, Any] = {"instrument_cik": cik, "ticker_roots": roots, "issuer_key": issuer_key(row["name"])}
        parent, method, reason = None, None, None
        if cik and by_cik.get(cik):
            group = sorted(by_cik[cik], key=lambda r: (r["ticker"], r["venue_code"]))
            parent = next((p for p in group if p["ticker"].upper() in roots), group[0])
            method = "sec_cik"
            if len(group) > 1:
                evidence["alternatives"] = [p["ticker"] for p in group if p is not parent]
        elif cik:
            reason = "no_listed_common_stock_with_same_cik"
        else:
            named = [p for root in roots for p in by_ticker.get(root, [])
                     if evidence["issuer_key"] and issuer_key(p["name"]) == evidence["issuer_key"]]
            if len(named) == 1:
                parent, method = named[0], "ticker_and_issuer_name"
            elif len(named) > 1:
                reason = "ambiguous_parent"
                evidence["candidates"] = [p["ticker"] for p in named]
            else:
                reason = "no_sec_identity_and_no_matching_listed_parent"
        links.append({
            "venue_code": row["venue_code"], "ticker": row["ticker"], "instrument_type": row["instrument_type"],
            "status": "linked" if parent else "unresolved",
            "parent_venue_code": parent["venue_code"] if parent else None,
            "parent_ticker": parent["ticker"] if parent else None,
            "parent_cik": cik_of[(parent["venue_code"], parent["ticker"])] if parent else None,
            "method": method, "reason": None if parent else reason, "evidence": evidence})
    return links


def _latest_snapshot(db: sqlite3.Connection) -> str:
    row = db.execute("SELECT snapshot_id FROM universe_snapshot ORDER BY retrieved_at DESC,rowid DESC LIMIT 1").fetchone()
    if row is None:
        raise ValueError("Run the universe build first")
    return row[0]


def effective_listings(db: sqlite3.Connection, snapshot: str) -> list[dict[str, Any]]:
    rows = []
    for payload_json, instrument_type in db.execute(
            "SELECT payload_json,instrument_type FROM effective_listed_symbol WHERE snapshot_id=?", (snapshot,)):
        payload = json.loads(payload_json)
        rows.append({"venue_code": payload["venue_code"], "ticker": payload["ticker"], "name": payload.get("name") or "",
                     "instrument_type": instrument_type, "directory_aliases": payload.get("directory_aliases") or []})
    return rows


def build(db: sqlite3.Connection, sec_tickers: Path = SEC_TICKERS, snapshot: str | None = None) -> dict[str, Any]:
    raw = sec_tickers.read_bytes()
    exact, canonical = sec_identities(json.loads(raw))
    snapshot = snapshot or _latest_snapshot(db)
    links = link_instruments(effective_listings(db, snapshot), exact, canonical)
    sec_sha = hashlib.sha256(raw).hexdigest()
    recorded = datetime.now(timezone.utc).isoformat()
    schema(db)
    with db:
        db.execute("DELETE FROM instrument_parent_link WHERE snapshot_id=?", (snapshot,))
        db.executemany("INSERT INTO instrument_parent_link VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)", [
            (snapshot, x["venue_code"], x["ticker"], x["instrument_type"], x["status"], x["parent_venue_code"],
             x["parent_ticker"], x["parent_cik"], x["method"], x["reason"],
             json.dumps({**x["evidence"], "sec_tickers_sha256": sec_sha}, ensure_ascii=False, sort_keys=True),
             METHOD_VERSION, recorded) for x in links])
    return summarize(links, snapshot)


def summarize(links: list[dict[str, Any]], snapshot: str) -> dict[str, Any]:
    return {"snapshot_id": snapshot, "method_version": METHOD_VERSION, "instruments": len(links),
            "linked": sum(x["status"] == "linked" for x in links),
            "by_method": dict(sorted(Counter(x["method"] for x in links if x["method"]).items())),
            "unresolved_by_reason": dict(sorted(Counter(x["reason"] for x in links if x["reason"]).items())),
            "by_type": {t: dict(Counter(x["status"] for x in links if x["instrument_type"] == t))
                        for t in sorted(LINKED_TYPES)}}


def links_for(db: sqlite3.Connection, ticker: str, snapshot: str | None = None) -> dict[str, Any]:
    """The instrument's own link (if it is one) and the instruments linked to it (if it is a parent)."""
    snapshot = snapshot or _latest_snapshot(db)
    ticker = ticker.upper()
    own = db.execute("SELECT * FROM instrument_parent_link WHERE snapshot_id=? AND ticker=?", (snapshot, ticker)).fetchone()
    children = db.execute(
        "SELECT k.ticker, k.venue_code, k.instrument_type, k.method, s.name FROM instrument_parent_link k "
        "LEFT JOIN listed_symbol s ON s.snapshot_id=k.snapshot_id AND s.venue_code=k.venue_code AND s.ticker=k.ticker "
        "WHERE k.snapshot_id=? AND k.parent_ticker=? ORDER BY k.instrument_type, k.ticker", (snapshot, ticker)).fetchall()
    link = None
    if own is not None:
        link = {k: own[k] for k in ("status", "instrument_type", "parent_ticker", "parent_venue_code", "method", "reason")}
        link["evidence"] = json.loads(own["evidence_json"])
    return {"link": link, "children": [dict(row) for row in children]}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    sub = parser.add_subparsers(dest="command", required=True)
    b = sub.add_parser("build", help="rebuild the links for the latest universe snapshot")
    b.add_argument("--sec-tickers", type=Path, default=SEC_TICKERS)
    s = sub.add_parser("show", help="show one symbol's link and linked instruments")
    s.add_argument("ticker")
    args = parser.parse_args(argv)
    db = connect(args.db)
    try:
        result = build(db, args.sec_tickers) if args.command == "build" else links_for(db, args.ticker)
    finally:
        db.close()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
