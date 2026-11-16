"""DIAG V122: WB Image CDN + Price Alt + Farpost Live Listing (2026-11-12)"""
import json, re, time, sys, os
from pathlib import Path

OUT = Path(__file__).parent / "diag_v122_result.log"
lines = []
def log(msg=""):
    lines.append(msg)
    print(msg, flush=True)

def save():
    OUT.write_text("\n".join(lines), encoding="utf-8")

log("=" * 64)
log("DIAG V122: WB IMAGES + PRICE ALT + FARPOST LIVE  (2026-11-12)")
log("=" * 64)

# ============================================================
# PART 1: WB IMAGE CDN PATTERNS
# ============================================================
log("\n" + "=" * 64)
log("PART 1: WB IMAGE CDN PATTERNS")
log("=" * 64)

nm_id = 259671175
imt_id = 236868886
vol = nm_id // 100000
part = nm_id // 1000
log(f"nm_id={nm_id}, imt_id={imt_id}, vol={vol}, part={part}")

try:
    from curl_cffi import requests as cffi_req
    def wb_get(url, timeout=10):
        r = cffi_req.get(url, impersonate="chrome", timeout=timeout)
        return r.status_code, len(r.content), r.headers.get("content-type", "")
    log("HTTP client: curl_cffi (impersonate=chrome)")
except ImportError:
    import requests as req
    def wb_get(url, timeout=10):
        h = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"}
        r = req.get(url, headers=h, timeout=timeout)
        return r.status_code, len(r.content), r.headers.get("content-type", "")
    log("HTTP client: requests (fallback)")

# --- 1A: basket-XX hosts ---
log("\n--- 1A: basket-XX.wbbasket.ru ---")
basket_host = None
for n in range(1, 61):
    host = f"basket-{n:02d}.wbbasket.ru"
    url = f"https://{host}/vol{vol}/part{part}/{nm_id}/info/ru/card.json"
    try:
        code, sz, ct = wb_get(url, timeout=6)
        if code == 200:
            basket_host = n
            log(f"  card.json on {host} (200, {sz}B)")
            break
        elif code != 404:
            log(f"  {host}: {code} {sz}B")
    except Exception as e:
        log(f"  {host}: ERR {e}")
        if n > 10:
            break

log(f"  → basket host: {f'basket-{basket_host:02d}' if basket_host else 'NOT FOUND'}")

# --- 1B: Image patterns on basket CDN ---
log("\n--- 1B: Image patterns on basket CDN ---")
img_patterns = [
    # (label, path_template)
    ("S800.jpg",        "/vol{vol}/part{part}/{nm}/images/S800/S800.jpg"),
    ("S120.jpg",        "/vol{vol}/part{part}/{nm}/images/S120/S120.jpg"),
    ("S100.jpg",        "/vol{vol}/part{part}/{nm}/images/S100/S100.jpg"),
    ("S200.jpg",        "/vol{vol}/part{part}/{nm}/images/S200/S200.jpg"),
    ("S400.jpg",        "/vol{vol}/part{part}/{nm}/images/S400/S400.jpg"),
    ("1.jpg",           "/vol{vol}/part{part}/{nm}/images/S800/1.jpg"),
    ("2.jpg",           "/vol{vol}/part{part}/{nm}/images/S800/2.jpg"),
    ("imt_id.jpg",      "/vol{vol}/part{part}/{nm}/images/S800/{imt}.jpg"),
    ("imt_S120",        "/vol{vol}/part{part}/{nm}/images/S120/{imt}.jpg"),
    ("no_size",         "/vol{vol}/part{part}/{nm}/images/{imt}.jpg"),
    ("webp",            "/vol{vol}/part{part}/{nm}/images/S800/S800.webp"),
    ("S800_1",          "/vol{vol}/part{part}/{nm}/images/S800/S800_1.jpg"),
]

if basket_host:
    host = f"basket-{basket_host:02d}.wbbasket.ru"
    for label, tmpl in img_patterns:
        path = tmpl.format(vol=vol, part=part, nm=nm_id, imt=imt_id)
        url = f"https://{host}{path}"
        try:
            code, sz, ct = wb_get(url, timeout=8)
            mark = "OK" if code == 200 and sz > 500 else "xx"
            log(f"  [{mark}] {code} {sz:>7}B {ct:<20} {path}")
        except Exception as e:
            log(f"  [ER] {label}: {e}")
        time.sleep(0.3)

# --- 1C: ir-XX hosts (alternative CDN) ---
log("\n--- 1C: ir-XX.wbbasket.ru ---")
for n in [1, 5, 10, 16, 20, 30, 40, 50, 60]:
    host = f"ir-{n:02d}.wbbasket.ru"
    path = f"/vol{vol}/part{part}/{nm_id}/images/S800/S800.jpg"
    url = f"https://{host}{path}"
    try:
        code, sz, ct = wb_get(url, timeout=6)
        mark = "OK" if code == 200 and sz > 500 else "xx"
        log(f"  [{mark}] {code} {sz:>7}B {ct:<20} {host}{path[:50]}")
    except Exception as e:
        log(f"  [ER] {host}: {str(e)[:60]}")
    time.sleep(0.3)

# --- 1D: wbxstatic / wbstatic ---
log("\n--- 1D: wbxstatic / wbstatic ---")
static_hosts = [
    "wbxstatic.ru", "static.wbstatic.net", "ir.wbxstatic.ru",
    "static1.wbstatic.net", "static2.wbstatic.net",
]
for host in static_hosts:
    path = f"/vol{vol}/part{part}/{nm_id}/images/S800/S800.jpg"
    url = f"https://{host}{path}"
    try:
        code, sz, ct = wb_get(url, timeout=6)
        mark = "OK" if code == 200 and sz > 500 else "xx"
        log(f"  [{mark}] {code} {sz:>7}B {ct:<20} {host}{path[:40]}")
    except Exception as e:
        log(f"  [ER] {host}: {str(e)[:60]}")
    time.sleep(0.3)

# --- 1E: Try WB HTML page for og:image / embedded image URLs ---
log("\n--- 1E: WB HTML page (og:image, embedded) ---")
html_url = f"https://www.wildberries.ru/catalog/{nm_id}/detail.aspx"
try:
    r = cffi_req.get(html_url, impersonate="chrome", timeout=20) if 'cffi_req' in dir() else None
    if r is None:
        import requests as req2
        h = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"}
        r = req2.get(html_url, headers=h, timeout=20)
    log(f"  HTML: {r.status_code}, {len(r.content)}B")
    html = r.text if hasattr(r, 'text') else r.content.decode('utf-8', 'ignore')
    
    # og:image
    for m in re.finditer(r'<meta[^>]*property=["\']og:image["\'][^>]*content=["\']([^"\']+)', html, re.I):
        log(f"  og:image: {m.group(1)}")
    
    # All wbbasket/wbstatic image URLs in HTML
    img_urls = set(re.findall(r'(https://[^"\']*wbbasket\.ru[^"\']*\.(?:jpg|png|webp))', html, re.I))
    img_urls |= set(re.findall(r'(https://[^"\']*wbstatic\.net[^"\']*\.(?:jpg|png|webp))', html, re.I))
    img_urls |= set(re.findall(r'(https://[^"\']*wbxstatic\.ru[^"\']*\.(?:jpg|png|webp))', html, re.I))
    log(f"  Image URLs in HTML: {len(img_urls)}")
    for u in sorted(img_urls)[:15]:
        log(f"    {u}")
    
    # Price in HTML
    price_m = re.findall(r'data-price=["\'](\d+)["\']', html)
    og_price = re.findall(r'og:price:amount["\'][^>]*content=["\']([\d.]+)', html, re.I)
    log(f"  data-price attrs: {price_m[:5]}")
    log(f"  og:price:amount: {og_price[:3]}")
    
    # JSON-LD
    for m in re.finditer(r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', html, re.DOTALL | re.I):
        try:
            ld = json.loads(m.group(1))
            if isinstance(ld, dict) and ld.get("@type") == "Product":
                log(f"  JSON-LD Product: name={ld.get('name','?')[:50]}")
                offers = ld.get("offers", {})
                log(f"  JSON-LD offers: {json.dumps(offers, ensure_ascii=False)[:200]}")
        except:
            pass
except Exception as e:
    log(f"  HTML error: {e}")

# --- 1F: Price via search.wb.ru (different approach) ---
log("\n--- 1F: Price via search.wb.ru ---")
search_urls = [
    f"https://search.wb.ru/exactmatch/ru/common/v5/search?appType=1&curr=rub&dest=-1257786&spp=30&query={nm_id}",
    f"https://search.wb.ru/suggest/v1/search?appType=1&curr=rub&dest=-1257786&query={nm_id}",
    f"https://card.wb.ru/cards/v2/detail?curr=rub&nm={nm_id}",
    f"https://card.wb.ru/cards/detail?curr=rub&nm={nm_id}",
]
for url in search_urls:
    try:
        code, sz, ct = wb_get(url, timeout=10)
        log(f"  [{code}] {sz:>6}B {ct:<20} {url[:80]}")
        if code == 200 and sz > 100:
            # Try to parse
            pass
    except Exception as e:
        log(f"  [ER] {url[:60]}: {str(e)[:50]}")
    time.sleep(0.5)

# ============================================================
# PART 2: FARPOST LIVE LISTING
# ============================================================
log("\n" + "=" * 64)
log("PART 2: FARPOST LIVE LISTING DISCOVERY + PARSE")
log("=" * 64)

try:
    from curl_cffi import requests as fp_req
    fp_session = fp_req.Session(impersonate="chrome")
except ImportError:
    import requests as fp_req
    fp_session = fp_req.Session()
    fp_session.headers.update({"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"})

# 2A: Warmup
log("\n--- 2A: Warmup ---")
try:
    r = fp_session.get("https://www.farpost.ru/", timeout=15)
    log(f"  GET /: {r.status_code}, {len(r.content)}B, cookies={list(fp_session.cookies.keys())}")
except Exception as e:
    log(f"  Warmup error: {e}")
time.sleep(1)

# 2B: Category page → find live listings
log("\n--- 2B: Category page (find live listings) ---")
cat_urls = [
    "https://www.farpost.ru/krasnoyarsk/zapchasti/",
    "https://www.farpost.ru/krasnoyarsk/avto/zapchasti/",
    "https://www.farpost.ru/ni/krasnoyarsk/zapchasti/",
]
live_listings = []
for cat_url in cat_urls:
    try:
        r = fp_session.get(cat_url, timeout=15)
        log(f"  {cat_url}: {r.status_code}, {len(r.content)}B")
        if r.status_code != 200:
            continue
        html = r.text
        # Find bulletin links: /bulletin/123456 or /{city}/{cat}/{slug}-{id}.html
        # Pattern 1: /bulletin/{id}
        for m in re.finditer(r'href="(/bulletin/(\d+))"', html):
            live_listings.append({"url": f"https://www.farpost.ru{m.group(1)}", "id": m.group(2), "type": "bulletin"})
        # Pattern 2: full path with numeric id at end
        for m in re.finditer(r'href="(/[^"]*?/(\d{8,}))?\.?html?"', html):
            path = m.group(1)
            if path and not path.startswith("/bulletin"):
                live_listings.append({"url": f"https://www.farpost.ru{path}", "id": m.group(2) or "?", "type": "path"})
        # Pattern 3: data-bulletin-id or similar
        for m in re.finditer(r'data-bulletin-id=["\'](\d+)["\']', html):
            live_listings.append({"url": f"https://www.farpost.ru/bulletin/{m.group(1)}", "id": m.group(1), "type": "data-attr"})
        
        log(f"  Found {len(live_listings)} listings so far")
        if len(live_listings) >= 5:
            break
    except Exception as e:
        log(f"  {cat_url}: ERR {e}")
    time.sleep(1)

# Deduplicate
seen_ids = set()
unique_listings = []
for l in live_listings:
    if l["id"] not in seen_ids:
        seen_ids.add(l["id"])
        unique_listings.append(l)

log(f"  Unique listings: {len(unique_listings)}")
for l in unique_listings[:8]:
    log(f"    [{l['type']}] id={l['id']} url={l['url'][:70]}")

# 2C: Test parse on first 3 live listings
log("\n--- 2C: Test parse on live listings ---")
for i, listing in enumerate(unique_listings[:3]):
    url = listing["url"]
    lid = listing["id"]
    log(f"\n  [{i+1}] {url}")
    try:
        r = fp_session.get(url, timeout=15)
        log(f"      HTTP {r.status_code}, {len(r.content)}B")
        if r.status_code != 200:
            continue
        html = r.text
        
        # Title
        title_m = re.search(r'<h1[^>]*>.*?<span[^>]*data-field="subject"[^>]*>(.*?)</span>', html, re.DOTALL)
        if not title_m:
            title_m = re.search(r'<title[^>]*>([^<]+)</title>', html, re.I)
        title = re.sub(r'<[^>]+>', '', title_m.group(1)).strip() if title_m else "?"
        log(f"      Title: {title[:60]}")
        
        # Price
        price_m = re.search(r'data-bulletin-price=["\'](\d+)["\']', html)
        if price_m:
            log(f"      Price (attr): {int(price_m.group(1))} RUB")
        else:
            price_m = re.search(r'class="viewbull-summary-price__value[^"]*"[^>]*>([\d\s\u00a0]+)', html)
            if price_m:
                log(f"      Price (class): {price_m.group(1).strip()}")
            else:
                log(f"      Price: NOT FOUND")
        
        # JSON-LD
        jsonld_found = False
        for m in re.finditer(r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', html, re.DOTALL):
            try:
                ld = json.loads(m.group(1))
                if isinstance(ld, dict) and ld.get("@type") == "Product":
                    jsonld_found = True
                    log(f"      JSON-LD Product: name={ld.get('name','?')[:40]}")
                    offers = ld.get("offers", {})
                    if isinstance(offers, dict):
                        log(f"      JSON-LD price: {offers.get('price','?')} {offers.get('priceCurrency','?')}")
                    break
            except:
                pass
        if not jsonld_found:
            log(f"      JSON-LD: no Product block")
        
        # Images (data-image-info)
        gallery_count = len(re.findall(r'data-image-info="(\{.*?\})"', html))
        log(f"      Gallery (data-image-info): {gallery_count} items")
        
        # Lightbox
        lightbox_m = re.search(r'baza\.lightbox\.init\(\s*(\[[\s\S]*?\])\s*\)', html)
        if lightbox_m:
            try:
                items = json.loads(lightbox_m.group(1))
                log(f"      Lightbox: {len(items)} items")
            except:
                log(f"      Lightbox: parse error")
        
        # og:image
        og_img = re.search(r'og:image["\'][^>]*content=["\']([^"\']+)', html, re.I)
        if og_img:
            log(f"      og:image: {og_img.group(1)[:60]}")
        
        # Specs
        spec_count = len(re.findall(r'class="field\s+viewbull-field__container', html))
        log(f"      Specs blocks: {spec_count}")
        
        # Description
        desc_m = re.search(r'<div\s+class="bulletinText[^"]*"[^>]*>(.*?)</div>', html, re.DOTALL)
        if desc_m:
            desc_text = re.sub(r'<[^>]+>', ' ', desc_m.group(1)).strip()[:80]
            log(f"      Desc: {desc_text}...")
        
        # Seller
        seller_m = re.search(r'<span\s+class="userNick[^"]*"[^>]*>\s*<a\s+href="(/user/[^/]+/?)">(.*?)</a>', html, re.DOTALL)
        if seller_m:
            log(f"      Seller: {re.sub(r'<[^>]+>','',seller_m.group(2)).strip()}")
        
    except Exception as e:
        log(f"      ERROR: {e}")
    time.sleep(2)

# 2D: Test the ?u= redirect with cookies
log("\n--- 2D: ?u= redirect with session cookies ---")
if unique_listings:
    test_url = unique_listings[0]["url"]
    path_part = test_url.replace("https://www.farpost.ru", "")
    redirect_url = f"https://www.farpost.ru?u={path_part}&f=1"
    try:
        r = fp_session.get(redirect_url, timeout=15, allow_redirects=True)
        log(f"  {redirect_url[:70]}")
        log(f"  → {r.status_code}, final={r.url[:70]}")
        if r.status_code == 200:
            html = r.text
            title_m = re.search(r'<title[^>]*>([^<]+)</title>', html, re.I)
            log(f"  Title: {title_m.group(1)[:60] if title_m else '?'}")
            # Check if it's the actual listing or homepage
            has_price = 'data-bulletin-price' in html
            has_gallery = 'data-image-info' in html
            log(f"  Has price attr: {has_price}, Has gallery: {has_gallery}")
    except Exception as e:
        log(f"  Error: {e}")

# ============================================================
# PART 3: WB PRICE - ALTERNATIVE (via basket info)
# ============================================================
log("\n" + "=" * 64)
log("PART 3: WB PRICE ALTERNATIVES")
log("=" * 64)

# Some basket hosts have a /info/ru/price.json or similar
price_endpoints = [
    f"/vol{vol}/part{part}/{nm_id}/info/ru/price.json",
    f"/vol{vol}/part{part}/{nm_id}/info/ru/sizes.json",
    f"/vol{vol}/part{part}/{nm_id}/info/ru/stock.json",
    f"/vol{vol}/part{part}/{nm_id}/info/ru/seller.json",
]
if basket_host:
    host = f"basket-{basket_host:02d}.wbbasket.ru"
    for path in price_endpoints:
        url = f"https://{host}{path}"
        try:
            code, sz, ct = wb_get(url, timeout=8)
            mark = "OK" if code == 200 else f"{code}"
            log(f"  [{mark}] {sz:>6}B {ct:<20} {path}")
            if code == 200 and sz > 10:
                # Save content for inspection
                try:
                    r2 = cffi_req.get(url, impersonate="chrome", timeout=8) if 'cffi_req' in dir() else None
                    if r2:
                        log(f"      Content: {r2.text[:200]}")
                except:
                    pass
        except Exception as e:
            log(f"  [ER] {path}: {str(e)[:50]}")
        time.sleep(0.5)

# Also try the "common" API that the website uses
log("\n  --- WB web API (different paths) ---")
web_apis = [
    f"https://www.wildberries.ru/web/api/catalog/cards/v1?nm={nm_id}",
    f"https://www.wildberries.ru/web/api/catalog/product/{nm_id}",
    f"https://www.wildberries.ru/web/api/catalog/cards/v1/detail?nm={nm_id}&curr=rub",
    f"https://www.wildberries.ru/web/api/catalog/cards/v2?nm={nm_id}",
]
for url in web_apis:
    try:
        code, sz, ct = wb_get(url, timeout=10)
        log(f"  [{code}] {sz:>6}B {ct:<20} {url[40:]}")
        if code == 200 and sz > 50:
            try:
                r2 = cffi_req.get(url, impersonate="chrome", timeout=10) if 'cffi_req' in dir() else None
                if r2:
                    log(f"      {r2.text[:200]}")
            except:
                pass
    except Exception as e:
        log(f"  [ER] {url[40:]}: {str(e)[:50]}")
    time.sleep(0.5)

log("\n" + "=" * 64)
log("DIAG V122 DONE")
log("=" * 64)
save()
print(f"\nSaved to {OUT}")
