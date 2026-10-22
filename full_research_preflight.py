"""Fail-closed readiness check for the full-universe research-only launcher.

The preflight is local and read-only except for its JSON report. It never calls a
provider, starts an agent, or contacts a broker.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from importlib import metadata
import json
from pathlib import Path
import re
import sqlite3
import subprocess
import sys
from typing import Any


ROOT = Path(__file__).resolve().parent
CORE_DISTRIBUTIONS = ("pandas", "yfinance", "scikit-learn", "TA-Lib", "openbb")
KNOWN_COMPANY_SMOKE = ("PANW", "MU", "UNH", "HWM", "PFBC")


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def locked_versions(path: Path) -> dict[str, str]:
    wanted = {name.lower(): name for name in CORE_DISTRIBUTIONS}
    found: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        match = re.match(r"^([A-Za-z0-9_.-]+)==([^ ]+)\s*\\?$", line.strip())
        if match and match.group(1).lower() in wanted:
            found[wanted[match.group(1).lower()]] = match.group(2)
    return found


def installed_versions() -> dict[str, str | None]:
    result: dict[str, str | None] = {}
    for name in CORE_DISTRIBUTIONS:
        try:
            result[name] = metadata.version(name)
        except metadata.PackageNotFoundError:
            result[name] = None
    return result


def registry_snapshot_state(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {"snapshot_id": None, "unique_symbols": 0}
    db = sqlite3.connect(str(path))
    try:
        row = db.execute(
            "SELECT snapshot_id FROM universe_snapshot ORDER BY retrieved_at DESC,rowid DESC LIMIT 1"
        ).fetchone()
        if not row:
            return {"snapshot_id": None, "unique_symbols": 0}
        snapshot_id = str(row[0])
        count = db.execute(
            "SELECT COUNT(DISTINCT ticker) FROM effective_listed_symbol WHERE snapshot_id=?",
            (snapshot_id,),
        ).fetchone()[0]
        return {"snapshot_id": snapshot_id, "unique_symbols": int(count)}
    finally:
        db.close()


def iso_age_hours(value: str, now: datetime) -> float:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return max(0.0, (now - parsed.astimezone(timezone.utc)).total_seconds() / 3600.0)


def build_report(root: Path, *, now: datetime | None = None,
                 executable: Path | None = None, version_info: tuple[int, int] | None = None,
                 packages: dict[str, str | None] | None = None,
                 pip_check_ok: bool | None = None,
                 registry_state: dict[str, Any] | None = None) -> dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    executable = (executable or Path(sys.executable)).resolve()
    version_info = version_info or (sys.version_info.major, sys.version_info.minor)
    config = read_json(root / "scanner_config.json")
    source_manifest = read_json(root / "data" / "research_universe_sources" / "manifest.json")
    coverage = read_json(root / "data" / "research_universe_exports" / "coverage.json")
    eligibility = read_json(root / "output" / "universe_eligibility_latest.json")
    routing = read_json(root / "output" / "all_listed_universe_routes_latest.json")
    if not all(isinstance(item, dict) for item in (config, coverage, eligibility, routing)):
        raise ValueError("Config, coverage, eligibility and routing roots must be JSON objects")
    lock_path = root / "requirements-windows-py312.lock"
    lock_versions = locked_versions(lock_path)
    package_versions = packages if packages is not None else installed_versions()
    if pip_check_ok is None:
        pip_check_ok = subprocess.run(
            [str(executable), "-m", "pip", "check"], capture_output=True, text=True, check=False
        ).returncode == 0

    universe = config.get("universe") or {}
    scan = config.get("scan") or {}
    pipeline = config.get("pipeline") or {}
    expected_runtime = (root / ".venv-next" / "Scripts" / "python.exe").resolve()
    sources = source_manifest.get("sources") if isinstance(source_manifest, dict) else source_manifest
    if not isinstance(sources, list):
        sources = []
    source_ages = {
        str(item.get("name")): round(iso_age_hours(str(item.get("retrieved_at")), now), 2)
        for item in sources if isinstance(item, dict) and item.get("retrieved_at")
    }
    excluded = {
        str(row.get("ticker") or "").upper()
        for key in ("asset_classifier_exclusions", "special_exclusions")
        for row in eligibility.get(key) or [] if isinstance(row, dict)
    }
    runner = (root / "run_full_research_control_plane.ps1").read_text(encoding="utf-8")
    unmapped = [name for name in (coverage.get("exchanges") or {}) if str(name).startswith("unmapped_exchange:")]
    registry_path = Path(universe.get("registry_db_path", "data/research_registry.db"))
    if not registry_path.is_absolute():
        registry_path = root / registry_path
    registry = registry_state if registry_state is not None else registry_snapshot_state(registry_path)
    eligible_symbols = eligibility.get("eligible_symbols")
    if not isinstance(eligible_symbols, list):
        eligible_symbols = []
    eligible_set = {str(ticker).upper() for ticker in eligible_symbols}
    expected_catalog_symbols = int(coverage.get("unique_symbols") or coverage.get("listings") or 0)

    checks = {
        "python_3_12": version_info == (3, 12),
        "locked_runtime_path": executable == expected_runtime,
        "locked_packages_match": bool(lock_versions) and all(
            package_versions.get(name) == locked for name, locked in lock_versions.items()
        ),
        "pip_dependencies_consistent": bool(pip_check_ok),
        "all_sources_downloaded": len(sources) == 3 and all(item.get("status") == "ok" for item in sources),
        "sources_fresh_24h": len(source_ages) == 3 and all(age <= 24.0 for age in source_ages.values()),
        "catalog_has_no_unmapped_exchange": not unmapped,
        "catalog_and_eligibility_same_snapshot": (
            eligibility.get("local_snapshot_id") == coverage.get("snapshot_id")
        ),
        "catalog_nonempty": int(coverage.get("listings") or 0) > 0,
        "eligible_company_universe_nonempty": int(eligibility.get("eligible_count") or 0) > 0,
        "eligibility_symbol_manifest_complete": (
            len(eligible_set) == int(eligibility.get("eligible_count") or 0)
        ),
        "known_companies_not_excluded": all(ticker not in excluded for ticker in KNOWN_COMPANY_SMOKE),
        "known_companies_company_routed": all(ticker in eligible_set for ticker in KNOWN_COMPANY_SMOKE),
        "all_listed_universe_mode": str(universe.get("mode") or "").lower() == "registry_all_listed",
        "registry_covers_catalog": (
            registry.get("snapshot_id") == coverage.get("snapshot_id")
            and int(registry.get("unique_symbols") or 0) == expected_catalog_symbols
            and expected_catalog_symbols > 0
        ),
        "routing_covers_catalog": (
            routing.get("snapshot_id") == coverage.get("snapshot_id")
            and int(routing.get("listed_symbol_count") or 0) == expected_catalog_symbols
            and int(routing.get("company_fundamental_route_count") or 0)
                + int(routing.get("eligible_company_not_listed_count") or 0)
                == int(eligibility.get("eligible_count") or 0)
            and int(routing.get("company_fundamental_route_count") or 0)
                + int(routing.get("instrument_observation_route_count") or 0)
                == expected_catalog_symbols
        ),
        "universe_uncapped": int(universe.get("max_symbols", -1)) == 0,
        "price_uncapped": scan.get("max_price") is None,
        "full_universe_enabled": pipeline.get("full_universe") is True,
        "research_only_launcher": all(marker in runner for marker in (
            "NoAutoTraderStart = $true", "paper_submission_requested = $false", "live_money = $false"
        )),
    }
    return {
        "schema": "ecosystem.full_research_preflight.v1",
        "checked_at": now.isoformat(),
        "status": "ready" if all(checks.values()) else "blocked",
        "checks": checks,
        "runtime": {"executable": str(executable), "python": ".".join(map(str, version_info)),
                    "packages": package_versions, "locked_packages": lock_versions},
        "sources": {"ages_hours": source_ages, "catalog_snapshot_id": coverage.get("snapshot_id"),
                    "catalog_listings": coverage.get("listings"), "exchanges": coverage.get("exchanges"),
                    "unmapped_exchanges": unmapped, "registry": registry},
        "eligibility": {"eligible_company_symbols": eligibility.get("eligible_count"),
                        "asset_classifier_exclusions": len(eligibility.get("asset_classifier_exclusions") or []),
                        "special_exclusions": len(eligibility.get("special_exclusions") or []),
                        "known_company_smoke": list(KNOWN_COMPANY_SMOKE)},
        "routing": {
            "listed_symbol_count": routing.get("listed_symbol_count"),
            "company_fundamental_route_count": routing.get("company_fundamental_route_count"),
            "eligible_company_not_listed_count": routing.get("eligible_company_not_listed_count"),
            "instrument_observation_route_count": routing.get("instrument_observation_route_count"),
            "route_counts": routing.get("route_counts"),
        },
        "safety": {"research_only": True, "paper_submission_requested": False, "live_money": False},
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--output", type=Path, default=Path("output/full_research_preflight_latest.json"))
    args = parser.parse_args(argv)
    root = args.root.resolve()
    output = args.output if args.output.is_absolute() else root / args.output
    try:
        report = build_report(root)
    except Exception as exc:
        report = {"schema": "ecosystem.full_research_preflight.v1",
                  "checked_at": datetime.now(timezone.utc).isoformat(), "status": "blocked",
                  "checks": {}, "error_type": type(exc).__name__, "error": str(exc)}
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["status"] == "ready" else 1


if __name__ == "__main__":
    raise SystemExit(main())
