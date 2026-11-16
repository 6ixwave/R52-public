"""V2: Parse _init_data_ (JS notation) → cards module → full products.
Fix: quote unquoted keys, navigate hierarchy, extract card data."""
import json, re, os, logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("diag_cards2")

BASE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(BASE, "data", "collector_output")
os.makedirs(OUT, exist_ok=True)

html_path = os.path.join(OUT, "diag_ws_page.html")
with open(html_path, "r", encoding="utf-8") as f:
    html = f.read()
print(f"HTML: {len(html)} bytes")

# ─── Extract big script ───
scripts = re.findall(r'<script[^>]*>(.*?)</script>', html, re.DOTALL)
big_script = None
for s in scripts:
    if len(s) > 200000:
        big_script = s
        break
print(f"Big script: {len(big_script)} chars")

# ─── Extract _init_data_ block ───
# Pattern: ._init_data_ = { ... }
m = re.search(r'\._init_data_\s*=\s*\{', big_script)
if not m:
    m = re.search(r'_init_data_\s*=\s*\{', big_script)
print(f"_init_data_ at: {m.start() if m else 'NOT FOUND'}")

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
print(f"Block: {len(block)} chars")
print(f"Start: {block[:200]}")
print(f"End: {block[-100:]}")

# ─── Convert JS object notation to valid JSON ───
# Main issue: unquoted keys like `data:`, `hierarchy:`, etc.
# Strategy: quote keys that are bare identifiers (not already quoted)
# Pattern: (?:^|[,{\[])\s*([a-zA-Z_$][a-zA-Z0-9_$]*)\s*:
def js_to_json(text):
    """Convert JS object literal to valid JSON by quoting unquoted keys."""
    # Step 1: Quote unquoted property keys
    # Match: after { or , or [ then whitespace then identifier then :
    # But NOT after " or ' (already quoted)
    # Use a state machine approach for safety
    
    result = []
    i = 0
    n = len(text)
    in_string = False
    string_char = ''
    
    while i < n:
        c = text[i]
        
        if in_string:
            result.append(c)
            if c == '\\' and i + 1 < n:
                result.append(text[i+1])
                i += 2
                continue
            if c == string_char:
                in_string = False
            i += 1
            continue
        
        if c in ('"', "'"):
            in_string = True
            string_char = c
            result.append(c)
            i += 1
            continue
        
        # Check if this is a key position: after { or , (at object level)
        # Simple heuristic: if we see identifier followed by :
        if c.isalpha() or c == '_' or c == '$':
            # Read the full identifier
            j = i
            while j < n and (text[j].isalnum() or text[j] in '_$'):
                j += 1
            ident = text[i:j]
            # Check if followed by : (with optional whitespace)
            k = j
            while k < n and text[k] in ' \t\n\r':
                k += 1
            if k < n and text[k] == ':':
                # It's a key - quote it
                result.append(f'"{ident}"')
                i = j
                continue
            else:
                result.append(c)
                i += 1
                continue
        
        result.append(c)
        i += 1
    
    return ''.join(result)

json_text = js_to_json(block)
print(f"Converted: {len(json_text)} chars")

# Save converted
conv_path = os.path.join(OUT, "diag_ws_init_data_conv.json")
with open(conv_path, "w", encoding="utf-8") as f:
    f.write(json_text)
print(f"Saved → {conv_path}")

# Try to parse
try:
    data = json.loads(json_text)
    print("✓ JSON parsed successfully!")
    print(f"Top keys: {list(data.keys())[:15]}")
except json.JSONDecodeError as e:
    print(f"✗ JSON parse error: {e}")
    # Show context around error
    pos = e.pos if hasattr(e, 'pos') else 0
    print(f"Context: ...{json_text[max(0,pos-50):pos+50]}...")
    # Try alternative: use a simpler regex fix
    # Just quote the first-level unquoted keys
    alt = re.sub(r'(?<=[{,])\s*([a-zA-Z_$][a-zA-Z0-9_$]*)\s*:', r' "\1":', json_text[:5000])
    print(f"Alt fix (first 5000): {alt[:300]}")
    data = None

if data:
    # ─── Navigate structure ───
    # From diag_v5: { data: { hierarchy: {...}, data: { root: {...}, cards2023_3722: {...} } } }
    d = data.get("data", data)
    print(f"\ndata keys: {list(d.keys())[:20]}")
    
    # Check hierarchy
    hier = d.get("hierarchy", {})
    if hier:
        struct = hier.get("structure", {})
        print(f"hierarchy.structure: {list(struct.keys())[:15]}")
    
    # The modules are in d["data"] or directly in d
    modules = d.get("data", d)
    if isinstance(modules, dict):
        print(f"\nModules: {list(modules.keys())[:25]}")
        
        # Find cards module
        cards_mod = None
        for k in modules:
            if "cards" in k.lower():
                cards_mod = modules[k]
                cards_key = k
                break
        
        if cards_mod:
            print(f"\n✓ Cards module: {cards_key}")
            print(f"  Type: {type(cards_mod).__name__}")
            if isinstance(cards_mod, dict):
                print(f"  Keys: {list(cards_mod.keys())[:20]}")
                
                # Save cards module
                cards_path = os.path.join(OUT, "diag_ws_cards_full.json")
                with open(cards_path, "w", encoding="utf-8") as f:
                    json.dump(cards_mod, f, ensure_ascii=False, indent=2)
                print(f"  Saved → {cards_path}")
                
                # Explore sub-structure
                for ck, cv in cards_mod.items():
                    if isinstance(cv, (dict, list)):
                        sz = len(json.dumps(cv, ensure_ascii=False))
                        print(f"    {ck}: {type(cv).__name__}, ~{sz} chars")
                        if isinstance(cv, list) and cv:
                            first = cv[0]
                            if isinstance(first, dict):
                                print(f"      [0] keys: {list(first.keys())[:15]}")
                        if isinstance(cv, dict):
                            print(f"      keys: {list(cv.keys())[:15]}")
        else:
            print("  No cards module found!")
            # List all modules with sizes
            for k, v in modules.items():
                sz = len(json.dumps(v, ensure_ascii=False)) if isinstance(v, (dict, list)) else len(str(v))
                if sz > 500:
                    print(f"    {k}: ~{sz}")
    else:
        print(f"modules is {type(modules).__name__}")

# ─── Also: extract product data via targeted search ───
print("\n" + "="*60)
print("TARGETED PRODUCT EXTRACTION")
print("="*60)

# In the big script, look for product card structures
# Each card likely has: itemId, title, price, image, url, seller
# Let's find clusters

# Method A: Find "itemCard" or card-like objects
# From earlier: 59 itemCard occurrences in HTML, but 0 in big_script
# Let's check the HTML directly for card data

# Find product data in HTML (not in script)
# Look for data in the rendered HTML body
body_start = html.find('<body')
if body_start > 0:
    body = html[body_start:]
    # Find product cards in HTML
    # Common patterns: <a href="...item/ID.html"> with nearby price/title
    card_links = re.findall(r'href="(https?://[^"]*aliexpress[^"]*/item/(\d+)\.html[^"]*)"', body)
    print(f"Product links in body: {len(card_links)}")
    if card_links:
        print(f"  First: {card_links[0][0][:80]}")

# Method B: Look for specific data patterns in big script
# Find "cardData" or "productData" or similar
for pattern in ["cardData", "productData", "itemData", "searchResult", "productList", "cardList"]:
    idx = big_script.find(pattern)
    if idx >= 0:
        print(f"  '{pattern}' at {idx}: {big_script[idx:idx+100]}")

# Method C: The cards module structure
# From diag_v5_data.js, the structure was:
# data.data.cards2023_3722.fields.mods... or data.data.cards2023_3722.data...
# Let's look at what's inside

if data:
    # Try to find the actual product list
    # Navigate: data → data → cards2023_3722 → ?
    d = data.get("data", data)
    modules = d.get("data", d)
    
    # Get cards module
    cards = None
    for k in modules:
        if "cards" in k.lower():
            cards = modules[k]
            break
    
    if cards and isinstance(cards, dict):
        # Try common paths
        paths_to_try = [
            ("fields.mods", lambda c: c.get("fields", {}).get("mods", {})),
            ("data", lambda c: c.get("data", {})),
            ("fields", lambda c: c.get("fields", {})),
            ("mods", lambda c: c.get("mods", {})),
        ]
        
        for name, getter in paths_to_try:
            try:
                section = getter(cards)
                if section and isinstance(section, dict):
                    print(f"\n  cards.{name}: keys={list(section.keys())[:15]}")
                    # Look for product list
                    for sk, sv in section.items():
                        if isinstance(sv, list) and len(sv) > 5:
                            print(f"    {sk}: list[{len(sv)}]")
                            if isinstance(sv[0], dict):
                                print(f"      [0] keys: {list(sv[0].keys())[:15]}")
                                # This might be our product list!
                                prod_list_path = os.path.join(OUT, f"diag_ws_{name}_{sk}.json")
                                with open(prod_list_path, "w", encoding="utf-8") as f:
                                    json.dump(sv[:5], f, ensure_ascii=False, indent=2)
                                print(f"      Saved first 5 → {prod_list_path}")
            except Exception as e:
                pass

# ─── Method D: Brute force - find all objects with both "title" and "price" ───
print("\n" + "="*60)
print("BRUTE FORCE: Objects with title+price")
print("="*60)

# In the converted JSON text, find patterns
# Look for "title":"..." near "price":"..." or "finalPrice":"..."
# Use a sliding window approach

# Find all "title" values
title_matches = list(re.finditer(r'"title"\s*:\s*"([^"]{10,300})"', json_text))
print(f"Title fields: {len(title_matches)}")
if title_matches:
    print(f"  First 5: {[m.group(1)[:50] for m in title_matches[:5]]}")

# Find all price-like values
price_matches = list(re.finditer(r'"(?:finalPrice|price|salePrice|displayPrice)"\s*:\s*"?([\d.]+)"?', json_text))
print(f"Price fields: {len(price_matches)}")
if price_matches:
    print(f"  First 10: {[m.group(1) for m in price_matches[:10]]}")

# Find itemId
id_matches = list(re.finditer(r'"itemId"\s*:\s*"?(\d+)"?', json_text))
print(f"itemId fields: {len(id_matches)}")
if id_matches:
    print(f"  First 10: {[m.group(1) for m in id_matches[:10]]}")

# Find image URLs (alicdn)
img_matches = re.findall(r'"(?:imageUrl|image|img|thumbUrl)"\s*:\s*"(https?://[^"]*(?:alicdn|aliexpress-media)[^"]*)"', json_text)
print(f"Image fields: {len(img_matches)}")
if img_matches:
    print(f"  First 3: {[i[:70] for i in img_matches[:3]]}")

# ─── Save everything for next step ───
summary = {
    "html_size": len(html),
    "big_script_size": len(big_script),
    "init_data_size": len(block),
    "json_converted_size": len(json_text),
    "json_parsed": data is not None,
    "titles_found": len(title_matches),
    "prices_found": len(price_matches),
    "item_ids_found": len(id_matches),
    "images_found": len(img_matches),
}
print(f"\nSUMMARY: {json.dumps(summary, indent=2)}")
print("\nDIAG CARDS2 DONE")
