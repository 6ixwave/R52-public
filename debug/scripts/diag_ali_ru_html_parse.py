"""V17: Parse AliExpress.ru wholesale HTML → product cards.
Strategy:
1. Fetch wholesale page with RU headers
2. Extract cards from #card-list (regex-based, one-line HTML)
3. Fallback: JSON-LD ItemList
4. Save results to JSON
"""
import json, re, os, sys, time, logging
from datetime import datetime, timezone

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("diag_ali_ru_html")

BASE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(BASE, "data", "collector_output")
os.makedirs(OUT, exist_ok=True)

# ─── Config ───
SEARCH_QUERY = "наушники"
WHOLESALE_URL = f"https://ru.aliexpress.com/wholesale?SearchText={SEARCH_QUERY}"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
    "Accept-Encoding": "gzip, deflate, br",
    "Referer": "https://ru.aliexpress.com/",
    "Upgrade-Insecure-Requests": "1",
}

def fetch_html(url, headers):
    """Fetch page HTML using curl_cffi or requests."""
    try:
        from curl_cffi import requests as cr
        resp = cr.get(url, headers=headers, impersonate="chrome", timeout=30)
        log.info(f"curl_cffi: status={resp.status_code}, len={len(resp.text)}")
        return resp.text
    except ImportError:
        log.info("curl_cffi not available, using requests")
    except Exception as e:
        log.warning(f"curl_cffi failed: {e}, falling back to requests")
    
    import requests
    resp = requests.get(url, headers=headers, timeout=30)
    log.info(f"requests: status={resp.status_code}, len={len(resp.text)}")
    return resp.text

def parse_cards_html(html):
    """Extract product cards from #card-list section."""
    products = []
    
    # Find the card-list section
    card_list_idx = html.find('id="card-list"')
    if card_list_idx < 0:
        log.warning("card-list not found in HTML")
        return products
    
    # Get a window after card-list (cards are after this point)
    # Find all <a ... search-card-item ...> blocks
    # Each card is wrapped in a div.search-item-card-wrapper-gallery
    
    # Strategy: find all item URLs in the card-list area
    # The card area extends until the next major section
    card_area = html[card_list_idx:card_list_idx + 600000]  # 600KB window
    
    # Find all product links: <a ... href="//xx.aliexpress.com/item/ID.html..."
    link_pattern = re.compile(
        r'<a[^>]*class="[^"]*search-card-item[^"]*"[^>]*href="([^"]+)"[^>]*>(.*?)</a>',
        re.DOTALL
    )
    
    matches = link_pattern.findall(card_area)
    log.info(f"Found {len(matches)} card links")
    
    for href, inner in matches:
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
        
        # Extract item_id from URL
        id_match = re.search(r'/item/(\d+)\.html', href)
        if id_match:
            p["item_id"] = id_match.group(1)
        
        # Build clean URL
        if href.startswith("//"):
            p["url"] = "https:" + href
        else:
            p["url"] = href
        # Remove tracking params for clean URL
        clean_url = re.sub(r'\?.*$', '', p["url"])
        p["url"] = clean_url
        
        # Extract title from us--title-- class
        title_match = re.search(
            r'class="[^"]*us--title[^"]*"[^>]*>(.*?)</(?:span|div|p|a)>',
            inner, re.DOTALL
        )
        if title_match:
            title_text = re.sub(r'<[^>]+>', '', title_match.group(1)).strip()
            # Decode HTML entities
            import html as html_mod
            title_text = html_mod.unescape(title_text)
            if title_text and len(title_text) > 5:
                p["title"] = title_text
        
        # Extract price from pdp_npi in URL: 6@dis!CUR!price!orig_price!...
        pdp_match = re.search(r'pdp_npi=[^&]*!([A-Z]{3})!([\d.]+)!([\d.]+)', href)
        if pdp_match:
            p["currency"] = pdp_match.group(1)
            try:
                p["price"] = float(pdp_match.group(2))
                p["original_price"] = float(pdp_match.group(3))
            except ValueError:
                pass
        
        # Also look for price in the card HTML
        # Common patterns: <span class="...price...">€46.90</span>
        price_patterns = [
            r'class="[^"]*(?:price|Price)[^"]*"[^>]*>[^<]*[€$₽£]\s*([\d.,]+)',
            r'data-price="([\d.]+)"',
            r'"price"\s*:\s*"([\d.]+)"',
        ]
        for pp in price_patterns:
            pm = re.search(pp, inner)
            if pm and not p["price"]:
                try:
                    p["price"] = float(pm.group(1).replace(",", "").replace(" ", ""))
                except ValueError:
                    pass
        
        # Extract images
        img_pattern = re.compile(
            r'(?:src|data-src|data-lazy-src)="([^"]*(?:alicdn|aliexpress-media)[^"]*)"',
            re.I
        )
        for img in img_pattern.findall(inner):
            if img not in p["images"] and len(p["images"]) < 5:
                p["images"].append(img)
        
        # Extract seller (if present)
        seller_match = re.search(
            r'class="[^"]*(?:seller|store|shop)[^"]*"[^>]*>([^<]+)<',
            inner, re.I
        )
        if seller_match:
            p["seller"] = seller_match.group(1).strip()
        
        products.append(p)
    
    return products

def parse_jsonld(html):
    """Extract JSON-LD ItemList as fallback."""
    products = []
    jsonld_pattern = re.compile(
        r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>',
        re.DOTALL
    )
    for m in jsonld_pattern.finditer(html):
        try:
            data = json.loads(m.group(1).strip())
        except json.JSONDecodeError:
            continue
        
        if data.get("@type") == "ItemList":
            for item in data.get("itemListElement", []):
                url = item.get("url", "")
                id_match = re.search(r'/item/(\d+)\.html', url)
                if id_match:
                    products.append({
                        "item_id": id_match.group(1),
                        "title": item.get("name", ""),
                        "price": None,
                        "currency": None,
                        "images": [],
                        "url": url,
                        "source": "jsonld",
                    })
    return products

def main():
    log.info(f"=== AliExpress.ru HTML Parser v17 ===")
    log.info(f"Query: {SEARCH_QUERY}")
    log.info(f"URL: {WHOLESALE_URL}")
    
    # Fetch
    t0 = time.time()
    html = fetch_html(WHOLESALE_URL, HEADERS)
    fetch_time = time.time() - t0
    log.info(f"Fetch: {fetch_time:.1f}s, {len(html)} bytes")
    
    # Save raw HTML
    html_path = os.path.join(OUT, "diag_v17_page.html")
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(html)
    log.info(f"HTML saved → {html_path}")
    
    # Check locale
    locale_match = re.search(r'<html[^>]*lang="(\w+)"', html)
    log.info(f"Page locale: {locale_match.group(1) if locale_match else 'unknown'}")
    
    # Check if TMD/CAPTCHA
    if "_____tmd_____" in html or "punish" in html:
        log.warning("⚠️ TMD/CAPTCHA detected!")
    
    # Parse cards
    log.info("\n--- Parsing HTML cards ---")
    products = parse_cards_html(html)
    log.info(f"HTML cards: {len(products)}")
    
    # Parse JSON-LD
    log.info("\n--- Parsing JSON-LD ---")
    jsonld_products = parse_jsonld(html)
    log.info(f"JSON-LD items: {len(jsonld_products)}")
    
    # Merge: HTML cards first, JSON-LD as supplement (only IDs not in HTML)
    html_ids = {p["item_id"] for p in products if p["item_id"]}
    for jp in jsonld_products:
        if jp["item_id"] not in html_ids:
            products.append(jp)
    
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
        log.info(f"      price={p['price']} {p['currency']}")
        log.info(f"      imgs={len(p['images'])}")
        log.info(f"      url={str(p['url'])[:80]}")
    
    # Save results
    result = {
        "query": SEARCH_QUERY,
        "source": "aliexpress_ru_wholesale_html",
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "fetch_time_sec": round(fetch_time, 1),
        "total": len(products),
        "with_title": with_title,
        "with_price": with_price,
        "with_images": with_img,
        "products": products,
    }
    out_path = os.path.join(OUT, "diag_v17_parsed.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    log.info(f"\nResults saved → {out_path}")
    
    # Success criteria
    if len(products) >= 10 and with_price >= 5:
        log.info("✅ SUCCESS: 10+ products, 5+ with prices")
        return 0
    elif len(products) >= 10:
        log.info("⚠️ PARTIAL: 10+ products but <5 with prices")
        return 1
    else:
        log.info("❌ LOW: <10 products")
        return 2

if __name__ == "__main__":
    sys.exit(main())
