"""Fetch public bulk listing/reference snapshots, once each, with provenance."""
import concurrent.futures
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import HTTPError

DEST = Path(__file__).resolve().parents[1] / 'data/research_universe_sources'
URLS = {
    'nasdaqlisted.txt': 'https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt',
    'otherlisted.txt': 'https://www.nasdaqtrader.com/dynamic/SymDir/otherlisted.txt',
    'nasdaq_profiles.json': 'https://api.nasdaq.com/api/screener/stocks?tableonly=true&limit=100000&download=true',
}


def fetch(item):
    name,url=item
    result={'name':name,'url':url,'retrieved_at':datetime.now(timezone.utc).isoformat()}
    try:
        req=Request(url,headers={'User-Agent':'Mozilla/5.0','Accept':'application/json,text/plain,*/*','Origin':'https://www.nasdaq.com'})
        with urlopen(req,timeout=25) as response:
            raw=response.read()
        if name.endswith('.json'):
            json.loads(raw)
        elif b'Security Name|' not in raw:
            raise ValueError('Not a symbol directory')
        (DEST/name).write_bytes(raw)
        result.update(status='ok',bytes=len(raw),sha256=hashlib.sha256(raw).hexdigest())
    except HTTPError as exc:
        result.update(status='http_error',http_status=exc.code)
    except Exception as exc:
        result.update(status='failed',error_type=type(exc).__name__)
    return result


def main():
    DEST.mkdir(parents=True,exist_ok=True)
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
        results=list(pool.map(fetch,URLS.items()))
    (DEST/'manifest.json').write_text(json.dumps(results,indent=2),encoding='utf-8')
    print(json.dumps(results,indent=2))


if __name__=='__main__':
    main()
