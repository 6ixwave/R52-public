"""
v4: AVITO __preloadedState__ parsing + WB with WB_JSON_HEADERS.
"""
import sys, os, re, json, time, logging, random
from pathlib import Path
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("R52_HOME", os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "test_home"))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
log = logging.getLogger("diag.v4")

DEBUG_DIR = Path(os.path.dirname(os.path.abspath(__file__)))
HOME = Path(os.environ["R52_HOME"])
QUERY = "тест"

# ============================================================
# PHASE A: AVITO — parse __preloadedState__ from saved HTML
# ============================================================
log.info("=== PHASE A: AVITO __preloadedState__ parsing ===")

html_path = DEBUG_DIR / "avito_real_search.html"
if not html_path.exists():
    log.error("avito_real_search.html not found!")
    sys.exit(1)

html = html_path.read_text(encoding="utf-8")
log.info("Loaded HTML: %d bytes, %d lines", len(html), html.count('\n'))

# Extract __preloadedState__ (it's a JSON string inside a JS string)
# Pattern: window.__preloadedState__ = "..."  (the outer quotes make it a JS string)
# The inner content is a JSON string with escaped quotes
state_pat = re.compile(
    r'window\.__preloadedState__\s*=\s*"((?:[^"\\]|\\.)*)"\s*;',
    re.DOTALL
)
sm = state_pat.search(html)
if not sm:
    # Try without semicolon
    state_pat2 = re.compile(
        r'window\.__preloadedState__\s*=\s*"((?:[^"\\]|\\.)*)"',
        re.DOTALL
    )
    sm = state_pat2.search(html)

if sm:
    raw_state = sm.group(1)
    log.info("Raw state string: %d chars", len(raw_state))
    
    # Unescape JS string → get actual JSON
    # JS string escaping: \" → ", \\ → \, \n → newline, etc.
    try:
        # Method 1: use json.loads on the whole thing (it's a JSON string literal)
        state_json_str = json.loads(f'"{raw_state}"')
        state = json.loads(state_json_str)
        log.info("State parsed OK! Top-level keys: %s", list(state.keys())[:20])
    except json.JSONDecodeError as e:
        log.warning("JSON parse method 1 failed: %s", e)
        # Method 2: manual unescape
        state_json_str = raw_state.replace('\\"', '"').replace('\\\\', '\\').replace('\\n', '\n').replace('\\t', '\t')
        try:
            state = json.loads(state_json_str)
            log.info("State parsed (method 2)! Keys: %s", list(state.keys())[:20])
        except json.JSONDecodeError as e2:
            log.error("State parse method 2 failed: %s", e2)
            state = None
else:
    log.error("__preloadedState__ not found in HTML!")
    state = None

# Save parsed state for inspection
if state:
    state_path = DEBUG_DIR / "avito_preloaded_state.json"
    state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    log.info("Saved state: %s (%d bytes)", state_path, state_path.stat().st_size)
    
    # Find ad/listing data in state
    # Common paths: state.search.results, state.listings, state.ads, state.items
    def find_ads(obj, path=""):
        """Recursively find lists of dicts that look like ad cards."""
        results = []
        if isinstance(obj, dict):
            for k, v in obj.items():
                new_path = f"{path}.{k}" if path else k
                if isinstance(v, list) and len(v) > 0 and isinstance(v[0], dict):
                    # Check if items have ad-like fields
                    first = v[0]
                    ad_keys = {'id', 'title', 'price', 'adId', 'listingId', 'name', 'amount'}
                    overlap = ad_keys & set(first.keys())
                    if len(overlap) >= 2:
                        results.append((new_path, v, overlap))
                elif isinstance(v, dict):
                    results.extend(find_ads(v, new_path))
                elif isinstance(v, list):
                    results.extend(find_ads(v, new_path))
        return results
    
    ad_paths = find_ads(state)
    log.info("Found %d potential ad-list paths:", len(ad_paths))
    for path, items, keys in ad_paths[:10]:
        log.info("  %s: %d items, keys=%s", path, len(items), list(keys))
        # Dump first item
        if items:
            first = items[0]
            log.info("    First item: %s", json.dumps(first, ensure_ascii=False)[:300])
    
    # Also specifically look for "price" anywhere
    def find_prices(obj, path=""):
        results = []
        if isinstance(obj, dict):
            for k, v in obj.items():
                new_path = f"{path}.{k}" if path else k
                if k.lower() in ('price', 'priceinfo', 'prices', 'amount', 'pricevalue'):
                    results.append((new_path, v))
                if isinstance(v, dict):
                    results.extend(find_prices(v, new_path))
                elif isinstance(v, list):
                    for i, item in enumerate(v[:3]):
                        if isinstance(item, dict):
                            results.extend(find_prices(item, f"{new_path}[{i}]"))
        return results
    
    price_paths = find_prices(state)
    log.info("Price fields found: %d", len(price_paths))
    for pp, pv in price_paths[:20]:
        pv_str = json.dumps(pv, ensure_ascii=False) if isinstance(pv, (dict, list)) else str(pv)
        log.info("  %s = %s", pp, pv_str[:150])

# ============================================================
# PHASE B: WB — use WB_JSON_HEADERS properly
# ============================================================
log.info("=== PHASE B: WB with WB_JSON_HEADERS ===")

from r52.collectors.wb import WBCollector, JSON_HEADERS
from r52.collectors.discovery import SearchError

wb = WBCollector(data_dir=HOME)
wb._warmup()
log.info("WB warmup done. Using JSON_HEADERS: %s", json.dumps(JSON_HEADERS, ensure_ascii=False))

# B1: v4 search (catalog.wb.ru)
log.info("--- B1: catalog.wb.ru v4 search ---")
from urllib.parse import quote
b1_url = (f"https://catalog.wb.ru/exactmatch/ru/common/v4/search"
          f"?query={quote(QUERY)}&resultset=catalog&dest=-1257786"
          f"&curr=rub&spp=30&appType=1&lang=ru&sort=popular&page=1")
try:
    r = wb._http_get(b1_url, headers=JSON_HEADERS, timeout=10)
    st = getattr(r, 'status_code', '?')
    body = r.text if hasattr(r, 'text') else r.content.decode('utf-8', 'replace')
    log.info("  v4 catalog: HTTP %s, %d bytes", st, len(body))
    if st == 200:
        data = json.loads(body)
        prods = data.get('data', {}).get('products', data.get('products', []))
        log.info("  → %d products", len(prods))
        for p in prods[:3]:
            name = p.get('name', '?')[:40]
            price = p.get('salePriceU', p.get('priceU', p.get('sizes', [{}])[0].get('priceU', '?')))
            log.info("    %s → price=%s", name, price)
except Exception as e:
    log.warning("  v4 error: %s %s", type(e).__name__, e)

# B2: v4 search (search.wb.ru)
log.info("--- B2: search.wb.ru v4 search ---")
b2_url = (f"https://search.wb.ru/exactmatch/ru/common/v4/search"
          f"?query={quote(QUERY)}&resultset=catalog&dest=-1257786"
          f"&curr=rub&spp=30&appType=1&lang=ru&sort=popular&page=1")
try:
    r = wb._http_get(b2_url, headers=JSON_HEADERS, timeout=10)
    st = getattr(r, 'status_code', '?')
    body = r.text if hasattr(r, 'text') else r.content.decode('utf-8', 'replace')
    log.info("  v4 search: HTTP %s, %d bytes", st, len(body))
    if st == 200:
        data = json.loads(body)
        prods = data.get('data', {}).get('products', data.get('products', []))
        log.info("  → %d products", len(prods))
        for p in prods[:3]:
            name = p.get('name', '?')[:40]
            price = p.get('salePriceU', p.get('priceU', '?'))
            log.info("    %s → price=%s", name, price)
except Exception as e:
    log.warning("  v4 search error: %s %s", type(e).__name__, e)

# B3: v5 search
log.info("--- B3: search.wb.ru v5 search ---")
b3_url = (f"https://search.wb.ru/exactmatch/ru/common/v5/search"
          f"?query={quote(QUERY)}&resultset=catalog&dest=-1257786"
          f"&curr=rub&spp=30&appType=1&lang=ru&sort=popular&page=1")
try:
    r = wb._http_get(b3_url, headers=JSON_HEADERS, timeout=10)
    st = getattr(r, 'status_code', '?')
    body = r.text if hasattr(r, 'text') else r.content.decode('utf-8', 'replace')
    log.info("  v5 search: HTTP %s, %d bytes", st, len(body))
    if st == 200:
        data = json.loads(body)
        prods = data.get('data', {}).get('products', data.get('products', []))
        log.info("  → %d products", len(prods))
        for p in prods[:3]:
            name = p.get('name', '?')[:40]
            price = p.get('salePriceU', p.get('priceU', '?'))
            log.info("    %s → price=%s", name, price)
except Exception as e:
    log.warning("  v5 error: %s %s", type(e).__name__, e)

# B4: card.wb.ru batch
log.info("--- B4: card.wb.ru batch ---")
b4_url = (f"https://card.wb.ru/cards/v2/query"
          f"?query={quote(QUERY)}&resultset=catalog&dest=-1257786"
          f"&curr=rub&spp=30&appType=1&lang=ru&sort=popular&page=1")
try:
    r = wb._http_get(b4_url, headers=JSON_HEADERS, timeout=10)
    st = getattr(r, 'status_code', '?')
    body = r.text if hasattr(r, 'text') else r.content.decode('utf-8', 'replace')
    log.info("  card batch: HTTP %s, %d bytes", st, len(body))
    if st == 200:
        data = json.loads(body)
        prods = data.get('data', {}).get('products', data.get('products', []))
        log.info("  → %d products", len(prods))
        for p in prods[:3]:
            name = p.get('name', '?')[:40]
            price = p.get('salePriceU', p.get('priceU', '?'))
            log.info("    %s → price=%s", name, price)
except Exception as e:
    log.warning("  card batch error: %s %s", type(e).__name__, e)

# B5: WB HTML page (www.wildberries.ru)
log.info("--- B5: WB HTML ---")
WB_HTML_HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
}
b5_url = f"https://www.wildberries.ru/catalog/0/ru/?search={quote(QUERY)}&page=1"
try:
    r = wb._http_get(b5_url, headers=WB_HTML_HEADERS, timeout=15)
    st = getattr(r, 'status_code', '?')
    body = r.text if hasattr(r, 'text') else r.content.decode('utf-8', 'replace')
    log.info("  HTML: HTTP %s, %d bytes", st, len(body))
    if st == 200 and len(body) > 5000:
        # Look for __NEXT_DATA__ or similar
        next_data = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', body, re.DOTALL)
        if next_data:
            log.info("  __NEXT_DATA__ found: %d chars", len(next_data.group(1)))
            nd = json.loads(next_data.group(1))
            log.info("  NEXT_DATA keys: %s", list(nd.keys())[:10])
        # Price patterns
        price_pat = re.compile(r'(\d{3,9}(?:[\s\u00a0]\d{3})*)\s*(?:₽|руб)')
        pms = list(price_pat.finditer(body))
        log.info("  Price matches in HTML: %d", len(pms))
        if pms:
            log.info("  First: %s", [m.group(1) for m in pms[:5]])
        # Save
        wb_html_path = DEBUG_DIR / "wb_html_v4.html"
        wb_html_path.write_text(body, encoding="utf-8")
        log.info("  Saved: %s", wb_html_path)
except Exception as e:
    log.warning("  HTML error: %s %s", type(e).__name__, e)

# ============================================================
# SUMMARY
# ============================================================
log.info("=== SUMMARY ===")
log.info("AVITO state: %s", "PARSED" if state else "FAILED")
if state:
    log.info("  ad paths: %d, price fields: %d", len(ad_paths), len(price_paths))
log.info("WB: see B1-B5 above")

log.info("=== DONE ===")
