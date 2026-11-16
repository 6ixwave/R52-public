"""Диагностика v5: извлечение _init_data_ из wholesale HTML AliExpress RU."""
import sys, os, time, json, re
from urllib.parse import quote

print("=" * 60)
print("DIAG v5: Extract _dida_config_._init_data_ from wholesale HTML")
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

# 2. Extract between markers
print("\n[2] Extracting _init_data_ between markers...")
start_marker = "/*!-->init-data-start--*/"
end_marker = "/*!-->init-data-end--*/"

start_idx = html.find(start_marker)
end_idx = html.find(end_marker)
if start_idx < 0 or end_idx < 0:
    print(f"    MARKERS NOT FOUND! start={start_idx}, end={end_idx}")
    # Try alternative: find window._dida_config_._init_data_=
    m = re.search(r'window\._dida_config_\._init_data_\s*=\s*', html)
    if m:
        start_idx = m.end()
        print(f"    Found assignment at {start_idx}")
        # Find end: look for }/*!-->init-data-end--*/ or similar
        end_idx = html.find("}/*!-->init-data-end--*/", start_idx)
        if end_idx < 0:
            end_idx = html.find("}", start_idx + 400000)
        print(f"    End at {end_idx}")
    else:
        print("    NO _init_data_ found at all!")
        sys.exit(1)
else:
    start_idx += len(start_marker)
    print(f"    Markers found: start={start_idx}, end={end_idx}")

raw = html[start_idx:end_idx].strip()
print(f"    Raw length: {len(raw)} chars")
print(f"    First 200: {raw[:200]}")
print(f"    Last 200: {raw[-200:]}")

# 3. Parse: it's JS object notation (unquoted keys)
print("\n[3] Parsing JS object...")
# Strategy: quote unquoted keys
# Pattern: {key: or ,key: where key is a valid identifier
def js_obj_to_json(s):
    """Convert JS object literal to valid JSON."""
    # Quote unquoted keys: {key: → {"key":
    # But don't touch strings
    result = []
    i = 0
    in_string = False
    string_char = ''
    while i < len(s):
        ch = s[i]
        if in_string:
            result.append(ch)
            if ch == '\\':
                # escape next char
                if i + 1 < len(s):
                    result.append(s[i+1])
                    i += 1
            elif ch == string_char:
                in_string = False
        else:
            if ch in ('"', "'"):
                in_string = True
                string_char = ch
                result.append(ch)
            elif ch in '{,':
                # Check if next non-space is a valid identifier (unquoted key)
                j = i + 1
                while j < len(s) and s[j] in ' \t\n\r':
                    j += 1
                if j < len(s) and (s[j].isalpha() or s[j] == '_'):
                    # Read identifier
                    k = j
                    while k < len(s) and (s[k].isalnum() or s[k] == '_'):
                        k += 1
                    ident = s[j:k]
                    # Check it's followed by : (not a string value)
                    m2 = k
                    while m2 < len(s) and s[m2] in ' \t\n\r':
                        m2 += 1
                    if m2 < len(s) and s[m2] == ':':
                        # Replace: skip whitespace, add quoted key
                        result.append(ch)
                        result.append(' ')
                        result.append(f'"{ident}"')
                        i = k
                        continue
                result.append(ch)
            else:
                result.append(ch)
        i += 1
    return ''.join(result)

# Also handle single-quoted strings → double-quoted
raw_fixed = js_obj_to_json(raw)
# Replace single quotes with double (rough, for strings that weren't handled above)
# Actually the js_obj_to_json should handle it. Let's try parsing.
try:
    data = json.loads(raw_fixed)
    print(f"    JSON OK! Top keys: {list(data.keys())[:15]}")
except json.JSONDecodeError as e:
    print(f"    Parse fail at pos {e.pos}: {e.msg}")
    print(f"    Context: ...{raw_fixed[max(0,e.pos-50):e.pos+50]}...")
    # Try: maybe it's `data: {...}` → need to handle top-level too
    # Let's try a simpler approach: just quote ALL unquoted identifiers before :
    raw2 = re.sub(r'([{,]\s*)([a-zA-Z_]\w*)(\s*:)', r'\1"\2"\3', raw)
    try:
        data = json.loads(raw2)
        print(f"    Regex JSON OK! Top keys: {list(data.keys())[:15]}")
    except json.JSONDecodeError as e2:
        print(f"    Regex parse fail at pos {e2.pos}: {e2.msg}")
        print(f"    Context: ...{raw2[max(0,e2.pos-50):e2.pos+50]}...")
        # Save for manual inspection
        out_raw = os.path.join(out_dir, "diag_v5_raw.txt")
        with open(out_raw, "w", encoding="utf-8") as f:
            f.write(raw)
        out_fixed = os.path.join(out_dir, "diag_v5_fixed.txt")
        with open(out_fixed, "w", encoding="utf-8") as f:
            f.write(raw_fixed)
        print(f"    Saved raw and fixed for inspection")
        data = None

if not data:
    print("    Trying node.js eval...")
    # Save as JS file and eval with node
    js_file = os.path.join(out_dir, "diag_v5_data.js")
    with open(js_file, "w", encoding="utf-8") as f:
        f.write(f"const data = {raw};\n")
        f.write(f"require('fs').writeFileSync('{out_dir.replace(os.sep,'/')}/diag_v5_node.json', JSON.stringify(data, null, 2));\n")
    print(f"    Saved JS: {js_file}")
    print("    (run: node diag_v5_data.js)")
    sys.exit(0)

# 4. Save full data
out_full = os.path.join(out_dir, "diag_v5_init_data.json")
with open(out_full, "w", encoding="utf-8") as f:
    json.dump(data, f, ensure_ascii=False, indent=2)
print(f"\n[4] Saved: {out_full} ({os.path.getsize(out_full)} bytes)")

# 5. Explore structure
print("\n[5] Structure...")
if "data" in data:
    d = data["data"]
    print(f"    data keys: {list(d.keys())[:20]}")
    if "hierarchy" in d:
        h = d["hierarchy"]
        print(f"    hierarchy keys: {list(h.keys())[:10]}")
        if "structure" in h:
            print(f"    structure: {json.dumps(h['structure'], ensure_ascii=False)[:300]}")
    if "components" in d:
        comps = d["components"]
        print(f"    components: {len(comps)} items")
        # Find the one with product list
        for cid, comp in list(comps.items())[:30]:
            if isinstance(comp, dict):
                fields = comp.get("fields", {})
                if isinstance(fields, dict):
                    fkeys = list(fields.keys())
                    if any(k in str(fkeys).lower() for k in ["item", "product", "list", "gallery", "card"]):
                        print(f"      {cid}: fields={fkeys[:10]}")
    # Also check top-level keys in data
    for k in ["itemList", "items", "productList", "searchItems"]:
        if k in d:
            print(f"    data.{k}: {type(d[k]).__name__}, len={len(d[k]) if isinstance(d[k], list) else '?'}")

# 6. Find product arrays
print("\n[6] Finding product arrays...")
def find_arrays(obj, path="", depth=0, results=None):
    if results is None:
        results = []
    if depth > 10:
        return results
    if isinstance(obj, list) and len(obj) >= 3:
        if obj and isinstance(obj[0], dict):
            keys = set(obj[0].keys())
            prod_keys = {"itemId","item_id","productId","title","price","salePrice",
                        "finalPrice","skuId","imageUrl","image","gallery","storeName",
                        "sellerId","itemTitle","itemUrl","priceInfo","minPrice","maxPrice",
                        "tItemType","fields","type"}
            overlap = keys & prod_keys
            if len(overlap) >= 2:
                results.append((path, len(obj), sorted(overlap), sorted(keys)[:20]))
    if isinstance(obj, dict):
        for k, v in obj.items():
            find_arrays(v, f"{path}.{k}" if path else k, depth+1, results)
    elif isinstance(obj, list):
        for i, item in enumerate(obj[:5]):
            find_arrays(item, f"{path}[{i}]", depth+1, results)
    return results

arrays = find_arrays(data)
print(f"    Found {len(arrays)} arrays:")
for path, count, overlap, keys in arrays[:15]:
    print(f"      {path}: {count} items")
    print(f"        overlap={overlap}")
    print(f"        keys={keys[:15]}")

# 7. Extract first product
if arrays:
    best = arrays[0]
    print(f"\n[7] First product from {best[0]}...")
    obj = data
    parts = re.findall(r'[^.\[\]]+|\[\d+\]', best[0])
    for part in parts:
        if part.startswith('['):
            obj = obj[int(part[1:-1])]
        else:
            obj = obj[part]
    first = obj[0] if isinstance(obj, list) else obj
    out_p = os.path.join(out_dir, "diag_v5_first_product.json")
    with open(out_p, "w", encoding="utf-8") as f:
        json.dump(first, f, ensure_ascii=False, indent=2)
    print(f"    Saved: {out_p}")
    # Show fields
    if isinstance(first, dict):
        for k in sorted(first.keys())[:25]:
            v = first[k]
            if isinstance(v, str) and len(v) > 80:
                v = v[:80] + "..."
            elif isinstance(v, dict):
                v = f"dict({list(v.keys())[:8]})"
            elif isinstance(v, list):
                v = f"list({len(v)})"
            print(f"      {k}: {v}")

print("\n" + "=" * 60)
print("DIAG v5 DONE")
print("=" * 60)
