"""
diag_v6m.py — Dump raw HTML around price elements in wholesale cards.
Цель: понять структуру new--price и починить regex.
Запуск: python diag_v6m.py
"""
import sys, os, time, re, logging

os.environ.setdefault("R52_HOME", os.path.join(os.environ.get("LOCALAPPDATA", "C:\\Temp"), "R52"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("diag_v6m")

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "diag_v6m_output.txt")
lines = []

def emit(s=""):
    print(s)
    lines.append(s)

def flush():
    with open(OUT, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")

QUERY = "наушники"

emit(f"=== diag_v6m — {time.strftime('%Y-%m-%d %H:%M:%S')} ===")
emit(f"Query: {QUERY!r}")
emit()

try:
    from urllib.parse import quote
    from curl_cffi import requests as cffi_req

    url = f"https://ru.aliexpress.com/wholesale?SearchText={quote(QUERY)}&page=1&_lang=ru_RU&_currency=RUB&gatewayAdapt=glo2rus"
    emit(f"URL: {url[:100]}")
    t0 = time.monotonic()
    resp = cffi_req.get(
        url,
        impersonate="chrome",
        timeout=(15, 30),
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
    emit(f"Anchors found: {len(matches)}")

    # Dump first 3 cards
    for i in range(min(3, len(matches))):
        block_start = matches[i].start()
        block_end = matches[i+1].start() if i+1 < len(matches) else min(block_start + 7000, len(card_area))
        card_html = card_area[block_start:block_end]
        href = matches[i].group(1)

        emit(f"\n{'='*60}")
        emit(f"CARD [{i}] href={href[:100]}")
        emit(f"Card HTML length: {len(card_html)}")

        # Find all price-related elements
        price_pat = re.compile(r'class="[^"]*(?:price|Price|Price)[^"]*"[^>]*>(.{0,300})', re.DOTALL)
        price_matches = list(price_pat.finditer(card_html))
        emit(f"Price-class elements: {len(price_matches)}")
        for pi, pm in enumerate(price_matches[:5]):
            emit(f"  [P{pi}] class area: {pm.group(0)[:200]}")

        # Find the specific new--price block
        np_pat = re.compile(r'class="[^"]*new--price[^"]*"[^>]*>(.*?)</(?:span|div)>', re.DOTALL)
        np_matches = list(np_pat.finditer(card_html))
        emit(f"new--price blocks: {len(np_matches)}")
        for ni, nm in enumerate(np_matches[:5]):
            emit(f"  [NP{ni}] raw: {repr(nm.group(1)[:200])}")
            # Apply current regex
            inner = nm.group(1)
            num_m = re.search(r'(\d{1,5}(?:[.,]\d{1,2})?)', inner)
            emit(f"  [NP{ni}] current regex match: {num_m.group(1) if num_m else 'NONE'}")

        # Broader: find ALL numbers in the card that look like prices
        all_nums = re.findall(r'(\d{1,5}(?:[.,]\d{1,2})?)\s*(?:EUR|USD|RUB|GBP|€|\$|₽|руб)', card_html)
        emit(f"Numbers near currency symbols: {all_nums[:10]}")

        # pdp_npi in href
        from urllib.parse import unquote
        import html as html_mod
        decoded = unquote(html_mod.unescape(href))
        pdp_m = re.search(r'pdp_npi=([^&]+)', decoded)
        if pdp_m:
            parts = pdp_m.group(1).split("!")
            emit(f"pdp_npi parts: {parts[:6]}")

        # Dump full card HTML (first 2000 chars) for inspection
        emit(f"  ── Card HTML (first 2000) ──")
        for j in range(0, min(2000, len(card_html)), 200):
            emit(f"  |{card_html[j:j+200]}")

    # Also: check if there's a JSON state with prices
    json_state = re.search(r'window\.__INIT_DATA_STATE__\s*=\s*(\{)', html)
    emit(f"\n__INIT_DATA_STATE__: {'found' if json_state else 'not found'}")

    # Check for data- attributes with prices
    data_price = re.findall(r'data-price="([^"]+)"', card_area[:100000])
    emit(f"data-price attrs: {data_price[:10]}")

    # Check for price in aria-labels
    aria_price = re.findall(r'aria-label="[^"]*?(\d+[\d.,]*)\s*(?:EUR|USD|RUB|€|\$|₽)', card_area[:100000])
    emit(f"aria-label prices: {aria_price[:10]}")

except Exception as e:
    import traceback
    emit(f"FATAL: {type(e).__name__}: {e}")
    emit(traceback.format_exc())

emit()
emit("=== DONE ===")
flush()
log.info("diag_v6m complete")
