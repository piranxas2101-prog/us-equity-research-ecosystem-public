"""Press-release reader: the company's own results the day it announces them (8-K item 2.02, exhibit 99.1).

Owner's decision 2026-10-22: take the financial results from the company itself, as soon as they are out. The
10-Q with XBRL follows days or weeks later; the press release is filed with the SEC the same hour as the
announcement. Every night, for every listed company (``research_universe.py``) with a new 8-K - or 6-K, the
foreign filers' current report (owner 2026-10-22, the LAES case) - in the SEC daily index:

1. the filing's index page gives its items and documents; only 8-Ks with item 2.02 (results of operations) and an
   EX-99.1 exhibit go on;
2. the exhibit's tables are read deterministically (no LLM): total revenue, net income (loss) and diluted EPS of
   the new quarter. Press releases lay their columns out in many ways (current year first, prior year first, or a
   row of quarters like Netflix's letter), so a figure is accepted only when it is anchored: one cell of the row
   must equal, within 0.5% and at some scale, the same quarter a year earlier that the SEC already holds
   (companyfacts); the new figure is then the cell in the current-period position (the next column for a table
   printing the prior year first, the previous column for current-year-first tables, four columns on in a row of
   quarters). No anchor, no figure: unknown, never a guess;
   A foreign filer reporting only once a year in XBRL (LAES) has no prior-year half or quarter to anchor on: its 6-K
   tables are then read from the column years in the header (current year against the prior one), the same
   plausibility bounds apply, and the figures are marked ``anchored: false`` (tag ``pr_verified = no``);
3. the event joins the results journal (``earnings_journal.py``) with the filing day as the announcement day and
   tags ``pr_revenue_growth``, ``pr_profit`` and ``pr_eps``: the journal's tests pick them up by themselves.

Every number is taken from the exhibit's own text and stored with the table row it came from. These are
"press release" figures, not audited; the 10-Q confirms them later. At most ``MAX_FILINGS`` SEC pages per night
under the shared SEC rate limit; a backlog drains over the following nights.
"""
from __future__ import annotations

import argparse
from contextlib import closing
from datetime import date, datetime, timedelta, timezone
import gzip
import json
import os
from pathlib import Path
import re
import sqlite3
from typing import Any, Callable

ROOT = Path(__file__).resolve().parent
MAX_FILINGS = 400
LOOKBACK_DAYS = 10
TOLERANCE = 0.005
REVENUE = re.compile(r"^(total\s+)?(net\s+)?(revenues?|sales|net sales|total net sales)(\s*,\s*net)?\s*$", re.I)
NET_INCOME = re.compile(r"^net\s+(income|loss|income\s*\(loss\)|\(loss\)\s*income|earnings)"
                        r"(\s+attributable to [\w\s.,&'-]+)?\s*$", re.I)
EPS_DILUTED = re.compile(r"^(diluted|diluted (net )?(income|earnings|loss)( \(loss\))? per (common )?share"
                         r"|(net )?(income|earnings)( \(loss\))? per share[ -]+diluted|diluted eps)\s*:?\s*$", re.I)
YEAR = re.compile(r"\b(20\d\d)\b")
QUARTER_TOKEN = re.compile(r"\bQ[1-4]\b|\b[1-4]Q\b", re.I)
NUMBER = re.compile(r"^\(?-?\(?([\d,]+(?:\.\d+)?)\)?$")
EPS_CONCEPTS = ("EarningsPerShareDiluted", "DilutedEarningsLossPerShare")

Fetch = Callable[[str], str]


def to_number(cell: str) -> float | None:
    text = cell.replace("—", "").replace("–", "").replace("$", "").replace("\xa0", " ").replace(" ", "")
    if not text or text == "-":
        return None
    match = NUMBER.match(text)
    if not match:
        return None
    value = float(match.group(1).replace(",", ""))
    return -value if text.startswith("(") or text.startswith("-") else value


def table_rows(table: Any) -> list[list[str]]:
    rows = []
    for tr in table.find_all("tr"):
        merged: list[str] = []
        for cell in (td.get_text(" ", strip=True) for td in tr.find_all(["td", "th"])):
            if merged and cell in (")", "%"):  # "$" and ")" are often cells of their own
                merged[-1] += cell
            elif cell not in ("$", ""):
                merged.append(cell)
        if merged:
            rows.append(merged)
    return rows


def layout(rows: list[list[str]]) -> str:
    header = " ".join(" ".join(r) for r in rows[:4])
    if len(QUARTER_TOKEN.findall(header)) >= 3:
        return "quarter_row"
    years = [int(y) for y in YEAR.findall(header)]
    return "prior_first" if len(years) >= 2 and years[0] < years[1] else "current_first"


def _same(a: float, b: float | None, eps: bool) -> bool:
    return b is not None and abs(a - b) <= TOLERANCE * abs(b) + (0.005 if eps else 0.0)


def anchored(values: list[float], prior: float, kind: str, eps: bool, last: float | None = None
             ) -> tuple[float, float, int, int] | None:
    """(current, scale, prior column, current column) when one cell equals the known prior-year quarter. The new
    quarter is looked for where the layout puts it; a cell equal to the latest quarter the SEC already holds (a
    "previous quarter" column, as in Intuitive Surgical's release) is never taken for the new one."""
    for factor in ((1.0,) if eps else (1.0, 1e3, 1e6, 1e9)):
        for j, value in enumerate(values):
            if not prior or not _same(value * factor, prior, eps):
                continue
            # year-to-date columns sit to the right of the quarter's pair: never look there for the new quarter
            order = {"quarter_row": [j + 4, j - 4, 0], "prior_first": [j + 1]}.get(kind, [j - 1, 0])
            for k in order:
                if 0 <= k < len(values) and k != j and not _same(values[k] * factor, last, eps):
                    return values[k] * factor, factor, j, k
    return None


# How far a new quarter may sit from the same quarter a year earlier before the cell is taken for another column
# (year to date, a segment, a per-share figure): revenue rarely falls by two thirds or triples in a year (LAES grew
# 131% in H1 2026, so the bound is wider than doubling); diluted EPS is checked
# only when last year's was not near zero; net income may swing up to tenfold either way.
BOUNDS = {"revenue": (0.33, 3.0), "eps_diluted": (0.2, 5.0), "net_income": (0.1, 10.0)}


def plausible(name: str, current: float, prior: float) -> bool:
    if current == prior:
        return False  # the prior-year cell itself, repeated in another column
    if name == "eps_diluted" and abs(prior) < 0.05:
        return True
    low, high = BOUNDS[name]
    return low <= abs(current) / abs(prior) <= high


def read_tables(html: str, anchors: dict[str, float]) -> dict[str, Any]:
    """Revenue, net income and diluted EPS of the new quarter, each anchored on the prior-year quarter the SEC
    already holds (``anchors``: name -> value). Without an anchor nothing is read."""
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "lxml")
    found: dict[str, Any] = {}
    for table in soup.find_all("table"):
        rows = table_rows(table)
        if len(rows) < 3:
            continue
        kind = layout(rows)
        for row in rows:
            label, values = row[0], [v for v in (to_number(c) for c in row[1:]) if v is not None]
            if len(values) < 2:
                continue
            for name, pattern in (("revenue", REVENUE), ("net_income", NET_INCOME), ("eps_diluted", EPS_DILUTED)):
                prior = anchors.get(name)
                if name in found or prior is None or not pattern.match(label):
                    continue
                hit = anchored(values, prior, kind, name == "eps_diluted", anchors.get(f"{name}_last"))
                if hit is not None and not plausible(name, hit[0], prior):
                    hit = None  # another column (year to date, a segment, per share): unknown, not wrong
                if hit is not None:
                    current, factor, j, k = hit
                    found[name] = {"current": current, "prior": prior, "row": " | ".join(row)[:300],
                                   "scale": factor, "layout": kind, "prior_column": j, "current_column": k}
    if found.get("revenue") and found["revenue"]["current"] <= 0:
        found.pop("revenue")
    income, eps = found.get("net_income"), found.get("eps_diluted")
    if income and eps and (income["current"] - income["prior"]) * (eps["current"] - eps["prior"]) < 0             and abs(income["current"] / income["prior"] - 1) > 0.05:
        found.pop("eps_diluted")  # earnings and earnings per share moving opposite ways: one cell is wrong
    return found


def read_unanchored(html: str) -> dict[str, Any]:
    """Revenue and net income of a 6-K whose company has no interim XBRL to anchor on: two columns ordered by the
    years in the table's header (current, prior), plausibility bounds, marked unverified. Scale-free: only the
    growth and the sign matter for the tags."""
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "lxml")
    found: dict[str, Any] = {}
    for table in soup.find_all("table"):
        rows = table_rows(table)
        if len(rows) < 3:
            continue
        years = [int(y) for y in YEAR.findall(" ".join(" ".join(r) for r in rows[:4]))]
        distinct = list(dict.fromkeys(years))
        if len(distinct) < 2 or abs(distinct[0] - distinct[1]) != 1:
            continue  # without two consecutive years in the header the columns cannot be told apart
        current_first = distinct[0] > distinct[1]
        for row in rows:
            label, values = row[0], [v for v in (to_number(c) for c in row[1:]) if v is not None]
            if len(values) < 2:
                continue
            current, prior = (values[0], values[1]) if current_first else (values[1], values[0])
            for name, pattern in (("revenue", REVENUE), ("net_income", NET_INCOME)):
                if name in found or not pattern.match(label) or not prior or not plausible(name, current, prior):
                    continue
                found[name] = {"current": current, "prior": prior, "row": " | ".join(row)[:300], "scale": None,
                               "layout": "years_in_header", "anchored": False}
    return found


def anchors_from(raw: dict[str, Any], filed: date) -> dict[str, float]:
    """The same quarter a year earlier as the SEC holds it: the latest quarter ending at least 360 days before the
    filing day, for revenue, net income and diluted EPS."""
    import periodic_results as pr

    out: dict[str, float] = {}
    revenue = pr.discrete(pr.concept_facts(raw, pr.FLOWS["revenue"], filed))
    income = pr.discrete(pr.concept_facts(raw, pr.FLOWS["net_income"], filed))
    kind = "quarter" if revenue["quarter"] else "half"  # half-year filers anchor on the prior-year half
    series = {"revenue": revenue[kind], "net_income": income[kind]}
    eps: dict[date, float] = {}
    for concept in EPS_CONCEPTS:
        units = (((raw.get("facts") or {}).get("us-gaap") or {}).get(concept) or {}).get("units", {})
        for row in units.get("USD/shares", []):
            start, end = pr._day(row.get("start")), pr._day(row.get("end"))
            if start and end and isinstance(row.get("val"), (int, float)) and pr._kind(start, end) == "quarter":
                eps[end] = float(row["val"])
    series["eps_diluted"] = eps
    for name, values in series.items():
        ends = [e for e in values if (filed - e).days >= 360]
        if ends:
            out[name] = values[max(ends)]
        before = [e for e in values if e < filed - timedelta(days=20)]  # the latest quarter already reported
        if before:
            out[f"{name}_last"] = values[max(before)]
    return out


def press_tags(numbers: dict[str, Any]) -> dict[str, str | None]:
    revenue, income, eps = (numbers.get(k) for k in ("revenue", "net_income", "eps_diluted"))
    growth = None if not revenue or not revenue["prior"] else (revenue["current"] / revenue["prior"] - 1) * 100
    if income is None:
        profit = None
    else:
        now, before = income["current"] >= 0, income["prior"] >= 0
        profit = ("profit" if now and before else "loss" if not now and not before else
                  "turned_profit" if now else "turned_loss")
    return {"pr_revenue_growth": None if growth is None else
            "<-10%" if growth < -10 else "-10..0%" if growth < 0 else "0..10%" if growth < 10 else
            "10..25%" if growth < 25 else ">=25%",
            "pr_profit": profit,
            "pr_eps": None if eps is None else "up" if eps["current"] > eps["prior"] else
            "down" if eps["current"] < eps["prior"] else "flat",
            "pr_verified": None if not numbers else
            "no" if any(v.get("anchored") is False for v in numbers.values()) else "yes"}


def read_index(html: str) -> dict[str, Any]:
    """Items and the EX-99.1 link from an EDGAR filing index page."""
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "lxml")
    text = " ".join(t.get_text(" ", strip=True) for t in soup.select("div.formGrouping"))
    exhibit = None
    for tr in soup.select("table.tableFile tr"):
        cells = [td.get_text(" ", strip=True) for td in tr.find_all("td")]
        link = tr.find("a")
        if len(cells) >= 4 and cells[3].upper().startswith("EX-99.1") and link is not None:
            exhibit = "https://www.sec.gov" + link.get("href")
            break
    return {"items": sorted(set(re.findall(r"Item (\d\.\d\d)", text))), "exhibit": exhibit}


def schema(conn: sqlite3.Connection) -> None:
    conn.execute("CREATE TABLE IF NOT EXISTS press_release (accession TEXT PRIMARY KEY, ticker TEXT NOT NULL,"
                 " cik TEXT NOT NULL, filed TEXT NOT NULL, items TEXT, exhibit_url TEXT, numbers_json TEXT,"
                 " status TEXT NOT NULL, recorded_at TEXT NOT NULL)")


def candidates(registry_db: Path, ciks: dict[str, str], since: date) -> list[tuple[str, str, str, str, str]]:
    """(accession, ticker, cik, filed, form) of the covered companies' 8-Ks and 6-Ks in the SEC daily index,
    newest first."""
    by_cik = {str(int(c)): t for t, c in ciks.items()}
    with closing(sqlite3.connect(f"file:{registry_db}?mode=ro", uri=True)) as conn:
        rows = conn.execute("SELECT accession, cik, filed_date, form FROM sec_filing_event"
                            " WHERE form IN ('8-K', '8-K/A', '6-K', '6-K/A') AND filed_date >= ?"
                            " ORDER BY filed_date DESC", (str(since),)).fetchall()
    return [(a, by_cik[str(int(c))], str(int(c)), f, form) for a, c, f, form in rows if str(int(c)) in by_cik]


def update(conn: sqlite3.Connection, items: list[tuple[str, ...]], fetch: Fetch, limit: int = MAX_FILINGS,
           anchors: Callable[[str, date], dict[str, float]] = lambda cik, filed: {}) -> list[dict[str, Any]]:
    schema(conn)
    known = {r[0] for r in conn.execute("SELECT accession FROM press_release")}
    stamp, read, done = datetime.now(timezone.utc).isoformat(), 0, []
    for item in items:
        accession, ticker, cik, filed = item[:4]
        foreign = len(item) > 4 and str(item[4]).startswith("6-K")
        if accession in known or read >= limit:
            continue
        folder = f"https://www.sec.gov/Archives/edgar/data/{cik}/{accession.replace('-', '')}"
        try:
            index = read_index(fetch(f"{folder}/{accession}-index.htm"))
            read += 1
        except Exception as exc:  # noqa: BLE001 - not readable tonight: tried again tomorrow
            print(f"[PRESS] {ticker} {accession} index unreadable: {type(exc).__name__}", flush=True)
            continue
        status, numbers = "not_results", {}
        if ("2.02" in index["items"] or foreign) and index["exhibit"]:  # a 6-K carries no items: its exhibit decides
            try:
                html = fetch(index["exhibit"])
                read += 1
                numbers = read_tables(html, anchors(cik, date.fromisoformat(filed)))
                if not numbers and foreign:
                    numbers = read_unanchored(html)
            except Exception as exc:  # noqa: BLE001
                print(f"[PRESS] {ticker} exhibit unreadable: {type(exc).__name__}", flush=True)
                continue
            status = "read" if numbers else ("no_results_table" if foreign else "no_anchored_figure")
        elif "2.02" in index["items"]:
            status = "no_exhibit"
        conn.execute("INSERT INTO press_release VALUES (?,?,?,?,?,?,?,?,?)",
                     (accession, ticker, cik, filed, ",".join(index["items"]), index["exhibit"],
                      json.dumps(numbers), status, stamp))
        conn.commit()
        if status == "read":
            done.append({"accession": accession, "ticker": ticker, "filed": filed, "numbers": numbers})
    return done


def to_journal(conn: sqlite3.Connection, price_dir: Path) -> int:
    """Every read press release becomes an event of the results journal (once); its reaction is measured from the
    filing day like any other event and its tags join the journal's tests by themselves."""
    import earnings_journal as ej
    import price_history_store as phs

    ej.schema(conn)
    known = {r[0] for r in conn.execute("SELECT accession FROM earnings_event")}
    spy = phs.load("SPY", price_dir)
    stamp, added = datetime.now(timezone.utc).isoformat(), 0
    for accession, ticker, cik, filed, numbers_json, items in conn.execute(
            "SELECT accession, ticker, cik, filed, numbers_json, items FROM press_release WHERE status='read'").fetchall():
        if accession in known or spy is None:
            continue
        numbers = json.loads(numbers_json or "{}")
        moves = ej.reaction(phs.load(ticker, price_dir), spy, date.fromisoformat(filed))
        conn.execute("INSERT INTO earnings_event VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                     (accession, ticker, cik, "8-K 2.02" if "2.02" in (items or "") else "6-K press release", filed,
                      None, "press_release", json.dumps(numbers),
                      json.dumps(press_tags(numbers)), None, moves["reaction_pct"], moves["drift5_pct"],
                      moves["drift20_pct"], stamp, int(all(v is not None for v in moves.values()))))
        added += 1
    conn.commit()
    return added


def sec_fetcher(user_agent: str, limiter: Any = None) -> Fetch:
    from urllib import request

    def fetch(url: str) -> str:
        if limiter is not None and not limiter.permit("sec", max_interval_wait_seconds=1.0).permitted:
            raise RuntimeError("SEC rate limit")
        req = request.Request(url, headers={"User-Agent": user_agent, "Accept-Encoding": "identity"})
        with request.urlopen(req, timeout=30) as response:
            return response.read().decode("utf-8", "replace")
    return fetch


def main(argv: list[str] | None = None) -> int:
    import periodic_filing
    import price_history_store as phs

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--shortlist", type=Path, default=ROOT / "output" / "babis_shortlist_latest.json")
    parser.add_argument("--db", type=Path, default=ROOT / "data" / "earnings_journal.db")
    parser.add_argument("--registry", type=Path, default=ROOT / "data" / "research_registry.db")
    parser.add_argument("--sec-tickers", type=Path, default=periodic_filing.DEFAULT_TICKERS)
    parser.add_argument("--companyfacts", type=Path, default=periodic_filing.DEFAULT_DIR)
    parser.add_argument("--prices", type=Path, default=phs.DEFAULT_DIR)
    parser.add_argument("--limit", type=int, default=MAX_FILINGS)
    parser.add_argument("--env", type=Path, default=ROOT / ".env")
    parser.add_argument("--lookback-days", type=int, default=LOOKBACK_DAYS)
    args = parser.parse_args(argv)
    from provider_rate_limiter import SharedProviderRateLimiter
    from research_workflow_orchestrator import load_env

    load_env(args.env)  # SEC_USER_AGENT, never printed
    agent = os.getenv("SEC_USER_AGENT", "").strip()
    if not agent:
        print(json.dumps({"status": "no SEC user agent configured"}))
        return 0
    import research_universe

    tickers = set(research_universe.company_tickers(args.shortlist.parent))  # every listed company
    mapping = {t: c for t, c in periodic_filing.ticker_to_cik(args.sec_tickers).items() if t in tickers}
    items = candidates(args.registry, mapping, date.today() - timedelta(days=args.lookback_days))

    def anchors(cik: str, filed: date) -> dict[str, float]:
        path = args.companyfacts / f"CIK{int(cik):010d}.json.gz"
        return anchors_from(json.loads(gzip.decompress(path.read_bytes())), filed) if path.exists() else {}

    args.db.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(args.db)) as conn:
        read = update(conn, items, sec_fetcher(agent, SharedProviderRateLimiter()), args.limit, anchors)
        added = to_journal(conn, args.prices)
    print(json.dumps({"filings_seen": len(items), "results_read": len(read), "journal_events": added}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
