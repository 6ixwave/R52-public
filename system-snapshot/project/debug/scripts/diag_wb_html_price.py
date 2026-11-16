"""Диагностика: что в HTML-странице WB по ценам.

Запуск: python debug/scripts/diag_wb_html_price.py
"""
import json
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

NM_ID = 1097975275
URL = f"https://www.wildberries.ru/catalog/{NM_ID}/detail.aspx"

try:
    from curl_cffi import requests as cffi_req
    USE_CFFI = True
    print("HTTP: curl_cffi (impersonate=chrome)")
except ImportError:
    import requests
    USE_CFFI = False
    print("HTTP: requests (fallback)")

HTML_HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
    "Referer": "https://www.wildberries.ru/",
}

t0 = time.monotonic()
if USE_CFFI:
    resp = cffi_req.get(URL, headers=HTML_HEADERS, impersonate="chrome", timeout=20)
else:
    resp = requests.get(URL, headers=HTML_HEADERS, timeout=20)
elapsed = round(time.monotonic() - t0, 1)

print(f"Status: {resp.status_code}  Time: {elapsed}s  Size: {len(resp.content)} bytes")

if resp.status_code != 200:
    print(f"Body[:500]: {resp.text[:500]}")
    sys.exit(1)

html = resp.text

# 1. Meta tags
print("\n=== META TAGS (price/product) ===")
for m in re.finditer(r'<meta[^>]*(?:property|name)=["\']([^"\']*(?:price|product|og:)[^"\']*)["\'][^>]*content=["\']([^"\']*)["\']', html, re.I):
    print(f"  {m.group(1)} = {m.group(2)[:100]}")

# 2. data-price attributes
print("\n=== data-price ATTRIBUTES ===")
for m in re.finditer(r'data-price=["\']([^"\']+)["\']', html, re.I):
    print(f"  data-price={m.group(1)}")

# 3. Embedded JSON: "price" object
print("\n=== EMBEDDED JSON 'price' OBJECTS ===")
count = 0
for m in re.finditer(r'"price"\s*:\s*\{([^}]{1,500})\}', html):
    count += 1
    if count <= 5:
        print(f"  [{count}] price = {{{m.group(1)[:200]}}}")
print(f"  Total price objects: {count}")

# 4. priceU / priceD / oldPrice
print("\n=== priceU / priceD / oldPrice ===")
for key in ("priceU", "priceD", "oldPrice", "priceU2", "priceD2"):
    for m in re.finditer(rf'"{key}"\s*:\s*([\d.]+)', html):
        print(f"  {key} = {m.group(1)}")
        break

# 5. JSON-LD structured data
print("\n=== JSON-LD (application/ld+json) ===")
for i, m in enumerate(re.finditer(r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', html, re.I | re.DOTALL)):
    try:
        data = json.loads(m.group(1))
        s = json.dumps(data, ensure_ascii=False)
        if "price" in s.lower() or "Price" in s:
            print(f"  [{i}] (has price): {s[:500]}")
        else:
            print(f"  [{i}] (no price): {s[:200]}")
    except (json.JSONDecodeError, ValueError):
        print(f"  [{i}] parse error")

# 6. Visible price text patterns
print("\n=== VISIBLE PRICE TEXT (₽ / руб) ===")
patterns = [
    r'>([\d\s\u00a0]{2,15})\s*(?:₽|руб\.?|RUB)\s*<',
    r'>([\d\s\u00a0]{2,15})\s*(?:₽|руб\.?|RUB)',
]
for pat in patterns:
    for i, m in enumerate(re.finditer(pat, html)):
        if i >= 10:
            break
        ctx_start = max(0, m.start() - 50)
        ctx_end = min(len(html), m.end() + 50)
        ctx = html[ctx_start:ctx_end].replace("\n", " ")
        print(f"  match={m.group(1)!r}  ctx=...{ctx}...")

# 7. Look for specific WB price containers
print("\n=== WB PRICE CONTAINERS (class-based) ===")
for cls in ("price", "Price", "product-price", "item-price", "sale-price", "old-price"):
    for m in re.finditer(rf'class=["\'][^"\']*{cls}[^"\']*["\'][^>]*>([^<]{{1,100}})<', html, re.I):
        print(f"  .{cls}: {m.group(1).strip()[:80]}")

# 8. Check for __NEXT_DATA__ or similar state
print("\n=== STATE OBJECTS ===")
for state_name in ("__NEXT_DATA__", "__NUXT__", "__INITIAL_STATE__", "window.__state"):
    m = re.search(rf'{state_name}\s*=\s*(\{{.*?\}}\s*;?\s*</script>)', html, re.DOTALL)
    if m:
        raw = m.group(1)
        print(f"  {state_name}: {len(raw)} chars")
        if "price" in raw.lower():
            # Find price context
            for pm in re.finditer(r'["\']?price["\']?\s*[:=]\s*([^,}]{1,100})', raw, re.I):
                print(f"    price = {pm.group(1)[:80]}")
                break
    else:
        # Try without </script>
        m = re.search(rf'{state_name}\s*=\s*(\{{)', html)
        if m:
            print(f"  {state_name}: found at pos {m.start()}, searching for price...")
            chunk = html[m.start():m.start()+5000]
            for pm in re.finditer(r'["\']?price["\']?\s*[:=]\s*([^,}]{1,100})', chunk, re.I):
                print(f"    price = {pm.group(1)[:80]}")
                break

# 9. Save relevant HTML chunk
out = ROOT / "debug" / "outputs" / "wb_html_snippet.html"
out.write_text(html[:50000], encoding="utf-8")
print(f"\nHTML snippet saved: {out} ({len(html)} total)")

# 10. Also check: is there a "sizes" or "variants" section with prices?
print("\n=== SIZES / VARIANTS SECTION ===")
for m in re.finditer(r'"(sizes|variants|offers)"\s*:\s*\[', html):
    chunk = html[m.start():m.start()+1000]
    print(f"  {m.group(1)}: {chunk[:300]}")
    break
