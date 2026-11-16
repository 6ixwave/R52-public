"""Извлечение товаров из wholesale-страницы AliExpress.ru.
Источники: JSON-LD + _init_data_ в big script."""
import json, re, os, logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("diag_products")

BASE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(BASE, "data", "collector_output")
os.makedirs(OUT, exist_ok=True)

html_path = os.path.join(OUT, "diag_ws_page.html")
with open(html_path, "r", encoding="utf-8") as f:
    html = f.read()
print(f"HTML: {len(html)} bytes")

# ═══════════════════════════════════════════
# SOURCE 1: JSON-LD (ItemList)
# ═══════════════════════════════════════════
print("\n" + "="*60)
print("SOURCE 1: JSON-LD")
print("="*60)

jsonld_blocks = re.findall(r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>', html, re.DOTALL)
jsonld_products = []
for i, bl in enumerate(jsonld_blocks):
    raw = bl.strip()
    # Fix \u0022 → "
    fixed = raw.replace('\\u0022', '"')
    try:
        jld = json.loads(fixed)
        jtype = jld.get("@type", "")
        print(f"  Block [{i}]: type={jtype}")
        if jtype == "ItemList":
            items = jld.get("itemListElement", [])
            print(f"    Items: {len(items)}")
            for it in items:
                if isinstance(it, dict):
                    jsonld_products.append({
                        "position": it.get("position"),
                        "url": it.get("url", ""),
                        "name": it.get("name", ""),
                        "item": it.get("item", it),  # nested Item or ListItem
                    })
            # Save
            jld_path = os.path.join(OUT, "diag_ws_jsonld_fixed.json")
            with open(jld_path, "w", encoding="utf-8") as f:
                json.dump(jld, f, ensure_ascii=False, indent=2)
            print(f"    Saved → {jld_path}")
            # Show first 3
            for p in jsonld_products[:3]:
                print(f"    [{p['position']}] {p['name'][:60]} | {p['url'][:60]}")
    except json.JSONDecodeError as e:
        print(f"  Block [{i}] parse error: {e}")
        print(f"    First 300: {fixed[:300]}")

print(f"\nJSON-LD products: {len(jsonld_products)}")

# ═══════════════════════════════════════════
# SOURCE 2: _init_data_ in big script
# ═══════════════════════════════════════════
print("\n" + "="*60)
print("SOURCE 2: _init_data_ in big script")
print("="*60)

scripts = re.findall(r'<script[^>]*>(.*?)</script>', html, re.DOTALL)
big_script = None
for s in scripts:
    if len(s) > 200000:
        big_script = s
        break

if not big_script:
    print("ERROR: no big script")
    exit(1)

print(f"Big script: {len(big_script)} chars")

# Find _init_data_ assignment (separate from _dida_config_)
# Pattern: ._init_data_ = { or _init_data_ = {
init_assigns = list(re.finditer(r'\._init_data_\s*=\s*\{', big_script))
if not init_assigns:
    init_assigns = list(re.finditer(r'_init_data_\s*=\s*\{', big_script))
print(f"_init_data_ assignments: {len(init_assigns)}")

init_data = None
for m in init_assigns:
    start = m.end()  # position after {
    # Actually start is the { itself
    start = big_script.index('{', m.start())
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
    block = big_script[start:end]
    print(f"  Block at {m.start()}: {len(block)} chars")
    if len(block) > 1000:  # The real data block
        try:
            init_data = json.loads(block)
            print(f"  Parsed! Top keys: {list(init_data.keys())[:15]}")
            break
        except json.JSONDecodeError as e:
            print(f"  Parse error: {e}")
            print(f"  Start: {block[:200]}")
    else:
        print(f"  Too small, skipping")

if init_data:
    # Navigate structure
    d = init_data.get("data", init_data)
    if isinstance(d, dict):
        print(f"  data keys: {list(d.keys())[:20]}")
        
        # Find hierarchy
        hier = d.get("hierarchy", {})
        if hier:
            struct = hier.get("structure", {})
            print(f"  hierarchy.structure keys: {list(struct.keys())[:15]}")
        
        # Find modules
        modules = d.get("data", d)
        if isinstance(modules, dict):
            print(f"  modules keys: {list(modules.keys())[:25]}")
            
            # Find cards module
            cards_mod = None
            for k in modules:
                if "cards" in k.lower():
                    cards_mod = modules[k]
                    print(f"\n  Cards module: {k}")
                    print(f"  Cards type: {type(cards_mod).__name__}")
                    if isinstance(cards_mod, dict):
                        print(f"  Cards keys: {list(cards_mod.keys())[:20]}")
                        # Save
                        cards_path = os.path.join(OUT, "diag_ws_cards_mod.json")
                        with open(cards_path, "w", encoding="utf-8") as f:
                            json.dump(cards_mod, f, ensure_ascii=False, indent=2)
                        print(f"  Saved → {cards_path}")
                        
                        # Explore structure
                        for ck, cv in cards_mod.items():
                            sz = len(json.dumps(cv, ensure_ascii=False)) if isinstance(cv, (dict, list)) else len(str(cv))
                            if sz > 200:
                                print(f"    cards.{ck}: {type(cv).__name__}, ~{sz}")
                                # If it's a list, check first element
                                if isinstance(cv, list) and cv:
                                    print(f"      list len={len(cv)}, first keys: {list(cv[0].keys())[:10] if isinstance(cv[0], dict) else type(cv[0])}")
                    break
    else:
        print(f"  data is {type(d).__name__}")

# ═══════════════════════════════════════════
# SOURCE 3: Direct regex on big script
# ═══════════════════════════════════════════
print("\n" + "="*60)
print("SOURCE 3: Regex on big script")
print("="*60)

# The URLs were found as it.aliexpress.com/item/...
# Let's find the actual product objects
# Look for "itemCard" context
item_card_ctx = []
for m in re.finditer(r'"itemCard"', big_script):
    ctx = big_script[max(0, m.start()-50):m.start()+200]
    item_card_ctx.append(ctx)
print(f"itemCard occurrences: {len(item_card_ctx)}")
if item_card_ctx:
    print(f"  First: {item_card_ctx[0][:200]}")

# Find product URL pattern with context
prod_url_pat = re.findall(r'"(?:productUrl|itemUrl|url)"\s*:\s*"(https?://[^"]*aliexpress[^"]*/item/\d+[^"]*)"', big_script)
print(f"\nProduct URLs: {len(prod_url_pat)}")
if prod_url_pat:
    print(f"  First 5: {prod_url_pat[:5]}")

# Find nearby data: look for objects around product URLs
# Try to find the structure: each product might be in a specific format
# Let's look at a 500-char window around first product URL
if prod_url_pat:
    first_url_match = re.search(re.escape(prod_url_pat[0]), big_script)
    if first_url_match:
        window = big_script[max(0, first_url_match.start()-300):first_url_match.start()+500]
        print(f"\n  Window around first URL:")
        print(f"  {window[:800]}")

# ═══════════════════════════════════════════
# SOURCE 4: HTML data attributes / embedded JSON
# ═══════════════════════════════════════════
print("\n" + "="*60)
print("SOURCE 4: HTML embedded data")
print("="*60)

# Look for data-* attributes with product info
data_attrs = re.findall(r'data-(?:product|item|card)[^=]*="([^"]{20,500})"', html)
print(f"data-* attributes: {len(data_attrs)}")
if data_attrs:
    print(f"  First: {data_attrs[0][:200]}")

# Look for window.__ data
window_data = re.findall(r'window\.(__\w+__|_\w+_\w+)\s*=\s*', html)
print(f"window.__* assignments: {set(window_data)}")

# ═══════════════════════════════════════════
# COMBINE & OUTPUT
# ═══════════════════════════════════════════
print("\n" + "="*60)
print("COMBINED PRODUCTS")
print("="*60)

# Start with JSON-LD products
products = []
for p in jsonld_products:
    url = p.get("url", "")
    # Extract item ID from URL
    id_m = re.search(r'/item/(\d+)', url)
    item_id = id_m.group(1) if id_m else None
    products.append({
        "item_id": item_id,
        "title": p.get("name", ""),
        "url": url,
        "price": None,
        "currency": "RUB",
        "images": [],
        "seller": None,
        "source": "jsonld",
    })

# Enrich with big script data (if we have cards module)
# For now, also extract prices from HTML near product URLs
# Look for price patterns: "1 234 ₽" or data-price attributes
price_patterns = re.findall(r'>([\d\s\u00a0\u202f]{3,15}(?:\.\d{1,2})?)\s*₽<', html)
print(f"Price patterns in HTML: {len(price_patterns)}")
if price_patterns:
    print(f"  First 10: {price_patterns[:10]}")

# Save combined
combined_path = os.path.join(OUT, "diag_ws_products.json")
with open(combined_path, "w", encoding="utf-8") as f:
    json.dump(products, f, ensure_ascii=False, indent=2)
print(f"\nSaved {len(products)} products → {combined_path}")

# Show sample
for p in products[:5]:
    print(f"  [{p['item_id']}] {p['title'][:50]} | {p['url'][:50]}")

print("\nDIAG PRODUCTS DONE")
