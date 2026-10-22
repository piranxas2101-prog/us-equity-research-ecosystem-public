"""Shared local database location and connection helper for evidence packets."""
from __future__ import annotations

import sqlite3
from pathlib import Path

import research_control_plane


ROOT = Path(__file__).resolve().parent
DEFAULT_CONTROL_PLANE_DB = ROOT / "data" / "market_intel.db"


def connect(path: Path = DEFAULT_CONTROL_PLANE_DB) -> sqlite3.Connection:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(target)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys=ON")
    research_control_plane.init_schema(db)
    return db
