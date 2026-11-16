"""
diag_farpost_prices.py — Диагностика извлечения цен из поисковой выдачи Farpost.
1. Загружает страницу поиска
2. Сохраняет HTML
3. Показывает HTML-окна вокруг bulletin-ссылок
4. Тестирует все price-паттерны
"""
import sys, os, re, json, time, logging
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s: %(message)s")
log = logging.getLogger("diag.farpost")

from r52.collectors.farpost import FarpostCollector

QUERY = "тест"
PAGE = 1
OUT_HTML = os.path.join(os.path.dirname(__file__), "farpost_search_page.html")

print("=" * 60)
print("FARPOST PRICE EXTRACTION DIAGNOSTIC")
print(f"Query: {QUERY!r}, page={PAGE}")
print("=" * 60)

collector = FarpostCollector(data_dir=None, use_proxies=False)

# Fetch search page
url = f"https://www.farpost.ru/dir?query={QUERY}&page={PAGE}"
print(f"\n[1] Fetching: {url}")
collector._warmup()
time.sleep(2)

headers = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Referer": "https://www.farpost.ru/",
}

if collector._cffi:
    resp = collector._cffi.get(url, impersonate="chrome", timeout=(15, 30), headers=headers)
else:
    resp = collector.session.get(url, headers=headers, timeout=(10, 30))

print(f"    Status: {resp.status_code}, Size: {len(resp.content)} bytes")

# Decode
ct = (resp.headers.get("Content-Type") or "").lower()
if "1251" in ct or "cp1251" in ct:
    html = resp.content.decode("windows-1251", errors="replace")
else:
    html = resp.text

# Save
with open(OUT_HTML, "w", encoding="utf-8") as f:
    f.write(html)
print(f"    Saved to: {OUT_HTML}")

# Find bulletin links
card_pat = re.compile(
    r'<a[^>]*href=["\'](?:https?://www\.farpost\.ru)?/[^"\']*/([a-z0-9\-]*?(\d{5,}))[a-z0-9\-]*\.html["\'][^>]*>',
    re.I
)
matches = list(card_pat.finditer(html))
print(f"\n[2] Bulletin links found: {len(matches)}")

if not matches:
    print("    NO LINKS FOUND — checking page structure...")
    # Show first 2000 chars
    print(f"    HTML head: {html[:2000]}")
    print("    EXIT: NO_LINKS")
    sys.exit(1)

# Show context around first 5 links
print(f"\n[3] HTML context around first {min(5, len(matches))} links:")
for i, m in enumerate(matches[:5]):
    fid = m.group(2)
    # Show 300 chars before and 500 chars after
    ctx_start = max(0, m.start() - 300)
    ctx_end = min(len(html), m.end() + 500)
    ctx = html[ctx_start:ctx_end]
    # Highlight the link
    link_rel_start = m.start() - ctx_start
    link_rel_end = m.end() - ctx_start
    print(f"\n  --- Link {i+1} (ID={fid}) ---")
    print(f"  Before: ...{ctx[max(0,link_rel_start-150):link_rel_start]}")
    print(f"  Link:   {ctx[link_rel_start:link_rel_end]}")
    print(f"  After:  {ctx[link_rel_end:link_rel_end+300]}...")

# Test price patterns on each card block
print(f"\n[4] Price pattern testing:")

price_patterns = {
    "data-price": re.compile(r'data-price=["\']([\d][\d\s\u00a0]*)'),
    "data-role=price": re.compile(r'data-role=["\']price["\']>([\d][\d\s\u00a0]*)'),
    "data-bulletin-price": re.compile(r'data-bulletin-price=["\'](\d+)["\']'),
    "text-rub": re.compile(r'(\d{2,9}(?:[\s\u00a0]\d{3})*)\s*(?:₽|руб|&#8381;)'),
    "text-ruble": re.compile(r'(\d{2,9}(?:[\s\u00a0]\d{3})*)\s*(?:\u20bd|руб\.?)'),
    "viewbull-price": re.compile(r'class="[^"]*price[^"]*"[^>]*>([\d\s\u00a0]+)'),
    "data-field-price": re.compile(r'data-field=["\']price["\'][^>]*>([\d\s\u00a0]+)'),
}

results = []
for i, m in enumerate(matches[:20]):
    fid = m.group(2)
    block_start = max(0, m.start() - 2500)
    block_end = matches[i + 1].start() if i + 1 < len(matches) else min(m.end() + 2000, len(html))
    block = html[block_start:block_end]
    
    found = {}
    for name, pat in price_patterns.items():
        pm = pat.search(block)
        if pm:
            found[name] = pm.group(1).strip()[:40]
    
    results.append({"id": fid, "patterns": found, "block_size": len(block)})
    
    status = "✓" if found else "✗"
    print(f"  [{i+1:2d}] ID={fid:>12} {status} {found if found else 'NO PRICE MATCHED'}")

# Summary
print(f"\n[5] SUMMARY:")
total = len(results)
with_price = sum(1 for r in results if r["patterns"])
print(f"    Cards: {total}, With price: {with_price}/{total}")

# Which patterns work?
pattern_hits = {}
for r in results:
    for p in r["patterns"]:
        pattern_hits[p] = pattern_hits.get(p, 0) + 1
print(f"    Pattern hit counts: {json.dumps(pattern_hits, ensure_ascii=False)}")

# Show a full block for a card WITH price (or first card if none)
sample_idx = next((i for i, r in enumerate(results) if r["patterns"]), 0)
sm = matches[sample_idx]
s_start = max(0, sm.start() - 2000)
s_end = matches[sample_idx + 1].start() if sample_idx + 1 < len(matches) else min(sm.end() + 2000, len(html))
s_block = html[s_start:s_end]
print(f"\n[6] Sample block (card {sample_idx+1}, {len(s_block)} chars):")
print(f"    {s_block[:2000]}")
print(f"    ...")
print(f"    {s_block[-1000:]}")

# Also check: are prices maybe in a DIFFERENT structure?
# Look for common Farpost price indicators in the whole page
print(f"\n[7] Global price indicators in page:")
for label, pat in [
    ("'price' in class", re.compile(r'class="[^"]*price[^"]*"', re.I)),
    ("data-price attr", re.compile(r'data-price=["\'][^"\']{2,20}', re.I)),
    ("₽ symbol", re.compile(r'[\d\s]{3,15}₽')),
    ("руб text", re.compile(r'[\d\s]{3,15}руб')),
    ("viewbull", re.compile(r'viewbull[^"]*', re.I)),
    ("bulletin-cell", re.compile(r'bulletin[^"]*cell[^"]*', re.I)),
    ("card-price", re.compile(r'card[^"]*price[^"]*', re.I)),
]:
    hits = pat.findall(html)
    print(f"    {label}: {len(hits)} hits" + (f" (first: {hits[0][:60]})" if hits else ""))

print(f"\n=== DONE ===")
