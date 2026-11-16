"""V18: AliExpress.ru wholesale parser — fixed titles, prices, RU locale.
Key fixes:
- Force RU locale via aep_usuc_f cookie
- Decode URL params in pdp_npi before parsing
- Better title extraction (handle nested img/span)
- Fallback: _init_data_ JSON for titles
"""
import json, re, os, sys, time, logging, html as html_mod
from datetime import datetime, timezone
from urllib.parse import unquote, urlparse, parse_qs

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("diag_v18")

BASE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(BASE, "data", "collector_output")
os.makedirs(OUT, exist_ok=True)

SEARCH_QUERY = "наушники"
WHOLESALE_URL = f"https://ru.aliexpress.com/wholesale?SearchText={SEARCH_QUERY}"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
    "Referer": "https://ru.aliexpress.com/",
    "Upgrade-Insecure-Requests": "1",
}

# Cookie to force RU locale (aep_usuc_f: region=RU, locale=ru_RU, currency=RUB)
COOKIE_RU = (
    "aep_usuc_f=site=rual&c_tp=RUB&region=RU&b_locale=ru_RU; "
    "aep_usuc=site=rual&c_tp=RUB&region=RU&b_locale=ru_RU; "
    "_m_h5_tk_enc=test; "
    "xman_us_f=x_as=i%7C%7C%7C%7C1%7Cru_RU%7CRUB%7C156%7C0; "
    "xman_us=site=rual&isb=n&c_tp=RUB&region=RU&b_locale=ru_RU"
)


def fetch_html(url, headers, cookies=""):
    """Fetch with curl_cffi, forcing cookies."""
    try:
        from curl_cffi import requests as cr
        h = dict(headers)
        if cookies:
            h["Cookie"] = cookies
        resp = cr.get(url, headers=h, impersonate="chrome", timeout=30)
        log.info(f"curl_cffi: status={resp.status_code}, len={len(resp.text)}")
        return resp.text
    except Exception as e:
        log.warning(f"curl_cffi failed: {e}")
    
    import requests
    h = dict(headers)
    if cookies:
        h["Cookie"] = cookies
    resp = requests.get(url, headers=h, timeout=30)
    log.info(f"requests: status={resp.status_code}, len={len(resp.text)}")
    return resp.text


def extract_card_block(html, start_idx):
    """Extract one card block starting from a search-card-item <a> tag.
    Returns the inner HTML of that <a>...</a>."""
    # Find the opening <a ...search-card-item...>
    a_start = html.find('<a', start_idx)
    if a_start < 0:
        return ""
    
    # Find the matching </a> — count nested <a> (rare but possible)
    depth = 0
    pos = a_start
    while pos < len(html):
        next_open = html.find('<a', pos)
        next_close = html.find('</a>', pos)
        if next_close < 0:
            break
        if next_open >= 0 and next_open < next_close:
            depth += 1
            pos = next_open + 2
        else:
            if depth == 0:
                return html[a_start:next_close + 4]
            depth -= 1
            pos = next_close + 4
    return html[a_start:a_start + 5000]  # fallback window


def parse_pdp_npi(href):
    """Parse pdp_npi URL param: 6@dis!CUR!price!orig!..."""
    # First decode URL encoding
    decoded = unquote(href)
    # Find pdp_npi=...& or pdp_npi=... (end)
    m = re.search(r'pdp_npi=([^&]+)', decoded)
    if not m:
        return None, None, None
    val = m.group(1)
    # Format: 6@dis!EUR!46.90!26.70!!...
    parts = val.split("!")
    if len(parts) >= 4:
        currency = parts[1] if len(parts[1]) == 3 else None
        try:
            price = float(parts[2])
        except (ValueError, IndexError):
            price = None
        try:
            orig = float(parts[3])
        except (ValueError, IndexError):
            orig = None
        return currency, price, orig
    return None, None, None


def extract_title_from_card(card_html):
    """Extract product title from card HTML.
    Title is in element with class containing 'us--title' or 'search-card-title'."""
    # Pattern 1: <span class="...us--title...">TEXT <img .../></span>
    # The text might be before nested elements
    patterns = [
        # us--title class
        r'class="[^"]*us--title[^"]*"[^>]*>(.*?)</(?:span|div|p)>',
        # search-card-title
        r'class="[^"]*search-card-title[^"]*"[^>]*>(.*?)</(?:span|div|p)>',
        # title--line
        r'class="[^"]*title--line[^"]*"[^>]*>(.*?)</(?:span|div|p)>',
        # Generic: aria-label on the card link
        r'aria-label="([^"]{15,})"',
    ]
    
    for pat in patterns:
        m = re.search(pat, card_html, re.DOTALL)
        if m:
            raw = m.group(1)
            # Remove all HTML tags
            text = re.sub(r'<[^>]+>', '', raw)
            # Decode entities
            text = html_mod.unescape(text).strip()
            # Remove extra whitespace
            text = re.sub(r'\s+', ' ', text)
            if len(text) > 8:
                return text
    return None


def extract_images_from_card(card_html):
    """Extract product image URLs from card."""
    imgs = []
    # Look for img tags with alicdn/aliexpress-media in src
    pattern = re.compile(
        r'<img[^>]*(?:src|data-src|data-lazy-src)="([^"]*(?:alicdn|aliexpress-media)[^"]*)"',
        re.I
    )
    for m in pattern.finditer(card_html):
        url = m.group(1)
        if url.startswith("//"):
            url = "https:" + url
        # Skip tiny icons (30x30, etc.)
        if re.search(r'/(?:30x30|48x48|16x16|24x24)', url):
            continue
        if url not in imgs and len(imgs) < 6:
            imgs.append(url)
    return imgs


def parse_cards_v2(html):
    """V2 card parser: more robust extraction."""
    products = []
    
    # Find card-list section
    card_list_idx = html.find('id="card-list"')
    if card_list_idx < 0:
        log.warning("card-list not found")
        return products
    
    # Work in a window after card-list
    window_start = card_list_idx
    window_end = min(window_start + 700000, len(html))
    card_area = html[window_start:window_end]
    
    # Find all search-card-item anchors
    anchor_pattern = re.compile(
        r'<a[^>]*class="[^"]*search-card-item[^"]*"[^>]*href="([^"]+)"',
        re.DOTALL
    )
    
    matches = list(anchor_pattern.finditer(card_area))
    log.info(f"Found {len(matches)} card anchors")
    
    for i, m in enumerate(matches):
        href = html_mod.unescape(m.group(1))
        
        # Get card block (from this anchor to next anchor or 5000 chars)
        block_start = m.start()
        block_end = matches[i + 1].start() if i + 1 < len(matches) else min(block_start + 5000, len(card_area))
        card_html = card_area[block_start:block_end]
        
        p = {
            "item_id": None,
            "title": None,
            "price": None,
            "currency": None,
            "original_price": None,
            "images": [],
            "url": None,
            "seller": None,
        }
        
        # Item ID
        id_match = re.search(r'/item/(\d+)\.html', href)
        if id_match:
            p["item_id"] = id_match.group(1)
        
        # Clean URL
        if href.startswith("//"):
            p["url"] = "https:" + href
        else:
            p["url"] = href
        p["url"] = re.sub(r'\?.*$', '', p["url"])
        
        # Title
        p["title"] = extract_title_from_card(card_html)
        
        # Price from pdp_npi
        cur, price, orig = parse_pdp_npi(href)
        p["currency"] = cur
        p["price"] = price
        p["original_price"] = orig
        
        # Also try price from card HTML (in case pdp_npi missing)
        if not p["price"]:
            # Look for price patterns in card
            price_re = re.compile(
                r'(?:class="[^"]*(?:price|Price)[^"]*"|data-price=)[^>]*>\s*(?:<[^>]+>\s*)*[€$₽£]\s*([\d.,]+)',
                re.DOTALL
            )
            pm = price_re.search(card_html)
            if pm:
                try:
                    p["price"] = float(pm.group(1).replace(",", "").replace(" ", ""))
                except ValueError:
                    pass
        
        # Images
        p["images"] = extract_images_from_card(card_html)
        
        # Seller (optional)
        seller_re = re.compile(r'class="[^"]*(?:seller|store)[^"]*"[^>]*>\s*([^<]{3,50})\s*<', re.I)
        sm = seller_re.search(card_html)
        if sm:
            p["seller"] = sm.group(1).strip()
        
        products.append(p)
    
    return products


def parse_init_data_titles(html):
    """Extract titles from _init_data_ JSON as supplement."""
    titles = []
    m = re.search(r'window\._dida_config_\._init_data_\s*=\s*(\{.*?\});?\s*(?:</script>|window\.)', html, re.DOTALL)
    if not m:
        # Try without the trailing pattern
        m = re.search(r'window\._dida_config_\._init_data_\s*=\s*(\{)', html)
        if m:
            # Find matching closing brace
            start = m.start(1)
            depth = 0
            pos = start
            while pos < len(html):
                if html[pos] == '{':
                    depth += 1
                elif html[pos] == '}':
                    depth -= 1
                    if depth == 0:
                        break
                pos += 1
            json_str = html[start:pos + 1]
        else:
            return titles
    else:
        json_str = m.group(1)
    
    try:
        data = json.loads(json_str)
    except json.JSONDecodeError as e:
        log.warning(f"_init_data_ JSON parse error: {e}")
        return titles
    
    # Recursively find all "text" fields that look like product titles
    def find_texts(obj, path=""):
        results = []
        if isinstance(obj, dict):
            for k, v in obj.items():
                if k == "text" and isinstance(v, str) and len(v.strip()) > 15:
                    results.append((path, v.strip()))
                elif k == "text" and isinstance(v, dict) and "text" in v:
                    t = v["text"].strip()
                    if len(t) > 15:
                        results.append((path, t))
                else:
                    results.extend(find_texts(v, f"{path}.{k}"))
        elif isinstance(obj, list):
            for i, item in enumerate(obj[:5]):  # limit depth
                results.extend(find_texts(item, f"{path}[{i}]"))
        return results
    
    all_texts = find_texts(data.get("data", data))
    # Filter: keep texts that look like product names (not filters, not UI)
    ui_keywords = re.compile(
        r'(?:фильтр|filter|сортиров|sort|категори|categor|выбер|выбери|выбор|выбрать|'
        r'offert|scont|spediz|conseg|qualit|promo|11\.11|free|shipping|delivery|'
        r'показа|показ|результат|result|товар|product|найдено|found|ещё|more|load)',
        re.I
    )
    
    product_texts = []
    for path, text in all_texts:
        if ui_keywords.search(text):
            continue
        if len(text) > 15 and len(text) < 200:
            product_texts.append(text)
    
    # Deduplicate
    seen = set()
    unique = []
    for t in product_texts:
        key = t.lower()[:50]
        if key not in seen:
            seen.add(key)
            unique.append(t)
    
    log.info(f"_init_data_: {len(all_texts)} texts, {len(unique)} product-like")
    return unique


def main():
    log.info("=== AliExpress.ru Parser V18 ===")
    log.info(f"Query: {SEARCH_QUERY}")
    
    t0 = time.time()
    html = fetch_html(WHOLESALE_URL, HEADERS, COOKIE_RU)
    fetch_time = time.time() - t0
    log.info(f"Fetch: {fetch_time:.1f}s, {len(html)} bytes")
    
    # Save
    html_path = os.path.join(OUT, "diag_v18_page.html")
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(html)
    
    # Check locale
    locale_match = re.search(r'<html[^>]*lang="(\w+)"', html)
    log.info(f"Page locale: {locale_match.group(1) if locale_match else 'unknown'}")
    
    # Check TMD
    if "_____tmd_____" in html or "/punish" in html:
        log.warning("⚠️ TMD/CAPTCHA detected!")
    
    # Parse cards
    log.info("--- Parsing HTML cards ---")
    products = parse_cards_v2(html)
    log.info(f"HTML cards: {len(products)}")
    
    # Supplement titles from _init_data_
    log.info("--- Parsing _init_data_ titles ---")
    init_titles = parse_init_data_titles(html)
    
    # Assign titles to products that don't have them
    # Strategy: match by order (init_data titles are in same order as cards)
    no_title = [p for p in products if not p["title"]]
    if no_title and init_titles:
        log.info(f"Assigning {min(len(no_title), len(init_titles))} titles from _init_data_")
        for i, p in enumerate(no_title):
            if i < len(init_titles):
                p["title"] = init_titles[i]
    
    # Stats
    with_title = sum(1 for p in products if p.get("title"))
    with_price = sum(1 for p in products if p.get("price"))
    with_img = sum(1 for p in products if p.get("images"))
    with_id = sum(1 for p in products if p.get("item_id"))
    
    log.info(f"\n=== RESULTS ===")
    log.info(f"Total: {len(products)}")
    log.info(f"With ID: {with_id}")
    log.info(f"With title: {with_title}")
    log.info(f"With price: {with_price}")
    log.info(f"With images: {with_img}")
    
    # Show first 5
    log.info("\n--- First 5 products ---")
    for i, p in enumerate(products[:5]):
        log.info(f"  [{i+1}] id={p['item_id']}")
        log.info(f"      title={str(p['title'])[:80]}")
        log.info(f"      price={p['price']} {p['currency']} (orig={p['original_price']})")
        log.info(f"      imgs={len(p['images'])}")
        log.info(f"      url={str(p['url'])[:80]}")
    
    # Save
    result = {
        "query": SEARCH_QUERY,
        "source": "aliexpress_ru_wholesale_v18",
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "fetch_time_sec": round(fetch_time, 1),
        "locale": locale_match.group(1) if locale_match else "unknown",
        "total": len(products),
        "with_title": with_title,
        "with_price": with_price,
        "with_images": with_img,
        "products": products,
    }
    out_path = os.path.join(OUT, "diag_v18_parsed.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    log.info(f"\nResults → {out_path}")
    
    # Success
    if len(products) >= 10 and with_price >= 5 and with_title >= 5:
        log.info("✅ SUCCESS")
        return 0
    elif len(products) >= 10:
        log.info("⚠️ PARTIAL")
        return 1
    else:
        log.info("❌ LOW")
        return 2


if __name__ == "__main__":
    sys.exit(main())
