"""Диагностический тест AliExpress RU: search + single item (v2)."""
import sys, os, json, time, logging
from pathlib import Path

logging.basicConfig(level=logging.INFO, format='%(name)s: %(message)s')
sys.path.insert(0, '.')

OUT = Path('data/collector_output/test_ali_ru_diag')
OUT.mkdir(parents=True, exist_ok=True)

from r52.collectors.aliexpress import AliExpressRUCollector

c = AliExpressRUCollector(data_dir=Path('data'), use_proxies=False)

# === TEST 1: Search mode (wholesale HTML, no browser needed) ===
print('\n=== TEST 1: SEARCH MODE "наушники bluetooth" ===')
t0 = time.monotonic()
results = c.collect_by_search('наушники bluetooth')
elapsed = time.monotonic() - t0
print(f'Got {len(results)} results in {elapsed:.1f}s')
for i, r in enumerate(results[:10]):
    obs = r.get('observation', {})
    title = obs.get('fields', {}).get('title', {}).get('value', 'N/A')
    pc = obs.get('price_candidates', [])
    price = pc[0].get('amount', 'N/A') if pc else 'N/A'
    cur = pc[0].get('currency', 'N/A') if pc else 'N/A'
    imgs = len(obs.get('media', {}).get('items', []))
    status = obs.get('status', 'N/A')
    pid = obs.get('id', '')
    ev = obs.get('evidence_level', '')
    print(f'  [{i+1}] {status} | {title[:60]} | {price} {cur} | {imgs} imgs | id={pid} | {ev}')

written = c.write_observations(results, OUT)
print(f'Files written: {len(written)}')
print(f'Stats after search: {json.dumps(c.stats, ensure_ascii=False)}')

# === TEST 2: Single item by ID (curl_cffi first, then PW) ===
print('\n=== TEST 2: SINGLE ITEM 1005006791583450 ===')
t0 = time.monotonic()
lid = '1005006791583450'
url = c.build_url(lid)
print(f'URL: {url}')
try:
    obs = c.collect_product(lid, url)
    if obs:
        o = obs.get('observation', {})
        title = o.get('fields', {}).get('title', {}).get('value', 'N/A')
        pc = o.get('price_candidates', [])
        price = pc[0].get('amount', 'N/A') if pc else 'N/A'
        cur = pc[0].get('currency', 'N/A') if pc else 'N/A'
        imgs = len(o.get('media', {}).get('items', []))
        print(f'  Result: {o.get("status")} | {title[:80]} | {price} {cur} | {imgs} imgs')
        c.write_observations([obs], OUT)
    else:
        print('  Result: None (parse returned nothing)')
except Exception as e:
    import traceback; traceback.print_exc()
    print(f'  Error: {e}')

elapsed = time.monotonic() - t0
print(f'Elapsed: {elapsed:.1f}s')

print(f'\nFinal stats: {json.dumps(c.stats, ensure_ascii=False)}')
c.close()
print('\nDone.')
