"""Remove the second copy of the research text from data/market_intel.db (owner 2026-11-18: "delete the duplicate").

The full-text search table kept its own copy of every information object's text (~14 GB of 41 GB). The code now
keeps only the index (``information_registry.compact_search``). This tool applies that to the live database without
ever working on it directly:

* ``prepare``: checkpoint the live database, take a consistent snapshot of it (SQLite backup API), rebuild the search
  on the snapshot and ``VACUUM INTO`` a compact file; then check the compact file against the snapshot - every table's
  row count except the search table, ``quick_check``, and the same results for sample searches. The live database is
  only read. State: ``data/compact_work/state.json``.
* ``swap``: only if the live database has not changed since the snapshot (size and modification time of the file and
  its WAL), move it to ``data/backups/`` and put the compact file in its place. A file another program holds open
  cannot be moved on Windows: then nothing is changed and the reason is printed.

The old database stays in ``data/backups/`` until the owner says it can go.

    python compact_market_intel.py prepare
    python compact_market_intel.py swap
"""
from __future__ import annotations

import argparse
from contextlib import closing
from datetime import datetime
import json
import os
from pathlib import Path
import sqlite3
import sys
import time
from typing import Any

import information_registry as registry

ROOT = Path(__file__).resolve().parent
LIVE = Path("data") / "market_intel.db"
WORK = Path("data") / "compact_work"
SAMPLE_QUERIES = 25


def _stat(path: Path) -> list[float] | None:
    if not path.exists():
        return None
    st = path.stat()
    return [st.st_size, st.st_mtime]


def live_state(root: Path) -> dict[str, Any]:
    live = root / LIVE
    return {"db": _stat(live), "wal": _stat(Path(str(live) + "-wal"))}


def _log(message: str) -> None:
    print(f"{datetime.now().strftime('%H:%M:%S')} {message}", flush=True)


def _tables(conn: sqlite3.Connection) -> dict[str, int]:
    names = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND sql NOT LIKE "
                                        "'CREATE VIRTUAL%' AND name NOT LIKE 'ecosystem_information_search%' "
                                        "AND name NOT LIKE 'sqlite_%'")]
    return {name: conn.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0] for name in sorted(names)}


def _queries(conn: sqlite3.Connection) -> list[str]:
    """Sample searches: words from the titles of objects spread over the table (deterministic)."""
    count = conn.execute("SELECT MAX(rowid) FROM ecosystem_information_object").fetchone()[0] or 0
    out: list[str] = []
    for k in range(1, SAMPLE_QUERIES + 1):
        row = conn.execute("SELECT title FROM ecosystem_information_object WHERE rowid>=? AND title IS NOT NULL "
                           "AND title!='' ORDER BY rowid LIMIT 1", (count * k // (SAMPLE_QUERIES + 1),)).fetchone()
        words = [w for w in str(row[0] if row else "").replace('"', " ").split() if len(w) > 3]
        if words:
            out.append(" ".join(words[:2]))
    return out


def _results(conn: sqlite3.Connection, queries: list[str]) -> dict[str, list[str]]:
    conn.row_factory = sqlite3.Row
    return {q: [r["information_urn"] for r in registry.search_information(conn, query=q, limit=200)] for q in queries}


def prepare(root: Path) -> dict[str, Any]:
    work = root / WORK
    work.mkdir(parents=True, exist_ok=True)
    snapshot, compact = work / "snapshot.db", work / "compact.db"
    for path in (snapshot, compact):
        if path.exists():
            path.unlink()
    _log("checkpoint of the live database (its WAL is folded in; nothing else changes)")
    with closing(sqlite3.connect(root / LIVE, timeout=60)) as live:
        live.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    before = live_state(root)
    _log(f"snapshot via the SQLite backup API -> {snapshot}")
    with closing(sqlite3.connect(f"file:{(root / LIVE).as_posix()}?mode=ro", uri=True)) as src, \
            closing(sqlite3.connect(snapshot)) as dst:
        src.backup(dst, pages=65536)
    with closing(sqlite3.connect(snapshot)) as conn:
        kind = registry._search_kind(conn)
        if kind != "fts_copy":
            return {"status": "nothing_to_do", "kind": kind}
        tables = _tables(conn)
        queries = _queries(conn)
        expected = _results(conn, queries)
    _log("rebuilding the search as an index only (on the snapshot; the live file is not touched)")
    snapshot_size = snapshot.stat().st_size
    with closing(sqlite3.connect(snapshot)) as conn:
        result = registry.compact_search(conn, progress=lambda done, total: _log(f"  {done}/{total} indexed"))
        _log(f"compacted: {result}")
        _log(f"VACUUM INTO {compact}")
        conn.execute("VACUUM INTO ?", (str(compact),))
    _log("checking the compact file against the counts and searches taken before the rebuild")
    with closing(sqlite3.connect(compact)) as conn:
        check = conn.execute("PRAGMA quick_check").fetchone()[0]
        same_tables = _tables(conn) == tables
        got = _results(conn, queries)
    snapshot.unlink()  # a working copy only
    state = {"prepared_at": datetime.now().astimezone().isoformat(timespec="seconds"), "live_before": before,
             "compact": str(compact), "compact_gb": round(compact.stat().st_size / 2**30, 2),
             "live_gb": round(snapshot_size / 2**30, 2), "quick_check": check, "same_table_counts": same_tables,
             "queries": len(queries), "same_search_results": got == expected, "compact_search": result}
    state["ready"] = check == "ok" and same_tables and got == expected
    (work / "state.json").write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    return state


def swap(root: Path) -> dict[str, Any]:
    work = root / WORK
    try:
        state = json.loads((work / "state.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"status": "refused", "why": "run prepare first"}
    compact = Path(state["compact"])
    if not state.get("ready") or not compact.exists():
        return {"status": "refused", "why": "the prepared file did not pass its checks", "state": state}
    now = live_state(root)
    if now != state["live_before"]:
        return {"status": "refused", "why": "the live database changed after the snapshot; run prepare again",
                "before": state["live_before"], "now": now}
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backups = root / "data" / "backups"
    backups.mkdir(parents=True, exist_ok=True)
    live = root / LIVE
    old = backups / f"market_intel_before_compact_{stamp}.db"
    try:
        os.replace(live, old)
    except OSError as exc:
        return {"status": "refused", "why": f"the live database is open in another program ({exc}); nothing changed"}
    for suffix in ("-wal", "-shm"):
        side = Path(str(live) + suffix)
        if side.exists():
            try:
                os.replace(side, Path(str(old) + suffix))
            except OSError as exc:
                os.replace(old, live)  # put it back as it was
                return {"status": "refused", "why": f"{side.name} is open in another program ({exc}); nothing changed"}
    os.replace(compact, live)
    with closing(sqlite3.connect(live)) as conn:
        kind = registry._search_kind(conn)
        objects = conn.execute("SELECT COUNT(*) FROM ecosystem_information_object").fetchone()[0]
    result = {"status": "swapped", "at": datetime.now().astimezone().isoformat(timespec="seconds"),
              "backup": str(old), "search": kind, "objects": objects,
              "live_gb": round(live.stat().st_size / 2**30, 2), "backup_gb": round(old.stat().st_size / 2**30, 2)}
    (work / "state.json").write_text(json.dumps({**state, "swap": result}, ensure_ascii=False, indent=2),
                                     encoding="utf-8")
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Remove the duplicate search text from data/market_intel.db.")
    parser.add_argument("command", choices=("prepare", "swap"))
    parser.add_argument("--root", type=Path, default=ROOT)
    args = parser.parse_args(argv)
    start = time.time()
    result = prepare(args.root) if args.command == "prepare" else swap(args.root)
    print(json.dumps({**result, "seconds": round(time.time() - start)}, ensure_ascii=False, indent=2))
    return 0 if result.get("status") in ("swapped", "nothing_to_do") or result.get("ready") else 1


if __name__ == "__main__":
    sys.exit(main())
