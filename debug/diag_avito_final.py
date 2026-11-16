"""
diag_avito_final.py — Parse avito_final_page.html: extract ad cards with prices, names, IDs.
Goal: understand the HTML structure so we can fix the collector.
"""
import re, json, sys
from pathlib import Path

DEBUG_DIR = Path(__file__).parent
html_path = DEBUG_DIR / "avito_final_page.html"

if not html_path.exists():
    print("ERROR: avito_final_page.html not found")
    sys.exit(1)

html = html_path.read_text(encoding="utf-8")
print(f"HTML: {len(html)} bytes, {html.count(chr(10))} lines")

# 1. Find all ad card containers
# AVITO uses data-listing-id, data-ad-id, or data-item-id
for attr in ['data-listing-id', 'data-ad-id', 'data-item-id', 'data-id']:
    ids = re.findall(rf'{attr}=["\'](\d+)["\']', html)
    if ids:
        print(f"\n=== {attr}: {len(ids)} unique={len(set(ids))} ===")
        for i in ids[:5]:
            print(f"  {i}")

# 2. Extract card blocks: find <a> or <div> with ad id, then look for price nearby
# Strategy: find each ad-id, then get surrounding 2000 chars
ad_id_pat = re.compile(r'data-(?:listing|ad|item)-id=["\'](\d+)["\']')
matches = list(ad_id_pat.finditer(html))
print(f"\n=== AD CARD BLOCKS: {len(matches)} ===")

cards = []
for m in matches[:30]:
    ad_id = m.group(1)
    # Get surrounding context (2000 chars after the id)
    start = max(0, m.start() - 200)
    end = min(len(html), m.end() + 2000)
    block = html[start:end]
    
    # Find price in this block
    price_pat = re.compile(r'(\d[\d\s\u00a0]{2,12})\s*(?:₽|руб\b)')
    prices = price_pat.findall(block)
    
    # Find title/name
    title_pat = re.compile(r'(?:aria-label|title|alt)=["\']([^"\']{10,120})["\']')
    titles = title_pat.findall(block)
    
    # Find href
    href_pat = re.compile(r'href=["\']([^"\']*)["\']')
    hrefs = href_pat.findall(block)
    
    card = {
        "id": ad_id,
        "prices": [p.strip() for p in prices[:3]],
        "titles": titles[:3],
        "hrefs": [h for h in hrefs if '/all/' in h or f'/{ad_id}' in h][:3],
    }
    cards.append(card)

print(f"Extracted {len(cards)} cards. First 5:")
for c in cards[:5]:
    print(f"  ID={c['id']}")
    print(f"    prices: {c['prices']}")
    print(f"    titles: {c['titles']}")
    print(f"    hrefs: {c['hrefs']}")

# 3. Look for structured JSON in __preloadedState__ of the FINAL page
ps = re.search(r'window\.__preloadedState__\s*=\s*"((?:[^"\\]|\\.)*)"', html, re.DOTALL)
if ps:
    raw_state = ps.group(1)
    print(f"\n=== __preloadedState__: {len(raw_state)} chars ===")
    try:
        state_str = json.loads(f'"{raw_state}"')
        state = json.loads(state_str)
        (DEBUG_DIR / "avito_final_state.json").write_text(
            json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"  Top keys: {list(state.keys())[:20]}")
        
        # Deep search for results/items/ads
        def find_ads_deep(obj, path="", depth=0):
            if depth > 10:
                return []
            hits = []
            if isinstance(obj, dict):
                for k, v in obj.items():
                    np = f"{path}.{k}" if path else k
                    if k.lower() in ('results', 'items', 'ads', 'listings', 'cards', 'offers', 'searchResults'):
                        if isinstance(v, list) and len(v) > 0:
                            hits.append((np, len(v), type(v[0]).__name__))
                    if isinstance(v, (dict, list)):
                        hits.extend(find_ads_deep(v, np, depth + 1))
            elif isinstance(obj, list):
                for i, item in enumerate(obj[:5]):
                    hits.extend(find_ads_deep(item, f"{path}[{i}]", depth + 1))
            return hits
        
        ad_hits = find_ads_deep(state)
        print(f"  Ad-list paths: {len(ad_hits)}")
        for h in ad_hits[:15]:
            print(f"    {h[0]}: {h[1]} items of {h[2]}")
        
        # Find prices in state
        def find_prices_deep(obj, path="", depth=0):
            if depth > 10:
                return []
            hits = []
            if isinstance(obj, dict):
                for k, v in obj.items():
                    np = f"{path}.{k}" if path else k
                    if k.lower() in ('price', 'amount', 'priceInfo', 'priceValue', 'prices'):
                        sv = json.dumps(v, ensure_ascii=False) if not isinstance(v, (int, float, str)) else str(v)
                        hits.append((np, sv[:100]))
                    if isinstance(v, (dict, list)):
                        hits.extend(find_prices_deep(v, np, depth + 1))
            elif isinstance(obj, list):
                for i, item in enumerate(obj[:3]):
                    hits.extend(find_prices_deep(item, f"{path}[{i}]", depth + 1))
            return hits
        
        price_hits = find_prices_deep(state)
        print(f"  Price fields: {len(price_hits)}")
        for ph in price_hits[:20]:
            print(f"    {ph[0]} = {ph[1]}")
    except (json.JSONDecodeError, TypeError) as e:
        print(f"  Parse error: {e}")

# 4. Check for specific AVITO card CSS classes
for cls in ['searchResult', 'catalog__item', 'listing', 'ad-card', 's-card', 'b-card']:
    found = re.findall(rf'class=["\'][^"\']*{cls}[^"\']*["\']', html, re.I)
    if found:
        print(f"\n=== Class '{cls}': {len(found)} ===")
        for f in found[:5]:
            print(f"  {f[:150]}")

# 5. Check for JSON-LD
jsonld = re.findall(r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', html, re.DOTALL)
print(f"\n=== JSON-LD: {len(jsonld)} ===")
for j in jsonld[:5]:
    try:
        d = json.loads(j.strip())
        print(f"  type={d.get('@type','?')}, keys={list(d.keys())[:8]}")
        if 'offers' in d:
            print(f"  offers: {json.dumps(d['offers'], ensure_ascii=False)[:200]}")
    except:
        print(f"  raw[:150]: {j.strip()[:150]}")

# 6. Check for data-props on search container
data_props = re.findall(r'data-(?:props|state|initial|serialized)=["\']([^"\']{100,})["\']', html)
print(f"\n=== DATA-PROPS: {len(data_props)} ===")
for dp in data_props[:3]:
    print(f"  len={len(dp)}: {dp[:200]}")

# 7. Save sample card HTML for reference
if matches:
    m = matches[0]
    start = max(0, m.start() - 500)
    end = min(len(html), m.end() + 3000)
    sample = html[start:end]
    (DEBUG_DIR / "avito_sample_card.html").write_text(sample, encoding="utf-8")
    print(f"\nSample card saved: {len(sample)} chars")

# 8. Summary
total_prices = len(re.findall(r'(\d[\d\s\u00a0]{2,12})\s*(?:₽|руб\b)', html))
print(f"\n=== SUMMARY ===")
print(f"  Total price matches: {total_prices}")
print(f"  Total ad IDs: {len(matches)}")
print(f"  Cards extracted: {len(cards)}")
print(f"  Cards with price: {sum(1 for c in cards if c['prices'])}")
print("=== DONE ===")
