"""Shared, persistent API budget and cooldown gate.

The gate stores counters only. Credentials remain in the process environment
and are never persisted or returned. A permit is reserved before a request;
there are no automatic retries after provider failures or HTTP 429 responses.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sqlite3
import time
from typing import Any, Callable


ROOT = Path(__file__).resolve().parent
DEFAULT_POLICY = ROOT / "research_registry" / "provider_rate_policy.json"
DEFAULT_DB = ROOT / "data" / "babis_enrichment" / "provider_rate_state.sqlite3"

ALIASES = {"sec_edgar": "sec", "fmp_quote": "fmp", "fmp_profile": "fmp",
           "polygon_ticker_details": "polygon", "yahoo_finance": "yahoo",
           "yfinance": "yahoo", "openbb:yfinance": "yahoo"}


@dataclass(frozen=True)
class RateDecision:
    provider: str
    permitted: bool
    reason_code: str
    observed_at: str
    next_eligible_at: str | None
    day_requests: int
    day_limit: int
    minute_requests: int
    minute_limit: int

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def canonical_provider(provider: str) -> str:
    value = str(provider or "").strip().lower()
    return ALIASES.get(value, value)


def _iso(epoch: float | None) -> str | None:
    return datetime.fromtimestamp(epoch, timezone.utc).isoformat() if epoch is not None else None


class SharedProviderRateLimiter:
    def __init__(self, policy_path: Path = DEFAULT_POLICY, db_path: Path = DEFAULT_DB, *,
                 clock: Callable[[], float] = time.time,
                 sleeper: Callable[[float], None] = time.sleep) -> None:
        self.policy_path, self.db_path = Path(policy_path), Path(db_path)
        self.clock, self.sleeper = clock, sleeper
        payload = json.loads(self.policy_path.read_text(encoding="utf-8-sig"))
        self.policy_version = str(payload.get("version") or "unknown")
        self.providers = dict(payload.get("providers") or {})
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._schema()

    @contextmanager
    def _connect(self):
        db = sqlite3.connect(self.db_path, timeout=30)
        db.row_factory = sqlite3.Row
        try:
            yield db
            db.commit()
        finally:
            db.close()

    def _schema(self) -> None:
        with self._connect() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS provider_rate_state(
              provider TEXT PRIMARY KEY,utc_day TEXT NOT NULL,day_requests INTEGER NOT NULL,
              minute_window_start REAL NOT NULL,minute_requests INTEGER NOT NULL,
              last_request_at REAL NOT NULL,blocked_until REAL NOT NULL,last_status TEXT NOT NULL)""")

    def _limits(self, provider: str) -> dict[str, Any] | None:
        return self.providers.get(canonical_provider(provider))

    def permit(self, provider: str, *, max_interval_wait_seconds: float = 0.0) -> RateDecision:
        name, now = canonical_provider(provider), self.clock()
        limits = self._limits(name)
        stamp = _iso(now) or ""
        if limits is None:
            return RateDecision(name, False, "provider_policy_missing", stamp, None, 0, 0, 0, 0)
        day_limit = int(limits["maximum_requests_per_utc_day"])
        minute_limit = int(limits["maximum_requests_per_minute"])
        minimum_interval = float(limits.get("minimum_interval_seconds") or 0.0)

        with self._connect() as db:
            row = db.execute("SELECT * FROM provider_rate_state WHERE provider=?", (name,)).fetchone()
        same_day = row and row["utc_day"] == datetime.fromtimestamp(now, timezone.utc).date().isoformat()
        wait = max(0.0, minimum_interval - (now - float(row["last_request_at"]))) if same_day else 0.0
        if wait and wait <= max_interval_wait_seconds:
            self.sleeper(wait)
            now, stamp = self.clock(), _iso(self.clock()) or stamp

        utc_day = datetime.fromtimestamp(now, timezone.utc).date().isoformat()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM provider_rate_state WHERE provider=?", (name,)).fetchone()
            if row:
                day_requests, minute_requests = int(row["day_requests"]), int(row["minute_requests"])
                window_start, last_request = float(row["minute_window_start"]), float(row["last_request_at"])
                blocked_until, last_status = float(row["blocked_until"]), str(row["last_status"])
                if str(row["utc_day"]) != utc_day:
                    day_requests, minute_requests, window_start = 0, 0, now
            else:
                day_requests, minute_requests, window_start = 0, 0, now
                last_request, blocked_until, last_status = 0.0, 0.0, "new"
            if blocked_until > now:
                db.rollback(); return RateDecision(name, False, "rate_limited", stamp, _iso(blocked_until), day_requests, day_limit, minute_requests, minute_limit)
            if day_requests >= day_limit:
                tomorrow = datetime.fromtimestamp(now, timezone.utc).date() + timedelta(days=1)
                next_at = datetime.combine(tomorrow, datetime.min.time(), tzinfo=timezone.utc).isoformat()
                db.rollback(); return RateDecision(name, False, "daily_budget_exhausted", stamp, next_at, day_requests, day_limit, minute_requests, minute_limit)
            if now - window_start >= 60.0:
                minute_requests, window_start = 0, now
            if minute_requests >= minute_limit:
                db.rollback(); return RateDecision(name, False, "minute_budget_exhausted", stamp, _iso(window_start + 60.0), day_requests, day_limit, minute_requests, minute_limit)
            remaining = minimum_interval - (now - last_request)
            if remaining > 0:
                db.rollback(); return RateDecision(name, False, "minimum_interval", stamp, _iso(now + remaining), day_requests, day_limit, minute_requests, minute_limit)
            day_requests, minute_requests = day_requests + 1, minute_requests + 1
            db.execute("INSERT OR REPLACE INTO provider_rate_state VALUES(?,?,?,?,?,?,?,?)",
                       (name, utc_day, day_requests, window_start, minute_requests, now, blocked_until, last_status))
            db.commit()
        return RateDecision(name, True, "permitted", stamp, None, day_requests, day_limit, minute_requests, minute_limit)

    def record_result(self, provider: str, status: str) -> None:
        name, limits = canonical_provider(provider), self._limits(provider)
        if limits is None:
            return
        cooldown = float(limits.get("cooldown_seconds_after_429") or 0.0) if status == "rate_limited" else 0.0
        with self._connect() as db:
            db.execute("UPDATE provider_rate_state SET last_status=?,blocked_until=MAX(blocked_until,?) WHERE provider=?",
                       (status or "unknown", self.clock() + cooldown, name))

    def status(self) -> list[dict[str, Any]]:
        now = self.clock()
        with self._connect() as db:
            rows = [dict(row) for row in db.execute("SELECT * FROM provider_rate_state ORDER BY provider")]
        for row in rows:
            limits = self.providers.get(str(row["provider"]), {})
            day_limit = int(limits.get("maximum_requests_per_utc_day") or 0)
            minute_limit = int(limits.get("maximum_requests_per_minute") or 0)
            row["day_limit"] = day_limit
            row["day_remaining"] = max(0, day_limit - int(row["day_requests"]))
            row["minute_limit"] = minute_limit
            row["minute_remaining"] = max(0, minute_limit - int(row["minute_requests"]))
            row["blocked"] = float(row["blocked_until"]) > now
            row["blocked_until"] = _iso(float(row["blocked_until"])) if row["blocked_until"] else None
            row["last_request_at"] = _iso(float(row["last_request_at"])) if row["last_request_at"] else None
        return rows
