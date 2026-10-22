"""API-first, read-only evidence packets for Slack research conversations.

Configured sources are queried before Yahoo Finance. Credentials are read only
from the process environment, sent only to their provider, and never returned in
packets, errors, logs, or source URLs.
"""

from __future__ import annotations

import json
import hashlib
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from provider_rate_limiter import SharedProviderRateLimiter


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _number(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    if result != result or result in (float("inf"), float("-inf")):
        return None
    return round(result, 4)


def _status_for_http(code: int) -> str:
    return {401: "auth_failed", 403: "forbidden_or_plan", 429: "rate_limited"}.get(
        code, "http_error"
    )


class ApiFirstResearchProvider:
    """Build compact packets using SEC/Alpaca/Polygon/FMP before Yahoo."""

    def __init__(
        self,
        *,
        now: Callable[[], str] = utc_now,
        timeout_seconds: int = 12,
        cache_root: Path = Path("cache"),
        rate_limiter: SharedProviderRateLimiter | None = None,
    ) -> None:
        self.now = now
        self.timeout_seconds = timeout_seconds
        self.cache_root = cache_root
        self.rate_limiter = rate_limiter or SharedProviderRateLimiter()

    def _cache_path(self, provider: str, url: str) -> Path:
        # The URL may contain a credential query parameter. Only its digest is
        # used as a filename; the URL itself is never persisted.
        key = hashlib.sha256(f"{provider}\n{url}".encode("utf-8")).hexdigest()
        return self.cache_root / "provider_responses" / f"{key}.json"

    def _cached(self, provider: str, url: str) -> dict[str, Any] | None:
        ttl = {"sec": 3600, "fmp_quote": 300, "fmp_profile": 86400,
               "alpaca": 300, "polygon": 300, "benzinga": 300}.get(provider, 300)
        path = self._cache_path(provider, url)
        try:
            cached = json.loads(path.read_text(encoding="utf-8"))
            if time.time() - float(cached["cached_at_epoch"]) > ttl:
                return None
            result = dict(cached["result"])
            result["cache_status"] = "hit"
            return result
        except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
            return None

    def _store_cache(self, provider: str, url: str, result: dict[str, Any]) -> None:
        if result.get("status") != "ok":
            return
        path = self._cache_path(provider, url)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps({"cached_at_epoch": time.time(), "result": result},
                                        ensure_ascii=False), encoding="utf-8")
        temporary.replace(path)

    def _json(
        self,
        provider: str,
        url: str,
        *,
        headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        cached = self._cached(provider, url)
        if cached is not None:
            return cached
        decision = self.rate_limiter.permit(provider, max_interval_wait_seconds=3.5)
        if not decision.permitted:
            return {
                "provider": provider,
                "status": decision.reason_code,
                "next_eligible_at": decision.next_eligible_at,
                "rate_policy_version": self.rate_limiter.policy_version,
            }
        safe_headers = {
            "Accept": "application/json",
            "User-Agent": "research-gateway/2.0",
        }
        safe_headers.update(headers or {})
        request = urllib.request.Request(url, headers=safe_headers, method="GET")
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                result = {
                    "provider": provider,
                    "status": "ok",
                    "data": json.loads(response.read().decode("utf-8")),
                }
                self.rate_limiter.record_result(provider, "ok")
                self._store_cache(provider, url, result)
                return result
        except urllib.error.HTTPError as exc:
            status = _status_for_http(int(exc.code))
            self.rate_limiter.record_result(provider, status)
            return {"provider": provider, "status": status}
        except (OSError, urllib.error.URLError, TimeoutError):
            self.rate_limiter.record_result(provider, "timeout_or_network")
            return {"provider": provider, "status": "timeout_or_network"}
        except (json.JSONDecodeError, UnicodeDecodeError):
            self.rate_limiter.record_result(provider, "invalid_json")
            return {"provider": provider, "status": "invalid_json"}
        except Exception:
            self.rate_limiter.record_result(provider, "unexpected_error")
            return {"provider": provider, "status": "unexpected_error"}

    @staticmethod
    def _missing(provider: str) -> dict[str, Any]:
        return {"provider": provider, "status": "missing_configuration"}

    def _alpaca(self, symbol: str) -> dict[str, Any]:
        key = os.getenv("ALPACA_API_KEY_ID", "").strip()
        secret = os.getenv("ALPACA_API_SECRET_KEY", "").strip()
        if not key or not secret:
            return self._missing("alpaca")
        end = datetime.now(timezone.utc)
        start = end - timedelta(days=400)
        query = urllib.parse.urlencode(
            {
                "symbols": symbol,
                "timeframe": "1Day",
                "start": start.strftime("%Y-%m-%dT00:00:00Z"),
                "end": end.strftime("%Y-%m-%dT23:59:59Z"),
                "limit": 10000,
                "adjustment": "raw",
                "feed": "iex",
            }
        )
        result = self._json(
            "alpaca",
            f"https://data.alpaca.markets/v2/stocks/bars?{query}",
            headers={"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret},
        )
        if result.get("status") == "ok":
            payload = result.pop("data", {})
            result["bars"] = (payload.get("bars") or {}).get(symbol, [])
            result["feed"] = "iex"
        return result

    def _polygon(self, symbol: str) -> dict[str, Any]:
        key = os.getenv("POLYGON_API_KEY", "").strip()
        if not key:
            return self._missing("polygon")
        end = datetime.now(timezone.utc).date()
        start = end - timedelta(days=400)
        query = urllib.parse.urlencode(
            {"adjusted": "true", "sort": "asc", "limit": 5000, "apiKey": key}
        )
        result = self._json(
            "polygon",
            f"https://api.polygon.io/v2/aggs/ticker/{urllib.parse.quote(symbol)}/range/1/day/"
            f"{start.isoformat()}/{end.isoformat()}?{query}",
        )
        if result.get("status") == "ok":
            payload = result.pop("data", {})
            result["bars"] = payload.get("results") or []
        return result

    def _fmp(self, symbol: str) -> dict[str, Any]:
        key = os.getenv("FMP_API_KEY", "").strip()
        if not key:
            return self._missing("fmp")
        query = urllib.parse.urlencode({"symbol": symbol})
        headers = {"apikey": key}
        quote = self._json(
            "fmp_quote",
            f"https://financialmodelingprep.com/stable/quote?{query}",
            headers=headers,
        )
        profile = self._json(
            "fmp_profile",
            f"https://financialmodelingprep.com/stable/profile?{query}",
            headers=headers,
        )
        return {
            "provider": "fmp",
            "status": "ok" if quote.get("status") == "ok" else quote.get("status"),
            "quote": (quote.get("data") or [{}])[0] if quote.get("status") == "ok" else {},
            "profile": (profile.get("data") or [{}])[0]
            if profile.get("status") == "ok"
            else {},
            "profile_status": profile.get("status"),
        }

    def _sec(self, symbol: str) -> dict[str, Any]:
        user_agent = os.getenv("SEC_USER_AGENT", "").strip()
        if not user_agent:
            return self._missing("sec")
        headers = {"User-Agent": user_agent, "Accept": "application/json"}
        ticker_payload: Any = None
        cache_path = self.cache_root / "sec_company_tickers.json"
        if cache_path.is_file():
            try:
                ticker_payload = json.loads(cache_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError, UnicodeDecodeError):
                ticker_payload = None
        if ticker_payload is None:
            tickers = self._json(
                "sec", "https://www.sec.gov/files/company_tickers.json", headers=headers
            )
            if tickers.get("status") != "ok":
                return tickers
            ticker_payload = tickers.get("data")
        rows = ticker_payload.values() if isinstance(ticker_payload, dict) else ticker_payload or []
        match = next(
            (
                row
                for row in rows
                if isinstance(row, dict) and str(row.get("ticker", "")).upper() == symbol
            ),
            None,
        )
        if not match:
            return {"provider": "sec", "status": "ticker_not_found"}
        cik = int(match["cik_str"])
        submissions = self._json(
            "sec",
            f"https://data.sec.gov/submissions/CIK{cik:010d}.json",
            headers=headers,
        )
        if submissions.get("status") != "ok":
            return submissions
        recent = (((submissions.get("data") or {}).get("filings") or {}).get("recent") or {})
        filings: list[dict[str, Any]] = []
        accepted = {"10-K", "10-Q", "8-K", "20-F", "6-K"}
        for index, form in enumerate(recent.get("form") or []):
            if form not in accepted:
                continue
            accession = (recent.get("accessionNumber") or [])[index]
            document = (recent.get("primaryDocument") or [])[index]
            filings.append(
                {
                    "form": form,
                    "filing_date": (recent.get("filingDate") or [])[index],
                    "report_date": (recent.get("reportDate") or [])[index],
                    "url": "https://www.sec.gov/Archives/edgar/data/"
                    f"{cik}/{str(accession).replace('-', '')}/{document}",
                }
            )
            if len(filings) >= 5:
                break
        return {
            "provider": "sec",
            "status": "ok",
            "company_name": match.get("title"),
            "cik": cik,
            "filings": filings,
        }

    def _benzinga(self, symbol: str) -> dict[str, Any]:
        key = os.getenv("BENZINGA_API_KEY", "").strip()
        if not key:
            return self._missing("benzinga")
        query = urllib.parse.urlencode(
            {"tickers": symbol, "pageSize": 5, "displayOutput": "abstract"}
        )
        result = self._json(
            "benzinga",
            f"https://api.benzinga.com/api/v2/news?{query}",
            headers={"Authorization": f"token {key}"},
        )
        if result.get("status") == "ok":
            result["news"] = [
                {
                    "title": item.get("title"),
                    "url": item.get("url"),
                    "created": item.get("created"),
                }
                for item in (result.pop("data", []) or [])[:5]
                if isinstance(item, dict) and item.get("title")
            ]
        return result

    def _yahoo_fallback(self, symbol: str, *, need_price: bool, need_news: bool) -> dict[str, Any]:
        result: dict[str, Any] = {"provider": "yahoo_finance", "status": "fallback"}
        if need_price:
            try:
                import yfinance as yf  # type: ignore

                history = yf.Ticker(symbol).history(
                    period="1y", interval="1d", auto_adjust=False, timeout=self.timeout_seconds
                )
                if history is not None and not history.empty:
                    result["bars"] = [
                        {
                            "t": str(index),
                            "c": row.get("Close"),
                            "v": row.get("Volume"),
                        }
                        for index, row in history.iterrows()
                    ]
            except Exception:
                result["price_status"] = "unavailable"
        if need_news:
            try:
                url = "https://finance.yahoo.com/rss/headline?s=" + urllib.parse.quote(symbol)
                request = urllib.request.Request(
                    url, headers={"User-Agent": "Mozilla/5.0 research-gateway"}
                )
                with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                    root = ET.fromstring(response.read())
                result["news"] = [
                    {"title": (item.findtext("title") or "").strip(), "url": (item.findtext("link") or "").strip()}
                    for item in root.findall(".//item")[:5]
                    if (item.findtext("title") or "").strip()
                ]
            except Exception:
                result["news_status"] = "unavailable"
        return result

    @staticmethod
    def _technical(bars: list[dict[str, Any]], provider: str) -> dict[str, Any]:
        rows = [row for row in bars if _number(row.get("c")) is not None]
        closes = [_number(row.get("c")) for row in rows]
        closes = [value for value in closes if value is not None]
        if not closes:
            return {}
        volumes = [_number(row.get("v")) for row in rows[-20:]]
        volumes = [value for value in volumes if value is not None]
        raw_time = rows[-1].get("t")
        if isinstance(raw_time, (int, float)):
            raw_time = datetime.fromtimestamp(float(raw_time) / 1000, timezone.utc).isoformat()
        return {
            "provider": provider,
            "last": closes[-1],
            "as_of": str(raw_time),
            "return_1d_pct": _number((closes[-1] / closes[-2] - 1) * 100)
            if len(closes) >= 2
            else None,
            "return_1m_pct": _number((closes[-1] / closes[-22] - 1) * 100)
            if len(closes) >= 22
            else None,
            "sma_20": _number(sum(closes[-20:]) / 20) if len(closes) >= 20 else None,
            "sma_50": _number(sum(closes[-50:]) / 50) if len(closes) >= 50 else None,
            "high_52w": max(closes),
            "low_52w": min(closes),
            "avg_volume_20": _number(sum(volumes) / len(volumes)) if volumes else None,
        }

    def build(self, ticker: str) -> dict[str, Any]:
        symbol = ticker.upper().strip()
        if not re.fullmatch(r"[A-Z][A-Z0-9.-]{0,9}", symbol):
            raise ValueError(f"Invalid ticker: {ticker!r}")
        observed_at = self.now()
        tasks = {
            "sec": lambda: self._sec(symbol),
            "alpaca": lambda: self._alpaca(symbol),
            "polygon": lambda: self._polygon(symbol),
            "fmp": lambda: self._fmp(symbol),
            "benzinga": lambda: self._benzinga(symbol),
        }
        primary: dict[str, dict[str, Any]] = {}
        with ThreadPoolExecutor(max_workers=len(tasks)) as executor:
            futures = {executor.submit(call): name for name, call in tasks.items()}
            for future in as_completed(futures):
                name = futures[future]
                try:
                    primary[name] = future.result()
                except Exception:
                    primary[name] = {"provider": name, "status": "unexpected_error"}

        alpaca_bars = list(primary["alpaca"].get("bars") or [])
        polygon_bars = list(primary["polygon"].get("bars") or [])
        fmp_quote = dict(primary["fmp"].get("quote") or {})
        fmp_profile = dict(primary["fmp"].get("profile") or {})
        selected_bars = alpaca_bars or polygon_bars
        selected_provider = "alpaca" if alpaca_bars else "polygon"
        news = list(primary["benzinga"].get("news") or [])
        yahoo = self._yahoo_fallback(
            symbol,
            need_price=not selected_bars and _number(fmp_quote.get("price")) is None,
            need_news=not news,
        )
        if not selected_bars and yahoo.get("bars"):
            selected_bars = list(yahoo["bars"])
            selected_provider = "yahoo_finance"
        if not news:
            news = list(yahoo.get("news") or [])

        price = self._technical(selected_bars, selected_provider) if selected_bars else {}
        if not price and _number(fmp_quote.get("price")) is not None:
            price = {
                "provider": "fmp",
                "last": _number(fmp_quote.get("price")),
                "as_of": datetime.fromtimestamp(
                    float(fmp_quote.get("timestamp") or 0), timezone.utc
                ).isoformat()
                if fmp_quote.get("timestamp")
                else observed_at,
                "return_1d_pct": _number(fmp_quote.get("changePercentage")),
                "sma_50": _number(fmp_quote.get("priceAvg50")),
                "sma_200": _number(fmp_quote.get("priceAvg200")),
                "high_52w": _number(fmp_quote.get("yearHigh")),
                "low_52w": _number(fmp_quote.get("yearLow")),
                "avg_volume_20": _number(fmp_profile.get("averageVolume")),
            }

        cross_checks: dict[str, float] = {}
        if alpaca_bars:
            cross_checks["alpaca"] = _number(alpaca_bars[-1].get("c")) or 0.0
        if polygon_bars:
            cross_checks["polygon"] = _number(polygon_bars[-1].get("c")) or 0.0
        if _number(fmp_quote.get("price")) is not None:
            cross_checks["fmp"] = _number(fmp_quote.get("price")) or 0.0
        price["cross_checks"] = cross_checks

        fundamentals = {
            "company": fmp_quote.get("name") or fmp_profile.get("companyName"),
            "market_cap": _number(fmp_quote.get("marketCap") or fmp_profile.get("marketCap")),
            "sector": fmp_profile.get("sector"),
            "industry": fmp_profile.get("industry"),
            "beta": _number(fmp_profile.get("beta")),
            "last_dividend": _number(fmp_profile.get("lastDividend")),
            "employees": fmp_profile.get("fullTimeEmployees"),
            "exchange": fmp_quote.get("exchange") or fmp_profile.get("exchange"),
        }
        fundamentals = {key: value for key, value in fundamentals.items() if value is not None}
        filings = list(primary["sec"].get("filings") or [])

        warnings = [
            f"{name}:{result.get('status')}"
            for name, result in primary.items()
            if result.get("status") != "ok"
        ]
        positive_checks = [value for value in cross_checks.values() if value > 0]
        if len(positive_checks) >= 2:
            spread = (max(positive_checks) / min(positive_checks) - 1) * 100
            if spread > 1.0:
                warnings.append(f"price_cross_check_disagreement:{spread:.2f}%")
        if selected_provider == "yahoo_finance" or (news and not primary["benzinga"].get("news")):
            warnings.append("yahoo_finance_fallback_used")

        sources: list[dict[str, Any]] = []
        if primary["sec"].get("status") == "ok":
            sources.extend(
                {
                    "name": f"SEC {filing.get('form')} filed {filing.get('filing_date')}",
                    "url": filing.get("url"),
                    "provider": "sec",
                    "tier": "configured_primary",
                    "observed_at": observed_at,
                }
                for filing in filings
            )
        provider_links = {
            "alpaca": "https://data.alpaca.markets/",
            "polygon": f"https://polygon.io/stocks/{symbol}",
            "fmp": f"https://financialmodelingprep.com/financial-summary/{symbol}",
        }
        for name in ("alpaca", "polygon", "fmp"):
            if primary[name].get("status") == "ok":
                sources.append(
                    {
                        "name": f"{name.upper()} market/company data",
                        "url": provider_links[name],
                        "provider": name,
                        "tier": "configured_primary",
                        "observed_at": observed_at,
                    }
                )
        for item in news:
            if item.get("url"):
                sources.append(
                    {
                        "name": item.get("title"),
                        "url": item.get("url"),
                        "provider": "benzinga"
                        if primary["benzinga"].get("news")
                        else "yahoo_finance",
                        "tier": "configured_primary"
                        if primary["benzinga"].get("news")
                        else "fallback",
                        "observed_at": observed_at,
                    }
                )

        return {
            "ticker": symbol,
            "observed_at": observed_at,
            "status": "fresh" if price else "degraded",
            "price": price,
            "fundamentals": fundamentals,
            "filings": filings,
            "news": news,
            "provider_status": {name: value.get("status") for name, value in primary.items()},
            "rate_policy_version": self.rate_limiter.policy_version,
            "source_priority": ["sec", "alpaca", "polygon", "fmp", "benzinga", "yahoo_finance"],
            "sources": sources,
            "warnings": warnings,
        }
