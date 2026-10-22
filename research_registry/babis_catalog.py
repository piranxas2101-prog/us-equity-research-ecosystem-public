"""Independent Babis catalogue enrichment; proposals only, no trading or LLM."""
from __future__ import annotations
import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import sys
import time
from urllib.parse import urlencode

from . import source_http
from .core import PROJECT, DEFAULT_DB, classify, taxonomy, digest

ROOT = PROJECT / 'data/babis_catalog'
CORPORATE = {'equity_candidate', 'adr_candidate', 'unknown_instrument'}


def now():
    return datetime.now(timezone.utc).isoformat()


def save(path, value):
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    temporary.replace(path)


def cik(value):
    text = str(value or '').strip()
    return str(int(text)) if text.isdigit() and int(text) > 0 else None


def prepare(root=ROOT):
    db = sqlite3.connect(DEFAULT_DB.as_uri() + '?mode=ro', uri=True)
    try:
        snapshot = db.execute('SELECT snapshot_id FROM universe_snapshot ORDER BY retrieved_at DESC,rowid DESC LIMIT 1').fetchone()[0]
        records = [json.loads(row[0]) for row in db.execute('SELECT payload_json FROM listed_symbol WHERE snapshot_id=? ORDER BY ticker', (snapshot,))]
    finally:
        db.close()
    raw = (PROJECT / 'cache/sec_company_tickers_exchange.json').read_bytes()
    payload = json.loads(raw)
    identities = defaultdict(set)
    for values in payload['data']:
        row = dict(zip(payload['fields'], values))
        identity = cik(row.get('cik'))
        if identity:
            identities[row['ticker']].add(identity)
    groups = defaultdict(list)
    for row in records:
        found = set().union(*(identities.get(alias, set()) for alias in row['directory_aliases']))
        identity = next(iter(found)) if len(found) == 1 else None
        row['candidate_cik'] = identity
        row['identity_basis'] = 'cached_sec_exact_alias' if identity else 'unresolved_or_ambiguous'
        key = 'cik:' + identity if identity and row['instrument_type'] in CORPORATE else 'listing:' + row['venue_code'] + ':' + row['ticker']
        groups[key].append(row)
    job_id = 'babis-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    folder = root / job_id
    folder.mkdir(parents=True)
    inputs = {'snapshot_id': snapshot, 'taxonomy_version': taxonomy()[0]['version'],
              'taxonomy_sha256': digest(taxonomy()[0]),
              'classifier_sha256': hashlib.sha256(Path(__file__).with_name('core.py').read_bytes()).hexdigest(),
              'worker_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
              'identity_sha256': hashlib.sha256(raw).hexdigest(),
              'groups': dict(groups), 'symbols': len(records)}
    save(folder / 'input.json', inputs)
    save(folder / 'state.json', {'status':'prepared', 'job_id':job_id, 'created_at':now(), 'symbols':len(records)})
    return folder


class Sources:
    def __init__(self, folder, max_requests=12000, max_seconds=21600):
        self.helper = source_http
        try:
            _, self.env = self.helper.load_env(str(PROJECT / '.env'))
        except SystemExit:
            self.env = {}
        self.db = sqlite3.connect(folder / 'evidence.sqlite3')
        self.db.execute('CREATE TABLE IF NOT EXISTS evidence(key TEXT PRIMARY KEY,payload TEXT NOT NULL)')
        self.db.execute('CREATE TABLE IF NOT EXISTS attempts(key TEXT PRIMARY KEY,at TEXT NOT NULL)')
        self.db.execute('CREATE TABLE IF NOT EXISTS circuits(provider TEXT PRIMARY KEY,status TEXT NOT NULL)')
        self.db.commit()
        self.max_requests = max_requests
        self.deadline = time.monotonic() + max_seconds
        self.last_call = 0.0

    def get(self, provider, identity):
        key = provider + ':' + identity
        cached = self.db.execute('SELECT payload FROM evidence WHERE key=?', (key,)).fetchone()
        if cached:
            return json.loads(cached[0])
        source = ('https://financialmodelingprep.com/stable/profile?' + urlencode({'symbol':identity})
                  if provider == 'fmp' else 'https://data.sec.gov/submissions/CIK' + identity.zfill(10) + '.json')
        result = {'provider':provider, 'source_url':source, 'observed_at':now(), 'data':{}}
        blocked = self.db.execute('SELECT status FROM circuits WHERE provider=?', (provider,)).fetchone()
        count = self.db.execute('SELECT COUNT(*) FROM attempts').fetchone()[0]
        interrupted = self.db.execute('SELECT 1 FROM attempts WHERE key=?', (key,)).fetchone()
        if blocked:
            result['status'] = 'provider_disabled:' + blocked[0]
        elif interrupted:
            result['status'] = 'interrupted_request_not_retried'
        elif count >= self.max_requests or time.monotonic() >= self.deadline:
            result['status'] = 'budget_exhausted'
        elif not self.env.get('FMP_API_KEY' if provider == 'fmp' else 'SEC_USER_AGENT'):
            result['status'] = 'missing_configuration'
        else:
            # <= 1 request/second across both sources, zero automatic retries.
            time.sleep(max(0, 1.0 - (time.monotonic() - self.last_call)))
            self.db.execute('INSERT INTO attempts VALUES(?,?)', (key,now()))
            self.db.commit()
            headers = {'apikey': self.env['FMP_API_KEY']} if provider == 'fmp' else {'User-Agent':self.env['SEC_USER_AGENT']}
            response = self.helper.fetch_json(provider, source, headers=headers, timeout=15)
            self.last_call = time.monotonic()
            result['status'] = response['status']
            payload = response.get('data')
            if response['status'] == 'ok':
                if provider == 'fmp':
                    matches = [r for r in payload if isinstance(r,dict) and r.get('symbol') == identity] if isinstance(payload,list) else []
                    payload = matches[0] if len(matches) == 1 else {}
                    fields = ['symbol','companyName','cik','sector','industry','description','isEtf','isFund','isAdr','website']
                else:
                    payload = payload if isinstance(payload,dict) else {}
                    fields = ['cik','name','sic','sicDescription','tickers','exchanges','entityType','filings']
                result['data'] = {field:payload[field] for field in fields if field in payload}
                if provider == 'sec':
                    recent = result['data'].pop('filings', {}).get('recent', {})
                    result['data']['recent_annual_filings'] = [
                        {k:recent.get(k, [None]*len(recent.get('form',[])))[i] for k in ['form','accessionNumber','primaryDocument','filingDate']}
                        for i, form in enumerate(recent.get('form',[])) if form in {'10-K','20-F','40-F'}][:3]
                if not payload:
                    result['status'] = 'empty_or_ambiguous_response'
            if response['status'] in {'auth_failed','forbidden_or_plan','rate_limited','timeout_or_network','invalid_json','unexpected_error'}:
                self.db.execute('INSERT OR REPLACE INTO circuits VALUES(?,?)', (provider,response['status']))
        # Persist only whitelisted fields; never response errors or credential-bearing URLs.
        result['evidence_sha256'] = digest(result)
        self.db.execute('INSERT INTO evidence VALUES(?,?)', (key,json.dumps(result,ensure_ascii=False)))
        self.db.commit()
        return result


def propose(rows, profile, primary):
    p = profile.get('data', {})
    s = primary.get('data', {})
    candidate = classify({'sector':p.get('sector'), 'industry':p.get('industry')})
    output = []
    for row in rows:
        old = row['node_id']
        proposed = candidate['node_id'] if profile.get('status') == 'ok' else None
        reason = candidate['basis'] if proposed else 'missing_profile_or_mapping'
        conflicts = []
        expected = row.get('candidate_cik')
        if expected and cik(p.get('cik')) != expected:
            conflicts.append('provider_cik_missing_or_conflicts_with_cached_sec')
        if s and (cik(s.get('cik')) != expected or not set(row['directory_aliases']).intersection(s.get('tickers', []))):
            conflicts.append('current_sec_identity_not_confirmed')
        if p.get('isEtf') or p.get('isFund'):
            proposed = None
            conflicts.append('provider_reports_fund_or_etf')
        if proposed and old and not (proposed == old or proposed.startswith(old + '.') or old.startswith(proposed + '.')):
            conflicts.append('provider_taxonomy_disagreement')
        if row['instrument_type'] == 'unknown_instrument':
            conflicts.append('instrument_type_requires_review')
        if not p.get('description'):
            conflicts.append('business_description_missing')
        if primary.get('status') != 'ok':
            conflicts.append('primary_identity_evidence_unavailable')
        if proposed and old and old.startswith(proposed + '.'):
            reason = 'new_source_less_specific_than_existing'
        output.append({'ticker':row['ticker'],'venue':row['venue'],'name':row['name'],
                       'instrument_type':row['instrument_type'],'candidate_cik':expected,
                       'old_node':old,'proposed_node':proposed,'proposed_depth':candidate['depth'] if proposed else 0,
                       'proposed_path':candidate['path'] if proposed else [],
                       'status':'proposal_needs_review' if proposed else 'unresolved',
                       'reason':reason,'conflicts':conflicts,
                       'source_refs':[{'provider':e.get('provider'),'url':e.get('source_url'),'observed_at':e.get('observed_at'),'status':e.get('status'),'hash':e.get('evidence_sha256')} for e in [profile,primary]],
                       'evidence':{'company_name':p.get('companyName'),'sector':p.get('sector'),'industry':p.get('industry'),
                                   'description':p.get('description'),'sec_name':s.get('name'),'sec_sic':s.get('sic'),
                                   'sec_sic_description':s.get('sicDescription'),'annual_filings':s.get('recent_annual_filings')},
                       'verified':False,'applied_to_registry':False})
    return output


def report(folder, inputs, db, state):
    rows = [json.loads(r[0]) for r in db.execute('SELECT payload FROM results ORDER BY ticker,venue')]
    counts = Counter(r['status'] for r in rows)
    changes = sum(bool(r.get('proposed_node')) and r['proposed_node'] != r.get('old_node') for r in rows)
    sector_counts = Counter(r['proposed_node'].split('.')[0] for r in rows if r.get('proposed_node'))
    depth_counts = Counter(r.get('proposed_depth',0) for r in rows if r.get('proposed_node'))
    state.update(processed_symbols=len(rows), total_symbols=inputs['symbols'], status_counts=dict(counts), proposed_changes=changes)
    save(folder / 'state.json', state)
    save(folder / 'proposals.json', rows)
    lines = ['# Μπάμπης: αναφορά ανεξάρτητης καταλογογράφησης','',f'Κατάσταση: {state["status"]}.',
             f'Σύμβολα που διεκπεραιώθηκαν: {len(rows)} / {inputs["symbols"]}.',
             f'Προτεινόμενες αλλαγές κόμβου: {changes}. Εφαρμοσμένες αλλαγές: 0.',
             f'Αιτήματα πηγών: {state.get("source_requests",0)}. Κλήσεις LLM: 0.', '',
             '## Μέθοδος και όρια','',
             'Χρησιμοποιήθηκε όλο το αποθηκευμένο σύμπαν, χωρίς scanner filters. Οι εταιρικές εγγραφές ομαδοποιήθηκαν με υποψήφια ταυτότητα CIK από την αποθηκευμένη SEC πηγή. Έγινε μία αναζήτηση προφίλ ανά ομάδα, με έλεγχο ταυτότητας πριν από αποδοχή. Τα αποτελέσματα παραμένουν προτάσεις.',
             'FMP: συμπληρωματικό εταιρικό προφίλ και περιγραφή. SEC submissions: ταυτότητα, SIC και αναφορές ετήσιων καταθέσεων. Δεν διαβάστηκαν τα πλήρη κείμενα των ετήσιων εκθέσεων. Το SIC δεν μετατράπηκε αυθαίρετα σε επίσημο GICS.',
             'Οι περιγραφές αποθηκεύονται για έλεγχο· η αυτόματη πρόταση βασίζεται στις ρητές υπάρχουσες αντιστοιχίσεις sector/industry, όχι σε νοηματική ανάλυση της περιγραφής. Ασαφείς κλάδοι δεν εξαναγκάζονται σε υποκλάδο.',
             'ETF και ειδικοί τίτλοι διατηρούνται σε χωριστές κατηγορίες, χωρίς νέα εξέταση συμμετοχών ή δομής προϊόντος. Η ολοκλήρωση της διέλευσης ΔΕΝ σημαίνει πλήρη επαλήθευση των ταξινομήσεων.', '',
             '## Αποτελέσματα','', '| Κατάσταση | Πλήθος |','| --- | ---: |']
    lines += [f'| {k} | {v} |' for k,v in sorted(counts.items())]
    lines += ['', '## Προτεινόμενες τοποθετήσεις στις υπάρχουσες κατηγορίες','',
              '| Κλάδος | Προτάσεις προς έλεγχο |','| --- | ---: |']
    nodes = taxonomy()[1]
    lines += [f'| {nodes[k][5]} | {v} |' for k,v in sorted(sector_counts.items())]
    lines += ['', 'Βάθος προτάσεων: ' + ', '.join(f'{k}: {v}' for k,v in sorted(depth_counts.items())) + '.',
              'Το proposed_path στο proposals.json δείχνει όλη τη διαδρομή κλάδου, ομάδας, βιομηχανίας και υποκλάδου, μέχρι το επίπεδο που υποστηρίζει η πηγή.']
    lines += ['', '## Έλεγχος αξιοπιστίας','',
              'Στο proposals.json κάθε σύμβολο κρατά προηγούμενο/προτεινόμενο κόμβο, αιτιολογία, περιγραφή δραστηριότητας, πηγές, χρόνους, hashes και συγκρούσεις. Ελέγξτε όλες τις συγκρούσεις και τις αλλαγές κλάδου, και δείγμα από αμετάβλητες ή βαθύτερες αντιστοιχίσεις. Καμία πρόταση δεν χαρακτηρίζεται verified.',
              'Οι εγγραφές retained_instrument_category δεν ερευνήθηκαν ως εταιρείες. Οι unresolved χρειάζονται δεδομένα. Οι proposal_needs_review δεν είναι εγκρίσεις. Το evidence.sqlite3 περιέχει τις αποθηκευμένες απαντήσεις και τα attempt records για έλεγχο κόστους/επαναλήψεων.', '',
              f'Πηγαίο στιγμιότυπο: `{inputs["snapshot_id"]}`.',
              'Τα input.json και state.json καταγράφουν έκδοση, αποτυπώματα κώδικα, πρόοδο και όρια. Δεν ενεργοποιήθηκαν trading, τεχνική ανάλυση, Slack ή προγραμματισμένες επανεκκινήσεις.', '']
    (folder / 'report.md').write_text('\n'.join(lines), encoding='utf-8')


def run(folder, max_requests=12000, max_seconds=21600):
    import msvcrt
    lock = (folder.parent / 'worker.lock').open('a+b')
    lock.seek(0)
    try:
        msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
    except OSError:
        lock.close()
        return 2
    inputs = json.loads((folder / 'input.json').read_text(encoding='utf-8'))
    db = sqlite3.connect(folder / 'results.sqlite3')
    db.execute('CREATE TABLE IF NOT EXISTS results(ticker TEXT,venue TEXT,payload TEXT,PRIMARY KEY(ticker,venue))')
    state = {'status':'running','pid':os.getpid(),'started_at':now(),'job_id':folder.name,
             'max_requests':max_requests,'max_seconds':max_seconds,'llm_calls':0,'registry_writes':0}
    sources = None
    try:
        if hashlib.sha256(Path(__file__).with_name('core.py').read_bytes()).hexdigest() != inputs['classifier_sha256'] or hashlib.sha256(Path(__file__).read_bytes()).hexdigest() != inputs['worker_sha256']:
            raise ValueError('Code changed since job preparation')
        if digest(taxonomy()[0]) != inputs['taxonomy_sha256']:
            raise ValueError('Taxonomy changed since job preparation')
        sources = Sources(folder,max_requests,max_seconds)
        report(folder,inputs,db,state)
        for index, rows in enumerate(inputs['groups'].values()):
            if (folder / 'STOP').exists():
                state['status'] = 'stopped_with_pending'
                break
            pending = [r for r in rows if not db.execute('SELECT 1 FROM results WHERE ticker=? AND venue=?',(r['ticker'],r['venue'])).fetchone()]
            if not pending:
                continue
            if rows[0]['instrument_type'] not in CORPORATE:
                proposals = [{**{k:r[k] for k in ['ticker','venue','name','instrument_type']},'old_node':r['node_id'],
                              'proposed_node':None,'status':'retained_instrument_category','reason':'separate_asset_review_required',
                              'verified':False,'applied_to_registry':False} for r in pending]
            else:
                representative = next((r for r in rows if re.fullmatch(r'[A-Z0-9.\-]{1,15}',r['ticker'])),None)
                profile = sources.get('fmp',representative['ticker']) if representative else {'status':'unsupported_symbol_syntax'}
                identity = rows[0]['candidate_cik']
                primary = sources.get('sec',identity) if identity else {'status':'identity_unresolved'}
                proposals = propose(pending,profile,primary)
            with db:
                db.executemany('INSERT INTO results VALUES(?,?,?)',[(r['ticker'],r['venue'],json.dumps(r,ensure_ascii=False)) for r in proposals])
            if index % 100 == 0:
                state.update(updated_at=now(),source_requests=sources.db.execute('SELECT COUNT(*) FROM attempts').fetchone()[0],
                             processed_symbols=db.execute('SELECT COUNT(*) FROM results').fetchone()[0])
                save(folder / 'state.json',state)
        else:
            state['status'] = 'finished_with_review_required'
    except Exception as exc:
        state.update(status='failed',error_type=type(exc).__name__)
    finally:
        state['finished_at'] = now()
        if sources:
            state['source_requests'] = sources.db.execute('SELECT COUNT(*) FROM attempts').fetchone()[0]
            state['provider_circuits'] = dict(sources.db.execute('SELECT provider,status FROM circuits'))
            sources.db.close()
        report(folder,inputs,db,state)
        db.close()
        lock.close()
    return 1 if state['status'] == 'failed' else 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=['start','run','status'])
    parser.add_argument('--job',type=Path)
    parser.add_argument('--max-requests',type=int,default=12000)
    parser.add_argument('--max-seconds',type=int,default=21600)
    args = parser.parse_args(argv)
    if not 0 <= args.max_requests <= 12000 or not 1 <= args.max_seconds <= 21600:
        parser.error('Bounds: 0..12000 requests and 1..21600 seconds')
    if args.action == 'start':
        folder = args.job or prepare()
        log = (folder / 'worker.log').open('a',encoding='utf-8')
        try:
            process = subprocess.Popen([sys.executable,'-B','-m','research_registry.babis_catalog','run','--job',str(folder),
                                        '--max-requests',str(args.max_requests),'--max-seconds',str(args.max_seconds)],
                                       cwd=PROJECT,stdin=subprocess.DEVNULL,stdout=log,stderr=log,
                                       creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW)
        finally:
            log.close()
        save(ROOT / 'latest.json',{'job':str(folder),'pid':process.pid})
        print(json.dumps({'status':'dispatched','pid':process.pid,'job':str(folder)},ensure_ascii=False))
        return 0
    folder = args.job or Path(json.loads((ROOT / 'latest.json').read_text())['job'])
    if args.action == 'status':
        print((folder / 'state.json').read_text(encoding='utf-8'))
        return 0
    return run(folder,args.max_requests,args.max_seconds)


if __name__ == '__main__':
    raise SystemExit(main())
