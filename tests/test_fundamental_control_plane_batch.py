from __future__ import annotations

import unittest
import tempfile
from pathlib import Path

from fundamental_control_plane_batch import _bounded_rows, _atomic_json, _batch_signature


class FundamentalControlPlaneBatchTests(unittest.TestCase):
    def test_zero_or_negative_limit_means_all_rows(self):
        payload = {"results": [{"ticker": "A"}, {"ticker": "B"}, {"ticker": "C"}]}
        self.assertEqual([row["ticker"] for row in _bounded_rows(payload, 0)], ["A", "B", "C"])
        self.assertEqual([row["ticker"] for row in _bounded_rows(payload, -1)], ["A", "B", "C"])

    def test_positive_limit_remains_bounded(self):
        payload = {"shortlist": [{"ticker": "A"}, {"ticker": "B"}, {"ticker": "C"}]}
        self.assertEqual([row["ticker"] for row in _bounded_rows(payload, 2)], ["A", "B"])

    def test_batch_signature_is_deterministic_and_run_scoped(self):
        rows = [{"ticker": "A", "score": 1}]
        first = _batch_signature(rows, "run-1", "2026-09-24")
        self.assertEqual(first, _batch_signature(rows, "run-1", "2026-09-24"))
        self.assertEqual(first, _batch_signature(rows, "run-1", "2026-09-25"))
        self.assertNotEqual(first, _batch_signature(rows, "run-2", "2026-09-24"))

    def test_atomic_json_never_leaves_temporary_file(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "checkpoint.json"
            _atomic_json(path, {"status": "running", "processed_count": 25})
            self.assertTrue(path.is_file())
            self.assertFalse(path.with_suffix(".json.tmp").exists())


if __name__ == "__main__":
    unittest.main()


class CurrentClassificationTests(unittest.TestCase):
    def registry(self, folder, rows):
        import sqlite3
        path = Path(folder) / "registry.db"
        db = sqlite3.connect(path)
        db.execute("CREATE TABLE universe_snapshot(snapshot_id TEXT, retrieved_at TEXT)")
        db.execute("CREATE TABLE effective_listed_symbol(snapshot_id TEXT, ticker TEXT, venue_code TEXT, node_id TEXT, status TEXT)")
        db.executemany("INSERT INTO universe_snapshot VALUES(?,?)", [("old", "2026-09-17"), ("new", "2026-10-06")])
        db.executemany("INSERT INTO effective_listed_symbol VALUES(?,?,?,?,?)", rows)
        db.commit()
        db.close()
        return path

    def test_routing_uses_todays_node_not_the_one_frozen_in_the_stored_profile(self):
        from fundamental_control_plane_batch import _current_classification, with_current_classification
        with tempfile.TemporaryDirectory() as folder:
            current = _current_classification(self.registry(folder, [
                ("old", "NFLX", "XNAS", "consumer_discretionary.goods_retail.retail.specialty", "approved"),
                ("new", "NFLX", "XNAS", "communication_services.media", "approved_provisional"),
                ("new", "BVN", "XNYS", None, "needs_review")]))
        stored = {"ticker": "NFLX", "venue_code": "XNAS", "taxonomy_node": "consumer_discretionary.goods_retail.retail.specialty"}
        routed = with_current_classification(stored, current)
        self.assertEqual(routed["taxonomy_node"], "communication_services.media")
        self.assertEqual(routed["stored_taxonomy_node"], "consumer_discretionary.goods_retail.retail.specialty")
        self.assertEqual(routed["taxonomy_node_source"], "current_classification")
        # no current node: unknown (unroutable), never the stale one
        self.assertIsNone(with_current_classification({"ticker": "BVN", "venue_code": "XNYS", "taxonomy_node": "materials.x"},
                                                      current)["taxonomy_node"])
        # not in the registry: the stored profile is kept as it was
        self.assertEqual(with_current_classification({"ticker": "ZZZ", "taxonomy_node": "a.b"}, current)["taxonomy_node"], "a.b")

    def test_a_missing_registry_changes_nothing(self):
        from fundamental_control_plane_batch import _current_classification
        self.assertEqual(_current_classification(Path("does/not/exist.db")), {})
