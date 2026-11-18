"""Owner 2026-11-18: the duplicate search text leaves market_intel.db through a checked copy, swapped in only when
the live file did not change; the old file stays as a backup."""
from __future__ import annotations

from contextlib import closing
import os
from pathlib import Path
import sqlite3
import tempfile
import time
import unittest

import compact_market_intel as cm
import information_registry as registry
import research_control_plane as control


def legacy_db(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(path)) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute("CREATE VIRTUAL TABLE ecosystem_information_search "
                     "USING fts5(information_urn UNINDEXED, title, summary, search_text)")
        control.init_schema(conn)
        for i in range(30):
            observation, _ = control.record_observation(
                conn, subject_id=f"issuer:CIK:{i:010d}", capability="company.profile", provider="sec_edgar",
                source_ref=f"https://www.sec.gov/{i}", observed_at="2026-09-18T10:00:00+00:00", as_of="2026-09-17",
                run_id="run_t", payload={"name": f"Company number {i} Industrial"})
            control.build_packet(conn, kind="company_profile", subject_id=observation["subject_id"], run_id="run_t",
                                 producer_name="company_profile_agent", producer_version="1",
                                 payload={"overview": f"Industrial software company {i}."},
                                 inputs=[("observation", observation["observation_id"])], as_of="2026-09-17")
        conn.commit()


class CompactTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        legacy_db(self.root / cm.LIVE)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_prepare_then_swap_keeps_every_row_and_every_search_result(self) -> None:
        with closing(sqlite3.connect(self.root / cm.LIVE)) as conn:
            conn.row_factory = sqlite3.Row
            before = [r["information_urn"] for r in registry.search_information(conn, query="Industrial software")]
            objects = conn.execute("SELECT COUNT(*) FROM ecosystem_information_object").fetchone()[0]
        state = cm.prepare(self.root)
        self.assertTrue(state["ready"], state)
        self.assertEqual((state["quick_check"], state["same_table_counts"], state["same_search_results"]),
                         ("ok", True, True))
        self.assertFalse((self.root / cm.WORK / "snapshot.db").exists())
        result = cm.swap(self.root)
        self.assertEqual((result["status"], result["search"], result["objects"]), ("swapped", "index_only", objects))
        self.assertTrue(Path(result["backup"]).exists())  # the old file stays until the owner says it can go
        with closing(sqlite3.connect(self.root / cm.LIVE)) as conn:
            conn.row_factory = sqlite3.Row
            after = [r["information_urn"] for r in registry.search_information(conn, query="Industrial software")]
        self.assertEqual(before, after)

    def test_a_live_change_after_the_snapshot_refuses_the_swap(self) -> None:
        self.assertTrue(cm.prepare(self.root)["ready"])
        time.sleep(0.05)
        live = self.root / cm.LIVE
        with closing(sqlite3.connect(live)) as conn:
            conn.execute("CREATE TABLE written_meanwhile (x)")
            conn.commit()
        os.utime(live, None)
        result = cm.swap(self.root)
        self.assertEqual(result["status"], "refused")
        self.assertIn("changed after the snapshot", result["why"])
        with closing(sqlite3.connect(live)) as conn:
            self.assertEqual(registry._search_kind(conn), "fts_copy")  # nothing was changed

    def test_an_empty_wal_from_a_reader_is_not_a_change(self) -> None:
        self.assertTrue(cm.prepare(self.root)["ready"])
        wal = Path(str(self.root / cm.LIVE) + "-wal")
        wal.write_bytes(b"")  # a reader opened the database; nothing was written
        self.assertEqual(cm.swap(self.root)["status"], "swapped")

    def test_a_wal_with_content_is_a_change(self) -> None:
        self.assertTrue(cm.prepare(self.root)["ready"])
        Path(str(self.root / cm.LIVE) + "-wal").write_bytes(b"x" * 64)
        self.assertEqual(cm.swap(self.root)["status"], "refused")

    def test_swap_without_prepare_is_refused(self) -> None:
        self.assertEqual(cm.swap(self.root)["status"], "refused")


if __name__ == "__main__":
    unittest.main()
