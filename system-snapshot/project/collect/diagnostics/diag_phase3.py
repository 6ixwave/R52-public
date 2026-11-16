"""
Phase 3: Surgical extraction.
- Farpost: dump full HTML, find real data structure (198KB page has data!)
- WB: session warm-up → detail.aspx → window.__ state
- Ozon: X-Request-Context header + entrypoint.shs
- Avito: m.avito.ru (reachable!) — find live ads via RSS/share
"""
import json, re, time, os, threading
from concurrent.futures import ThreadPoolExecutor

try:
    from curl_cffi import requests as cffi_req
    USE_CFFI = True
except ImportError:
    import requests as cffi_req
    USE_CFFI = False

OUT = "C:\\Users\\aleks\\Downloads\\R52-2.12\\R52-2.12"
results = {}
lock = threading.Lock()

def log(msg):
    with lock:
        print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)

def save_html(name, html):
    path = os.path.join(OUT, name)
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)
    log(f"Saved {name} ({len(html)} bytes)")
    return path


# ============ FARPOST ============
def farpost_p3():
    log("=== FARPOST P3 ===")
    fp = {}
    sess = cffi_req.Session(impersonate="chrome")

    # Warm-up
    sess.get("https://www.farpost.ru/", timeout=15)
    time.sleep(1)

    # 1. Item page — full dump
    tid = 18530297982
    r = sess.get(f"https://www.farpost.ru/vladivostok/{tid}.html", timeout=20)
    html = r.text
    save_html("fp_item_full.html", html)
    
    # Extract: all script blocks
    scripts = re.findall(r'<script[^>]*>(.*?)</script>', html, re.DOTALL)
    big_scripts = [s[:500] for s in scripts if len(s.strip()) > 50]
    
    # Extract: data-* attributes
    data_attrs = re.findall(r'(data-[a-z\-]+)=["\']([^"\']{3,80})["\']', html)
    unique_data = list(set(data_attrs))[:50]
    
    # Extract: class names with "bulletin", "item", "price", "card"
    relevant_classes = re.findall(r'class="([^"]*(?:bulletin|item|price|card|product|offer)[^"]*)"', html, re.I)
    unique_classes = list(set(relevant_classes))[:30]
    
    # Extract: all hrefs (for item links)
    hrefs = re.findall(r'href="([^"]+)"', html)
    item_hrefs = [h for h in hrefs if re.search(r'\d{6,}', h) and '.html' in h]
    
    # Extract: window.* variables
    window_vars = re.findall(r'window\.([a-zA-Z_$][a-zA-Z0-9_$]*)\s*=', html)
    
    # Extract: JSON-like blocks in scripts
    json_blocks = re.findall(r'(?:var|let|const)\s+\w+\s*=\s*(\{[^;]{100,500})', html)
    
    fp["item-analysis"] = {
        "status": r.status_code,
        "size": len(html),
        "scripts_count": len(scripts),
        "big_scripts": big_scripts[:5],
        "data_attrs_sample": unique_data[:20],
        "relevant_classes": unique_classes[:20],
        "item_hrefs": item_hrefs[:15],
        "window_vars": list(set(window_vars))[:20],
        "json_blocks": json_blocks[:3],
    }
    log(f"FP item: {r.status_code} size={len(html)} scripts={len(scripts)} data_attrs={len(unique_data)} item_hrefs={len(item_hrefs)}")

    # 2. Category page — find live items
    r2 = sess.get("https://www.farpost.ru/vladivostok/telefony/", timeout=20)
    html2 = r2.text
    save_html("fp_cat_full.html", html2)
    
    cat_hrefs = re.findall(r'href="([^"]+)"', html2)
    cat_items = [h for h in cat_hrefs if re.search(r'\d{6,}', h) and '.html' in h and 'telefony' not in h.lower()]
    
    # Also look for data attributes with IDs
    cat_data_ids = re.findall(r'data-(?:id|bulletin|item)[a-z\-]*=["\'](\d{6,})["\']', html2)
    
    # Look for JSON in category scripts
    cat_scripts = re.findall(r'<script[^>]*>(.*?)</script>', html2, re.DOTALL)
    cat_json = [s[:300] for s in cat_scripts if '"id"' in s and len(s) > 100]
    
    fp["cat-analysis"] = {
        "status": r2.status_code,
        "size": len(html2),
        "total_hrefs": len(cat_hrefs),
        "item_hrefs": cat_items[:20],
        "data_ids": cat_data_ids[:15],
        "json_scripts": cat_json[:3],
    }
    log(f"FP cat: {r2.status_code} size={len(html2)} item_hrefs={len(cat_items)} data_ids={len(cat_data_ids)}")

    # 3. If we found live items, fetch one
    live_id = None
    for h in cat_items[:10]:
        m = re.search(r'(\d{6,})\.html', h)
        if m:
            live_id = m.group(1)
            break
    if live_id:
        time.sleep(1)
        r3 = sess.get(f"https://www.farpost.ru/vladivostok/{live_id}.html", timeout=20)
        html3 = r3.text
        save_html(f"fp_live_{live_id}.html", html3)
        price_m = re.search(r'data-bulletin-price=["\'](\d+)["\']', html3)
        title_m = re.search(r'<title[^>]*>([^<]+)</title>', html3)
        fp["live-item"] = {
            "id": live_id, "status": r3.status_code, "size": len(html3),
            "title": title_m.group(1)[:100] if title_m else None,
            "price": price_m.group(1) if price_m else None,
            "is_generic": "доска объявлений" in (title_m.group(1) if title_m else ""),
        }
        log(f"FP live {live_id}: {r3.status_code} size={len(html3)} title={fp['live-item']['title']}")

    log("=== FARPOST P3 DONE ===")
    return fp


# ============ WILDBERRIES ============
def wb_p3():
    log("=== WB P3 ===")
    wb = {}
    sess = cffi_req.Session(impersonate="chrome")
    
    # Step 1: Warm-up — GET main page (get cookies)
    t0 = time.time()
    try:
        w = sess.get("https://www.wildberries.ru/", timeout=15)
        wb["warmup"] = {"status": w.status_code, "time": round(time.time()-t0,1),
                        "cookies": list(sess.cookies.keys()), "size": len(w.text)}
        log(f"WB warmup: {w.status_code} cookies={list(sess.cookies.keys())}")
    except Exception as e:
        wb["warmup"] = {"error": str(e)[:200]}
    time.sleep(2)

    # Step 2: Fetch detail.aspx with session
    nm = 199298871
    t0 = time.time()
    try:
        r = sess.get(f"https://www.wildberries.ru/catalog/{nm}/detail.aspx", timeout=20,
                     headers={"Accept": "text/html", "Accept-Language": "ru-RU,ru;q=0.9"})
        html = r.text
        save_html("wb_detail_full.html", html)
        
        # Look for state data
        state_m = re.findall(r'window\.(__\w+|WB_\w+|__\w+__)\s*=', html)
        jsonld = re.findall(r'application/ld\+json["\'][^>]*>(.*?)</script>', html, re.DOTALL)
        og = re.findall(r'og:(title|description|image)["\'][^>]*content=["\']([^"\']+)', html, re.I)
        price_m = re.search(r'"price"\s*:\s*"?(\d+)', html)
        
        wb["detail"] = {
            "status": r.status_code, "time": round(time.time()-t0,1), "size": len(html),
            "state_vars": state_vars, "jsonld_count": len(jsonld),
            "og": og[:5], "price": price_m.group(1) if price_m else None,
            "head": html[:1500],
        }
        log(f"WB detail: {r.status_code} size={len(html)} state={state_vars} jsonld={len(jsonld)}")
    except Exception as e:
        wb["detail"] = {"error": str(e)[:200]}
    time.sleep(2)

    # Step 3: Try basket CDN with session (cookies might help)
    vol = nm // 100000
    part = nm // 1000
    # Try ALL basket numbers 10-50 (the ones that resolved in Phase 1)
    basket_results = {}
    for bn in range(10, 51):
        url = f"https://basket-{bn}.wbbasket.ru/vol{vol}/part{part}/{nm}/info/{nm}-info.json"
        t0 = time.time()
        try:
            br = sess.get(url, timeout=6, headers={"Accept": "application/json"})
            basket_results[bn] = {"status": br.status_code, "time": round(time.time()-t0,1)}
            if br.status_code == 200:
                basket_results[bn]["preview"] = br.text[:500]
                log(f"WB basket-{bn}: 200! FOUND!")
                break
        except Exception:
            basket_results[bn] = {"status": 0}
        time.sleep(0.3)
    wb["basket-scan"] = basket_results
    log(f"WB basket scan: {len([v for v in basket_results.values() if v['status']==200])}/41 OK")

    # Step 4: Try WB search page (might be less protected)
    t0 = time.time()
    try:
        r = sess.get("https://www.wildberries.ru/search?query=iphone", timeout=15,
                     headers={"Accept": "text/html", "Accept-Language": "ru-RU,ru;q=0.9"})
        html = r.text
        # Find product IDs in search results
        prod_ids = re.findall(r'/catalog/(\d+)/detail', html)
        wb["search"] = {"status": r.status_code, "time": round(time.time()-t0,1),
                        "size": len(html), "products": len(prod_ids), "sample": prod_ids[:10]}
        log(f"WB search: {r.status_code} size={len(html)} products={len(prod_ids)}")
    except Exception as e:
        wb["search"] = {"error": str(e)[:200]}

    log("=== WB P3 DONE ===")
    return wb


# ============ OZON ============
def ozon_p3():
    log("=== OZON P3 ===")
    oz = {}
    pid = 1600562267
    sess = cffi_req.Session(impersonate="chrome")
    
    # Warm-up
    sess.get("https://www.ozon.ru/", timeout=15)
    time.sleep(1)

    # S1: Composer API with X-Request-Context (Ozon's internal header)
    xrc_values = ["bXf_Magazine", "bXf_PDP", "bXf_Search", "bXf_Catalog"]
    for xrc in xrc_values:
        t0 = time.time()
        try:
            r = sess.get(
                f"https://api.ozon.ru/composer-api.bx/page/json/v2?url=/product/{pid}/",
                headers={
                    "Accept": "application/json",
                    "Accept-Language": "ru-RU,ru;q=0.9",
                    "X-Request-Context": xrc,
                    "Referer": f"https://www.ozon.ru/product/{pid}/",
                    "Origin": "https://www.ozon.ru",
                },
                timeout=12, verify=False)
            dt = round(time.time()-t0,1)
            oz[f"composer-xrc-{xrc}"] = {"status": r.status_code, "time": dt, "preview": r.text[:500]}
            if r.status_code == 200:
                log(f"Ozon composer XRC={xrc}: 200!")
                break
        except Exception as e:
            oz[f"composer-xrc-{xrc}"] = {"status": 0, "error": str(e)[:200]}
        time.sleep(1)

    # S2: Old entrypoint.shs API
    t0 = time.time()
    try:
        r = sess.get(
            f"https://www.ozon.ru/api/entrypoint.shs",
            params={"url": f"/product/{pid}/"},
            headers={"Accept": "application/json", "X-Request-Context": "bXf_PDP"},
            timeout=12)
        dt = round(time.time()-t0,1)
        oz["entrypoint"] = {"status": r.status_code, "time": dt, "preview": r.text[:500]}
        log(f"Ozon entrypoint: {r.status_code}")
    except Exception as e:
        oz["entrypoint"] = {"error": str(e)[:200]}

    # S3: Mobile app API (different endpoint, less protected)
    mobile_urls = [
        f"https://api.ozon.ru/composer-api.bx/page/json/v2?url=/product/{pid}/&isMobile=true",
        f"https://www.ozon.ru/api/composer-api.bx/page/json/v2?url=%2Fproduct%2F{pid}%2F",
    ]
    for url in mobile_urls:
        t0 = time.time()
        try:
            r = sess.get(url, headers={
                "Accept": "application/json",
                "User-Agent": "OzonApp/1.0 (iPhone; iOS 17.0)",
                "X-Request-Context": "bXf_PDP",
            }, timeout=12)
            dt = round(time.time()-t0,1)
            oz[f"mobile-api-{url[30:65]}"] = {"status": r.status_code, "time": dt, "preview": r.text[:400]}
            if r.status_code == 200:
                log(f"Ozon mobile API: 200!")
        except Exception as e:
            oz[f"mobile-api-{url[30:65]}"] = {"error": str(e)[:200]}
        time.sleep(1)

    # S4: Try with full browser session (warm-up → product)
    t0 = time.time()
    try:
        r = sess.get(f"https://www.ozon.ru/product/{pid}/", timeout=15,
                     headers={"Accept": "text/html", "Accept-Language": "ru-RU,ru;q=0.9",
                              "Upgrade-Insecure-Requests": "1"})
        html = r.text
        save_html("ozon_product_full.html", html)
        og = re.findall(r'og:(title|description|image)["\'][^>]*content=["\']([^"\']+)', html, re.I)
        state = re.findall(r'window\.(__\w+|__\w+__)\s*=\s*(\{.{10,200})', html)
        oz["html-session"] = {"status": r.status_code, "time": round(time.time()-t0,1),
                              "size": len(html), "og": og[:5], "state": state[:3],
                              "is_antibot": "Antibot" in html or "abt-challenge" in html}
        log(f"Ozon HTML session: {r.status_code} size={len(html)} antibot={oz['html-session']['is_antibot']}")
    except Exception as e:
        oz["html-session"] = {"error": str(e)[:200]}

    log("=== OZON P3 DONE ===")
    return oz


# ============ AVITO ============
def avito_p3():
    log("=== AVITO P3 ===")
    av = {}
    sess = cffi_req.Session(impersonate="chrome")
    
    # Key: m.avito.ru/ad/{id} gives 404 (not 429) → reachable!
    # www gives 429 → IP blocked
    # Strategy: use m.avito.ru for everything
    
    # S1: m.avito.ru main page (get cookies, find structure)
    t0 = time.time()
    try:
        r = sess.get("https://m.avito.ru/", timeout=15,
                     headers={"Accept": "text/html", "Accept-Language": "ru-RU,ru;q=0.9"})
        html = r.text
        save_html("avito_m_main.html", html)
        ad_links = re.findall(r'avito\.ru/[^"]*?/(\d{8,})', html)
        av["m-main"] = {"status": r.status_code, "time": round(time.time()-t0,1),
                        "size": len(html), "ads": len(ad_links), "sample": ad_links[:10],
                        "cookies": list(sess.cookies.keys())}
        log(f"Avito m-main: {r.status_code} size={len(html)} ads={len(ad_links)}")
    except Exception as e:
        av["m-main"] = {"error": str(e)[:200]}
    time.sleep(1)

    # S2: m.avito.ru category (different path structure)
    cat_urls = [
        "https://m.avito.ru/moscow/telefony",
        "https://m.avito.ru/vladivostok/telefony",
        "https://m.avito.ru/moscow/elektronika/telefony",
    ]
    for url in cat_urls:
        t0 = time.time()
        try:
            r = sess.get(url, timeout=15,
                         headers={"Accept": "text/html", "Accept-Language": "ru-RU,ru;q=0.9"})
            html = r.text
            ad_ids = re.findall(r'avito\.ru/[^"]*?/(\d{8,})["/\s]', html)
            av[f"m-cat-{url[35:65]}"] = {"status": r.status_code, "time": round(time.time()-t0,1),
                                          "size": len(html), "ads": len(ad_ids), "sample": ad_ids[:10]}
            if r.status_code == 200 and ad_ids:
                log(f"Avito {url[35:60]}: 200, {len(ad_ids)} ads!")
                break
            else:
                log(f"Avito {url[35:60]}: {r.status_code}")
        except Exception as e:
            av[f"m-cat-{url[35:65]}"] = {"error": str(e)[:200]}
        time.sleep(1)

    # S3: Try a RECENT ad ID (2025-2026 format: 11 digits starting with 1)
    # Avito IDs in 2025+ are around 1090000000-1110000000
    test_ids = [1105000000, 1102000000, 1099000000, 1095000000, 1092000000]
    for aid in test_ids:
        t0 = time.time()
        try:
            r = sess.get(f"https://m.avito.ru/ad/{aid}", timeout=10,
                         headers={"Accept": "text/html", "Accept-Language": "ru-RU,ru;q=0.9"})
            html = r.text
            og_title = re.search(r'og:title["\'][^>]*content=["\']([^"\']+)', html, re.I)
            av[f"m-ad-{aid}"] = {"status": r.status_code, "time": round(time.time()-t0,1),
                                  "size": len(html),
                                  "title": og_title.group(1)[:80] if og_title else None,
                                  "is_404": "Не найдено" in html or "not found" in html.lower()}
            if r.status_code == 200 and not av[f"m-ad-{aid}"]["is_404"]:
                log(f"Avito m-ad-{aid}: 200 LIVE! title={av[f'm-ad-{aid}']['title']}")
                save_html(f"avito_live_{aid}.html", html)
                break
        except Exception as e:
            av[f"m-ad-{aid}"] = {"error": str(e)[:200]}
        time.sleep(0.5)

    # S4: Avito "share" URL (sometimes less protected)
    t0 = time.time()
    try:
        r = sess.get("https://www.avito.ru/share/1096783029", timeout=10,
                     headers={"Accept": "text/html", "Accept-Language": "ru-RU,ru;q=0.9"})
        av["share"] = {"status": r.status_code, "time": round(time.time()-t0,1),
                       "size": len(r.text), "preview": r.text[:300]}
        log(f"Avito share: {r.status_code}")
    except Exception as e:
        av["share"] = {"error": str(e)[:200]}

    log("=== AVITO P3 DONE ===")
    return av


# ============ MAIN ============
def main():
    t0 = time.time()
    log(f"PHASE3 START (cffi={USE_CFFI})")
    
    with ThreadPoolExecutor(max_workers=4) as pool:
        f_fp = pool.submit(farpost_p3)
        f_wb = pool.submit(wb_p3)
        f_oz = pool.submit(ozon_p3)
        f_av = pool.submit(avito_p3)
        
        results["FARPOST"] = f_fp.result()
        results["WB"] = f_wb.result()
        results["OZON"] = f_oz.result()
        results["AVITO"] = f_av.result()
    
    elapsed = round(time.time()-t0,1)
    log(f"PHASE3 DONE in {elapsed}s")
    
    with open(os.path.join(OUT, "diag_phase3_result.json"), "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    log("Saved: diag_phase3_result.json")
    
    log("--- SUMMARY ---")
    for plat in ["FARPOST", "WB", "OZON", "AVITO"]:
        ok = [k for k, v in results[plat].items() if isinstance(v, dict) and v.get("status") == 200]
        log(f"  {plat}: {len(ok)}/{len(results[plat])} → {ok[:4]}")


if __name__ == "__main__":
    main()
