import csv
import hashlib
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from research_registry import full_universe as universe
from research_registry.core import taxonomy


class UniverseTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.db = sqlite3.connect(':memory:')
        self.db.row_factory = sqlite3.Row
        self.db.execute('PRAGMA foreign_keys=ON')
        self.addCleanup(self.db.close)
        self.sources = self.root / 'sources'
        self.sources.mkdir()
        self.exports = self.root / 'exports'
        files = {
            'nasdaqlisted.txt': 'Symbol|Security Name|Test Issue|ETF\nABC|Example Common Stock|N|N\nFUND|Example ETF|N|Y\nZZT|Test|Y|N\nFile Creation Time: 0904202621:31\n',
            'otherlisted.txt': 'ACT Symbol|Security Name|Exchange|CQS Symbol|NASDAQ Symbol|Test Issue|ETF\nABC.W|Example Warrants|Z|ABC.W|ABCW|N|N\nUNK|Unclear instrument|X|UNK|UNK|N|N\nFile Creation Time: 0904202621:31\n',
            'nasdaq_profiles.json': json.dumps({'data': {'rows': [
                {'symbol': 'ABC', 'sector': 'Technology', 'industry': 'Semiconductors'},
                {'symbol': 'FUND', 'sector': 'Technology', 'industry': 'Semiconductors'}]}}),
        }
        manifest = []
        for name, value in files.items():
            raw = value.encode('utf-8')
            (self.sources / name).write_bytes(raw)
            manifest.append({'name': name, 'status': 'ok', 'sha256': hashlib.sha256(raw).hexdigest(),
                             'retrieved_at': '2026-09-07T21:02:32Z', 'url': 'https://example.invalid/' + name})
        (self.sources / 'manifest.json').write_text(json.dumps(manifest), encoding='utf-8')

    def test_directory_preserves_special_symbols_and_unknown_venues(self):
        rows, excluded, _ = universe.parse_directory((self.sources/'otherlisted.txt').read_text())
        self.assertEqual([r['ticker'] for r in rows], ['ABC.W', 'UNK'])
        self.assertEqual(rows[1]['venue'], 'unmapped_exchange:X')
        self.assertEqual(excluded, 0)
        with self.assertRaises(ValueError):
            universe.parse_directory('Symbol|Security Name\nABC|Name')

    def test_txse_participant_code_is_named(self):
        text = ('ACT Symbol|Security Name|Exchange|CQS Symbol|NASDAQ Symbol|Test Issue|ETF\n'
                'TXS|Texas Capital Texas Equity Index ETF|F|TXS|TXS|N|Y\n'
                'File Creation Time: 0921202603:03\n')
        rows, excluded, _ = universe.parse_directory(text)
        self.assertEqual(excluded, 0)
        self.assertEqual(rows[0]['venue'], 'Texas Stock Exchange')

    def test_preferred_bank_is_not_a_preferred_security(self):
        row = {'ticker':'PFBC','venue_code':'Q','venue':'Nasdaq','name':'Preferred Bank - Common Stock','etf':False}
        self.assertEqual(universe.instrument(row, {})[0], 'equity_candidate')
        preferred = {**row, 'ticker':'ABC$A', 'name':'Example Corp Series A Preferred Stock'}
        self.assertEqual(universe.instrument(preferred, {})[0], 'preferred')

    def test_build_reconciles_and_is_idempotent(self):
        result = universe.build(self.db, self.sources, self.exports)
        self.assertEqual(result['listings'], 4)
        self.assertEqual(result['excluded_test_issues'], 1)
        self.assertEqual(result['unknown_instrument_review'], 1)
        self.assertEqual(result['partial_hierarchy_review'], 1)
        self.assertEqual(sum(result['status_counts'].values()), 4)
        self.assertEqual(universe.build(self.db, self.sources, self.exports), result)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM listed_symbol').fetchone()[0], 4)
        with (self.exports/'all_us_listed_symbols.csv').open(encoding='utf-8-sig', newline='') as handle:
            rows = {r['ticker']: r for r in csv.DictReader(handle)}
        self.assertEqual(len(rows), 4)
        self.assertEqual(rows['FUND']['node_id'], '')
        self.assertEqual(rows['ABC.W']['instrument_type'], 'warrant')
        self.assertEqual(rows['UNK']['status'], 'needs_review')
        review = json.loads((self.exports/'classification_review.json').read_text(encoding='utf-8'))
        self.assertEqual({r['ticker'] for r in review}, {'ABC', 'UNK'})
        self.assertFalse(self.db.execute('PRAGMA foreign_key_check').fetchall())

    def test_tampered_source_is_rejected(self):
        (self.sources/'nasdaqlisted.txt').write_text('changed')
        with self.assertRaisesRegex(ValueError, 'hash mismatch'):
            universe.build(self.db, self.sources, self.exports)

    def test_rule_changes_create_new_snapshot(self):
        first = universe.build(self.db, self.sources, self.exports)
        original = Path.read_bytes
        def altered(path):
            raw = original(path)
            return raw + b'\n# changed rules' if path.name == 'full_universe.py' else raw
        with patch.object(Path, 'read_bytes', altered):
            second = universe.build(self.db, self.sources, self.exports)
        self.assertNotEqual(first['snapshot_id'], second['snapshot_id'])
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM universe_snapshot').fetchone()[0], 2)

    def test_conservative_classification(self):
        _, nodes = taxonomy()
        mappings = json.loads(Path(universe.__file__).with_name('nasdaq_industries.json').read_text())['mappings']
        self.assertTrue(all(node in nodes for node in mappings.values()))
        row = {'ticker':'ABC','venue_code':'Q','venue':'Nasdaq','name':'Example Common Stock','etf':False}
        result = universe.classify_listing(row, {'industry':'Real Estate Investment Trusts'}, mappings, nodes)
        self.assertEqual(result['status'], 'needs_review')
        self.assertIsNone(result['node_id'])
        result = universe.classify_listing(row, {'sector':'Technology','industry':'Unmapped'}, mappings, nodes)
        self.assertEqual(result['depth'], 1)
        self.assertFalse(result['official_gics'])

    def test_ambiguous_industry_does_not_guess_a_sector(self):
        _, nodes = taxonomy()
        crosswalk = json.loads(Path(universe.__file__).with_name('nasdaq_industries.json').read_text())
        row = {'ticker':'EDU','venue_code':'N','venue':'NYSE','name':'Example Education Common Stock','etf':False}
        profile = {'sector':'Real Estate','industry':'Other Consumer Services'}
        result = universe.classify_listing(row, profile, crosswalk['mappings'], nodes,
                                           frozenset(crosswalk['ambiguous_industries']))
        self.assertIsNone(result['node_id'])
        self.assertEqual((result['status'], result['reason']), ('needs_review', 'ambiguous_provider_industry'))
        self.assertEqual(result['raw_sector'], 'Real Estate')  # the provider's view is kept as evidence


if __name__ == '__main__':
    unittest.main()
