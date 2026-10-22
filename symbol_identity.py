from __future__ import annotations

import hashlib
import json
from typing import Any


SCHEMA = "ecosystem.symbol_identity.v1"


class SymbolIdentityError(ValueError):
    pass


def normalize_symbol(value: Any) -> str:
    return str(value or "").strip().upper()


def resolve_sec_identity(symbol: str, ticker_to_cik: dict[str, int]) -> dict[str, Any]:
    ticker = normalize_symbol(symbol)
    cik = ticker_to_cik.get(ticker)
    payload = {
        "schema": SCHEMA,
        "ticker": ticker,
        "cik": None if cik is None else int(cik),
        "status": "mapped" if cik is not None else "symbol_mapping_error",
        "source": "sec_company_tickers",
        "guessed": False,
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return {**payload, "identity_sha256": hashlib.sha256(encoded.encode("utf-8")).hexdigest()}


def require_sec_identity(symbol: str, ticker_to_cik: dict[str, int]) -> dict[str, Any]:
    identity = resolve_sec_identity(symbol, ticker_to_cik)
    if identity["status"] != "mapped":
        raise SymbolIdentityError(
            f"symbol_mapping_error: SEC CIK mapping missing for {identity['ticker']}; identity was not guessed"
        )
    return identity
