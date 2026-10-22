"""The shared SQLite connections switch databases to write-ahead logging."""
from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

import common
import market_intel_db
from research_registry import core as registry_core


class SqliteWalTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_market_intel_connect_enables_wal(self) -> None:
        conn = market_intel_db.connect(self.root / "market.db")
        try:
            self.assertEqual(conn.execute("PRAGMA journal_mode").fetchone()[0].lower(), "wal")
        finally:
            conn.close()

    def test_registry_connect_enables_wal(self) -> None:
        db = registry_core.connect(self.root / "registry.db")
        try:
            self.assertEqual(db.execute("PRAGMA journal_mode").fetchone()[0].lower(), "wal")
        finally:
            db.close()

    def test_wal_persists_for_plain_and_read_only_connections(self) -> None:
        path = self.root / "shared.db"
        conn = market_intel_db.connect(path)
        conn.execute("CREATE TABLE t(x)")
        conn.execute("INSERT INTO t VALUES (1)")
        conn.commit()
        conn.close()
        plain = sqlite3.connect(path)
        try:
            self.assertEqual(plain.execute("PRAGMA journal_mode").fetchone()[0].lower(), "wal")
        finally:
            plain.close()
        read_only = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
        try:
            self.assertEqual(read_only.execute("SELECT COUNT(*) FROM t").fetchone()[0], 1)
        finally:
            read_only.close()

    def test_reader_is_not_blocked_by_open_write_transaction(self) -> None:
        path = self.root / "concurrent.db"
        setup = market_intel_db.connect(path)
        setup.execute("CREATE TABLE t(x)")
        setup.execute("INSERT INTO t VALUES (1)")
        setup.commit()
        setup.close()
        writer = sqlite3.connect(path)
        writer.execute("BEGIN IMMEDIATE")
        writer.execute("INSERT INTO t VALUES (2)")
        reader = sqlite3.connect(path, timeout=0.5)
        try:
            # Under the old rollback journal this read would wait for the writer.
            self.assertEqual(reader.execute("SELECT COUNT(*) FROM t").fetchone()[0], 1)
        finally:
            reader.close()
            writer.rollback()
            writer.close()

    def test_enable_wal_on_memory_database_does_not_raise(self) -> None:
        conn = sqlite3.connect(":memory:")
        try:
            self.assertEqual(common.enable_wal(conn), "memory")
        finally:
            conn.close()


if __name__ == "__main__":
    unittest.main()
