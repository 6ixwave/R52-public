"""
diag_v7b.py — Точечная диагностика цен AE_RU (wholesale HTML) и AE_COM (product page).
Запуск: python diag_v7b.py
"""
import sys, os, time, re, json, logging
from html import unescape as html_unescape
from urllib.parse import quote, unquote

os.environ.setdefault("R52_HOME", os.path.join(os.environ.get("LOCALAPPDATA", "C:\\Temp"), "R52"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("diag_v7b")

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "diag_v7b_output.txt")
lines = []

def emit(s=""):
    print(s)
    lines.append(s)

def flush():
    with open(OUT, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")

QUERY = "наушники"

emit(f"=== diag_v7b — {time.strftime('%Y-%m-%d %H:%M:%S')} ===")
emit(f"Query: {QUERY!r}")
emit()

# ─── PART 1: AE_RU wholesale HTML — raw card dump + price regex trace ───
emit("═══ PART 1: AE_RU wholesale HTML ═══")
emit()

from curl_cffi import requests as cffi_req

url = (
    f"https://ru.aliexpress.com/wholesale?SearchText={quote(QUERY)}"
    f"&page=1&_lang=ru_RU&_currency=RUB&gatewayAdapt=glo2rus"
)
emit(f"URL: {url}")
t0 = time.monotonic()
resp = cffi_req.get(
    url, impersonate="chrome", timeout=(15, 30),
    headers={
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
        "Referer": "https://ru.aliexpress.com/",
    },
    cookies={
        "aep_usuc_f": "f_locale=ru-RU&f_region=RU&f_currency=RUB&ais=ru-RU&fre=0&c_b_locale=ru_RU",
        "aep_usuc": "f_region=RU&f_locale=ru-RU&f_currency=RUB",
    },
)
el = round(time.monotonic() - t0, 1)
emit(f"HTTP {resp.status_code}, {len(resp.text)} bytes, {el}s")
html = resp.text

# Find card-list
card_list_idx = html.find('id="card-list"')
emit(f"card-list at index: {card_list_idx}")
if card_list_idx < 0:
    emit("FATAL: card-list not found")
    flush()
    sys.exit(1)

window_end = min(card_list_idx + 700000, len(html))
card_area = html[card_list_idx:window_end]

# Find anchors
anchor_pat = re.compile(r'<a[^>]*class="[^"]*search-card-item[^"]*"[^>]*href="([^"]+)"[^>]*>')
matches = list(anchor_pat.finditer(card_area))
if not matches:
    anchor_pat2 = re.compile(r'<a[^>]*href="([^"]*/item/\d+\.html[^"]*)"[^>]*>')
    matches = list(anchor_pat2.finditer(card_area))
    emit(f"Fallback pattern: {len(matches)} matches")
else:
    emit(f"Primary pattern: {len(matches)} matches")

emit()

# Dump first 3 cards in detail
for i in range(min(3, len(matches))):
    m = matches[i]
    href = html_unescape(m.group(1))
    block_start = m.start()
    block_end = matches[i+1].start() if i+1 < len(matches) else min(block_start + 7000, len(card_area))
    card_html = card_area[block_start:block_end]

    emit(f"── Card [{i}] ──")
    emit(f"  href: {href[:150]}")

    # Item ID
    id_match = re.search(r'/item/(\d+)\.html', href)
    emit(f"  item_id: {id_match.group(1) if id_match else 'NONE'}")

    # pdp_npi
    decoded_href = unquote(html_unescape(href))
    pdp_m = re.search(r'pdp_npi=([^&]+)', decoded_href)
    if pdp_m:
        pdp_raw = pdp_m.group(1)
        emit(f"  pdp_npi raw: {pdp_raw[:120]}")
        parts = pdp_raw.split("!")
        emit(f"  pdp_npi parts: {parts[:8]}")
        if len(parts) >= 4:
            emit(f"    parts[1] (currency?): {parts[1]}")
            emit(f"    parts[2] (orig?):     {parts[2]}")
            emit(f"    parts[3] (sale?):     {parts[3]}")
    else:
        emit(f"  pdp_npi: NOT FOUND in href")
        # Try in card_html
        pdp_m2 = re.search(r'pdp_npi=([^&"\'<]+)', card_html)
        if pdp_m2:
            pdp_raw2 = pdp_m2.group(1)
            emit(f"  pdp_npi (in card): {pdp_raw2[:120]}")
            parts2 = pdp_raw2.split("!")
            emit(f"  pdp_npi parts: {parts2[:8]}")

    # Title
    t_match = re.search(r'<h3[^>]*>(.*?)</h3>', card_html, re.DOTALL)
    title = re.sub(r'<[^>]+>', '', t_match.group(1)).strip() if t_match else "NONE"
    emit(f"  title: {html_unescape(title)[:80]}")

    # ── PRICE TRACE ──
    emit(f"  ── PRICE TRACE ──")

    # Step 1: aria-label
    aria_price = re.search(
        r'class="[^"]*(?:us--price|new--price)[^"]*"[^>]*aria-label="([^"]+)"',
        card_html
    )
    if aria_price:
        emit(f"  [1] aria-label: {aria_price.group(1)!r}")
    else:
        emit(f"  [1] aria-label: NOT FOUND")
        # Broader search for any aria-label with digits
        aria_broad = re.findall(r'aria-label="([^"]*[\d][^"]*)"', card_html)
        if aria_broad:
            emit(f"      broad aria-labels: {aria_broad[:3]}")

    # Step 2: DOM price block
    price_block = re.search(
        r'class="[^"]*(?:us--price|new--price)[^"]*"[^>]*>(.*?)</(?:span|div)>',
        card_html, re.DOTALL
    )
    if price_block:
        inner = price_block.group(1)
        emit(f"  [2] DOM price inner ({len(inner)} chars): {inner[:200]!r}")
        num_m = re.search(r'(\d{1,5}(?:[.,]\d{1,2})?)', inner)
        if num_m:
            emit(f"      → regex matched: {num_m.group(1)!r}")
        else:
            emit(f"      → regex: NO MATCH")
    else:
        emit(f"  [2] DOM price block: NOT FOUND")
        # Broader: find any element with price in class
        price_elems = re.findall(r'class="([^"]*price[^"]*)"[^>]*>(.{0,100})', card_html, re.DOTALL)
        if price_elems:
            for cls, content in price_elems[:4]:
                emit(f"      elem class={cls[:60]} content={content[:80]!r}")

    # Step 3: Currency from nearby
    cur_area = ''
    pidx = card_html.find('price')
    if pidx >= 0:
        cur_area = card_html[pidx:pidx + 400]
    cur_found = None
    for cur_sym in ('EUR', 'USD', 'RUB', 'GBP', '€', '$', '₽'):
        if cur_sym in cur_area:
            cur_found = cur_sym
            break
    emit(f"  [3] currency from nearby: {cur_found}")

    # Step 4: pdp_npi price
    if not price_block or not (re.search(r'(\d{1,5}(?:[.,]\d{1,2})?)', price_block.group(1))):
        emit(f"  [4] pdp_npi fallback would be used")

    # Raw card HTML (first 800 chars) for manual inspection
    emit(f"  ── RAW CARD (first 800 chars) ──")
    emit(f"  {card_html[:800]}")
    emit()

# ─── PART 2: AE_COM product page price check ───
emit("═══ PART 2: AE_COM product page ═══")
emit()

# Use first AE_COM item from diag_v7a: 1005013565057281
test_id = "1005013565057281"
test_url = f"https://www.aliexpress.com/item/{test_id}.html"
emit(f"Testing: {test_url}")

t0 = time.monotonic()
try:
    resp2 = cffi_req.get(
        test_url, impersonate="chrome", timeout=(15, 30),
        headers={
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            "Referer": "https://www.aliexpress.com/",
        },
    )
    el2 = round(time.monotonic() - t0, 1)
    emit(f"HTTP {resp2.status_code}, {len(resp2.text)} bytes, {el2}s")
    html2 = resp2.text

    # Look for price patterns
    emit(f"  ── Price patterns in product page ──")

    # __INIT_DATA_STATE__
    init_m = re.search(r'window\.__INIT_DATA_STATE__\s*=\s*(\{.*?\})\s*;\s*(?:</script>|window\.)', html2, re.DOTALL)
    if init_m:
        emit(f"  __INIT_DATA_STATE__: found ({len(init_m.group(1))} chars)")
        # Look for price fields
        state_text = init_m.group(1)
        price_keys = re.findall(r'"(?:salePrice|originalPrice|price|salePriceString|displayPrice)"\s*:\s*("?[^,}]{1,50}"?)', state_text)
        emit(f"  price keys: {price_keys[:6]}")
    else:
        emit(f"  __INIT_DATA_STATE__: NOT FOUND")

    # runParams
    rp_m = re.search(r'window\.runParams\s*=\s*(\{.*?\})\s*;\s*(?:</script>|window\.)', html2, re.DOTALL)
    if rp_m:
        emit(f"  runParams: found ({len(rp_m.group(1))} chars)")

    # Meta price
    meta_price = re.findall(r'<meta[^>]*property="(?:product:price:amount|og:price:amount)"[^>]*content="([^"]+)"', html2)
    emit(f"  meta price: {meta_price}")

    # JSON-LD
    jsonld = re.findall(r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>', html2, re.DOTALL)
    for jld in jsonld[:2]:
        if 'price' in jld.lower():
            emit(f"  JSON-LD (price): {jld[:300]}")

    # Generic price class elements
    price_divs = re.findall(r'class="[^"]*(?:price|Price)[^"]*"[^>]*>(.{0,200})', html2)
    emit(f"  price class elements: {len(price_divs)} found")
    for pd in price_divs[:5]:
        clean = re.sub(r'<[^>]+>', ' ', pd).strip()[:80]
        emit(f"    {clean!r}")

    # Title
    title_m = re.search(r'<title[^>]*>(.*?)</title>', html2, re.DOTALL)
    emit(f"  <title>: {title_m.group(1).strip()[:80] if title_m else 'NONE'}")

except Exception as e:
    emit(f"  ERROR: {type(e).__name__}: {e}")

emit()
emit("=== DONE ===")
flush()
log.info("diag_v7b complete")
