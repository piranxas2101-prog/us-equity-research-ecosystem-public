"""Daily price history for every company Babis scans, kept on disk for backtests and research.

The nightly scan downloads ~420 days of prices per company and keeps none of it, so nothing could be backtested.
This store keeps five years of daily OHLCV per symbol (``data/price_history/<SYMBOL>.csv.gz``), downloaded once and
then extended by the new days only. Prices come from yfinance in batches (raw, unadjusted closes plus the adjusted
close, as yfinance reports them). A symbol yfinance does not answer for stays absent and is listed as such; nothing is
filled in. Research only: no agent reads the store during the nightly run.
"""
from __future__ import annotations

import argparse
from datetime import date, datetime, timedelta, timezone
import json
from pathlib import Path
import time
from typing import Any, Callable

import pandas as pd

ROOT = Path(__file__).resolve().parent
DEFAULT_DIR = ROOT / "data" / "price_history"
DEFAULT_ROUTES = ROOT / "output" / "all_listed_universe_routes_latest.json"
BENCHMARKS = ("SPY", "QQQ", "IWM")
COLUMNS = ("Open", "High", "Low", "Close", "Adj Close", "Volume")
HISTORY_YEARS = 5
OVERLAP_DAYS = 7  # re-read the last week so late corrections replace earlier values


def company_symbols(routes_path: Path) -> list[str]:
    routes = json.loads(Path(routes_path).read_text(encoding="utf-8-sig")).get("routes") or []
    symbols = {str(r.get("ticker") or "").upper() for r in routes
               if r.get("company_fundamental_eligible") and not r.get("defer_market_scan")}
    return sorted(s for s in symbols if s) + list(BENCHMARKS)


def _path(directory: Path, symbol: str) -> Path:
    safe = "".join(ch if ch.isalnum() else "_" for ch in symbol.upper())
    return directory / f"{safe}.csv.gz"


def load(symbol: str, directory: Path = DEFAULT_DIR) -> pd.DataFrame | None:
    """The stored history of one symbol (index: session date), or None."""
    path = _path(directory, symbol)
    if not path.is_file():
        return None
    frame = pd.read_csv(path, index_col="Date", parse_dates=["Date"])
    return frame if not frame.empty else None


def _last_date(symbol: str, directory: Path) -> date | None:
    frame = load(symbol, directory)
    return None if frame is None else frame.index.max().date()


def _yahoo_symbol(symbol: str) -> str:
    return symbol.replace(".", "-").replace("/", "-")


def _download(symbols: list[str], start: date) -> dict[str, pd.DataFrame]:
    import yfinance as yf

    tickers = {_yahoo_symbol(s): s for s in symbols}
    raw = yf.download(list(tickers), start=start.isoformat(), interval="1d", auto_adjust=False, actions=False,
                      group_by="ticker", progress=False, threads=True, timeout=60)
    out: dict[str, pd.DataFrame] = {}
    if raw is None or raw.empty:
        return out
    for yahoo, symbol in tickers.items():
        try:
            frame = raw[yahoo] if isinstance(raw.columns, pd.MultiIndex) else raw
        except KeyError:
            continue
        frame = frame[[c for c in COLUMNS if c in frame.columns]].dropna(how="all")
        if not frame.empty:
            frame.index = pd.to_datetime(frame.index).tz_localize(None).normalize()
            frame.index.name = "Date"
            out[symbol] = frame
    return out


def _consistent(old: pd.DataFrame | None, new: pd.DataFrame) -> bool:
    """False when the overlapping days disagree by more than 2% (a split or a re-based series): refetch everything."""
    if old is None:
        return True
    shared = old.index.intersection(new.index)
    if len(shared) == 0:
        return True
    a, b = old.loc[shared, "Close"].astype(float), new.loc[shared, "Close"].astype(float)
    return bool(((a - b).abs() / b.abs()).max() <= 0.02)


def _merge(old: pd.DataFrame | None, new: pd.DataFrame) -> pd.DataFrame:
    if old is None:
        return new.sort_index()
    combined = pd.concat([old[~old.index.isin(new.index)], new])
    return combined.sort_index()


def update(symbols: list[str], directory: Path = DEFAULT_DIR, *, batch: int = 40, pause: float = 1.5,
           today: date | None = None, download: Callable[[list[str], date], dict[str, pd.DataFrame]] = _download,
           progress: Callable[[str], None] = print) -> dict[str, Any]:
    """Download what is missing: five years for new symbols, the last days for stored ones."""
    today = today or date.today()
    directory.mkdir(parents=True, exist_ok=True)
    groups: dict[date, list[str]] = {}
    for symbol in symbols:
        last = _last_date(symbol, directory)
        start = (today - timedelta(days=365 * HISTORY_YEARS + 2)) if last is None else last - timedelta(days=OVERLAP_DAYS)
        if last is not None and last >= today - timedelta(days=1):
            continue
        groups.setdefault(start, []).append(symbol)
    counts = {"symbols": len(symbols), "updated": 0, "no_answer": 0, "batches": 0}
    absent: list[str] = []
    started = time.perf_counter()
    for start, members in sorted(groups.items()):
        for offset in range(0, len(members), batch):
            chunk = members[offset:offset + batch]
            try:
                frames = download(chunk, start)
            except Exception as exc:  # noqa: BLE001 - one failed batch must not stop the rest
                progress(f"[BATCH FAILED] {chunk[0]}..{chunk[-1]}: {type(exc).__name__}")
                frames = {}
            counts["batches"] += 1
            for symbol in chunk:
                frame = frames.get(symbol)
                if frame is None:
                    counts["no_answer"] += 1
                    absent.append(symbol)
                    continue
                stored = load(symbol, directory)
                if not _consistent(stored, frame):
                    refetched = download([symbol], today - timedelta(days=365 * HISTORY_YEARS + 2)).get(symbol)
                    counts["refetched_after_split"] = counts.get("refetched_after_split", 0) + 1
                    stored, frame = None, (refetched if refetched is not None else frame)
                merged = _merge(stored, frame)
                merged.index.name = "Date"
                merged.to_csv(_path(directory, symbol), compression="gzip")
                counts["updated"] += 1
            progress(f"[BATCH {counts['batches']}] {counts['updated']} updated, {counts['no_answer']} without answer")
            time.sleep(pause)
    status = {"schema": "ecosystem.price_history_store.v1", "updated_at": datetime.now(timezone.utc).isoformat(),
              "counts": counts, "seconds": round(time.perf_counter() - started, 1), "no_answer": absent[:500]}
    (directory / "_status.json").write_text(json.dumps(status, indent=2), encoding="utf-8")
    return status


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--routes", type=Path, default=DEFAULT_ROUTES)
    parser.add_argument("--dir", type=Path, default=DEFAULT_DIR)
    parser.add_argument("--limit", type=int, default=0, help="only the first N symbols (testing)")
    parser.add_argument("--batch", type=int, default=40)
    parser.add_argument("--shortlist", type=Path, help="only the symbols of this Babis shortlist (nightly update)")
    args = parser.parse_args(argv)
    if args.shortlist:
        rows = json.loads(args.shortlist.read_text(encoding="utf-8-sig")).get("shortlist") or []
        symbols = sorted({str(r.get("ticker") or "").upper() for r in rows} - {""}) + list(BENCHMARKS)
    else:
        symbols = company_symbols(args.routes)
    if args.limit > 0:
        symbols = symbols[:args.limit] + [s for s in BENCHMARKS if s not in symbols[:args.limit]]
    status = update(symbols, args.dir, batch=args.batch)
    print(json.dumps({"counts": status["counts"], "seconds": status["seconds"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
