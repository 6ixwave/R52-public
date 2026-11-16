"""Live test: fetch AliExpress.ru wholesale with forced Russian locale cookies.
Goal: get RU titles, RUB prices, ru.aliexpress.com URLs."""
import json, re, os, sys, time, logging, html as html_mod
from datetime import datetime, timezone
from urllib.parse import quote, unquote

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("live_ru")

BASE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(BASE, "data", "collector_output")
os.makedirs(OUT, exist_ok=True)

SEARCH_QUERY = "наушники"
WHOLESALE_URL = f"https://ru.aliexpress.com/wholesale?SearchText={quote(SEARCH_QUERY)}"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
    "Referer": "https://ru.aliexpress.com/",
    "Upgrade-Insecure-Requests": "1",
}

# Russian locale cookies — critical for forcing RU content
RU_COOKIES = {
    "aep_usuc_f": "region=RU&b_locale=ru_RU&c_locale=ru_RU&site=ru",
    "intl_locale": "ru_RU",
    "xman_us_f": "region=RU&b_locale=ru_RU&site=ru",
    "xman_us_t": "domain=3&site=ru",
    "aep_dtc": "d",
}


def fetch_with_cookies(url, headers, cookies):
    """Fetch with explicit cookies using curl_cffi."""
    from curl_cffi import requests as cr
    t0 = time.time()
    resp = cr.get(
        url,
        headers=headers,
        cookies=cookies,
        impersonate="chrome",
        timeout=30,
    )
    elapsed = time.time() - t0
    log.info(f"curl_cffi: status={resp.status_code}, len={len(resp.text)}, time={elapsed:.1f}s")
    return resp.text


def parse_cards(html):
    """Same parser as V20."""
    products = []
    card_list_idx = html.find('id="card-list"')
    if card_list_idx < 0:
        log.warning("card-list not found")
        # Try alternative: look for data-grid or search-result
        for alt in ['data-grid', 'search-result', 'product-list', 'id="grid"']:
            card_list_idx = html.find(alt)
            if card_list_idx >= 0:
                log.info(f"Alternative container: {alt} at {card_list_idx}")
                break
        if card_list_idx < 0:
            return products

    window_end = min(card_list_idx + 700000, len(html))
    card_area = html[card_list_idx:window_end]

    # Multiple anchor patterns
    anchor_pat = re.compile(
        r'<a[^>]*class="[^"]*search-card-item[^"]*"[^>]*href="([^"]+)"[^>]*>'
    )
    matches = list(anchor_pat.finditer(card_area))
    
    if not matches:
        # Try: any <a> with /item/ in href within card area
        anchor_pat2 = re.compile(
            r'<a[^>]*href="([^"]*/item/\d+\.html[^"]*)"[^>]*>'
        )
        matches = list(anchor_pat2.finditer(card_area))
        log.info(f"Alt pattern: {len(matches)} item links")

    log.info(f"Found {len(matches)} card anchors")

    for i, m in enumerate(matches):
        href = html_mod.unescape(m.group(1))
        block_start = m.start()
        block_end = matches[i+1].start() if i+1 < len(matches) else min(block_start + 7000, len(card_area))
        card_html = card_area[block_start:block_end]

        p = {"item_id": None, "title": None, "price": None, "currency": None,
             "original_price": None, "images": [], "url": None}

        # Item ID
        id_match = re.search(r'/item/(\d+)\.html', href)
        if id_match:
            p["item_id"] = id_match.group(1)

        # URL
        if href.startswith("//"):
            p["url"] = "https:" + href
        else:
            p["url"] = href
        p["url"] = re.sub(r'\?.*$', '', p["url"])

        # Title: multiple patterns
        title = None
        # A: title="..." on title div
        t_match = re.search(r'class="[^"]*new--title[^"]*"[^>]*title="([^"]{10,300})"', card_html)
        if t_match:
            title = html_mod.unescape(t_match.group(1)).strip()
        # B: <h3 class="new--titleText...">
        if not title:
            t_match = re.search(r'<h3[^>]*class="[^"]*new--titleText[^"]*"[^>]*>(.*?)</h3>', card_html, re.DOTALL)
            if t_match:
                title = re.sub(r'<[^>]+>', '', t_match.group(1)).strip()
                title = html_mod.unescape(title)
        # C: img alt
        if not title:
            t_match = re.search(r'class="[^"]*images--item[^"]*"[^>]*alt="([^"]{15,300})"', card_html)
            if t_match:
                title = html_mod.unescape(t_match.group(1)).strip()
        # D: aria-label
        if not title:
            t_match = re.search(r'aria-label="([^"]{15,300})"', card_html)
            if t_match:
                title = html_mod.unescape(t_match.group(1)).strip()
        p["title"] = title

        # Price from pdp_npi
        decoded = unquote(html_mod.unescape(href))
        pdp_m = re.search(r'pdp_npi=([^&]+)', decoded)
        if pdp_m:
            parts = pdp_m.group(1).split("!")
            if len(parts) >= 4:
                if re.match(r'^[A-Z]{3}$', parts[1]):
                    p["currency"] = parts[1]
                try:
                    p["price"] = float(parts[2])
                except (ValueError, IndexError):
                    pass
                try:
                    p["original_price"] = float(parts[3])
                except (ValueError, IndexError):
                    pass

        # Images
        img_pat = re.compile(r'<img[^>]*class="[^"]*images--item[^"]*"[^>]*src="([^"]+)"', re.I)
        for im in img_pat.finditer(card_html):
            url = html_mod.unescape(im.group(1))
            if url.startswith("//"):
                url = "https:" + url
            if url not in p["images"]:
                p["images"].append(url)

        # Lazy images
        lazy_pat = re.compile(r'<div[^>]*class="[^"]*lazy-load[^"]*"[^>]*data-src="([^"]+)"', re.I)
        for im in lazy_pat.finditer(card_html):
            url = html_mod.unescape(im.group(1))
            if url.startswith("//"):
                url = "https:" + url
            if url not in p["images"] and len(p["images"]) < 5:
                p["images"].append(url)

        products.append(p)

    return products


def main():
    log.info("=== AliExpress.ru LIVE RU-locale test ===")
    log.info(f"URL: {WHOLESALE_URL}")
    log.info(f"Cookies: {RU_COOKIES}")

    html = fetch_with_cookies(WHOLESALE_URL, HEADERS, RU_COOKIES)

    # Save HTML
    html_path = os.path.join(OUT, "diag_live_ru_page.html")
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(html)
    log.info(f"Saved HTML: {html_path} ({len(html)} bytes)")

    # Locale check
    lm = re.search(r'<html[^>]*lang="(\w+)"', html)
    log.info(f"HTML lang: {lm.group(1) if lm else 'unknown'}")

    # Check for locale indicators
    locale_indicators = {
        "RUB": html.count("RUB") + html.count("\u20bd"),
        "EUR": html.count("EUR") + html.count("\u20ac"),
        "ru.aliexpress": html.count("ru.aliexpress.com"),
        "it.aliexpress": html.count("it.aliexpress.com"),
        "www.aliexpress": html.count("www.aliexpress.com"),
        "Cyrillic_chars": len(re.findall(r'[\u0400-\u04FF]', html)),
    }
    log.info(f"Locale indicators: {json.dumps(locale_indicators, ensure_ascii=False)}")

    # Parse
    products = parse_cards(html)

    # Stats
    with_title = sum(1 for p in products if p.get("title"))
    with_price = sum(1 for p in products if p.get("price"))
    with_img = sum(1 for p in products if p.get("images"))
    with_id = sum(1 for p in products if p.get("item_id"))
    rub_count = sum(1 for p in products if p.get("currency") == "RUB")
    eur_count = sum(1 for p in products if p.get("currency") == "EUR")
    ru_url_count = sum(1 for p in products if p.get("url", "").startswith("https://ru.aliexpress"))
    it_url_count = sum(1 for p in products if p.get("url", "").startswith("https://it.aliexpress"))

    log.info(f"\n=== RESULTS ===")
    log.info(f"Total: {len(products)}")
    log.info(f"ID: {with_id} | Title: {with_title} | Price: {with_price} | Img: {with_img}")
    log.info(f"Currency: RUB={rub_count} EUR={eur_count}")
    log.info(f"URL domain: ru={ru_url_count} it={it_url_count}")

    # Show first 5
    log.info("\n--- First 5 ---")
    for i, p in enumerate(products[:5]):
        log.info(f"  [{i+1}] id={p['item_id']}")
        log.info(f"      title={str(p['title'])[:90]}")
        log.info(f"      price={p['price']} {p['currency']} (orig={p['original_price']})")
        log.info(f"      url={p['url'][:70] if p['url'] else 'N/A'}")
        log.info(f"      imgs={len(p['images'])}")

    # Save
    result = {
        "query": SEARCH_QUERY,
        "source": "aliexpress_ru_live_ru",
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "total": len(products),
        "with_title": with_title,
        "with_price": with_price,
        "with_images": with_img,
        "currency_breakdown": {"RUB": rub_count, "EUR": eur_count},
        "url_domain_breakdown": {"ru": ru_url_count, "it": it_url_count},
        "locale_indicators": locale_indicators,
        "products": products,
    }
    out_path = os.path.join(OUT, "diag_live_ru_parsed.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    log.info(f"\nSaved → {out_path}")

    # Verdict
    if len(products) >= 10 and with_title >= 8 and with_price >= 8:
        if rub_count > eur_count:
            log.info("✅ SUCCESS: Russian locale confirmed, good data")
            return 0
        else:
            log.info("⚠️ PARTIAL: data OK but locale still not RU")
            return 1
    elif len(products) >= 5:
        log.info("⚠️ PARTIAL: fewer products")
        return 1
    else:
        log.info("❌ LOW: insufficient products")
        return 2


if __name__ == "__main__":
    sys.exit(main())
