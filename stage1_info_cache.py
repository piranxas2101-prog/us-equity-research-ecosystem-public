"""Babis stage 1: keep yfinance company info between runs instead of asking every day.

Filing-based fields (margins, growth, ROE, cash, debt, ratios of the balance sheet)
change only after a company reports. Price-based fields (market cap, enterprise
value, P/E, P/S, P/B, PEG) are recomputed every day from the stored value and the
day's close, which is exact while the underlying per-share numbers are unchanged:

    value_today = stored_value * close_today / reference_price_at_storage
    (enterprise value: stored EV + stored market cap * (ratio - 1))

In ``reuse`` mode a stored copy is used only when it is at most ``max_age_days``
(7, owner-approved) old, the SEC filing calendar covers the window without a gap,
and the company filed no financial form (8-K included, since earnings are
announced there) from five days before the storage (yfinance updates with a lag)
up to the previous business day. Otherwise yfinance is asked, as before.

In ``reuse`` mode stage 1 also asks news only for the watchlist (the previous
shortlist), and skips company info for instruments that are not companies.
Modes: ``off``, ``populate`` (store, change nothing), ``reuse``.

Staggered refresh (owner-approved 2026-10-08): every symbol has a fixed refresh day in the ``max_age_days`` cycle,
and on that day a copy at least a day old is asked again. About 1/7 of the copies are renewed each night, so they
never all expire together (on 2026-10-07 nearly every copy was 8 days old and stage 1 asked yfinance for all of them,
1.8 h instead of ~0.5 h).
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
from typing import Any

from companyfacts_cache import eastern_today, effective_mode, previous_business_day
from monitor_core import yahoo_symbol

ROOT = Path(__file__).resolve().parent
MODES = ("off", "populate", "reuse")
PRICE_SCALED_FIELDS = ("marketCap", "forwardPE", "trailingPE", "priceToSalesTrailing12Months",
                       "priceToBook", "pegRatio", "trailingPegRatio")
STORED_FIELDS = PRICE_SCALED_FIELDS + (
    "enterpriseValue", "totalRevenue", "grossMargins", "operatingMargins", "profitMargins", "revenueGrowth",
    "earningsGrowth", "returnOnEquity", "returnOnAssets", "totalCash", "totalDebt", "debtToEquity",
    "freeCashflow", "operatingCashflow", "currentRatio", "quickRatio", "sharesOutstanding", "netIncomeToCommon",
    "longName", "shortName", "sector", "industry", "currency",
    # short interest and float for the SHORT gates (alpaca_short_gate_data.py, owner's choice B 2026-10-24)
    "shortPercentOfFloat", "floatShares")
FILING_LAG_DAYS = 5
CARRY_MAX_DAYS = 30  # a field missing from a fresh answer keeps the stored value at most this long
POPULATE_KEEP_DAYS = 6  # a populate copy is refreshed only when older (reuse accepts copies up to 7 days)


def reference_price(info: dict[str, Any]) -> float | None:
    for key in ("currentPrice", "regularMarketPrice", "previousClose"):
        try:
            value = float(info.get(key))
        except (TypeError, ValueError):
            continue
        if value > 0:
            return value
    return None


def scale_to_price(stored: dict[str, Any], price_then: float, price_now: float) -> dict[str, Any]:
    ratio = price_now / price_then
    info = dict(stored)
    for key in PRICE_SCALED_FIELDS:
        if isinstance(info.get(key), (int, float)):
            info[key] = info[key] * ratio
    if isinstance(stored.get("enterpriseValue"), (int, float)) and isinstance(stored.get("marketCap"), (int, float)):
        info["enterpriseValue"] = stored["enterpriseValue"] + stored["marketCap"] * (ratio - 1)
    return info


class Stage1InfoCache:
    def __init__(self, directory: Path, filing_db: Path, cik_map: dict[str, int], *, mode: str = "off",
                 max_age_days: int = 7, news_watchlist: frozenset[str] = frozenset(),
                 today: date | None = None, stagger_refresh: bool = True) -> None:
        if mode not in MODES:
            raise ValueError(f"Unknown stage-1 info cache mode: {mode}")
        self.directory, self.filing_db, self.cik_map = Path(directory), Path(filing_db), cik_map
        self.mode, self.max_age_days, self.news_watchlist, self._today = mode, int(max_age_days), news_watchlist, today
        self.stagger_refresh = stagger_refresh

    def refresh_day(self, symbol: str) -> int:
        """The symbol's fixed day in the refresh cycle (stable across runs and machines)."""
        return int(hashlib.sha256(symbol.upper().encode("utf-8")).hexdigest(), 16) % max(1, self.max_age_days)

    def scheduled_refresh(self, symbol: str, fetched: date) -> bool:
        return (self.stagger_refresh and (self.today - fetched).days >= 1
                and self.today.toordinal() % max(1, self.max_age_days) == self.refresh_day(symbol))

    @classmethod
    def from_config(cls, config: dict[str, Any], cik_map: dict[str, int], *, root: Path = ROOT,
                    **overrides: Any) -> "Stage1InfoCache":
        cfg = dict(((config.get("pipeline") or {}).get("stage1_info_cache")) or {})
        watchlist: set[str] = set()
        shortlist = root / str(cfg.get("news_watchlist_path", "output/babis_shortlist_latest.json"))
        try:
            payload = json.loads(shortlist.read_text(encoding="utf-8-sig"))
            watchlist = {str(row.get("ticker") or row.get("symbol") or "").upper()
                         for row in payload.get("shortlist") or [] if isinstance(row, dict)} - {""}
        except (OSError, ValueError):
            pass
        return cls(root / str(cfg.get("dir", "cache/stage1_info")), root / "data" / "research_registry.db", cik_map,
                   mode=effective_mode("stage1_info", str(cfg.get("mode", "off"))),
                   max_age_days=int(cfg.get("max_age_days", 7)),
                   news_watchlist=frozenset(watchlist), **overrides)

    @property
    def today(self) -> date:
        return self._today or eastern_today()

    @property
    def lean(self) -> bool:
        """Whether stage 1 may skip requests (news outside the watchlist, info of non-companies)."""
        return self.mode == "reuse"

    def wants_news(self, symbol: str) -> bool:
        return not self.lean or symbol.upper() in self.news_watchlist

    def _path(self, symbol: str) -> Path:
        safe = "".join(ch if ch.isalnum() else "_" for ch in symbol.upper())
        return self.directory / f"{safe}.json"

    def store(self, symbol: str, info: dict[str, Any], carried: dict[str, str] | None = None) -> bool:
        if self.mode == "off":
            return False
        price = reference_price(info)
        if price is None:
            return False  # without the price the ratios were computed at, nothing can be rescaled
        if self.mode == "populate" and self._kept_for_shadow(symbol):
            return False
        record = {"schema": "ecosystem.stage1_info_cache.v1", "symbol": symbol.upper(),
                  "fetched_at": datetime.now(timezone.utc).isoformat(), "fetched_date_et": self.today.isoformat(),
                  "reference_price": price, "info": {k: info.get(k) for k in STORED_FIELDS if info.get(k) is not None}}
        if carried:
            record["carried_fields"] = dict(carried)  # each carried field keeps the date it was really read
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
            path = self._path(symbol)
            temporary = path.with_name(path.name + ".tmp")
            temporary.write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
            temporary.replace(path)
        except OSError:
            return False
        return True

    def fill_gaps(self, symbol: str, info: dict[str, Any]) -> tuple[dict[str, Any], dict[str, str]]:
        """A fresh yfinance answer that lacks a field the stored copy had keeps the stored value (owner's decision
        2026-10-16: on 2026-10-16 Yahoo answered BLK and JNJ, and 21 more, without ROE and current ratio, and the
        complete copy was overwritten). Carried only when the stored copy is at most CARRY_MAX_DAYS old and the
        company filed no financial report since it was stored - otherwise the field stays unknown. Price-scaled
        fields are rescaled to today's reference price. Returns the merged info and {field: stored date}."""
        if self.mode == "off":
            return info, {}
        try:
            record = json.loads(self._path(symbol).read_text(encoding="utf-8"))
            fetched = date.fromisoformat(record["fetched_date_et"])
            price_then = float(record["reference_price"])
            stored = record.get("info") or {}
        except (OSError, ValueError, KeyError, TypeError):
            return info, {}
        if (self.today - fetched).days < 0:
            return info, {}
        origin = {k: record.get("carried_fields", {}).get(k, record["fetched_date_et"]) for k in STORED_FIELDS}
        missing = [k for k in STORED_FIELDS if info.get(k) is None and stored.get(k) is not None
                   and 0 <= (self.today - date.fromisoformat(origin[k])).days <= CARRY_MAX_DAYS]
        if not missing:
            return info, {}
        cik = self.cik_map.get(symbol.upper())
        filings = None if not cik else self._filings(cik, since=fetched - timedelta(days=FILING_LAG_DAYS + 1),
                                                     until=previous_business_day(self.today))
        if filings is None or filings:
            return info, {}  # a new report (or an unreadable calendar) may have changed the value: unknown
        price_now = reference_price(info)
        rescaled = scale_to_price(stored, price_then, price_now) if price_now else stored
        merged, carried = dict(info), {}
        for key in missing:
            if key in PRICE_SCALED_FIELDS and not price_now:
                continue
            merged[key] = rescaled.get(key)
            carried[key] = origin[key]
        return merged, carried

    def invalidate(self, symbol: str) -> bool:
        """Drop one stored copy so the next run asks yfinance for that company (the refresh gate found it drifted)."""
        try:
            self._path(symbol).unlink()
        except FileNotFoundError:
            return False
        except OSError:
            return False
        return True

    def _kept_for_shadow(self, symbol: str) -> bool:
        """In populate mode a copy younger than POPULATE_KEEP_DAYS is left alone so it can age to the
        3 days the refresh gate needs (overwriting it every night would restart that clock forever)."""
        try:
            fetched = date.fromisoformat(json.loads(self._path(symbol).read_text(encoding="utf-8"))["fetched_date_et"])
        except (OSError, ValueError, KeyError):
            return False
        return 0 <= (self.today - fetched).days < POPULATE_KEEP_DAYS  # a future-dated copy is not trusted

    def decide(self, symbol: str, close_today: float | None) -> tuple[dict[str, Any] | None, dict[str, Any]]:
        """(info rescaled to today's close, provenance) when reusable, else (None, {"reason": ...})."""
        if self.mode != "reuse":
            return None, {"reason": f"cache_mode_{self.mode}"}
        if symbol.upper() in getattr(self, "always_fresh", frozenset()):
            return None, {"reason": "open_valuation_gap"}  # valuation_gaps.py: ask afresh until the value is found
        try:
            record = json.loads(self._path(symbol).read_text(encoding="utf-8"))
            fetched = date.fromisoformat(record["fetched_date_et"])
            price_then = float(record["reference_price"])
        except (OSError, ValueError, KeyError, TypeError):
            return None, {"reason": "no_cached_copy"}
        if (self.today - fetched).days > self.max_age_days:
            return None, {"reason": "cached_copy_older_than_max_age"}
        if self.scheduled_refresh(symbol, fetched):
            return None, {"reason": "scheduled_refresh"}
        if not close_today or close_today <= 0 or price_then <= 0:
            return None, {"reason": "no_price_to_rescale"}
        cik = self.cik_map.get(symbol.upper())
        if not cik:
            return None, {"reason": "no_sec_identity_to_check_filings"}
        filings = self._filings(cik, since=fetched - timedelta(days=FILING_LAG_DAYS + 1),
                                until=previous_business_day(self.today))
        if filings is None:
            return None, {"reason": "filing_calendar_gap"}
        if filings:
            return None, {"reason": "financial_filing_since_storage", "filings": filings[:3]}
        provenance = {"source": "cached_info_price_scaled", "fetched_at": record["fetched_at"],
                      "fetched_date_et": record["fetched_date_et"], "reference_price": price_then,
                      "close_today": close_today, "price_scaled_fields": list(PRICE_SCALED_FIELDS) + ["enterpriseValue"]}
        return scale_to_price(record["info"], price_then, close_today), provenance

    def _filings(self, cik: int, *, since: date, until: date) -> list[dict[str, Any]] | None:
        from research_registry.sec_filing_index import filed_since

        if not self.filing_db.is_file():
            return None
        try:
            db = sqlite3.connect(f"file:{self.filing_db.resolve().as_posix()}?mode=ro", uri=True, timeout=10)
        except sqlite3.Error:
            return None
        try:
            return filed_since(db, cik, since, until=until, category="financial")
        except sqlite3.Error:
            return None
        finally:
            db.close()


MIN_SHADOW_AGE_DAYS = 3


def shadow(cache: Stage1InfoCache, symbols: list[str], *, tolerance: float = 0.02,
           min_age_days: int = MIN_SHADOW_AGE_DAYS, limit: int = 50) -> dict[str, Any]:
    """For symbols the cache would reuse, compare the rescaled copy with a fresh yfinance answer.

    Only copies at least ``min_age_days`` old are compared, oldest first (a copy from hours ago
    is trivially equal to a fresh answer and proves nothing about reuse across days).
    """
    import statistics

    import yfinance as yf

    reuse = Stage1InfoCache(cache.directory, cache.filing_db, cache.cik_map, mode="reuse",
                            max_age_days=cache.max_age_days, today=cache._today, stagger_refresh=False)
    diffs: dict[str, list[float]] = {}
    drifted: dict[str, list[str]] = {}
    compared, skipped, too_recent = 0, {}, 0
    aged = []
    for symbol in symbols:
        try:
            fetched = date.fromisoformat(json.loads(reuse._path(symbol).read_text(encoding="utf-8"))["fetched_date_et"])
        except (OSError, ValueError, KeyError):
            continue
        age = (reuse.today - fetched).days
        if min_age_days <= age <= reuse.max_age_days:  # older copies cannot be reused anyway: do not spend the sample
            aged.append((fetched, symbol))
        elif age > reuse.max_age_days:
            skipped["cached_copy_older_than_max_age"] = skipped.get("cached_copy_older_than_max_age", 0) + 1
        else:
            too_recent += 1
    for _, symbol in sorted(aged)[: max(0, limit)]:
        fresh = yf.Ticker(yahoo_symbol(symbol)).get_info() or {}
        price = reference_price(fresh)
        cached, info = reuse.decide(symbol, price)
        if cached is None:
            skipped[info["reason"]] = skipped.get(info["reason"], 0) + 1
            continue
        compared += 1
        for key in STORED_FIELDS:
            a, b = cached.get(key), fresh.get(key)
            if isinstance(a, (int, float)) and isinstance(b, (int, float)) and b:
                diffs.setdefault(key, []).append(abs(a - b) / abs(b))
                if abs(a - b) / abs(b) > tolerance:
                    drifted.setdefault(symbol, []).append(key)
    fields = {key: {"median": round(statistics.median(v), 4), "max": round(max(v), 4),
                    "within_tolerance": round(sum(x <= tolerance for x in v) / len(v), 3), "n": len(v)}
              for key, v in sorted(diffs.items())}
    return {"compared": compared, "skipped": skipped, "too_recent": too_recent, "tolerance": tolerance, "fields": fields,
            "drifted": drifted}


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--config", type=Path, default=ROOT / "scanner_config.json")
    parser.add_argument("--limit", type=int, default=50)
    parser.add_argument("--output", type=Path, default=ROOT / "output" / "stage1_info_shadow_latest.json")
    args = parser.parse_args(argv)
    from us_equity_monitor import load_sec_ticker_cik_map

    config = json.loads(args.config.read_text(encoding="utf-8-sig"))
    cache = Stage1InfoCache.from_config(config, load_sec_ticker_cik_map(config.get("data_sources") or {}))
    symbols = [p.stem for p in cache.directory.glob("*.json")]
    result = shadow(cache, symbols, limit=args.limit)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"compared": result["compared"], "skipped": result["skipped"]}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
