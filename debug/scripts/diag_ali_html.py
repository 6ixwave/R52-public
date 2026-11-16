"""Диагностика: что реально в HTML AliExpress — цены, медиа, JSON."""
import sys
import json
import logging
import re
import time

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
sys.path.insert(0, ".")

from r52.collectors.aliexpress import AliExpressCollector

c = AliExpressCollector(use_proxies=False)
lid = "1005011813323763"
url = c.build_url(lid)

t0 = time.monotonic()
resp = c.fetch(url)
elapsed = time.monotonic() - t0
if resp is None:
    print("FETCH FAILED")
    sys.exit(1)

html = resp.text if hasattr(resp, "text") else resp.decode("utf-8", errors="replace")
print(f"HTML: {len(html)} bytes ({elapsed:.1f}s)")

# 1. JSON-LD
print("\n=== JSON-LD ===")
blocks = re.findall(r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', html, re.DOTALL | re.IGNORECASE)
print(f"Blocks: {len(blocks)}")
for i, b in enumerate(blocks[:3]):
    print(f"  [{i}] {b.strip()[:300]}")

# 2. __INIT_DATA_STATE__ / runParams
print("\n=== Inline JS State ===")
for marker in ("__INIT_DATA_STATE__", "runParams", "productData", "skuPriceList", "priceInfo"):
    idx = html.find(marker)
    if idx >= 0:
        print(f"  {marker} at pos {idx}: ...{html[max(0,idx-20):idx+200]}...")
    else:
        print(f"  {marker}: NOT FOUND")

# 3. Price patterns
print("\n=== Price Patterns ===")
# data-price, data-original-price
for pat_name, pat in [
    ("data-price", r'data-price=["\']([^"\']+)["\']'),
    ("data-original-price", r'data-original-price=["\']([^"\']+)["\']'),
    ("data-activity-price", r'data-activity-price=["\']([^"\']+)["\']'),
    ("class=price", r'class="[^"]*price[^"]*"[^>]*>\s*(?:<[^>]+>\s*)*([\d,]+\.?\d*)'),
    ("minPrice", r'"minPrice"\s*:\s*([\d.]+)'),
    ("salePrice", r'"salePrice"\s*:\s*([\d.]+)'),
    ("tradePrice", r'"tradePrice"\s*:\s*([\d.]+)'),
    ("formattedPrice", r'"formattedPrice"\s*:\s*"([^"]+)"'),
    ("priceValue", r'"priceValue"\s*:\s*([\d.]+)'),
    ("amount", r'"amount"\s*:\s*([\d.]+)'),
]:
    matches = re.findall(pat, html, re.I)
    if matches:
        print(f"  {pat_name}: {matches[:5]}")
    else:
        print(f"  {pat_name}: -")

# 4. Currency
print("\n=== Currency ===")
for pat in [r'"currency"\s*:\s*"([^"]+)"', r'data-currency=["\']([^"\']+)', r'og:price:currency[^>]*content=["\']([^"\']+)']:
    m = re.findall(pat, html, re.I)
    if m:
        print(f"  {pat[:40]}: {m[:3]}")

# 5. Images (alicdn)
print("\n=== Images (alicdn) ===")
img_urls = re.findall(r'(?:src|data-src|data-lazy-src|data-original|data-url)=["\']([^"\']*alicdn[^"\']*)["\']', html, re.I)
print(f"Total alicdn img refs: {len(img_urls)}")
for u in img_urls[:8]:
    print(f"  {u[:120]}")

# Also check for //ae01.alicdn.com pattern
img2 = re.findall(r"""["'](//[^"']*alicdn[^"']*)["']""", html)
print(f"Protocol-relative alicdn: {len(img2)}")
for u in img2[:5]:
    print(f"  {u[:120]}")

# 6. Availability
print("\n=== Availability ===")
for pat in [r'(in\s*stock|out\s*of\s*stock|pre-?order|available|unavailable)',
            r'"stock"\s*:\s*(\d+)',
            r'"inStock"\s*:\s*(true|false)',
            r'"stockStatus"\s*:\s*"([^"]+)"']:
    m = re.findall(pat, html, re.I)
    if m:
        print(f"  {pat[:50]}: {m[:5]}")

# 7. h1 / title
print("\n=== Title ===")
h1 = re.findall(r'<h1[^>]*>(.*?)</h1>', html, re.DOTALL | re.I)
print(f"h1: {[x[:80] for x in h1[:2]]}")
title_tag = re.findall(r'<title[^>]*>(.*?)</title>', html, re.DOTALL | re.I)
print(f"<title>: {[x[:80] for x in title_tag[:1]]}")

# 8. Check for specific AliExpress price component
print("\n=== AliExpress Price Component ===")
# AliExpress often uses: <div class="price-current"><span class="price-current-symbol">...</span><span class="price-current-integer">...</span>
for pat in [
    r'price-current[^>]*>(.*?)</div>',
    r'price-current-integer[^>]*>(\d+)',
    r'price-current-decimal[^>]*>(\d+)',
    r'price-current-symbol[^>]*>([^<]+)',
    r'price-original[^>]*>(.*?)</div>',
]:
    m = re.findall(pat, html, re.I | re.DOTALL)
    if m:
        print(f"  {pat[:50]}: {[x[:60] for x in m[:4]]}")

# 9. Dump a section around first "price" occurrence
print("\n=== Context around first 'price' ===")
idx = html.lower().find("price")
if idx >= 0:
    print(html[max(0, idx-100):idx+400])

print(f"\nDone. Stats: {c.stats}")
