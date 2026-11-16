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

The better version (Cohen, Malloy and Pomorski 2012, "Decoding Inside Information"): most insider trades are routine,
the same calendar month every year (after earnings, under a 10b5-1 plan), and carry no news. At the start of each year
an insider who traded in each of the three years before is *routine* if some calendar month holds a trade in all three,
otherwise *opportunistic*; an insider without three years of history is not classified. Two more factors count only
the opportunistic ones:

* ``insider_opp_buyers_90d``: opportunistic insiders who bought in the last 90 days;
* ``insider_opp_sellers_90d``: opportunistic insiders who sold in the last 90 days (the literature's sign is negative).

They are unknown until three full calendar years before the day are covered.

A Form 4 is due within two business days, so inside the period the data sets cover, no filing means no trade: zero
buyers is a known zero. Outside that period (before the first quarter loaded, after the last one) the factor is
unknown, never zero. The net ratio is unknown when nobody traded (0 / 0 is not "neutral").

After the last published quarter, the daily reader (owner 2026-11-16) fills the days: the SEC daily form index
names every Form 4 of a day; each listed company's filing is read and its open-market trades kept in
``cache/sec_insider/daily/<day>.json``. A day is written only once all its filings were read, and the days after a
missing one are not used, so a gap is unknown and never "no trades". Weekends and holidays are recorded as such.

Research and shadow only: nothing here changes a score, a limit or an order.

    python insider_transactions.py sync [--start 2018q1]   # download the missing quarters (needs SEC_USER_AGENT)
    python insider_transactions.py build                   # parse them into cache/sec_insider/insider_index.json.gz
    python insider_transactions.py daily [--max-filings N] # read the days after the last quarter (needs SEC_USER_AGENT)
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
import re
import zipfile
from xml.etree import ElementTree

ROOT = Path(__file__).resolve().parent
DIRECTORY = ROOT / "cache" / "sec_insider"
INDEX = DIRECTORY / "insider_index.json.gz"
# the SEC moved the newer quarters (2026q2 on) to a second folder; both are tried
URLS = ("https://www.sec.gov/files/structureddata/data/insider-transactions-data-sets/{quarter}_form345.zip",
        "https://www.sec.gov/files/datastandardsinnovation/data/insider-transactions-data-sets/{quarter}_form345.zip")
DAILY = DIRECTORY / "daily"
DAILY_INDEX = "https://www.sec.gov/Archives/edgar/daily-index/{year}/QTR{quarter}/form.{day}.idx"
ARCHIVES = "https://www.sec.gov/Archives/"
DAILY_MAX_FILINGS = 4000  # per nightly call (~8 minutes at the SEC's pace); a longer backlog continues next night
LIVE_STALE_DAYS = 5  # a live tag needs the data to reach this close to today, else it is unknown
SCHEMA = "ecosystem.insider_index.v1"
FORMS = {"4", "4/A"}
CODES = {"P", "S"}
BUYERS_DAYS, NET_DAYS = 90, 180
HISTORY_YEARS = 3  # Cohen-Malloy-Pomorski: three years of trades decide routine or opportunistic
FIRST_QUARTER = "2018q1"  # the price store starts 2021-10; three full years before it classify the insiders

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
        data, error_name = None, "none"
        for url in URLS:
            try:
                data = fetch(url.format(quarter=name))
                zipfile.ZipFile(io.BytesIO(data)).testzip()
                break
            except Exception as exc:  # one missing quarter must not stop the others; it is listed
                data, error_name = None, type(exc).__name__
        if data is None:
            failed[name] = error_name
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


def classify(trades: list[list[Any]], year: int) -> dict[str, str]:
    """Routine or opportunistic for each insider with a trade in each of the three years before ``year``."""
    months: dict[str, dict[int, set[int]]] = {}
    for filed, owner, _code, _usd in trades:
        y = int(filed[:4])
        if year - HISTORY_YEARS <= y < year:
            months.setdefault(owner, {}).setdefault(y, set()).add(int(filed[5:7]))
    out = {}
    for owner, by_year in months.items():
        if len(by_year) < HISTORY_YEARS:
            continue  # not enough history: not classified, not counted as either
        common = set.intersection(*by_year.values())
        out[owner] = "routine" if common else "opportunistic"
    return out


def factors(trades: list[list[Any]] | None, as_of: date, coverage: list[str] | None) -> dict[str, Any]:
    """The insider factors on ``as_of`` from one issuer's trades (filed by that day). Unknown outside coverage."""
    unknown = {"insider_buyers_90d": None, "insider_net_buy_180d": None,
               "insider_opp_buyers_90d": None, "insider_opp_sellers_90d": None}
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
    out = {**unknown, "insider_buyers_90d": len(buyers),
           "insider_net_buy_180d": None if total == 0 else (bought - sold) / total}
    if date(as_of.year - HISTORY_YEARS, 1, 1) < first:
        return out  # three full years are not covered: the opportunistic factors stay unknown
    kinds = classify(trades or [], as_of.year)
    recent = [(owner, code) for filed, owner, code, _usd in trades or [] if buyers_from < filed <= day]
    out["insider_opp_buyers_90d"] = len({o for o, c in recent if c == "P" and kinds.get(o) == "opportunistic"})
    out["insider_opp_sellers_90d"] = len({o for o, c in recent if c == "S" and kinds.get(o) == "opportunistic"})
    return out


# ---------------------------------------------------------------- the days after the last quarter

StatusFetch = Callable[[str], tuple[int, bytes | None]]
_INDEX_ROW = re.compile(r"^(?P<form>\S+)\s+.*?\s+(?P<cik>\d{1,10})\s+(?P<date>\d{8}|\d{4}-\d{2}-\d{2})\s+"
                        r"(?P<file>edgar/\S+)\s*$")


def form4_filings(index_text: str) -> dict[str, set[int]]:
    """The Form 4 / 4/A files of one daily form index, each with the CIKs it is listed under (issuer and owners)."""
    out: dict[str, set[int]] = {}
    for line in index_text.splitlines():
        match = _INDEX_ROW.match(line)
        if match and match["form"] in FORMS:
            out.setdefault(match["file"], set()).add(int(match["cik"]))
    return out


def _text(node: Any, path: str) -> str:
    found = node.find(path)
    return "" if found is None or found.text is None else found.text.strip()


def parse_form4(document: str, filed: date) -> list[dict[str, Any]]:
    """Open-market trades of one Form 4 submission (the same rules as the quarterly data sets)."""
    start, end = document.find("<ownershipDocument"), document.find("</ownershipDocument>")
    if start < 0 or end < 0:
        raise ValueError("no ownershipDocument in the filing")
    root = ElementTree.fromstring(document[start:end + len("</ownershipDocument>")])
    if _text(root, "documentType") not in FORMS:
        return []
    cik, owner = _text(root, "issuer/issuerCik"), _text(root, "reportingOwner/reportingOwnerId/rptOwnerCik")
    if not cik.isdigit():
        raise ValueError("no issuer CIK")
    out = []
    for trade in root.iter("nonDerivativeTransaction"):
        code = _text(trade, "transactionCoding/transactionCode").upper()
        shares = _number(_text(trade, "transactionAmounts/transactionShares/value"))
        price = _number(_text(trade, "transactionAmounts/transactionPricePerShare/value"))
        if code in CODES and shares and price and shares > 0 and price > 0:
            out.append({"cik": int(cik), "filed": filed.isoformat(), "owner": owner.lstrip("0") or owner,
                        "code": code, "usd": round(shares * price, 2)})
    return out


RETRY_STATUS = {429, 500, 502, 503, 504}
RETRY_WAITS = (2.0, 10.0, 30.0)  # the SEC answers 503 now and then under load; a short wait usually clears it


def _get(fetch: StatusFetch, url: str, sleeper: Callable[[float], None]) -> tuple[int, bytes | None]:
    """One request, retried on a temporary refusal or a network error; the last answer is returned as it is."""
    for wait in (*RETRY_WAITS, None):
        try:
            status, body = fetch(url)
        except OSError:
            status, body = 0, None
        if status not in RETRY_STATUS and status != 0:
            return status, body
        if wait is None:
            return status, body
        sleeper(wait)
    return 0, None


def _business_days_after(day: date, today: date) -> int:
    return sum((day + timedelta(days=n)).weekday() < 5 for n in range(1, (today - day).days + 1))


def sync_daily(fetch: StatusFetch, *, start: date, today: date, listed: set[int] | None = None,
               directory: Path = DAILY, max_filings: int = DAILY_MAX_FILINGS, pause: float = 0.15,
               sleeper: Callable[[float], None] = time.sleep) -> dict[str, Any]:
    """Read the days from ``start`` to yesterday in order, stopping at the first day that cannot be completed."""
    directory.mkdir(parents=True, exist_ok=True)
    result: dict[str, Any] = {"read": [], "no_index": [], "filings": 0, "stopped": None}
    day = start
    while day < today:
        path = directory / f"{day.isoformat()}.json"
        if path.is_file():
            day += timedelta(days=1)
            continue
        url = DAILY_INDEX.format(year=day.year, quarter=(day.month - 1) // 3 + 1, day=day.strftime("%Y%m%d"))
        status, body = _get(fetch, url, sleeper)
        sleeper(pause)
        if status in (403, 404) and (day.weekday() >= 5 or _business_days_after(day, today) >= 2):
            path.write_text(json.dumps({"day": day.isoformat(), "status": "no_index", "trades": []}), encoding="utf-8")
            result["no_index"].append(day.isoformat())
            day += timedelta(days=1)
            continue
        if status != 200 or body is None:
            result["stopped"] = f"{day.isoformat()}: index http {status}"
            break
        files = [f for f, ciks in form4_filings(body.decode("latin-1")).items() if listed is None or ciks & listed]
        if result["filings"] and result["filings"] + len(files) > max_filings:
            result["stopped"] = f"{day.isoformat()}: the night's budget of {max_filings} filings is used"
            break
        trades: list[list[Any]] = []
        failed = None
        for name in files:
            status, body = _get(fetch, ARCHIVES + name, sleeper)
            sleeper(pause)
            if status != 200 or body is None:
                failed = f"{name}: http {status}"
                break
            try:
                trades += [[t["filed"], t["owner"], t["code"], t["usd"], t["cik"]]
                           for t in parse_form4(body.decode("utf-8", errors="replace"), day)]
            except (ValueError, ElementTree.ParseError):
                continue  # a filing without a readable ownership document has no trade to read; the day goes on
        result["filings"] += len(files)
        if failed:
            result["stopped"] = f"{day.isoformat()}: {failed}"
            break
        path.write_text(json.dumps({"day": day.isoformat(), "status": "ok", "filings": len(files), "trades": trades}),
                        encoding="utf-8")
        result["read"].append(day.isoformat())
        day += timedelta(days=1)
    return result


class InsiderSource:
    """The stored index, read once; ``trades(cik)`` feeds ``factors``."""

    def __init__(self, index_path: Path = INDEX) -> None:
        try:
            payload = json.loads(gzip.decompress(Path(index_path).read_bytes()))
        except (OSError, EOFError, ValueError):
            payload = {}
        self.coverage = payload.get("coverage")
        self.issuers = payload.get("issuers") or {}
        self.daily_days = self._add_daily(Path(index_path).parent / "daily")

    def _add_daily(self, directory: Path) -> int:
        """Extend coverage day by day with the daily files that follow the quarters without a gap."""
        if not self.coverage:
            return 0
        day, added = date.fromisoformat(self.coverage[1]) + timedelta(days=1), 0
        while True:
            try:
                payload = json.loads((directory / f"{day.isoformat()}.json").read_text(encoding="utf-8"))
            except (OSError, ValueError):
                break
            for filed, owner, code, usd, cik in payload.get("trades") or []:
                self.issuers.setdefault(str(int(cik)), []).append([filed, owner, code, usd])
            self.coverage = [self.coverage[0], day.isoformat()]
            day, added = day + timedelta(days=1), added + 1
        return added

    def trades(self, cik: int | str) -> list[list[Any]]:
        return self.issuers.get(str(int(cik)), [])

    def factors(self, cik: int | str | None, as_of: date) -> dict[str, Any]:
        if cik is None:
            return factors(None, as_of, None)
        return factors(self.trades(cik), as_of, self.coverage)


def annotate(rows: list[dict[str, Any]], source: InsiderSource | None = None, mapping: dict[str, Any] | None = None,
             today: date | None = None) -> int:
    """Shadow: add ``insider_factors`` to each Giannis row, as of the last covered day; unknown when that is stale."""
    if mapping is None:
        import periodic_filing
        mapping = periodic_filing.ticker_to_cik(periodic_filing.DEFAULT_TICKERS)
    source = source or InsiderSource()
    today = today or date.today()
    last = date.fromisoformat(source.coverage[1]) if source.coverage else None
    fresh = last is not None and (today - last).days <= LIVE_STALE_DAYS
    known = 0
    for row in rows:
        cik = mapping.get(str(row.get("ticker") or "").upper())
        values = source.factors(cik, last) if fresh and cik else factors(None, today, None)
        row["insider_factors"] = {"as_of": last.isoformat() if fresh else None, **values}
        known += values.get("insider_opp_buyers_90d") is not None
    return known


def _listed_ciks() -> set[int] | None:
    import periodic_filing

    mapping = periodic_filing.ticker_to_cik(periodic_filing.DEFAULT_TICKERS)
    return {int(c) for c in mapping.values() if str(c).isdigit()} or None


def _counts(result: dict[str, Any]) -> dict[str, Any]:
    return {k: (len(v) if isinstance(v, list) else v) for k, v in result.items()}


def sync_nightly(user_agent: str, today: date) -> dict[str, Any]:
    """The nightly step: new quarters (rebuilt into the index), then the days after them, within the night's budget."""
    from research_registry import sec_filing_index

    quarterly = sync(default_fetch(user_agent), quarters(FIRST_QUARTER, today))
    built = build() if quarterly["added"] or not INDEX.is_file() else None
    source = InsiderSource()
    if not source.coverage:
        return {"quarterly": _counts(quarterly), "built": built, "daily": None}
    daily = sync_daily(sec_filing_index.default_fetch(user_agent),
                       start=date.fromisoformat(source.coverage[1]) + timedelta(days=1), today=today,
                       listed=_listed_ciks())
    return {"quarterly": _counts(quarterly), "built": built, "daily": _counts(daily)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    sy = sub.add_parser("sync", help="download the missing quarterly data sets from the SEC")
    sy.add_argument("--start", default=FIRST_QUARTER)
    sub.add_parser("build", help="parse the stored quarters into the index")
    dy = sub.add_parser("daily", help="read the days after the last quarter (SEC daily index + each Form 4)")
    dy.add_argument("--max-filings", type=int, default=DAILY_MAX_FILINGS)
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
    elif args.command == "daily":
        from research_registry import sec_filing_index
        from research_workflow_orchestrator import load_env
        load_env(ROOT / ".env")  # SEC_USER_AGENT, never printed
        agent = os.getenv("SEC_USER_AGENT", "").strip()
        if not agent:
            raise SystemExit("SEC_USER_AGENT is not configured")
        source = InsiderSource()
        if not source.coverage:
            raise SystemExit("no quarters built yet: run sync and build first")
        result = _counts(sync_daily(sec_filing_index.default_fetch(agent),
                                    start=date.fromisoformat(source.coverage[1]) + timedelta(days=1),
                                    today=sec_filing_index.sec_today(), listed=_listed_ciks(),
                                    max_filings=args.max_filings))
    else:
        source = InsiderSource()
        result = {"stored": sorted(p.name[:6] for p in DIRECTORY.glob("*_form345.zip")), "coverage": source.coverage,
                  "daily_days": source.daily_days, "issuers": len(source.issuers)}
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
