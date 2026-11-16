"""
diag_avito_cards.py — Extract full card blocks from avito_final_page.html.
Find where price, title, link, image are within each card.
"""
import re, json, sys
from pathlib import Path

DEBUG_DIR = Path(__file__).parent
html = (DEBUG_DIR / "avito_final_page.html").read_text(encoding="utf-8")
print(f"HTML: {len(html)} bytes")

# Find all card start positions
card_pat = re.compile(r'data-item-id=["\'](\d+)["\']')
matches = list(card_pat.finditer(html))
print(f"Cards: {len(matches)}")

# Extract full card blocks (from data-item-id to next data-item-id or end of container)
cards = []
for i, m in enumerate(matches):
    start = m.start()
    if i + 1 < len(matches):
        end = matches[i + 1].start()
    else:
        end = min(len(html), start + 8000)
    block = html[start:end]
    cards.append({"id": m.group(1), "block": block, "len": len(block)})

# Analyze first 3 cards in detail
for ci, card in enumerate(cards[:3]):
    print(f"\n{'='*60}")
    print(f"CARD {ci}: id={card['id']}, block_len={card['len']}")
    block = card['block']
    
    # Find price in block
    price_pat = re.compile(r'(\d[\d\s\u00a0]{2,12})\s*(?:₽|руб\b)')
    prices = [(m.start(), m.group(1)) for m in price_pat.finditer(block)]
    print(f"  Prices: {len(prices)}")
    for pos, p in prices[:5]:
        context = block[max(0,pos-80):pos+len(p)+20]
        print(f"    pos={pos}: '{p}' ctx=...{context[-100:]}...")
    
    # Find itemProp="name" or title
    name_pat = re.compile(r'itemProp=["\']name["\'][^>]*>([^<]+)<')
    names = name_pat.findall(block)
    if not names:
        name_pat2 = re.compile(r'<(?:h[1-6]|span|a)[^>]*itemProp=["\']name["\'][^>]*>([^<]+)<')
        names = name_pat2.findall(block)
    print(f"  Names: {names[:3]}")
    
    # Find href with ad id
    href_pat = re.compile(r'href=["\']([^"\'<>]*' + card['id'] + r'[^"\'<>]*)["\']')
    hrefs = href_pat.findall(block)
    print(f"  Hrefs (with id): {hrefs[:2]}")
    
    # Find image
    img_pat = re.compile(r'<img[^>]*itemProp=["\']image["\'][^>]*src=["\']([^"\']+)["\']')
    imgs = img_pat.findall(block)
    if not imgs:
        img_pat2 = re.compile(r'<img[^>]*src=["\']([^"\']*avito[^"\']+)["\']')
        imgs = img_pat2.findall(block)
    print(f"  Images: {len(imgs)}")
    if imgs:
        print(f"    First: {imgs[0][:120]}")
    
    # Find data-marker="item-price" or similar
    price_markers = re.findall(r'data-marker=["\']([^"\']*price[^"\']*)["\']', block, re.I)
    print(f"  Price markers: {price_markers[:5]}")
    
    # Find itemProp="price" or "offers"
    offers_pat = re.compile(r'itemProp=["\'](?:price|offers|priceCurrency|priceValue)["\'][^>]*(?:content=["\']([^"\']*)["\']|>([^<]+)<)')
    offers = offers_pat.findall(block)
    print(f"  itemProp price/offers: {offers[:5]}")
    
    # Find schema.org structured data
    schema_pat = re.compile(r'itemProp=["\'](\w+)["\']')
    schema_props = schema_pat.findall(block)
    print(f"  schema props: {schema_props[:15]}")
    
    # Save first card full block for reference
    if ci == 0:
        (DEBUG_DIR / "avito_full_card0.html").write_text(block, encoding="utf-8")
        print(f"  Saved full card0: {len(block)} chars")

# Summary: how many cards have prices?
cards_with_price = 0
for card in cards:
    if re.search(r'\d[\d\s\u00a0]{2,12}\s*(?:₽|руб\b)', card['block']):
        cards_with_price += 1
print(f"\n=== SUMMARY ===")
print(f"Total cards: {len(cards)}")
print(f"Cards with price in block: {cards_with_price}")
print(f"Avg block size: {sum(c['len'] for c in cards)//len(cards)} chars")
print("=== DONE ===")
