"""
Phase 2: targeted fixes based on Phase 1 results.
- Farpost: session + full HTML + live ID discovery
- WB: correct basket formula + static.wbstatic.net + live IDs
- Ozon: verify=False on composer + safari
- Avito: m.avito.ru (not IP-blocked) for live ads
"""
import json, re, time, threading
from concurrent.futures import ThreadPoolExecutor

try:
    from curl_cffi import requests as cffi_req
    USE_CFFI = True
except ImportError:
    import requests as cffi_req
    USE_CFFI = False

results = {}
lock = threading.Lock()

def log(msg):
    with lock:
        print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)

def cffi_get(url, headers=None, timeout=12, impersonate="chrome", verify=True, full=False):
    """GET → (status, headers, body). full=True → no truncation."""
    kw = {"impersonate": impersonate, "timeout": timeout}
    if headers:
        kw["headers"] = headers
    if not verify:
        kw["verify"] = False
    try:
        resp = cffi_req.get(url, **kw)
        body = resp.text if (full or resp.status_code == 200) else resp.text[:3000]
        return resp.status_code, dict(resp.headers), body
    except Exception as e:
        return 0, {}, str(e)[:500]


# ============ FARPOST (most promising) ============
def farpost_p2():
    log("=== FARPOST P2 ===")
    fp = {}
    
    # Step 1: Session-based warm-up + item fetch
    sess = cffi_req.Session(impersonate="chrome") if USE_CFFI else cffi_req.Session()
    
    # Warm-up: GET main page
    t0 = time.time()
    try:
        w = sess.get("https://www.farpost.ru/", timeout=15)
        fp["warmup"] = {"status": w.status_code, "time": round(time.time()-t0,1), 
                        "cookies": list(sess.cookies.keys()), "size": len(w.text)}
        log(f"FP warmup: {w.status_code}, cookies={list(sess.cookies.keys())}")
    except Exception as e:
        fp["warmup"] = {"error": str(e)[:200]}
    time.sleep(1)
    
    # Step 2: Fetch item with session (full HTML, no truncation)
    # Use ID that gave 200 in phase 1
    test_ids = [18530297982, 139122082]
    for tid in test_ids:
        t0 = time.time()
        try:
            r = sess.get(f"https://www.farpost.ru/vladivostok/{tid}.html", timeout=15)
            html = r.text
            entry = {"status": r.status_code, "time": round(time.time()-t0,1), "size": len(html)}
            
            # Check for actual item content
            title_m = re.search(r'<title[^>]*>([^<]+)</title>', html)
            price_m = re.search(r'data-bulletin-price=["\'](\d+)["\']', html)
            og_title = re.search(r'og:title["\'][^>]*content=["\']([^"\']+)', html)
            jsonld_m = re.search(r'application/ld\+json["\'][^>]*>(.*?)</script>', html, re.DOTALL)
            
            entry["title"] = title_m.group(1)[:100] if title_m else None
            entry["og_title"] = og_title.group(1)[:100] if og_title else None
            entry["price"] = price_m.group(1) if price_m else None
            entry["has_jsonld"] = bool(jsonld_m)
            entry["has_breadcrumb"] = 'breadcrumb' in html.lower() or 'viewbull' in html
            entry["has_bulletin"] = 'bulletin' in html.lower()
            
            # Check if it's the generic shell or real item
            entry["is_generic"] = "доска объявлений" in (title_m.group(1) if title_m else "")
            
            # Save first 2000 chars for analysis
            entry["head"] = html[:2000]
            # Save JSON-LD if present
            if jsonld_m:
                entry["jsonld"] = jsonld_m.group(1)[:1500]
            
            fp[f"item-{tid}"] = entry
            log(f"FP {tid}: {r.status_code} size={len(html)} generic={entry['is_generic']} price={entry['price']} jsonld={entry['has_jsonld']}")
        except Exception as e:
            fp[f"item-{tid}"] = {"error": str(e)[:200]}
        time.sleep(1)
    
    # Step 3: Find LIVE items from category page (full HTML)
    t0 = time.time()
    try:
        r = sess.get("https://www.farpost.ru/vladivostok/telefony/", timeout=15)
        html = r.text
        # Farpost item links: /{city}/{category}/...-{id}.html
        item_links = re.findall(r'href="(/[^"]*?/(\d{8,})\.html)"', html)
        # Also try: data-bulletin-id or similar
        data_ids = re.findall(r'data-bulletin-id=["\'](\d+)["\']', html)
        # Or: bulletin number in URL patterns
        alt_links = re.findall(r'href="([^"]*?bulletin[^"]*?)"', html, re.I)
        
        fp["cat-telefony"] = {
            "status": r.status_code, "time": round(time.time()-t0,1), "size": len(html),
            "item_links": len(item_links), "data_ids": len(data_ids),
            "sample_links": item_links[:10], "sample_data_ids": data_ids[:5],
            "alt_links": alt_links[:5],
            "head": html[:1500]
        }
        log(f"FP cat-telefony: {r.status_code} size={len(html)} links={len(item_links)} data_ids={len(data_ids)}")
    except Exception as e:
        fp["cat-telefony"] = {"error": str(e)[:200]}
    
    # Step 4: Try /item/{id} path (different from /vladivostok/{id}.html)
    t0 = time.time()
    try:
        r = sess.get("https://www.farpost.ru/item/18530297982", timeout=15)
        html = r.text
        title_m = re.search(r'<title[^>]*>([^<]+)</title>', html)
        price_m = re.search(r'data-bulletin-price=["\'](\d+)["\']', html)
        fp["item-path-18530297982"] = {
            "status": r.status_code, "time": round(time.time()-t0,1), "size": len(html),
            "title": title_m.group(1)[:100] if title_m else None,
            "price": price_m.group(1) if price_m else None,
            "is_generic": "доска объявлений" in (title_m.group(1) if title_m else ""),
            "head": html[:1500]
        }
        log(f"FP /item/ path: {r.status_code} size={len(html)}")
    except Exception as e:
        fp["item-path-18530297982"] = {"error": str(e)[:200]}
    
    log("=== FARPOST P2 DONE ===")
    return fp


# ============ WILDBERRIES ============
def wb_p2():
    log("=== WB P2 ===")
    wb = {}
    
    # S1: Correct basket formula — try vol%50+1 (common WB formula)
    # For nm=199298871: vol=1992, 1992%50=42, +1=43 → basket-43
    # Also try: (nm//100000)%50 → 1992%50=42
    nm = 199298871
    vol = nm // 100000  # 1992
    part = nm // 1000   # 199298
    
    formulas = {
        "vol%50": vol % 50,           # 42
        "vol%50+1": (vol % 50) + 1,   # 43
        "vol%50-1": (vol % 50) - 1,   # 41
        "vol%100": vol % 100,         # 92
        "vol%100+1": (vol % 100) + 1, # 93
        "nm%50": nm % 50,             # 21
        "nm%50+1": (nm % 50) + 1,     # 22
        "nm%100": nm % 100,           # 71
        "nm//10000%50": (nm//10000) % 50,  # 19929%50=29
        "nm//10000%50+1": (nm//10000) % 50 + 1,  # 30
    }
    
    tried = set()
    for name, bn in formulas.items():
        if bn in tried or bn < 1:
            continue
        tried.add(bn)
        url = f"https://basket-{bn}.wbbasket.ru/vol{vol}/part{part}/{nm}/info/{nm}-info.json"
        t0 = time.time()
        code, hdrs, body = cffi_get(url, headers={
            "Accept": "application/json",
            "Referer": "https://www.wildberries.ru/",
        }, timeout=8)
        dt = round(time.time()-t0, 1)
        wb[f"basket-{name}={bn}"] = {"status": code, "time": dt, "preview": body[:300]}
        if code == 200:
            log(f"WB basket {name}={bn}: 200!")
            break
        elif code == 0:
            log(f"WB basket {name}={bn}: DNS err")
        else:
            log(f"WB basket {name}={bn}: {code}")
    
    # S2: static.wbstatic.net (image CDN that might also serve JSON)
    for path in [
        f"https://static.wbstatic.net/card/{vol}/{nm}.json",
        f"https://static.wbstatic.net/vol{vol}/part{part}/{nm}/info/{nm}-info.json",
        f"https://wbx-content-v2.wbcontent.ru/cards/v2/detail?nm={nm}",
    ]:
        t0 = time.time()
        code, hdrs, body = cffi_get(path, headers={"Accept": "application/json"}, timeout=8)
        dt = round(time.time()-t0, 1)
        wb[f"cdn-{path[30:70]}"] = {"status": code, "time": dt, "preview": body[:300]}
        if code == 200:
            log(f"WB CDN 200: {path[:50]}")
    
    # S3: Try a VERY popular WB product (e.g., iPhone case, known to exist)
    # Use search-like approach: WB has /catalog/{id}/detail.aspx
    # But HTML gives 498... Try the "web" API that the SPA uses
    popular_ids = [
        228676543,  # random recent
        155000000,  # older
        100000000,  # very old
    ]
    for pid in popular_ids:
        v2 = pid // 100000
        p2 = pid // 1000
        # Try a few basket numbers
        for bn in [v2 % 50, (v2 % 50) + 1, v2 % 50 + 25]:
            if bn < 1:
                continue
            url = f"https://basket-{bn}.wbbasket.ru/vol{v2}/part{p2}/{pid}/info/{pid}-info.json"
            t0 = time.time()
            code, hdrs, body = cffi_get(url, timeout=8)
            dt = round(time.time()-t0, 1)
            wb[f"alt-{pid}-b{bn}"] = {"status": code, "time": dt, "preview": body[:200]}
            if code == 200:
                log(f"WB alt {pid} basket-{bn}: 200!")
                break
        else:
            continue
        break
    
    # S4: WB SPA API — the frontend calls /api/... or similar
    spa_urls = [
        f"https://www.wildberries.ru/api/catalog/{nm}/info",
        f"https://www.wildberries.ru/api/cards/v2/detail?nm={nm}",
        f"https://wbx-content-v2.wbcontent.ru/cards/v2/detail?nm={nm}&spp=30&appType=1&curr=rub&lang=ru",
    ]
    for url in spa_urls:
        t0 = time.time()
        code, hdrs, body = cffi_get(url, headers={
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "ru-RU,ru;q=0.9",
            "Referer": f"https://www.wildberries.ru/catalog/{nm}/detail.aspx",
            "Origin": "https://www.wildberries.ru",
        }, timeout=10, impersonate="chrome")
        dt = round(time.time()-t0, 1)
        wb[f"spa-{url[30:70]}"] = {"status": code, "time": dt, "preview": body[:400]}
        if code == 200:
            log(f"WB SPA 200: {url[:50]}")
    
    # S5: Try with different impersonation (safari, firefox)
    for imp in ["safari", "firefox", "chrome124"]:
        t0 = time.time()
        code, hdrs, body = cffi_get(
            f"https://card.wb.ru/cards/v2/detail?nm={nm}",
            headers={"Accept": "application/json", "Referer": "https://www.wildberries.ru/"},
            timeout=10, impersonate=imp)
        dt = round(time.time()-t0, 1)
        wb[f"price-imp-{imp}"] = {"status": code, "time": dt, "preview": body[:300]}
        if code == 200:
            log(f"WB price {imp}: 200!")
            break
    
    log("=== WB P2 DONE ===")
    return wb


# ============ OZON ============
def ozon_p2():
    log("=== OZON P2 ===")
    oz = {}
    pid = 1600562267
    
    # S1: Composer API with verify=False (fixes SSL curl 60)
    composer_urls = [
        f"https://api.ozon.ru/composer-api.bx/page/json/v2?url=/product/{pid}/",
        f"https://api.ozon.ru/composer-api.bx/page/json?url=/product/{pid}/",
    ]
    for url in composer_urls:
        t0 = time.time()
        code, hdrs, body = cffi_get(url, headers={
            "Accept": "application/json",
            "Accept-Language": "ru-RU,ru;q=0.9",
        }, timeout=12, impersonate="chrome", verify=False)
        dt = round(time.time()-t0, 1)
        oz[f"composer-verify0-{url[20:60]}"] = {"status": code, "time": dt, "preview": body[:500]}
        if code == 200:
            log(f"Ozon composer verify=0: 200!")
    
    # S2: Safari impersonation on HTML
    for imp in ["safari", "safari15_3", "firefox"]:
        t0 = time.time()
        code, hdrs, body = cffi_get(
            f"https://www.ozon.ru/product/{pid}/",
            headers={"Accept": "text/html", "Accept-Language": "ru-RU,ru;q=0.9"},
            timeout=12, impersonate=imp)
        dt = round(time.time()-t0, 1)
        og = re.findall(r'og:(title|description|image)["\'][^>]*content=["\']([^"\']+)', body, re.I)
        oz[f"html-{imp}"] = {"status": code, "time": dt, "size": len(body), "og": og[:3], "preview": body[:400]}
        if code == 200:
            log(f"Ozon {imp}: 200!")
    
    # S3: Mobile with different path
    mobile_urls = [
        f"https://m.ozon.ru/product/{pid}/",
        f"https://m.ozon.ru/product/{pid}",
        f"https://www.ozon.ru/product/{pid}/?from=mobile",
    ]
    for url in mobile_urls:
        t0 = time.time()
        code, hdrs, body = cffi_get(url, headers={
            "Accept": "text/html",
            "Accept-Language": "ru-RU,ru;q=0.9",
            "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15",
        }, timeout=12, impersonate="safari")
        dt = round(time.time()-t0, 1)
        oz[f"mobile-{url[30:65]}"] = {"status": code, "time": dt, "size": len(body), "preview": body[:300]}
        if code == 200:
            log(f"Ozon mobile 200: {url[:50]}")
    
    # S4: Try a different (popular) product ID
    for test_pid in [2000000000, 1800000000, 1900000000]:
        t0 = time.time()
        code, hdrs, body = cffi_get(
            f"https://www.ozon.ru/product/{test_pid}/",
            headers={"Accept": "text/html", "Accept-Language": "ru-RU,ru;q=0.9"},
            timeout=10, impersonate="chrome")
        dt = round(time.time()-t0, 1)
        oz[f"alt-{test_pid}"] = {"status": code, "time": dt, "size": len(body), "preview": body[:300]}
        if code == 200:
            log(f"Ozon alt {test_pid}: 200!")
            break
    
    # S5: Ozon search page (might be less protected)
    t0 = time.time()
    code, hdrs, body = cffi_get(
        "https://www.ozon.ru/search/?text=iphone&from_global=true",
        headers={"Accept": "text/html", "Accept-Language": "ru-RU,ru;q=0.9"},
        timeout=12, impersonate="chrome")
    dt = round(time.time()-t0, 1)
    product_links = re.findall(r'/product/[a-z0-9\-]*(\d+)/', body)
    oz["search"] = {"status": code, "time": dt, "size": len(body), 
                    "products": len(product_links), "sample": product_links[:5],
                    "preview": body[:400]}
    if code == 200:
        log(f"Ozon search: 200, {len(product_links)} products")
    
    log("=== OZON P2 DONE ===")
    return oz


# ============ AVITO ============
def avito_p2():
    log("=== AVITO P2 ===")
    av = {}
    
    # Key insight: m.avito.ru gives 404 (not 429!) → server alive, just dead ad
    # Strategy: find LIVE ads via m.avito.ru category/search
    
    # S1: m.avito.ru category pages
    cat_urls = [
        "https://m.avito.ru/moscow/telefony",
        "https://m.avito.ru/moscow/telefony/iphone",
        "https://m.avito.ru/vladivostok/telefony",
        "https://m.avito.ru/moscow/elektronika",
    ]
    for url in cat_urls:
        t0 = time.time()
        code, hdrs, body = cffi_get(url, headers={
            "Accept": "text/html",
            "Accept-Language": "ru-RU,ru;q=0.9",
        }, timeout=12, impersonate="chrome")
        dt = round(time.time()-t0, 1)
        # Find ad IDs: /{city}/{category}/{id}
        ad_ids = re.findall(r'avito\.ru/[^"]*?/(\d{8,})["\s/]', body)
        # Also: data-id or similar
        data_ids = re.findall(r'data-id=["\'](\d+)["\']', body)
        
        av[f"cat-{url[35:70]}"] = {
            "status": code, "time": dt, "size": len(body),
            "ad_ids": len(ad_ids), "data_ids": len(data_ids),
            "sample": ad_ids[:10] if ad_ids else data_ids[:10],
            "preview": body[:500]
        }
        if code == 200 and (ad_ids or data_ids):
            log(f"Avito {url[35:60]}: 200, {len(ad_ids)}+{len(data_ids)} ads")
            break
        else:
            log(f"Avito {url[35:60]}: {code}")
    
    # S2: m.avito.ru search
    t0 = time.time()
    code, hdrs, body = cffi_get(
        "https://m.avito.ru/moscow/search?q=iphone",
        headers={"Accept": "text/html", "Accept-Language": "ru-RU,ru;q=0.9"},
        timeout=12, impersonate="chrome")
    dt = round(time.time()-t0, 1)
    ad_ids_s = re.findall(r'avito\.ru/[^"]*?/(\d{8,})["\s/]', body)
    av["search-mobile"] = {"status": code, "time": dt, "size": len(body),
                           "ads": len(ad_ids_s), "sample": ad_ids_s[:10],
                           "preview": body[:400]}
    if code == 200:
        log(f"Avito search-mobile: 200, {len(ad_ids_s)} ads")
    
    # S3: Fetch a LIVE ad (from category or search results)
    live_id = None
    for key in av:
        if "sample" in av[key] and av[key]["sample"]:
            live_id = av[key]["sample"][0]
            break
    if live_id:
        t0 = time.time()
        code, hdrs, body = cffi_get(
            f"https://m.avito.ru/ad/{live_id}",
            headers={"Accept": "text/html", "Accept-Language": "ru-RU,ru;q=0.9"},
            timeout=12, impersonate="chrome", full=True)
        dt = round(time.time()-t0, 1)
        # Extract data
        og_title = re.search(r'og:title["\'][^>]*content=["\']([^"\']+)', body, re.I)
        og_desc = re.search(r'og:description["\'][^>]*content=["\']([^"\']+)', body, re.I)
        jsonld = re.search(r'application/ld\+json["\'][^>]*>(.*?)</script>', body, re.DOTALL)
        price_m = re.search(r'"price"\s*:\s*"?(\d+)', body)
        
        av[f"live-{live_id}"] = {
            "status": code, "time": dt, "size": len(body),
            "og_title": og_title.group(1)[:100] if og_title else None,
            "og_desc": og_desc.group(1)[:100] if og_desc else None,
            "has_jsonld": bool(jsonld),
            "price": price_m.group(1) if price_m else None,
            "head": body[:2000]
        }
        if jsonld:
            av[f"live-{live_id}"]["jsonld"] = jsonld.group(1)[:1500]
        log(f"Avito live {live_id}: {code} size={len(body)} title={av[f'live-{live_id}']['og_title']}")
    else:
        log("Avito: no live ID found from categories")
    
    # S4: Try www.avito.ru with m.avito.ru cookie (if we got any)
    # Actually: try a known recent ad ID format (2024+ ads are 11 digits starting with 1)
    for test_id in [1100000000, 1099000000, 1098000000]:
        t0 = time.time()
        code, hdrs, body = cffi_get(
            f"https://m.avito.ru/ad/{test_id}",
            headers={"Accept": "text/html", "Accept-Language": "ru-RU,ru;q=0.9"},
            timeout=10, impersonate="chrome")
        dt = round(time.time()-t0, 1)
        av[f"test-{test_id}"] = {"status": code, "time": dt, "size": len(body), "preview": body[:300]}
        if code == 200:
            log(f"Avito test {test_id}: 200!")
            break
    
    log("=== AVITO P2 DONE ===")
    return av


# ============ MAIN ============
def main():
    t0 = time.time()
    log(f"PHASE2 START (cffi={USE_CFFI})")
    
    with ThreadPoolExecutor(max_workers=4) as pool:
        f_fp = pool.submit(farpost_p2)
        f_wb = pool.submit(wb_p2)
        f_oz = pool.submit(ozon_p2)
        f_av = pool.submit(avito_p2)
        
        results["FARPOST"] = f_fp.result()
        results["WB"] = f_wb.result()
        results["OZON"] = f_oz.result()
        results["AVITO"] = f_av.result()
    
    elapsed = round(time.time()-t0, 1)
    log(f"PHASE2 DONE in {elapsed}s")
    
    with open("diag_phase2_result.json", "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    log("Saved: diag_phase2_result.json")
    
    # Summary
    log("--- SUMMARY ---")
    for plat in ["FARPOST", "WB", "OZON", "AVITO"]:
        ok = [k for k, v in results[plat].items() if isinstance(v, dict) and v.get("status") == 200]
        log(f"  {plat}: {len(ok)}/{len(results[plat])} → {ok[:4]}")


if __name__ == "__main__":
    main()
