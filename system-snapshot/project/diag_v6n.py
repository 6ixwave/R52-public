"""
diag_v6n.py — Точечная диагностика: какой шаг price-extraction срабатывает.
Запуск: python diag_v6n.py
"""
import sys, os, time, re, logging

os.environ.setdefault("R52_HOME", os.path.join(os.environ.get("LOCALAPPDATA", "C:\\Temp"), "R52"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("diag_v6n")

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "diag_v6n_output.txt")
lines = []

def emit(s=""):
    print(s)
    lines.append(s)

def flush():
    with open(OUT, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")

QUERY = "наушники"

emit(f"=== diag_v6n — {time.strftime('%Y-%m-%d %H:%M:%S')} ===")
emit()

try:
    from r52.collectors.aliexpress import AliExpressRUCollector
    a = AliExpressRUCollector(use_proxies=False)
    a.source = "ALIEXPRESS_RU"

    # Fetch raw HTML
    html = a._fetch_wholesale_html(QUERY, 1)
    if not html:
        emit("FATAL: no HTML")
        flush()
        sys.exit(1)
    emit(f"HTML: {len(html)} bytes")

    # Parse with internal method
    products = a._parse_wholesale_html(html)
    emit(f"Parsed: {len(products)} products")

    # For first 3 products, show what the parser extracted
    for i in range(min(3, len(products))):
        p = products[i]
        emit(f"\n--- Product[{i}] ---")
        emit(f"  item_id: {p.get('item_id')}")
        emit(f"  title: {str(p.get('title',''))[:60]!r}")
        emit(f"  price: {p.get('price')}")
        emit(f"  original_price: {p.get('original_price')}")
        emit(f"  currency: {p.get('currency')}")
        emit(f"  images: {len(p.get('images',[]))}")

    # Now manually trace the price extraction on card[0]
    emit(f"\n{'='*60}")
    emit("MANUAL TRACE: card[0] price extraction steps")
    emit(f"{'='*60}")

    import html as html_mod
    from urllib.parse import unquote, quote

    card_list_idx = html.find('id="card-list"')
    window_end = min(card_list_idx + 700000, len(html))
    card_area = html[card_list_idx:window_end]

    anchor_pat = re.compile(r'<a[^>]*class="[^"]*search-card-item[^"]*"[^>]*href="([^"]+)"[^>]*>')
    matches = list(anchor_pat.finditer(card_area))
    if not matches:
        anchor_pat = re.compile(r'<a[^>]*href="([^"]*/item/\d+\.html[^"]*)"[^>]*>')
        matches = list(anchor_pat.finditer(card_area))

    if matches:
        block_start = matches[0].start()
        block_end = matches[1].start() if len(matches) > 1 else min(block_start + 7000, len(card_area))
        card_html = card_area[block_start:block_end]
        href = matches[0].group(1)
        emit(f"Card length: {len(card_html)}")
        emit(f"href: {href[:100]}")

        # Step 1: aria-label
        aria_price = re.search(
            r'class="[^"]*(?:us--price|new--price)[^"]*"[^>]*aria-label="([^"]+)"',
            card_html
        )
        emit(f"\n[Step 1] aria-label regex match: {bool(aria_price)}")
        if aria_price:
            emit(f"  matched text: {aria_price.group(0)[:120]}")
            emit(f"  captured: {aria_price.group(1)!r}")
            raw_label = aria_price.group(1)
            num_m = re.search(r'([\d,]+\.?\d*)', raw_label)
            emit(f"  num_m: {num_m.group(1) if num_m else 'NONE'}")
            if num_m:
                raw = num_m.group(1)
                if ',' in raw and '.' not in raw:
                    raw = raw.replace(',', '.')
                elif ',' in raw and '.' in raw:
                    raw = raw.replace(',', '')
                emit(f"  after comma fix: {raw!r}")
                try:
                    emit(f"  float: {float(raw)}")
                except:
                    emit(f"  float: ERROR")
        else:
            # Show what us--price elements exist
            all_price_els = re.findall(r'class="[^"]*us--price[^"]*"[^>]*', card_html)
            emit(f"  All us--price elements ({len(all_price_els)}):")
            for pe in all_price_els[:5]:
                emit(f"    {pe[:150]}")

        # Step 2: DOM block
        price_block = re.search(
            r'class="[^"]*(?:us--price|new--price)[^"]*"[^>]*>(.*?)</(?:span|div)>',
            card_html, re.DOTALL
        )
        emit(f"\n[Step 2] DOM block match: {bool(price_block)}")
        if price_block:
            inner = price_block.group(1)
            emit(f"  inner (first 200): {inner[:200]!r}")
            num_m = re.search(r'(\d{1,5}(?:[.,]\d{1,2})?)', inner)
            emit(f"  num_m: {num_m.group(1) if num_m else 'NONE'}")

        # Step 4: pdp_npi
        decoded = unquote(html_mod.unescape(href))
        pdp_m = re.search(r'pdp_npi=([^&]+)', decoded)
        emit(f"\n[Step 4] pdp_npi match: {bool(pdp_m)}")
        if pdp_m:
            parts = pdp_m.group(1).split("!")
            emit(f"  parts[:6]: {parts[:6]}")

    a.close()

except Exception as e:
    import traceback
    emit(f"FATAL: {type(e).__name__}: {e}")
    emit(traceback.format_exc())

emit()
emit("=== DONE ===")
flush()
log.info("diag_v6n complete")
