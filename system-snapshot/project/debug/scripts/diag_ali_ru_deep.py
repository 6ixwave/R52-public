"""Глубокий поиск товарных данных в распарсенном _init_data_.
Цель: найти массив карточек (price, title, image) или XHR-endpoint."""
import json, re, os, logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("diag_deep")

BASE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(BASE, "data", "collector_output")
os.makedirs(OUT, exist_ok=True)

# Load converted JSON
conv_path = os.path.join(OUT, "diag_ws_init_data_conv.json")
with open(conv_path, "r", encoding="utf-8") as f:
    raw = f.read()
print(f"Converted JSON: {len(raw)} chars")

data = json.loads(raw)
d = data.get("data", data)
print(f"Top keys: {list(d.keys())}")

# ─── 1. GLOBAL section ───
print("\n" + "="*60)
print("1. GLOBAL section")
print("="*60)
global_sec = d.get("global", {})
if global_sec:
    print(f"global keys: {list(global_sec.keys())[:20]}")
    for k, v in global_sec.items():
        sz = len(json.dumps(v, ensure_ascii=False)) if isinstance(v, (dict, list)) else len(str(v))
        if sz > 100:
            print(f"  {k}: {type(v).__name__}, ~{sz}")
            if isinstance(v, dict):
                print(f"    sub-keys: {list(v.keys())[:15]}")
            elif isinstance(v, list) and v:
                print(f"    len={len(v)}, [0] type={type(v[0]).__name__}")
                if isinstance(v[0], dict):
                    print(f"    [0] keys: {list(v[0].keys())[:15]}")
    # Save global
    gpath = os.path.join(OUT, "diag_ws_global.json")
    with open(gpath, "w", encoding="utf-8") as f:
        json.dump(global_sec, f, ensure_ascii=False, indent=2)
    print(f"  Saved → {gpath}")

# ─── 2. searchResult area ───
print("\n" + "="*60)
print("2. searchResult area")
print("="*60)
sr_idx = raw.find("searchResult")
print(f"searchResult at offset: {sr_idx}")
if sr_idx > 0:
    window = raw[sr_idx-50:sr_idx+2000]
    print(f"Context: {window[:1500]}")
    # Try to find the containing object
    # Look backwards for the key
    # Find "searchResultType" and go up to find the parent object
    sr_type_idx = raw.find('"searchResultType"')
    if sr_type_idx > 0:
        # Go back to find the opening {
        # Count backwards for matching {
        back = sr_type_idx
        depth = 0
        while back > 0:
            if raw[back] == '}':
                depth += 1
            elif raw[back] == '{':
                if depth == 0:
                    break
                depth -= 1
            back -= 1
        sr_block = raw[back:back+3000]
        print(f"\nsearchResult block (first 2000): {sr_block[:2000]}")

# ─── 3. Find product arrays (look for arrays with >10 dict elements) ───
print("\n" + "="*60)
print("3. Product arrays in modules")
print("="*60)

modules = d.get("data", d)
for mod_name, mod_val in modules.items():
    if not isinstance(mod_val, dict):
        continue
    # Recursively find lists with >10 elements
    def find_lists(obj, path="", depth=0):
        results = []
        if depth > 8:
            return results
        if isinstance(obj, list) and len(obj) > 10:
            if obj and isinstance(obj[0], dict):
                results.append((path, len(obj), list(obj[0].keys())[:10]))
        if isinstance(obj, dict):
            for k, v in obj.items():
                results.extend(find_lists(v, f"{path}.{k}" if path else k, depth+1))
        elif isinstance(obj, list):
            for i, item in enumerate(obj[:3]):
                results.extend(find_lists(item, f"{path}[{i}]", depth+1))
        return results
    
    lists = find_lists(mod_val, mod_name)
    if lists:
        print(f"\n  Module {mod_name}:")
        for path, length, keys in lists[:5]:
            print(f"    {path}: list[{length}], keys={keys}")

# ─── 4. Look for specific product data patterns ───
print("\n" + "="*60)
print("4. Product data patterns")
print("="*60)

# In the raw text, look for patterns that indicate product cards
# Common: "cardData", "productInfo", "itemInfo", "skuInfo"
for pattern in ["cardData", "productInfo", "itemInfo", "skuInfo", "productCard",
                "itemCard", "cardList", "productList", "items", "products"]:
    matches = list(re.finditer(rf'"{pattern}"\s*:\s*', raw))
    if matches:
        print(f"  '{pattern}': {len(matches)} occurrences")
        # Show first match context
        m = matches[0]
        ctx = raw[m.start():m.start()+300]
        print(f"    First: {ctx[:250]}")

# ─── 5. The cards module deep dive ───
print("\n" + "="*60)
print("5. Cards module deep dive")
print("="*60)

cards = None
for k in modules:
    if "cards" in k.lower():
        cards = modules[k]
        cards_key = k
        break

if cards:
    print(f"Module: {cards_key}")
    print(f"Full JSON: {json.dumps(cards, ensure_ascii=False)[:500]}")
    # Maybe the data is in a sub-key we missed
    for k, v in cards.items():
        print(f"  {k}: {type(v).__name__}")
        if isinstance(v, dict):
            for k2, v2 in v.items():
                sz = len(json.dumps(v2, ensure_ascii=False)) if isinstance(v2, (dict, list)) else len(str(v2))
                print(f"    {k2}: {type(v2).__name__}, ~{sz}")

# ─── 6. Check for XHR/API URL patterns in the script ───
print("\n" + "="*60)
print("6. XHR/API endpoints in script")
print("="*60)

# Find URL patterns that look like API calls
api_patterns = re.findall(r'"(https?://[^"]*(?:api|webapi|mtop|gateway)[^"]*)"', raw)
print(f"API URLs: {len(api_patterns)}")
for u in set(api_patterns[:20]):
    print(f"  {u[:100]}")

# Also look for relative paths
rel_apis = re.findall(r'"(/(?:api|webapi|mtop|gateway|fn)/[^"]*)"', raw)
print(f"\nRelative API paths: {len(rel_apis)}")
for u in set(rel_apis[:15]):
    print(f"  {u[:100]}")

# ─── 7. Check the big script for window.__INIT_DATA_CALLBACK__ ───
print("\n" + "="*60)
print("7. __INIT_DATA_CALLBACK__ and page config")
print("="*60)

html_path = os.path.join(OUT, "diag_ws_page.html")
with open(html_path, "r", encoding="utf-8") as f:
    html = f.read()

# Find __INIT_DATA_CALLBACK__
cb_idx = html.find("__INIT_DATA_CALLBACK__")
if cb_idx > 0:
    ctx = html[cb_idx-100:cb_idx+500]
    print(f"__INIT_DATA_CALLBACK__ context: {ctx[:500]}")

# Find _page_config_
pc_idx = html.find("_page_config_")
if pc_idx > 0:
    ctx = html[pc_idx-50:pc_idx+500]
    print(f"\n_page_config_ context: {ctx[:400]}")

# ─── 8. Look for the actual cards data in a SEPARATE script or XHR ───
print("\n" + "="*60)
print("8. Separate data scripts")
print("="*60)

scripts = re.findall(r'<script[^>]*>(.*?)</script>', html, re.DOTALL)
for i, s in enumerate(scripts):
    if len(s) > 10000 and i != 11:  # Skip the big one
        # Check for product data
        has_products = any(m in s for m in ["itemId", "itemTitle", "finalPrice", "productsFeed"])
        if has_products:
            print(f"  Script [{i}] ({len(s)} chars) has product data!")
            print(f"    First 200: {s.strip()[:200]}")

# ─── 9. Check if there's a "data" key INSIDE the cards module that's a string ───
print("\n" + "="*60)
print("9. Cards module - string-encoded data?")
print("="*60)

if cards:
    # Maybe the data is JSON-encoded as a string
    for k, v in cards.items():
        if isinstance(v, str) and len(v) > 100:
            print(f"  cards.{k} (string): {v[:200]}")
            try:
                parsed = json.loads(v)
                print(f"    Parsed! type={type(parsed).__name__}")
                if isinstance(parsed, dict):
                    print(f"    Keys: {list(parsed.keys())[:15]}")
            except:
                pass

# ─── 10. Full module dump (sizes) ───
print("\n" + "="*60)
print("10. All modules sizes")
print("="*60)
for k, v in modules.items():
    sz = len(json.dumps(v, ensure_ascii=False)) if isinstance(v, (dict, list)) else len(str(v))
    print(f"  {k}: ~{sz} chars")

print("\nDIAG DEEP DONE")
