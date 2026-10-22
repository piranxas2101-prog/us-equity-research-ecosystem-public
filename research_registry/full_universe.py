"""Classify every non-test issue in the public US exchange symbol directories."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
from collections import Counter, defaultdict
from pathlib import Path

from .core import PROJECT, DEFAULT_DB, connect, initialize, taxonomy, digest

SOURCES = PROJECT / 'data/research_universe_sources'
EXPORTS = PROJECT / 'data/research_universe_exports'
CLASSIFIER_VERSION = '1.2.0'
VENUES = {'Q':'Nasdaq','N':'NYSE','A':'NYSE American','P':'NYSE Arca','Z':'Cboe BZX','V':'IEX',
          'F':'Texas Stock Exchange'}
SECTORS = {'technology':'technology','health care':'healthcare','finance':'financials',
           'consumer discretionary':'consumer_discretionary','consumer staples':'consumer_staples',
           'industrials':'industrials','energy':'energy','basic materials':'materials',
           'utilities':'utilities','real estate':'real_estate','telecommunications':'communication_services'}


def parse_directory(text, nasdaq=False):
    lines=text.splitlines()
    footer=[line for line in lines if line.startswith('File Creation Time:')]
    if len(footer)!=1 or not lines or 'Security Name' not in lines[0]:
        raise ValueError('Missing/invalid symbol directory header or timestamp')
    rows=[]
    excluded=0
    for row in csv.DictReader((line for line in lines if not line.startswith('File Creation Time:')),delimiter='|'):
        if row['Test Issue']=='Y':
            excluded+=1
            continue
        ticker=row.get('Symbol') if nasdaq else row.get('ACT Symbol')
        if not ticker or not row.get('Security Name'):
            raise ValueError('Directory row missing identity')
        venue='Q' if nasdaq else row.get('Exchange')
        if not venue:
            raise ValueError('Missing exchange code')
        rows.append({'ticker':ticker,'venue_code':venue,'venue':VENUES.get(venue,'unmapped_exchange:'+venue),
                     'name':row['Security Name'],'etf':row.get('ETF')=='Y',
                     'aliases':sorted({v for v in [ticker,row.get('CQS Symbol'),row.get('NASDAQ Symbol'),row.get('Nasdaq Symbol')] if v}),
                     'directory_row':row})
    keys=[(r['venue_code'],r['ticker']) for r in rows]
    if len(set(keys))!=len(keys):
        raise ValueError('Duplicate directory listings')
    return rows,excluded,footer[0].split('|')[0]


def instrument(row, profile):
    name=row['name'].lower()
    industry=str(profile.get('industry') or '').strip().lower()
    if row['etf']: return 'etf','directory_etf_flag'
    if industry=='blank checks': return 'spac','provider_industry'
    if 'warrant' in name: return 'warrant','security_name'
    if (('common stock' not in name
            and re.search(r'\bpreferred\b.*\b(?:stock|shares?|securities|depositary|series)\b', name))
            or 'preference share' in name): return 'preferred','security_name'
    if 'depositary' in name and 'preferred' in name: return 'preferred','security_name'
    if 'right' in name and ('rights' in name or ' - right' in name): return 'right','security_name'
    if 'exchange traded note' in name or ' etn' in name: return 'etn','security_name'
    if any(t in name for t in [' notes',' note due',' debenture',' bonds']): return 'debt','security_name'
    if ' units' in name or name.endswith(' unit'): return 'unit','security_name'
    if 'fund' in name and not any(t in name for t in ['fundamental','funding','funds group','funds services']): return 'fund_candidate','name_requires_validation'
    if 'depositary' in name or 'depository' in name or ' adr' in name or ' ads' in name: return 'adr_candidate','name_requires_ratio_validation'
    if any(t in name for t in ['common stock','common share','ordinary share','capital stock','class a','class b','class c','shares of beneficial interest']): return 'equity_candidate','security_name'
    return 'unknown_instrument','requires_asset_master'


def classify_listing(row, profile, mappings, nodes, ambiguous=frozenset()):
    kind,basis=instrument(row,profile)
    industry=str(profile.get('industry') or '').strip()
    sector=str(profile.get('sector') or '').strip()
    noncorporate=kind in {'etf','fund_candidate','etn','debt','unit','right','warrant','preferred','spac','unknown_instrument'}
    node=None
    if kind == 'unknown_instrument':
        status='needs_review'
        reason='requires_asset_master'
    elif noncorporate:
        status='instrument_category_only'
        reason='issuer_sector_not_assigned_to_this_instrument'
    elif industry in {'Real Estate Investment Trusts','Trusts Except Educational Religious and Charitable'}:
        status='needs_review'
        reason='fund_or_equity_or_mortgage_reit_requires_disambiguation'
    else:
        node=mappings.get(industry)
        if node:
            status='provisional'
            reason='explicit_internal_industry_crosswalk'
        elif industry in ambiguous:
            # Nasdaq's sector for these industries was wrong more often than right when
            # checked against SEC SIC and provider profiles (2026-09-28: e.g. education
            # under "Real Estate"), so no sector is guessed; the reassessment fills it
            # from sources that agree.
            status='needs_review'
            reason='ambiguous_provider_industry'
        else:
            node=SECTORS.get(sector.lower())
            status='provisional' if node else 'needs_review'
            reason='provider_sector_only' if node else 'missing_or_ambiguous_provider_classification'
    if node and node not in nodes: raise ValueError('Invalid crosswalk node: '+node)
    path=[]
    cursor=node
    while cursor:
        n=nodes[cursor]
        path.append({'id':cursor,'level':n[4],'label_el':n[5]})
        cursor=n[3]
    path.reverse()
    return {**{k:row[k] for k in ('ticker','venue_code','venue','name')},
            'instrument_type':kind,'instrument_basis':basis,'raw_sector':sector or None,'raw_industry':industry or None,
            'node_id':node,'depth':len(path),'path':path,'status':status,'reason':reason,
            'provider_sector_differs':bool(node and SECTORS.get(sector.lower()) and node.split('.')[0]!=SECTORS[sector.lower()]),
            'profile_available':bool(profile),'official_gics':False}


def schema(db):
    initialize(db)
    db.executescript('''
    CREATE TABLE IF NOT EXISTS universe_snapshot(
        snapshot_id TEXT PRIMARY KEY, retrieved_at TEXT NOT NULL, provenance_json TEXT NOT NULL, summary_json TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS listed_symbol(
        snapshot_id TEXT NOT NULL REFERENCES universe_snapshot,
        venue_code TEXT NOT NULL,ticker TEXT NOT NULL,name TEXT NOT NULL,
        instrument_type TEXT NOT NULL, scheme TEXT NOT NULL, version TEXT NOT NULL,
        node_id TEXT, status TEXT NOT NULL, payload_json TEXT NOT NULL,
        PRIMARY KEY(snapshot_id,venue_code,ticker),
        FOREIGN KEY(scheme,version,node_id) REFERENCES taxonomy_node(scheme,version,node_id));
    CREATE INDEX IF NOT EXISTS listed_symbol_taxonomy ON listed_symbol(snapshot_id,node_id);
    ''')
    from .classification_governance import governance_schema
    governance_schema(db)


def build(db, sources=SOURCES, exports=EXPORTS):
    metadata=json.loads((sources/'manifest.json').read_text(encoding='utf-8'))
    byname={m['name']:m for m in metadata}
    raws={}
    for filename in ('nasdaqlisted.txt','otherlisted.txt','nasdaq_profiles.json'):
        meta=byname.get(filename,{})
        if meta.get('status')!='ok': raise ValueError('Source unavailable: '+filename)
        raw=(sources/filename).read_bytes()
        if hashlib.sha256(raw).hexdigest()!=meta['sha256']: raise ValueError('Source hash mismatch')
        raws[filename]=raw.decode('utf-8-sig')
    profile_payload=json.loads(raws['nasdaq_profiles.json'])
    profiles=profile_payload.get('data',{}).get('rows')
    if not isinstance(profiles,list) or not profiles: raise ValueError('Missing bulk profiles')
    indexed=defaultdict(list)
    for p in profiles:
        symbol=str(p.get('symbol') or '').strip()
        if symbol: indexed[symbol].append(p)
    data,nodes=taxonomy()
    crosswalk=json.loads((Path(__file__).with_name('nasdaq_industries.json')).read_text(encoding='utf-8'))
    mappings=crosswalk['mappings']
    ambiguous=frozenset(crosswalk.get('ambiguous_industries') or ())
    if any(n not in nodes for n in mappings.values()): raise ValueError('Invalid crosswalk')
    listings=[]; excluded=0; dates={}
    for filename in ('nasdaqlisted.txt','otherlisted.txt'):
        rows,tests,date=parse_directory(raws[filename],filename=='nasdaqlisted.txt')
        excluded+=tests; dates[filename]=date
        for row in rows:
            matches={digest(p):p for alias in row['aliases'] for p in indexed.get(alias,[])}
            profile=next(iter(matches.values())) if len(matches)==1 else {}
            result=classify_listing(row,profile,mappings,nodes,ambiguous)
            result['profile_match']='exact_directory_alias' if len(matches)==1 else 'ambiguous' if matches else 'missing'
            result['source_directory']=filename
            result['directory_aliases']=row['aliases']
            result['raw_directory']=row['directory_row']
            result['raw_profile']=profile
            listings.append(result)
    keys={(r['venue_code'],r['ticker']) for r in listings}
    if len(keys)!=len(listings): raise ValueError('Duplicate listing across files')
    implementation_hash=hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    snapshot_id=digest([sorted((m['name'],m['sha256']) for m in metadata),data,crosswalk,implementation_hash])
    counts=Counter(r['path'][0]['id'] if r['path'] else 'not_sector_classified' for r in listings)
    summary={'snapshot_id':snapshot_id,'listings':len(listings),'unique_symbols':len({r['ticker'] for r in listings}),
             'excluded_test_issues':excluded,'source_file_times':dates,'exchanges':dict(Counter(r['venue'] for r in listings)),
             'instrument_types':dict(Counter(r['instrument_type'] for r in listings)),
             'sector_counts':dict(sorted(counts.items())), 'depth_counts':dict(sorted(Counter(r['depth'] for r in listings).items())),
             'status_counts':dict(Counter(r['status'] for r in listings)),
             'profile_missing_or_ambiguous':sum(not r['profile_available'] for r in listings),
             'corporate_sector_review':sum(r['status']=='needs_review' and r['instrument_type']!='unknown_instrument' for r in listings),
             'unknown_instrument_review':sum(r['instrument_type']=='unknown_instrument' for r in listings),
             'partial_hierarchy_review':sum(bool(r['node_id']) and r['depth']<4 for r in listings),
             'scope':'All non-test equity/ETP issues in Nasdaq Trader Nasdaq-listed and other-listed directories; OTC/options/futures excluded.',
             'coverage_note':'Full directory processing, not a claim that every security has a verified corporate subindustry.'}
    provenance={'sources':metadata,'file_times':dates,'taxonomy_version':data['version'],'crosswalk_version':crosswalk['version'],
                'classifier_version':CLASSIFIER_VERSION,'implementation_sha256':implementation_hash}
    schema(db)
    with db:
        db.execute('INSERT OR IGNORE INTO universe_snapshot VALUES(?,?,?,?)',(snapshot_id,max(m['retrieved_at'] for m in metadata),json.dumps(provenance),json.dumps(summary)))
        db.executemany('INSERT OR IGNORE INTO listed_symbol VALUES(?,?,?,?,?,?,?,?,?,?)',
            [(snapshot_id,r['venue_code'],r['ticker'],r['name'],r['instrument_type'],data['scheme'],data['version'],r['node_id'],r['status'],json.dumps(r,ensure_ascii=False)) for r in listings])
        actual=db.execute('SELECT COUNT(*) FROM listed_symbol WHERE snapshot_id=?',(snapshot_id,)).fetchone()[0]
        if actual!=len(listings): raise ValueError('Row count reconciliation failed')
        if db.execute('PRAGMA foreign_key_check').fetchall(): raise ValueError('Foreign-key failure')
    exports.mkdir(parents=True,exist_ok=True)
    fields=['ticker','venue','name','instrument_type','sector','industry_group','industry','subindustry','node_id','depth','status','reason','raw_sector','raw_industry','profile_match']
    with (exports/'all_us_listed_symbols.csv').open('w',encoding='utf-8-sig',newline='') as handle:
        writer=csv.DictWriter(handle,fieldnames=fields); writer.writeheader()
        for r in sorted(listings,key=lambda r:(r['node_id'] or '~',r['ticker'],r['venue'])):
            out={k:r.get(k) for k in fields}
            for level,key in enumerate(['sector','industry_group','industry','subindustry']):
                out[key]=r['path'][level]['label_el'] if level<len(r['path']) else ''
            writer.writerow(out)
    (exports/'coverage.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
    (exports/'provenance.json').write_text(json.dumps(provenance,ensure_ascii=False,indent=2),encoding='utf-8')
    unresolved=[{k:r.get(k) for k in ['ticker','venue','name','instrument_type','raw_sector','raw_industry','reason','depth']} for r in listings if r['status']=='needs_review' or (r['node_id'] and r['depth']<4)]
    (exports/'classification_review.json').write_text(json.dumps(unresolved,ensure_ascii=False,indent=2),encoding='utf-8')
    return summary


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--db',type=Path,default=DEFAULT_DB)
    sub=p.add_subparsers(dest='command',required=True)
    b=sub.add_parser('build');b.add_argument('--sources',type=Path,default=SOURCES);b.add_argument('--exports',type=Path,default=EXPORTS)
    sub.add_parser('summary')
    b=sub.add_parser('browse'); b.add_argument('--node'); b.add_argument('--ticker'); b.add_argument('--limit',type=int,default=30)
    args=p.parse_args(argv)
    db=connect(args.db)
    try:
        if args.command=='build': result=build(db,args.sources,args.exports)
        else:
            schema(db)
            latest=db.execute('SELECT * FROM universe_snapshot ORDER BY retrieved_at DESC,rowid DESC LIMIT 1').fetchone()
            if latest is None: raise ValueError('Run universe build first')
            if args.command=='summary': result=json.loads(latest['summary_json'])
            else:
                clause='snapshot_id=?'; params=[latest['snapshot_id']]
                if args.node:
                    clause+=' AND (node_id=? OR substr(node_id,1,length(?)+1)=?||\'.\')';params.extend([args.node]*3)
                if args.ticker: clause+=' AND ticker=?';params.append(args.ticker.upper())
                params.append(max(1,args.limit))
                result=[{k:r[k] for k in ['ticker','venue_code','name','instrument_type','node_id','status']} for r in db.execute('SELECT * FROM effective_listed_symbol WHERE '+clause+' ORDER BY ticker LIMIT ?',params)]
        print(json.dumps(result,ensure_ascii=False,indent=2))
    finally: db.close()
    return 0


if __name__=='__main__':
    raise SystemExit(main())
