"""Local store of SEC companyfacts, reused on days the company filed nothing new.

SEC companyfacts only change when a company files a financial report. Downloading
them for every shortlisted company every day was the longest step of the full run.
This store keeps the last download per CIK (compressed, with its sha256 and the
US-Eastern date it was fetched) and, in ``reuse`` mode, hands it back when:

- it was fetched at most ``max_age_days`` ago (30, owner-approved), and
- the SEC filing calendar covers every day from the day before the fetch up to the
  previous business day, and lists no financial-report filing for that CIK.

Anything else (no copy, too old, a calendar gap, a new filing, an unreadable file)
means a normal download. The reused raw document is re-extracted with today's
``as_of`` by the same pure extractor, so freshness rules are evaluated as for a
fresh download; the provenance (source, fetch time, sha256, filings checked
through) travels with the observation.

Modes: ``off`` (not used), ``populate`` (store downloads, never reuse), ``reuse``.
"""
from __future__ import annotations

import argparse
from datetime import date, datetime, timedelta, timezone
import gzip
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import time
from typing import Any

ROOT = Path(__file__).resolve().parent
DEFAULT_DIR = ROOT / "cache" / "sec_companyfacts"
DEFAULT_FILING_DB = ROOT / "data" / "research_registry.db"
MODES = ("off", "populate", "reuse")
# Forms whose XBRL changes companyfacts. 8-K is excluded: its financial data reaches
# companyfacts through the 10-Q/10-K that follows. 6-K is kept to stay conservative.
POPULATE_KEEP_DAYS = 10
ABSENT_RECHECK_DAYS = 14  # a company the SEC has no companyfacts for (HTTP 404) is asked again after this long
COMPANYFACTS_FORMS = frozenset({"10-K", "10-K/A", "10-Q", "10-Q/A", "10-KT", "10-QT", "20-F", "20-F/A",
                                "40-F", "40-F/A", "6-K", "6-K/A"})


REFRESH_MODES_FILE = ROOT / "data" / "refresh_modes.json"


def effective_mode(key: str, configured: str, path: Path | None = None) -> str:
    """The configured mode, raised to "reuse" (or lowered back to "populate") by refresh_gate.py.

    The gate only moves between populate and reuse; a store configured "off" stays off.
    """
    if configured == "off":
        return "off"
    try:
        state = json.loads((path or REFRESH_MODES_FILE).read_text(encoding="utf-8"))
        mode = str((state.get(key) or {}).get("mode"))
    except (OSError, ValueError, AttributeError):
        return configured
    return mode if mode in ("populate", "reuse") else configured


def eastern_today() -> date:
    from zoneinfo import ZoneInfo
    return datetime.now(ZoneInfo("America/New_York")).date()


def previous_business_day(day: date) -> date:
    day -= timedelta(days=1)
    while day.weekday() >= 5:
        day -= timedelta(days=1)
    return day


class CompanyFactsCache:
    def __init__(self, directory: Path = DEFAULT_DIR, filing_db: Path = DEFAULT_FILING_DB, *,
                 mode: str = "off", max_age_days: int = 30, today: date | None = None) -> None:
        if mode not in MODES:
            raise ValueError(f"Unknown companyfacts cache mode: {mode}")
        self.directory, self.filing_db, self.mode = Path(directory), Path(filing_db), mode
        self.max_age_days, self._today = int(max_age_days), today

    @classmethod
    def from_config(cls, config: dict[str, Any], **overrides: Any) -> "CompanyFactsCache":
        cfg = dict(((config.get("data_sources") or {}).get("sec") or {}).get("companyfacts_cache") or {})
        return cls(ROOT / str(cfg.get("dir", "cache/sec_companyfacts")),
                   mode=effective_mode("companyfacts", str(cfg.get("mode", "off"))),
                   max_age_days=int(cfg.get("max_age_days", 30)), **overrides)

    @property
    def today(self) -> date:
        return self._today or eastern_today()

    def _paths(self, cik: int) -> tuple[Path, Path]:
        stem = f"CIK{int(cik):010d}"
        return self.directory / f"{stem}.json.gz", self.directory / f"{stem}.meta.json"

    def _absent_path(self, cik: int) -> Path:
        return self.directory / f"CIK{int(cik):010d}.absent.json"

    def known_absent(self, cik: int) -> dict[str, Any] | None:
        """The SEC answered 404 for this CIK less than ABSENT_RECHECK_DAYS ago: it publishes no companyfacts for it
        (trusts, some foreign issuers). Not asked again until then; the result stays unknown, labelled permanent."""
        if self.mode == "off":
            return None
        try:
            marker = json.loads(self._absent_path(cik).read_text(encoding="utf-8"))
            checked = date.fromisoformat(marker["checked_date_et"])
        except (OSError, ValueError, KeyError):
            return None
        if not 0 <= (self.today - checked).days < ABSENT_RECHECK_DAYS:
            return None
        return {**marker, "recheck_after": (checked + timedelta(days=ABSENT_RECHECK_DAYS)).isoformat()}

    def note_absent(self, cik: int, http_status: int = 404) -> dict[str, Any] | None:
        if self.mode == "off":
            return None
        marker = {"cik": int(cik), "http_status": int(http_status), "checked_date_et": self.today.isoformat(),
                  "checked_at": datetime.now(timezone.utc).isoformat()}
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
            path = self._absent_path(cik)
            temporary = path.with_name(path.name + ".tmp")
            temporary.write_text(json.dumps(marker), encoding="utf-8")
            temporary.replace(path)
        except OSError:
            return None
        return {**marker, "recheck_after": (self.today + timedelta(days=ABSENT_RECHECK_DAYS)).isoformat()}

    def clear_absent(self, cik: int) -> None:
        try:
            self._absent_path(cik).unlink(missing_ok=True)
        except OSError:
            pass

    def store(self, cik: int, raw: dict[str, Any]) -> dict[str, Any] | None:
        if self.mode == "off":
            return None
        self.clear_absent(cik)  # a successful download proves the SEC publishes it now
        data_path, meta_path = self._paths(cik)
        if self.mode == "populate" and self._kept_for_shadow(meta_path):
            return None
        self.directory.mkdir(parents=True, exist_ok=True)
        # Keep the SEC's key order: the extractor must see the same document as a download.
        body = json.dumps(raw, ensure_ascii=False).encode("utf-8")
        meta = {"schema": "ecosystem.companyfacts_cache.v1", "cik": int(cik),
                "fetched_at": datetime.now(timezone.utc).isoformat(), "fetched_date_et": self.today.isoformat(),
                "sha256": hashlib.sha256(body).hexdigest(), "bytes": len(body)}
        try:  # the data file first, the meta last: a copy without meta is never reused
            for path, content in ((data_path, gzip.compress(body, compresslevel=4)), (meta_path, json.dumps(meta).encode("utf-8"))):
                temporary = path.with_name(path.name + ".tmp")
                temporary.write_bytes(content)
                temporary.replace(path)
        except OSError:
            return None  # storing is an optimisation; the download itself already succeeded
        return meta

    def _kept_for_shadow(self, meta_path: Path) -> bool:
        """In populate mode a copy younger than POPULATE_KEEP_DAYS is left alone, so it can age to the
        3 days the refresh gate needs; overwriting it every night would restart that clock forever."""
        try:
            fetched = date.fromisoformat(json.loads(meta_path.read_text(encoding="utf-8"))["fetched_date_et"])
        except (OSError, ValueError, KeyError):
            return False
        return 0 <= (self.today - fetched).days < POPULATE_KEEP_DAYS  # a future-dated copy is not trusted

    def decide(self, cik: int) -> tuple[dict[str, Any] | None, dict[str, Any]]:
        """(raw companyfacts, provenance) when reusable, else (None, {"reason": ...})."""
        if self.mode != "reuse":
            return None, {"reason": f"cache_mode_{self.mode}"}
        data_path, meta_path = self._paths(cik)
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            fetched = date.fromisoformat(meta["fetched_date_et"])
        except (OSError, ValueError, KeyError):
            return None, {"reason": "no_cached_copy"}
        if (self.today - fetched).days > self.max_age_days:
            return None, {"reason": "cached_copy_older_than_max_age", "fetched_date_et": meta["fetched_date_et"]}
        checked_through = previous_business_day(self.today)
        # filed_since counts filings strictly after ``since``: start two days before the
        # fetch so a filing on the day before it (possibly not yet in the copy) counts.
        filings = self._financial_filings(cik, since=fetched - timedelta(days=2), until=checked_through)
        if filings is None:
            return None, {"reason": "filing_calendar_gap", "fetched_date_et": meta["fetched_date_et"]}
        if filings:
            return None, {"reason": "new_financial_filing", "filings": filings[:5]}
        try:
            body = gzip.decompress(data_path.read_bytes())
        except OSError:
            return None, {"reason": "cached_copy_unreadable"}
        if hashlib.sha256(body).hexdigest() != meta.get("sha256"):
            return None, {"reason": "cached_copy_hash_mismatch"}
        provenance = {"source": "cached_companyfacts", "fetched_at": meta["fetched_at"],
                      "fetched_date_et": meta["fetched_date_et"], "sha256": meta["sha256"],
                      "filings_checked_through": checked_through.isoformat(), "max_age_days": self.max_age_days}
        return json.loads(body), provenance

    def is_current(self, cik: int) -> bool:
        """A stored copy exists, is within max age and no financial form was filed since (no document read).

        The cheap check the nightly prefetch uses to decide whether a company needs a new download; ``decide``
        stays the authority on reuse (it also verifies the document hash)."""
        _, meta_path = self._paths(cik)
        try:
            fetched = date.fromisoformat(json.loads(meta_path.read_text(encoding="utf-8"))["fetched_date_et"])
        except (OSError, ValueError, KeyError):
            return False
        if (self.today - fetched).days > self.max_age_days:
            return False
        filings = self._financial_filings(cik, since=fetched - timedelta(days=2), until=previous_business_day(self.today))
        return filings == []

    def _financial_filings(self, cik: int, *, since: date, until: date) -> list[dict[str, Any]] | None:
        from research_registry.sec_filing_index import filed_since

        if not self.filing_db.is_file():
            return None
        try:
            db = sqlite3.connect(f"file:{self.filing_db.resolve().as_posix()}?mode=ro", uri=True, timeout=10)
        except sqlite3.Error:
            return None
        try:
            found = filed_since(db, cik, since, until=until, category="financial")
        except sqlite3.Error:
            return None  # calendar tables missing: nothing is known
        finally:
            db.close()
        return None if found is None else [f for f in found if f["form"] in COMPANYFACTS_FORMS]


MIN_SHADOW_AGE_DAYS = 3
# The SEC re-assigns the "frame" of a fact when another filing (even a DEF 14A with XBRL) adds a fact for the
# same period, and entity names can change whitespace; neither changes a value, period, unit or filing.
NON_MATERIAL_FIELD_KEYS = frozenset({"frame"})
NON_MATERIAL_TOP_KEYS = frozenset({"payload_sha256", "entity_name"})


def material_view(extracted: dict[str, Any]) -> dict[str, Any]:
    """The part of an extraction that decides results: values, periods, units and filings of every field."""
    view = {key: value for key, value in extracted.items() if key not in NON_MATERIAL_TOP_KEYS and key != "fields"}
    view["fields"] = {
        name: ({k: v for k, v in field.items() if k not in NON_MATERIAL_FIELD_KEYS} if isinstance(field, dict) else field)
        for name, field in (extracted.get("fields") or {}).items()}
    return view


def shadow(cache: CompanyFactsCache, user_agent: str, *, limit: int, as_of: str,
           pause_seconds: float = 0.2, min_age_days: int = MIN_SHADOW_AGE_DAYS) -> dict[str, Any]:
    """Compare, for CIKs the cache would reuse today, the extraction of the cached copy with a fresh download.

    Only copies at least ``min_age_days`` old are compared, oldest first: a copy fetched hours ago
    is trivially equal to a fresh one and proves nothing about reuse across days.
    """
    from sec_xbrl_pilot import extract_companyfacts, fetch_companyfacts

    reuse = CompanyFactsCache(cache.directory, cache.filing_db, mode="reuse",
                              max_age_days=cache.max_age_days, today=cache._today)
    results = {"identical": [], "different": [], "not_reusable": {}, "download_failed": [], "too_recent": 0,
               "non_material_differences": []}
    today, aged = reuse.today, []
    for meta_path in cache.directory.glob("CIK*.meta.json"):
        try:
            fetched = date.fromisoformat(json.loads(meta_path.read_text(encoding="utf-8"))["fetched_date_et"])
        except (OSError, ValueError, KeyError):
            continue
        if (today - fetched).days >= min_age_days:
            aged.append((fetched, meta_path))
        else:
            results["too_recent"] += 1
    for _, meta_path in sorted(aged, key=lambda item: (item[0], item[1].name))[: max(0, limit)]:
        cik = int(meta_path.name[3:13])
        raw, info = reuse.decide(cik)
        if raw is None:
            results["not_reusable"][info["reason"]] = results["not_reusable"].get(info["reason"], 0) + 1
            continue
        try:
            fresh = fetch_companyfacts(cik, user_agent)
        except (RuntimeError, OSError) as exc:
            results["download_failed"].append({"cik": cik, "error": str(exc)[:120]})
            continue
        finally:
            time.sleep(pause_seconds)
        left, right = extract_companyfacts(raw, as_of), extract_companyfacts(fresh, as_of)
        dump = lambda value: json.dumps(value, sort_keys=True, default=str)  # noqa: E731
        same = dump(material_view(left)) == dump(material_view(right))
        entry = {"cik": cik, "fetched_date_et": info["fetched_date_et"]}
        if not same:
            entry["differing_keys"] = sorted(k for k in set(left) | set(right) if left.get(k) != right.get(k))[:20]
        elif dump(left) != dump(right):  # identical where it matters; keep the rest visible
            results["non_material_differences"].append({**entry, "differing_keys": sorted(
                k for k in set(left) | set(right) if left.get(k) != right.get(k))[:20]})
        results["identical" if same else "different"].append(entry)
    results["summary"] = {k: (len(v) if isinstance(v, list) else v if isinstance(v, int) else sum(v.values()))
                          for k, v in results.items()}
    return results


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--config", type=Path, default=ROOT / "scanner_config.json")
    sub = parser.add_subparsers(dest="command", required=True)
    sh = sub.add_parser("shadow", help="compare cached and freshly downloaded companyfacts (reads SEC)")
    sh.add_argument("--limit", type=int, default=50)
    sh.add_argument("--output", type=Path, default=ROOT / "output" / "companyfacts_shadow_latest.json")
    sub.add_parser("status", help="show mode and how many companies are stored")
    args = parser.parse_args(argv)
    cache = CompanyFactsCache.from_config(json.loads(args.config.read_text(encoding="utf-8-sig")))
    if args.command == "status":
        result = {"mode": cache.mode, "max_age_days": cache.max_age_days, "directory": str(cache.directory),
                  "stored": len(list(cache.directory.glob("CIK*.meta.json"))) if cache.directory.is_dir() else 0}
    else:
        from research_workflow_orchestrator import load_env
        load_env(ROOT / ".env")  # SEC_USER_AGENT, never printed
        agent = os.getenv("SEC_USER_AGENT", "").strip()
        if not agent:
            raise SystemExit("SEC_USER_AGENT is not configured")
        result = shadow(cache, agent, limit=args.limit, as_of=datetime.now(timezone.utc).date().isoformat())
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        result = result["summary"]
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
