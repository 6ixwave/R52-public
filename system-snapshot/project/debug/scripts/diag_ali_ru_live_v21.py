"""V21: Live fetch WITHOUT RU cookies (gets 835KB page with cards) + V20 parser.
Confirms stable extraction of products from fresh ru.aliexpress.com fetch."""
import json, re, os, sys, time, logging, html as html_mod
from datetime import datetime, timezone
from urllib.parse import quote, unquote

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("v21")

BASE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(BASE, "data", "collector_output")
os.makedirs(OUT, exist_ok=True)

SEARCH_QUERY = "наушники"
WHOLESALE_URL = f"https://ru.aliexpress.com/wholesale?SearchText={quote(SEARCH_QUERY)}"

# NO special cookies — just headers. This gets the 835KB server-rendered page.
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
    "Referer": "https://ru.aliexpress.com/",
    "Upgrade-Insecure-Requests": "1",
}


def fetch_html(url, headers):
    from curl_cffi import requests as cr
    t0 = time.time()
    resp = cr.get(url, headers=headers, impersonate="chrome", timeout=30)
    elapsed = time.time() - t0
    log.info(f"curl_cffi: status={resp.status_code}, len={len(resp.text)}, time={elapsed:.1f}s")
    if resp.status_code != 200:
        return None
    return resp.text


def parse_pdp_npi(href):
    decoded = unquote(html_mod.unescape(href))
    m = re.search(r'pdp_npi=([^&]+)', decoded)
    if not m:
        return None, None, None
    parts = m.group(1).split("!")
    if len(parts) >= 4:
        currency = parts[1] if re.match(r'^[A-Z]{3}$', parts[1]) else None
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


def parse_cards(html):
    products = []
    card_list_idx = html.find('id="card-list"')
    if card_list_idx < 0:
        log.warning("card-list not found in HTML")
        return products

    window_end = min(card_list_idx + 700000, len(html))
    card_area = html[card_list_idx:window_end]

    anchor_pat = re.compile(
        r'<a[^>]*class="[^"]*search-card-item[^"]*"[^>]*href="([^"]+)"[^>]*>'
    )
    matches = list(anchor_pat.finditer(card_area))

    # Fallback: any /item/ link
    if not matches:
        anchor_pat2 = re.compile(r'<a[^>]*href="([^"]*/item/\d+\.html[^"]*)"[^>]*>')
        matches = list(anchor_pat2.finditer(card_area))
        log.info(f"Fallback pattern: {len(matches)} item links")

    log.info(f"Found {len(matches)} card anchors")

    for i, m in enumerate(matches):
        href = html_mod.unescape(m.group(1))
        block_start = m.start()
        block_end = matches[i+1].start() if i+1 < len(matches) else min(block_start + 7000, len(card_area))
        card_html = card_area[block_start:block_end]

        p = {"item_id": None, "title": None, "price": None, "currency": None,
             "original_price": None, "images": [], "url": None}

        # ID
        id_match = re.search(r'/item/(\d+)\.html', href)
        if id_match:
            p["item_id"] = id_match.group(1)

        # URL
        if href.startswith("//"):
            p["url"] = "https:" + href
        else:
            p["url"] = href
        p["url"] = re.sub(r'\?.*$', '', p["url"])

        # Title
        title = None
        t_match = re.search(r'class="[^"]*new--title[^"]*"[^>]*title="([^"]{10,300})"', card_html)
        if t_match:
            title = html_mod.unescape(t_match.group(1)).strip()
        if not title:
            t_match = re.search(r'<h3[^>]*class="[^"]*new--titleText[^"]*"[^>]*>(.*?)</h3>', card_html, re.DOTALL)
            if t_match:
                title = re.sub(r'<[^>]+>', '', t_match.group(1)).strip()
                title = html_mod.unescape(title)
        if not title:
            t_match = re.search(r'class="[^"]*images--item[^"]*"[^>]*alt="([^"]{15,300})"', card_html)
            if t_match:
                title = html_mod.unescape(t_match.group(1)).strip()
        if not title:
            t_match = re.search(r'aria-label="([^"]{15,300})"', card_html)
            if t_match:
                title = html_mod.unescape(t_match.group(1)).strip()
        p["title"] = title

        # Price from pdp_npi
        cur, price, orig = parse_pdp_npi(href)
        p["currency"] = cur
        p["price"] = price
        p["original_price"] = orig

        # Images
        img_pat = re.compile(r'<img[^>]*class="[^"]*images--item[^"]*"[^>]*src="([^"]+)"', re.I)
        for im in img_pat.finditer(card_html):
            url = html_mod.unescape(im.group(1))
            if url.startswith("//"):
                url = "https:" + url
            if url not in p["images"]:
                p["images"].append(url)
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
    log.info("=== V21: Live fetch (no RU cookies) + V20 parser ===")
    log.info(f"URL: {WHOLESALE_URL}")

    html = fetch_html(WHOLESALE_URL, HEADERS)
    if not html:
        log.error("Fetch failed")
        return 2

    # Save
    html_path = os.path.join(OUT, "diag_v21_page.html")
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(html)
    log.info(f"Saved: {html_path} ({len(html)} bytes)")

    # Locale indicators
    lm = re.search(r'<html[^>]*lang="(\w+)"', html)
    title_tag = re.search(r'<title>(.*?)</title>', html, re.DOTALL)
    log.info(f"HTML lang: {lm.group(1) if lm else 'N/A'}")
    log.info(f"Page title: {title_tag.group(1)[:80] if title_tag else 'N/A'}")

    indicators = {
        "RUB": html.count("RUB") + html.count("\u20bd"),
        "EUR": html.count("EUR") + html.count("\u20ac"),
        "ru.aliexpress": html.count("ru.aliexpress.com"),
        "it.aliexpress": html.count("it.aliexpress.com"),
        "aliexpress.ru": html.count("aliexpress.ru"),
        "Cyrillic": len(re.findall(r'[\u0400-\u04FF]', html)),
    }
    log.info(f"Indicators: {json.dumps(indicators)}")

    # Parse
    products = parse_cards(html)

    with_title = sum(1 for p in products if p.get("title"))
    with_price = sum(1 for p in products if p.get("price"))
    with_img = sum(1 for p in products if p.get("images"))
    with_id = sum(1 for p in products if p.get("item_id"))
    rub = sum(1 for p in products if p.get("currency") == "RUB")
    eur = sum(1 for p in products if p.get("currency") == "EUR")

    log.info(f"\n=== RESULTS ===")
    log.info(f"Total: {len(products)} | ID: {with_id} | Title: {with_title} | Price: {with_price} | Img: {with_img}")
    log.info(f"Currency: RUB={rub} EUR={eur}")

    log.info("\n--- First 5 ---")
    for i, p in enumerate(products[:5]):
        log.info(f"  [{i+1}] id={p['item_id']}")
        log.info(f"      title={str(p['title'])[:90]}")
        log.info(f"      price={p['price']} {p['currency']} (orig={p['original_price']})")
        log.info(f"      url={p['url'][:70] if p['url'] else 'N/A'}")
        log.info(f"      imgs={len(p['images'])}")

    result = {
        "query": SEARCH_QUERY,
        "source": "aliexpress_ru_v21_live",
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "html_size": len(html),
        "total": len(products),
        "with_title": with_title,
        "with_price": with_price,
        "with_images": with_img,
        "currency": {"RUB": rub, "EUR": eur},
        "products": products,
    }
    out_path = os.path.join(OUT, "diag_v21_parsed.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    log.info(f"Saved → {out_path}")

    if len(products) >= 10 and with_title >= 8 and with_price >= 8:
        log.info("✅ SUCCESS: stable extraction confirmed")
        return 0
    elif len(products) >= 5:
        log.info("⚠️ PARTIAL")
        return 1
    return 2


if __name__ == "__main__":
    sys.exit(main())
