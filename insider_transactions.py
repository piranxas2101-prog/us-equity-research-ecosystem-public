"""Insider buying and selling from Form 4 (owner approved 2026-11-16; docs/FUNDAMENTAL_FACTORS_PLAN.md phase 4).

Source: the SEC's quarterly "Insider Transactions Data Sets" (public domain, one zip per quarter with every Form 3/4/5
as tab-separated tables). One zip a quarter instead of one request per filing, read with the standard library only.

Only open-market trades count, the ones the literature finds informative (Lakonishok-Lee 2001, Cohen-Malloy-Pomorski
2012): transaction code ``P`` (purchase) and ``S`` (sale) on non-derivative securities of Forms 4 and 4/A. Grants,
option exercises, gifts and tax withholdings are not decisions to trade and stay out.

Two factors per company and day, from the filings *filed* by that day (point in time):

* ``insider_buyers_90d``: how many different insiders bought on the open market in the last 90 days;
* ``insider_net_buy_180d``: (buys - sells) / (buys + sells) over 180 days, counting each insider's filing once per
  side (Lakonishok-Lee's net purchase ratio by count), from -1 to +1. Counts, not dollars: a few hundred lines in the
  data sets carry impossible prices or sizes (up to 10^15 dollars), and one of them would decide a dollar ratio.

A Form 4 is due within two business days, so inside the period the data sets cover, no filing means no trade: zero
buyers is a known zero. Outside that period (before the first quarter loaded, after the last one) the factor is
unknown, never zero. The net ratio is unknown when nobody traded (0 / 0 is not "neutral").

Research and shadow only: nothing here changes a score, a limit or an order.

    python insider_transactions.py sync [--start 2021q3]   # download the missing quarters (needs SEC_USER_AGENT)
    python insider_transactions.py build                   # parse them into cache/sec_insider/insider_index.json.gz
    python insider_transactions.py status
"""
from __future__ import annotations

import argparse
import csv
from datetime import date, datetime, timedelta
import gzip
import io
import json
import os
from pathlib import Path
import time
from typing import Any, Callable, Iterable
import zipfile

ROOT = Path(__file__).resolve().parent
DIRECTORY = ROOT / "cache" / "sec_insider"
INDEX = DIRECTORY / "insider_index.json.gz"
URL = "https://www.sec.gov/files/structureddata/data/insider-transactions-data-sets/{quarter}_form345.zip"
SCHEMA = "ecosystem.insider_index.v1"
FORMS = {"4", "4/A"}
CODES = {"P", "S"}
BUYERS_DAYS, NET_DAYS = 90, 180
FIRST_QUARTER = "2021q3"  # the price store starts 2021-10; 180 days of history before it

Fetch = Callable[[str], bytes]


def quarter_of(day: date) -> str:
    return f"{day.year}q{(day.month - 1) // 3 + 1}"


def quarter_bounds(name: str) -> tuple[date, date]:
    year, q = int(name[:4]), int(name[-1])
    start = date(year, 3 * q - 2, 1)
    end = date(year + (q == 4), 1 if q == 4 else 3 * q + 1, 1) - timedelta(days=1)
    return start, end


def quarters(first: str, today: date) -> list[str]:
    """Every finished quarter from ``first`` to the last one before today's (the current one is not published)."""
    out, start = [], quarter_bounds(first)[0]
    while quarter_bounds(quarter_of(start))[1] < today:
        out.append(quarter_of(start))
        start = quarter_bounds(quarter_of(start))[1] + timedelta(days=1)
    return out


def _date(text: str) -> date | None:
    text = (text or "").strip()
    for fmt in ("%d-%b-%Y", "%Y-%m-%d", "%m/%d/%Y"):
        try:
            return datetime.strptime(text.title() if "-" in text and fmt == "%d-%b-%Y" else text, fmt).date()
        except ValueError:
            continue
    return None


def _number(text: str) -> float | None:
    try:
        value = float((text or "").replace(",", ""))
    except ValueError:
        return None
    return value if value == value else None


def _table(archive: zipfile.ZipFile, name: str) -> Iterable[dict[str, str]]:
    member = next((m for m in archive.namelist() if m.upper().endswith(name)), None)
    if member is None:
        raise ValueError(f"{name} is missing from the data set")
    with archive.open(member) as handle:
        yield from csv.DictReader(io.TextIOWrapper(handle, encoding="utf-8", errors="replace"), delimiter="\t",
                                  quoting=csv.QUOTE_NONE)


def parse_quarter(data: bytes) -> list[dict[str, Any]]:
    """Open-market trades of one quarter's data set: issuer, filing date, insider, code, dollars."""
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        filings = {}
        for row in _table(archive, "SUBMISSION.TSV"):
            if (row.get("DOCUMENT_TYPE") or "").strip() in FORMS:
                filed, cik = _date(row.get("FILING_DATE", "")), (row.get("ISSUERCIK") or "").strip()
                if filed and cik.isdigit():
                    filings[row["ACCESSION_NUMBER"]] = (int(cik), filed)
        owners: dict[str, str] = {}
        for row in _table(archive, "REPORTINGOWNER.TSV"):
            owners.setdefault(row.get("ACCESSION_NUMBER", ""), (row.get("RPTOWNERCIK") or "").strip())
        out = []
        for row in _table(archive, "NONDERIV_TRANS.TSV"):
            filing = filings.get(row.get("ACCESSION_NUMBER", ""))
            code = (row.get("TRANS_CODE") or "").strip().upper()
            if filing is None or code not in CODES:
                continue
            shares, price = _number(row.get("TRANS_SHARES", "")), _number(row.get("TRANS_PRICEPERSHARE", ""))
            if not shares or not price or shares <= 0 or price <= 0:
                continue  # a trade without a size or price cannot be weighed; it is not read as zero dollars
            out.append({"cik": filing[0], "filed": filing[1].isoformat(),
                        "owner": owners.get(row["ACCESSION_NUMBER"]) or row["ACCESSION_NUMBER"],
                        "code": code, "usd": round(shares * price, 2)})
        return out


def default_fetch(user_agent: str, timeout: int = 120) -> Fetch:
    from urllib import request

    def fetch(url: str) -> bytes:
        req = request.Request(url, headers={"User-Agent": user_agent, "Accept-Encoding": "identity"})
        with request.urlopen(req, timeout=timeout) as response:
            return response.read()
    return fetch


def sync(fetch: Fetch, names: list[str], directory: Path = DIRECTORY, pause: float = 1.0,
         sleeper: Callable[[float], None] = time.sleep) -> dict[str, Any]:
    """Download the quarters not stored yet (a stored quarter never changes). A zip that does not open is not kept."""
    directory.mkdir(parents=True, exist_ok=True)
    added, failed = [], {}
    for name in names:
        path = directory / f"{name}_form345.zip"
        if path.is_file():
            continue
        try:
            data = fetch(URL.format(quarter=name))
            zipfile.ZipFile(io.BytesIO(data)).testzip()
        except Exception as exc:  # one missing quarter must not stop the others; it is listed
            failed[name] = type(exc).__name__
            continue
        path.write_bytes(data)
        added.append(name)
        sleeper(pause)
    return {"added": added, "failed": failed}


def build(directory: Path = DIRECTORY, index_path: Path = INDEX) -> dict[str, Any]:
    """Parse every stored quarter into one index. Coverage is the unbroken run of quarters from the first stored."""
    stored = sorted(p.name[:6] for p in directory.glob("*_form345.zip"))
    loaded: list[str] = []
    issuers: dict[str, list[list[Any]]] = {}
    for name in stored:
        if loaded and quarter_of(quarter_bounds(loaded[-1])[1] + timedelta(days=1)) != name:
            break  # a gap: what follows it would be read as "no trades" across the hole
        for trade in parse_quarter((directory / f"{name}_form345.zip").read_bytes()):
            issuers.setdefault(str(trade["cik"]), []).append([trade["filed"], trade["owner"], trade["code"], trade["usd"]])
        loaded.append(name)
    for rows in issuers.values():
        rows.sort()
    payload = {"schema": SCHEMA, "built_at": datetime.now().isoformat(timespec="seconds"), "quarters": loaded,
               "coverage": None if not loaded else [quarter_bounds(loaded[0])[0].isoformat(),
                                                   quarter_bounds(loaded[-1])[1].isoformat()],
               "issuers": issuers}
    index_path.parent.mkdir(parents=True, exist_ok=True)
    index_path.write_bytes(gzip.compress(json.dumps(payload, separators=(",", ":")).encode("utf-8")))
    return {"quarters": len(loaded), "skipped_after_gap": len(stored) - len(loaded), "issuers": len(issuers),
            "trades": sum(len(v) for v in issuers.values()), "coverage": payload["coverage"]}


def factors(trades: list[list[Any]] | None, as_of: date, coverage: list[str] | None) -> dict[str, Any]:
    """The two insider factors on ``as_of`` from one issuer's trades (filed by that day). Unknown outside coverage."""
    unknown = {"insider_buyers_90d": None, "insider_net_buy_180d": None}
    if not coverage:
        return unknown
    first, last = date.fromisoformat(coverage[0]), date.fromisoformat(coverage[1])
    if as_of - timedelta(days=NET_DAYS) < first or as_of > last:
        return unknown
    day, buyers_from, net_from = as_of.isoformat(), (as_of - timedelta(days=BUYERS_DAYS)).isoformat(), \
        (as_of - timedelta(days=NET_DAYS)).isoformat()
    buyers: set[str] = set()
    events: dict[str, set[tuple[str, str]]] = {"P": set(), "S": set()}
    for filed, owner, code, _usd in trades or []:
        if filed > day or filed <= net_from:
            continue
        events[code].add((owner, filed))  # one filing's several lines count once
        if code == "P" and filed > buyers_from:
            buyers.add(owner)
    bought, sold = len(events["P"]), len(events["S"])
    total = bought + sold
    return {"insider_buyers_90d": len(buyers), "insider_net_buy_180d": None if total == 0 else (bought - sold) / total}


class InsiderSource:
    """The stored index, read once; ``trades(cik)`` feeds ``factors``."""

    def __init__(self, index_path: Path = INDEX) -> None:
        try:
            payload = json.loads(gzip.decompress(Path(index_path).read_bytes()))
        except (OSError, EOFError, ValueError):
            payload = {}
        self.coverage = payload.get("coverage")
        self.issuers = payload.get("issuers") or {}

    def trades(self, cik: int | str) -> list[list[Any]]:
        return self.issuers.get(str(int(cik)), [])

    def factors(self, cik: int | str | None, as_of: date) -> dict[str, Any]:
        if cik is None:
            return factors(None, as_of, None)
        return factors(self.trades(cik), as_of, self.coverage)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    sy = sub.add_parser("sync", help="download the missing quarterly data sets from the SEC")
    sy.add_argument("--start", default=FIRST_QUARTER)
    sub.add_parser("build", help="parse the stored quarters into the index")
    sub.add_parser("status", help="what is stored and what the index covers")
    args = parser.parse_args(argv)
    if args.command == "sync":
        from research_workflow_orchestrator import load_env
        load_env(ROOT / ".env")  # SEC_USER_AGENT, never printed
        agent = os.getenv("SEC_USER_AGENT", "").strip()
        if not agent:
            raise SystemExit("SEC_USER_AGENT is not configured")
        result: dict[str, Any] = sync(default_fetch(agent), quarters(args.start, date.today()))
    elif args.command == "build":
        result = build()
    else:
        source = InsiderSource()
        result = {"stored": sorted(p.name[:6] for p in DIRECTORY.glob("*_form345.zip")), "coverage": source.coverage,
                  "issuers": len(source.issuers)}
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
