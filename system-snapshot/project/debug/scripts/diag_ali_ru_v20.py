"""V20: Final AliExpress.ru HTML parser — verified patterns from v19 dump.
Parses saved v17 HTML to confirm extraction, then does live fetch."""
import json, re, os, sys, time, logging, html as html_mod
from datetime import datetime, timezone
from urllib.parse import unquote

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("v20")

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


def fetch_html(url, headers):
    try:
        from curl_cffi import requests as cr
        resp = cr.get(url, headers=headers, impersonate="chrome", timeout=30)
        log.info(f"curl_cffi: status={resp.status_code}, len={len(resp.text)}")
        return resp.text
    except Exception as e:
        log.warning(f"curl_cffi: {e}")
    import requests
    resp = requests.get(url, headers=headers, timeout=30)
    log.info(f"requests: status={resp.status_code}, len={len(resp.text)}")
    return resp.text


def parse_pdp_npi(href):
    """Parse pdp_npi: 6@dis!CUR!price!orig!!..."""
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
    """Extract product cards from wholesale page HTML."""
    products = []
    
    card_list_idx = html.find('id="card-list"')
    if card_list_idx < 0:
        log.warning("card-list not found")
        return products
    
    window_end = min(card_list_idx + 700000, len(html))
    card_area = html[card_list_idx:window_end]
    
    # Find all card anchors
    anchor_pat = re.compile(
        r'<a[^>]*class="[^"]*search-card-item[^"]*"[^>]*href="([^"]+)"[^>]*>'
    )
    matches = list(anchor_pat.finditer(card_area))
    log.info(f"Found {len(matches)} card anchors")
    
    for i, m in enumerate(matches):
        href = html_mod.unescape(m.group(1))
        
        # Card block: from this anchor to next (or 7000 chars)
        block_start = m.start()
        block_end = matches[i+1].start() if i+1 < len(matches) else min(block_start + 7000, len(card_area))
        card_html = card_area[block_start:block_end]
        
        p = {
            "item_id": None,
            "title": None,
            "price": None,
            "currency": None,
            "original_price": None,
            "images": [],
            "url": None,
        }
        
        # 1. Item ID
        id_match = re.search(r'/item/(\d+)\.html', href)
        if id_match:
            p["item_id"] = id_match.group(1)
        
        # 2. Clean URL
        if href.startswith("//"):
            p["url"] = "https:" + href
        else:
            p["url"] = href
        p["url"] = re.sub(r'\?.*$', '', p["url"])
        
        # 3. Title — PRIMARY: title="..." attribute on new--title div
        #    SECONDARY: <h3 class="new--titleText...">TEXT</h3>
        #    TERTIARY: img alt="..." in images--item
        title = None
        
        # Pattern A: title="..." on the title div
        t_match = re.search(
            r'class="[^"]*new--title[^"]*"[^>]*title="([^"]{10,300})"',
            card_html
        )
        if t_match:
            title = html_mod.unescape(t_match.group(1)).strip()
        
        # Pattern B: <h3 class="new--titleText...">TEXT</h3>
        if not title:
            t_match = re.search(
                r'<h3[^>]*class="[^"]*new--titleText[^"]*"[^>]*>(.*?)</h3>',
                card_html, re.DOTALL
            )
            if t_match:
                title = re.sub(r'<[^>]+>', '', t_match.group(1)).strip()
                title = html_mod.unescape(title)
        
        # Pattern C: img alt in images--item
        if not title:
            t_match = re.search(
                r'class="[^"]*images--item[^"]*"[^>]*alt="([^"]{15,300})"',
                card_html
            )
            if t_match:
                title = html_mod.unescape(t_match.group(1)).strip()
        
        p["title"] = title
        
        # 4. Price from pdp_npi
        cur, price, orig = parse_pdp_npi(href)
        p["currency"] = cur
        p["price"] = price
        p["original_price"] = orig
        
        # 5. Images (images--item class, first 1-2)
        img_pat = re.compile(
            r'<img[^>]*class="[^"]*images--item[^"]*"[^>]*src="([^"]+)"',
            re.I
        )
        for im in img_pat.finditer(card_html):
            url = html_mod.unescape(im.group(1))
            if url.startswith("//"):
                url = "https:" + url
            if url not in p["images"]:
                p["images"].append(url)
        
        # Also grab lazy-load images (data-src)
        lazy_pat = re.compile(
            r'<div[^>]*class="[^"]*lazy-load[^"]*"[^>]*data-src="([^"]+)"',
            re.I
        )
        for im in lazy_pat.finditer(card_html):
            url = html_mod.unescape(im.group(1))
            if url.startswith("//"):
                url = "https:" + url
            if url not in p["images"] and len(p["images"]) < 5:
                p["images"].append(url)
        
        products.append(p)
    
    return products


def main():
    log.info("=== AliExpress.ru Parser V20 ===")
    log.info(f"Query: {SEARCH_QUERY}")
    
    # Try saved HTML first for quick test
    saved = os.path.join(OUT, "diag_v17_page.html")
    if os.path.exists(saved) and "--live" not in sys.argv:
        log.info(f"Using saved HTML: {saved}")
        with open(saved, "r", encoding="utf-8") as f:
            html = f.read()
        fetch_time = 0
    else:
        t0 = time.time()
        html = fetch_html(WHOLESALE_URL, HEADERS)
        fetch_time = time.time() - t0
        # Save
        with open(os.path.join(OUT, "diag_v20_page.html"), "w", encoding="utf-8") as f:
            f.write(html)
    
    log.info(f"HTML: {len(html)} bytes, fetch={fetch_time:.1f}s")
    
    # Locale check
    lm = re.search(r'<html[^>]*lang="(\w+)"', html)
    log.info(f"Locale: {lm.group(1) if lm else 'unknown'}")
    
    # Parse
    products = parse_cards(html)
    
    # Stats
    with_title = sum(1 for p in products if p.get("title"))
    with_price = sum(1 for p in products if p.get("price"))
    with_img = sum(1 for p in products if p.get("images"))
    with_id = sum(1 for p in products if p.get("item_id"))
    
    log.info(f"\n=== RESULTS ===")
    log.info(f"Total: {len(products)}")
    log.info(f"ID: {with_id} | Title: {with_title} | Price: {with_price} | Img: {with_img}")
    
    # Show first 5
    log.info("\n--- First 5 ---")
    for i, p in enumerate(products[:5]):
        log.info(f"  [{i+1}] id={p['item_id']}")
        log.info(f"      title={str(p['title'])[:90]}")
        log.info(f"      price={p['price']} {p['currency']} (orig={p['original_price']})")
        log.info(f"      imgs={len(p['images'])}")
    
    # Save
    result = {
        "query": SEARCH_QUERY,
        "source": "aliexpress_ru_wholesale_v20",
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "total": len(products),
        "with_title": with_title,
        "with_price": with_price,
        "with_images": with_img,
        "products": products,
    }
    out_path = os.path.join(OUT, "diag_v20_parsed.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    log.info(f"\nSaved → {out_path}")
    
    if len(products) >= 10 and with_price >= 5 and with_title >= 5:
        log.info("✅ SUCCESS: titles + prices + images")
        return 0
    elif len(products) >= 10:
        log.info("⚠️ PARTIAL")
        return 1
    return 2


if __name__ == "__main__":
    sys.exit(main())
