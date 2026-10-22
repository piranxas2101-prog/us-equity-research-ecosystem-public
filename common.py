"""Small shared helpers with no project dependencies.

This module is intentionally dependency-free (standard library only) so any
module can import it without risking an import cycle. It is the canonical home
for trivial helpers that were previously copy-pasted across many modules.

Only helpers whose duplicated definitions were byte-for-byte identical live
here. Variants with different behavior (returning ``None`` on missing files,
applying defaults, etc.) are deliberately left in their own modules.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

# Seconds a connection waits for another process's lock before raising
# "database is locked". Background daemons and the daily pipeline share the
# same SQLite files, so the stdlib default of 5 seconds is too short.
SQLITE_BUSY_TIMEOUT_SECONDS = 30.0


def load_json(path: Path) -> dict[str, Any]:
    """Read a JSON object from ``path``, tolerating a UTF-8 BOM.

    Raises the usual ``OSError``/``json.JSONDecodeError`` on a missing or
    malformed file - callers that need a softer failure keep their own variant.
    """
    return json.loads(path.read_text(encoding="utf-8-sig"))


def enable_wal(conn: sqlite3.Connection) -> str:
    """Switch the database file to write-ahead logging and return the active mode.

    WAL lets readers keep reading while one process writes, instead of every
    reader blocking behind the writer. The setting is stored in the database
    file, so it also applies to connections opened with plain ``sqlite3.connect``.
    If another process holds a lock right now, the file keeps its current mode
    and a later connection converts it; this never raises for that case.
    """
    try:
        row = conn.execute("PRAGMA journal_mode=WAL").fetchone()
    except sqlite3.OperationalError:
        row = conn.execute("PRAGMA journal_mode").fetchone()
    return str(row[0]).lower() if row else ""
