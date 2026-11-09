"""Headlines for the held positions and the traders' candidates: read, recorded and measured, never acted on.

Owner 2026-11-09, after the NVIDIA news of 2026-11-07/08 (the board's investigation of its CEO) reached no agent: the
nightly run reads prices up to the last close and SEC filings, and the stage-1 Yahoo headlines never arrived (the
yfinance news call answered "Yahoo is down" while Yahoo's own headline feed worked, and a failed call was written as
"no news").

* Symbols: the held positions (data/paper_positions.json) first, then tonight's trader candidates (Yankos, Periklis);
  not the whole universe (the feed is unofficial and rate-limited).
* Sources: Yahoo Finance's per-symbol headline feed (RSS, free, no key) first; the yfinance library second (nightly
  only, stops after a few failures). FMP's news needs a paid plan (tested 2026-11-09: 402 on the free key).
* Status per symbol: ``found``, ``none`` (the feed answered with no headline), ``not_recognised`` (the feed does not
  know the symbol), ``no_answer`` (no source answered) or ``not_read`` (out of time). Only the first two are known:
  a feed that did not answer is never "no news".
* A symbol's feed also carries articles about other companies (a market wrap, a peer). A headline counts for the
  symbol only when it names it: the ticker in capitals, or the company's name (its first word, or first two when the
  first is a common word such as "Advanced" or "American").
* Each headline gets a catalyst category from the catalyst taxonomy's keywords plus a few news words below, matched on
  whole words. ``material``: categories that can move a stock on their own (results, legal and regulatory, management,
  financing, dilution, deals, listing, clinical, large contracts) and naming the company. Headlines are secondary sources: they change no
  score, gate, limit or order.
* Shadow measurement (``evaluate``, with the nightly learning evaluation): the candidates and trades of each session
  with a material headline published between the previous close and the open, against those without one. If the
  flagged ones do clearly worse on enough sessions a lesson candidate is proposed (the pre-open check would take them
  down to watch, as it does for a new 8-K); accepting it stays the owner's decision.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
from contextlib import closing
from datetime import date, datetime, time as dtime, timedelta, timezone
from email.utils import parsedate_to_datetime
import json
from pathlib import Path
import re
import sqlite3
import threading
import time
from typing import Any, Callable, Iterable
from urllib import parse, request
import xml.etree.ElementTree as ET
from zoneinfo import ZoneInfo

import catalyst_taxonomy

ROOT = Path(__file__).resolve().parent
NEW_YORK = ZoneInfo("America/New_York")
DB_NAME = "news_watch.db"
FEED_URL = "https://feeds.finance.yahoo.com/rss/2.0/headline?s={symbol}&region=US&lang=en-US"
USER_AGENT = "Mozilla/5.0"          # the feed answers 404 to a request without a browser-style client name
FEED_TIMEOUT_SECONDS = 10.0
FEED_MAX_BYTES = 1_000_000
WORKERS = 4
NIGHTLY_BUDGET_SECONDS = 600.0      # what is not read by then stays not_read
PREOPEN_BUDGET_SECONDS = 240.0      # the pre-open check runs at 09:00 New York; orders wait for 09:35
SECOND_SOURCE_FAILURES_MAX = 3      # yfinance not answering: stop asking it for the rest of the run
SECOND_SOURCE_TIMEOUT_SECONDS = 15.0
KNOWN = ("found", "none")
STATUSES = ("found", "none", "not_recognised", "no_answer", "not_read")
# categories whose news can move a stock by itself (catalyst taxonomy ids); the rest are kept, not flagged
MATERIAL = frozenset({"earnings_guidance", "regulatory_legal", "governance_management", "financing_liquidity",
                      "dilution_capital_structure", "m_and_a_strategic", "listing_compliance", "clinical_healthcare",
                      "commercial_customer"})
# words newsrooms use that the filing-oriented taxonomy keywords miss (whole-word match, lower case)
NEWS_TERMS = {
    "regulatory_legal": ["investigates", "investigating", "investigated", "probe", "probes", "subpoena", "sues",
                         "sued", "class action", "fraud", "espionage", "spy", "export controls", "sanctions",
                         "recall", "data breach", "cyberattack", "charged", "indictment", "doj", "ftc", "sec charges"],
    "governance_management": ["ceo", "cfo", "resigns", "steps down", "ousted", "fired", "succession"],
    "earnings_guidance": ["beats", "misses", "raises outlook", "cuts outlook", "lowers outlook", "raises forecast",
                          "cuts forecast", "eps", "quarterly", "results"],
    "financing_liquidity": ["bankruptcy", "chapter 11", "default", "defaults"],
    "dilution_capital_structure": ["stock offering", "share offering", "secondary offering"],
    "m_and_a_strategic": ["buyout", "takeover", "to acquire", "to buy", "deal to buy", "spin off", "spinoff"],
    "listing_compliance": ["halted", "delisted", "delist"],
}

# a ticker that is also a common word or abbreviation is not matched on its own (the company name still is)
TICKER_WORDS = frozenset({"A", "I", "AN", "AT", "BE", "DO", "GO", "IT", "ON", "OR", "SO", "US", "AI", "EV", "CEO", "IPO",
                          "ALL", "ARE", "NOW", "ONE", "TWO", "NEW", "BIG", "CAR", "LOW", "HIGH", "WELL", "REAL"})
COMMON_NAME_WORDS = frozenset({"advanced", "american", "applied", "first", "general", "global", "international",
                               "national", "new", "the", "united", "royal", "taiwan", "china", "bank", "south", "north",
                               "western", "eastern", "southern", "northern", "great", "pacific", "atlantic", "digital",
                               "energy", "health", "capital", "group", "us", "u.s."})
NAME_SUFFIXES = re.compile(r"[,.]?\s+(inc|corp|corporation|company|co|ltd|plc|holdings|group|sa|nv|ag|se)\b.*$", re.I)

Fetch = Callable[[str], dict[str, Any]]


# ---------------------------------------------------------------- reading


def _utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat()


def parse_feed(payload: bytes, symbol: str) -> dict[str, Any]:
    """One feed answer: its headlines, or ``not_recognised`` when the feed does not know the symbol."""
    root = ET.fromstring(payload)
    channel = root.find("channel")
    if channel is None:
        raise ValueError("no channel in the feed")
    described = (channel.findtext("description") or "").strip()
    if not described.upper().endswith(symbol.upper()):
        return {"status": "not_recognised", "items": []}
    items = []
    for item in channel.findall("item"):
        title = (item.findtext("title") or "").strip()
        if not title:
            continue
        try:
            published = _utc(parsedate_to_datetime(item.findtext("pubDate") or ""))
        except (TypeError, ValueError):
            published = None  # unknown date: kept, never counted as before the open
        items.append({"guid": (item.findtext("guid") or item.findtext("link") or title).strip(), "title": title,
                      "summary": (item.findtext("description") or "").strip() or None, "publisher": None,
                      "link": (item.findtext("link") or "").strip() or None, "published_at": published})
    return {"status": "found" if items else "none", "items": items}


def yahoo_feed(symbol: str) -> dict[str, Any]:
    from monitor_core import yahoo_symbol

    provider = yahoo_symbol(symbol)
    req = request.Request(FEED_URL.format(symbol=parse.quote(provider, safe="-.^")),
                          headers={"User-Agent": USER_AGENT, "Accept": "application/rss+xml, application/xml"})
    with request.urlopen(req, timeout=FEED_TIMEOUT_SECONDS) as response:
        payload = response.read(FEED_MAX_BYTES + 1)
    if len(payload) > FEED_MAX_BYTES:
        raise ValueError("feed answer too large")
    return parse_feed(payload, provider)


def parse_yfinance(raw: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """yfinance news items, the 1.x shape (``content``) and the older flat one."""
    items = []
    for item in raw or []:
        content = item.get("content") if isinstance(item.get("content"), dict) else {}
        title = str(content.get("title") or item.get("title") or "").strip()
        if not title:
            continue
        published = None
        if content.get("pubDate"):
            try:
                published = _utc(datetime.fromisoformat(str(content["pubDate"]).replace("Z", "+00:00")))
            except ValueError:
                published = None
        elif item.get("providerPublishTime"):
            try:
                published = _utc(datetime.fromtimestamp(float(item["providerPublishTime"]), timezone.utc))
            except (TypeError, ValueError, OverflowError):
                published = None
        link = ((content.get("canonicalUrl") or {}).get("url") if isinstance(content.get("canonicalUrl"), dict)
                else None) or item.get("link")
        publisher = ((content.get("provider") or {}).get("displayName") if isinstance(content.get("provider"), dict)
                     else None) or item.get("publisher")
        items.append({"guid": str(item.get("id") or item.get("uuid") or link or title), "title": title,
                      "summary": content.get("summary") or None, "publisher": publisher, "link": link,
                      "published_at": published})
    return items


def yfinance_news(symbol: str) -> dict[str, Any]:
    import yfinance as yf
    from monitor_core import yahoo_symbol

    items = parse_yfinance(yf.Ticker(yahoo_symbol(symbol)).get_news(count=10))
    return {"status": "found" if items else "none", "items": items}


def _with_timeout(fetch: Fetch, symbol: str, seconds: float) -> dict[str, Any]:
    """A library call that may hang: run it in a daemon thread and give up after ``seconds``."""
    box: dict[str, Any] = {}

    def target() -> None:
        try:
            box["answer"] = fetch(symbol)
        except Exception as exc:  # noqa: BLE001 - reported as no answer
            box["error"] = exc
    thread = threading.Thread(target=target, daemon=True)
    thread.start()
    thread.join(seconds)
    if "answer" in box:
        return box["answer"]
    raise TimeoutError("no answer in time") if "error" not in box else box["error"]


class SecondSource:
    """The second source, asked only when the first did not answer, and dropped after a few failures in a row."""

    def __init__(self, fetch: Fetch | None, timeout: float = SECOND_SOURCE_TIMEOUT_SECONDS) -> None:
        self.fetch, self.timeout, self.failures, self.lock = fetch, timeout, 0, threading.Lock()

    def __call__(self, symbol: str) -> dict[str, Any] | None:
        with self.lock:
            if self.fetch is None or self.failures >= SECOND_SOURCE_FAILURES_MAX:
                return None
        try:
            answer = _with_timeout(self.fetch, symbol, self.timeout)
        except Exception:  # noqa: BLE001 - the second source is optional
            with self.lock:
                self.failures += 1
            return None
        with self.lock:
            self.failures = 0
        return answer


def read_symbol(symbol: str, first: Fetch, second: SecondSource) -> dict[str, Any]:
    try:
        answer = first(symbol)
        if answer["status"] in KNOWN:
            return {**answer, "source": "yahoo_rss"}
        not_recognised = answer
    except Exception:  # noqa: BLE001 - the feed did not answer: try the second source
        not_recognised = None
    fallback = second(symbol)
    if fallback is not None:
        return {**fallback, "source": "yfinance"}
    if not_recognised is not None:
        return {**not_recognised, "source": "yahoo_rss"}
    return {"status": "no_answer", "items": [], "source": None}


def read_all(symbols: list[str], first: Fetch, second: SecondSource, budget_seconds: float,
             workers: int = WORKERS, clock: Callable[[], float] = time.monotonic) -> dict[str, dict[str, Any]]:
    """Every symbol once, in order of priority; what is not read within the budget stays ``not_read``."""
    out: dict[str, dict[str, Any]] = {}
    deadline = clock() + budget_seconds
    queue = list(symbols)
    pool = ThreadPoolExecutor(max_workers=max(1, workers))
    running: dict[Any, str] = {}
    try:
        while queue or running:
            while queue and len(running) < max(1, workers) and clock() < deadline:
                symbol = queue.pop(0)
                running[pool.submit(read_symbol, symbol, first, second)] = symbol
            if not running:
                break
            done, _ = wait(list(running), timeout=max(0.0, deadline - clock()), return_when=FIRST_COMPLETED)
            if not done:
                break
            for future in done:
                symbol = running.pop(future)
                try:
                    out[symbol] = future.result()
                except Exception:  # noqa: BLE001 - read_symbol reports its own failures; this is a safety net
                    out[symbol] = {"status": "no_answer", "items": [], "source": None}
    finally:
        pool.shutdown(wait=False, cancel_futures=True)
    for symbol in symbols:
        out.setdefault(symbol, {"status": "not_read", "items": [], "source": None})
    return out


# ---------------------------------------------------------------- classifying


def _terms(taxonomy: dict[str, Any]) -> dict[str, list[tuple[str, re.Pattern[str]]]]:
    out: dict[str, list[tuple[str, re.Pattern[str]]]] = {}
    for category_id, category in (taxonomy.get("categories") or {}).items():
        if category_id == "unknown":
            continue
        words = [str(w).lower() for w in category.get("keywords") or []] + NEWS_TERMS.get(category_id, [])
        out[category_id] = [(w, re.compile(r"(?<![a-z0-9])" + re.escape(w) + r"(?![a-z0-9])")) for w in dict.fromkeys(words)]
    return out


def classify(title: str, summary: str | None, terms: dict[str, list[tuple[str, re.Pattern[str]]]],
             materiality: dict[str, float]) -> dict[str, Any]:
    """The category with most matched words in the title (then the summary), ties by the taxonomy's materiality."""
    best = None
    for text in (title, summary or ""):
        lowered = text.lower()
        matches = []
        for category_id, patterns in terms.items():
            found = [w for w, p in patterns if p.search(lowered)]
            if found:
                matches.append((len(found), materiality.get(category_id, 1.0), category_id, found))
        if matches:
            best = max(matches, key=lambda m: (m[0], m[1]))
            break
    if best is None:
        return {"category_id": "unknown", "material": False, "matched": []}
    return {"category_id": best[2], "material": best[2] in MATERIAL, "matched": best[3][:5]}


def name_words(company: str | None) -> str | None:
    """The words a headline uses for the company: its first word, or the first two when the first is common."""
    name = NAME_SUFFIXES.sub("", str(company or "")).strip()
    words = [w for w in re.split(r"\s+", name) if w]
    if not words:
        return None
    first = words[0].strip(",.").lower()
    if (first in COMMON_NAME_WORDS or len(first) < 3) and len(words) > 1:
        return f"{words[0]} {words[1]}".strip(",.").lower()
    return first if len(first) >= 3 else None


def names_company(title: str, symbol: str, company_words: str | None) -> bool:
    if symbol not in TICKER_WORDS and re.search(r"(?<![A-Za-z0-9$])\$?" + re.escape(symbol) + r"(?![A-Za-z0-9])", title):
        return True
    if company_words:
        return re.search(r"(?<![a-z0-9])" + re.escape(company_words) + r"(?![a-z0-9])", title.lower()) is not None
    return False


# ---------------------------------------------------------------- storing


def connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    ensure_tables(conn)
    return conn


def ensure_tables(conn: sqlite3.Connection) -> None:
    conn.execute("CREATE TABLE IF NOT EXISTS news_headlines (symbol TEXT NOT NULL, guid TEXT NOT NULL, title TEXT NOT NULL,"
                 " summary TEXT, publisher TEXT, link TEXT, published_at TEXT, source TEXT NOT NULL,"
                 " category_id TEXT NOT NULL, names_company INTEGER NOT NULL, material INTEGER NOT NULL,"
                 " matched_json TEXT NOT NULL, first_seen_at TEXT NOT NULL, PRIMARY KEY (symbol, guid))")
    conn.execute("CREATE TABLE IF NOT EXISTS news_fetches (fetched_at TEXT NOT NULL, run_label TEXT NOT NULL,"
                 " symbol TEXT NOT NULL, status TEXT NOT NULL, source TEXT, items INTEGER NOT NULL,"
                 " PRIMARY KEY (fetched_at, symbol))")


def store(conn: sqlite3.Connection, results: dict[str, dict[str, Any]], run_label: str, fetched_at: str,
          taxonomy: dict[str, Any] | None = None, companies: dict[str, str] | None = None) -> int:
    """Every answer as a fetch row; every headline once (the first sighting is kept). Returns new headlines.
    ``material``: a material category in a headline that names the company."""
    ensure_tables(conn)
    taxonomy = taxonomy or catalyst_taxonomy.load_taxonomy()
    terms = _terms(taxonomy)
    materiality = {k: float(v.get("base_materiality") or 1) for k, v in (taxonomy.get("categories") or {}).items()}
    added = 0
    for symbol, answer in results.items():
        words = name_words((companies or {}).get(symbol))
        conn.execute("INSERT OR REPLACE INTO news_fetches VALUES (?,?,?,?,?,?)",
                     (fetched_at, run_label, symbol, answer["status"], answer.get("source"), len(answer["items"])))
        for item in answer["items"]:
            label = classify(item["title"], item.get("summary"), terms, materiality)
            about = names_company(item["title"], symbol, words)
            cursor = conn.execute("INSERT OR IGNORE INTO news_headlines VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                                  (symbol, item["guid"], item["title"], item.get("summary"), item.get("publisher"),
                                   item.get("link"), item.get("published_at"), answer.get("source") or "unknown",
                                   label["category_id"], int(about), int(label["material"] and about),
                                   json.dumps(label["matched"]), fetched_at))
            added += cursor.rowcount
    conn.commit()
    return added


# ---------------------------------------------------------------- which symbols


def company_names(root: Path) -> dict[str, str]:
    """Ticker to company name from tonight's trader boards (they cover the held positions too)."""
    names: dict[str, str] = {}
    for agent in ("yankos", "periklis"):
        path = root / "output" / f"{agent}_strategy_latest.json"
        try:
            rows = json.loads(path.read_text(encoding="utf-8-sig")).get("results") or [] if path.is_file() else []
        except ValueError:
            rows = []
        for row in rows:
            if row.get("ticker") and row.get("company"):
                names.setdefault(str(row["ticker"]).upper(), str(row["company"]))
    return names


def watched_symbols(root: Path) -> dict[str, list[str]]:
    """{symbol: roles}: the held positions first, then tonight's candidates, each in board order."""
    roles: dict[str, list[str]] = {}
    registry = root / "data" / "paper_positions.json"
    if registry.is_file():
        try:
            held = json.loads(registry.read_text(encoding="utf-8"))
        except ValueError:
            held = {}
        for symbol in held if isinstance(held, dict) else []:
            roles.setdefault(str(symbol).upper(), []).append("held")
    for agent in ("yankos", "periklis"):
        path = root / "output" / f"{agent}_strategy_latest.json"
        if not path.is_file():
            continue
        try:
            rows = json.loads(path.read_text(encoding="utf-8-sig")).get("results") or []
        except ValueError:
            continue
        for row in rows:
            if row.get("post_gate_verdict") == "candidate" and row.get("ticker"):
                symbol = str(row["ticker"]).upper()
                if f"candidate:{agent}" not in roles.setdefault(symbol, []):
                    roles[symbol].append(f"candidate:{agent}")
    return roles


# ---------------------------------------------------------------- report


def recent_material(conn: sqlite3.Connection, symbols: Iterable[str], since: str) -> list[dict[str, Any]]:
    wanted = sorted({s.upper() for s in symbols})
    if not wanted:
        return []
    rows = conn.execute(
        f"SELECT symbol, title, category_id, published_at, link FROM news_headlines WHERE material=1 AND "
        f"published_at >= ? AND symbol IN ({','.join('?' * len(wanted))}) ORDER BY published_at DESC",
        (since, *wanted)).fetchall()
    return [{"symbol": s, "title": t, "category_id": c, "published_at": p, "link": link} for s, t, c, p, link in rows]


def summarise(conn: sqlite3.Connection, roles: dict[str, list[str]], results: dict[str, dict[str, Any]],
              run_label: str, fetched_at: str, added: int, since: str) -> dict[str, Any]:
    counts = {s: sum(1 for r in results.values() if r["status"] == s) for s in STATUSES}
    sources = {}
    for r in results.values():
        if r.get("source"):
            sources[r["source"]] = sources.get(r["source"], 0) + 1
    material = recent_material(conn, roles, since)
    for item in material:
        item["roles"] = roles.get(item["symbol"], [])
    return {"schema": "ecosystem.news_watch.v1", "run_label": run_label, "fetched_at": fetched_at, "since": since,
            "symbols": len(roles), "held": sum(1 for r in roles.values() if "held" in r), "statuses": counts,
            "sources": sources, "new_headlines": added, "material": material,
            "unknown_symbols": sorted(s for s, r in results.items() if r["status"] not in KNOWN),
            "active_decisions_changed": False}


def render(summary: dict[str, Any], limit: int = 40) -> str:
    s = summary["statuses"]
    lines = ["# Ειδήσεις για θέσεις και υποψήφιους (μόνο ανάγνωση)", "",
             f"Ανάγνωση {summary['run_label']} στις {summary['fetched_at']} UTC · σύμβολα {summary['symbols']} "
             f"(θέσεις {summary['held']}) · με ειδήσεις {s['found']} · χωρίς {s['none']} · άγνωστο σύμβολο "
             f"{s['not_recognised']} · δεν απάντησε {s['no_answer']} · δεν διαβάστηκαν {s['not_read']} · "
             f"νέοι τίτλοι {summary['new_headlines']}.", "",
             "Οι τίτλοι είναι δευτερεύουσα πηγή: δεν αλλάζουν βαθμούς, όρια ή εντολές. "
             f"Σημαντικοί από {summary['since']} UTC:", ""]
    if not summary["material"]:
        lines.append("- Κανένας σημαντικός τίτλος.")
    else:
        lines += ["| μετοχή | ρόλος | κατηγορία | δημοσίευση UTC | τίτλος |", "|---|---|---|---|---|"]
        for item in summary["material"][:limit]:
            title = item["title"].replace("|", "/")
            title = f"[{title}]({item['link']})" if item.get("link") else title
            lines.append(f"| {item['symbol']} | {', '.join(item.get('roles') or [])} | {item['category_id']} | "
                         f"{item['published_at'] or 'άγνωστο'} | {title} |")
        if len(summary["material"]) > limit:
            lines.append(f"\n…και {len(summary['material']) - limit} ακόμη (output/news_watch_latest.json).")
    return "\n".join(lines) + "\n"


def run(root: Path, run_label: str, *, first: Fetch = yahoo_feed, second: Fetch | None = yfinance_news,
        budget_seconds: float = NIGHTLY_BUDGET_SECONDS, now: datetime | None = None,
        write: bool = True) -> dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    fetched_at = _utc(now)
    roles = watched_symbols(root)
    results = read_all(list(roles), first, SecondSource(second), budget_seconds)
    with closing(connect(root / "data" / DB_NAME)) as conn:
        added = store(conn, results, run_label, fetched_at, companies=company_names(root))
        summary = summarise(conn, roles, results, run_label, fetched_at, added, _utc(now - timedelta(days=3)))
    if write:
        out = root / "output"
        out.mkdir(parents=True, exist_ok=True)
        (out / "news_watch_latest.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        (out / "news_watch_latest.md").write_text(render(summary), encoding="utf-8")
    return summary


# ---------------------------------------------------------------- shadow measurement


def previous_close(session: date) -> datetime:
    """16:00 New York of the weekday before (an exchange holiday in between makes the window longer, never shorter)."""
    day = session - timedelta(days=1)
    while day.weekday() >= 5:
        day -= timedelta(days=1)
    return datetime.combine(day, dtime(16, 0), NEW_YORK)


def session_open(session: date) -> datetime:
    return datetime.combine(session, dtime(9, 30), NEW_YORK)


def news_flag(conn: sqlite3.Connection, symbol: str, session: date) -> dict[str, Any]:
    """Whether a material headline came out between the previous close and the session's open. Unknown when no
    known read of the symbol was made from that close to the end of the session (the feed keeps the last headlines)."""
    start, opening = previous_close(session), session_open(session)
    end = datetime.combine(session, dtime(23, 59), NEW_YORK)
    read = conn.execute("SELECT 1 FROM news_fetches WHERE symbol=? AND status IN ('found','none') AND fetched_at>=? "
                        "AND fetched_at<=? LIMIT 1", (symbol, _utc(start), _utc(end))).fetchone()
    if read is None:
        return {"known": False, "flagged": None, "categories": []}
    rows = conn.execute("SELECT DISTINCT category_id FROM news_headlines WHERE symbol=? AND material=1 AND "
                        "published_at>=? AND published_at<?", (symbol, _utc(start), _utc(opening))).fetchall()
    return {"known": True, "flagged": bool(rows), "categories": sorted(r[0] for r in rows)}


def evaluate(conn: sqlite3.Connection, records: list[dict[str, Any]],
             metrics: Callable[[list[dict[str, Any]]], dict[str, Any]], margin_pct: float) -> dict[str, Any]:
    """Candidates and trades with a material headline before the open against those without one."""
    ensure_tables(conn)
    flagged, clear, unknown = [], [], 0
    by_category: dict[str, list[dict[str, Any]]] = {}
    for record in records:
        if record.get("kind") not in ("trade", "shadow_candidate"):
            continue
        try:
            session = date.fromisoformat(str(record["session_date"]))
        except (KeyError, ValueError):
            continue
        flag = news_flag(conn, str(record.get("ticker") or "").upper(), session)
        if not flag["known"]:
            unknown += 1
            continue
        (flagged if flag["flagged"] else clear).append(record)
        for category in flag["categories"]:
            by_category.setdefault(category, []).append(record)
    a, b = metrics(flagged), metrics(clear)
    judged = a["status"] == "judged" and b["status"] == "judged"
    gap = None if a["mean_pct"] is None or b["mean_pct"] is None else round(a["mean_pct"] - b["mean_pct"], 3)
    verdict = ("insufficient_sample" if not judged else
               "news-flagged did worse" if gap is not None and gap <= -margin_pct else
               "news-flagged did better" if gap is not None and gap >= margin_pct else "no clear difference")
    return {"schema": "ecosystem.news_shadow.v1", "flagged": a, "clear": b, "unknown": unknown,
            "flagged_minus_clear_pct": gap, "by_category": {k: metrics(v) for k, v in sorted(by_category.items())},
            "verdict": verdict, "active_decisions_changed": False}


def proposal(result: dict[str, Any]) -> list[dict[str, Any]]:
    if result.get("verdict") != "news-flagged did worse":
        return []
    return [{"target_agent": "preopen_check", "criterion_id": "material_news_before_open",
             "proposed_change": "A candidate with a material headline published after the research close takes the "
                                "same path as a new 8-K: candidate to watch at the pre-open check.",
             "support": result["flagged"]["known"],
             "evidence": {k: result[k] for k in ("flagged", "clear", "flagged_minus_clear_pct", "by_category")}}]


def render_shadow(result: dict[str, Any]) -> str:
    def v(x: Any) -> str:
        return "άγνωστο" if x is None else str(x)
    lines = ["# Ειδήσεις πριν το άνοιγμα (shadow, χωρίς εντολές)", "",
             "Υποψήφιοι και θέσεις με σημαντικό τίτλο ανάμεσα στο προηγούμενο κλείσιμο και το άνοιγμα, έναντι των "
             "υπολοίπων. Τίποτα δεν αλλάζει στις εντολές.", "",
             f"Συμπέρασμα: **{result['verdict']}** · διαφορά μέσου {v(result['flagged_minus_clear_pct'])}% · "
             f"χωρίς γνωστή ανάγνωση {result['unknown']}.", "",
             "| ομάδα | με αποτέλεσμα | μέρες | μέσο % | υπέρβαση SPY % | νίκησαν SPY % | κατάσταση |",
             "|---|---|---|---|---|---|---|"]
    for name, m in [("με είδηση", result["flagged"]), ("χωρίς", result["clear"]),
                    *[(f"κατηγορία {k}", m) for k, m in result["by_category"].items()]]:
        lines.append(f"| {name} | {m['known']} | {m['days']} | {v(m['mean_pct'])} | {v(m['mean_excess_pct'])} | "
                     f"{v(m['beat_spy_pct'])} | {m['status']} |")
    return "\n".join(lines) + "\n"


def run_shadow(root: Path, records: list[dict[str, Any]], metrics: Callable[[list[dict[str, Any]]], dict[str, Any]],
               margin_pct: float, *, write: bool = True) -> dict[str, Any]:
    with closing(connect(root / "data" / DB_NAME)) as conn:
        result = evaluate(conn, records, metrics, margin_pct)
    if write:
        out = root / "output" / "paper_learning"
        out.mkdir(parents=True, exist_ok=True)
        (out / "news_shadow_latest.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        (out / "news_shadow_latest.md").write_text(render_shadow(result), encoding="utf-8")
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--label", default="nightly", choices=("nightly", "preopen", "manual"))
    parser.add_argument("--budget-seconds", type=float)
    parser.add_argument("--no-second-source", action="store_true", help="the feed only, no yfinance")
    args = parser.parse_args(argv)
    budget = args.budget_seconds or (PREOPEN_BUDGET_SECONDS if args.label == "preopen" else NIGHTLY_BUDGET_SECONDS)
    second = None if args.no_second_source or args.label == "preopen" else yfinance_news
    summary = run(args.root, args.label, second=second, budget_seconds=budget)
    print(json.dumps({"symbols": summary["symbols"], **summary["statuses"], "sources": summary["sources"],
                      "new_headlines": summary["new_headlines"], "material": len(summary["material"])},
                     ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
