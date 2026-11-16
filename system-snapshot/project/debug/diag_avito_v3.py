"""
AVITO v3: ретраи до реального search HTML (>30KB), дамп структуры цен.
WB: альтернативные подходы к 403.
"""
import sys, os, re, json, time, logging, random
from pathlib import Path
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("R52_HOME", os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "test_home"))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
log = logging.getLogger("diag.v3")

from r52.collectors.avito import AvitoCollector
from r52.collectors.wb import WBCollector
from r52.collectors.discovery import SearchError

DEBUG_DIR = Path(os.path.dirname(os.path.abspath(__file__)))
HOME = Path(os.environ["R52_HOME"])
QUERY = "тест"

# ============================================================
# PHASE A: AVITO — retry until real page
# ============================================================
log.info("=== PHASE A: AVITO search (retry up to 5 attempts) ===")
col = AvitoCollector(data_dir=HOME)
col._warmup()

real_html = None
real_status = None
real_url = None

for attempt in range(1, 6):
    log.info("--- Attempt %d ---", attempt)
    time.sleep(random.uniform(3, 6))
    
    # Try m.avito.ru first
    m_url = f"https://m.avito.ru/rossiya?q={QUERY}&p=1"
    headers = {
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        "Referer": "https://www.avito.ru/",
    }
    
    try:
        if col._cffi:
            resp = col._cffi.get(m_url, impersonate="chrome", timeout=(15, 30), headers=headers)
        else:
            resp = col.session.get(m_url, headers=headers, timeout=(10, 30))
        st = resp.status_code
        html = resp.text if hasattr(resp, 'text') else resp.content.decode('utf-8', 'replace')
        log.info("  m.avito.ru: HTTP %d, %d bytes", st, len(html))
        
        if st == 200 and len(html) > 30000:
            real_html = html
            real_status = st
            real_url = m_url
            break
        elif st == 439:
            # Try www fallback
            time.sleep(2)
            w_url = f"https://www.avito.ru/rossiya?q={QUERY}&p=1"
            if col._cffi:
                w_resp = col._cffi.get(w_url, impersonate="chrome", timeout=(15, 30), headers=headers)
            else:
                w_resp = col.session.get(w_url, headers=headers, timeout=(10, 30))
            w_st = w_resp.status_code
            w_html = w_resp.text if hasattr(w_resp, 'text') else w_resp.content.decode('utf-8', 'replace')
            log.info("  www.avito.ru: HTTP %d, %d bytes", w_st, len(w_html))
            
            if w_st == 200 and len(w_html) > 30000:
                real_html = w_html
                real_status = w_st
                real_url = w_url
                break
            elif w_st == 439 and len(w_html) > 30000:
                # Sometimes 439 still has content
                real_html = w_html
                real_status = w_st
                real_url = w_url
                break
    except Exception as e:
        log.warning("  Error: %s", e)
    
    if attempt < 5:
        wait = random.uniform(8, 15)
        log.info("  Waiting %.0fs before next attempt...", wait)
        time.sleep(wait)

if real_html:
    log.info("GOT REAL PAGE: %d bytes from %s (HTTP %d)", len(real_html), real_url, real_status)
    # Save
    save_path = DEBUG_DIR / "avito_real_search.html"
    save_path.write_text(real_html, encoding="utf-8")
    log.info("Saved: %s", save_path)
    
    # Find ad links
    card_pat = re.compile(
        r'<a[^>]*href=["\'](?:https?://(?:www\.|m\.)?avito\.ru/)?[^"]*?(\d{6,})[^"]*["\'][^>]*>',
        re.I
    )
    matches = list(card_pat.finditer(real_html))
    log.info("Ad links found: %d", len(matches))
    
    # For first 5 ads, dump 3000-char blocks and test ALL price patterns
    PRICE_PATTERNS = [
        ("text_rub", re.compile(r'(\d{2,9}(?:[\s\u00a0]\d{3})*)(?:\s*[.,]\d{1,2})?\s*(?:\u20bd|\ub040\u0443\u0431)', re.I)),
        ("data-price", re.compile(r'data-price=["\']?(\d{3,9})')),
        ("data-ad-price", re.compile(r'data-ad-price=["\']?(\d{3,9})')),
        ("class-price", re.compile(r'class=["\'][^"\']*price[^"\']*["\'][^>]*>\s*([\d\s\u00a0,.]+)', re.I)),
        ("span-num", re.compile(r'<span[^>]*class=["\'][^"\']*(?:num|amount|value)[^"\']*["\'][^>]*>(\d[\d\s\u00a0]*)</span>', re.I)),
        ("jsonld", re.compile(r'"price"\s*:\s*"?([\d.]+)"?')),
        ("state-json", re.compile(r'"price"\s*:\s*(\d{3,9})\b')),
        ("any-big-num", re.compile(r'>\s*(\d{4,9})\s*<')),
        ("data-value", re.compile(r'data-value=["\']?(\d{3,9})')),
    ]
    
    for i, m in enumerate(matches[:5]):
        aid = m.group(1)
        b_start = max(0, m.start() - 500)
        b_end = min(len(real_html), m.end() + 3000)
        block = real_html[b_start:b_end]
        
        log.info("--- Ad #%d: id=%s ---", i+1, aid)
        found = {}
        for name, pat in PRICE_PATTERNS:
            pm = pat.search(block)
            if pm:
                found[name] = pm.group(1).strip()[:50]
        
        if found:
            log.info("  PRICES: %s", json.dumps(found, ensure_ascii=False))
        else:
            log.info("  NO PRICE — dumping block snippet:")
            # Find the most "interesting" part: look for numbers
            nums = re.findall(r'\d{3,}', block)
            log.info("  Numbers in block: %s", nums[:20])
            log.info("  Block[500:1500]: %s", block[500:1500].replace('\n', ' ')[:500])
        
        # Also check: is there a <div> or <p> with class containing "price" nearby?
        price_divs = re.findall(r'<(?:div|p|span|b|strong)[^>]*class=["\'][^"\']*price[^"\']*["\'][^>]*>([^<]{1,100})', block, re.I)
        if price_divs:
            log.info("  price-divs: %s", price_divs[:5])

    # Global scan: all occurrences of "руб" or "₽" with surrounding context
    log.info("=== Global 'руб/₽' context scan ===")
    rub_pat = re.compile(r'(.{0,80}(?:\u20bd|\ub040\u0443\u0431.{0,3}).{0,30})', re.I)
    rub_matches = list(rub_pat.finditer(real_html))
    log.info("Total руб/₽ occurrences: %d", len(rub_matches))
    for ri, rm in enumerate(rub_matches[:10]):
        ctx = rm.group(1).replace('\n', ' ').strip()
        log.info("  #%d: ...%s...", ri+1, ctx[:120])
    
    # Global: all data-price / data-ad-price
    for attr in ['data-price', 'data-ad-price', 'data-value']:
        attr_pat = re.compile(rf'{attr}=["\']?(\d+)["\']?')
        am = list(attr_pat.finditer(real_html))
        if am:
            log.info("  %s: %d hits: %s", attr, len(am), [m.group(1) for m in am[:5]])

else:
    log.warning("No real page after 5 attempts. Saving last attempt for analysis.")
    if real_html is None:
        # Last resort: use the 439 page to at least understand structure
        log.warning("All attempts returned challenge pages. Will need proxy or different approach.")

# ============================================================
# PHASE B: WB — alternative approaches
# ============================================================
log.info("=== PHASE B: WB alternative approaches ===")
wb = WBCollector(data_dir=HOME)
wb._warmup()

# Approach 1: Different headers (mobile app)
log.info("--- WB Approach 1: Mobile app headers ---")
mobile_headers = {
    "Accept": "application/json",
    "Accept-Language": "ru-RU,ru;q=0.9",
    "User-Agent": "Wildberries/8.21.0 (iPhone; iOS 17.2; Scale/3.00)",
    "X-WB-App": "1",
    "X-WB-Device": "iPhone14,2",
}
wb_mobile_url = f"https://catalog.wb.ru/exactmatch/ru/common/v5/search?query={QUERY}&resultset=catalog&dest=-1257786&curr=rub&spp=30&appType=1&lang=ru&sort=popular&page=1"
try:
    r = wb._http_get(wb_mobile_url, headers=mobile_headers, timeout=8)
    log.info("  v5 mobile: HTTP %s, %d bytes", getattr(r, 'status_code', '?'), len(getattr(r, 'content', b'')))
except Exception as e:
    log.warning("  v5 mobile error: %s", e)

# Approach 2: v5 endpoint (newer than v4)
log.info("--- WB Approach 2: v5 endpoint ---")
wb_v5_url = f"https://search.wb.ru/exactmatch/ru/common/v5/search?query={QUERY}&resultset=catalog&dest=-1257786&curr=rub&spp=30&appType=1&lang=ru&sort=popular&page=1"
try:
    r = wb._http_get(wb_v5_url, headers=wb.JSON_HEADERS, timeout=8)
    log.info("  v5 search: HTTP %s, %d bytes", getattr(r, 'status_code', '?'), len(getattr(r, 'content', b'')))
    if getattr(r, 'status_code', 0) == 200:
        log.info("  v5 SUCCESS! data keys: %s", list(r.json().keys())[:10])
except Exception as e:
    log.warning("  v5 search error: %s", e)

# Approach 3: card.wb.ru batch with different format
log.info("--- WB Approach 3: card.wb.ru batch ---")
# Try the batch endpoint with proper query format
card_urls = [
    f"https://card.wb.ru/cards/v2/query?query={QUERY}&resultset=catalog&dest=-1257786&curr=rub&spp=30&appType=1&lang=ru&sort=popular&page=1",
    f"https://card.wb.ru/cards/v1/query?query={QUERY}&resultset=catalog&dest=-1257786&curr=rub&spp=30&appType=1&lang=ru&sort=popular&page=1",
]
for cu in card_urls:
    try:
        r = wb._http_get(cu, headers=wb.JSON_HEADERS, timeout=8)
        st = getattr(r, 'status_code', '?')
        sz = len(getattr(r, 'content', b''))
        log.info("  %s: HTTP %s, %d bytes", cu.split('?')[0], st, sz)
        if st == 200 and sz > 100:
            data = r.json()
            prods = data.get('data', {}).get('products', data.get('products', []))
            log.info("  → %d products", len(prods))
            if prods:
                log.info("  First: %s", json.dumps(prods[0], ensure_ascii=False)[:200])
            break
    except Exception as e:
        log.warning("  card batch error: %s", e)

# Approach 4: HTML with different UA
log.info("--- WB Approach 4: HTML with Safari UA ---")
safari_headers = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Safari/605.1.15",
}
wb_html_url = f"https://www.wildberries.ru/catalog/0/ru/?search={QUERY}&page=1"
try:
    r = wb._http_get(wb_html_url, headers=safari_headers, timeout=12)
    st = getattr(r, 'status_code', '?')
    html = r.text if hasattr(r, 'text') else r.content.decode('utf-8', 'replace')
    log.info("  HTML Safari: HTTP %s, %d bytes", st, len(html))
    if st == 200 and len(html) > 10000:
        # Find product cards
        prod_pat = re.compile(r'/catalog/(\d+)/detail\.aspx')
        pms = list(prod_pat.finditer(html))
        log.info("  Product links: %d", len(pms))
        # Find prices
        price_pat = re.compile(r'(\d{3,9}(?:[\s\u00a0]\d{3})*)\s*(?:\u20bd|\ub040\u0443\u0431)')
        pms2 = list(price_pat.finditer(html))
        log.info("  Price matches: %d", len(pms2))
        if pms2:
            log.info("  First prices: %s", [m.group(1) for m in pms2[:5]])
        # Save
        wb_html_path = DEBUG_DIR / "wb_html_safari.html"
        wb_html_path.write_text(html, encoding="utf-8")
        log.info("  Saved: %s", wb_html_path)
except Exception as e:
    log.warning("  HTML Safari error: %s", e)

# Approach 5: Direct card.wb.ru for known product IDs (if we have any from HTML)
log.info("--- WB Approach 5: Known product card ---")
# Try a well-known WB product ID
test_ids = [123456789, 987654321, 111111111]
for tid in test_ids:
    card_url = f"https://card.wb.ru/cards/v2/detail?nm={tid}"
    try:
        r = wb._http_get(card_url, headers=wb.JSON_HEADERS, timeout=6)
        st = getattr(r, 'status_code', '?')
        log.info("  card %d: HTTP %s", tid, st)
        if st == 200:
            data = r.json()
            prods = data.get('data', {}).get('products', data.get('products', []))
            if prods:
                p = prods[0]
                log.info("    name=%s price=%s", p.get('name', '?')[:40], p.get('salePriceU', p.get('priceU', '?')))
            break
    except Exception as e:
        log.warning("  card %d error: %s", tid, e)

# ============================================================
# SUMMARY
# ============================================================
log.info("=== SUMMARY ===")
log.info("AVITO: %s", "OK" if real_html else "CHALLENGE (all attempts)")
if real_html:
    log.info("  bytes=%d, ads=%d", len(real_html), len(matches))
log.info("WB: see approaches above")

report = {
    "ts": datetime.now(timezone(timedelta(hours=10))).isoformat(),
    "avito": {
        "got_real_page": bool(real_html),
        "bytes": len(real_html) if real_html else 0,
        "url": real_url,
        "status": real_status,
        "ad_links": len(matches) if real_html else 0,
    },
    "wb": "see log",
}
rpath = DEBUG_DIR / "diag_v3_report.json"
rpath.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
log.info("Report: %s", rpath)
log.info("=== DONE ===")
