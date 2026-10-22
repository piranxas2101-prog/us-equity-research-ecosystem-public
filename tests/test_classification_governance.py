import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from research_registry.classification_governance import (
    POLICY_PATH, apply_batch, carry_forward_overrides, disposition, governance_schema, preflight_batch,
    prepare_batch, read_json, rollback_batch, validate_batch,
)
from research_registry.babis_catalog_v2 import EXACT_SIC
from research_registry.core import digest, taxonomy


class ClassificationGovernanceTests(unittest.TestCase):
    def proposal(self, **changes):
        row = {
            "ticker": "ABC", "venue": "Nasdaq", "name": "Example", "old_instrument_type": "equity_candidate",
            "old_node": "technology", "proposed_node": "technology.software", "proposed_instrument_type": "equity_candidate",
            "status": "classification_proposal_review", "relation": "deeper_assignment", "reason": "explicit_sic_crosswalk",
            "source_status": "ok", "source_ref": {"provider": "polygon", "url": "https://example.test/ABC",
            "observed_at": "2026-09-16T00:00:00+00:00", "hash": "evidence", "details": {"cik": "1", "sic": "7372"}},
        }
        row.update(changes)
        return row

    def test_safe_exact_mapping_is_candidate_but_not_auto_applied(self):
        self.assertEqual(disposition(self.proposal(source_ref={"provider": "sec", "url": "https://example.test/ABC",
            "observed_at": "2026-09-16T00:00:00+00:00", "hash": "evidence", "details": {"cik": "1", "sic": "7372"}}))[0], "approval_candidate")

    def test_ambiguous_exact_sic_is_semantic_review(self):
        row = self.proposal(source_ref={"provider": "sec", "url": "https://example.test/ABC",
            "observed_at": "2026-09-16T00:00:00+00:00", "hash": "evidence", "details": {"cik": "1", "sic": "2834"}})
        self.assertEqual(disposition(row), ("human_review", "ambiguous_exact_sic_requires_semantic_review"))

    def test_policy_explicitly_covers_every_exact_sic_mapping(self):
        controls = read_json(POLICY_PATH)["sic_candidate_controls"]
        allowed = set(controls["allowed_exact_codes"])
        reviewed = set(controls["semantic_review_codes"])
        self.assertFalse(allowed & reviewed)
        self.assertEqual(allowed | reviewed, {str(code) for code in EXACT_SIC})

    def test_conflicts_and_range_mappings_require_review(self):
        self.assertEqual(disposition(self.proposal(relation="cross_sector_conflict"))[0], "human_review")
        self.assertEqual(disposition(self.proposal(reason="sic_range_sector_or_group"))[0], "human_review")

    def test_existing_specific_value_is_not_changed(self):
        self.assertEqual(disposition(self.proposal(status="confirmed_existing_more_specific",
                                                    relation="existing_more_specific"))[0], "no_change")

    def test_prepare_apply_and_rollback_are_hash_bound(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); job = root / "job"; out = root / "batch"; job.mkdir()
            tax = taxonomy()[0]
            inputs = {"snapshot_id": "snapshot", "taxonomy_sha256": digest(tax), "symbols": 1}
            state = {"status": "finished_with_review_required"}
            for name, value in (("input.json", inputs), ("state.json", state), ("proposals.json", [self.proposal()])):
                (job / name).write_text(json.dumps(value), encoding="utf-8")
            manifest = prepare_batch(job, out)
            self.assertEqual(validate_batch(out)["manifest_sha256"], manifest["manifest_sha256"])
            change = json.loads((out / "changes.json").read_text(encoding="utf-8"))[0]
            approval = {"batch_id": manifest["batch_id"], "manifest_sha256": manifest["manifest_sha256"],
                        "approved_by": "tester", "approved_at": "2026-09-16T00:00:00+00:00",
                        "approved_change_ids": [change["change_id"]]}
            approval_path = root / "approval.json"; approval_path.write_text(json.dumps(approval), encoding="utf-8")
            db = sqlite3.connect(":memory:"); db.row_factory = sqlite3.Row
            db.execute("CREATE TABLE listed_symbol(snapshot_id TEXT,venue_code TEXT,ticker TEXT,instrument_type TEXT,node_id TEXT)")
            db.execute("ALTER TABLE listed_symbol ADD COLUMN name TEXT")
            db.execute("ALTER TABLE listed_symbol ADD COLUMN scheme TEXT")
            db.execute("ALTER TABLE listed_symbol ADD COLUMN version TEXT")
            db.execute("ALTER TABLE listed_symbol ADD COLUMN status TEXT")
            db.execute("ALTER TABLE listed_symbol ADD COLUMN payload_json TEXT")
            db.execute("INSERT INTO listed_symbol VALUES('snapshot','Q','ABC','equity_candidate','technology','Example','internal','v','provisional','{}')")
            preflight = preflight_batch(db, out)
            self.assertTrue(preflight["ready"])
            self.assertEqual(preflight["registry_writes"], 0)
            result = apply_batch(db, out, approval_path)
            self.assertEqual(result["applied"], 1)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM listing_classification_override").fetchone()[0], 1)
            self.assertEqual(db.execute("SELECT node_id FROM effective_listed_symbol").fetchone()[0], "technology.software")
            rollback_batch(db, manifest["batch_id"], "tester")
            self.assertEqual(db.execute("SELECT COUNT(*) FROM listing_classification_override").fetchone()[0], 0)
            self.assertEqual(db.execute("SELECT node_id FROM effective_listed_symbol").fetchone()[0], "technology")
            self.assertEqual(db.execute("SELECT COUNT(*) FROM classification_change_event").fetchone()[0], 2)

    def _carry_db(self):
        db = sqlite3.connect(":memory:"); db.row_factory = sqlite3.Row
        db.executescript("""
            CREATE TABLE universe_snapshot(snapshot_id TEXT PRIMARY KEY, retrieved_at TEXT, provenance_json TEXT, summary_json TEXT);
            CREATE TABLE listed_symbol(snapshot_id TEXT, venue_code TEXT, ticker TEXT, name TEXT, instrument_type TEXT,
                                       scheme TEXT, version TEXT, node_id TEXT, status TEXT, payload_json TEXT);
            INSERT INTO universe_snapshot VALUES('old','2026-09-07','{}','{}'), ('new','2026-09-24','{}','{}');
        """)
        governance_schema(db)
        rows = [("old", "Q", "SAME", "real_estate"), ("old", "Q", "MOVED", "real_estate"), ("old", "Q", "GONE", "real_estate"),
                ("new", "Q", "SAME", "real_estate"), ("new", "Q", "MOVED", "industrials")]
        db.executemany("INSERT INTO listed_symbol VALUES(?,?,?,'n','equity_candidate','s','2.1.0',?,'provisional','{}')", rows)
        db.execute("INSERT INTO classification_change_batch VALUES('b1','h','old','2.1.0','user','t','2026-09-16','applied')")
        for ticker in ("SAME", "MOVED", "GONE"):
            db.execute("INSERT INTO listing_classification_override VALUES('old','Q',?,'equity_candidate',"
                       "'consumer_discretionary.services_housing.education','approved_provisional','b1','2026-09-16')", (ticker,))
        db.commit()
        return db

    def test_carry_forward_reapplies_only_when_base_is_unchanged(self):
        db = self._carry_db()
        result = carry_forward_overrides(db, "new")
        self.assertEqual(result["carried"], 1)
        self.assertEqual([row["ticker"] for row in result["base_changed_needs_review"]], ["MOVED"])
        self.assertEqual([row["ticker"] for row in result["listing_missing"]], ["GONE"])
        effective = dict(db.execute("SELECT ticker,node_id FROM effective_listed_symbol WHERE snapshot_id='new'").fetchall())
        self.assertEqual(effective["SAME"], "consumer_discretionary.services_housing.education")
        self.assertEqual(effective["MOVED"], "industrials")  # not overwritten blindly
        event = db.execute("SELECT after_json FROM classification_change_event WHERE batch_id=?", (result["batch_id"],)).fetchone()
        self.assertEqual(json.loads(event[0])["carried_from_batch"], "b1")

    def test_carry_forward_keeps_approval_when_the_base_became_unknown(self):
        db = self._carry_db()
        db.execute("UPDATE listed_symbol SET node_id=NULL WHERE snapshot_id='new' AND ticker='MOVED'")
        result = carry_forward_overrides(db, "new")
        self.assertEqual(result["carried"], 2)
        self.assertEqual(result["base_changed_needs_review"], [])

    def test_carry_forward_is_idempotent_and_reversible(self):
        db = self._carry_db()
        first = carry_forward_overrides(db, "new")
        second = carry_forward_overrides(db, "new")
        self.assertEqual(second["carried"], 0)
        self.assertEqual(second["already_current"], 1)
        rollback_batch(db, first["batch_id"], "tester")
        node = db.execute("SELECT node_id FROM effective_listed_symbol WHERE snapshot_id='new' AND ticker='SAME'").fetchone()[0]
        self.assertEqual(node, "real_estate")

    def test_tampered_batch_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); job = root / "job"; out = root / "batch"; job.mkdir()
            tax = taxonomy()[0]
            for name, value in (("input.json", {"snapshot_id": "s", "taxonomy_sha256": digest(tax), "symbols": 1}),
                                ("state.json", {"status": "finished_with_review_required"}),
                                ("proposals.json", [self.proposal()])):
                (job / name).write_text(json.dumps(value), encoding="utf-8")
            prepare_batch(job, out)
            (out / "changes.json").write_text("[]", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "hash mismatch"):
                validate_batch(out)


if __name__ == "__main__":
    unittest.main()
