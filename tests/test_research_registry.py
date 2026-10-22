import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from research_registry.core import initialize, classify, import_identities, import_profiles, summary, browse
from symbol_classification import classify_symbol


class RegistryTests(unittest.TestCase):
    def setUp(self):
        self.db=sqlite3.connect(':memory:')
        self.db.row_factory=sqlite3.Row
        self.db.execute('PRAGMA foreign_keys=ON')
        initialize(self.db)
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(self.db.close)

    def write(self,name,payload):
        path=Path(self.tmp.name)/name
        path.write_text(json.dumps(payload),encoding='utf-8')
        return path

    def identities(self):
        return self.write('sec.json',{'fields':['cik','name','ticker','exchange'],'data':[[1,'Example','ABC','Nasdaq'],[1,'Example','ABC.B','NYSE']]})

    def profile(self,sector='Technology',industry='Semiconductors',timestamp='2026-09-02T10:00:00+00:00',ticker='ABC'):
        return self.write('profile.json',{'run_timestamp':timestamp,'shortlist':[{'ticker':ticker,'sector':sector,'industry':industry}]})

    def test_init_idempotent_and_complete(self):
        initialize(self.db)
        self.assertEqual(summary(self.db)['taxonomy_node'],196)
        self.assertEqual(len(browse(self.db)),11)

    def test_legacy_contract_and_conservative_depth(self):
        row=classify_symbol({'ticker':'ABC','sector':'Consumer Cyclical','industry':'Specialty Retail'})
        self.assertEqual(row['industry_id'],'consumer_discretionary.specialty_retail')
        self.assertEqual(row['research_classification']['depth'],4)
        self.assertEqual(classify({'sector':'Technology','industry':'Semiconductors'})['depth'],3)
        self.assertEqual(classify({'sector':'Technology','industry':'Unsupported'})['depth'],1)
        self.assertIsNone(classify({})['node_id'])

    def test_identity_dedup_and_import_repeat(self):
        path=self.identities()
        import_identities(self.db,path,'2026-09-01T10:00:00+00:00')
        self.assertEqual(import_identities(self.db,path,'2026-09-01T10:00:00+00:00')['status'],'unchanged')
        self.assertEqual(summary(self.db)['company'],1)
        path=self.profile()
        self.assertEqual(import_profiles(self.db,path)['profiles'],1)
        self.assertEqual(import_profiles(self.db,path)['status'],'unchanged')
        self.assertEqual(len(browse(self.db,'technology')['companies']),1)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM security').fetchone()[0],0)

    def test_future_identity_is_not_backdated(self):
        import_identities(self.db,self.identities(),'2026-09-03T10:00:00+00:00')
        self.assertEqual(import_profiles(self.db,self.profile())['unresolved_identity_rows'],1)

    def test_changes_preserve_history(self):
        import_identities(self.db,self.identities(),'2026-09-01T10:00:00+00:00')
        import_profiles(self.db,self.profile())
        import_profiles(self.db,self.profile('Healthcare','Biotechnology','2026-09-03T10:00:00+00:00'))
        rows=self.db.execute('SELECT * FROM classification ORDER BY valid_from').fetchall()
        self.assertEqual(len(rows),2)
        self.assertEqual(rows[0]['valid_to'],rows[1]['valid_from'])
        self.assertIsNone(rows[1]['valid_to'])
        self.assertFalse(self.db.execute('PRAGMA foreign_key_check').fetchall())

    def test_invalid_batch_rolls_back(self):
        path=self.write('bad.json',{'fields':['cik','name','ticker'],'data':[[1,'Example','ABC'],[2,{'bad':1},'DEF']]})
        with self.assertRaises(sqlite3.ProgrammingError):
            import_identities(self.db,path,'2026-09-01T00:00:00+00:00')
        self.assertEqual(summary(self.db)['company'],0)


if __name__=='__main__':
    unittest.main()
