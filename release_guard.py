"""The release fingerprint: what reaches orders, as one id (owner 2026-11-18, ADR-002, RULES_REVIEW section 14).

``release_manifest.json`` lists the files (and the scanner_config sections) that decide which trade opens, how big
and when it closes. ``fingerprint`` hashes their content into a ``release_id``; a missing file is recorded as
missing, never skipped. Every Jarvis pass appends a snapshot to ``data/release_log.jsonl``; ``for_session`` tells which
release a session ran under, from the snapshots taken before and after it, and says so plainly when it changed on the
day (the id is then unknown, not guessed). ``period_changes`` lists every change inside the measurement period.

It reads files only. It never stops, blocks or changes a session, an order or a commit: it makes a change visible.

    python release_guard.py          # print the current release_id and the files behind it
"""
from __future__ import annotations

from datetime import date, datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent
MANIFEST = "release_manifest.json"
LOG = Path("data") / "release_log.jsonl"
NEW_YORK = ZoneInfo("America/New_York")
SESSION_OPEN_HOUR = 9  # New York; snapshots before 09:00 count as "before the session"


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def load_manifest(root: Path = ROOT) -> dict[str, Any]:
    return json.loads((root / MANIFEST).read_text(encoding="utf-8-sig"))


def fingerprint(root: Path = ROOT, manifest: dict[str, Any] | None = None) -> dict[str, Any]:
    """{"release_id", "files": {path: sha | "missing"}, "config_keys": {file: {key: sha | "missing"}}}. Line endings
    are normalised so a checkout's CRLF does not look like a change."""
    manifest = manifest or load_manifest(root)
    files: dict[str, str] = {}
    for rel in sorted(manifest.get("files") or []):
        path = root / rel
        files[rel] = _sha(path.read_bytes().replace(b"\r\n", b"\n")) if path.is_file() else "missing"
    keys: dict[str, dict[str, str]] = {}
    for rel, wanted in sorted((manifest.get("config_keys") or {}).items()):
        try:
            config = json.loads((root / rel).read_text(encoding="utf-8-sig"))
        except (OSError, ValueError):
            config = None
        keys[rel] = {k: ("missing" if not isinstance(config, dict) or k not in config else
                         _sha(json.dumps(config[k], sort_keys=True, ensure_ascii=False).encode("utf-8")))
                     for k in sorted(wanted)}
    release_id = _sha(json.dumps({"files": files, "config_keys": keys}, sort_keys=True).encode("utf-8"))[:16]
    return {"release_id": release_id, "files": files, "config_keys": keys}


def snapshot(root: Path, kind: str, *, now: datetime | None = None) -> dict[str, Any]:
    """Append the current fingerprint to the log (one line per Jarvis pass) and return it."""
    now = now or datetime.now(timezone.utc)
    entry = {"at": now.astimezone(timezone.utc).isoformat(timespec="seconds"), "kind": kind, **fingerprint(root)}
    path = root / LOG
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, ensure_ascii=False, sort_keys=True) + "\n")
    return entry


def read_log(root: Path) -> list[dict[str, Any]]:
    path = root / LOG
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return sorted(out, key=lambda e: str(e.get("at")))


def _ny(entry: dict[str, Any]) -> datetime:
    return datetime.fromisoformat(str(entry["at"])).astimezone(NEW_YORK)


def for_session(log: list[dict[str, Any]], session: date) -> dict[str, Any]:
    """The release a session ran under: the last snapshot before that day's 09:00 New York and the first one after it
    (Jarvis passes in the morning and after the session). Same id on both sides: that id. Different: ``changed_on_day``
    with both ids and release_id None. No snapshot before: unknown (None), never guessed from a later one."""
    cut = datetime.combine(session, datetime.min.time(), NEW_YORK).replace(hour=SESSION_OPEN_HOUR)
    before = [e for e in log if _ny(e) <= cut]
    after = [e for e in log if cut < _ny(e) <= cut.replace(hour=23, minute=59)]
    if not before:
        return {"release_id": None, "basis": "unknown"}
    first = before[-1]["release_id"]
    if after and after[0]["release_id"] != first:
        return {"release_id": None, "basis": "changed_on_day", "before": first, "after": after[0]["release_id"]}
    return {"release_id": first, "basis": "snapshot"}


def changed_files(old: dict[str, Any], new: dict[str, Any]) -> list[str]:
    out = [f for f in sorted(set(old.get("files") or {}) | set(new.get("files") or {}))
           if (old.get("files") or {}).get(f) != (new.get("files") or {}).get(f)]
    for rel in sorted(set(old.get("config_keys") or {}) | set(new.get("config_keys") or {})):
        a, b = (old.get("config_keys") or {}).get(rel) or {}, (new.get("config_keys") or {}).get(rel) or {}
        out += [f"{rel}:{k}" for k in sorted(set(a) | set(b)) if a.get(k) != b.get(k)]
    return out


def period_changes(log: list[dict[str, Any]], manifest: dict[str, Any]) -> dict[str, Any]:
    """Every release change from the period's start to its end (New York dates), with the files behind it."""
    period = manifest.get("period") or {}
    start, end = str(period.get("start") or ""), str(period.get("end") or "")
    inside = [e for e in log if start <= _ny(e).date().isoformat() <= end]
    earlier = [e for e in log if _ny(e).date().isoformat() < start]
    previous = earlier[-1] if earlier else None
    changes = []
    for entry in inside:
        if previous is not None and entry["release_id"] != previous["release_id"]:
            changes.append({"at": entry["at"], "from": previous["release_id"], "to": entry["release_id"],
                            "files": changed_files(previous, entry)})
        previous = entry
    return {"period": period.get("id"), "start": start, "end": end,
            "start_release": (earlier[-1]["release_id"] if earlier else (inside[0]["release_id"] if inside else None)),
            "changes": changes}


def main() -> int:
    current = fingerprint(ROOT)
    print(json.dumps({"release_id": current["release_id"],
                      "missing": [f for f, h in current["files"].items() if h == "missing"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
