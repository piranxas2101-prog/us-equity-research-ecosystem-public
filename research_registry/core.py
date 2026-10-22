from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path

from common import SQLITE_BUSY_TIMEOUT_SECONDS, enable_wal

ROOT = Path(__file__).resolve().parent
PROJECT = ROOT.parent
DEFAULT_DB = PROJECT / 'data/research_registry.db'


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def utc(value):
    result = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    if result.tzinfo is None:
        raise ValueError('Timestamp must include a timezone')
    return result.astimezone(timezone.utc).isoformat()


@lru_cache(maxsize=1)
def taxonomy():
    data = json.loads((ROOT / 'taxonomy.json').read_text(encoding='utf-8'))
    nodes = {}
    leaves = set()
    for row in data['rows']:
        if len(row) != 8:
            raise ValueError('Invalid taxonomy row')
        for level in range(1, 5):
            node_id = '.'.join(row[::2][:level])
            node = (data['scheme'], data['version'], node_id,
                    '.'.join(row[::2][:level-1]) if level > 1 else None, level, row[level*2-1])
            if node_id in nodes and nodes[node_id] != node:
                raise ValueError('Conflicting taxonomy node: ' + node_id)
            nodes[node_id] = node
        if node_id in leaves:
            raise ValueError('Duplicate taxonomy leaf: ' + node_id)
        leaves.add(node_id)
    return data, nodes


# Explicit internal mappings, never official GICS assignments. Broad labels map
# to ancestors where the provider does not distinguish the proposed leaves.
ALIASES = {
    ('technology', 'semiconductors'): 'technology.semiconductors.chips',
    ('technology', 'semiconductor equipment & materials'): 'technology.semiconductors.equipment',
    ('technology', 'software - application'): 'technology.software.applications',
    ('technology', 'software - infrastructure'): 'technology.software.infrastructure',
    ('technology', 'information technology services'): 'technology.hardware_services.services',
    ('technology', 'communication equipment'): 'technology.hardware_services.hardware.networking',
    ('healthcare', 'biotechnology'): 'healthcare.therapeutics.medicines.biotech',
    ('healthcare', 'drug manufacturers - general'): 'healthcare.therapeutics.medicines.pharma',
    ('healthcare', 'drug manufacturers - specialty & generic'): 'healthcare.therapeutics.medicines.pharma',
    ('healthcare', 'medical devices'): 'healthcare.care.equipment.devices',
    ('financials', 'banks - regional'): 'financials.banking_credit.banks.regional',
    ('financials', 'banks - diversified'): 'financials.banking_credit.banks.diversified',
    ('financials', 'asset management'): 'financials.markets.investment.management',
    ('financials', 'capital markets'): 'financials.markets.investment',
    ('consumer_discretionary', 'specialty retail'): 'consumer_discretionary.goods_retail.retail.specialty',
    ('consumer_discretionary', 'auto manufacturers'): 'consumer_discretionary.mobility.automotive.makers',
    ('consumer_discretionary', 'auto parts'): 'consumer_discretionary.mobility.automotive.suppliers',
    ('consumer_staples', 'packaged foods'): 'consumer_staples.products.food.packaged',
    ('consumer_staples', 'farm products'): 'consumer_staples.products.food.agricultural',
    ('consumer_staples', 'beverages - brewers'): 'consumer_staples.products.beverages_tobacco.beverages',
    ('industrials', 'aerospace & defense'): 'industrials.capital_goods.aerospace.systems',
    ('industrials', 'marine shipping'): 'industrials.transport.freight.marine',
    ('industrials', 'farm & heavy construction machinery'): 'industrials.capital_goods.machinery.heavy',
    ('energy', 'oil & gas e&p'): 'energy.hydrocarbons.production.upstream',
    ('energy', 'oil & gas integrated'): 'energy.hydrocarbons.production.integrated',
    ('materials', 'gold'): 'materials.resources.metals.precious',
    ('materials', 'silver'): 'materials.resources.metals.precious',
    ('materials', 'other precious metals & mining'): 'materials.resources.metals.precious',
    ('materials', 'copper'): 'materials.resources.metals.industrial',
    ('materials', 'steel'): 'materials.resources.metals.industrial',
    ('utilities', 'utilities - renewable'): 'utilities.generation.independent.renewable',
    ('utilities', 'utilities - regulated electric'): 'utilities.networks.regulated.electric',
    ('real_estate', 'reit - industrial'): 'real_estate.ownership.equity_reits.industrial',
    ('communication_services', 'internet content & information'): 'communication_services.platforms.interactive.social_search',
    ('communication_services', 'entertainment'): 'communication_services.media.content',
    # FMP industry names (2026-09-28). Broad provider industries map to a safe
    # group, not a guessed leaf. FMP files education under Consumer Defensive,
    # but this taxonomy (like GICS) puts it in consumer services.
    ('consumer_staples', 'education & training services'): 'consumer_discretionary.services_housing.other.education_personal',
    ('consumer_staples', 'agricultural farm products'): 'consumer_staples.products.food.agricultural',
    ('consumer_staples', 'tobacco'): 'consumer_staples.products.beverages_tobacco.tobacco',
    ('consumer_staples', 'beverages - non-alcoholic'): 'consumer_staples.products.beverages_tobacco.beverages',
    ('consumer_staples', 'beverages - alcoholic'): 'consumer_staples.products.beverages_tobacco.beverages',
    ('consumer_staples', 'beverages - wineries & distilleries'): 'consumer_staples.products.beverages_tobacco.beverages',
    ('consumer_staples', 'food confectioners'): 'consumer_staples.products.food.packaged',
    ('consumer_staples', 'household & personal products'): 'consumer_staples.household_distribution.care',
    ('consumer_staples', 'grocery stores'): 'consumer_staples.household_distribution.distribution.retail',
    ('consumer_staples', 'discount stores'): 'consumer_staples.household_distribution.distribution.retail',
    ('consumer_staples', 'food distribution'): 'consumer_staples.household_distribution.distribution.wholesale',
    ('technology', 'hardware, equipment & parts'): 'technology.hardware_services.hardware',
    ('technology', 'consumer electronics'): 'technology.hardware_services.hardware',
    ('technology', 'computer hardware'): 'technology.hardware_services.hardware.computing',
    ('technology', 'electronic gaming & multimedia'): 'communication_services.media.content.games',
    ('industrials', 'electrical equipment & parts'): 'industrials.capital_goods.electrical',
    ('industrials', 'industrial - machinery'): 'industrials.capital_goods.machinery',
    ('industrials', 'agricultural - machinery'): 'industrials.capital_goods.machinery.heavy',
    ('industrials', 'engineering & construction'): 'industrials.capital_goods.construction.contractors',
    ('industrials', 'construction'): 'industrials.capital_goods.construction',
    ('industrials', 'airlines, airports & air services'): 'industrials.transport',
    ('industrials', 'waste management'): 'industrials.business_services.commercial.environment',
    ('industrials', 'security & protection services'): 'industrials.business_services.commercial',
    ('industrials', 'specialty business services'): 'industrials.business_services.commercial',
    ('industrials', 'staffing & employment services'): 'industrials.business_services.commercial.professional',
    ('industrials', 'consulting services'): 'industrials.business_services.commercial.professional',
    ('industrials', 'integrated freight & logistics'): 'industrials.transport.freight.logistics',
    ('industrials', 'railroads'): 'industrials.transport.freight.rail_road',
    ('industrials', 'trucking'): 'industrials.transport.freight.rail_road',
    ('materials', 'construction materials'): 'materials.resources.construction',
    ('materials', 'chemicals'): 'materials.chemicals.chemicals',
    ('materials', 'chemicals - specialty'): 'materials.chemicals.chemicals.specialty',
    ('materials', 'agricultural inputs'): 'materials.chemicals.chemicals.fertilizers',
    ('materials', 'paper, lumber & forest products'): 'materials.forest_packaging.forest',
    ('materials', 'packaging & containers'): 'materials.forest_packaging.packaging.containers',
    ('materials', 'aluminum'): 'materials.resources.metals.industrial',
    ('communication_services', 'telecommunications services'): 'communication_services.telecom',
    ('communication_services', 'advertising agencies'): 'communication_services.media.publishing_ads.advertising',
    ('communication_services', 'publishing'): 'communication_services.media.publishing_ads.publishing',
    ('communication_services', 'broadcasting'): 'communication_services.media.content.film_tv',
    ('healthcare', 'medical - devices'): 'healthcare.care.equipment.devices',
    ('healthcare', 'medical - instruments & supplies'): 'healthcare.care.equipment',
    ('healthcare', 'medical - care facilities'): 'healthcare.care.services.providers',
    ('healthcare', 'medical - pharmaceuticals'): 'healthcare.therapeutics.medicines',
    ('healthcare', 'medical - healthcare plans'): 'healthcare.care.services.managed_care',
    ('healthcare', 'medical - distribution'): 'healthcare.care.services.distribution',
    ('financials', 'financial - credit services'): 'financials.banking_credit.lending',
    ('financials', 'financial - mortgages'): 'financials.banking_credit.lending.mortgage',
    ('financials', 'financial - capital markets'): 'financials.markets.investment',
    ('financials', 'financial - data & stock exchanges'): 'financials.markets.infrastructure.exchanges',
    ('financials', 'insurance - property & casualty'): 'financials.insurance.underwriting.property',
    ('financials', 'insurance - life'): 'financials.insurance.underwriting.life',
    ('financials', 'insurance - brokers'): 'financials.insurance.services.brokers',
    ('financials', 'insurance - reinsurance'): 'financials.insurance.services.reinsurance',
    ('consumer_discretionary', 'auto - parts'): 'consumer_discretionary.mobility.automotive.suppliers',
    ('consumer_discretionary', 'auto - manufacturers'): 'consumer_discretionary.mobility.automotive.makers',
    ('consumer_discretionary', 'auto - dealerships'): 'consumer_discretionary.goods_retail.retail.specialty',
    ('consumer_discretionary', 'restaurants'): 'consumer_discretionary.services_housing.leisure.restaurants',
    ('consumer_discretionary', 'leisure'): 'consumer_discretionary.services_housing.leisure',
    ('consumer_discretionary', 'gambling, resorts & casinos'): 'consumer_discretionary.services_housing.leisure.gaming',
    ('consumer_discretionary', 'travel services'): 'consumer_discretionary.services_housing.leisure.travel',
    ('consumer_discretionary', 'travel lodging'): 'consumer_discretionary.services_housing.leisure.travel',
    ('consumer_discretionary', 'furnishings, fixtures & appliances'): 'consumer_discretionary.goods_retail.goods.home',
    ('consumer_discretionary', 'apparel - footwear & accessories'): 'consumer_discretionary.goods_retail.goods.apparel',
    ('consumer_discretionary', 'apparel - manufacturers'): 'consumer_discretionary.goods_retail.goods.apparel',
    ('consumer_discretionary', 'apparel - retail'): 'consumer_discretionary.goods_retail.retail.specialty',
    ('consumer_discretionary', 'home improvement'): 'consumer_discretionary.goods_retail.retail.specialty',
    ('consumer_discretionary', 'department stores'): 'consumer_discretionary.goods_retail.retail.broadline',
    ('consumer_discretionary', 'residential construction'): 'consumer_discretionary.services_housing.other.homebuilding',
    ('consumer_discretionary', 'personal products & services'): 'consumer_discretionary.services_housing.other.education_personal',
    ('energy', 'coal'): 'energy.support_fuels.fuels.coal',
    ('energy', 'uranium'): 'energy.support_fuels.fuels',
    ('energy', 'oil & gas exploration & production'): 'energy.hydrocarbons.production.upstream',
    ('energy', 'oil & gas refining & marketing'): 'energy.hydrocarbons.processing_transport.refining',
    ('energy', 'oil & gas midstream'): 'energy.hydrocarbons.processing_transport.midstream',
    ('energy', 'oil & gas equipment & services'): 'energy.support_fuels.services.oilfield',
    ('energy', 'oil & gas drilling'): 'energy.support_fuels.services.oilfield',
    ('utilities', 'regulated electric'): 'utilities.networks.regulated.electric',
    ('utilities', 'regulated gas'): 'utilities.networks.regulated.gas',
    ('utilities', 'regulated water'): 'utilities.networks.regulated.water',
    ('utilities', 'diversified utilities'): 'utilities.networks.regulated.multi',
    ('utilities', 'renewable utilities'): 'utilities.generation.independent.renewable',
    ('utilities', 'independent power producers'): 'utilities.generation.independent',
    ('real_estate', 'reit - mortgage'): 'real_estate.ownership.mortgage_reits',
    ('real_estate', 'reit - retail'): 'real_estate.ownership.equity_reits.commercial',
    ('real_estate', 'reit - office'): 'real_estate.ownership.equity_reits.commercial',
    ('real_estate', 'reit - residential'): 'real_estate.ownership.equity_reits.residential',
    ('real_estate', 'reit - specialty'): 'real_estate.ownership.equity_reits.specialized',
    ('real_estate', 'reit - healthcare facilities'): 'real_estate.ownership.equity_reits',
    ('real_estate', 'reit - hotel & motel'): 'real_estate.ownership.equity_reits',
    ('real_estate', 'reit - diversified'): 'real_estate.ownership.equity_reits',
    ('real_estate', 'real estate - services'): 'real_estate.development_services.operations.services',
    ('real_estate', 'real estate - development'): 'real_estate.development_services.operations.development',
}


def classify(row):
    # Lazy import avoids a cycle when the existing classifier enriches its output.
    from symbol_classification import SECTOR_ALIASES, SECTORS
    data, nodes = taxonomy()
    sector = str(row.get('sector_id') or '')
    if sector not in SECTORS or sector == 'unknown':
        sector = SECTOR_ALIASES.get(str(row.get('sector') or row.get('sector_label') or '').strip().lower(), 'unknown')
    industry = str(row.get('industry') or row.get('industry_label') or '').strip()
    node_id = ALIASES.get((sector, industry.lower()), sector if sector != 'unknown' else None)
    if node_id is not None and node_id not in nodes:
        raise ValueError('Mapping references missing taxonomy node')
    path = []
    cursor = node_id
    while cursor:
        node = nodes[cursor]
        path.append({'id': cursor, 'level': node[4], 'label_el': node[5]})
        cursor = node[3]
    return {'scheme': data['scheme'], 'version': data['version'], 'node_id': node_id,
            'path': list(reversed(path)), 'status': 'provisional' if node_id else 'unclassified',
            'depth': len(path), 'raw_industry': industry or None,
            'basis': 'explicit_provider_alias' if (sector, industry.lower()) in ALIASES else 'sector_only' if node_id else 'missing',
            'official_gics': False}


def connect(path=DEFAULT_DB):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path, timeout=SQLITE_BUSY_TIMEOUT_SECONDS)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA foreign_keys=ON')
    enable_wal(db)
    return db


def initialize(db):
    data, nodes = taxonomy()
    exists = db.execute("SELECT 1 FROM sqlite_master WHERE name='registry_meta'").fetchone()
    if not exists:
        # Refuse to apply this schema over an unrelated existing database.
        if db.execute("SELECT 1 FROM sqlite_master WHERE type='table'").fetchone():
            raise ValueError('Use a dedicated research_registry database')
        db.executescript((ROOT / 'schema.sql').read_text(encoding='utf-8'))
        db.executescript('''
        CREATE TABLE registry_meta(version INTEGER NOT NULL);
        INSERT INTO registry_meta VALUES(1);
        CREATE TABLE import_batch(batch_id TEXT PRIMARY KEY, path TEXT NOT NULL, as_of TEXT NOT NULL, kind TEXT NOT NULL);
        CREATE TABLE identity_observation(source_id TEXT NOT NULL REFERENCES source, ticker TEXT NOT NULL, company_id TEXT NOT NULL REFERENCES company, observed_at TEXT NOT NULL, venue TEXT NOT NULL, PRIMARY KEY(source_id,ticker,company_id,venue));
        CREATE TABLE pending_record(record_id TEXT PRIMARY KEY, source_id TEXT NOT NULL REFERENCES source, ticker TEXT, reason TEXT NOT NULL, payload_json TEXT NOT NULL);
        CREATE TABLE profile_observation(record_id TEXT PRIMARY KEY, company_id TEXT NOT NULL REFERENCES company, source_id TEXT NOT NULL REFERENCES source, as_of TEXT NOT NULL, payload_json TEXT NOT NULL);
        ''')
    if db.execute('SELECT version FROM registry_meta').fetchone()[0] != 1:
        raise ValueError('Unsupported registry schema version')
    for node in sorted(nodes.values(), key=lambda n:n[4]):
        old = db.execute('SELECT * FROM taxonomy_node WHERE scheme=? AND version=? AND node_id=?', node[:3]).fetchone()
        if old and tuple(old) != node:
            raise ValueError('Taxonomy version is immutable')
        db.execute('INSERT OR IGNORE INTO taxonomy_node VALUES(?,?,?,?,?,?)', node)
    db.commit()


def source_file(db, path, kind, as_of):
    raw = path.read_bytes()
    checksum = hashlib.sha256(raw).hexdigest()
    source_id = digest([kind, checksum, as_of])
    db.execute('INSERT OR IGNORE INTO source VALUES(?,?,?,?,?,?,?)',
               (source_id, path.resolve().as_uri(), 'local_cached_artifact', kind, None,
                datetime.now(timezone.utc).isoformat(), checksum))
    return source_id, json.loads(raw.decode('utf-8-sig'))


def pending(db, source_id, row, reason):
    db.execute('INSERT OR IGNORE INTO pending_record VALUES(?,?,?,?,?)',
               (digest([source_id,row,reason]), source_id, row.get('ticker'), reason, json.dumps(row,ensure_ascii=False)))


def import_identities(db, path, as_of):
    as_of = utc(as_of)
    with db:
        source_id, payload = source_file(db, path, 'sec_identity_cache', as_of)
        if db.execute('SELECT 1 FROM import_batch WHERE batch_id=?',(source_id,)).fetchone():
            return {'status':'unchanged'}
        if 'fields' in payload and 'data' in payload:
            rows = [dict(zip(payload['fields'], row)) for row in payload['data']]
        else:
            rows = list(payload.values())
        count = 0
        for row in rows:
            cik = row.get('cik') or row.get('cik_str')
            ticker = str(row.get('ticker') or '').strip().upper()
            name = row.get('name') or row.get('title')
            if not str(cik).isdigit() or not ticker or not name:
                pending(db, source_id, row, 'missing_sec_identity')
                continue
            cik = str(int(cik)).zfill(10)
            company_id = 'sec:' + cik
            db.execute('INSERT OR IGNORE INTO company VALUES(?,?,?,?,?,?)',
                       (company_id, name, cik, None, None, 'identity_from_sec_cache'))
            # SEC ticker cache identifies issuers, not a verified share-class master.
            # Store observations; do not invent security IDs or listing start dates.
            db.execute('INSERT OR IGNORE INTO identity_observation VALUES(?,?,?,?,?)',
                       (source_id,ticker,company_id,as_of,str(row.get('exchange') or 'unknown')))
            count += 1
        db.execute('INSERT INTO import_batch VALUES(?,?,?,?)',(source_id,str(path),as_of,'identities'))
    return {'status':'imported','identity_observations':count}


def resolve_company(db, ticker, as_of):
    rows = db.execute('SELECT company_id, observed_at FROM identity_observation WHERE ticker=? AND observed_at<=? ORDER BY observed_at DESC', (ticker,as_of)).fetchall()
    if not rows:
        return None
    latest = rows[0]['observed_at']
    ids = {r['company_id'] for r in rows if r['observed_at']==latest}
    return next(iter(ids)) if len(ids)==1 else None


def assign(db, company_id, classification, source_id, as_of):
    if not classification['node_id']:
        return
    scheme, version = classification['scheme'], classification['version']
    old = db.execute('SELECT * FROM classification WHERE company_id=? AND scheme=? AND is_primary=1 AND valid_to IS NULL', (company_id,scheme)).fetchone()
    if old and old['valid_from'] >= as_of:
        if old['node_id'] != classification['node_id']:
            pending(db,source_id,{'company_id':company_id,**classification},'historical_or_conflicting_classification')
        return
    if old and old['node_id']==classification['node_id'] and old['version']==version:
        return
    if old:
        db.execute('UPDATE classification SET valid_to=? WHERE classification_id=?', (as_of,old['classification_id']))
    db.execute('INSERT INTO classification VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
               (digest([company_id,source_id,as_of]), company_id,scheme,version,classification['node_id'],1,
                'provisional',classification['basis'],source_id,as_of,None,datetime.now(timezone.utc).isoformat()))


def import_profiles(db, path):
    payload = json.loads(path.read_text(encoding='utf-8-sig'))
    as_of = utc(payload['run_timestamp'])
    rows = payload.get('results', payload.get('shortlist'))
    if not isinstance(rows,list):
        raise ValueError('Expected scanner results or Babis shortlist')
    with db:
        source_id, _ = source_file(db,path,'research_profile_snapshot',as_of)
        if db.execute('SELECT 1 FROM import_batch WHERE batch_id=?',(source_id,)).fetchone():
            return {'status':'unchanged'}
        imported = 0
        for row in rows:
            ticker = str(row.get('ticker') or row.get('symbol') or '').strip().upper()
            company_id = resolve_company(db,ticker,as_of)
            if not company_id:
                pending(db,source_id,row,'missing_or_ambiguous_identity_at_snapshot')
                continue
            classification = classify(row)
            # Store the original artifact for traceability; technical fields remain
            # historical raw payload, never promoted to research metrics or signals.
            db.execute('INSERT OR IGNORE INTO profile_observation VALUES(?,?,?,?,?)',
                       (digest([source_id,row]),company_id,source_id,as_of,json.dumps(row,ensure_ascii=False)))
            assign(db,company_id,classification,source_id,as_of)
            if classification['depth']<4:
                pending(db,source_id,row,'classification_requires_review')
            imported += 1
        db.execute('INSERT INTO import_batch VALUES(?,?,?,?)',(source_id,str(path),as_of,'profiles'))
    return {'status':'imported','profiles':imported,'unresolved_identity_rows':len(rows)-imported}


def summary(db):
    tables = ['company','identity_observation','profile_observation','pending_record','taxonomy_node']
    result = {t:db.execute('SELECT COUNT(*) FROM '+t).fetchone()[0] for t in tables}
    result['classification_depths'] = [dict(r) for r in db.execute('SELECT n.level,COUNT(*) AS companies FROM classification c JOIN taxonomy_node n USING(scheme,version,node_id) WHERE c.valid_to IS NULL GROUP BY n.level')]
    result['unclassified_companies'] = db.execute('SELECT COUNT(*) FROM company WHERE company_id NOT IN (SELECT company_id FROM classification WHERE valid_to IS NULL)').fetchone()[0]
    return result


def browse(db, node=None):
    if node is None:
        return [dict(r) for r in db.execute('SELECT node_id,label_el,level FROM taxonomy_node WHERE level=1 ORDER BY node_id')]
    data,_ = taxonomy()
    children = [dict(r) for r in db.execute('SELECT node_id,label_el,level FROM taxonomy_node WHERE scheme=? AND version=? AND parent_id=? ORDER BY node_id',(data['scheme'],data['version'],node))]
    companies = [dict(r) for r in db.execute('SELECT co.company_id,co.legal_name,c.node_id,c.status FROM classification c JOIN company co USING(company_id) WHERE c.scheme=? AND c.version=? AND c.valid_to IS NULL AND (c.node_id=? OR substr(c.node_id,1,length(?)+1)=?||\'.\') ORDER BY co.legal_name',(data['scheme'],data['version'],node,node,node))]
    return {'children':children,'companies':companies}


def main(argv=None):
    parser=argparse.ArgumentParser(description='Local research registry; no network or broker calls')
    parser.add_argument('--db',type=Path,default=DEFAULT_DB)
    sub=parser.add_subparsers(dest='command',required=True)
    sub.add_parser('init'); sub.add_parser('summary')
    p=sub.add_parser('import-identities'); p.add_argument('path',type=Path); p.add_argument('--as-of',required=True,help='Known cache observation timestamp including timezone')
    p=sub.add_parser('import-profiles'); p.add_argument('path',type=Path)
    p=sub.add_parser('browse'); p.add_argument('--node')
    sub.add_parser('pending')
    up=sub.add_parser('universe'); up.add_argument('universe_args',nargs=argparse.REMAINDER)
    args=parser.parse_args(argv)
    if args.command=='universe':
        from .full_universe import main as universe_main
        return universe_main(['--db',str(args.db),*args.universe_args])
    db=connect(args.db)
    try:
        initialize(db)
        if args.command=='import-identities': result=import_identities(db,args.path,args.as_of)
        elif args.command=='import-profiles': result=import_profiles(db,args.path)
        elif args.command=='browse': result=browse(db,args.node)
        elif args.command=='pending': result=[dict(r) for r in db.execute('SELECT reason,COUNT(*) AS records FROM pending_record GROUP BY reason')]
        else: result=summary(db)
        print(json.dumps(result,ensure_ascii=False,indent=2))
    finally:
        db.close()
    return 0
