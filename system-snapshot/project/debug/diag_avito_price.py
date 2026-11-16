"""
Диагностика AVITO: извлечение цен из mobile search HTML.
Цель: понять, в каком формате цена лежит в карточках m.avito.ru
и подобрать рабочий regex.
"""
import sys, os, re, json, time, logging
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("R52_HOME", os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "test_home"))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
log = logging.getLogger("diag.avito")

from pathlib import Path
from r52.collectors.avito import AvitoCollector

col = AvitoCollector(data_dir=Path(os.environ["R52_HOME"]))

QUERY = "тест"
PAGE = 1

# --- Step 1: Fetch mobile search page ---
log.info("=== AVITO mobile search fetch ===")
col._warmup()
time.sleep(2)

url = f"https://m.avito.ru/rossiya?q={QUERY}&p={PAGE}"
headers = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Referer": "https://www.avito.ru/",
}

t0 = time.monotonic()
if col._cffi:
    resp = col._cffi.get(url, impersonate="chrome", timeout=(15, 30), headers=headers)
else:
    resp = col.session.get(url, headers=headers, timeout=(10, 30))
elapsed = time.monotonic() - t0

status = resp.status_code
html = resp.text if hasattr(resp, 'text') else resp.content.decode('utf-8', 'replace')
log.info("m.avito.ru: HTTP %d, %d bytes, %.1fs", status, len(html), elapsed)

if status not in (200, 439):
    log.error("Unexpected status %d, aborting", status)
    sys.exit(1)

# If 439, try www fallback
if status == 439:
    log.info("439 → trying www.avito.ru")
    w_url = f"https://www.avito.ru/rossiya?q={QUERY}&p={PAGE}"
    if col._cffi:
        w_resp = col._cffi.get(w_url, impersonate="chrome", timeout=(10, 20), headers=headers)
    else:
        w_resp = col.session.get(w_url, headers=headers, timeout=(8, 20))
    status = w_resp.status_code
    html = w_resp.text if hasattr(w_resp, 'text') else w_resp.content.decode('utf-8', 'replace')
    log.info("www.avito.ru: HTTP %d, %d bytes", status, len(html))

# Save raw HTML for inspection
raw_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "avito_mobile_raw.html")
with open(raw_path, "w", encoding="utf-8") as f:
    f.write(html)
log.info("Saved mobile HTML: %s (%d bytes)", raw_path, len(html))

# --- Step 2: Find ad links and dump surrounding blocks ---
log.info("=== Finding ad links ===")
card_pat = re.compile(
    r'<a[^>]*href=["\'](?:https?://(?:www\.|m\.)?avito\.ru/)?[^"]*?(\d{6,})[^"]*["\'][^>]*>',
    re.I
)
matches = list(card_pat.finditer(html))
log.info("Found %d ad links", len(matches))

# --- Step 3: For each ad, extract a wider block and test price patterns ---
PRICE_PATTERNS = [
    ("text_rub", re.compile(r'(\d{2,9}(?:[\s\u00a0]\d{3})*)(?:\s*[.,]\d{1,2})?\s*(?:₽|руб\.?|RUB)', re.I)),
    ("text_nbsp", re.compile(r'(\d{2,9}(?:[\s\u00a0]\d{3})*)\s*₽', re.I)),
    ("data-price", re.compile(r'data-price=["\']?(\d{3,9})')),
    ("data-ad-price", re.compile(r'data-ad-price=["\']?(\d{3,9})')),
    ("class-price", re.compile(r'class=["\'][^"\']*price[^"\']*["\'][^>]*>\s*(\d{2,9}(?:[\s\u00a0]\d{3})*)', re.I)),
    ("span-price", re.compile(r'<span[^>]*class=["\'][^"\']*price[^"\']*["\'][^>]*>(\d[\d\s\u00a0]*)</span>', re.I)),
    ("jsonld", re.compile(r'"price"\s*:\s*"?([\d.]+)"?')),
    ("meta-og", re.compile(r'og:price:amount["\'][^>]*content=["\']([\d.]+)', re.I)),
    ("any-number-rub", re.compile(r'(\d{3,9})\s*(?:₽|руб)', re.I)),
    ("any-number-space-rub", re.compile(r'(\d{3,9}(?:\s\d{3})*)\s*(?:₽|руб)', re.I)),
]

results = []
for i, m in enumerate(matches[:10]):
    aid = m.group(1)
    # Wider block: 6000 chars before and after the link
    b_start = max(0, m.start() - 2000)
    b_end = min(len(html), m.end() + 6000)
    block = html[b_start:b_end]
    
    log.info("--- Ad #%d: id=%s, block=%d chars ---", i+1, aid, len(block))
    
    found_prices = {}
    for name, pat in PRICE_PATTERNS:
        pm = pat.search(block)
        if pm:
            found_prices[name] = pm.group(1).strip()
    
    # Also look for price in the immediate 1000 chars after the link
    near_block = html[m.end():m.end()+1000]
    for name, pat in PRICE_PATTERNS:
        pm = pat.search(near_block)
        if pm and name not in found_prices:
            found_prices[name] = pm.group(1).strip() + " [near]"
    
    if found_prices:
        log.info("  PRICES: %s", json.dumps(found_prices, ensure_ascii=False))
    else:
        log.info("  NO PRICE FOUND in block")
        # Dump first 500 chars of block for manual inspection
        log.info("  BLOCK HEAD: %.300s", block[:500])
    
    results.append({"id": aid, "prices": found_prices, "block_len": len(block)})

# --- Step 4: Global price scan on full HTML ---
log.info("=== Global price patterns on full HTML ===")
for name, pat in PRICE_PATTERNS:
    all_matches = list(pat.finditer(html))
    if all_matches:
        samples = [m.group(1).strip()[:30] for m in all_matches[:5]]
        log.info("  %s: %d matches, samples=%s", name, len(all_matches), samples)
    else:
        log.info("  %s: 0 matches", name)

# --- Step 5: Look for structured data (JSON in script tags) ---
log.info("=== Script JSON scan ===")
script_pat = re.compile(r'<script[^>]*>([\s\S]{100,}?)</script>', re.I)
scripts = list(script_pat.finditer(html))
log.info("Found %d script tags", len(scripts))
for si, sm in enumerate(scripts[:20]):
    scontent = sm.group(1)
    if 'price' in scontent.lower() and len(scontent) > 200:
        # Extract a snippet around 'price'
        idx = scontent.lower().find('price')
        snippet = scontent[max(0, idx-50):idx+100]
        log.info("  Script #%d (%d chars): ...%s...", si, len(scontent), snippet[:150])

# --- Summary ---
report = {
    "ts": datetime.now(timezone(timedelta(hours=10))).isoformat(),
    "url": url,
    "http_status": status,
    "html_bytes": len(html),
    "ad_links": len(matches),
    "ads": results,
    "elapsed_sec": round(time.monotonic() - t0, 1),
}

rpath = os.path.join(os.path.dirname(os.path.abspath(__file__)), "diag_avito_price_report.json")
with open(rpath, "w", encoding="utf-8") as f:
    json.dump(report, f, ensure_ascii=False, indent=2)
log.info("Report: %s", rpath)
log.info("=== DONE ===")
