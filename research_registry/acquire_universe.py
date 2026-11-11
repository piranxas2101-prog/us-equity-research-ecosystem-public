"""Fetch public bulk listing/reference snapshots, once each, with provenance.

A source that refuses once is asked again (2026-11-11: one 403 from the Nasdaq screener blocked the whole nightly
run and the fallback research stood in; the same request worked hours later). If the Nasdaq profiles still do not
come, the last good file is used again while it is at most ``STALE_MAX_HOURS`` old and the manifest says so
(``stale_reused``, with the time it was really retrieved) - owner's decision 2026-11-11. The two symbol directories
decide which symbols exist and must always be fresh.

When the last manifest holds no good row for the profiles (the night before failed too, as on 2026-11-12, when the
reuse never applied because the manifest only remembered the failure), the age is the file's own time: the file is
written only after a download that parsed, so its time is when it was retrieved (``age_from: file_time``).

The screener refused two nights running around 00:35 Athens and answered at noon, so the pre-open check also asks
for the profiles on weekday afternoons (``refresh_profiles``, owner 2026-11-12): the night then has a copy of the
same day to fall back on. A refusal there changes nothing.
"""
import concurrent.futures
import hashlib
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import HTTPError

DEST = Path(__file__).resolve().parents[1] / 'data/research_universe_sources'
PROFILES = 'nasdaq_profiles.json'
URLS = {
    'nasdaqlisted.txt': 'https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt',
    'otherlisted.txt': 'https://www.nasdaqtrader.com/dynamic/SymDir/otherlisted.txt',
    PROFILES: 'https://api.nasdaq.com/api/screener/stocks?tableonly=true&limit=100000&download=true',
}
RETRY_PAUSES_SECONDS = (5, 15, 30)   # four attempts in all
STALE_MAX_HOURS = 72.0               # the profiles only: three days
STALE = 'stale_reused'


def download(url):
    req = Request(url, headers={'User-Agent': 'Mozilla/5.0', 'Accept': 'application/json,text/plain,*/*',
                                'Origin': 'https://www.nasdaq.com'})
    with urlopen(req, timeout=25) as response:
        return response.read()


def _age_hours(stamp, now):
    try:
        then = datetime.fromisoformat(str(stamp).replace('Z', '+00:00'))
    except ValueError:
        return None
    if then.tzinfo is None:
        then = then.replace(tzinfo=timezone.utc)
    return (now - then).total_seconds() / 3600


def reuse_last_good(name, previous, dest, now):
    """The last good profiles file when it is recent enough, else None. ``previous``: its row in the last manifest."""
    path = dest / name
    if name != PROFILES or not path.is_file():
        return None
    if isinstance(previous, dict) and previous.get('status') in ('ok', STALE):
        retrieved_at, age_from = previous.get('retrieved_at'), 'manifest'
    else:
        retrieved_at = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat()
        age_from = 'file_time'
    age = _age_hours(retrieved_at, now)
    if age is None or age < 0 or age > STALE_MAX_HOURS:
        return None
    raw = path.read_bytes()
    try:
        json.loads(raw)
    except ValueError:
        return None
    return {'status': STALE, 'retrieved_at': retrieved_at, 'age_hours': round(age, 2), 'age_from': age_from,
            'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()}


def fetch(item, *, dest=None, previous=None, get=download, sleep=time.sleep, now=None):
    name, url = item
    dest = dest or DEST
    result = {'name': name, 'url': url}
    failure = {}
    attempts = len(RETRY_PAUSES_SECONDS) + 1
    for attempt in range(attempts):
        if attempt:
            sleep(RETRY_PAUSES_SECONDS[attempt - 1])
        result['retrieved_at'] = (now or datetime.now(timezone.utc)).isoformat()
        try:
            raw = get(url)
            if name.endswith('.json'):
                json.loads(raw)
            elif b'Security Name|' not in raw:
                raise ValueError('Not a symbol directory')
            (dest / name).write_bytes(raw)
            result.update(status='ok', attempts=attempt + 1, bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest())
            return result
        except HTTPError as exc:
            failure = {'status': 'http_error', 'http_status': exc.code}
        except Exception as exc:
            failure = {'status': 'failed', 'error_type': type(exc).__name__}
    reused = reuse_last_good(name, previous, dest, now or datetime.now(timezone.utc))
    if reused:
        result.update(reused, attempts=attempts, attempted_at=result['retrieved_at'], last_failure=failure)
        return result
    result.update(failure, attempts=attempts)
    return result


def row_usable(row):
    """One source is there: ``ok``, or the profiles reused from the last good file."""
    return isinstance(row, dict) and (row.get('status') == 'ok'
                                      or (row.get('status') == STALE and row.get('name') == PROFILES))


def usable(rows):
    """All three sources there (``row_usable`` each). Every reader of the manifest judges a row with these two, so
    a reused file is not accepted in one place and refused in the next (2026-11-12: the universe build refused it)."""
    return isinstance(rows, list) and len(rows) == 3 and all(row_usable(r) for r in rows)


def stale_sources(rows):
    return [{'name': r.get('name'), 'retrieved_at': r.get('retrieved_at'), 'age_hours': r.get('age_hours')}
            for r in rows or [] if isinstance(r, dict) and r.get('status') == STALE]


def refresh_profiles(*, dest=None, get=download, sleep=time.sleep, now=None):
    """The profiles alone, outside the nightly run. A good answer replaces the file and its manifest row; anything
    else leaves the file and the manifest exactly as they were."""
    dest = dest or DEST
    dest.mkdir(parents=True, exist_ok=True)
    fresh = fetch((PROFILES, URLS[PROFILES]), dest=dest, previous=None, get=get, sleep=sleep, now=now)
    if fresh.get('status') != 'ok':
        failure = fresh.get('last_failure') or {k: fresh[k] for k in ('status', 'http_status', 'error_type') if k in fresh}
        return {'refreshed': False, **failure, 'attempts': fresh.get('attempts')}
    try:
        rows = json.loads((dest / 'manifest.json').read_text(encoding='utf-8-sig'))
        rows = [r for r in rows if isinstance(r, dict) and r.get('name') != PROFILES] if isinstance(rows, list) else []
    except (OSError, ValueError):
        rows = []
    (dest / 'manifest.json').write_text(json.dumps(rows + [fresh], indent=2), encoding='utf-8')
    return {'refreshed': True, 'attempts': fresh['attempts'], 'bytes': fresh['bytes'], 'retrieved_at': fresh['retrieved_at']}


def main():
    DEST.mkdir(parents=True, exist_ok=True)
    try:
        last = json.loads((DEST / 'manifest.json').read_text(encoding='utf-8-sig'))
        previous = {r.get('name'): r for r in last if isinstance(r, dict)} if isinstance(last, list) else {}
    except (OSError, ValueError):
        previous = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
        results = list(pool.map(lambda item: fetch(item, previous=previous.get(item[0])), URLS.items()))
    (DEST / 'manifest.json').write_text(json.dumps(results, indent=2), encoding='utf-8')
    print(json.dumps(results, indent=2))


if __name__ == '__main__':
    import sys

    if '--profiles-only' in sys.argv[1:]:
        print(json.dumps(refresh_profiles(), indent=2))
    else:
        main()
