"""Диагностика v4: точное извлечение JSON с товарами из wholesale HTML."""
import sys, os, time, json, re
from urllib.parse import quote

print("=" * 60)
print("DIAG v4: Extract product JSON from wholesale HTML")
print("=" * 60)

QUERY = "наушники беспроводные"
ENCODED = quote(QUERY, safe="")
PAGE_URL = f"https://ru.aliexpress.com/wholesale?SearchText={ENCODED}&page=1"

out_dir = os.path.join(os.path.dirname(__file__), "data", "collector_output")
os.makedirs(out_dir, exist_ok=True)

# 1. Fetch
print("\n[1] Fetching wholesale page...")
from curl_cffi import requests as cffi_req
t0 = time.monotonic()
resp = cffi_req.get(PAGE_URL, impersonate="chrome", timeout=(20, 40), headers={
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
})
elapsed = time.monotonic() - t0
print(f"    {resp.status_code}, {len(resp.text)} bytes, {elapsed:.1f}s")
html = resp.text

# 2. Find the big script[11] and extract JSON
print("\n[2] Finding product JSON in scripts...")
scripts = re.findall(r'<script[^>]*>(.*?)</script>', html, re.DOTALL)
print(f"    Total scripts: {len(scripts)}")

# The big one (558K chars) at index 11
target_script = None
for i, sc in enumerate(scripts):
    if len(sc) > 100000:
        target_script = (i, sc)
        print(f"    Big script at index {i}: {len(sc)} chars")
        break

if not target_script:
    print("    NO BIG SCRIPT FOUND!")
    sys.exit(1)

idx, sc = target_script
print(f"    First 300 chars: {sc[:300]}")
print(f"    Last 200 chars: {sc[-200:]}")

# 3. Try to parse the whole script as JSON (with leading/trailing cleanup)
print("\n[3] Parsing script as JSON...")
# Try direct
json_data = None
try:
    json_data = json.loads(sc)
    print(f"    Direct parse OK! Keys: {list(json_data.keys())[:20]}")
except json.JSONDecodeError as e:
    print(f"    Direct parse fail: {e}")
    # Try stripping leading non-JSON
    stripped = sc.strip()
    # Find first {
    first_brace = stripped.find('{')
    if first_brace > 0:
        print(f"    Leading {first_brace} chars before {{: {stripped[:first_brace][:100]}")
        stripped = stripped[first_brace:]
    # Find last }
    last_brace = stripped.rfind('}')
    if last_brace > 0:
        stripped = stripped[:last_brace+1]
    try:
        json_data = json.loads(stripped)
        print(f"    Stripped parse OK! Keys: {list(json_data.keys())[:20]}")
    except json.JSONDecodeError as e2:
        print(f"    Stripped parse fail: {e2}")
        # Try to find the main object by depth counting
        depth = 0
        end = 0
        for j, ch in enumerate(stripped):
            if ch == '{': depth += 1
            elif ch == '}':
                depth -= 1
                if depth == 0:
                    end = j + 1
                    break
        if end > 0:
            print(f"    Depth-counted: {end} chars")
            try:
                json_data = json.loads(stripped[:end])
                print(f"    Depth parse OK! Keys: {list(json_data.keys())[:20]}")
            except json.JSONDecodeError as e3:
                print(f"    Depth parse fail: {e3}")

if not json_data:
    print("    FAILED to parse JSON, trying regex approach...")
    # Look for the pattern: window.SOMETHING = {...};
    m = re.search(r'window\.(\w+)\s*=\s*(\{.+?\})\s*;\s*(?:window\.|$)', sc, re.DOTALL)
    if m:
        print(f"    Found window.{m.group(1)} = {{...}} ({len(m.group(2))} chars)")
        try:
            json_data = json.loads(m.group(2))
            print(f"    OK! Keys: {list(json_data.keys())[:20]}")
        except json.JSONDecodeError as e:
            print(f"    Parse fail: {e}")

if not json_data:
    print("    GIVING UP on JSON parse")
    sys.exit(1)

# 4. Save full JSON
out_full = os.path.join(out_dir, "diag_v4_full.json")
with open(out_full, "w", encoding="utf-8") as f:
    json.dump(json_data, f, ensure_ascii=False)
print(f"\n[4] Saved full JSON: {out_full} ({os.path.getsize(out_full)} bytes)")

# 5. Explore structure
print("\n[5] Structure exploration...")
top_keys = list(json_data.keys())
print(f"    Top keys ({len(top_keys)}): {top_keys[:25]}")

# Find itemList / mods / items
for key in ["itemList", "mods", "items", "searchItems", "productList", "resultList", "data"]:
    if key in json_data:
        val = json_data[key]
        if isinstance(val, list):
            print(f"    {key}: list of {len(val)}")
            if val:
                first = val[0]
                if isinstance(first, dict):
                    print(f"      First item keys: {list(first.keys())[:20]}")
        elif isinstance(val, dict):
            print(f"    {key}: dict with keys {list(val.keys())[:15]}")
            # Check sub-keys
            for sk in val:
                sv = val[sk]
                if isinstance(sv, list) and len(sv) > 5:
                    print(f"      {key}.{sk}: list of {len(sv)}")
                    if sv and isinstance(sv[0], dict):
                        print(f"        First: {list(sv[0].keys())[:15]}")
        elif isinstance(val, str):
            print(f"    {key}: str ({len(val)} chars): {val[:80]}")

# 6. Deep dive: find product-like arrays
print("\n[6] Deep dive for product arrays...")
def find_product_arrays(obj, path="", depth=0, results=None):
    if results is None:
        results = []
    if depth > 8:
        return results
    if isinstance(obj, list) and len(obj) >= 5:
        # Check if items look like products
        if obj and isinstance(obj[0], dict):
            keys = set(obj[0].keys())
            product_keys = {"itemId", "item_id", "productId", "title", "price", "salePrice",
                           "finalPrice", "skuId", "imageUrl", "image", "storeName", "sellerId",
                           "itemTitle", "itemUrl", "priceInfo", "gallery"}
            overlap = keys & product_keys
            if len(overlap) >= 2:
                results.append((path, len(obj), list(overlap), list(keys)[:20]))
    if isinstance(obj, dict):
        for k, v in obj.items():
            find_product_arrays(v, f"{path}.{k}" if path else k, depth+1, results)
    elif isinstance(obj, list):
        for i, item in enumerate(obj[:3]):  # only first 3 to avoid recursion explosion
            find_product_arrays(item, f"{path}[{i}]", depth+1, results)
    return results

product_arrays = find_product_arrays(json_data)
print(f"    Found {len(product_arrays)} product-like arrays:")
for path, count, overlap, keys in product_arrays[:10]:
    print(f"      {path}: {count} items, product_keys={overlap}")
    print(f"        all_keys={keys[:15]}")

# 7. If found, extract first product fully
if product_arrays:
    best_path = product_arrays[0][0]
    print(f"\n[7] Extracting first product from {best_path}...")
    # Navigate path
    obj = json_data
    parts = re.findall(r'[^.\[\]]+|\[\d+\]', best_path)
    for part in parts:
        if part.startswith('['):
            idx = int(part[1:-1])
            obj = obj[idx]
        else:
            obj = obj[part]
    first_prod = obj[0] if isinstance(obj, list) else obj
    out_prod = os.path.join(out_dir, "diag_v4_first_product.json")
    with open(out_prod, "w", encoding="utf-8") as f:
        json.dump(first_prod, f, ensure_ascii=False, indent=2)
    print(f"    Saved: {out_prod}")
    # Show key fields
    for k in ["itemId", "item_id", "productId", "title", "itemTitle", "price", "salePrice",
              "finalPrice", "priceInfo", "imageUrl", "image", "gallery", "storeName",
              "sellerId", "itemUrl", "skuId", "sku", "minPrice", "maxPrice"]:
        if k in first_prod:
            v = first_prod[k]
            if isinstance(v, str) and len(v) > 100:
                v = v[:100] + "..."
            elif isinstance(v, dict):
                v = f"dict({list(v.keys())[:8]})"
            elif isinstance(v, list):
                v = f"list({len(v)})"
            print(f"      {k}: {v}")

print("\n" + "=" * 60)
print("DIAG v4 DONE")
print("=" * 60)
