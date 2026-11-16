"""Диагностика v3: извлечение товаров из HTML страницы wholesale AliExpress RU."""
import sys, os, time, json, re, traceback
from urllib.parse import quote

print("=" * 60)
print("DIAG v3: Parse wholesale HTML for product data")
print("=" * 60)

QUERY = "наушники беспроводные"
ENCODED = quote(QUERY, safe="")
PAGE_URL = f"https://ru.aliexpress.com/wholesale?SearchText={ENCODED}&page=1"

# 1. Fetch wholesale page
print("\n[1] Fetching wholesale page...")
from curl_cffi import requests as cffi_req
t0 = time.monotonic()
resp = cffi_req.get(PAGE_URL, impersonate="chrome", timeout=(20, 40), headers={
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
})
elapsed = time.monotonic() - t0
print(f"    Status: {resp.status_code}, size: {len(resp.text)} bytes, {elapsed:.1f}s")

out_dir = os.path.join(os.path.dirname(__file__), "data", "collector_output")
os.makedirs(out_dir, exist_ok=True)
html_path = os.path.join(out_dir, "diag_wholesale_full.html")
with open(html_path, "w", encoding="utf-8") as f:
    f.write(resp.text)
print(f"    Saved: {html_path}")

html = resp.text

# 2. Look for embedded JSON states
print("\n[2] Searching for embedded JSON data patterns...")
patterns = [
    ("__INIT_DATA_STATE__", r'window\.__INIT_DATA_STATE__\s*=\s*(\{.*?\})\s*;\s*(?:</script>|window\.)'),
    ("__NEXT_DATA__", r'<script[^>]*id="__NEXT_DATA__"[^>]*>(.*?)</script>'),
    ("runParams", r'window\.runParams\s*=\s*(\{.*?\})\s*;\s*(?:</script>|window\.)'),
    ("__INITIAL_STATE__", r'window\.__INITIAL_STATE__\s*=\s*(\{.*?\})\s*;\s*(?:</script>|window\.)'),
    ("__APP_DATA__", r'window\.__APP_DATA__\s*=\s*(\{.*?\})\s*;\s*(?:</script>|window\.)'),
    ("g_config", r'window\.g_config\s*=\s*(\{.*?\})\s*;\s*(?:</script>|window\.)'),
    ("__INIT_PROPS__", r'window\.__INIT_PROPS__\s*=\s*(\{.*?\})\s*;\s*(?:</script>|window\.)'),
]

for name, pat in patterns:
    m = re.search(pat, html, re.DOTALL)
    if m:
        raw = m.group(1)
        print(f"    FOUND: {name} ({len(raw)} chars)")
        # Try to parse
        try:
            data = json.loads(raw)
            print(f"      JSON OK! Top keys: {list(data.keys())[:15]}")
            out = os.path.join(out_dir, f"diag_{name}.json")
            with open(out, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False)
            print(f"      Saved: {out}")
        except json.JSONDecodeError as e:
            print(f"      JSON parse fail: {e}")
            print(f"      First 200: {raw[:200]}")
    else:
        # Check if name exists at all
        idx = html.find(name)
        if idx >= 0:
            snippet = html[max(0, idx-20):idx+100].replace("\n", " ")
            print(f"    PARTIAL: {name} found at {idx}: {snippet[:120]}")
        else:
            print(f"    MISS: {name}")

# 3. Look for product-like structures in any script
print("\n[3] Scanning scripts for product data...")
scripts = re.findall(r'<script[^>]*>(.*?)</script>', html, re.DOTALL)
print(f"    Total scripts: {len(scripts)}")

product_markers = ["itemId", "item_id", "productId", "product_id", "finalPrice", "salePrice",
                   "title", "price", "skuId", "sku_id", "sellerId", "storeName"]
for i, sc in enumerate(scripts):
    found_markers = [m for m in product_markers if m in sc]
    if len(found_markers) >= 3 and len(sc) > 1000:
        print(f"    Script[{i}] ({len(sc)} chars): markers={found_markers[:6]}")
        # Try to find JSON object
        json_starts = [m.start() for m in re.finditer(r'\{', sc[:500])]
        if json_starts:
            # Try parsing the whole thing or a big chunk
            for start_pos in [0, json_starts[0]]:
                chunk = sc[start_pos:start_pos+50000]
                try:
                    # Try to find balanced JSON
                    depth = 0
                    end = 0
                    for j, ch in enumerate(chunk):
                        if ch == '{': depth += 1
                        elif ch == '}':
                            depth -= 1
                            if depth == 0:
                                end = j + 1
                                break
                    if end > 0:
                        data = json.loads(chunk[:end])
                        print(f"      Parsed JSON at offset {start_pos}, keys: {list(data.keys())[:10] if isinstance(data, dict) else 'list'}")
                        out = os.path.join(out_dir, f"diag_script_{i}.json")
                        with open(out, "w", encoding="utf-8") as f:
                            json.dump(data, f, ensure_ascii=False)
                        break
                except (json.JSONDecodeError, ValueError):
                    continue

# 4. Look for data-* attributes with product info
print("\n[4] HTML data attributes...")
data_attrs = re.findall(r'data-(?:item-?id|product-?id|sku-?id|price|title)[^=]*="([^"]{3,200})"', html, re.I)
print(f"    Found {len(data_attrs)} data attributes")
for a in data_attrs[:10]:
    print(f"      {a[:80]}")

# 5. Look for product cards in DOM structure
print("\n[5] Product card patterns...")
# Common AliExpress patterns
card_pats = [
    (r'class="[^"]*product-card[^"]*"', "product-card class"),
    (r'class="[^"]*gallery-item[^"]*"', "gallery-item"),
    (r'data-pl="[^"]*product[^"]*"', "data-pl product"),
    (r'class="[^"]*search-item[^"]*"', "search-item"),
    (r'class="[^"]*item-card[^"]*"', "item-card"),
    (r'class="[^"]*Card[^"]*"', "Card class"),
    (r'class="[^"]*card[^"]*"', "card class"),
]
for pat, label in card_pats:
    matches = re.findall(pat, html, re.I)
    if matches:
        print(f"    {label}: {len(matches)} matches")
        print(f"      First: {matches[0][:80]}")

# 6. Try to find __INIT_DATA_STATE__ with more flexible regex
print("\n[6] Flexible JSON extraction...")
# Sometimes it's: window.__INIT_DATA_STATE__ = {...};  (no trailing period)
flex_pats = [
    r'window\.__INIT_DATA_STATE__\s*=\s*(\{.+?\})\s*;',
    r'window\.__INIT_DATA_STATE__\s*=\s*(\{.+?\})\s*\n',
    r'__INIT_DATA_STATE__\s*=\s*(\{.+?\})\s*;',
]
for i, pat in enumerate(flex_pats):
    m = re.search(pat, html, re.DOTALL)
    if m:
        raw = m.group(1)
        print(f"    Flex[{i}] found: {len(raw)} chars")
        try:
            data = json.loads(raw)
            print(f"      JSON OK! Keys: {list(data.keys())[:15]}")
            out = os.path.join(out_dir, f"diag_flex_{i}.json")
            with open(out, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False)
        except json.JSONDecodeError as e:
            print(f"      Parse fail at pos {e.pos}: {raw[e.pos-20:e.pos+20]}")
        break

# 7. Check for "mods" or "searchItems" or "itemList" in any JSON
print("\n[7] Deep search for product arrays in HTML...")
# Look for large JSON blobs containing product-like keys
big_jsons = re.findall(r'(?:=\s*|>\s*)(\{.{5000,}\})\s*(?:</script>|;|\n|$)', html, re.DOTALL)
print(f"    Large JSON-like blobs: {len(big_jsons)}")
for i, blob in enumerate(big_jsons[:5]):
    keys_found = [k for k in ["itemList", "searchItems", "mods", "items", "productList", "resultList"] if k in blob]
    if keys_found:
        print(f"    Blob[{i}] ({len(blob)} chars): keys={keys_found}")
        # Try to find the array
        for k in keys_found:
            m = re.search(rf'"{k}"\s*:\s*(\[[^\]]{{100,}}\])', blob)
            if m:
                arr_raw = m.group(1)
                try:
                    arr = json.loads(arr_raw)
                    print(f"      {k}: {len(arr)} items")
                    if arr:
                        print(f"      First item keys: {list(arr[0].keys())[:10] if isinstance(arr[0], dict) else arr[0]}")
                except:
                    pass

print("\n" + "=" * 60)
print("DIAG v3 DONE")
print("=" * 60)
