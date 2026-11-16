"""Диагностика v6: точное извлечение _init_data_ (срез префикса + кавычки)."""
import sys, os, time, json, re
from urllib.parse import quote

print("=" * 60)
print("DIAG v6: _init_data_ — strip prefix, quote keys, parse")
print("=" * 60)

QUERY = "наушники беспроводные"
ENCODED = quote(QUERY, safe="")
PAGE_URL = f"https://ru.aliexpress.com/wholesale?SearchText={ENCODED}&page=1"

out_dir = os.path.join(os.path.dirname(__file__), "data", "collector_output")
os.makedirs(out_dir, exist_ok=True)

# 1. Fetch
print("\n[1] Fetch...")
from curl_cffi import requests as cffi_req
t0 = time.monotonic()
resp = cffi_req.get(PAGE_URL, impersonate="chrome", timeout=(20, 40), headers={
    "Accept": "text/html,application/xhtml+xml",
    "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
})
elapsed = time.monotonic() - t0
print(f"    {resp.status_code}, {len(resp.text)} bytes, {elapsed:.1f}s")
html = resp.text

# 2. Extract between markers
print("\n[2] Extract between markers...")
start_marker = "/*!-->init-data-start--*/"
end_marker = "/*!-->init-data-end--*/"
start_idx = html.find(start_marker)
end_idx = html.find(end_marker)
print(f"    start={start_idx}, end={end_idx}")
raw = html[start_idx + len(start_marker):end_idx].strip()
print(f"    raw len: {len(raw)}")
print(f"    First 100: {raw[:100]}")

# 3. Strip prefix "window._dida_config_._init_data_="
prefix_pat = r'^window\._dida_config_\._init_data_\s*=\s*'
m = re.match(prefix_pat, raw)
if m:
    print(f"    Stripped prefix: {m.group(0)!r}")
    raw_obj = raw[m.end():]
else:
    # Maybe already starts with {
    raw_obj = raw
    print(f"    No prefix, starts with: {raw_obj[:50]!r}")

print(f"    Object len: {len(raw_obj)}")
print(f"    Object first 100: {raw_obj[:100]}")
print(f"    Object last 100: {raw_obj[-100:]}")

# 4. Quote unquoted keys (regex: after { or , or start, identifier followed by :)
print("\n[3] Quoting unquoted keys...")
# Handle: {key: or ,key:  → {"key": or ,"key":
# But NOT inside strings. For 489K chars this is fast enough with a state machine.
def quote_keys(s):
    out = []
    i = 0
    n = len(s)
    in_str = False
    str_ch = ''
    while i < n:
        ch = s[i]
        if in_str:
            out.append(ch)
            if ch == '\\' and i + 1 < n:
                out.append(s[i+1])
                i += 1
            elif ch == str_ch:
                in_str = False
            i += 1
        else:
            if ch in ('"', "'"):
                in_str = True
                str_ch = ch
                out.append(ch)
                i += 1
            elif ch in '{,':
                # Look ahead for identifier:
                j = i + 1
                while j < n and s[j] in ' \t\n\r':
                    j += 1
                if j < n and (s[j].isalpha() or s[j] == '_'):
                    k = j
                    while k < n and (s[k].isalnum() or s[k] == '_'):
                        k += 1
                    ident = s[j:k]
                    # Must be followed by optional whitespace then :
                    m2 = k
                    while m2 < n and s[m2] in ' \t\n\r':
                        m2 += 1
                    if m2 < n and s[m2] == ':':
                        out.append(ch)
                        out.append('"')
                        out.append(ident)
                        out.append('"')
                        i = k  # skip the identifier, loop will add whitespace + :
                        continue
                out.append(ch)
                i += 1
            else:
                out.append(ch)
                i += 1
    return ''.join(out)

fixed = quote_keys(raw_obj)
print(f"    Fixed len: {len(fixed)}")
print(f"    Fixed first 150: {fixed[:150]}")

# 5. Parse JSON
print("\n[4] Parsing JSON...")
data = None
try:
    data = json.loads(fixed)
    print(f"    SUCCESS! Top keys: {list(data.keys())[:20]}")
except json.JSONDecodeError as e:
    print(f"    Fail at pos {e.pos}: {e.msg}")
    print(f"    Context: ...{fixed[max(0,e.pos-60):e.pos+60]}...")
    # Save for debug
    with open(os.path.join(out_dir, "diag_v6_fixed.txt"), "w", encoding="utf-8") as f:
        f.write(fixed)
    print(f"    Saved fixed text for inspection")

if not data:
    print("    GIVING UP")
    sys.exit(1)

# 6. Save full
out_full = os.path.join(out_dir, "diag_v6_full.json")
with open(out_full, "w", encoding="utf-8") as f:
    json.dump(data, f, ensure_ascii=False, indent=2)
print(f"\n[5] Saved: {out_full} ({os.path.getsize(out_full)} bytes)")

# 7. Explore
print("\n[6] Structure...")
d = data.get("data", data)
print(f"    data keys: {list(d.keys())[:20]}")

if "components" in d:
    comps = d["components"]
    print(f"    components: {len(comps)}")
    # Find product-containing components
    for cid, comp in comps.items():
        if isinstance(comp, dict):
            fields = comp.get("fields", {})
            if isinstance(fields, dict):
                fkeys = list(fields.keys())
                joined = str(fkeys).lower()
                if any(x in joined for x in ["item", "product", "gallery", "card", "list", "search"]):
                    print(f"      {cid}: {fkeys[:12]}")

# 8. Find product arrays
print("\n[7] Finding product arrays...")
def find_product_lists(obj, path="", depth=0, results=None):
    if results is None:
        results = []
    if depth > 12:
        return results
    if isinstance(obj, list) and len(obj) >= 5:
        if obj and isinstance(obj[0], dict):
            keys = set(obj[0].keys())
            prod_keys = {"itemId","item_id","productId","title","price","salePrice",
                        "finalPrice","skuId","imageUrl","image","gallery","storeName",
                        "sellerId","itemTitle","itemUrl","priceInfo","minPrice","maxPrice",
                        "tItemType","fields","type","cardData","layoutAreas","presentations"}
            overlap = keys & prod_keys
            if len(overlap) >= 2:
                results.append((path, len(obj), sorted(overlap)))
    if isinstance(obj, dict):
        for k, v in obj.items():
            find_product_lists(v, f"{path}.{k}" if path else k, depth+1, results)
    elif isinstance(obj, list):
        for i, item in enumerate(obj[:5]):
            find_product_lists(item, f"{path}[{i}]", depth+1, results)
    return results

arrays = find_product_lists(data)
print(f"    Found {len(arrays)} product-like arrays:")
for path, count, overlap in arrays[:15]:
    print(f"      {path}: {count} items, keys={overlap[:8]}")

# 9. Extract first product
if arrays:
    best_path = arrays[0][0]
    print(f"\n[8] First product from {best_path}...")
    obj = data
    parts = re.findall(r'[^.\[\]]+|\[\d+\]', best_path)
    for part in parts:
        if part.startswith('['):
            obj = obj[int(part[1:-1])]
        else:
            obj = obj[part]
    first = obj[0] if isinstance(obj, list) else obj
    out_p = os.path.join(out_dir, "diag_v6_first_product.json")
    with open(out_p, "w", encoding="utf-8") as f:
        json.dump(first, f, ensure_ascii=False, indent=2)
    print(f"    Saved: {out_p}")
    if isinstance(first, dict):
        for k in sorted(first.keys())[:30]:
            v = first[k]
            if isinstance(v, str) and len(v) > 80:
                v = v[:80] + "..."
            elif isinstance(v, dict):
                v = f"dict({list(v.keys())[:10]})"
            elif isinstance(v, list):
                v = f"list({len(v)})"
            print(f"      {k}: {v}")

print("\n" + "=" * 60)
print("DIAG v6 DONE")
print("=" * 60)
