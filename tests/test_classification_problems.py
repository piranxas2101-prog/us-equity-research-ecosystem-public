import json
import sqlite3
import unittest

from research_registry.classification_problems import build, category, enrichment_schema, reconcile_applied_batch, resolve_no_change_assessment, schema


class AppliedBatchReconciliationTests(unittest.TestCase):
    def test_applied_change_closes_only_matching_run_item(self):
        db = sqlite3.connect(":memory:")
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        schema(db)
        db.execute("CREATE TABLE classification_change_batch(batch_id TEXT PRIMARY KEY,status TEXT)")
        db.execute("CREATE TABLE classification_change_event(batch_id TEXT,action TEXT,snapshot_id TEXT,venue_code TEXT,ticker TEXT,after_json TEXT)")
        db.execute("INSERT INTO classification_problem_run VALUES(?,?,?,?,?,?,?)", ("run","snap","2.1","job","imported_open","now","{}"))
        db.execute("INSERT INTO classification_problem_group VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", ("group","run","classification_candidate_review","key","7372","technology","technology","equity_candidate","review","open",1,"[]"))
        db.execute("INSERT INTO classification_problem_item VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", ("problem","run","group","snap","2.1","Q","ABC","Example","classification_candidate_review","7372","technology","technology.software","equity_candidate","equity_candidate","classification_proposal_review","explicit_sic_crosswalk","deeper_assignment","review","open"))
        db.execute("INSERT INTO classification_change_batch VALUES('batch','applied')")
        db.execute("INSERT INTO classification_change_event VALUES(?,?,?,?,?,?)", ("batch","apply","snap","Q","ABC",json.dumps({"node_id":"technology.software"})))
        result = reconcile_applied_batch(db,"run","batch")
        self.assertEqual(result["resolved"],1)
        self.assertEqual(result["remaining_open"],0)
        self.assertEqual(db.execute("SELECT status FROM classification_problem_group").fetchone()[0],"resolved")
        db.close()


class ClassificationProblemTests(unittest.TestCase):
    def row(self, **changes):
        row = {"ticker":"ABC","venue":"Nasdaq","venue_code":"Q","name":"Example",
               "old_instrument_type":"equity_candidate","proposed_instrument_type":"equity_candidate",
               "old_node":"technology","proposed_node":"industrials","status":"classification_conflict_review",
               "reason":"sic_range_sector_or_group","relation":"cross_sector_conflict",
               "source_ref":{"details":{"sic":"7370"}}}
        row.update(changes); return row

    def test_problem_categories_are_deterministic(self):
        self.assertEqual(category(self.row()), "cross_sector_conflict")
        self.assertEqual(category(self.row(status="asset_type_review", relation="no_candidate")), "instrument_type_review")
        self.assertEqual(category(self.row(status="unresolved", reason="missing_sic", relation="no_candidate")), "missing_sic")
        self.assertIsNone(category(self.row(status="confirmed_same_node", relation="same_node")))

    def test_every_nonterminal_item_reconciles_to_one_group(self):
        rows = [self.row(), self.row(ticker="DEF"), self.row(ticker="GHI", status="unresolved", reason="unsupported_sic", relation="no_candidate", proposed_node=None)]
        payload = build(rows, "run", "snapshot", "2.1.0")
        self.assertTrue(payload["reconciled"])
        self.assertEqual(len(payload["items"]), 3)
        self.assertEqual(sum(group["count"] for group in payload["groups"]), 3)


class NoChangeResolutionTests(unittest.TestCase):
    def test_supports_current_closes_without_classification_write(self):
        db = sqlite3.connect(":memory:")
        db.row_factory = sqlite3.Row
        schema(db); enrichment_schema(db)
        db.execute("CREATE TABLE effective_listed_symbol(snapshot_id TEXT,venue_code TEXT,ticker TEXT,node_id TEXT,instrument_type TEXT)")
        db.execute("INSERT INTO classification_problem_run VALUES(?,?,?,?,?,?,?)", ("run","snap","2.1","job","imported_open","now","{}"))
        db.execute("INSERT INTO classification_problem_group VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", ("group","run","semantic_taxonomy_review","key","2834","healthcare","healthcare","equity_candidate","review","open",1,"[]"))
        db.execute("INSERT INTO classification_problem_item VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", ("problem","run","group","snap","2.1","Q","ABC","Example","semantic_taxonomy_review","2834","healthcare.therapeutics","healthcare.therapeutics.medicines","equity_candidate","equity_candidate","review","explicit","deeper_assignment","review","open"))
        db.execute("INSERT INTO effective_listed_symbol VALUES(?,?,?,?,?)", ("snap","Q","ABC","healthcare.therapeutics","equity_candidate"))
        payload = {"assessment":"supports_current_taxonomy","current_node":"healthcare.therapeutics","taxonomy_relation":"deeper_assignment","expected_sec_cik":"000123","observed_ciks":["000123"],"source_refs":[{"provider":"fmp","status":"ok","hash":"evidence"}]}
        db.execute("INSERT INTO classification_problem_enrichment VALUES(?,?,?,?,?)", ("problem","enrichment","supports_current_taxonomy",json.dumps(payload),"now"))
        result = resolve_no_change_assessment(db,"run","enrichment","user")
        self.assertEqual(result["resolved"],1)
        self.assertEqual(result["classification_writes"],0)
        self.assertEqual(db.execute("SELECT status FROM classification_problem_item").fetchone()[0],"resolved_no_change")
        repeated = resolve_no_change_assessment(db,"run","enrichment","second-user")
        self.assertEqual(repeated["status"],"already_applied")
        self.assertEqual(db.execute("SELECT COUNT(*) FROM classification_problem_resolution_event").fetchone()[0],1)
        db.close()


if __name__ == "__main__": unittest.main()
