"""Pre-open check: the traders' nightly decisions looked at again 30 minutes before the US open.

The nightly research run decides what is worth trading from prices up to the last close. Two things can change
before the next open and are not in those prices: the pre-market price (a stock may open far from where it
closed) and new SEC filings accepted after the close. This check runs at 09:00 New York, before the separate
paper workflow, and writes a pre-open copy of each trader's output for it:

* Gap against the trade: a long whose pre-market price is below the last close by more than one 14-day ATR
  (a short: above it) goes from candidate to watch. The nightly setup assumed a price that is no longer there.
* New content-bearing 8-K item after the close (results, agreements, officer changes ...; mapped by the catalyst
  taxonomy, supporting-only items and plain forms such as Form 4 ignored): candidate to watch. The business
  news is newer than the fundamental review, and its effect is unknown until the next research run reads it.

The check can only take a candidate down to watch, never raise anything, and never changes scores, the
fundamental gate, risk limits or sizing. A pre-market price or an ATR that cannot be read stays unknown and is
reported as such; the nightly decision is then kept. Rationale: docs/PREOPEN_TRADER_RUN.md.
"""
from __future__ import annotations

import argparse
from datetime import date, datetime, time as dtime, timezone
import json
import os
import sys
from pathlib import Path
from typing import Any, Callable, Iterable
from zoneinfo import ZoneInfo

import catalyst_taxonomy
import price_history_store as phs

ROOT = Path(__file__).resolve().parent
NEW_YORK = ZoneInfo("America/New_York")
AGENTS = ("yankos", "periklis")
GAP_ATR_MULTIPLE = 1.0
MAX_CLOSE_AGE_DAYS = 5
QUOTES_BUDGET_SECONDS = 300.0   # pre-market prices: what is not read in 5 minutes stays unknown
FILINGS_BUDGET_SECONDS = 300.0  # SEC filings: same
ABANDONED_WORK = False          # a source thread was left running: exit hard once the outputs are written

Quotes = Callable[[list[str]], dict[str, dict[str, Any]]]
Filings = Callable[[int], list[dict[str, Any]]]


def to_float(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number else None


def last_close(ticker: str, directory: Path = phs.DEFAULT_DIR) -> tuple[float | None, date | None]:
    """The last stored session close (raw, as traded) and its date, or unknown."""
    history = phs.load(ticker, directory)
    if history is None or "Close" not in history:
        return None, None
    closes = history["Close"].dropna()
    if closes.empty:
        return None, None
    return float(closes.iloc[-1]), closes.index[-1].date()


def close_moment(session: date) -> datetime:
    """16:00 New York on the given session day, in UTC."""
    return datetime.combine(session, dtime(16, 0), tzinfo=NEW_YORK).astimezone(timezone.utc)


def recent_filings(submissions: dict[str, Any]) -> list[dict[str, Any]]:
    recent = (submissions.get("filings") or {}).get("recent") or {}
    forms = recent.get("form") or []
    def pick(name: str, index: int) -> Any:
        values = recent.get(name) or []
        return values[index] if index < len(values) else None

    out = []
    for index, form in enumerate(forms):
        accession = str(pick("accessionNumber", index) or "")
        document = pick("primaryDocument", index)
        out.append({"form": str(form or ""), "filing_date": pick("filingDate", index),
                    "accepted": pick("acceptanceDateTime", index), "items": pick("items", index) or "",
                    "accession": accession,
                    "url": document and accession and
                    f"https://www.sec.gov/Archives/edgar/data/{int(submissions.get('cik') or 0)}/"
                    f"{accession.replace('-', '')}/{document}"})
    return out


def _accepted_at(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        moment = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def content_events_since(filings: Iterable[dict[str, Any]], since: datetime) -> list[dict[str, Any]]:
    """Content-bearing SEC item events of the filings accepted after ``since`` (taxonomy decides what counts)."""
    fresh = [f for f in filings if (_accepted_at(f.get("accepted")) or since) > since]
    if not fresh:
        return []
    classified = catalyst_taxonomy.classify_catalyst({"sec_recent_filings": [
        {"form": f["form"], "filing_date": f.get("filing_date"), "items": f.get("items"), "url": f.get("url")}
        for f in fresh]})
    return [{"sec_form": e.get("sec_form"), "sec_item": e.get("sec_item"), "event_name": e.get("event_name"),
             "direction": e.get("direction"), "event_date": e.get("event_date"), "source_url": e.get("source_url")}
            for e in classified.get("evidence") or [] if e.get("source_kind") == "sec_item"]


def judge(row: dict[str, Any], quote: dict[str, Any] | None, reference: tuple[float | None, date | None],
          events: list[dict[str, Any]] | None) -> dict[str, Any]:
    """The pre-open view of one trader row: gap, new filings, and whether a candidate stays one."""
    direction = str(row.get("trade_direction") or "").lower()
    close, session = reference
    price = to_float((quote or {}).get("price"))
    atr = to_float(row.get("atr_14_pct"))
    gap = None if price is None or close is None or close <= 0 else round((price / close - 1.0) * 100.0, 3)
    reasons: list[str] = []
    notes: list[str] = []
    if gap is None:
        notes.append("Pre-market price or last close unknown: the gap could not be judged.")
    elif atr is None or atr <= 0:
        notes.append(f"Gap {gap:+.2f}%, but the 14-day ATR is unknown: the gap could not be judged.")
    else:
        against = -gap if direction == "long" else gap if direction == "short" else None
        if against is not None and against > GAP_ATR_MULTIPLE * atr:
            reasons.append(f"Pre-market gap {gap:+.2f}% against the {direction} trade is larger than "
                           f"{GAP_ATR_MULTIPLE:g}x the 14-day ATR ({atr:.2f}%).")
    if events is None:
        notes.append("New SEC filings since the close could not be read.")
    elif events:
        names = ", ".join(sorted({f"{e['sec_form']} item {e['sec_item']}" for e in events}))
        reasons.append(f"New SEC event after the last close ({names}); the fundamental review has not read it.")
    candidate = str(row.get("signal_verdict") or "").lower() == "candidate"
    return {"premarket_price": price, "premarket_as_of": (quote or {}).get("as_of"), "last_close": close,
            "last_close_session": None if session is None else str(session), "gap_pct": gap, "atr_14_pct": atr,
            "new_sec_events": events, "demote_reasons": reasons if candidate else [], "notes": notes,
            "decision": ("demoted_to_watch" if candidate and reasons else "kept" if candidate else "not_candidate")}


def apply(payload: dict[str, Any], views: dict[str, dict[str, Any]], *, checked_at: str) -> dict[str, Any]:
    """A pre-open copy of a trader payload: demoted candidates become watch, every candidate carries its view."""
    results = []
    for row in payload.get("results") or []:
        view = views.get(str(row.get("ticker") or "").upper()) if isinstance(row, dict) else None
        if view is None or str(row.get("signal_verdict") or "").lower() != "candidate":
            results.append(row)
            continue
        row_view = judge(row, view["quote"], view["reference"], view["events"])
        updated = {**row, "preopen_check": row_view}
        if row_view["decision"] == "demoted_to_watch":
            updated["signal_verdict"] = "watch"
            updated["nightly_signal_verdict"] = row.get("signal_verdict")
        results.append(updated)
    return {**payload, "results": results, "preopen": {"schema": "ecosystem.preopen_check.v1", "checked_at": checked_at,
                                                       "gap_atr_multiple": GAP_ATR_MULTIPLE,
                                                       "nightly_run_id": payload.get("run_id")}}


def _premarket(moment: datetime, today: date) -> bool:
    local = moment.astimezone(NEW_YORK)
    return local.date() == today and dtime(4, 0) <= local.time() < dtime(9, 30)


def premarket_trade(snapshot: dict[str, Any] | None, today: date) -> dict[str, Any] | None:
    """Today's pre-market price from an Alpaca snapshot's latest trade, or None (an older trade is not today's)."""
    trade = (snapshot or {}).get("latestTrade") or {}
    price, stamp = to_float(trade.get("p")), str(trade.get("t") or "")
    if price is None or price <= 0 or not stamp:
        return None
    try:  # RFC 3339 with nanoseconds: keep microseconds for fromisoformat
        head, _, rest = stamp.replace("Z", "+00:00").partition(".")
        fraction, zone = (rest[:6], rest[rest.index("+"):]) if "+" in rest else (rest[:6], "+00:00")
        moment = datetime.fromisoformat(f"{head}.{fraction or '0'}{zone}" if rest else head)
    except ValueError:
        return None
    if not _premarket(moment, today):
        return None
    return {"price": price, "as_of": moment.astimezone(NEW_YORK).isoformat(), "source": "alpaca_iex"}


class BadSymbol(ValueError):
    """The snapshot endpoint refuses a whole batch when one symbol is invalid (HTTP 400)."""


def alpaca_snapshot_quotes(tickers: list[str], fetch: Callable[[list[str]], dict[str, Any]], *,
                           today: date | None = None, out: dict[str, dict[str, Any]] | None = None,
                           batch: int = 100) -> dict[str, dict[str, Any]]:
    """Pre-market prices from Alpaca's multi-symbol snapshots (IEX feed): one request per 100 symbols. A batch
    refused for one bad symbol is split in halves until the bad one is isolated and left unknown."""
    today = today or datetime.now(NEW_YORK).date()
    out = {} if out is None else out

    def take(chunk: list[str]) -> None:
        try:
            snapshots = fetch(chunk)
        except BadSymbol:
            if len(chunk) > 1:
                take(chunk[: len(chunk) // 2])
                take(chunk[len(chunk) // 2:])
            return
        for ticker in chunk:
            quote = premarket_trade(snapshots.get(ticker), today)
            if quote is not None:
                out[ticker] = quote

    for offset in range(0, len(tickers), batch):
        take(tickers[offset:offset + batch])
    return out


def alpaca_snapshot_fetcher(key_id: str, secret: str, timeout: float = 15.0) -> Callable[[list[str]], dict[str, Any]]:
    """Read-only market data (no account, no orders): GET data.alpaca.markets/v2/stocks/snapshots on the IEX feed."""
    from urllib import error, parse, request

    def fetch(symbols: list[str]) -> dict[str, Any]:
        query = parse.urlencode({"symbols": ",".join(symbols), "feed": "iex"})
        req = request.Request(f"https://data.alpaca.markets/v2/stocks/snapshots?{query}",
                              headers={"APCA-API-KEY-ID": key_id, "APCA-API-SECRET-KEY": secret,
                                       "Accept": "application/json"})
        try:
            with request.urlopen(req, timeout=timeout) as response:
                payload = json.loads(response.read() or b"{}")
        except error.HTTPError as exc:
            if exc.code == 400:
                raise BadSymbol(str(exc)) from exc
            raise
        return payload.get("snapshots", payload) if isinstance(payload, dict) else {}
    return fetch


def yfinance_quotes(tickers: list[str], *, today: date | None = None, out: dict[str, dict[str, Any]] | None = None,
                    batch: int = 25) -> dict[str, dict[str, Any]]:
    """Last pre-market trade of today (New York) per ticker from yfinance 1-minute bars; absent when none.

    Yahoo limits requests per IP and then answers "possibly delisted; no price data found" for valid symbols
    (yfinance issues #359, #2422, #2431): small batches, and one more try for the symbols a batch missed."""
    import pandas as pd
    import yfinance as yf

    today = today or datetime.now(NEW_YORK).date()
    out = {} if out is None else out

    def take(chunk: list[str]) -> list[str]:
        names = {phs._yahoo_symbol(t): t for t in chunk}
        raw = yf.download(list(names), period="1d", interval="1m", prepost=True, auto_adjust=False,
                          group_by="ticker", progress=False, threads=False, timeout=20)
        missed = []
        for yahoo, ticker in names.items():
            try:
                frame = raw[yahoo] if isinstance(raw.columns, pd.MultiIndex) else raw
                closes = frame["Close"].dropna()
            except (KeyError, AttributeError, TypeError):
                missed.append(ticker)
                continue
            if closes.empty:
                missed.append(ticker)
                continue
            index = closes.index.tz_convert(NEW_YORK) if closes.index.tz is not None else closes.index.tz_localize(NEW_YORK)
            mask = [_premarket(ts.to_pydatetime(), today) for ts in index]
            selected = closes[mask]
            if not selected.empty:
                out[ticker] = {"price": float(selected.iloc[-1]), "as_of": index[mask][-1].isoformat(),
                               "source": "yfinance"}
        return missed

    retry: list[str] = []
    for offset in range(0, len(tickers), batch):
        retry += take(tickers[offset:offset + batch])
    for offset in range(0, len(retry), batch):
        take(retry[offset:offset + batch])
    return out


def layered_quotes(sources: list[tuple[str, Callable[..., Any]]]) -> Callable[[list[str]], dict[str, dict[str, Any]]]:
    """Each source only for the tickers the earlier ones did not answer. ``collected`` keeps what arrived, so a
    budget that runs out keeps the answers already in."""
    collected: dict[str, dict[str, Any]] = {}

    def quotes(tickers: list[str]) -> dict[str, dict[str, Any]]:
        for name, source in sources:
            missing = [t for t in tickers if t not in collected]
            if not missing:
                break
            try:
                source(missing, out=collected)
            except Exception as exc:  # noqa: BLE001 - the next source tries; the rest stays unknown
                print(f"[PREOPEN] quote source {name} failed: {type(exc).__name__}", flush=True)
        return collected

    quotes.collected = collected  # type: ignore[attr-defined]
    return quotes


def sec_filings_fetcher(user_agent: str, limiter: Any = None) -> Filings:
    from urllib import request

    def fetch(cik: int) -> list[dict[str, Any]]:
        if limiter is not None and not limiter.permit("sec", max_interval_wait_seconds=1.0).permitted:
            raise RuntimeError("SEC rate limit")
        req = request.Request(f"https://data.sec.gov/submissions/CIK{cik:010d}.json",
                              headers={"User-Agent": user_agent, "Accept-Encoding": "identity"})
        with request.urlopen(req, timeout=30) as response:
            return recent_filings(json.loads(response.read()))
    return fetch


def with_deadline(function: Callable[[], Any], seconds: float) -> Any:
    """Run ``function`` in a daemon thread; TimeoutError if it is not done within ``seconds`` (it is abandoned)."""
    import threading

    box: dict[str, Any] = {}

    def target() -> None:
        try:
            box["value"] = function()
        except BaseException as exc:  # noqa: BLE001 - re-raised in the caller
            box["error"] = exc

    worker = threading.Thread(target=target, daemon=True)
    worker.start()
    worker.join(seconds)
    if worker.is_alive():
        global ABANDONED_WORK
        ABANDONED_WORK = True
        raise TimeoutError(f"no answer within {seconds:.0f} s")
    if "error" in box:
        raise box["error"]
    return box.get("value")


def run(payloads: dict[str, dict[str, Any]], *, quotes: Quotes, filings: Filings | None, ciks: dict[str, int],
        price_dir: Path = phs.DEFAULT_DIR, now: datetime | None = None,
        quotes_budget: float = QUOTES_BUDGET_SECONDS, filings_budget: float = FILINGS_BUDGET_SECONDS,
        priority: dict[str, list[str]] | None = None) -> dict[str, Any]:
    """The check. Each source has a time budget (2026-10-14: the check hung for 7.5 hours and the orders missed
    the entry window); what is not read in time stays unknown and the nightly decision is kept."""
    import time

    now = now or datetime.now(timezone.utc)
    tickers = sorted({str(r.get("ticker") or "").upper() for p in payloads.values() for r in p.get("results") or []
                      if isinstance(r, dict) and str(r.get("signal_verdict") or "").lower() == "candidate"} - {""})
    priority = priority or {}

    def first(names: list[str]) -> list[str]:  # what the last check left unknown is looked at first
        ahead = set(names)
        return [t for t in tickers if t in ahead] + [t for t in tickers if t not in ahead]

    started = time.monotonic()
    quote_order = first(priority.get("quotes") or [])
    try:
        quote_map = (with_deadline(lambda: quotes(quote_order), quotes_budget) if tickers else {}) or {}
        quote_error = None
    except Exception as exc:  # noqa: BLE001 - no quotes is unknown gaps, not a failed check
        quote_map = dict(getattr(quotes, "collected", {}) or {})  # the answers that arrived before the budget ran out
        quote_error = f"{type(exc).__name__}: {exc}"
    timings = {"quotes_seconds": round(time.monotonic() - started, 1), "quotes_budget_seconds": quotes_budget}
    print(f"[PREOPEN] quotes: {len(quote_map)}/{len(tickers)} in {timings['quotes_seconds']} s"
          + (f" ({quote_error})" if quote_error else ""), flush=True)
    filings_started = time.monotonic()
    filings_cut = 0
    views: dict[str, dict[str, Any]] = {}
    for ticker in first(priority.get("filings") or []):
        reference = last_close(ticker, price_dir)
        if reference[1] is not None and (now.astimezone(NEW_YORK).date() - reference[1]).days > MAX_CLOSE_AGE_DAYS:
            reference = (None, None)  # a close older than the last few sessions is no reference for today's gap
        events: list[dict[str, Any]] | None = None
        cik = ciks.get(ticker)
        if filings is not None and cik is not None and reference[1] is not None:
            left = filings_budget - (time.monotonic() - filings_started)
            if left <= 0:
                filings_cut += 1  # over budget: unread, unknown
            else:
                try:
                    events = content_events_since(with_deadline(lambda c=cik: filings(c), min(left, 60.0)),
                                                  close_moment(reference[1]))
                except Exception:  # noqa: BLE001 - unread filings stay unknown for this ticker
                    events = None
        views[ticker] = {"quote": quote_map.get(ticker), "reference": reference, "events": events}
    timings.update({"filings_seconds": round(time.monotonic() - filings_started, 1),
                    "filings_budget_seconds": filings_budget, "filings_not_read_over_budget": filings_cut})
    print(f"[PREOPEN] filings in {timings['filings_seconds']} s, {filings_cut} left unread over budget", flush=True)
    checked_at = now.isoformat()
    timings["quote_sources"] = {name: sum(1 for q in quote_map.values() if q.get("source") == name)
                                for name in sorted({str(q.get("source")) for q in quote_map.values()})}
    pending = {"quotes": [t for t in tickers if views[t]["quote"] is None],
               "filings": [t for t in tickers if views[t]["events"] is None and ciks.get(t) is not None]}
    outputs = {agent: apply(payload, views, checked_at=checked_at) for agent, payload in payloads.items()}
    rows = []
    for agent, payload in outputs.items():
        for row in payload["results"]:
            if isinstance(row, dict) and row.get("preopen_check"):
                rows.append({"agent": agent, "ticker": row.get("ticker"), "playbook": row.get("playbook_name"),
                             "direction": row.get("trade_direction"), "score": row.get("signal_score"),
                             **{k: row["preopen_check"][k] for k in ("decision", "gap_pct", "atr_14_pct",
                                                                    "demote_reasons", "notes")}})
    counts = {"candidates": len(rows), "kept": sum(r["decision"] == "kept" for r in rows),
              "demoted_to_watch": sum(r["decision"] == "demoted_to_watch" for r in rows),
              "symbols": len(tickers), "with_premarket_price": sum(1 for t in tickers if quote_map.get(t)),
              "with_filings_read": sum(1 for t in tickers if views[t]["events"] is not None)}
    report = {"schema": "ecosystem.preopen_check_report.v1", "checked_at": checked_at,
              "nightly_run_id": next(iter(payloads.values())).get("run_id") if payloads else None,
              "gap_atr_multiple": GAP_ATR_MULTIPLE, "counts": counts, "quote_error": quote_error, "timings": timings,
              "pending_unknowns": pending, "rows": rows}
    return {"outputs": outputs, "report": report}


def render_md(report: dict[str, Any]) -> str:
    c = report["counts"]
    lines = ["# Έλεγχος πριν το άνοιγμα", "",
             f"Run της νύχτας: {report['nightly_run_id']} · έλεγχος: {report['checked_at']}", "",
             f"Candidates: {c['candidates']} · έμειναν: {c['kept']} · σε watch: {c['demoted_to_watch']} · "
             f"με τιμή pre-market: {c['with_premarket_price']}/{c['symbols']} · "
             f"με νέες καταθέσεις διαβασμένες: {c['with_filings_read']}/{c['symbols']}", ""]
    if report.get("quote_error"):
        lines += [f"Οι τιμές pre-market δεν διαβάστηκαν: {report['quote_error']}", ""]
    lines += ["| trader | μετοχή | playbook | κατεύθυνση | gap % | ATR % | απόφαση | λόγος |", "|---|---|---|---|---|---|---|---|"]
    for r in sorted(report["rows"], key=lambda r: (r["decision"] != "demoted_to_watch", r["agent"], -(r["score"] or 0))):
        why = " ".join(r["demote_reasons"] or r["notes"]) or "-"
        gap = "άγνωστο" if r["gap_pct"] is None else f"{r['gap_pct']:+.2f}"
        atr = "άγνωστο" if r["atr_14_pct"] is None else f"{r['atr_14_pct']:.2f}"
        lines.append(f"| {r['agent']} | {r['ticker']} | {r['playbook']} | {r['direction']} | {gap} | {atr} | "
                     f"{r['decision']} | {why} |")
    return "\n".join(lines) + "\n"


def keep_awake() -> None:
    """Ask Windows to keep the computer awake while this process runs: started by a wake timer, it would otherwise
    be put back to sleep after a few unattended minutes. Released automatically when the process exits."""
    try:
        import ctypes

        ctypes.windll.kernel32.SetThreadExecutionState(0x80000000 | 0x00000001)  # ES_CONTINUOUS | ES_SYSTEM_REQUIRED
    except (AttributeError, OSError):
        pass  # not Windows: nothing to do


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", type=Path, default=ROOT, help="project folder with output/, data/ and cache/")
    parser.add_argument("--out-dir", type=Path, help="default: <root>/output/preopen")
    parser.add_argument("--no-filings", action="store_true", help="skip the SEC filings read (testing)")
    parser.add_argument("--quote-sources", default="alpaca_iex,yfinance",
                        help="pre-market price sources in order (alpaca_iex: read-only Alpaca market data)")
    args = parser.parse_args(argv)
    keep_awake()
    root = args.root
    args.out_dir = args.out_dir or root / "output" / "preopen"
    tickers_path = root / "cache" / "sec_company_tickers.json"
    payloads = {agent: json.loads((root / "output" / f"{agent}_strategy_latest.json").read_text(encoding="utf-8-sig"))
                for agent in AGENTS}
    filings: Filings | None = None
    ciks: dict[str, int] = {}
    from research_workflow_orchestrator import load_env

    load_env(root / ".env")  # SEC_USER_AGENT and the Alpaca market-data keys, never printed
    sources: list[tuple[str, Callable[..., Any]]] = []
    for name in [n.strip() for n in args.quote_sources.split(",") if n.strip()]:
        if name == "alpaca_iex":
            key_id, secret = os.getenv("ALPACA_API_KEY_ID", "").strip(), os.getenv("ALPACA_API_SECRET_KEY", "").strip()
            if key_id and secret:
                fetch = alpaca_snapshot_fetcher(key_id, secret)
                sources.append((name, lambda tickers, out, f=fetch: alpaca_snapshot_quotes(tickers, f, out=out)))
            else:
                print("[PREOPEN] no Alpaca market-data keys; alpaca_iex skipped", flush=True)
        elif name == "yfinance":
            sources.append((name, lambda tickers, out: yfinance_quotes(tickers, out=out)))
    pending_path = args.out_dir / "pending_unknowns.json"
    priority = {}
    if pending_path.is_file():
        try:
            priority = json.loads(pending_path.read_text(encoding="utf-8"))
        except ValueError:
            priority = {}
    if not args.no_filings:
        from fundamental_control_plane_batch import _ticker_to_cik
        from provider_rate_limiter import SharedProviderRateLimiter

        agent = os.getenv("SEC_USER_AGENT", "").strip()
        if agent and tickers_path.is_file():
            ciks = _ticker_to_cik(tickers_path)
            filings = sec_filings_fetcher(agent, SharedProviderRateLimiter())
    result = run(payloads, quotes=layered_quotes(sources), filings=filings, ciks=ciks,
                 price_dir=root / "data" / "price_history", priority=priority)
    args.out_dir.mkdir(parents=True, exist_ok=True)
    pending_path.write_text(json.dumps(result["report"]["pending_unknowns"], indent=2), encoding="utf-8")
    for agent, payload in result["outputs"].items():
        (args.out_dir / f"{agent}_strategy_preopen_latest.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    report = result["report"]
    (args.out_dir / "preopen_check_latest.json").write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str),
                                                           encoding="utf-8")
    (args.out_dir / "preopen_check_latest.md").write_text(render_md(report), encoding="utf-8")
    history = args.out_dir / "history"
    history.mkdir(exist_ok=True)
    (history / f"preopen_check_{datetime.now(NEW_YORK):%Y%m%d}.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(json.dumps({**report["counts"], **report["timings"]}), flush=True)
    if ABANDONED_WORK:  # a hung source thread must not keep the process (and the afternoon session) waiting
        sys.stdout.flush()
        os._exit(0)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
