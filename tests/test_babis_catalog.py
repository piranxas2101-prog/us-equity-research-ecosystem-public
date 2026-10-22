import json
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

from research_registry.babis_catalog import Sources, propose
from research_registry.core import taxonomy


@contextmanager
def fixture_source_helper():
    """Keep offline tests free of provider calls and of the local .env file."""
    def unexpected_call(*args, **kwargs):
        raise AssertionError("unexpected provider call")
    with patch('research_registry.source_http.load_env', return_value=(None, {})), \
            patch('research_registry.source_http.fetch_json', side_effect=unexpected_call):
        yield


class CatalogTests(unittest.TestCase):
    def row(self, **extra):
        return dict(ticker='ABC',venue='Nasdaq',name='Example',instrument_type='equity_candidate',
                    node_id='technology',candidate_cik='1',directory_aliases=['ABC'],**extra)

    def test_proposal_uses_existing_taxonomy_and_never_applies(self):
        p = {'status':'ok','data':{'cik':'0000000001','sector':'Technology','industry':'Software - Application','description':'Business applications'}}
        s = {'status':'ok','data':{'cik':1,'tickers':['ABC']}}
        result = propose([self.row()],p,s)[0]
        self.assertIn(result['proposed_node'],taxonomy()[1])
        self.assertEqual(result['proposed_depth'],3)
        self.assertFalse(result['verified'])
        self.assertFalse(result['applied_to_registry'])
        self.assertFalse(result['conflicts'])

    def test_mismatch_and_missing_evidence_are_visible(self):
        p = {'status':'ok','data':{'cik':2,'sector':'Healthcare','industry':'Biotechnology'}}
        r = propose([self.row()],p,{'status':'missing_configuration'})[0]
        self.assertIn('provider_cik_missing_or_conflicts_with_cached_sec',r['conflicts'])
        self.assertIn('provider_taxonomy_disagreement',r['conflicts'])
        self.assertEqual(r['status'],'proposal_needs_review')

    def test_fund_does_not_get_corporate_sector(self):
        p = {'status':'ok','data':{'isEtf':True,'sector':'Technology'}}
        self.assertIsNone(propose([self.row()],p,{})[0]['proposed_node'])

    def test_missing_profile_is_not_success(self):
        r = propose([self.row()],{'status':'rate_limited'},{})[0]
        self.assertEqual(r['status'],'unresolved')
        self.assertIsNone(r['proposed_node'])

    def test_source_budget_cache_and_circuit_breaker(self):
        with fixture_source_helper(), tempfile.TemporaryDirectory() as directory:
            source = Sources(Path(directory), max_requests=2)
            source.env = {'FMP_API_KEY':'test-not-a-real-key'}
            with patch.object(source.helper,'fetch_json',return_value={'status':'rate_limited'}) as fetch:
                first = source.get('fmp','ABC')
                again = source.get('fmp','ABC')
                second = source.get('fmp','DEF')
            self.assertEqual(first,again)
            self.assertEqual(fetch.call_count,1)
            self.assertEqual(second['status'],'provider_disabled:rate_limited')
            self.assertNotIn('test-not-a-real-key',json.dumps(first))
            source.db.close()

    def test_zero_budget_makes_no_calls(self):
        with fixture_source_helper(), tempfile.TemporaryDirectory() as directory:
            source = Sources(Path(directory),max_requests=0)
            with patch.object(source.helper,'fetch_json') as fetch:
                self.assertEqual(source.get('sec','1')['status'],'budget_exhausted')
                fetch.assert_not_called()
            source.db.close()

    def test_worker_finishes_all_fixture_rows_with_report(self):
        from research_registry import babis_catalog as worker
        import hashlib
        from research_registry.core import digest
        with fixture_source_helper(), tempfile.TemporaryDirectory() as directory:
            folder = Path(directory) / 'job'
            folder.mkdir()
            corporate = self.row()
            etf = dict(corporate,ticker='ETF',instrument_type='etf',node_id=None)
            inputs = {'symbols':2,'snapshot_id':'fixture','taxonomy_sha256':digest(taxonomy()[0]),
                      'classifier_sha256':hashlib.sha256(Path(worker.__file__).with_name('core.py').read_bytes()).hexdigest(),
                      'worker_sha256':hashlib.sha256(Path(worker.__file__).read_bytes()).hexdigest(),
                      'groups':{'company':[corporate],'fund':[etf]}}
            worker.save(folder/'input.json',inputs)
            self.assertEqual(worker.run(folder,max_requests=0),0)
            state = json.loads((folder/'state.json').read_text())
            self.assertEqual(state['processed_symbols'],2)
            self.assertEqual(state['source_requests'],0)
            self.assertTrue((folder/'report.md').exists())
            self.assertEqual(worker.run(folder,max_requests=0),0)
            self.assertEqual(len(json.loads((folder/'proposals.json').read_text(encoding='utf-8'))),2)
