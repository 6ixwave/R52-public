"""
diag_avito_html.py — Deep analysis of avito_real_search.html for ad cards, prices, links.
Focus: find where actual search results live (not in __preloadedState__).
"""
import re, json, sys
from pathlib import Path

DEBUG_DIR = Path(__file__).parent
html_path = DEBUG_DIR / "avito_real_search.html"

if not html_path.exists():
    print("ERROR: avito_real_search.html not found")
    sys.exit(1)

html = html_path.read_text(encoding="utf-8")
print(f"HTML: {len(html)} bytes, {html.count(chr(10))} lines")

# 1. All script tags with content > 100 chars
scripts = re.findall(r'<script[^>]*>(.*?)</script>', html, re.DOTALL)
print(f"\n=== SCRIPT TAGS: {len(scripts)} total ===")
for i, s in enumerate(scripts):
    stripped = s.strip()
    if len(stripped) > 100:
        first_line = stripped.split('\n')[0][:200]
        print(f"  [{i}] len={len(stripped)}: {first_line}")

# 2. Price data-attributes
price_attrs = re.findall(
    r'data-[a-z-]*(?:price|amount|cost|value)[a-z-]*=["\']([^"\']*)["\']',
    html, re.I
)
print(f"\n=== PRICE DATA-ATTRS: {len(price_attrs)} ===")
for p in price_attrs[:20]:
    print(f"  {p[:120]}")

# 3. Ad-like hrefs (paths ending in /digits)
ad_hrefs = re.findall(r'href=["\'](/[^"\'<>]*/(\d{6,}))["\']', html)
print(f"\n=== AD-LIKE HREFS: {len(ad_hrefs)} ===")
seen = set()
for h, ad_id in ad_hrefs:
    if ad_id not in seen:
        seen.add(ad_id)
        print(f"  /{h[1:]}  (id={ad_id})")
    if len(seen) >= 15:
        break

# 4. Price in visible text (₽, руб, RUB)
price_text = re.findall(
    r'(\d[\d\s\u00a0]{2,12})\s*(?:₽|руб\b|RUB)',
    html
)
print(f"\n=== PRICE TEXT: {len(price_text)} matches ===")
for p in price_text[:20]:
    print(f"  {repr(p)}")

# 5. aria-label with numbers (often contains price)
aria_prices = re.findall(r'aria-label=["\']([^"\']*\d[^"\']*)["\']', html)
print(f"\n=== ARIA-LABELS with numbers: {len(aria_prices)} ===")
for a in aria_prices[:15]:
    print(f"  {a[:150]}")

# 6. Look for specific AVITO card structure
# AVITO search results often use: <a class="s-searchResult-item" ...>
# or data-listing-id, data-ad-id
for pattern_name, pattern in [
    ("data-listing-id", r'data-listing-id=["\'](\d+)["\']'),
    ("data-ad-id", r'data-ad-id=["\'](\d+)["\']'),
    ("data-id", r'data-id=["\'](\d+)["\']'),
    ("s-searchResult", r'class=["\'][^"\']*s-searchResult[^"\']*["\']'),
    ("catalog__item", r'class=["\'][^"\']*catalog__item[^"\']*["\']'),
    ("listing-card", r'class=["\'][^"\']*listing-card[^"\']*["\']'),
]:
    matches = re.findall(pattern, html, re.I)
    print(f"\n=== {pattern_name}: {len(matches)} ===")
    for m in matches[:8]:
        print(f"  {m[:100]}")

# 7. JSON-LD
jsonld = re.findall(r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', html, re.DOTALL)
print(f"\n=== JSON-LD: {len(jsonld)} ===")
for j in jsonld[:3]:
    try:
        data = json.loads(j.strip())
        print(f"  type={data.get('@type','?')}, keys={list(data.keys())[:10]}")
        if 'offers' in data:
            print(f"  offers: {json.dumps(data['offers'], ensure_ascii=False)[:300]}")
    except:
        print(f"  raw: {j.strip()[:200]}")

# 8. Check for __NEXT_DATA__ or other framework data
for marker in ['__NEXT_DATA__', '__APP_DATA__', '__INITIAL_STATE__', 'window.__data', 'window.__STATE', 'window.__APP']:
    m = re.search(marker, html)
    if m:
        snippet = html[m.start():m.start()+200]
        print(f"\n=== Found {marker} at pos {m.start()} ===")
        print(f"  {snippet[:180]}")

# 9. Check for data-props or data-state on main container
data_props = re.findall(r'data-(?:props|state|initial-data|serialized-state)=["\']([^"\']{50,})["\']', html)
print(f"\n=== DATA-PROPS/STATE: {len(data_props)} ===")
for dp in data_props[:5]:
    print(f"  len={len(dp)}: {dp[:200]}")

# 10. Check the large script (217872 chars was preloadedState, but check others)
# Find the biggest script
biggest = max(enumerate(scripts), key=lambda x: len(x[1].strip()), default=(0, ""))
print(f"\n=== BIGGEST SCRIPT: [{biggest[0]}] len={len(biggest[1].strip())} ===")
print(f"  First 300: {biggest[1].strip()[:300]}")

# 11. Look for fetch/API URLs in the HTML
api_urls = re.findall(r'(?:url|endpoint|api)\s*[:=]\s*["\']([^"\']*avito[^"\']*)["\']', html, re.I)
print(f"\n=== API URLS in HTML: {len(api_urls)} ===")
for u in api_urls[:10]:
    print(f"  {u[:150]}")

# 12. Check for "search" related data in the HTML (outside preloadedState)
# Remove the preloadedState block first
html_no_state = re.sub(r'window\.__preloadedState__\s*=\s*"[^"]*";?', '', html, flags=re.DOTALL)
search_snippets = re.findall(r'[^>]{0,50}search[^<]{0,80}', html_no_state, re.I)
print(f"\n=== 'search' in HTML (excl. state): {len(search_snippets)} ===")
for s in search_snippets[:10]:
    print(f"  {s.strip()[:120]}")

print("\n=== DONE ===")
