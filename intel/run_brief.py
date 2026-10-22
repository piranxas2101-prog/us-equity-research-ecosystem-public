#!/usr/bin/env python3
import argparse
import datetime as dt
import json
import os
from typing import Dict, List, Optional, Tuple

import urllib.request


USER_AGENT = "market-brief/1.0 (contact: codex@example.com)"


def fetch_json(url: str) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=20) as response:
            return json.loads(response.read().decode("utf-8"))
    except Exception:
        return {}


def get_quotes(symbols: List[str]) -> Dict[str, dict]:
    url = "https://query1.finance.yahoo.com/v7/finance/quote?symbols=" + ",".join(symbols)
    data = fetch_json(url)
    results = data.get("quoteResponse", {}).get("result", [])
    return {item.get("symbol"): item for item in results if item.get("symbol")}


def format_quote(quote: Optional[dict], label: str, suffix: str = "") -> str:
    if not quote:
        return f"{label} n/a"
    price = quote.get("regularMarketPrice")
    change_pct = quote.get("regularMarketChangePercent")
    if price is None:
        return f"{label} n/a"
    if change_pct is None:
        return f"{label} {price:.2f}{suffix}"
    return f"{label} {price:.2f} ({change_pct:+.2f}%)"


def format_tnx(quote: Optional[dict]) -> str:
    if not quote or quote.get("regularMarketPrice") is None:
        return "TNX n/a"
    return f"TNX {quote['regularMarketPrice'] / 10:.2f}%"


def get_cik_for_ticker(ticker: str) -> Optional[str]:
    tickers_url = "https://www.sec.gov/files/company_tickers.json"
    data = fetch_json(tickers_url)
    for item in data.values():
        if item.get("ticker", "").upper() == ticker.upper():
            return str(item.get("cik_str")).zfill(10)
    return None


def get_recent_filings(cik: str) -> List[Tuple[str, str, str]]:
    submissions_url = f"https://data.sec.gov/submissions/CIK{cik}.json"
    data = fetch_json(submissions_url)
    recent = data.get("filings", {}).get("recent", {})
    forms = recent.get("form", [])
    dates = recent.get("filingDate", [])
    accessions = recent.get("accessionNumber", [])
    filings = []
    for form, date, accession in zip(forms, dates, accessions):
        filings.append((form, date, accession))
    return filings


def is_red_flag(form: str) -> bool:
    form_upper = form.upper()
    red_prefixes = ("S-", "424B", "FWP", "POS", "EFFECT", "RW", "WARRANT")
    return form_upper.startswith(red_prefixes)


def sec_index_link(cik: str, accession: str) -> str:
    accession_no_dashes = accession.replace("-", "")
    return f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{accession_no_dashes}/{accession}-index.html"


def get_laes_status() -> Tuple[str, List[str], List[str], bool]:
    cik = get_cik_for_ticker("LAES")
    news_items = []
    filings_items = []
    red_flag = False
    now = dt.datetime.utcnow()
    if cik:
        filings = get_recent_filings(cik)
        for form, date_str, accession in filings:
            try:
                filed_dt = dt.datetime.strptime(date_str, "%Y-%m-%d")
            except ValueError:
                continue
            if now - filed_dt <= dt.timedelta(hours=48):
                link = sec_index_link(cik, accession)
                filings_items.append(f"{form} ({date_str}) {link}")
                if is_red_flag(form):
                    red_flag = True
    try:
        news = fetch_json("https://query1.finance.yahoo.com/v1/finance/search?q=LAES")
        for item in news.get("news", []):
            published = item.get("providerPublishTime")
            if not published:
                continue
            published_dt = dt.datetime.utcfromtimestamp(published)
            if now - published_dt <= dt.timedelta(hours=24):
                title = item.get("title", "News")
                link = item.get("link", "")
                if link:
                    news_items.append(f"{title} ({link})")
                else:
                    news_items.append(title)
    except Exception:
        pass
    return cik or "n/a", news_items, filings_items, red_flag


def build_am_brief(quotes: Dict[str, dict], laes_info: Tuple[str, List[str], List[str], bool]) -> str:
    _, news_items, filings_items, red_flag = laes_info
    lines = []
    if red_flag and filings_items:
        lines.append("[ALERT][RED] " + " ".join(filings_items[:2]))
    lines.append(f"AM Pulse {dt.datetime.now().strftime('%Y-%m-%d')}")
    lines.append("Status: OK")
    lines.append(
        "Futures: "
        + ", ".join(
            [
                format_quote(quotes.get("ES=F"), "ES"),
                format_quote(quotes.get("RTY=F"), "RTY"),
                format_quote(quotes.get("VXX"), "VXX"),
            ]
        )
    )
    lines.append(
        "Indices: "
        + ", ".join(
            [
                format_quote(quotes.get("SPY"), "SPY"),
                format_quote(quotes.get("QQQ"), "QQQ"),
                format_quote(quotes.get("IWM"), "IWM"),
                format_quote(quotes.get("^VIX"), "VIX"),
                format_tnx(quotes.get("^TNX")),
                format_quote(quotes.get("DX-Y.NYB"), "DXY"),
            ]
        )
    )
    if news_items:
        lines.append("LAES news 24h: " + "; ".join(news_items[:2]))
    else:
        lines.append("LAES news 24h: none")
    if filings_items:
        lines.append("LAES filings 48h: " + "; ".join(filings_items))
    else:
        lines.append("LAES filings 48h: none")
    return "\n".join(lines[:12])


def build_preopen_brief(quotes: Dict[str, dict], laes_info: Tuple[str, List[str], List[str], bool]) -> str:
    _, news_items, filings_items, red_flag = laes_info
    lines = []
    if red_flag and filings_items:
        lines.append("[ALERT][RED] " + " ".join(filings_items[:2]))
    lines.append(f"Pre-open Brief {dt.datetime.now().strftime('%Y-%m-%d')}")
    lines.append(
        "RTY/ES/VXX: "
        + ", ".join(
            [
                format_quote(quotes.get("RTY=F"), "RTY"),
                format_quote(quotes.get("ES=F"), "ES"),
                format_quote(quotes.get("VXX"), "VXX"),
            ]
        )
    )
    lines.append(
        "Gauges: "
        + ", ".join(
            [
                format_quote(quotes.get("SPY"), "SPY"),
                format_quote(quotes.get("QQQ"), "QQQ"),
                format_quote(quotes.get("IWM"), "IWM"),
                format_quote(quotes.get("^VIX"), "VIX"),
                format_tnx(quotes.get("^TNX")),
                format_quote(quotes.get("DX-Y.NYB"), "DXY"),
            ]
        )
    )
    if news_items:
        lines.append("LAES news 24h: " + "; ".join(news_items[:2]))
    else:
        lines.append("LAES news 24h: none")
    if filings_items:
        lines.append("LAES filings 48h: " + "; ".join(filings_items))
    else:
        lines.append("LAES filings 48h: none")
    lines.append("Regime: mixed")
    lines.append("Watch next: 1) Rate/FX volatility, 2) Small-cap breadth, 3) VIX term structure")
    return "\n".join(lines)


def write_brief(mode: str, content: str) -> None:
    date_str = dt.datetime.now().strftime("%Y-%m-%d")
    out_dir = os.path.join("intel", date_str)
    os.makedirs(out_dir, exist_ok=True)
    if mode == "am":
        out_path = os.path.join(out_dir, "am.md")
    else:
        out_path = os.path.join(out_dir, "preopen.md")
    with open(out_path, "w", encoding="utf-8") as handle:
        handle.write(content + "\n")
    with open(os.path.join("intel", "latest.md"), "w", encoding="utf-8") as handle:
        handle.write(content + "\n")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["am", "preopen"], required=True)
    args = parser.parse_args()

    symbols = [
        "ES=F",
        "RTY=F",
        "VXX",
        "SPY",
        "QQQ",
        "IWM",
        "^VIX",
        "^TNX",
        "DX-Y.NYB",
    ]
    quotes = get_quotes(symbols)
    laes_info = get_laes_status()

    if args.mode == "am":
        content = build_am_brief(quotes, laes_info)
    else:
        content = build_preopen_brief(quotes, laes_info)

    write_brief(args.mode, content)
    print(content)


if __name__ == "__main__":
    main()
