"""
test_avito_parser.py — Validate new AVITO parser logic against avito_final_page.html.
Simulates collect_by_search parsing without network.
"""
import re, sys, os
from pathlib import Path

DEBUG_DIR = Path(__file__).parent
html = (DEBUG_DIR / "avito_final_page.html").read_text(encoding="utf-8")
print(f"HTML: {len(html)} bytes")

# New card detection
card_pat = re.compile(r'data-item-id=["\'](\d{6,})["\']', re.I)
matches = list(card_pat.finditer(html))
print(f"Cards found: {len(matches)}")

page_size = 20
results = []
seen_ids = set()

for i, m in enumerate(matches[:page_size * 3]):
    aid = m.group(1)
    if aid in seen_ids:
        continue
    seen_ids.add(aid)
    if len(results) >= page_size:
        break

    block_start = m.start()
    block_end = matches[i + 1].start() if i + 1 < len(matches) else min(block_start + 4000, len(html))
    block = html[block_start:block_end]

    # Title
    title = None
    t_match = re.search(r'itemProp=["\']name["\'][^>]*>([^<]{5,300})<', block)
    if t_match:
        title = re.sub(r'<[^>]+>', '', t_match.group(1)).strip()
    if not title:
        t_match = re.search(r'<h2[^>]*itemProp=["\']name["\'][^>]*>(.*?)</h2>', block, re.DOTALL)
        if t_match:
            title = re.sub(r'<[^>]+>', '', t_match.group(1)).strip()
    if not title:
        t_match = re.search(r'aria-label=["\']([^"\']{10,200})["\']', block)
        if t_match:
            title = t_match.group(1).strip()
    if not title:
        t_match = re.search(r'>([^<>]{15,200})</(?:h2|h3|span|a)>', block)
        if t_match:
            candidate = t_match.group(1).strip()
            if len(candidate) > 10 and not candidate.startswith(('http', '{')):
                title = candidate
    if not title:
        title = f"AVITO-{aid}"

    # Price
    price_minor = 0
    p_match = re.search(r'itemProp=["\']price["\'][^>]*content=["\'](\d+(?:\.\d+)?)["\']', block)
    if p_match:
        try:
            price_minor = int(float(p_match.group(1)) * 100)
        except ValueError:
            pass
    if not price_minor:
        p_match = re.search(r'data-marker=["\']item-price-value["\'][^>]*>([^<]+)<', block)
        if p_match:
            ptext = p_match.group(1).replace(' ', '').replace('\u00a0', '').replace('₽', '').replace('руб', '').strip()
            try:
                price_minor = int(float(ptext) * 100)
            except ValueError:
                pass
    if not price_minor:
        p_match = re.search(r'(\d{2,9}(?:[\s\u00a0]\d{3})*)(?:\s*[.,]\d{1,2})?\s*(?:₽|руб)', block)
        if p_match:
            price_str = p_match.group(1).replace(' ', '').replace('\u00a0', '')
            try:
                price_minor = int(float(price_str) * 100)
            except ValueError:
                pass
    if not price_minor:
        p_match = re.search(r'data-price=["\']?(\d{3,9})', block)
        if p_match:
            price_minor = int(p_match.group(1))

    # Image: 2026-11-15 — expanded (src, data-src, data-lazy-src, data-original, srcset)
    img_url = None
    img_match = re.search(r'(?:src|data-src|data-lazy-src|data-original)=["\']([^"\']*(?:avito\.st|avito\.ru)/[^"\']+)["\']', block)
    if img_match:
        img_url = img_match.group(1)
    if not img_url:
        img_match = re.search(r'srcset=["\'][^"\']*(?:avito\.st|avito\.ru)/[^"\',\s]+', block)
        if img_match:
            img_match2 = re.search(r'((?:https?:)?//[^"\',\s]+)', img_match.group(0))
            if img_match2:
                img_url = img_match2.group(1)
    if not img_url:
        img_match = re.search(r'data-marker=["\']slider-image/image-((?:https?:)?//[^"\']*avito\.st/[^"\']+)["\']', block)
        if img_match:
            img_url = img_match.group(1)
    if not img_url:
        img_match = re.search(r'style=["\'][^"\']*url\(((?:https?:)?//[^)"]*avito\.st/[^)"]+)\)', block)
        if img_match:
            img_url = img_match.group(1)

    price_str = f"{price_minor // 100}.{price_minor % 100:02d}" if price_minor > 0 else "NO_PRICE"
    results.append({"id": aid, "title": title[:60], "price": price_str, "img": bool(img_url)})

print(f"\nParsed {len(results)} cards:")
has_price = 0
has_title = 0
has_img = 0
for r in results:
    if r["price"] != "NO_PRICE":
        has_price += 1
    if not r["title"].startswith("AVITO-"):
        has_title += 1
    if r["img"]:
        has_img += 1
    print(f"  [{r['id']}] {r['price']:>12} ₽ | {r['title'][:50]} | img={'Y' if r['img'] else 'N'}")

print(f"\n=== SUMMARY ===")
print(f"  Total: {len(results)}")
print(f"  With price: {has_price}/{len(results)}")
print(f"  With title: {has_title}/{len(results)}")
print(f"  With image: {has_img}/{len(results)}")
if has_price == len(results):
    print("  ✓ ALL CARDS HAVE PRICES")
else:
    print(f"  ✗ {len(results)-has_price} cards missing price")
print("=== DONE ===")
