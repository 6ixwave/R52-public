"""Извлечение товарных карточек из wholesale-страницы AliExpress.ru.
Цель: найти и распарсить 59 товаров из script[11] + JSON-LD."""
import json, re, os, time, logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("diag_cards")

BASE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(BASE, "data", "collector_output")
os.makedirs(OUT, exist_ok=True)

# ─── Load saved wholesale HTML ───
html_path = os.path.join(OUT, "diag_ws_page.html")
with open(html_path, "r", encoding="utf-8") as f:
    html = f.read()
print(f"HTML loaded: {len(html)} bytes")

# ─── Extract scripts ───
scripts = re.findall(r'<script[^>]*>(.*?)</script>', html, re.DOTALL)
print(f"Total scripts: {len(scripts)}")

# Find the big data script (script[11] = 452K)
big_script = None
big_idx = -1
for i, s in enumerate(scripts):
    if len(s) > 200000:
        big_script = s
        big_idx = i
        print(f"Big script [{i}]: {len(s)} chars")
        break

if not big_script:
    print("ERROR: No big script found!")
    exit(1)

# Save big script for inspection
big_path = os.path.join(OUT, "diag_ws_big_script.js")
with open(big_path, "w", encoding="utf-8") as f:
    f.write(big_script)
print(f"Saved big script → {big_path}")

# ─── Parse _dida_config_ from big script ───
# Pattern: window._dida_config_ = {...}
# The data might be: window._dida_config_ = {"pageVersion":"...","pageName":"search-pc","_init_data_":{...}}
# Or: window._dida_config_ = {...}; window._dida_config_._init_data_ = {...}

# Strategy: find all top-level assignments
# First, try to find the main JSON object
# Look for patterns like: = { or ._init_data_ = {

# Method 1: Find "window._dida_config_" and extract the full object
dida_match = re.search(r'window\._dida_config_\s*=\s*', big_script)
if dida_match:
    start = dida_match.end()
    # Brace matching from start
    depth = 0
    end = start
    for i in range(start, min(start + len(big_script), len(big_script))):
        c = big_script[i]
        if c == '{':
            depth += 1
        elif c == '}':
            depth -= 1
            if depth == 0:
                end = i + 1
                break
    dida_block = big_script[start:end]
    print(f"_dida_config_ block: {len(dida_block)} chars")
    
    # Try to parse as JSON (might need to handle JS-specific stuff)
    try:
        dida = json.loads(dida_block)
        print(f"_dida_config_ parsed! Keys: {list(dida.keys())[:20]}")
        
        # Check for _init_data_
        init_data = dida.get("_init_data_")
        if init_data:
            print(f"_init_data_ found! Keys: {list(init_data.keys())[:20]}")
        else:
            print(f"No _init_data_ in dida. Looking deeper...")
            for k, v in dida.items():
                sz = len(json.dumps(v, ensure_ascii=False)) if isinstance(v, (dict, list)) else len(str(v))
                print(f"  {k}: {type(v).__name__}, ~{sz} chars")
    except json.JSONDecodeError as e:
        print(f"_dida_config_ JSON error: {e}")
        print(f"Block start (200): {dida_block[:200]}")
        print(f"Block end (200): {dida_block[-200:]}")
else:
    print("No window._dida_config_ found in big script")
    # Maybe it's set differently
    for pattern in [r'_dida_config_', r'_init_data_', r'pageData', r'searchData']:
        idx = big_script.find(pattern)
        if idx >= 0:
            print(f"  '{pattern}' at {idx}: {big_script[idx:idx+100]}")

# ─── Method 2: Direct search for product data in big script ───
print("\n" + "=" * 60)
print("METHOD 2: Direct product markers in big script")
print("=" * 60)

# Find all itemId occurrences with context
item_ids = re.findall(r'"itemId"\s*:\s*"?(\d+)"?', big_script)
print(f"itemId values: {len(item_ids)} unique: {len(set(item_ids))}")
if item_ids:
    print(f"First 10: {item_ids[:10]}")

# Find itemTitle
titles = re.findall(r'"itemTitle"\s*:\s*"([^"]{10,200})"', big_script)
print(f"itemTitle values: {len(titles)}")
if titles:
    print(f"First 3: {titles[:3]}")

# Find price patterns
prices = re.findall(r'"(?:finalPrice|price|salePrice)"\s*:\s*"?([\d.]+)"?', big_script)
print(f"Price values: {len(prices)}")
if prices:
    print(f"First 10: {prices[:10]}")

# Find image URLs
images = re.findall(r'"(?:imageUrl|image|url)"\s*:\s*"(https?://[^"]*(?:alicdn|aliexpress-media)[^"]*)"', big_script)
print(f"Image URLs: {len(images)}")
if images:
    print(f"First 3: {[i[:80] for i in images[:3]]}")

# Find product URLs
prod_urls = re.findall(r'"(?:productUrl|url|itemUrl)"\s*:\s*"(https?://[^"]*aliexpress[^"]*item[^"]*)"', big_script)
print(f"Product URLs: {len(prod_urls)}")
if prod_urls:
    print(f"First 3: {[u[:80] for u in prod_urls[:3]]}")

# ─── Method 3: JSON-LD (script[23]) ───
print("\n" + "=" * 60)
print("METHOD 3: JSON-LD")
print("=" * 60)

jsonld_blocks = re.findall(r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>', html, re.DOTALL)
print(f"JSON-LD blocks: {len(jsonld_blocks)}")
for i, bl in enumerate(jsonld_blocks):
    try:
        jld = json.loads(bl)
        jtype = jld.get("@type", "unknown")
        print(f"  [{i}] type={jtype}")
        if jtype == "ItemList":
            items = jld.get("itemListElement", [])
            print(f"    Items: {len(items)}")
            if items:
                print(f"    First item keys: {list(items[0].keys()) if isinstance(items[0], dict) else type(items[0])}")
                # Save
                jld_path = os.path.join(OUT, "diag_ws_jsonld.json")
                with open(jld_path, "w", encoding="utf-8") as f:
                    json.dump(jld, f, ensure_ascii=False, indent=2)
                print(f"    Saved → {jld_path}")
    except json.JSONDecodeError as e:
        print(f"  [{i}] parse error: {e}")
        print(f"    First 200: {bl.strip()[:200]}")

# ─── Method 4: Find the cards module in _init_data_ ───
print("\n" + "=" * 60)
print("METHOD 4: Navigate to cards module")
print("=" * 60)

# The structure from diag_v5: data.data.{moduleId}
# cards2023_3722 is the cards module
# Let's search for it in the big script

# Find "cards2023" or similar
cards_patterns = [r'"cards\d*_\d+"', r'"cards2023_\d+"']
for pat in cards_patterns:
    cards_keys = re.findall(pat, big_script)
    if cards_keys:
        print(f"Cards keys found: {set(cards_keys)}")
        break

# If we have the dida object parsed, navigate
# Otherwise, try to find the data section
# The big script likely has: window._dida_config_ = {pageVersion, pageName, _init_data_: {data: {hierarchy, data: {root, main, rightContent, cards...}}}}

# Try: find the "data" object inside _init_data_
# Look for pattern: "data":{"hierarchy"
data_section_idx = big_script.find('"data":{"hierarchy"')
if data_section_idx < 0:
    data_section_idx = big_script.find('"data": {"hierarchy"')
if data_section_idx >= 0:
    print(f"Data section at offset {data_section_idx}")
    # Extract from there
    # Find the start of the data object
    data_start = big_script.find("{", data_section_idx + 6)  # after "data":
    depth = 0
    data_end = data_start
    for i in range(data_start, min(data_start + 500000, len(big_script))):
        c = big_script[i]
        if c == '{':
            depth += 1
        elif c == '}':
            depth -= 1
            if depth == 0:
                data_end = i + 1
                break
    data_block = big_script[data_start:data_end]
    print(f"Data block: {len(data_block)} chars")
    
    # Save
    data_path = os.path.join(OUT, "diag_ws_data_section.json")
    with open(data_path, "w", encoding="utf-8") as f:
        f.write(data_block)
    print(f"Saved → {data_path}")
    
    # Try to parse
    try:
        data_obj = json.loads(data_block)
        print(f"Parsed! Keys: {list(data_obj.keys())[:15]}")
        
        # Navigate to data.data (the module container)
        modules = data_obj.get("data", {})
        if isinstance(modules, dict):
            print(f"Module keys: {list(modules.keys())[:20]}")
            
            # Find cards module
            cards_mod = None
            for k in modules:
                if "cards" in k.lower():
                    cards_mod = modules[k]
                    print(f"Cards module: {k}, type={type(cards_mod).__name__}")
                    break
            
            if cards_mod and isinstance(cards_mod, dict):
                print(f"Cards keys: {list(cards_mod.keys())[:20]}")
                # Save cards
                cards_path = os.path.join(OUT, "diag_ws_cards_module.json")
                with open(cards_path, "w", encoding="utf-8") as f:
                    json.dump(cards_mod, f, ensure_ascii=False, indent=2)
                print(f"Saved → {cards_path}")
                
                # Look for product list inside
                # Could be: cards_mod.data.list or cards_mod.fields.cards etc.
                for k, v in cards_mod.items():
                    sz = len(json.dumps(v, ensure_ascii=False)) if isinstance(v, (dict, list)) else len(str(v))
                    if sz > 100:
                        print(f"  cards.{k}: {type(v).__name__}, ~{sz} chars")
                        
    except json.JSONDecodeError as e:
        print(f"Data section parse error: {e}")
        print(f"Start: {data_block[:300]}")

# ─── Method 5: Regex-based product extraction (fallback) ───
print("\n" + "=" * 60)
print("METHOD 5: Regex product extraction")
print("=" * 60)

# If the structure is complex, try to extract individual product objects
# Each product card likely has: itemId, title, price, image, url

# Find product-like objects: look for clusters of itemId + title + price
# Pattern: objects containing "itemId" and nearby "title" and "price"

# Try: find all objects that have itemId
# In JSON, each product might be a dict in an array
# Let's find arrays of objects with itemId

# First, try to find the product array
# Common patterns: "list":[{...},{...}] or "cards":[{...}] or "items":[{...}]
prod_array_patterns = [
    r'"(?:list|cards|items|products|cardList)"\s*:\s*\[',
]

for pat in prod_array_patterns:
    matches = list(re.finditer(pat, big_script))
    if matches:
        print(f"Pattern '{pat}': {len(matches)} matches")
        for m in matches[:3]:
            print(f"  at {m.start()}: {big_script[m.start():m.start()+100]}")

# ─── Summary ───
print("\n" + "=" * 60)
print("SUMMARY")
print("=" * 60)
print(f"HTML size: {len(html)}")
print(f"Big script: {len(big_script)} chars")
print(f"itemIds: {len(set(item_ids))}")
print(f"Titles: {len(titles)}")
print(f"Prices: {len(prices)}")
print(f"Images: {len(images)}")
print(f"JSON-LD blocks: {len(jsonld_blocks)}")
print(f"DIAG CARDS DONE")
