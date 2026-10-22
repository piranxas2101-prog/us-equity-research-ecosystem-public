from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Any

import pandas as pd


OPENBB_UNSUPPORTED_PROVIDERS = {"polygon"}


def _to_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    if pd.isna(numeric):
        return None
    return numeric


def _to_int(value: Any) -> int | None:
    numeric = _to_float(value)
    if numeric is None:
        return None
    return int(numeric)


def _clean_text(value: Any) -> str:
    return str(value or "").strip()


def _parse_timestamp(value: Any) -> datetime | None:
    text = _clean_text(value)
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _normalize_history(frame: pd.DataFrame) -> pd.DataFrame:
    if frame is None or frame.empty:
        raise ValueError("No price history returned from OpenBB.")

    working = frame.reset_index()
    lower_map = {str(column).lower(): column for column in working.columns}
    required = {
        "date": lower_map.get("date"),
        "open": lower_map.get("open"),
        "high": lower_map.get("high"),
        "low": lower_map.get("low"),
        "close": lower_map.get("close"),
        "volume": lower_map.get("volume"),
    }
    missing = [name for name, source in required.items() if source is None and name != "volume"]
    if missing:
        raise ValueError(f"OpenBB history missing required columns: {', '.join(missing)}")

    out = pd.DataFrame(
        {
            "Date": pd.to_datetime(working[required["date"]], errors="coerce"),
            "Open": pd.to_numeric(working[required["open"]], errors="coerce"),
            "High": pd.to_numeric(working[required["high"]], errors="coerce"),
            "Low": pd.to_numeric(working[required["low"]], errors="coerce"),
            "Close": pd.to_numeric(working[required["close"]], errors="coerce"),
            "Volume": (
                pd.to_numeric(working[required["volume"]], errors="coerce")
                if required["volume"] is not None
                else pd.NA
            ),
        }
    )
    out = out.dropna(subset=["Date", "Close"]).sort_values("Date")
    if out.empty:
        raise ValueError("OpenBB history returned no usable rows.")
    return out.set_index("Date")


def _resolve_adjustment(value: Any, provider: str) -> str:
    raw = str(value or "raw").strip().lower()
    mapping = {
        "raw": "unadjusted",
        "unadjusted": "unadjusted",
        "split": "splits_only",
        "splits_only": "splits_only",
        "adjusted": "splits_and_dividends",
        "splits_and_dividends": "splits_and_dividends",
    }
    resolved = mapping.get(raw, "unadjusted")
    if provider == "yfinance" and resolved == "unadjusted":
        return "splits_only"
    return resolved


def _set_session_credentials() -> None:
    from openbb import obb

    mappings = {
        "FMP_API_KEY": "fmp_api_key",
        "POLYGON_API_KEY": "polygon_api_key",
        "FRED_API_KEY": "fred_api_key",
    }
    for env_name, target in mappings.items():
        value = os.getenv(env_name, "").strip()
        if value:
            setattr(obb.user.credentials, target, value)


def fetch_equity_history(symbol: str, lookback_days: int, market_cfg: dict[str, Any], provider: str | None = None) -> pd.DataFrame:
    from openbb import obb

    _set_session_credentials()
    openbb_provider = str(provider or market_cfg.get("openbb_provider", "yfinance")).strip().lower()
    if not openbb_provider:
        openbb_provider = "yfinance"
    if openbb_provider in OPENBB_UNSUPPORTED_PROVIDERS:
        raise ValueError(f"OpenBB provider '{openbb_provider}' is not supported in current OpenBB releases.")

    end_dt = pd.Timestamp.utcnow().date()
    start_dt = end_dt - timedelta(days=max(lookback_days * 2, 365))
    params: dict[str, Any] = {
        "symbol": symbol,
        "start_date": start_dt.isoformat(),
        "end_date": end_dt.isoformat(),
        "interval": "1d",
        "provider": openbb_provider,
        "adjustment": _resolve_adjustment(market_cfg.get("adjustment", "raw"), openbb_provider),
    }
    result = obb.equity.price.historical(**params)
    return _normalize_history(result.to_dataframe())


def _fetch_share_statistics(symbol: str, provider: str = "yfinance") -> tuple[dict[str, Any], str | None]:
    from openbb import obb

    result = obb.equity.ownership.share_statistics(symbol=symbol, provider=provider)
    frame = result.to_dataframe()
    if frame.empty:
        return {}, "No OpenBB share-statistics rows returned."
    row = frame.reset_index().iloc[0].to_dict()
    return {
        "date": str(row.get("date") or ""),
        "float_shares": _to_float(row.get("float_shares")),
        "outstanding_shares": _to_float(row.get("outstanding_shares")),
        "short_interest": _to_float(row.get("short_interest")),
        "short_percent_of_float": _to_float(row.get("short_percent_of_float")),
        "days_to_cover": _to_float(row.get("days_to_cover")),
        "insider_ownership": _to_float(row.get("insider_ownership")),
        "institution_ownership": _to_float(row.get("institution_ownership")),
        "institution_float_ownership": _to_float(row.get("institution_float_ownership")),
        "institutions_count": _to_int(row.get("institutions_count")),
        "provider": provider,
    }, None


def _is_open_market_purchase(tx_type: str, footnote: str) -> bool:
    compact = tx_type.lower()
    note = footnote.lower()
    if "purchase" in compact:
        return True
    if any(term in compact for term in ["exercise", "conversion", "vest", "withhold", "award", "gift", "trust", "tax liability"]):
        return False
    if "no shares were sold" in note:
        return False
    return False


def _is_open_market_sale(tx_type: str, footnote: str) -> bool:
    compact = tx_type.lower()
    note = footnote.lower()
    if "sale" in compact and "no shares were sold" not in note:
        return True
    if any(term in compact for term in ["exercise", "conversion", "vest", "withhold", "award", "gift", "trust", "tax liability"]):
        return False
    if "no shares were sold" in note:
        return False
    return False


def _fetch_insider_activity(symbol: str, limit: int = 80, lookback_days: int = 180) -> tuple[dict[str, Any], str | None]:
    from openbb import obb

    result = obb.equity.ownership.insider_trading(symbol=symbol, provider="sec", limit=limit)
    frame = result.to_dataframe()
    if frame.empty:
        return {}, "No OpenBB insider-trading rows returned."

    cutoff = datetime.now(timezone.utc) - timedelta(days=max(30, lookback_days))
    recent_rows: list[dict[str, Any]] = []
    for item in frame.reset_index(drop=True).to_dict(orient="records"):
        filing_date = _parse_timestamp(item.get("filing_date"))
        transaction_date = _parse_timestamp(item.get("transaction_date"))
        effective_date = filing_date or transaction_date
        if effective_date and effective_date < cutoff:
            continue
        recent_rows.append(item)

    if not recent_rows:
        return {
            "provider": "sec",
            "recent_row_count": 0,
            "open_market_buy_count": 0,
            "open_market_sell_count": 0,
            "open_market_buy_value": 0.0,
            "open_market_sell_value": 0.0,
            "net_open_market_value": 0.0,
            "latest_filing_date": None,
            "latest_transaction_date": None,
            "recent_owner_names": [],
        }, None

    buy_count = 0
    sell_count = 0
    buy_value = 0.0
    sell_value = 0.0
    owner_names: list[str] = []
    latest_filing: datetime | None = None
    latest_tx: datetime | None = None
    for item in recent_rows:
        tx_type = _clean_text(item.get("transaction_type"))
        footnote = _clean_text(item.get("footnote"))
        price = _to_float(item.get("transaction_price")) or 0.0
        shares = _to_float(item.get("securities_transacted")) or 0.0
        owner_name = _clean_text(item.get("owner_name"))
        if owner_name and owner_name not in owner_names:
            owner_names.append(owner_name)
        filing_date = _parse_timestamp(item.get("filing_date"))
        transaction_date = _parse_timestamp(item.get("transaction_date"))
        if filing_date and (latest_filing is None or filing_date > latest_filing):
            latest_filing = filing_date
        if transaction_date and (latest_tx is None or transaction_date > latest_tx):
            latest_tx = transaction_date
        if _is_open_market_purchase(tx_type, footnote):
            buy_count += 1
            buy_value += shares * price
        elif _is_open_market_sale(tx_type, footnote):
            sell_count += 1
            sell_value += shares * price

    return {
        "provider": "sec",
        "recent_row_count": len(recent_rows),
        "open_market_buy_count": buy_count,
        "open_market_sell_count": sell_count,
        "open_market_buy_value": round(buy_value, 2),
        "open_market_sell_value": round(sell_value, 2),
        "net_open_market_value": round(buy_value - sell_value, 2),
        "latest_filing_date": None if latest_filing is None else latest_filing.date().isoformat(),
        "latest_transaction_date": None if latest_tx is None else latest_tx.date().isoformat(),
        "recent_owner_names": owner_names[:5],
    }, None


def _fetch_darkpool_otc(symbol: str, weeks: int = 4) -> tuple[dict[str, Any], str | None]:
    from openbb import obb

    result = obb.equity.darkpool.otc(symbol=symbol, provider="finra")
    frame = result.to_dataframe()
    if frame.empty:
        return {}, "No OpenBB OTC/dark-pool rows returned."

    working = frame.reset_index(drop=True).copy()
    if "update_date" in working.columns:
        working["update_date"] = pd.to_datetime(working["update_date"], errors="coerce")
        working = working.sort_values("update_date", ascending=False)
    subset = working.head(max(2, weeks))
    latest = subset.iloc[0].to_dict()
    latest_shares = _to_float(latest.get("share_quantity"))
    latest_trades = _to_float(latest.get("trade_quantity"))
    avg_shares = _to_float(subset["share_quantity"].astype(float).mean()) if "share_quantity" in subset.columns else None
    avg_trades = _to_float(subset["trade_quantity"].astype(float).mean()) if "trade_quantity" in subset.columns else None
    return {
        "provider": "finra",
        "latest_week_date": (
            pd.to_datetime(latest.get("update_date"), errors="coerce").date().isoformat()
            if latest.get("update_date") is not None and not pd.isna(latest.get("update_date"))
            else None
        ),
        "latest_week_shares": latest_shares,
        "latest_week_trades": latest_trades,
        "avg_recent_week_shares": avg_shares,
        "avg_recent_week_trades": avg_trades,
        "activity_ratio": None if latest_shares is None or not avg_shares else round(latest_shares / avg_shares, 3),
    }, None


def _build_smart_money_summary(
    share_stats: dict[str, Any],
    insider: dict[str, Any],
    darkpool: dict[str, Any],
) -> tuple[dict[str, Any], list[str]]:
    notes: list[str] = []
    score = 0.0

    institution_ownership = _to_float(share_stats.get("institution_ownership"))
    institutions_count = _to_int(share_stats.get("institutions_count"))
    short_percent_of_float = _to_float(share_stats.get("short_percent_of_float"))
    days_to_cover = _to_float(share_stats.get("days_to_cover"))
    buy_count = _to_int(insider.get("open_market_buy_count")) or 0
    sell_count = _to_int(insider.get("open_market_sell_count")) or 0
    buy_value = _to_float(insider.get("open_market_buy_value")) or 0.0
    sell_value = _to_float(insider.get("open_market_sell_value")) or 0.0
    net_value = _to_float(insider.get("net_open_market_value")) or 0.0
    darkpool_ratio = _to_float(darkpool.get("activity_ratio"))

    if institution_ownership is not None:
        if institution_ownership >= 0.6:
            score += 0.3
            notes.append("Institutional ownership is high.")
        elif institution_ownership <= 0.2:
            score -= 0.1
            notes.append("Institutional ownership is relatively low.")
    if institutions_count is not None and institutions_count >= 150:
        score += 0.15
        notes.append("Institutional holder count is broad.")
    if buy_count > 0 and buy_value > sell_value:
        score += 0.45
        notes.append("Recent insider open-market buying outweighs selling.")
    elif sell_count > 0 and sell_value > buy_value:
        score -= 0.35
        notes.append("Recent insider open-market selling outweighs buying.")
    if short_percent_of_float is not None and days_to_cover is not None:
        if short_percent_of_float >= 0.08 and days_to_cover >= 3:
            score += 0.15
            notes.append("Short interest is elevated enough to matter.")
    if darkpool_ratio is not None:
        if darkpool_ratio >= 1.2:
            notes.append("Recent OTC/dark-pool activity is above its recent average.")
        elif darkpool_ratio <= 0.8:
            notes.append("Recent OTC/dark-pool activity is below its recent average.")

    signal = "neutral"
    if score >= 0.45:
        signal = "bullish"
    elif score <= -0.25:
        signal = "bearish"
    elif notes:
        signal = "mixed"

    return {
        "smart_money_signal": signal,
        "smart_money_score": round(score, 2),
        "institution_ownership": institution_ownership,
        "institutions_count": institutions_count,
        "insider_ownership": _to_float(share_stats.get("insider_ownership")),
        "institution_float_ownership": _to_float(share_stats.get("institution_float_ownership")),
        "short_percent_of_float": short_percent_of_float,
        "days_to_cover": days_to_cover,
        "open_market_buy_count": buy_count,
        "open_market_sell_count": sell_count,
        "open_market_buy_value": round(buy_value, 2),
        "open_market_sell_value": round(sell_value, 2),
        "net_open_market_value": round(net_value, 2),
        "darkpool_activity_ratio": darkpool_ratio,
        "darkpool_last_week_shares": _to_float(darkpool.get("latest_week_shares")),
        "darkpool_last_week_date": darkpool.get("latest_week_date"),
        "latest_insider_filing_date": insider.get("latest_filing_date"),
        "latest_insider_transaction_date": insider.get("latest_transaction_date"),
        "recent_insider_names": insider.get("recent_owner_names", []),
    }, notes


def fetch_research_context(symbol: str, research_cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    cfg = research_cfg or {}
    if not bool(cfg.get("enabled", True)):
        return {
            "enabled": False,
            "status": "disabled",
            "summary": {"smart_money_signal": "unavailable", "smart_money_score": 0.0},
            "datasets": {},
            "errors": [],
        }

    _set_session_credentials()
    errors: list[str] = []
    datasets: dict[str, Any] = {}
    share_statistics_enabled = bool(cfg.get("share_statistics_enabled", True))
    insider_trading_enabled = bool(cfg.get("insider_trading_enabled", True))
    darkpool_enabled = bool(cfg.get("darkpool_enabled", True))

    if share_statistics_enabled:
        try:
            share_stats, err = _fetch_share_statistics(
                symbol, provider=str(cfg.get("share_statistics_provider", "yfinance")).strip() or "yfinance"
            )
            if err:
                errors.append(f"share_statistics: {err}")
            else:
                datasets["share_statistics"] = share_stats
        except Exception as exc:
            errors.append(f"share_statistics: {exc}")

    if insider_trading_enabled:
        try:
            insider, err = _fetch_insider_activity(
                symbol,
                limit=int(cfg.get("insider_limit", 80)),
                lookback_days=int(cfg.get("insider_lookback_days", 180)),
            )
            if err:
                errors.append(f"insider_trading: {err}")
            else:
                datasets["insider_trading"] = insider
        except Exception as exc:
            errors.append(f"insider_trading: {exc}")

    if darkpool_enabled:
        try:
            darkpool, err = _fetch_darkpool_otc(symbol, weeks=int(cfg.get("darkpool_weeks", 4)))
            if err:
                errors.append(f"darkpool_otc: {err}")
            else:
                datasets["darkpool_otc"] = darkpool
        except Exception as exc:
            errors.append(f"darkpool_otc: {exc}")

    summary, notes = _build_smart_money_summary(
        datasets.get("share_statistics", {}),
        datasets.get("insider_trading", {}),
        datasets.get("darkpool_otc", {}),
    )
    summary["smart_money_notes"] = notes

    status = "ok"
    if errors and datasets:
        status = "partial"
    elif errors and not datasets:
        status = "error"

    return {
        "enabled": True,
        "status": status,
        "summary": summary,
        "datasets": datasets,
        "errors": errors,
    }
