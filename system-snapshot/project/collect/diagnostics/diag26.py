"""diag26: 8 коротких стратегий по 4 площадкам. Цель — найти хотя бы одну рабочую."""
import json, re, time, sys, os
from pathlib import Path

OUT = Path(__file__).parent / "d26_results.json"
results = {}
t0 = time.time()

def mark(key, ok, detail):
    results[key] = {"ok": ok, "detail": detail, "s": round(time.time() - t0, 1)}
    print(f"[{key}] {'PASS' if ok else 'FAIL'} ({results[key]['s']}s) {detail[:200]}")

# ============ FARPOST ============
print("=== FARPOST ===")

# FP-1: CFFI category, save raw, deep-parse
try:
    from curl_cffi import requests as cr
    s = cr.Session(impersonate="chrome")
    # warmup
    r0 = s.get("https://www.farpost.ru/", timeout=10)
    time.sleep(1)
    # category
    r1 = s.get("https://www.farpost.ru/vladivostok/auto/misc/", timeout=15)
    html = r1.content.decode("windows-1251", errors="replace")
    Path("d26_fp_cat_cffi.html").write_bytes(r1.content)
    # parse: look for bulletin links /id.html
    ids = re.findall(r'/vladivostok/\d+/\S*?(\d{6,})\.html', html)
    ids2 = re.findall(r'href="[^"]*?/(\d{6,})\.html', html)
    prices = re.findall(r'data-bulletin-price=["\'](\d+)["\']', html)
    # also try: class="viewbull" or bulletin blocks
    blocks = re.findall(r'class="[^"]*viewbull[^"]*"', html)
    # check for JSON data in scripts
    scripts = re.findall(r'<script[^>]*>(.*?)</script>', html, re.DOTALL)
    json_data = [sc for sc in scripts if 'bulletin' in sc.lower() or 'items' in sc.lower()]
    detail = f"HTTP={r1.status_code} len={len(html)} ids1={len(ids)} ids2={len(ids2)} prices={len(prices)} blocks={len(blocks)} scripts_with_data={len(json_data)}"
    mark("FP:cat_cffi_v2", len(ids2) > 0 or len(prices) > 0, detail)
except Exception as e:
    mark("FP:cat_cffi_v2", False, f"EXC {e}")

# FP-2: CFFI /verify cookie flow
try:
    from curl_cffi import requests as cr
    s = cr.Session(impersonate="chrome")
    r0 = s.get("https://www.farpost.ru/", timeout=10)
    time.sleep(1)
    # Hit /verify to get cookie
    rv = s.get("https://www.farpost.ru/verify?u=%2Fvladivostok%2Fauto%2Fmisc", timeout=10, allow_redirects=False)
    cookies = dict(s.cookies)
    time.sleep(2)
    # Now try category
    r1 = s.get("https://www.farpost.ru/vladivostok/auto/misc/", timeout=15)
    html = r1.content.decode("windows-1251", errors="replace")
    ids = re.findall(r'href="[^"]*?/(\d{6,})\.html', html)
    prices = re.findall(r'data-bulletin-price=["\'](\d+)["\']', html)
    detail = f"verify_HTTP={rv.status_code} cookies={list(cookies.keys())} cat_HTTP={r1.status_code} len={len(html)} ids={len(ids)} prices={len(prices)}"
    mark("FP:verify_cookie", len(ids) > 0, detail)
except Exception as e:
    mark("FP:verify_cookie", False, f"EXC {e}")

# FP-3: CFFI mobile UA
try:
    from curl_cffi import requests as cr
    s = cr.Session(impersonate="safari")
    r0 = s.get("https://www.farpost.ru/", timeout=10)
    time.sleep(1)
    r1 = s.get("https://m.farpost.ru/vladivostok/auto/misc/", timeout=15)
    if r1.status_code in (301, 302):
        r1 = s.get("https://www.farpost.ru/vladivostok/auto/misc/", timeout=15)
    html = r1.content.decode("windows-1251", errors="replace")
    ids = re.findall(r'href="[^"]*?/(\d{6,})\.html', html)
    prices = re.findall(r'(?:data-bulletin-price|price[^>]*>)["\']?(\d{3,})', html)
    detail = f"HTTP={r1.status_code} len={len(html)} ids={len(ids)} prices={len(prices)} url={r1.url}"
    mark("FP:mobile_cffi", len(ids) > 0, detail)
except Exception as e:
    mark("FP:mobile_cffi", False, f"EXC {e}")

# FP-4: CFFI card known ID (134693091)
try:
    from curl_cffi import requests as cr
    s = cr.Session(impersonate="chrome")
    s.get("https://www.farpost.ru/", timeout=10)
    time.sleep(1)
    r1 = s.get("https://www.farpost.ru/vladivostok/134693091.html", timeout=15)
    html = r1.content.decode("windows-1251", errors="replace")
    Path("d26_fp_card_cffi.html").write_bytes(r1.content)
    jld = re.findall(r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', html, re.DOTALL)
    price = re.search(r'data-bulletin-price=["\'](\d+)["\']', html)
    title_m = re.search(r'<h1[^>]*>.*?data-field="subject"[^>]*>(.*?)</span>', html, re.DOTALL)
    detail = f"HTTP={r1.status_code} len={len(html)} jld={len(jld)} price={price.group(1) if price else 'N/A'} title={'Y' if title_m else 'N'}"
    mark("FP:card_cffi", r1.status_code == 200 and (price or len(jld) > 0), detail)
except Exception as e:
    mark("FP:card_cffi", False, f"EXC {e}")

# FP-5: Playwright with longer wait + /verify auto-pass
try:
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        b = p.chromium.launch(headless=True)
        ctx = b.new_context(user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")
        pg = ctx.new_page()
        # warmup
        pg.goto("https://www.farpost.ru/", wait_until="domcontentloaded", timeout=15000)
        pg.wait_for_timeout(2000)
        # category
        pg.goto("https://www.farpost.ru/vladivostok/auto/misc/", wait_until="domcontentloaded", timeout=20000)
        pg.wait_for_timeout(4000)  # let JS settle
        url = pg.url
        html = pg.content()
        ids = re.findall(r'href="[^"]*?/(\d{6,})\.html', html)
        prices = re.findall(r'data-bulletin-price=["\'](\d+)["\']', html)
        # check if /verify auto-redirected
        is_verify = "/verify" in url
        detail = f"url={url} verify={is_verify} len={len(html)} ids={len(ids)} prices={len(prices)}"
        mark("FP:cat_pw_wait", len(ids) > 0, detail)
        b.close()
except Exception as e:
    mark("FP:cat_pw_wait", False, f"EXC {e}")

# ============ OZON ============
print("=== OZON ===")

# OZ-1: CFFI PDP with JSON-LD
try:
    from curl_cffi import requests as cr
    s = cr.Session(impersonate="chrome")
    r1 = s.get("https://www.ozon.ru/product/malenkiy-prints-i-lis-nabor-dobryh-letnih-otkrytok-dlya-postkrossinga-pozdravleniy-i-motivatsii-3481320800/", timeout=15)
    html = r1.text
    jld_blocks = re.findall(r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', html, re.DOTALL)
    parsed = []
    for jb in jld_blocks:
        try:
            d = json.loads(jb.strip())
            if isinstance(d, dict) and d.get("@type") == "Product":
                parsed.append(d)
            elif isinstance(d, list):
                for item in d:
                    if isinstance(item, dict) and item.get("@type") == "Product":
                        parsed.append(item)
        except: pass
    price = None
    title = None
    if parsed:
        offer = parsed[0].get("offers", {})
        if isinstance(offer, list):
            offer = offer[0] if offer else {}
        price = offer.get("price")
        title = parsed[0].get("name")
    detail = f"HTTP={r1.status_code} len={len(html)} jld_products={len(parsed)} price={price} title={title}"
    mark("OZ:cffi_pdp", bool(parsed and price), detail)
except Exception as e:
    mark("OZ:cffi_pdp", False, f"EXC {e}")

# OZ-2: Composer API
try:
    from curl_cffi import requests as cr
    s = cr.Session(impersonate="chrome")
    api_url = "https://www.ozon.ru/api/composer-api.bx/page/json/v2"
    params = {"url": "/product/malenkiy-prints-i-lis-nabor-dobryh-letnih-otkrytok-dlya-postkrossinga-pozdravleniy-i-motivatsii-3481320800/"}
    r1 = s.get(api_url, params=params, timeout=15, headers={"Accept": "application/json"})
    data = r1.json() if r1.status_code == 200 else {}
    # find price in response
    txt = json.dumps(data)
    price_m = re.search(r'"price"\s*:\s*"?([\d.]+)"?', txt)
    detail = f"HTTP={r1.status_code} len={len(r1.text)} price={price_m.group(1) if price_m else 'N/A'}"
    mark("OZ:composer_api", r1.status_code == 200 and bool(price_m), detail)
except Exception as e:
    mark("OZ:composer_api", False, f"EXC {e}")

# OZ-3: Mobile page
try:
    from curl_cffi import requests as cr
    s = cr.Session(impersonate="safari")
    r1 = s.get("https://www.ozon.ru/product/3481320800/", timeout=15,
               headers={"Accept": "text/html"})
    html = r1.text
    jld = re.findall(r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', html, re.DOTALL)
    price_m = re.search(r'"price"\s*:\s*"?([\d.]+)"?', html)
    detail = f"HTTP={r1.status_code} len={len(html)} jld={len(jld)} price={price_m.group(1) if price_m else 'N/A'}"
    mark("OZ:mobile", r1.status_code == 200 and bool(price_m), detail)
except Exception as e:
    mark("OZ:mobile", False, f"EXC {e}")

# ============ WILDBERRIES ============
print("=== WILDBERRIES ===")

# WB-1: card.wb.ru v2 detail
try:
    from curl_cffi import requests as cr
    s = cr.Session(impersonate="chrome")
    r1 = s.get("https://card.wb.ru/cards/v2/detail?nm=1097975275", timeout=10,
               headers={"Accept": "application/json", "Referer": "https://www.wildberries.ru/"})
    data = r1.json() if r1.status_code == 200 else {}
    products = data.get("data", {}).get("products", [])
    price = None
    if products:
        p = products[0]
        price = p.get("sizes", [{}])[0].get("price", {}).get("basic") if p.get("sizes") else None
        if price:
            price = price / 100
    detail = f"HTTP={r1.status_code} products={len(products)} price={price}"
    mark("WB:card_v2", r1.status_code == 200 and len(products) > 0, detail)
except Exception as e:
    mark("WB:card_v2", False, f"EXC {e}")

# WB-2: catalog.wb.ru v1
try:
    from curl_cffi import requests as cr
    s = cr.Session(impersonate="chrome")
    r1 = s.get("https://catalog.wb.ru/cards/v1/detail?nm=1097975275", timeout=10,
               headers={"Accept": "application/json"})
    data = r1.json() if r1.status_code == 200 else {}
    products = data.get("data", {}).get("products", [])
    price = None
    if products:
        p = products[0]
        price = p.get("sizes", [{}])[0].get("price", {}).get("basic") if p.get("sizes") else None
    detail = f"HTTP={r1.status_code} products={len(products)} price={price}"
    mark("WB:catalog_v1", r1.status_code == 200 and len(products) > 0, detail)
except Exception as e:
    mark("WB:catalog_v1", False, f"EXC {e}")

# ============ AVITO ============
print("=== AVITO ===")

# AV-1: RSS feed
try:
    from curl_cffi import requests as cr
    s = cr.Session(impersonate="chrome")
    # Avito RSS: /vladivostok/rss/cat/... 
    r1 = s.get("https://www.avito.ru/vladivostok/rss/cat/transport/avto-moto/avto-zapchasti", timeout=15,
               headers={"Accept": "application/rss+xml, application/xml, text/xml"})
    txt = r1.text
    items = re.findall(r'<item>', txt)
    prices = re.findall(r'<price>([\d.]+)</price>', txt)
    detail = f"HTTP={r1.status_code} len={len(txt)} items={len(items)} prices={len(prices)}"
    mark("AV:rss", r1.status_code == 200 and len(items) > 0, detail)
except Exception as e:
    mark("AV:rss", False, f"EXC {e}")

# AV-2: CFFI category page
try:
    from curl_cffi import requests as cr
    s = cr.Session(impersonate="chrome")
    r1 = s.get("https://www.avito.ru/vladivostok/transport/avto-moto/avto-zapchasti", timeout=15)
    html = r1.text
    ids = re.findall(r'avito\.ru/vladivostok/\S*?/p\d+', html)
    prices = re.findall(r'"price"\s*:\s*\{[^}]*"value"\s*:\s*(\d+)', html)
    # also try data attributes
    prices2 = re.findall(r'data-price="(\d+)"', html)
    detail = f"HTTP={r1.status_code} len={len(html)} ids={len(ids)} prices={len(prices)} prices2={len(prices2)}"
    mark("AV:cat_cffi", len(ids) > 0 or len(prices) > 0, detail)
except Exception as e:
    mark("AV:cat_cffi", False, f"EXC {e}")

# ============ DONE ============
results["_meta"] = {"total_s": round(time.time() - t0, 1), "time": time.strftime("%Y-%m-%d %H:%M:%S")}
OUT.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")
print(f"\nDONE {results['_meta']['total_s']}s -> {OUT}")
